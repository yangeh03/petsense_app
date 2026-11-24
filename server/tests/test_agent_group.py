"""双 Agent 群聊：受理与逐条生成、路由分工与单角色权限（docs/agent-dual-agent-chat-design.md §2–§4）。"""
import hashlib
import json
import threading
import unittest
from datetime import timedelta

from group_testkit import PROTOCOL, GroupBase, route_json, user_texts
from petsense.agent import group, jobs, loop, registry, router
from petsense.db import utc_now


def half(text):
    """模型桩分两段出字时的第一段。"""
    return text[:len(text) // 2]


class AcceptanceTests(GroupBase):
    def test_post_accepts_immediately_and_runs_both_in_plan_order(self):
        self.use(router=route_json(), pet='想你啦！', assistant='我查了今天的记录：暂时没有数据。')
        sock = self.connect()
        response = self.post('陪我聊两句，也查查走了多久')
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        body = response.json
        self.assertEqual((body['protocol'], body['accepted']), (PROTOCOL, True))
        self.assertEqual((body['turn']['status'], body['turn']['messages']), ('pending', []))
        turn_id = body['turn']['id']
        job = self.job_row(turn_id)
        self.assertEqual((job.kind, job.status), (group.TURN_KIND, 'pending'))
        self.settle()

        pet_text, assistant_text = '想你啦！', '我查了今天的记录：暂时没有数据。'
        messages = self.rows(turn_id)
        self.assertEqual([(item['role'], item['kind'], item['status'], item['position']) for item in messages],
                         [('pet', 'respond', 'complete', 0), ('assistant', 'answer', 'complete', 1)])
        self.assertEqual([item['text'] for item in messages], [pet_text, assistant_text])
        self.assertRegex(messages[0]['id'], r'^m\d+$')
        self.assertGreater(int(messages[1]['id'][1:]), int(messages[0]['id'][1:]))
        self.assertEqual([item['version'] for item in messages], [4, 4], '领取、首个增量落库、收尾各递增一次')
        self.assertTrue(all(item['readOnly'] is False for item in messages))

        turn = self.turn_row(turn_id)
        self.assertEqual(turn.status, 'complete')
        self.assertEqual(turn.reply, f'【爱宠】{pet_text}\n【照料助手】{assistant_text}')
        self.assertEqual((turn.speaker, turn.target), ('pet', 'auto'))
        self.assertEqual([(block['type'], block['speaker']) for block in json.loads(turn.blocks)],
                         [('text', 'pet'), ('text', 'assistant')])
        route = self.route_row(turn_id)
        self.assertEqual((route.scope, route.source), ('allowed', 'model'))
        self.assertEqual([item['role'] for item in json.loads(route.plan_json)], ['pet', 'assistant'])

        run_list = self.runs_of(turn_id)
        self.assertEqual([run['form'] for run in run_list], ['route', 'dialogue', 'dialogue'])
        self.assertEqual([run['status'] for run in run_list], ['complete'] * 3)
        self.assertEqual([run['conclusion'].get('speaker') for run in run_list[1:]], ['pet', 'assistant'])
        self.assertEqual([run['messageId'] for run in run_list[1:]],
                         [int(item['id'][1:]) for item in messages], '每条消息对应一条独立轨迹')

        kinds = [frame['type'] for frame in sock.frames]
        self.assertEqual(kinds.count('turn'), 2, kinds)  # 路由落库后的排队快照 + 收尾快照
        opened = [frame['message'] for frame in sock.frames
                  if frame['type'] == 'message' and frame['message']['status'] == 'running']
        self.assertEqual([item['role'] for item in opened], ['pet', 'assistant'])
        for role, text, message in (('pet', pet_text, messages[0]), ('assistant', assistant_text, messages[1])):
            deltas = [frame for frame in sock.frames if frame['type'] == 'message_delta' and frame['role'] == role]
            self.assertEqual([frame['text'] for frame in deltas], [half(text), text])
            self.assertEqual([frame['messageId'] for frame in deltas], [message['id']] * 2,
                             '同一发言从首字到完成用同一个 ID')
            self.assertEqual([frame['version'] for frame in deltas], [2, 3])
        self.assertFalse(any(frame['type'] == 'delta' for frame in sock.frames), '群聊连接不收旧的增量帧')
        final = sock.frames[-1]
        self.assertEqual((final['type'], final['turn']['status']), ('turn', 'complete'))
        self.assertEqual([item['status'] for item in final['turn']['messages']], ['complete', 'complete'])

    def test_job_stays_open_until_turn_settles_without_resubmitting(self):
        gate = threading.Event()
        script = self.use(router=route_json(), pet={'text': '想你啦！', 'gate': gate}, assistant='还没有数据。')
        turn_id = self.accepted()
        job_id = self.job_row(turn_id).id
        now = utc_now()
        first = group.handle_job({'payload': {'turn_id': turn_id}}, now)
        self.assertEqual(first, {'next_run_at': now + timedelta(seconds=group.RECHECK_S)},
                         '回合结束前作业保持到期复查：进程中途重启后调度会重新领取')
        self.assertTrue(self.wait_until(lambda: script.counts.get('pet') == 1))
        again = group.handle_job({'payload': {'turn_id': turn_id}}, now + timedelta(seconds=6))
        self.assertEqual(again, {'next_run_at': now + timedelta(seconds=6 + group.RECHECK_S)})
        self.assertFalse(group.submit(turn_id), '本进程已在处理的回合不重复提交')
        self.assertEqual(len(script.of('pet')), 1)
        gate.set()
        self.assertTrue(group.wait_idle(30))
        self.assertIsNone(group.handle_job({'payload': {'turn_id': turn_id}}, now + timedelta(seconds=20)),
                         '回合结束后作业才结束')
        jobs._finish(job_id, None)
        self.assertEqual(self.job_row(turn_id).status, 'done')

    def test_legacy_connection_and_legacy_reader_see_compatible_shape(self):
        self.use(router=route_json(), pet='想你啦！', assistant='今天没有走动数据。')
        legacy = self.connect(group_protocol=False)
        turn_id = self.accepted('陪我聊聊', target='both')
        self.settle()
        pet_text, assistant_text = '【爱宠】想你啦！', '【照料助手】'
        deltas = [frame for frame in legacy.frames if frame['type'] == 'delta']
        self.assertEqual([frame['text'] for frame in deltas], [
            '【爱宠】' + half('想你啦！'), pet_text,
            pet_text + '\n' + assistant_text + half('今天没有走动数据。'),
            pet_text + '\n' + assistant_text + '今天没有走动数据。'])
        self.assertEqual([frame['speaker'] for frame in deltas], ['pet', 'pet', 'assistant', 'assistant'],
                         '旧连接的发言人取正在生成的那位')
        self.assertFalse(any(frame['type'] in ('message_delta', 'message', 'message_status') for frame in legacy.frames))
        frame = [item for item in legacy.frames if item['type'] == 'turn'][-1]
        self.assertEqual((frame['turn']['target'], frame['turn']['status']), ('auto', 'complete'))
        legacy_get = self.client.get(self.path).json
        self.assertEqual([item['target'] for item in legacy_get['turns']], ['auto'], 'both 对旧客户端等同未指定')
        self.assertNotIn('protocol', legacy_get)
        self.assertNotIn('messages', legacy_get['turns'][0])

    def test_single_speaker_routes(self):
        script = self.use(router=route_json(companion=True, professional=False, order=['pet'], intent='social'),
                          pet='想啦！', assistant='不该出现')
        turn_id = self.accepted('想我了吗')
        self.settle()
        self.assertEqual([(item['role'], item['kind']) for item in self.rows(turn_id)], [('pet', 'respond')])
        self.assertEqual(script.of('assistant'), [], '普通闲聊不需要助手凑话')

        self.use(router=route_json(companion=False, professional=True, order=['assistant']),
                 pet='不该出现', assistant='还没有走动数据。')
        turn_id = self.accepted('它今天走了多久')
        self.settle()
        self.assertEqual([(item['role'], item['kind']) for item in self.rows(turn_id)], [('assistant', 'answer')])

    def test_named_pet_professional_request_pet_acknowledges_assistant_answers(self):
        script = self.use(router=route_json(companion=False, professional=True),
                          pet='辛苦啦，走动的事让照料助手告诉你。', assistant='我查了今天的记录：还没有数据。')
        turn_id = self.accepted('小白，我今天有点累，帮我看看走了多久', target='pet')
        self.settle()
        self.assertEqual([(item['role'], item['kind']) for item in self.rows(turn_id)],
                         [('pet', 'acknowledge'), ('assistant', 'answer')])
        pet_prompt = script.of('pet')[0]['items'][0]['content']
        self.assertIn('不回答专业内容，也不报任何数据', pet_prompt)
        self.assertIn('事实、查证或操作的部分由照料助手另发一条', pet_prompt)
        assistant_prompt = script.of('assistant')[0]['items'][0]['content']
        self.assertIn('结论只来自本次工具结果', assistant_prompt)
        self.assertIn('你不替它说话', assistant_prompt)

    def test_named_pet_speaks_first_even_if_router_orders_the_assistant_first(self):
        # spec §6 专项回归：点名爱宠询问专业问题，路由模型给出相反顺序时爱宠仍先接话
        script = self.use(router=route_json(companion=False, professional=True,
                                            order=['assistant', 'pet'], follows=True),
                          pet='辛苦啦，走动的事让照料助手告诉你。', assistant='我查了今天的记录：还没有数据。')
        turn_id = self.accepted('小白，我今天有点累，帮我看看走了多久', target='pet')
        self.settle()
        self.assertEqual([(item['role'], item['kind']) for item in self.rows(turn_id)],
                         [('pet', 'acknowledge'), ('assistant', 'answer')])
        self.assertEqual(json.loads(self.route_row(turn_id).plan_json), [
            {'role': 'pet', 'kind': 'acknowledge', 'follows': False},
            {'role': 'assistant', 'kind': 'answer', 'follows': False}])
        self.assertNotIn('群里爱宠在本轮已经先发了', '\n'.join(user_texts(script.of('assistant')[0]['items'])),
                         '顺序由代码纠正后，第二位不承接这一句')

    def test_explicit_both_with_only_professional_content_keeps_pet_to_one_acknowledgement(self):
        self.use(router=route_json(companion=False, professional=True), pet='我来陪你。', assistant='结论如下。')
        turn_id = self.accepted('帮我查一下走了多久', target='both')
        self.settle()
        self.assertEqual([(item['role'], item['kind']) for item in self.rows(turn_id)],
                         [('pet', 'acknowledge'), ('assistant', 'answer')])
        self.assertEqual(self.turn_row(turn_id).target, 'both')

    def test_pet_handoff_never_receives_or_publishes_the_professional_answer(self):
        question = '小白，成年猫能不能只吃水煮鸡胸肉？'
        script = self.use(router=route_json(companion=False, professional=True, intent='knowledge'),
                          pet='我来陪你。\n\n【照料助手】不建议只吃水煮鸡胸肉。',
                          assistant='不能长期只吃水煮鸡胸肉。')
        turn_id = self.accepted(question, target='pet')
        self.settle()
        pet, assistant = self.rows(turn_id)
        self.assertIn(pet['text'], loop._PET_HANDOFF_LINES)
        self.assertNotIn('鸡胸肉', '\n'.join(user_texts(script.of('pet')[0]['items'])))
        self.assertIn('鸡胸肉', assistant['text'])

    def test_pet_reply_drops_a_second_paragraph_of_unsolicited_advice(self):
        self.use(router=route_json(companion=True, professional=False, intent='social'),
                 pet='我也想你呀！来抱抱。\n\n顺便让助手替你设个定时提醒吧。')
        turn_id = self.accepted('小白，想我了吗？')
        self.settle()
        self.assertEqual(self.rows(turn_id)[0]['text'], '我也想你呀！来抱抱。')

    def test_mixed_request_gives_each_role_only_its_own_original_excerpt(self):
        companion_text = '小白，我想抱抱你'
        professional_text = '帮我查一下今天有多少活动记录'
        script = self.use(router=route_json(companion=True, professional=True,
                                            companionText=companion_text,
                                            professionalText=professional_text),
                          pet='来抱抱～', assistant='今天没有有效观测。')
        self.accepted(f'{companion_text}。顺便{professional_text}。', target='pet')
        self.settle()
        pet_input = '\n'.join(user_texts(script.of('pet')[0]['items']))
        assistant_input = '\n'.join(user_texts(script.of('assistant')[0]['items']))
        self.assertIn(companion_text, pet_input)
        self.assertNotIn(professional_text, pet_input)
        self.assertIn(professional_text, assistant_input)
        self.assertNotIn(companion_text, assistant_input)

    def test_assistant_only_target_does_not_invite_pet(self):
        self.use(router=route_json(companion=True, professional=False), pet='不该出现', assistant='我在。')
        turn_id = self.accepted('你好呀', target='assistant')
        self.settle()
        self.assertEqual([item['role'] for item in self.rows(turn_id)], ['assistant'])

    def test_second_message_can_follow_the_first(self):
        script = self.use(router=route_json(follows=True, intent='knowledge'), pet='我在呢～',
                          assistant='补充一句：睡够十二小时也正常。')
        turn_id = self.accepted('慢慢说')
        self.settle()
        notes = '\n'.join(user_texts(script.of('assistant')[0]['items']))
        self.assertIn('群里爱宠在本轮已经先发了', notes)
        self.assertIn('我在呢～', notes)
        self.assertNotIn('群里爱宠在本轮已经先发了', '\n'.join(user_texts(script.of('pet')[0]['items'])))
        self.assertEqual([item['role'] for item in self.rows(turn_id)], ['pet', 'assistant'])

    def test_second_message_without_follows_does_not_see_the_first(self):
        script = self.use(router=route_json(follows=False), pet='我在呢～', assistant='我在。')
        turn_id = self.accepted('陪我聊聊')
        self.settle()
        self.assertNotIn('群里爱宠在本轮已经先发了', '\n'.join(user_texts(script.of('assistant')[0]['items'])))

    def test_router_order_is_honoured_and_pet_can_follow_assistant(self):
        script = self.use(router=route_json(order=['assistant', 'pet'], follows=True, intent='knowledge'),
                          pet='听完就放心啦。', assistant='睡够十二小时也正常。')
        turn_id = self.accepted('它怎么睡这么多')
        self.settle()
        rows = self.rows(turn_id)
        self.assertEqual([(item['role'], item['position']) for item in rows], [('assistant', 0), ('pet', 1)])
        self.assertIn('群里照料助手在本轮已经先发了', '\n'.join(user_texts(script.of('pet')[0]['items'])))
        self.assertEqual(self.turn_row(turn_id).speaker, 'assistant')


class PermissionTests(GroupBase):
    def test_pet_role_only_sees_read_tools_and_cannot_execute_assistant_tools(self):
        script = self.use(router=route_json(), pet=[
            {'calls': [('query_observations', {'start': '2026-01-01T00:00:00+08:00',
                                               'end': '2026-01-02T00:00:00+08:00'})]},
            '我只能陪陪你。'], assistant='还没有数据。')
        turn_id = self.accepted('陪我聊聊，也查查数据')
        self.settle()
        pet_call = script.of('pet')[0]
        self.assertEqual(sorted(pet_call['tools']), sorted(registry.PET_ROLE_TOOLS))
        self.assertFalse(pet_call['web_search'], '群里只有照料助手能联网')
        self.assertEqual(pet_call['max_output_tokens'], loop.ROLE_BUDGETS['pet']['tokens'])
        self.assertTrue(script.of('assistant')[0]['web_search'])
        pet_run = next(run for run in self.runs_of(turn_id) if run['conclusion'].get('speaker') == 'pet')
        self.assertEqual((pet_run['toolSummary'][0]['name'], pet_run['toolSummary'][0]['allowed'],
                          pet_run['toolSummary'][0]['reason']),
                         ('query_observations', False, 'role_not_allowed'))
        self.assertTrue(any(entry.get('role_retry_reminder') for entry in pet_run['messages']))
        self.assertEqual(self.rows(turn_id)[0]['text'], '我只能陪陪你。')

    def test_pet_role_can_read_shared_memory(self):
        self.use(router=route_json(companion=True, professional=False, order=['pet']), pet=[
            {'calls': [('search_memories', {'keyword': '公园'})]}, '记得呀，我们去公园玩过！'])
        turn_id = self.accepted('还记得公园吗')
        self.settle()
        pet_run = self.runs_of(turn_id)[1]
        self.assertEqual((pet_run['toolSummary'][0]['name'], pet_run['toolSummary'][0]['allowed']),
                         ('search_memories', True))

    def test_role_prompts_split_snapshot_and_tasks(self):
        script = self.use(router=route_json(), pet='想你啦！', assistant='还没有数据。')
        turn_id = self.accepted('陪我聊聊')
        self.settle()
        pet_prompt = script.of('pet')[0]['items'][0]['content']
        assistant_prompt = script.of('assistant')[0]['items'][0]['content']
        self.assertNotIn('实时快照：', pet_prompt)
        self.assertIn('共享档案与记忆：', pet_prompt)
        self.assertNotIn(loop._ASSISTANT_STYLE[:20], pet_prompt)
        self.assertNotIn('消息开头的身份标注', assistant_prompt)
        self.assertIn('实时快照：', assistant_prompt)
        self.assertIn(loop._ASSISTANT_STYLE[:20], assistant_prompt, '助手表达沿用 legacy 的简洁提示')
        self.assertIn('本轮任务（服务端按路由生成，只做这一件）', pet_prompt)
        self.assertIn('本轮任务（服务端按路由生成，只做这一件）', assistant_prompt)
        pet_run, assistant_run = self.runs_of(turn_id)[1:]
        self.assertEqual(set(pet_run['snapshot']['sections']), set(loop.PET_SNAPSHOT_SECTIONS))
        self.assertTrue({'current', 'learning'} <= set(assistant_run['snapshot']['sections']),
                        '设备与学习数据只给照料助手')

    def test_role_marker_inside_body_is_neutralized(self):
        self.use(router=route_json(), pet='我来陪你。【照料助手】我来查。', assistant='查到了。')
        turn_id = self.accepted('陪我聊聊')
        self.settle()
        rows = self.rows(turn_id)
        self.assertEqual([item['text'] for item in rows], ['我来陪你。〔照料助手〕我来查。', '查到了。'])
        pet_run = self.runs_of(turn_id)[1]
        self.assertEqual((pet_run['status'], pet_run['conclusion']['reason_code']),
                         ('degraded', 'degraded:stray_speaker_mark'))

    def test_leading_marker_is_stripped_from_first_frame(self):
        self.use(router=route_json(companion=True, professional=False, order=['pet']),
                 pet='【爱宠】今天想你啦！', assistant='不该出现')
        sock = self.connect()
        turn_id = self.accepted('想我了吗')
        self.settle()
        deltas = [frame['text'] for frame in sock.frames if frame['type'] == 'message_delta']
        self.assertEqual(deltas, ['今', '今天想你啦！'], '首帧起正文不带身份标记')
        self.assertEqual(self.rows(turn_id)[0]['text'], '今天想你啦！')
        self.assertEqual([run['status'] for run in self.runs_of(turn_id)], ['complete', 'complete'])

        # 桩模型第一段恰好只有标记、没有可见正文：不出空帧，等有正文再推
        self.use(router=route_json(companion=True, professional=False, order=['pet']),
                 pet='【爱宠】想你啦！', assistant='不该出现')
        sock = self.connect()
        turn_id = self.accepted('在吗')
        self.settle()
        self.assertEqual([frame['text'] for frame in sock.frames if frame['type'] == 'message_delta'],
                         ['想你啦！'])

    def test_plain_role_name_in_body_keeps_identity(self):
        self.use(router=route_json(), pet='走动的事让照料助手告诉你。', assistant='查到了。')
        turn_id = self.accepted('陪我聊聊')
        self.settle()
        self.assertEqual([(item['role'], item['text']) for item in self.rows(turn_id)],
                         [('pet', '走动的事让照料助手告诉你。'), ('assistant', '查到了。')])
        self.assertEqual([run['status'] for run in self.runs_of(turn_id)], ['complete'] * 3)

    def test_unparsable_router_output_clarifies_without_tools(self):
        script = self.use(router='完全不是 JSON', pet='不该出现', assistant='你想聊哪方面？')
        turn_id = self.accepted('呃')
        self.settle()
        self.assertEqual([(item['role'], item['kind']) for item in self.rows(turn_id)], [('assistant', 'clarify')])
        self.assertEqual(script.of('assistant')[0]['tools'], [])
        route = self.route_row(turn_id)
        self.assertEqual((route.source, route.scope), ('fallback', 'unknown'))

    def test_router_exception_falls_back_to_named_speaker(self):
        self.use(router=[{'raise': True}], pet='你想和我聊什么呀？')
        turn_id = self.accepted('在吗', target='pet')
        self.settle()
        self.assertEqual([(item['role'], item['kind']) for item in self.rows(turn_id)], [('pet', 'clarify')])
        self.assertEqual((self.route_row(turn_id).source, self.route_row(turn_id).scope), ('fallback', 'unknown'))
        self.assertEqual([run['status'] for run in self.runs_of(turn_id)], ['failed', 'complete'])

    def test_out_of_scope_answered_by_assistant_without_tools(self):
        script = self.use(router=route_json(scope='out_of_scope', companion=False), pet='不该出现',
                          assistant='这个我帮不了。')
        turn_id = self.accepted('帮我写段代码')
        self.settle()
        self.assertEqual([item['role'] for item in self.rows(turn_id)], ['assistant'])
        self.assertEqual(script.of('assistant')[0]['tools'], [])
        self.assertFalse(script.of('assistant')[0]['web_search'])
        self.assertIn('这句话不在服务范围内', script.of('assistant')[0]['items'][0]['content'])

        # 范围外的请求里夹着对爱宠说的寒暄：爱宠只接寒暄，范围外的说明仍由助手发
        self.use(router=route_json(scope='out_of_scope', companion=True), pet='我在呢。',
                 assistant='这个我帮不了。')
        turn_id = self.accepted('小白乖，帮我写段代码')
        self.settle()
        self.assertEqual([(item['role'], item['text']) for item in self.rows(turn_id)],
                         [('pet', '我在呢。'), ('assistant', '这个我帮不了。')])

    def test_normalize_enforces_plan_constraints(self):
        def plan(raw, target='auto', quoted_role=None, image_only=False):
            return [(item['role'], item['kind'], item['follows']) for item in router.normalize(
                raw, target=target, quoted_role=quoted_role, image_only=image_only)['plan']]

        both = {'scope': 'allowed', 'intent': 'read', 'companion': True, 'professional': True,
                'order': ['pet', 'assistant']}
        self.assertEqual(plan(dict(both, companion=False, professional=True, order=['assistant'])),
                         [('assistant', 'answer', False)])
        self.assertEqual(plan(dict(both, companion=True, professional=False, order=['pet'])),
                         [('pet', 'respond', False)])
        self.assertEqual(plan(dict(both, companion=True, professional=False, order=['pet']), target='assistant'),
                         [('assistant', 'answer', False)])  # 只点了助手：有寒暄成分也由助手自己回应
        self.assertEqual(plan({'scope': 'allowed', 'intent': 'social', 'companion': False, 'professional': False}),
                         [('pet', 'answer', False)])  # 两位都不需要：默认一位作答
        self.assertEqual(plan({'scope': 'allowed', 'intent': 'knowledge', 'companion': False, 'professional': False}),
                         [('assistant', 'answer', False)])
        self.assertEqual(plan(dict(both, follows=True)), [('pet', 'respond', False), ('assistant', 'answer', True)])
        self.assertEqual(plan(dict(both, order=['assistant', 'pet'], follows=True)),
                         [('assistant', 'answer', False), ('pet', 'respond', True)])
        self.assertEqual(plan(dict(both, companion=True, professional=True, follows=False)),
                         [('pet', 'respond', False), ('assistant', 'answer', False)])
        self.assertEqual(plan(dict(both, companion=False, professional=True), target='pet'),
                         [('pet', 'acknowledge', False), ('assistant', 'answer', False)])  # D3
        self.assertEqual(plan(dict(both, companion=True, professional=False, order=[]),
                              target='pet', quoted_role='assistant'),
                         [('assistant', 'answer', False), ('pet', 'respond', False)])  # 被引用的那位先说
        # 点名爱宠：爱宠先接话，不采纳路由器的相反顺序；爱宠只是承接时同理（承接先说，第二位不承接）
        self.assertEqual(plan(dict(both, companion=False, professional=True, order=['assistant', 'pet'], follows=True),
                              target='pet'),
                         [('pet', 'acknowledge', False), ('assistant', 'answer', False)])
        self.assertEqual(plan(dict(both, order=['assistant', 'pet'], follows=True), target='pet'),
                         [('pet', 'respond', False), ('assistant', 'answer', False)])
        self.assertEqual(plan(dict(both, companion=False, professional=True, order=['assistant', 'pet'], follows=True),
                              target='both'),
                         [('pet', 'acknowledge', False), ('assistant', 'answer', False)])
        # 没有点名时仍按路由器的顺序（两位都回、各有陪伴与专业内容）
        self.assertEqual(plan(dict(both, order=['assistant', 'pet'], follows=True), target='both'),
                         [('assistant', 'answer', False), ('pet', 'respond', True)])
        self.assertEqual(plan({'scope': 'unknown'}, target='pet'), [('pet', 'clarify', False)])
        self.assertEqual(plan({'scope': 'unknown'}), [('assistant', 'clarify', False)])
        self.assertEqual(plan(dict(both, scope='out_of_scope', companion=False, order=['assistant'])),
                         [('assistant', 'answer', False)])
        self.assertEqual(plan(dict(both, scope='out_of_scope', companion=True), target='pet'),
                         [('pet', 'respond', False), ('assistant', 'answer', False)])
        self.assertEqual(plan({'scope': 'allowed', 'intent': 'social', 'companion': False, 'professional': False},
                              image_only=True), [('assistant', 'answer', False)])  # 看图回答归照料助手
        self.assertEqual(plan(dict(both, companion=True, professional=False, order=['dog', 'pet'])),
                         [('pet', 'respond', False)])  # 认不出的 order 被丢弃，不阻断路由

    def test_fallback_route_speaker_rules(self):
        def speaker(**fields):
            return router.fallback_route(reason='x', **fields)['plan']

        self.assertEqual(speaker(target='pet', quoted_role=None), [{'role': 'pet', 'kind': 'clarify', 'follows': False}])
        self.assertEqual(speaker(target='assistant', quoted_role=None)[0]['role'], 'assistant')
        self.assertEqual(speaker(target='both', quoted_role='pet')[0]['role'], 'pet', '被引用的那位优先')
        self.assertEqual(speaker(target='pet', quoted_role=None, image_count=1)[0]['role'], 'assistant',
                         '纯图片消息归照料助手')
        self.assertEqual(speaker(target='pet', quoted_role=None, image_count=1, text='看这张')[0]['role'], 'pet',
                         '带文字的图片消息仍按点名')

    def test_legacy_prompts_unchanged(self):
        """旧路径的系统提示不因群聊改动而变化（钉住摘要；改提示属于另一个决策）。"""
        for search_on, recording_on, expected in (
                (True, True, '90f25b2d3f705216453669275480b3a10da291d6800bec2343027a449224ba85'),
                (False, False, '1ee02e1deeb000f29dd7f7433495db4b9275ff953d5bba61df868746d46dcc19'),
                (True, False, '46111426cb8177dacab8e562eac10babd42680df59834fda5b9971b2885bf321'),
                (False, True, 'b1e33a3d118d7406190e9030c27b13b16d9428e9ca78e3a09d291c1b2f04f64a')):
            with self.subTest(search_on=search_on, recording_on=recording_on):
                digest = hashlib.sha256(loop.system_prompt(search_on, recording_on).encode()).hexdigest()
                self.assertEqual(digest, expected)


if __name__ == '__main__':
    unittest.main()
