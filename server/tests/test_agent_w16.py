"""W16 多会话测试（docs/agent-w16-spec.md §8）：会话接口、历史隔离、后台归属、帧与删除边界。

不依赖真实模型：对话路径的模型往返用桩代替，并记录每次调用拿到的输入，用来断言「模型只看到当前
会话的历史」。迁移回填见 test_migrations，深链见 test_agent_w12，行为评测 E80–E81 见 agent_behavior。
"""
import json
import unittest
from datetime import timedelta
from itertools import count
from unittest.mock import patch

from flask import Flask
from group_testkit import FakeSocket, user_texts
from helpers import TurnClient, fresh_db
from test_agent import allowed_stance, model_turn
from test_pets import seed_user_with_pet

from petsense import db
from petsense.agent import care, conversations, deliver, tasks as task_service, ws
from petsense.agent import routes as agent_routes
from petsense.agent.conversation_routes import bp as conversation_bp
from petsense.agent.inbox_routes import bp as inbox_bp
from petsense.db import utc_now
from petsense.db.models import Pet
from petsense.db.models.agent import (AgentCareTopic, AgentConversation, AgentTask, AgentTaskPlan, AgentTurn)
from sqlalchemy import select

AUTH_TARGETS = ('petsense.agent.routes.require_app_user',
                'petsense.agent.conversation_routes.require_app_user',
                'petsense.agent.inbox_routes.require_app_user')
_keys = count(1)


class ConversationApiTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        self.app = Flask(__name__)
        for blueprint in (agent_routes.bp, conversation_bp, inbox_bp):
            self.app.register_blueprint(blueprint)
        for target in AUTH_TARGETS:
            auth = patch(target, return_value=({'id': self.uid}, None))
            auth.start()
            self.addCleanup(auth.stop)
        with db.session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id
        self.client = TurnClient(self.app.test_client(), self.pet)
        self.path = f'/api/app/agent/pets/{self.pet}/turns'
        self.base = f'/api/app/agent/pets/{self.pet}/conversations'

    def say(self, text, conversation_id=None, key=None, **fields):
        """发一条消息（模型打桩），返回 (响应, 每次模型调用拿到的输入)。key 是请求键，不给就自动生成。"""
        calls = []
        reply = allowed_stance('assistant', 'read') + '好的。'

        def respond(items, tools=None, on_delta=None, **_kwargs):
            calls.append(items)
            if on_delta is not None:
                on_delta(reply)  # 走一遍流式：同步路径据此推增量帧
            return model_turn(reply)

        body = {'text': text, 'requestId': key or 'w16-%d' % next(_keys), **fields}
        if conversation_id is not None:
            body['conversationId'] = conversation_id
        with patch('petsense.agent.model.respond', side_effect=respond):
            response = self.client.post(self.path, json=body)
        return response, calls

    def background(self, request_key, text, conversation_id):
        with db.session_scope() as s:
            turn, _created = deliver.deliver_background_message(
                s, self.pet, request_key, [{'type': 'text', 'speaker': 'assistant', 'text': text}],
                conversation_id=conversation_id, now=utc_now())
            return turn.id

    # ── 接口 ──────────────────────────────────────────────────────────────

    def test_create_reuses_the_latest_empty_conversation(self):
        """连点「新对话」不会留下一串空会话：最近一个仍为空且未改名的直接复用。"""
        self.say('先说一句，把默认会话占上')
        first = self.client.post(self.base)
        again = self.client.post(self.base)
        self.assertEqual((first.status_code, first.json['created']), (201, True))
        self.assertEqual((again.status_code, again.json['created']), (200, False))
        self.assertEqual(again.json['conversation']['id'], first.json['conversation']['id'])
        # 改过名的空会话不复用
        named = self.client.patch(f"/api/app/agent/conversations/{first.json['conversation']['id']}",
                                  json={'title': '等着用'})
        self.assertEqual(named.status_code, 200)
        third = self.client.post(self.base)
        self.assertEqual((third.status_code, third.json['created']), (201, True))
        self.assertNotEqual(third.json['conversation']['id'], first.json['conversation']['id'])

    def test_first_message_titles_the_conversation_and_feeds_the_summary(self):
        response, _calls = self.say('  今天散步走了很久很久很久很久很久  ')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        summary = response.json['conversation']
        self.assertEqual(summary['title'], '今天散步走了很久很久很久很久很久')
        self.assertEqual(summary['titleCustom'], False)
        self.assertEqual((summary['turnCount'], summary['preview']['text']), (1, '今天散步走了很久很久很久很久很久'))
        self.assertEqual(response.json['turn']['conversationId'], summary['id'])
        self.assertNotIn('topicId', response.json['turn'])
        # 标题取前 20 个字，之后的消息不再改标题
        self.say('第二句话不该变成标题')
        item = self.client.get(self.base).json['conversations'][0]
        self.assertEqual((item['title'], item['turnCount'], item['preview']['text']),
                         ('今天散步走了很久很久很久很久很久', 2, '第二句话不该变成标题'))

    def test_image_only_first_message_gets_a_fixed_title(self):
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            conversation, _created = conversations.create_chat(s, pet)
            conversations.auto_title(conversation, '')
            self.assertEqual(conversation.title, '图片消息')

    def test_rename_validation_and_custom_title_sticks(self):
        response, _calls = self.say('这是自动生成的标题')
        url = f"/api/app/agent/conversations/{response.json['conversation']['id']}"
        for bad in ('', '   ', '字' * 41, 12, None):
            self.assertEqual(self.client.patch(url, json={'title': bad}).status_code, 400)
        named = self.client.patch(url, json={'title': '  我们的   约定  '})
        self.assertEqual((named.status_code, named.json['conversation']['title'],
                          named.json['conversation']['titleCustom']), (200, '我们的 约定', True))
        # 主人会话：改过名之后新消息不再改标题
        self.say('再来一句话')
        self.assertEqual(self.client.get(self.base).json['conversations'][0]['title'], '我们的 约定')

    def test_another_account_cannot_see_or_change_a_conversation(self):
        conversation_id = self.client.conversation_id
        with patch('petsense.agent.conversation_routes.require_app_user',
                   return_value=({'id': self.uid + 1}, None)):
            self.assertEqual(self.client.patch(f'/api/app/agent/conversations/{conversation_id}',
                                               json={'title': 'x'}).status_code, 404)
            self.assertEqual(self.client.delete(f'/api/app/agent/conversations/{conversation_id}').status_code, 404)
            self.assertEqual(self.client.post(f'/api/app/agent/conversations/{conversation_id}/read',
                                              json={}).status_code, 404)
            self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_listing_orders_by_activity_and_paginates(self):
        first = self.client.conversation_id
        self.say('甲会话', key='l1')
        second = self.client.post(self.base).json['conversation']['id']
        self.say('乙会话', conversation_id=second, key='l2')
        self.say('甲会话又说了一句', key='l3')  # 甲最近活动：排在最前
        listing = self.client.get(f'{self.base}?limit=2').json
        self.assertEqual([item['id'] for item in listing['conversations']], [first, second])
        self.assertEqual((listing['total'], listing['hasMore']), (2, False))
        page = self.client.get(f'{self.base}?limit=1&offset=1').json
        self.assertEqual([item['id'] for item in page['conversations']], [second])
        self.assertEqual(page['hasMore'], False)

    def test_empty_conversations_sort_after_conversations_with_activity(self):
        """空会话（刚建完还没说话）排在有内容的会话后面。

        按创建时间倒序的话，后建的空会话会盖在有内容的会话上面；本机没记住上次会话时
        §6.1 的「取列表第一个」还会把主人直接落到一个空会话里。
        """
        with_content = self.client.conversation_id
        self.say('有内容的一句', key='o1')
        empty = self.client.post(self.base).json['conversation']['id']
        listing = self.client.get(self.base).json['conversations']
        self.assertEqual([item['id'] for item in listing], [with_content, empty])
        self.assertEqual((listing[0]['turnCount'], listing[1]['turnCount']), (1, 0))
        # 空会话排在后面，但仍然在列表里、也没有被软删除
        self.assertIsNone(listing[1]['lastTurnAt'])
        self.assertEqual(self.client.get(f'{self.base}?limit=abc').status_code, 400)

    # ── 历史只取当前会话 ──────────────────────────────────────────────────

    def test_history_only_covers_the_current_conversation(self):
        first = self.client.conversation_id
        self.say('我家狗叫豆豆', key='h1')
        second = self.client.post(self.base).json['conversation']['id']
        response, calls = self.say('它叫什么名字', conversation_id=second, key='h2')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        sent = user_texts(calls[-1])
        self.assertTrue(any('它叫什么名字' in item for item in sent))
        self.assertFalse(any('豆豆' in item for item in sent), '另一个会话里说过的话不进本会话的模型输入')
        # 同一会话的追问带着上一轮
        response, calls = self.say('那你再说说它的名字', conversation_id=first, key='h3')
        self.assertTrue(any('豆豆' in item for item in user_texts(calls[-1])))
        self.assertEqual(len(self.client.raw.get(f'{self.path}?conversationId={first}').json['turns']), 2)
        self.assertEqual(len(self.client.raw.get(f'{self.path}?conversationId={second}').json['turns']), 1)

    def test_quote_and_request_key_are_scoped_to_the_conversation(self):
        first = self.client.conversation_id
        quoted = self.say('第一句话', key='q1')[0].json['turn']['id']
        second = self.client.post(self.base).json['conversation']['id']
        refused, _calls = self.say('接着说', conversation_id=second, key='q2', replyTo=quoted)
        self.assertEqual((refused.status_code, refused.json['message']), (404, '消息引用不存在'))
        self.assertEqual(self.client.raw.get(f'{self.path}?conversationId={second}').json['turns'], [])
        same_key, _calls = self.say('另一句话', conversation_id=second, key='q1')
        self.assertEqual(same_key.status_code, 409, '同键但不同会话视为另一条消息')
        allowed, _calls = self.say('接着说', key='q3', replyTo=quoted)
        self.assertEqual((allowed.status_code, allowed.json['turn']['replyTo']), (200, quoted))

    def test_care_conversation_tells_the_model_which_alert_it_is_about(self):
        with db.session_scope() as s:
            topic = care.open_topic(s, self.pet, 'device', 'battery_low', 'collar-16',
                                    title='项圈电量偏低', now=utc_now())
            conversation = conversations.for_topic(s, topic)
            deliver.deliver_background_message(
                s, self.pet, 'care:event:%d:open' % topic.id,
                [{'type': 'text', 'speaker': 'assistant', 'text': '项圈电量偏低'}],
                conversation_id=conversation.id, now=utc_now())
            conversation_id, topic_id = conversation.id, topic.id
        response, calls = self.say('为什么提醒我？', conversation_id=conversation_id, key='why')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        system = calls[-1][0]['content']
        self.assertIn('主题 #%d' % topic_id, system)
        self.assertIn('项圈电量偏低', system)

    def test_care_hint_is_absent_for_ordinary_conversations(self):
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            conversation, _created = conversations.create_chat(s, pet)
            self.assertIsNone(conversations.care_hint(s, conversation))
            self.assertEqual(conversations.history(s, self.pet, 10 ** 9, conversation.id), [])

    # ── 已读 ──────────────────────────────────────────────────────────────

    def test_read_marks_the_whole_conversation_and_returns_its_summary(self):
        conversation_id = self.client.conversation_id
        for index in range(2):
            self.background('bg-%d' % index, '提醒%d' % index, conversation_id)
        item = self.client.get(self.base).json['conversations'][0]
        self.assertEqual((item['unread'], item['turnCount'], item['preview']['text']), (2, 2, '提醒1'))
        marked = self.client.post(f'/api/app/agent/conversations/{conversation_id}/read', json={}).json
        self.assertEqual((marked['markedCount'], marked['conversation']['unread'], len(marked['turns'])),
                         (2, 0, 2))
        self.assertEqual(self.client.get(self.base).json['conversations'][0]['unread'], 0)
        self.assertEqual(self.client.post(f'/api/app/agent/conversations/{conversation_id}/read',
                                          json={'ids': []}).status_code, 400)

    # ── 删除 ──────────────────────────────────────────────────────────────

    def test_deleted_conversation_is_invisible_to_reads_and_sends(self):
        response, _calls = self.say('留在这条会话里的话', key='d1')
        conversation_id = response.json['conversation']['id']
        url = f'/api/app/agent/conversations/{conversation_id}'
        self.assertEqual(self.client.delete(url).json, {'success': True})
        self.assertEqual(self.client.raw.get(f'{self.path}?conversationId={conversation_id}').status_code, 404)
        refused, _calls = self.say('还能发吗', conversation_id=conversation_id, key='d2')
        self.assertEqual((refused.status_code, refused.json['message']), (404, '会话不存在'))
        self.assertEqual(self.client.get(self.base).json['conversations'], [])
        self.assertEqual(self.client.delete(url).status_code, 200)  # 重复删除幂等
        # 回合本身还在（轨迹与审计需要），只是不再可见
        with db.session_scope() as s:
            self.assertIsNotNone(s.get(AgentTurn, response.json['turn']['id']))

    def test_delete_refuses_while_a_task_or_an_alert_is_open(self):
        conversation_id = self.say('帮我留意电量', key='g0')[0].json['conversation']['id']
        url = f'/api/app/agent/conversations/{conversation_id}'
        now = utc_now()
        with db.session_scope() as s:
            task = AgentTask(pet_id=self.pet, conversation_id=conversation_id, task_key='busy',
                             status='observing', deadline_at=now + timedelta(days=1), effective_from=now,
                             created_at=now, updated_at=now)
            s.add(task)
            s.flush()
            task_id = task.id
        refused = self.client.delete(url)
        self.assertEqual((refused.status_code, refused.json['code'], refused.json['activeTasks']),
                         (409, 'conversation_busy', 1))
        self.assertIn('委托', refused.json['message'])
        # 数字两侧留空格：与「标题需为 1 到 N 个字」等文案口径一致
        self.assertEqual(refused.json['message'], '这个会话里还有 1 个进行中的委托，结束后才能删除')
        with db.session_scope() as s:
            s.get(AgentTask, task_id).status = 'completed'
            topic = care.open_topic(s, self.pet, 'device', 'battery_low', 'collar-busy', now=now)
            topic.conversation_id = conversation_id  # 告警自带的会话就是要删的这个
            topic_id = topic.id
        refused = self.client.delete(url)
        self.assertEqual((refused.status_code, refused.json['openCareTopics']), (409, 1))
        self.assertEqual(refused.json['message'], '这个会话里还有 1 个未结束的提醒，结束后才能删除')
        with db.session_scope() as s:
            s.get(AgentCareTopic, topic_id).status = 'closed'
        self.assertEqual(self.client.delete(url).status_code, 200)

    def test_task_book_reports_its_conversation(self):
        conversation_id = self.client.conversation_id
        now = utc_now()
        with db.session_scope() as s:
            task = AgentTask(pet_id=self.pet, conversation_id=conversation_id, task_key='book',
                             status='completed', deadline_at=now, effective_from=now,
                             created_at=now, updated_at=now)
            s.add(task)
            s.flush()
            s.add(AgentTaskPlan(task_id=task.id, version=1, created_at=now,
                                payload=json.dumps({'title': '电量观察', 'kind': 'fact_watch'})))
            s.flush()
            payload = task_service.public(s, task)
            self.assertEqual((payload['conversationId'], payload['conversationDeleted']),
                             (conversation_id, False))
            self.assertNotIn('topicId', payload)
            s.get(AgentConversation, conversation_id).deleted_at = now
            self.assertTrue(task_service.public(s, task)['conversationDeleted'])

    # ── 帧 ────────────────────────────────────────────────────────────────

    def test_turn_and_stream_frames_carry_the_conversation(self):
        socket = FakeSocket()
        ws.clients[socket] = self.uid
        self.addCleanup(ws.clients.pop, socket, None)
        conversation_id = self.client.conversation_id
        self.say('你好呀', key='f1')
        frames = list(socket.frames)  # FakeSocket 存的已经是解析后的帧
        self.assertEqual(frames[-1]['type'], 'turn')
        self.assertEqual({frame['conversationId'] for frame in frames}, {conversation_id})
        final = frames[-1]
        self.assertEqual([frame['type'] for frame in frames][-2:], ['delta', 'turn'])  # 流式增量也带着会话
        self.assertEqual(final['conversation']['id'], conversation_id)
        self.assertEqual(final['turn']['conversationId'], conversation_id)
        self.assertNotIn('topicId', final['turn'])


class CareConversationTests(unittest.TestCase):
    """主动消息自带的系统会话：主题 ↔ 会话一对一，标题跟随主题，回顾汇总各成一个会话。"""

    def setUp(self):
        fresh_db(self)
        seed_user_with_pet()
        with db.session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id
        self.now = utc_now()

    def test_topic_gets_one_conversation_and_its_title_follows_the_topic(self):
        with db.session_scope() as s:
            topic = care.open_topic(s, self.pet, 'device', 'battery_low', 'collar-c',
                                    title='项圈电量偏低', now=self.now)
            conversation = conversations.for_topic(s, topic, self.now)
            conversation_id, topic_id = conversation.id, topic.id
            self.assertEqual((topic.conversation_id, conversation.kind, conversation.care_kind),
                             (conversation_id, 'care', 'device'))
            again = conversations.for_topic(s, topic, self.now)
            self.assertEqual(again.id, conversation_id)  # 之后的消息进同一个会话
            topic.title, topic.status = '电量很低', 'ongoing'
            conversations.sync_care_title(conversation, topic)
            self.assertEqual(conversation.title, '电量很低')
            conversations.rename(conversation, '我关心的电量', self.now)
            topic.title = '又改了标题'
            conversations.sync_care_title(conversation, topic)
            self.assertEqual(conversation.title, '我关心的电量')  # 主人改过名就不跟随
            self.assertEqual(len(s.execute(select(AgentConversation)).scalars().all()), 1)

    def test_care_hint_names_the_topic_and_its_status(self):
        with db.session_scope() as s:
            topic = care.open_topic(s, self.pet, 'device', 'battery_low', 'collar-h',
                                    title='项圈电量偏低', now=self.now)
            conversation = conversations.for_topic(s, topic, self.now)
            self.assertEqual(conversations.care_hint(s, conversation),
                             '本会话来自主动提醒：主题 #%d「项圈电量偏低」（进行中）' % topic.id)
            topic.status = 'recovered'
            self.assertTrue(conversations.care_hint(s, conversation).endswith('（已恢复）'))

    def test_review_titles_use_the_window_in_the_pets_timezone(self):
        self.assertEqual(conversations.review_title('daily', '2026-11-24'), '观察小结 · 11月24日')
        self.assertEqual(conversations.review_title('weekly', '2026-11-23'), '本周回顾 · 11/23–11/29')

    def test_background_message_requires_a_conversation_and_touches_it(self):
        with db.session_scope() as s:
            with self.assertRaises(ValueError):
                deliver.deliver_background_message(s, self.pet, 'x-1', [], conversation_id=None)
            chat, _created = conversations.create_chat(s, s.get(Pet, self.pet))
            older = self.now - timedelta(hours=1)
            deliver.deliver_background_message(s, self.pet, 'x-2',
                                               [{'type': 'text', 'speaker': 'assistant', 'text': '先一条'}],
                                               conversation_id=chat.id, now=older)
            turn, created = deliver.deliver_background_message(
                s, self.pet, 'x-2', [{'type': 'text', 'speaker': 'assistant', 'text': '后一条'}],
                conversation_id=chat.id, now=self.now)
            self.assertFalse(created)                      # 同键幂等：不重复落回合，活动时间也不动
            self.assertEqual(chat.last_turn_at, older)
            # 回合里的预览取首段正文
            self.assertEqual(conversations.public(s, chat)['preview']['text'], '先一条')


if __name__ == '__main__':
    unittest.main()
