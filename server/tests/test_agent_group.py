"""双 Agent 群聊：受理与确定性派发、爱宠转交与角色权限（docs/agent-w18-spec.md）。

W18 起没有前置意图路由：派发由主人的选择决定；默认模式下爱宠判断自己答不了时
调用 handoff_to_assistant 把专业部分转交照料助手。
"""
import hashlib
import json
import threading
import unittest
from datetime import timedelta

from group_testkit import PROTOCOL, GroupBase, pet_says, say, user_texts
from petsense.agent import group, jobs, loop, registry
from petsense.db import session_scope, utc_now
from petsense.db.models.agent import AgentMessage
from sqlalchemy import select


def half(text):
    """模型桩分两段出字时的第一段。"""
    return text[:len(text) // 2]


class DispatchTests(GroupBase):
    """派发：由主人的选择决定谁发言，不看消息内容，也没有额外的模型调用。"""

    def test_post_accepts_immediately_and_dispatches_without_a_router_call(self):
        script = self.use(pet='想你啦！', assistant=say('我查了今天的记录：暂时没有数据。'))
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

        pet_text = '想你啦！'
        messages = self.rows(turn_id)
        self.assertEqual([(item['role'], item['kind'], item['status'], item['position']) for item in messages],
                         [('pet', 'respond', 'complete', 0)])
        self.assertEqual([item['text'] for item in messages], [pet_text])
        self.assertRegex(messages[0]['id'], r'^m\d+$')
        self.assertEqual([item['version'] for item in messages], [4], '领取、首个增量落库、收尾各递增一次')
        self.assertTrue(all(item['readOnly'] is False for item in messages))

        turn = self.turn_row(turn_id)
        self.assertEqual(turn.status, 'complete')
        self.assertEqual(turn.reply, f'【爱宠】{pet_text}')
        self.assertEqual((turn.speaker, turn.target), ('pet', 'auto'))

        route = self.route_row(turn_id)
        self.assertEqual((route.scope, route.source), ('unknown', 'target'), '派发记录不含路由判定')
        self.assertEqual([item['role'] for item in json.loads(route.plan_json)], ['pet'])

        run_list = self.runs_of(turn_id)
        self.assertEqual([run['form'] for run in run_list], ['dialogue'], '没有路由运行')
        self.assertEqual([run['conclusion'].get('speaker') for run in run_list], ['pet'])
        self.assertEqual(script.of('assistant'), [], '默认模式下爱宠自己答得了就只有它一条')
        self.assertEqual(len(script.calls), 1, '一轮只调一次模型')

        kinds = [frame['type'] for frame in sock.frames]
        self.assertEqual(kinds.count('turn'), 2, kinds)  # 派发落库后的排队快照 + 收尾快照
        opened = [frame['message'] for frame in sock.frames
                  if frame['type'] == 'message' and frame['message']['status'] == 'running']
        self.assertEqual([item['role'] for item in opened], ['pet'])
        deltas = [frame for frame in sock.frames if frame['type'] == 'message_delta']
        self.assertEqual([frame['text'] for frame in deltas], [half(pet_text), pet_text])
        self.assertEqual([frame['messageId'] for frame in deltas], [messages[0]['id']] * 2,
                         '同一发言从首字到完成用同一个 ID')
        self.assertFalse(any(frame['type'] == 'delta' for frame in sock.frames), '群聊连接不收旧的增量帧')
        final = sock.frames[-1]
        self.assertEqual((final['type'], final['turn']['status']), ('turn', 'complete'))

    def test_auto_hands_off_to_the_assistant_when_the_pet_cannot_answer(self):
        script = self.use(
            pet=pet_says('这个我不懂，让照料助手帮你看看。', '查今天走了多久'),
            assistant=say('我查了今天的记录：还没有走动数据。'))
        turn_id = self.accepted('它今天走了多久')
        self.settle()

        messages = self.rows(turn_id)
        self.assertEqual([(item['role'], item['kind'], item['status'], item['position']) for item in messages],
                         [('pet', 'handoff', 'complete', 0), ('assistant', 'answer', 'complete', 1)])
        self.assertIn('让照料助手', messages[0]['text'])
        self.assertIn('还没有走动数据', messages[1]['text'])
        self.assertNotIn('还没有走动数据', messages[0]['text'], '专业答案只由照料助手给')
        self.assertEqual(len(script.of('pet')), 1, '转交后不再追问模型')
        self.assertEqual(len(script.of('assistant')), 1)

        turn = self.turn_row(turn_id)
        self.assertEqual(turn.status, 'complete')
        self.assertEqual(turn.reply, f'【爱宠】{messages[0]["text"]}\n【照料助手】{messages[1]["text"]}')
        route = self.route_row(turn_id)
        self.assertEqual((route.scope, route.intent, route.source), ('allowed', 'read', 'target'),
                         '照料助手的自报范围回填到派发记录')
        self.assertEqual([item['role'] for item in json.loads(route.plan_json)], ['pet'],
                         '派发计划只有爱宠：助手那条是转交后追加的')
        runs = self.runs_of(turn_id)
        self.assertEqual([run['form'] for run in runs], ['dialogue', 'dialogue'])
        self.assertEqual(runs[0]['conclusion']['reason_code'], 'handoff')
        pet_call = next(call for call in runs[0]['toolSummary'] if call['name'] == 'handoff_to_assistant')
        self.assertTrue(pet_call['allowed'])
        assistant_input = '\n'.join(user_texts(script.of('assistant')[0]['items']))
        self.assertIn('爱宠已经把这条消息里需要查证', assistant_input)
        self.assertIn('这个我不懂，让照料助手帮你看看。', assistant_input)

    def test_explicit_pet_answers_alone_and_never_hands_off(self):
        """D1：只点了爱宠，专业问题也只由爱宠回答——它说不懂并建议去问照料助手。"""
        script = self.use(pet='这个我不懂，你问照料助手看看。', assistant=say('不该出现'))
        turn_id = self.accepted('它今天走了多久', target='pet')
        self.settle()
        messages = self.rows(turn_id)
        self.assertEqual([(item['role'], item['kind']) for item in messages], [('pet', 'respond')])
        self.assertIn('照料助手', messages[0]['text'])
        self.assertEqual(script.of('assistant'), [], '只选一位时不产生另一位的消息')
        pet_call = script.of('pet')[0]
        self.assertNotIn('handoff_to_assistant', pet_call['tools'], '该模式不挂转交工具')
        self.assertEqual(len(script.of('pet')), 1)

    def test_handoff_call_is_denied_when_this_turn_does_not_allow_it(self):
        """转交工具没挂上时，即使模型硬调也执行不了（权限在代码里，不只在提示词里）。"""
        script = self.use(pet=pet_says('这个让照料助手帮你看看。'), assistant=say('不该出现'))
        turn_id = self.accepted('它今天走了多久', target='pet')
        self.settle()
        pet_run = self.runs_of(turn_id)[0]
        call = next(item for item in pet_run['toolSummary'] if item['name'] == 'handoff_to_assistant')
        self.assertFalse(call['allowed'])
        self.assertEqual(call['reason'], 'role_not_allowed')
        self.assertEqual([item['role'] for item in self.rows(turn_id)], ['pet'])

    def test_explicit_assistant_answers_alone(self):
        script = self.use(pet='不该出现', assistant=say('我查了今天的记录：还没有走动数据。'))
        turn_id = self.accepted('它今天走了多久', target='assistant')
        self.settle()
        messages = self.rows(turn_id)
        self.assertEqual([(item['role'], item['kind']) for item in messages], [('assistant', 'answer')])
        self.assertEqual(script.of('pet'), [], '不请爱宠插话')
        self.assertIn('query_observations', script.of('assistant')[0]['tools'])
        self.assertNotIn('handoff_to_assistant', script.of('assistant')[0]['tools'])

    def test_both_gives_two_messages_in_order(self):
        script = self.use(pet='来抱抱～', assistant=say('今天没有有效观测。'))
        turn_id = self.accepted('想我了吗，顺便查下今天走了多久', target='both')
        self.settle()
        messages = self.rows(turn_id)
        self.assertEqual([(item['role'], item['kind'], item['position']) for item in messages],
                         [('pet', 'respond', 0), ('assistant', 'answer', 1)])
        self.assertEqual([item['text'] for item in messages], ['来抱抱～', '今天没有有效观测。'])
        self.assertEqual(len(script.of('pet')), 1)
        self.assertEqual(len(script.of('assistant')), 1)
        pet_call = script.of('pet')[0]
        self.assertNotIn('handoff_to_assistant', pet_call['tools'], '两位都回时不需要转交')
        assistant_prompt = script.of('assistant')[0]['items'][0]['content']
        self.assertIn('只回答主人明确请求的专业部分', assistant_prompt)

    def test_dispatch_depends_only_on_the_chosen_target(self):
        """派发只看主人本轮的选择：同一句话在四种选择下派给不同的人。"""
        for target, roles in (('auto', ['pet']), ('pet', ['pet']),
                              ('assistant', ['assistant']), ('both', ['pet', 'assistant'])):
            with self.subTest(target=target):
                self.use(pet='我在呢。', assistant=say('我查了今天的记录。'))
                turn_id = self.accepted('它今天走了多久', target=target)
                self.settle()
                self.assertEqual([item['role'] for item in self.rows(turn_id)], roles)

    def test_quoted_message_reaches_the_speaker_without_changing_the_plan(self):
        script = self.use(pet='我在呢～', assistant=say('按今天的走动记录算的。'))
        first = self.accepted('它今天走了多久', target='assistant')
        self.settle()
        quote = self.rows(first)[0]['id']
        second = self.accepted('那它昨晚呢', target='assistant', replyTo=first, replyToMessage=quote)
        self.settle()
        assistant_input = '\n'.join(user_texts(script.of('assistant')[1]['items']))
        self.assertIn('主人这条消息引用了群里照料助手', assistant_input)
        self.assertEqual([item['role'] for item in self.rows(second)], ['assistant'],
                         '引用只进上下文，不改派发')


class HandoffTests(GroupBase):
    """转交：爱宠调用工具把专业部分交给照料助手。"""

    def test_handoff_tool_is_only_mounted_for_the_pet_in_auto_mode(self):
        script = self.use(pet='我在呢。', assistant=say('我查了今天的记录。'))
        turn_id = self.accepted('陪我聊聊')
        self.settle()
        pet_tools = script.of('pet')[0]['tools']
        self.assertEqual(sorted(pet_tools), sorted(['read_pet_profile', 'search_memories',
                                                    'handoff_to_assistant']))
        assistant_tools = [item['name'] for item in registry.schemas_for(
            registry.DIALOGUE, 'allowed', role='assistant')]
        self.assertNotIn('handoff_to_assistant', assistant_tools, '转交只由爱宠发起')
        pet_without_handoff = [item['name'] for item in registry.schemas_for(
            registry.DIALOGUE, 'allowed', role='pet')]
        self.assertNotIn('handoff_to_assistant', pet_without_handoff, '不挂转交工具时不出现')

    def test_pet_cannot_call_assistant_tools_even_with_the_handoff_tool(self):
        script = self.use(pet=[
            {'calls': [('query_observations', {'start': '2026-01-01T00:00:00+08:00',
                                               'end': '2026-01-02T00:00:00+08:00'})]},
            '我只能陪陪你。'], assistant=say('还没有数据。'))
        turn_id = self.accepted('陪我聊聊，也查查数据')
        self.settle()
        pet_run = self.runs_of(turn_id)[0]
        self.assertEqual((pet_run['toolSummary'][0]['name'], pet_run['toolSummary'][0]['allowed'],
                          pet_run['toolSummary'][0]['reason']),
                         ('query_observations', False, 'role_not_allowed'))
        self.assertTrue(any(entry.get('role_retry_reminder') for entry in pet_run['messages']))
        self.assertEqual(self.rows(turn_id)[0]['text'], '我只能陪陪你。')

    def test_handoff_with_empty_pet_reply_falls_back_to_a_server_line(self):
        script = self.use(pet=pet_says('', '查今天走了多久'),
                          assistant=say('我查了今天的记录：还没有走动数据。'))
        turn_id = self.accepted('它今天走了多久')
        self.settle()
        messages = self.rows(turn_id)
        self.assertEqual(messages[0]['text'], loop._PET_HANDOFF_LINES[0], '爱宠没说话时用服务端兜底承接句')
        self.assertEqual(messages[0]['kind'], 'handoff')
        self.assertIn('还没有走动数据', messages[1]['text'])
        pet_run = self.runs_of(turn_id)[0]
        self.assertIn('pet_handoff_default_line', pet_run['conclusion']['reason_code'])

    def test_handoff_state_survives_a_restart(self):
        """转交状态落在消息行上：只重试失败的那条时，助手那条仍会被跑起来。"""
        self.use(pet=pet_says('这个让照料助手帮你看看。', '查数据'),
                 assistant=say('我查了今天的记录：还没有走动数据。'))
        turn_id = self.accepted('它今天走了多久')
        self.settle()
        self.assertEqual([item['kind'] for item in self.rows(turn_id)], ['handoff', 'answer'])

        # 模拟「爱宠已完成、助手这条失败」：只把助手那条退回失败再重试
        with session_scope() as s:
            row = s.execute(select(AgentMessage).where(
                AgentMessage.turn_id == turn_id, AgentMessage.role == 'assistant')).scalar_one()
            row.status, row.text, row.error = 'failed', '', 'interrupted'
        retry = self.retry(turn_id)
        self.assertEqual(retry.status_code, 202, retry.get_data(as_text=True))
        self.settle()
        rows = self.rows(turn_id)
        self.assertEqual([item['status'] for item in rows], ['complete', 'complete'])
        self.assertEqual(rows[0]['kind'], 'handoff', '重试沿用同一份派发与转交状态')
        self.assertEqual(len(rows), 2, '不会多出一条助手消息')

    def test_handoff_is_not_repeated_when_the_turn_is_processed_again(self):
        self.use(pet=pet_says('这个让照料助手帮你看看。', '查数据'),
                 assistant=say('我查了今天的记录：还没有走动数据。'))
        turn_id = self.accepted('它今天走了多久')
        self.settle()
        group.process_turn(turn_id)
        self.settle()
        messages = self.rows(turn_id)
        self.assertEqual(len(messages), 2, '重复进入不会多出一条助手消息')
        self.assertEqual([item['role'] for item in messages], ['pet', 'assistant'])


class PermissionTests(GroupBase):
    def test_assistant_self_declared_scope_gates_its_tools(self):
        """W18：范围由照料助手自己这一次调用给出——自报范围外时工具调用被拒，之后不再挂工具。"""
        script = self.use(pet=pet_says('这个让照料助手帮你看看。', '查数据'), assistant=[
            {'text': '{"scope":"out_of_scope","intent":"social","speaker":"assistant"}\n这个我帮不了。',
             'calls': [('query_observations', {'start': '2026-01-01T00:00:00+08:00',
                                               'end': '2026-01-02T00:00:00+08:00'})]},
            say('这个我帮不了。', scope='out_of_scope')])
        turn_id = self.accepted('帮我写段代码')
        self.settle()
        messages = self.rows(turn_id)
        self.assertEqual([item['role'] for item in messages], ['pet', 'assistant'])
        assistant_run = self.runs_of(turn_id)[1]
        self.assertEqual((assistant_run['toolSummary'][0]['name'], assistant_run['toolSummary'][0]['allowed'],
                          assistant_run['toolSummary'][0]['reason']),
                         ('query_observations', False, 'scope_not_allowed'))
        self.assertEqual(script.of('assistant')[1]['tools'], [], '自报范围外之后不再挂工具')
        self.assertFalse(script.of('assistant')[1]['web_search'])
        self.assertIn('范围声明', script.of('assistant')[0]['items'][0]['content'])
        self.assertEqual(self.route_row(turn_id).scope, 'out_of_scope')

    def test_pet_role_only_sees_read_tools_and_cannot_execute_assistant_tools(self):
        script = self.use(pet='我只能陪陪你。', assistant=say('还没有数据。'))
        turn_id = self.accepted('陪我聊聊', target='both')
        self.settle()
        pet_call = script.of('pet')[0]
        self.assertFalse(pet_call['web_search'], '群里只有照料助手能联网')
        self.assertEqual(pet_call['max_output_tokens'], loop.ROLE_BUDGETS['pet']['tokens'])
        self.assertTrue(script.of('assistant')[0]['web_search'])
        self.assertEqual(self.rows(turn_id)[0]['text'], '我只能陪陪你。')

    def test_pet_role_can_read_shared_memory(self):
        self.use(pet=[{'calls': [('search_memories', {'keyword': '公园'})]}, '记得呀，我们去公园玩过！'])
        turn_id = self.accepted('还记得公园吗')
        self.settle()
        pet_run = self.runs_of(turn_id)[0]
        self.assertEqual((pet_run['toolSummary'][0]['name'], pet_run['toolSummary'][0]['allowed']),
                         ('search_memories', True))

    def test_role_prompts_split_snapshot_and_tasks(self):
        script = self.use(pet='想你啦！', assistant=say('还没有数据。'))
        turn_id = self.accepted('陪我聊聊', target='both')
        self.settle()
        pet_prompt = script.of('pet')[0]['items'][0]['content']
        assistant_prompt = script.of('assistant')[0]['items'][0]['content']
        self.assertNotIn('实时快照：', pet_prompt)
        self.assertIn('共享档案与记忆：', pet_prompt)
        self.assertNotIn(loop._ASSISTANT_STYLE[:20], pet_prompt)
        self.assertNotIn('消息开头的身份标注', assistant_prompt)
        self.assertIn('实时快照：', assistant_prompt)
        self.assertIn(loop._ASSISTANT_STYLE[:20], assistant_prompt, '助手表达沿用 legacy 的简洁提示')
        self.assertIn('本轮任务（服务端按主人的选择与转交生成，只做这一件）', pet_prompt)
        self.assertIn('本轮任务（服务端按主人的选择与转交生成，只做这一件）', assistant_prompt)
        pet_run, assistant_run = self.runs_of(turn_id)
        self.assertEqual(set(pet_run['snapshot']['sections']), set(loop.PET_SNAPSHOT_SECTIONS))
        self.assertTrue({'current', 'learning'} <= set(assistant_run['snapshot']['sections']),
                        '设备与学习数据只给照料助手')

    def test_role_marker_inside_body_is_neutralized(self):
        self.use(pet='我来陪你。【照料助手】我来查。', assistant=say('查到了。'))
        turn_id = self.accepted('陪我聊聊')
        self.settle()
        rows = self.rows(turn_id)
        self.assertEqual([item['text'] for item in rows], ['我来陪你。〔照料助手〕我来查。'])
        pet_run = self.runs_of(turn_id)[0]
        self.assertEqual((pet_run['status'], pet_run['conclusion']['reason_code']),
                         ('degraded', 'degraded:stray_speaker_mark'))

    def test_leading_marker_is_stripped_from_first_frame(self):
        self.use(pet='【爱宠】今天想你啦！')
        sock = self.connect()
        turn_id = self.accepted('想我了吗')
        self.settle()
        deltas = [frame['text'] for frame in sock.frames if frame['type'] == 'message_delta']
        self.assertEqual(deltas, ['今', '今天想你啦！'], '首帧起正文不带身份标记')
        self.assertEqual(self.rows(turn_id)[0]['text'], '今天想你啦！')

    def test_plain_role_name_in_body_keeps_identity(self):
        self.use(pet='走动的事让照料助手告诉你。')
        turn_id = self.accepted('陪我聊聊')
        self.settle()
        self.assertEqual([(item['role'], item['text']) for item in self.rows(turn_id)],
                         [('pet', '走动的事让照料助手告诉你。')])

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


class LifecycleTests(GroupBase):
    def test_job_stays_open_until_turn_settles_without_resubmitting(self):
        gate = threading.Event()
        script = self.use(pet={'text': '想你啦！', 'gate': gate})
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
        self.use(pet='想你啦！', assistant=say('今天没有走动数据。'))
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
        self.assertFalse(any(frame['type'] in ('message_delta', 'message', 'message_status')
                             for frame in legacy.frames))
        frame = [item for item in legacy.frames if item['type'] == 'turn'][-1]
        self.assertEqual((frame['turn']['target'], frame['turn']['status']), ('auto', 'complete'))
        legacy_get = self.client.get(self.path).json
        self.assertEqual([item['target'] for item in legacy_get['turns']], ['auto'], 'both 对旧客户端等同未指定')
        self.assertNotIn('protocol', legacy_get)
        self.assertNotIn('messages', legacy_get['turns'][0])


if __name__ == '__main__':
    unittest.main()
