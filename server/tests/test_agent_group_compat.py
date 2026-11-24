"""双 Agent 群聊兼容性：幂等、全局回退、消息级引用、旧历史投影、旧客户端与卡片同步。

每条用例都在回答同一个问题：同一份历史，在新旧两种客户端、开关前后三种状态（开 / 关 / 关闭后仍有
已入队的回合）下读到的内容是否一致、能否继续操作。
"""
import json
import os
import threading
from unittest.mock import patch

from flask import Flask

from group_testkit import FakeSocket, GroupBase, legacy_turn, next_key, other_user_pet, route_json, user_texts
from petsense.agent import group, ws
from petsense.agent.routes import public
from petsense.db import session_scope, utc_now
from petsense.db.models.agent import AgentMessage, AgentRoute, AgentTurn

FAILED_NOTE = '暂时没能接上照料助手，你的话已经保留。稍后可以重新发送。'
PROPOSAL_ROUND = {'calls': [('prepare_profile_update', {'fields': {'birthYear': 2023}, 'explicit': False,
                                                                   'note': '主人说它三岁'})]}


def card(kind, number, **fields):
    return {'type': kind, kind: {'id': number, **fields}}


def card_keys(raw):
    """消息卡片序列的可读摘要：文本块记 text，卡片记 类型:id。"""
    keys = []
    for block in json.loads(raw):
        inner = block.get('proposal') or block.get('action')
        keys.append(f"{block['type']}:{inner['id']}" if inner else block['type'])
    return keys


class IdempotencyTests(GroupBase):
    def test_resending_the_same_request_never_generates_twice(self):
        script = self.use(router=route_json(), pet='我在呢。', assistant='今天走了 40 分钟。')
        key = next_key()
        first = self.post('今天走了多久', key=key, target='both')
        again = self.post('今天走了多久', key=key, target='both')
        self.assertEqual((first.status_code, again.status_code), (202, 202), again.get_data(as_text=True))
        self.assertEqual(first.json['turn']['id'], again.json['turn']['id'])
        self.assertTrue(again.json['accepted'])
        self.settle()
        settled = self.post('今天走了多久', key=key, target='both')
        self.assertEqual(settled.status_code, 200)  # 回合已结束：幂等重发返回当前状态
        self.assertEqual([message['status'] for message in settled.json['turn']['messages']], ['complete'] * 2)
        self.assertEqual([len(script.of(kind)) for kind in ('router', 'pet', 'assistant')], [1, 1, 1])
        self.assertEqual((len(self.group_jobs()), self.count(AgentRoute)), (1, 1))

    def test_same_key_for_another_message_is_rejected(self):
        turn_id = self.accepted('今天走了多久', key='same-key', target='both')
        self.assertEqual(self.post('它几岁了', key='same-key', target='both').status_code, 409)
        self.assertEqual(self.post('今天走了多久', key='same-key', target='pet').status_code, 409)
        # 「两位」与「未指定」是同一条主人消息：账号回退后客户端改用 auto 重发，照常幂等
        again = self.post('今天走了多久', key='same-key', target='auto')
        self.assertEqual(again.status_code, 202)
        self.assertEqual(again.json['turn']['id'], turn_id)
        self.assertEqual((self.count(AgentTurn), len(self.group_jobs())), (1, 1))

    def test_invalid_requests_leave_nothing_behind(self):
        foreign = legacy_turn(other_user_pet(), '别人的问题')
        own = legacy_turn(self.pet, '自己的问题', reply='【照料助手】自己的回答')
        cases = [
            ({'text': '   '}, 400),
            ({'text': '你好', 'requestId': ''}, 400),
            ({'text': '你好', 'target': 'everyone'}, 400),
            ({'text': '你好', 'replyTo': foreign}, 404),  # 别人的回合：不存在，不泄露它是否存在
            ({'text': '你好', 'replyToMessage': f't{own}-0'}, 400),  # 消息级引用必须与回合级引用同时出现
            ({'text': '你好', 'replyTo': own, 'replyToMessage': 'm1'}, 400),  # 这个回合里没有这条消息
            ({'text': '你好', 'replyTo': own, 'replyToMessage': f't{own}-7'}, 400),
            ({'text': '你好', 'replyTo': own, 'replyToMessage': 'whatever'}, 400),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                self.assertEqual(self.post(**fields).status_code, expected)
        self.assertEqual((self.count(AgentTurn), len(self.group_jobs()), self.count(AgentMessage)), (2, 0, 0))


class RollbackTests(GroupBase):
    """新协议默认启用。关闭时声明群聊协议的客户端也继续收到旧格式。"""

    def setUp(self):
        super().setUp()
        self.script = self.use(legacy='【照料助手】今天走了 40 分钟。')

    def test_flag_off_runs_the_legacy_path_for_a_declaring_client(self):
        with patch.dict(os.environ, {'PETSENSE_DUAL_AGENT_ENABLED': ''}):
            response = self.post('今天走了多久', target='auto')
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(response.json['turn']['reply'], '【照料助手】今天走了 40 分钟。')
            self.assertNotIn('protocol', response.json)
            self.assertNotIn('messages', response.json['turn'])
            listing = self.get()
            self.assertEqual(sorted(listing.json), ['turns'])
            self.assertNotIn('messages', listing.json['turns'][0])
        self.assertEqual([call['kind'] for call in self.script.calls], ['legacy'])
        self.assertEqual((len(self.group_jobs()), self.count(AgentRoute), self.count(AgentMessage)), (0, 0, 0))

    def test_both_target_is_refused_with_a_recognisable_code(self):
        with patch.dict(os.environ, {'PETSENSE_DUAL_AGENT_ENABLED': ''}):
            response = self.post('你好', target='both')
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json['code'], 'dual_agent_unavailable')
            self.assertEqual(self.count(AgentTurn), 0)  # 被拒绝的请求不留下回合

    def test_turn_enqueued_before_rollback_finishes_on_its_persisted_plan(self):
        self.script.scripts['router'] = [route_json()]
        self.script.scripts['pet'] = ['我在呢。']
        self.script.scripts['assistant'] = ['今天走了 40 分钟。']
        turn_id = self.accepted('今天走了多久', key='rollback-1', target='both')
        with patch.dict(os.environ, {'PETSENSE_DUAL_AGENT_ENABLED': ''}):
            self.settle()
            # 回执丢失后的重发：同一条消息照常幂等返回，只是回到旧结构（「两位」等同未指定）
            response = self.post('今天走了多久', key='rollback-1', target='both', protocol=False)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertNotIn('messages', response.json['turn'])
            self.assertEqual(response.json['turn']['target'], 'auto')
            legacy = self.get(protocol=False).json['turns'][-1]
        self.assertEqual([message['status'] for message in self.rows(turn_id)], ['complete'] * 2)
        self.assertEqual(legacy['reply'], '【爱宠】我在呢。\n【照料助手】今天走了 40 分钟。')
        self.assertEqual([call['kind'] for call in self.script.calls], ['router', 'pet', 'assistant'])

    def test_group_protocol_is_enabled_with_no_feature_configuration(self):
        with patch.dict(os.environ):
            os.environ.pop(group.ENABLED_ENV, None)
            self.assertEqual(self.post('你好').status_code, 202)
            self.assertEqual(self.get().json['protocol'], group.PROTOCOL)

    def test_disabled_scheduler_keeps_the_legacy_path(self):
        with patch.dict(os.environ, {'PETSENSE_SCHEDULER_DISABLED': '1'}):
            self.assertEqual(self.post('你好', target='both').status_code, 400)
            self.assertEqual(self.post('你好').status_code, 200)
            listing = self.get()
            self.assertEqual(sorted(listing.json), ['turns'])
        self.assertEqual(self.count(AgentTurn), 1)

    def test_declaring_connection_after_rollback_still_streams_legacy_frames(self):
        """关闭群聊协议后，新客户端照旧收到流式正文。"""
        socket = FakeSocket()
        release = threading.Event()
        socket.receive = lambda: release.wait(5) and None  # 连接保持到用例放行，帧才会送到这条连接

        def serve():  # 请求上下文只对建立它的线程有效：与真实连接一样在各自线程里握手
            with Flask(__name__).test_request_context(f'/api/app/agent/ws?token=x&protocol={group.PROTOCOL}'):
                ws.handler(socket)

        with patch.dict(os.environ, {group.ENABLED_ENV: '0'}), \
                patch.object(ws, 'user_for_token', return_value={'id': self.uid, 'username': 'u1'}):
            handler = threading.Thread(target=serve, daemon=True)
            handler.start()
            self.assertTrue(self.wait_until(lambda: socket in ws.clients))
            response = self.post('今天走了多久')
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            release.set()
            handler.join(5)
        self.assertEqual([frame['type'] for frame in socket.frames], ['delta', 'delta', 'turn'])
        self.assertEqual(socket.frames[-2]['text'], '【照料助手】今天走了 40 分钟。')
        self.assertNotIn('messages', socket.frames[-1]['turn'])
        self.assertNotIn(socket, ws.protocols)



class QuoteTests(GroupBase):
    def test_message_quote_reaches_the_router_and_both_speakers(self):
        script = self.use(router=[route_json(), route_json(order=None)],
                          pet=['我在呢。'], assistant=['它今天走了 40 分钟。', '昨天走了 35 分钟。'])
        first = self.accepted('它今天走了多久', target='assistant')
        self.settle()
        answer = self.rows(first)[0]
        second = self.accepted('那昨天呢', replyTo=first, replyToMessage=answer['id'], target='both')
        self.settle()
        stored = self.turn_row(second)
        self.assertEqual((stored.reply_to, stored.reply_to_message), (first, answer['id']))
        # 路由器没有给出可用顺序时，被引用的那位先说
        self.assertEqual([message['role'] for message in self.rows(second)], ['assistant', 'pet'])
        router_text = ''.join(user_texts(script.of('router')[1]['items']))
        self.assertIn('被引用消息：【照料助手】它今天走了 40 分钟。', router_text)
        note = '（系统补充）主人这条消息引用了群里照料助手先前发的一条消息：「它今天走了 40 分钟。」'
        for kind, index in (('assistant', 1), ('pet', 0)):
            self.assertIn(note, ''.join(user_texts(script.of(kind)[index]['items'])))
        listing = self.get().json['turns'][-1]
        self.assertEqual((listing['replyTo'], listing['replyToMessage']), (first, answer['id']))

    def test_quoting_the_pet_message_answers_as_if_the_pet_was_named(self):
        script = self.use(router=[route_json(), route_json(companion=True, professional=False)],
                          pet=['汪！', '汪汪！'], assistant='它今天走了 40 分钟。')
        first = self.accepted('陪我聊聊', target='both')
        self.settle()
        bark = self.rows(first)[0]
        self.accepted('你刚才说什么', replyTo=first, replyToMessage=bark['id'])
        self.settle()
        router_text = ''.join(user_texts(script.of('router')[1]['items']))
        self.assertIn('被引用消息：【爱宠】汪！', router_text)
        note = '（系统补充）主人这条消息引用了群里爱宠先前发的一条消息：「汪！」'
        self.assertIn(note, ''.join(user_texts(script.of('pet')[1]['items'])))

    def test_turn_level_quote_uses_the_last_message_with_text(self):
        script = self.use(router=[route_json(), route_json(order=None)], pet=['汪！', '汪汪！'],
                          assistant=['它今天走了 40 分钟。', '还是 40 分钟。'])
        first = self.accepted('陪我聊聊', target='both')
        self.settle()
        second = self.accepted('再说一遍', replyTo=first, target='both')
        self.settle()
        self.assertIsNone(self.turn_row(second).reply_to_message)
        router_text = ''.join(user_texts(script.of('router')[1]['items']))
        self.assertIn('被引用消息：【照料助手】它今天走了 40 分钟。', router_text)
        # 近期话题按实际发言人分行：不在一位的名字底下混着两位的话
        self.assertIn('近期话题（由早到晚）：\n主人：陪我聊聊\n爱宠：汪！\n照料助手：它今天走了 40 分钟。',
                      router_text)

    def test_legacy_turns_are_quotable_as_read_only_messages(self):
        sources = [{'type': 'web', 'url': 'https://example.com/a', 'title': '来源'}]
        old = legacy_turn(self.pet, '旧问题', reply='【爱宠】汪！\n【照料助手】它今天走了 40 分钟。',
                          blocks=[{'type': 'text', 'speaker': 'pet', 'text': '汪！'},
                                  {'type': 'text', 'speaker': 'assistant', 'text': '它今天走了 40 分钟。'}],
                          sources=sources)
        projected = self.get().json['turns'][0]['messages']
        self.assertEqual([(message['id'], message['role'], message['text']) for message in projected],
                         [(f't{old}-0', 'pet', '汪！'), (f't{old}-1', 'assistant', '它今天走了 40 分钟。')])
        self.assertEqual([message['sources'] for message in projected], [[], sources])
        self.assertTrue(all(message['readOnly'] and message['status'] == 'complete' for message in projected))
        self.assertNotIn('messages', self.get(protocol=False).json['turns'][0])
        # 旧回合里的某一条可以被精确引用
        script = self.use(router=route_json(), assistant='是按今天的走动记录算的。')
        quoted = self.accepted('这个数怎么来的', replyTo=old, replyToMessage=f't{old}-1', target='assistant')
        self.settle()
        router_text = ''.join(user_texts(script.of('router')[0]['items']))
        self.assertIn('被引用消息：【照料助手】它今天走了 40 分钟。', router_text)
        self.assertEqual(self.turn_row(quoted).reply_to_message, f't{old}-1')

    def test_legacy_streaming_and_failed_turns_project_without_role_markers(self):
        streaming = legacy_turn(self.pet, '旧问题', reply='【照料助手】今天走了', speaker='assistant',
                                status='pending')
        both = legacy_turn(self.pet, '两位都在说', reply='【爱宠】汪！\n【照料助手】它今天走了',
                           speaker='assistant', status='pending')
        failed = legacy_turn(self.pet, '坏问题', reply=FAILED_NOTE, status='failed')
        by_id = {turn['id']: turn['messages'] for turn in self.get().json['turns']}
        self.assertEqual([(message['role'], message['status'], message['text']) for message in by_id[streaming]],
                         [('assistant', 'running', '今天走了')])
        self.assertEqual([(message['id'], message['role'], message['text']) for message in by_id[both]],
                         [(f't{both}-0', 'pet', '汪！'), (f't{both}-1', 'assistant', '它今天走了')])
        self.assertEqual([(message['role'], message['status'], message['text']) for message in by_id[failed]],
                         [('assistant', 'failed', FAILED_NOTE)])
        self.assertTrue(all(message['readOnly'] for messages in by_id.values() for message in messages))


class CompatTests(GroupBase):
    def settled_turn(self, text='陪我聊聊', **scripts):
        script = self.use(**({'router': route_json(), 'pet': '我在呢。', 'assistant': '今天走了 40 分钟。'}
                             | scripts))
        turn_id = self.accepted(text, target='both')
        self.settle()
        return turn_id, script

    def test_pending_turn_before_routing_has_no_messages_yet(self):
        self.accepted('陪我聊聊', target='both')
        snapshot = self.get().json['turns'][-1]
        self.assertEqual((snapshot['status'], snapshot['messages']), ('pending', []))
        legacy = self.get(protocol=False).json['turns'][-1]
        self.assertEqual((legacy['status'], legacy['reply']), ('pending', ''))

    def test_mixed_history_reads_the_same_content_in_both_protocols(self):
        old = legacy_turn(self.pet, '旧问题', reply='【照料助手】旧回答')
        new, _script = self.settled_turn()
        group_listing = self.get().json
        self.assertEqual(sorted(group_listing), ['dualAgent', 'protocol', 'turns'])
        self.assertEqual((group_listing['protocol'], group_listing['dualAgent']), ('group-v1', True))
        group_view = {turn['id']: turn for turn in group_listing['turns']}
        legacy_listing = self.get(protocol=False).json
        self.assertEqual(sorted(legacy_listing), ['turns'])
        legacy_view = {turn['id']: turn for turn in legacy_listing['turns']}
        self.assertEqual([message['id'] for message in group_view[old]['messages']], [f't{old}-0'])
        self.assertNotIn('messages', legacy_view[old])
        self.assertEqual([message['role'] for message in group_view[new]['messages']], ['pet', 'assistant'])
        self.assertEqual((legacy_view[new]['target'], legacy_view[new]['reply']),
                         ('auto', '【爱宠】我在呢。\n【照料助手】今天走了 40 分钟。'))
        self.assertEqual(legacy_view[new]['reply'], group_view[new]['reply'])
        self.assertEqual([block['speaker'] for block in legacy_view[new]['blocks']], ['pet', 'assistant'])

    def test_turn_frames_follow_each_connection_protocol(self):
        group_sock, legacy_sock = self.connect(True), self.connect(False)
        turn_id, _script = self.settled_turn()
        final_group = [frame for frame in group_sock.frames if frame['type'] == 'turn'][-1]['turn']
        final_legacy = [frame for frame in legacy_sock.frames if frame['type'] == 'turn'][-1]['turn']
        self.assertEqual([message['role'] for message in final_group['messages']], ['pet', 'assistant'])
        self.assertEqual(final_group['target'], 'both')
        self.assertNotIn('messages', final_legacy)
        self.assertEqual((final_legacy['target'], final_legacy['reply']),
                         ('auto', '【爱宠】我在呢。\n【照料助手】今天走了 40 分钟。'))
        self.assertFalse([frame for frame in group_sock.frames if frame['type'] == 'delta'])
        self.assertFalse([frame for frame in legacy_sock.frames if frame['type'] == 'message_delta'])
        # 旧入口（后台回信等）推送的旧结构快照：群聊连接按持久化消息补齐，旧连接原样收到
        old = legacy_turn(self.pet, '旧问题', reply='【照料助手】旧回答')
        with session_scope() as s:
            payload = public(s.get(AgentTurn, old))
        ws.push_turn(self.uid, self.pet, payload)
        self.assertEqual([message['id'] for message in group_sock.frames[-1]['turn']['messages']], [f't{old}-0'])
        self.assertNotIn('messages', legacy_sock.frames[-1]['turn'])

    def test_inbox_anchor_follows_the_client_protocol(self):
        turn_id, _script = self.settled_turn()
        grouped = self.client.get(f'/api/app/agent/inbox/turn/{turn_id}',
                                  query_string={'protocol': group.PROTOCOL}).json
        self.assertEqual(sorted(grouped), ['protocol', 'turn'])
        self.assertEqual([message['role'] for message in grouped['turn']['messages']], ['pet', 'assistant'])
        legacy = self.client.get(f'/api/app/agent/inbox/turn/{turn_id}').json
        self.assertEqual(sorted(legacy), ['turn'])
        self.assertNotIn('messages', legacy['turn'])
        self.assertEqual(legacy['turn']['target'], 'auto')
        foreign = legacy_turn(other_user_pet(), '别人的问题')
        self.assertEqual(self.client.get(f'/api/app/agent/inbox/turn/{foreign}').status_code, 404)
        self.assertEqual(self.client.get('/api/app/agent/inbox/turn/999999').status_code, 404)

    def test_read_endpoint_returns_each_protocol_shape(self):
        turn_id, _script = self.settled_turn()
        path = f'/api/app/agent/pets/{self.pet}/read'
        self.assertIsNone(self.get().json['turns'][-1]['readAt'])  # 读取列表不改变已读状态
        grouped = self.client.post(path, json={'ids': [turn_id]}, query_string={'protocol': group.PROTOCOL}).json
        self.assertEqual((grouped['protocol'], len(grouped['turns'][0]['messages'])), ('group-v1', 2))
        self.assertIsNotNone(grouped['turns'][0]['readAt'])
        legacy = self.client.post(path, json={'ids': [turn_id]}).json
        self.assertEqual(sorted(legacy), ['turns'])
        self.assertNotIn('messages', legacy['turns'][0])
        self.assertEqual(legacy['turns'][0]['target'], 'auto')

    def test_card_decision_follows_the_client_protocol_and_syncs_to_the_assistant_message(self):
        group_sock, legacy_sock = self.connect(True), self.connect(False)
        text = '我整理了一个生日候选，确认后才会保存。'
        self.use(router=[route_json(intent='write')] * 2, pet=['我在呢。'] * 2,
                 assistant=[PROPOSAL_ROUND, text,
                            {'calls': [('prepare_profile_update', {'fields': {'birthYear': 2022}, 'explicit': False,
                                                                            'note': '主人改口说两岁'})]}, text])

        def proposal_turn(message):
            turn_id = self.accepted(message, target='both')
            self.settle()
            messages = {item['role']: item for item in self.rows(turn_id)}
            proposal = next(block['proposal'] for block in messages['assistant']['blocks']
                            if block['type'] == 'proposal')
            return turn_id, proposal['id'], messages

        def decide(turn_id, proposal_id, **fields):
            self.assertEqual([frame['turn']['id'] for frame in group_sock.frames if frame['type'] == 'turn'][-1],
                             turn_id)
            return self.client.post(f'/api/app/agent/pets/{self.pet}/proposals/{proposal_id}/decision',
                                    json={'action': 'confirm', **fields})

        def status(blocks):
            return next(block['proposal']['status'] for block in blocks if block['type'] == 'proposal')

        # 旧客户端确认：响应是旧结构；群聊消息里卡片同步到照料助手那条并递增版本，爱宠那条不动
        turn_id, proposal_id, before = proposal_turn('它三岁了')
        response = decide(turn_id, proposal_id)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertNotIn('messages', response.json['turn'])
        self.assertEqual(response.json['turn']['target'], 'auto')
        self.assertEqual(status(response.json['turn']['blocks']), 'confirmed')
        after = {item['role']: item for item in self.rows(turn_id)}
        self.assertEqual(status(after['assistant']['blocks']), 'confirmed')
        self.assertEqual(after['assistant']['version'], before['assistant']['version'] + 1)
        self.assertEqual(after['pet']['version'], before['pet']['version'])
        self.assertIn('messages', group_sock.frames[-1]['turn'])
        self.assertNotIn('messages', legacy_sock.frames[-1]['turn'])
        self.assertEqual(legacy_sock.frames[-1]['turn']['target'], 'auto')
        # 重复确认：幂等，不再改写消息
        decide(turn_id, proposal_id)
        self.assertEqual(self.rows(turn_id)[1]['version'], after['assistant']['version'])

        # 群聊协议客户端确认：响应带消息视图，「两位」保留
        turn_id, proposal_id, before = proposal_turn('它其实是两岁')
        response = decide(turn_id, proposal_id, protocol=group.PROTOCOL)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json['turn']['target'], 'both')
        self.assertEqual([message['role'] for message in response.json['turn']['messages']], ['pet', 'assistant'])
        self.assertEqual(status(response.json['turn']['messages'][1]['blocks']), 'confirmed')

    def test_card_sync_keeps_replaced_cards_in_place(self):
        with session_scope() as s:
            turn = AgentTurn(pet_id=self.pet, request_id=next_key(), text='生成日记', target='assistant',
                             reply='', sources='[]', speaker='assistant', status='complete', created_at=utc_now())
            s.add(turn)
            s.flush()
            rows = {}
            for position, role in enumerate(('assistant', 'pet')):
                rows[role] = AgentMessage(turn_id=turn.id, pet_id=self.pet, role=role, position=position,
                                          kind='answer', follows=0, status='complete', text='好的。', version=4,
                                          sources='[]', effect_json='[]', attempts=1, created_at=utc_now())
            s.add_all(rows.values())
            s.flush()
            text_block = {'type': 'text', 'speaker': 'assistant', 'text': '好的。'}

            def rewrite(tail, owner=True):
                # 既有路径改写的是回合级 blocks；这里同时把它写成消息当前的卡片，模拟收尾后的状态
                blocks = [text_block, *tail]
                turn.blocks = json.dumps(blocks, ensure_ascii=False)
                if owner:
                    rows['assistant'].blocks = json.dumps(blocks, ensure_ascii=False)
                s.flush()

            rewrite([card('proposal', 7), card('action', 3)])
            self.assertEqual(group.sync_blocks(s, turn), [])  # 没有变化不动版本
            rewrite([card('action', 9), card('action', 3)], owner=False)  # 确认后：候选卡被动作回执取代
            self.assertEqual(group.sync_blocks(s, turn), [rows['assistant'].id])
            self.assertEqual(card_keys(rows['assistant'].blocks), ['text', 'action:9', 'action:3'])
            self.assertEqual((rows['assistant'].version, rows['pet'].version), (5, 4))
            rewrite([card('action', 9), card('action', 3), card('proposal', 11)], owner=False)  # 新卡还没有归属
            group.sync_blocks(s, turn)
            self.assertEqual(card_keys(rows['assistant'].blocks), ['text', 'action:9', 'action:3', 'proposal:11'])
            rewrite([card('action', 9), card('action', 3), card('proposal', 11, status='superseded')],
                    owner=False)  # 被新候选替代：状态更新同步到原位
            group.sync_blocks(s, turn)
            self.assertEqual(card_keys(rows['assistant'].blocks), ['text', 'action:9', 'action:3', 'proposal:11'])
            self.assertEqual(json.loads(rows['assistant'].blocks)[-1]['proposal']['status'], 'superseded')
            self.assertEqual((rows['assistant'].version, rows['pet'].version), (7, 4))
