"""双 Agent 群聊：分帧与轮询、部分失败与重试、中断恢复、副作用不重复（docs/agent-w18-spec.md）。"""
import json
import os
import threading
import time
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select

from group_testkit import MEMORY_ARGS, PROTOCOL, GroupBase, legacy_turn, other_user_pet, say, user_texts
from petsense.agent import group, jobs, loop, model
from petsense.db import session_scope, utc_now
from petsense.db.models.agent import AgentMessage, AgentTurn

FAILED_NOTE = '这条消息没有生成成功，请稍后重试。'
INTERRUPTED_NOTE = '上次生成中断了，这条消息没有完成，可以重试。'
BUDGET_NOTE = '这一轮用时太长，这条消息没有完成，可以重试。'
WINDOW = {'start': '2026-01-01T00:00:00+08:00', 'end': '2026-01-02T00:00:00+08:00'}


def deltas(sock, role=None):
    return [frame for frame in sock.frames
            if frame['type'] == 'message_delta' and (role is None or frame['role'] == role)]


def snapshots(sock, status=None):
    return [frame['message'] for frame in sock.frames
            if frame['type'] == 'message' and (status is None or frame['message']['status'] == status)]


class StreamingTests(GroupBase):
    def test_partial_text_is_persisted_for_polling(self):
        gate = threading.Event()
        self.use(pet={'text': '今天也想你！', 'gate': gate}, assistant=say('还没有数据。'))
        turn_id = self.accepted('陪我聊聊，也看看今天', target='both')
        jobs.tick()
        self.assertTrue(self.wait_until(lambda: len(self.rows(turn_id)) == 2
                                        and self.rows(turn_id)[0]['text'] == '今天也'), '首段落库后轮询才看得到')

        poll = self.get().json
        self.assertEqual((poll['protocol'], poll['dualAgent']), (PROTOCOL, True))
        pet_msg, assistant_msg = poll['turns'][-1]['messages']
        self.assertEqual((pet_msg['status'], pet_msg['text'], pet_msg['version']), ('running', '今天也', 3))
        self.assertEqual((assistant_msg['status'], assistant_msg['text'], assistant_msg['version']), ('queued', '', 1))
        # 旧客户端在同一时刻读到的是它一直认识的形状：按发言人划分的累计正文，没有 messages
        legacy = self.get(protocol=False).json['turns'][-1]
        self.assertEqual((legacy['status'], legacy['reply'], legacy['speaker']), ('pending', '【爱宠】今天也', 'pet'))
        self.assertNotIn('messages', legacy)

        gate.set()
        self.assertTrue(group.wait_idle(30))
        self.assertEqual([(item['status'], item['text']) for item in self.rows(turn_id)],
                         [('complete', '今天也想你！'), ('complete', '还没有数据。')])

    def test_frames_between_two_snapshots_share_a_version_with_growing_text(self):
        """落库有节流：两次落库之间的多段增量共享同一版本，正文是累计的、只会变长。"""
        text = '今天也在陪着你呀。'
        self.use(pet={'text': text, 'chunks': 3})
        sock = self.connect()
        self.accepted('想我了吗')
        self.settle()
        frames = deltas(sock)
        self.assertEqual([frame['text'] for frame in frames], ['今天也', '今天也在陪着', text])
        self.assertEqual([frame['version'] for frame in frames], [2, 3, 3])
        self.assertEqual(len({frame['messageId'] for frame in frames}), 1)
        final = snapshots(sock)[-1]
        self.assertEqual((final['status'], final['version'], final['text']), ('complete', 4, text))
        self.assertEqual(final['id'], frames[0]['messageId'], '终态快照与增量是同一个消息 ID')

    def test_text_replaced_after_a_tool_round_carries_a_higher_version(self):
        """「累计正文」只在一次模型调用内只增不减：工具轮之后模型重新起头时，这段增量必须带更高的
        版本——客户端只接受「版本更高，或同版本且正文更长」，否则会把替换当成乱序的旧帧扔掉。"""
        self.use(assistant=[say('我先查一下记录。', calls=[('query_observations', WINDOW)]),
                            say('今天没有走动数据。')])
        sock = self.connect()
        self.accepted('今天走了多久', target='assistant')
        self.settle()
        frames = deltas(sock, 'assistant')
        self.assertEqual([frame['text'] for frame in frames],
                         ['我先查一', '我先查一下记录。', '今天没有', '今天没有走动数据。'])
        previous = None
        for frame in frames:
            replaced = previous is not None and not frame['text'].startswith(previous['text'])
            self.assertTrue(not replaced or frame['version'] > previous['version'], frames)
            previous = frame
        # 重新起头的那段先落库（3 → 4）再推，不等到下一次节流落库
        self.assertEqual([frame['version'] for frame in frames], [2, 3, 4, 4])
        final = snapshots(sock, 'complete')[-1]
        self.assertEqual((final['text'], final['version']), ('今天没有走动数据。', 5))

    def test_search_hint_is_a_status_and_web_sources_belong_to_the_assistant_message(self):
        text = '查到了：睡够十二小时也正常。'
        script = self.use(pet='我在呢～',
                          assistant=say(text, hint=True, web=True))
        sock = self.connect()
        turn_id = self.accepted('小白，帮我联网核实一下它睡这么久正常吗', target='both')
        self.settle()
        pet_msg, assistant_msg = self.rows(turn_id)
        self.assertEqual([frame for frame in sock.frames if frame['type'] == 'message_status'],
                         [{'type': 'message_status', 'petId': self.pet, 'conversationId': self.client.conversation_id,
                           'turnId': turn_id, 'messageId': assistant_msg['id'], 'role': 'assistant',
                           'status': 'searching'}])
        self.assertFalse(any(model.SEARCH_PROGRESS_HINT in (frame.get('text') or '') for frame in sock.frames),
                         '联网进度不是正文，不进任何文本字段')
        self.assertEqual((pet_msg['status'], pet_msg['text'], pet_msg['sources']), ('complete', '我在呢～', []))
        self.assertEqual((assistant_msg['status'], assistant_msg['text']), ('complete', text))
        self.assertEqual([(source['type'], source['url']) for source in assistant_msg['sources']],
                         [('web', 'https://example.com/source')], '来源跟的是发起搜索的那条消息')
        self.assertTrue(script.of('assistant')[0]['web_search'])
        self.assertEqual([source['url'] for source in json.loads(self.turn_row(turn_id).sources)],
                         ['https://example.com/source'], '回合级 sources 仍镜像给旧客户端')

    def test_tool_sources_belong_to_the_message_that_queried(self):
        self.use(pet=[
            {'calls': [('query_observations', WINDOW)]}, '我陪着你。'],
            assistant=[say('', intent='read', calls=[('query_observations', WINDOW)]), say('那天没有走动记录。')])
        turn_id = self.accepted('陪我聊聊，也查查那天走了多久', target='both')
        self.settle()
        pet_run, assistant_run = self.runs_of(turn_id)
        self.assertEqual((pet_run['toolSummary'][0]['allowed'], pet_run['toolSummary'][0]['reason']),
                         (False, 'role_not_allowed'))
        self.assertEqual((assistant_run['toolSummary'][0]['allowed'], assistant_run['toolSummary'][0]['reason']),
                         (True, None))
        pet_msg, assistant_msg = self.rows(turn_id)
        self.assertEqual(pet_msg['sources'], [], '爱宠的查询被拒，不产生来源')
        self.assertEqual([(source['type'], source['startAt'], source['endAt']) for source in assistant_msg['sources']],
                         [('observations', '2025-12-31T16:00:00.000Z', '2026-01-01T16:00:00.000Z')])

    def test_second_message_failure_and_retry_reruns_only_the_failed_message(self):
        script = self.use(pet='我在呢～', assistant=[{'raise': True}, say('我查了：还没有数据。')])
        sock = self.connect()
        turn_id = self.accepted('陪我聊聊，也看看走了多久', target='both')
        self.settle()
        pet_msg, assistant_msg = self.rows(turn_id)
        self.assertEqual((pet_msg['status'], pet_msg['text']), ('complete', '我在呢～'), '一位失败不影响另一位')
        self.assertEqual((assistant_msg['status'], assistant_msg['text'], assistant_msg['error'],
                          assistant_msg['errorNote']), ('failed', '', 'model_error', FAILED_NOTE))
        failed_version = assistant_msg['version']
        turn = self.turn_row(turn_id)
        self.assertEqual((turn.status, turn.reply),
                         ('complete', f'【爱宠】我在呢～\n【照料助手】（{FAILED_NOTE}）'))
        self.assertEqual([(item['status'], item['id']) for item in snapshots(sock)],
                         [('running', pet_msg['id']), ('complete', pet_msg['id']),
                          ('running', assistant_msg['id']), ('failed', assistant_msg['id'])])
        self.assertEqual(sock.frames[-1]['turn']['messages'][1]['error'], 'model_error')

        response = self.retry(turn_id)
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        self.assertEqual([(item['role'], item['status']) for item in response.json['messages']],
                         [('pet', 'complete'), ('assistant', 'queued')])
        self.assertEqual([run['status'] for run in self.runs_of(turn_id)], ['complete', 'failed'])
        self.settle()

        pet_msg, assistant_msg = self.rows(turn_id)
        self.assertEqual([(item['role'], item['status'], item['text'], item['error']) for item in (pet_msg, assistant_msg)],
                         [('pet', 'complete', '我在呢～', None), ('assistant', 'complete', '我查了：还没有数据。', None)])
        self.assertIsNone(assistant_msg['errorNote'])
        self.assertEqual(assistant_msg['id'], snapshots(sock)[2]['id'], '重试沿用同一个消息 ID，不新增消息')
        self.assertGreater(assistant_msg['version'], failed_version, '版本只增不减，晚到的旧帧不会覆盖')
        self.assertEqual((len(script.of('pet')), len(script.of('assistant'))), (1, 2))
        self.assertEqual([run['status'] for run in self.runs_of(turn_id)],
                         ['complete', 'failed', 'complete'])
        self.assertEqual([run['messageId'] for run in self.runs_of(turn_id)],
                         [int(pet_msg['id'][1:]), int(assistant_msg['id'][1:]), int(assistant_msg['id'][1:])],
                         '成功的那条只有一条轨迹；失败与重试两次运行都挂在重试的那条消息上')
        self.assertEqual(self.turn_row(turn_id).reply, '【爱宠】我在呢～\n【照料助手】我查了：还没有数据。')
        self.assertEqual(sock.frames[-1]['turn']['status'], 'complete')
        self.assertEqual(self.settle_job(turn_id), 'done')

    def test_retry_conflicts(self):
        gate = threading.Event()
        script = self.use(pet={'text': '我在呢～', 'gate': gate}, assistant=say('还没有数据。'))
        turn_id = self.accepted('陪我聊聊', target='both')
        jobs.tick()
        self.assertTrue(self.wait_until(lambda: script.counts.get('pet') == 1))
        response = self.retry(turn_id)
        self.assertEqual((response.status_code, response.json.get('code')), (409, 'turn_running'))
        gate.set()
        self.assertTrue(group.wait_idle(30))
        response = self.retry(turn_id)
        self.assertEqual((response.status_code, response.json.get('code')), (409, 'nothing_to_retry'))

        legacy_id = self.legacy('在吗', reply='【照料助手】在的。')
        response = self.retry(legacy_id)
        self.assertEqual((response.status_code, response.json.get('code')), (409, 'not_group_turn'))
        with patch.dict(os.environ, {'PETSENSE_DUAL_AGENT_ENABLED': '0'}):
            response = self.retry(turn_id)
        self.assertEqual((response.status_code, response.json.get('code')), (409, 'dual_agent_disabled'))
        response = self.retry(turn_id, pet_id=other_user_pet())
        self.assertEqual((response.status_code, response.json), (404, {'message': '宠物不存在'}))
        response = self.client.post(f'/api/app/agent/pets/{self.pet}/turns/{legacy_id + 100}/retry', json={})
        self.assertEqual(response.status_code, 404)

    def test_turn_call_budget_is_enforced(self):
        self.use(pet='我在呢～', assistant=say('我查了：还没有数据。'))
        with patch.object(group, 'TURN_CALL_BUDGET', 1):
            turn_id = self.accepted('陪我聊聊，也看看走了多久', target='both')
            self.settle()
        pet_msg, assistant_msg = self.rows(turn_id)
        self.assertEqual(pet_msg['status'], 'complete')
        self.assertEqual((assistant_msg['status'], assistant_msg['text'], assistant_msg['error'],
                          assistant_msg['errorNote']), ('failed', '', 'budget_exhausted', BUDGET_NOTE))
        assistant_run = self.runs_of(turn_id)[-1]
        self.assertEqual((assistant_run['status'], assistant_run['conclusion']['reason_code']),
                         ('failed', 'budget_exhausted'))
        self.assertEqual(assistant_run['error'], 'ValueError: 整轮模型调用预算已用尽')

        response = self.retry(turn_id)  # 预算按每次处理重新计：重试有自己的一份
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        self.settle()
        self.assertEqual([item['status'] for item in self.rows(turn_id)], ['complete', 'complete'])

    def test_error_codes(self):
        now = time.monotonic()
        self.assertEqual(group._error_code(RuntimeError('x'), now + 10), 'model_error')
        self.assertEqual(group._error_code(RuntimeError('x'), now - 1), 'timeout')
        self.assertEqual(group._error_code(ValueError(loop._TURN_BUDGET_ERROR), now - 1), 'budget_exhausted')
        self.assertEqual(group.ERROR_NOTES['timeout'], BUDGET_NOTE)

    def test_side_effect_is_journaled_and_not_repeated_on_retry(self):
        script = self.use(pet='我记住啦。',
                          assistant=[say('', intent='write', calls=[('write_memory', MEMORY_ARGS)]), {'raise': True},
                                     say('', intent='write', calls=[('write_memory', MEMORY_ARGS)]),
                                     say('记下了，今天去了公园。')])
        turn_id = self.accepted('我今天带它去公园跑了两圈，帮我记下来', target='assistant')
        self.settle()
        self.assertEqual([item['status'] for item in self.rows(turn_id)], ['failed'])
        self.assertEqual(self.memory_count(), 1, '第一次尝试里写入已经生效')
        self.assertEqual([(entry['name'], entry['status']) for entry in self.journal(turn_id, 'assistant')],
                         [('write_memory', 'done')])

        self.assertEqual(self.retry(turn_id).status_code, 202)
        self.settle()
        assistant_msg = self.rows(turn_id)[0]
        self.assertEqual((assistant_msg['status'], assistant_msg['text']),
                         ('complete', '记下了，今天去了公园。'))
        self.assertEqual(self.memory_count(), 1, '同一句话不写第二遍')
        retry_run = self.runs_of(turn_id)[-1]
        self.assertEqual((retry_run['toolSummary'][0]['name'], retry_run['toolSummary'][0]['allowed'],
                          retry_run['toolSummary'][0]['reason']), ('write_memory', True, 'replayed'))
        notes = '\n'.join(user_texts(script.of('assistant')[2]['items']))
        self.assertIn('其中有些操作已经生效，不要重复执行', notes)
        self.assertIn('已保存一条记忆（主人今天带我去公园跑了两圈）', notes)
        self.assertNotIn('其中有些操作已经生效', '\n'.join(user_texts(script.of('assistant')[0]['items'])))
        self.assertEqual([(entry['name'], entry['status']) for entry in self.journal(turn_id, 'assistant')],
                         [('write_memory', 'done')], '回放的是已持久化的结果，不再执行一次')

    def test_failed_journal_blocks_the_effect(self):
        self.use(assistant=[say('', intent='write', calls=[('write_memory', MEMORY_ARGS)]),
                            say('这次没能记下来。')])
        with patch.object(group, '_effect_journal', return_value=lambda *_args: False):
            turn_id = self.accepted('我今天带它去公园跑了两圈，帮我记下来', target='assistant')
            self.settle()
        self.assertEqual(self.memory_count(), 0, '写前意图落不了库就不执行，宁可少做不可重复')
        assistant_run = self.runs_of(turn_id)[-1]
        self.assertEqual((assistant_run['toolSummary'][0]['allowed'], assistant_run['toolSummary'][0]['reason']),
                         (False, 'effect_journal_failed'))
        self.assertEqual([item['status'] for item in self.rows(turn_id)], ['complete'])

    def test_interrupted_message_is_marked_failed_and_the_rest_continues(self):
        """进程在生成中途重启：派发已落库、那条消息停在生成中；调度重新领取后如实标记并继续其余消息。"""
        script = self.use(pet='我在呢～', assistant=say('还没有数据。'))
        turn_id = self.accepted('陪我聊聊', target='both')
        group._dispatch_stage(turn_id, utc_now())
        with session_scope() as s:
            row = s.execute(select(AgentMessage).where(AgentMessage.turn_id == turn_id,
                                                       AgentMessage.role == 'pet')).scalar_one()
            row.status, row.text, row.started_at = 'running', '我在', utc_now()
        self.settle()  # 作业仍在：调度领取 → 线程池接手

        pet_msg, assistant_msg = self.rows(turn_id)
        self.assertEqual((pet_msg['status'], pet_msg['text'], pet_msg['error'], pet_msg['errorNote']),
                         ('failed', '', 'interrupted', INTERRUPTED_NOTE))
        self.assertEqual((assistant_msg['status'], assistant_msg['text']), ('complete', '还没有数据。'))
        self.assertEqual(self.turn_row(turn_id).reply,
                         f'【爱宠】（{INTERRUPTED_NOTE}）\n【照料助手】还没有数据。')
        runs = self.runs_of(turn_id)
        self.assertEqual([run['status'] for run in runs], ['failed', 'complete'],
                         '中断的那条此前没有轨迹：恢复时补一条失败运行')
        self.assertEqual((runs[0]['conclusion']['speaker'], runs[0]['conclusion']['reason_code'],
                          runs[0]['error'], runs[0]['messageId']),
                         ('pet', 'interrupted', 'interrupted', int(pet_msg['id'][1:])))

        self.assertEqual(self.retry(turn_id).status_code, 202)
        self.settle()
        pet_msg, assistant_msg = self.rows(turn_id)
        self.assertEqual([(item['status'], item['text'], item['error']) for item in (pet_msg, assistant_msg)],
                         [('complete', '我在呢～', None), ('complete', '还没有数据。', None)])
        self.assertEqual((len(script.of('pet')), len(script.of('assistant'))), (1, 1))

    def test_stale_pending_turn_expires_on_read_and_retry_is_not_stale(self):
        self.use(pet='我在呢～', assistant=say('还没有数据。'))
        turn_id = self.accepted('陪我聊聊', target='both')
        group._dispatch_stage(turn_id, utc_now())
        with session_scope() as s:
            s.get(AgentTurn, turn_id).created_at = utc_now() - timedelta(seconds=group.STALE_PENDING_S + 10)
            row = s.execute(select(AgentMessage).where(AgentMessage.turn_id == turn_id,
                                                       AgentMessage.role == 'pet')).scalar_one()
            row.status, row.text, pet_row_id = 'running', '我在', row.id

        poll = self.get().json
        messages = poll['turns'][-1]['messages']
        self.assertEqual([(item['role'], item['status'], item['error']) for item in messages],
                         [('pet', 'failed', 'interrupted'), ('assistant', 'failed', 'interrupted')])
        self.assertEqual(messages[0]['text'], '', '中断的消息不保留半截正文')
        self.assertEqual(self.turn_row(turn_id).status, 'failed')

        self.assertEqual(self.retry(turn_id).status_code, 202)
        messages = self.get().json['turns'][-1]['messages']  # 重试后的轮询：按重新开始等待算，不判超时
        self.assertEqual([item['status'] for item in messages], ['queued', 'queued'])
        self.settle()
        self.assertEqual([item['status'] for item in self.rows(turn_id)], ['complete', 'complete'])
        with session_scope() as s:
            self.assertGreaterEqual(s.get(AgentMessage, pet_row_id).version, 5)

    def test_crash_while_generating_fails_the_unfinished_message(self):
        self.use(pet='我在呢～', assistant=say('还没有数据。'))
        real = group._run_one

        def crash_on_assistant(turn_id, item, *args):
            if item['role'] == 'assistant':
                raise RuntimeError('boom')
            return real(turn_id, item, *args)

        sock = self.connect()
        turn_id = self.accepted('陪我聊聊', target='both')
        with patch.object(group, '_run_one', side_effect=crash_on_assistant):
            self.settle()
        pet_msg, assistant_msg = self.rows(turn_id)
        self.assertEqual((pet_msg['status'], pet_msg['text']), ('complete', '我在呢～'))
        self.assertEqual((assistant_msg['status'], assistant_msg['error']), ('failed', 'model_error'))
        self.assertEqual(self.turn_row(turn_id).status, 'complete')
        self.assertEqual(sock.frames[-1]['turn']['status'], 'complete', '崩溃后仍推收尾帧')
        self.assertEqual(self.settle_job(turn_id), 'done', '回合已收尾，作业不会反复触发同一个崩溃')
        self.assertEqual(self.retry(turn_id).status_code, 202)
        self.settle()
        self.assertEqual([item['status'] for item in self.rows(turn_id)], ['complete', 'complete'])

    def test_crash_before_dispatch_fails_the_turn_once(self):
        self.use(pet='我在呢～')
        turn_id = self.accepted('陪我聊聊')
        with patch.object(group, '_dispatch_stage', side_effect=RuntimeError('dispatch storage down')):
            self.settle()
        turn = self.turn_row(turn_id)
        self.assertEqual((turn.status, turn.reply), ('failed', group.FAILED_TURN_NOTE))
        self.assertEqual(self.rows(turn_id), [])
        messages = self.get().json['turns'][-1]['messages']
        self.assertEqual([(item['id'], item['role'], item['status'], item['readOnly']) for item in messages],
                         [(f't{turn_id}-0', 'assistant', 'failed', True)])
        self.assertEqual(self.settle_job(turn_id), 'done')
        self.assertEqual([run['status'] for run in self.runs_of(turn_id)], [])

    def test_next_turn_sees_the_settled_reply_as_history(self):
        script = self.use(pet=['我在呢～'] * 2)
        self.accepted('在吗')
        self.settle()
        turn_id = self.accepted('今天呢')
        self.settle()
        items = script.of('pet')[1]['items']
        self.assertEqual([item['content'] for item in items if item.get('role') == 'assistant'], ['【爱宠】我在呢～'])
        self.assertEqual([item['text'] for item in self.rows(turn_id)], ['我在呢～'])

    # -- helper ----------------------------------------------------------
    def journal(self, turn_id, role):
        with session_scope() as s:
            row = s.execute(select(AgentMessage).where(AgentMessage.turn_id == turn_id,
                                                       AgentMessage.role == role)).scalar_one()
            return json.loads(row.effect_json)
