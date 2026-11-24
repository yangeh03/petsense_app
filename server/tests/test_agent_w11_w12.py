"""W11 → W12 已确认发布资格（confirmed publication eligibility）的端到端测试。

场景取自**真实的回顾→收件箱生命周期**：W09 周期回顾开主题并交付一条回合，
W11 在该次运行的不可变轨迹里给出「模型明确 high + 发现被接受」的发布溯源，
W12 的投影据此把该条目判定为个人高相关推送候选。

覆盖：
- 严格相等 `provenance == 'model_high_accepted'` + 已交付回合（宠物与运行精确匹配）
  + 主题本身是回合里交付的个人变化（证据 id 精确匹配）→ 条目成为推送候选；
- **绝不抬升**：normal / 默认高相关（legacy_default_*）/ 安静档（无交付回合）/
  证据缺失（无运行）/ 运行与宠物不匹配 / 回合与运行不匹配 / 同一次回顾的其它发现 /
  主题证据不在回合的个人变化块里 → 都不是推送候选；
- **重启后由投影恢复**：条目先建、运行与回合后到，再次投影即恢复资格；
- 既有中心策略不受影响：warning/critical 设备告警与到期回信仍是候选。
"""
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import fresh_db, new_conversation
from sqlalchemy import select

from petsense import db
from petsense.agent import inbox, inbox_publication
from petsense.db import utc_now
from petsense.db.models import AuthSession, Pet, User
from petsense.db.models.agent import (
    AgentCareTopic,
    AgentInboxItem,
    AgentRun,
    AgentTask,
    AgentTaskDelivery,
    AgentTaskPlan,
    AgentTurn,
)

WINDOW = '2026-11-23'
EVIDENCE_ID = 'actions:2026-11-23:1'     # W11 冻结证据的稳定引用（feature:date:slot）


def _sha(value):
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()


def _frozen_evidence(evidence_id=EVIDENCE_ID):
    """W11 冻结证据的最小形态（handle_review 经 change.freeze 写入主题与回合）。"""
    return {'evidenceId': evidence_id, 'feature': 'actions', 'dimension': '动作',
            'date': '2026-11-23', 'slot': 1, 'slotLabel': '上午 06–12 点',
            'value': 7000, 'direction': 'above',
            'range': {'p10': 700, 'p50': 700, 'p90': 700},
            'note': '动作上午 06–12 点实际 116.7 分钟，高于平时范围。'}


class W11W12PublicationTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.now = utc_now()
        with db.session_scope() as s:
            user = User(username='w11w12', display_name='w11w12', password_hash='x', salt='s',
                        avatar_emoji='🙂', bio='', created_at=self.now)
            s.add(user)
            s.flush()
            self.uid = user.id
            self.token = 'token-w11w12'
            s.add(AuthSession(token_hash=_sha(self.token), user_id=user.id,
                              expires_at=self.now + timedelta(days=7)))
            pet = Pet(user_id=user.id, name='豆豆', species='dog', device_id='dev-w11w12',
                      created_at=self.now)
            s.add(pet)
            s.flush()
            self.pet_id = pet.id
            other = User(username='w11w12-other', display_name='o', password_hash='x', salt='s',
                         avatar_emoji='🙂', bio='', created_at=self.now)
            s.add(other)
            s.flush()
            self.other_uid = other.id
            other_pet = Pet(user_id=other.id, name='别的', species='dog', device_id='dev-other',
                            created_at=self.now)
            s.add(other_pet)
            s.flush()
            self.other_pet_id = other_pet.id
        # W16：回合与委托必须属于某个会话
        self.conversations = {pet: new_conversation(pet) for pet in (self.pet_id, self.other_pet_id)}

    # ── 夹具：真实回顾生命周期的三件东西 ──────────────────────────────────
    def _review_run(self, *, provenance='model_high_accepted', pet_id=None,
                    window=WINDOW, delivered_by='w09_review'):
        """W09 回顾运行（W11 的不可变轨迹）：结论里带发布溯源。"""
        pet_id = pet_id or self.pet_id
        with db.session_scope() as s:
            run = AgentRun(pet_id=pet_id, turn_id=None, form='review', status='complete',
                           conclusion=json.dumps({
                               'reason_code': 'ok', 'window': window,
                               'deliveredBy': delivered_by,
                               'publication': {'provenance': provenance, 'relevance': 'high',
                                               'modelLevel': 'high' if provenance.startswith(
                                                   'model_high') else 'normal',
                                               'acceptedFindings': 1, 'acceptedChanges': 1},
                           }, ensure_ascii=False),
                           messages='[]', tool_summary='[]', budget='{}',
                           created_at=self.now)
            s.add(run)
            s.flush()
            return run.id

    def _review_turn(self, run_id, *, pet_id=None, kind='daily', window=WINDOW, link=True,
                     changes=(EVIDENCE_ID,)):
        """W09 已交付的回顾回合：care 块带 runId（与运行的精确链接）。

        changes 为回合里交付的个人变化证据 id（personal_change 块）；同一次回顾的
        覆盖/观察类发现只开主题，不进这些块。
        """
        pet_id = pet_id or self.pet_id
        with db.session_scope() as s:
            turn = AgentTurn(pet_id=pet_id, conversation_id=self.conversations[pet_id],
                             request_id='care:review:%s:%d:%s' % (kind, pet_id, window),
                             text='', target='assistant', topic_id=None, reply_to=None,
                             sources='[]', speaker='assistant', reply='回顾已送达',
                             status='complete', created_at=self.now,
                             blocks=json.dumps(
                                 [{'type': 'text', 'speaker': 'assistant', 'text': '回顾已送达'}]
                                 + [{'type': 'personal_change', 'change': _frozen_evidence(item)}
                                    for item in changes]
                                 + [{'type': 'care', 'care': {'kind': 'review', 'category': 'daily',
                                                             'title': '周期回顾', 'status': 'info',
                                                             'runId': run_id if link else 999999}}],
                                 ensure_ascii=False))
            s.add(turn)
            s.flush()
            return turn.id

    def _review_topic(self, *, run_id=None, pet_id=None, window=WINDOW,
                      category='personal_change', evidence_ids=(EVIDENCE_ID,), anchor=None):
        """W09 回顾开出的关怀主题（kind='observation'）。

        个人变化主题带冻结证据行（锚点 change:<evidenceId>）；其它类别没有冻结证据。
        """
        self._enable()          # 启用之后创建的来源才入箱（D5）
        pet_id = pet_id or self.pet_id
        with db.session_scope() as s:
            rows = [{'at': '2026-11-23T09:00:00.000Z', 'kind': 'personal_change',
                     'note': '个人变化', 'change': _frozen_evidence(item)}
                    for item in evidence_ids]
            rows.append({'at': '2026-11-23T09:00:00.000Z', 'kind': 'review',
                         'note': '个人变化' if category == 'personal_change' else '其它发现',
                         'window': window})
            topic = AgentCareTopic(pet_id=pet_id, kind='observation', category=category,
                                   anchor=anchor or ('change:%s' % evidence_ids[0]
                                                     if evidence_ids else 'change:slot-1'),
                                   instance=1, status='open',
                                   severity='info', title='走动比平时多了',
                                   detail='今天比平时多走了一些',
                                   evidence=json.dumps(rows, ensure_ascii=False),
                                   delivered=0, run_id=run_id, opened_at=self.now,
                                   updated_at=self.now)
            s.add(topic)
            s.flush()
            return topic.id

    def _enable(self):
        """首次投影 = 启用（对齐部署语义）：启用之前创建的来源不入箱。"""
        if getattr(self, '_enabled', False):
            return
        self._enabled = True
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)

    def _project(self):
        """投影（含富化）：条目创建后运行/回合到达时由它恢复资格。"""
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)

    def _care_item(self, session):
        return session.execute(select(AgentInboxItem).where(
            AgentInboxItem.source_kind == 'care')).scalar_one_or_none()

    def _push_kinds(self):
        with db.session_scope() as s:
            return {row.source_kind for row in inbox.push_candidates(s, self.uid)}

    # ── 正例：已确认的模型高相关发布 ──────────────────────────────────────
    def test_accepted_publication_becomes_push_candidate(self):
        run_id = self._review_run()
        self._review_turn(run_id)
        self._review_topic(run_id=run_id)
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertIsNotNone(row)
            self.assertEqual(row.relevance, 'high')
            self.assertEqual(row.frozen_evidence, 1)
            self.assertEqual(row.delivery_approved, 1)
            self.assertEqual(row.high_relevance, 1)
            self.assertEqual(row.push_candidate, 1)
            self.assertEqual(row.published, 1)
            self.assertEqual({c.source_kind for c in inbox.push_candidates(s, self.uid)}, {'care'})

    def test_publication_is_recovered_by_projection_after_restart(self):
        """条目先建（还没有运行与交付回合），随后运行与回合到达 → 投影恢复资格。"""
        self._review_topic()
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.push_candidate, 0)
            self.assertEqual(row.relevance, '')
        run_id = self._review_run()
        self._review_turn(run_id)
        with db.session_scope() as s:
            item = self._care_item(s)
            topic = s.get(AgentCareTopic, json.loads(item.source_ref)['topicId'])
            topic.run_id = run_id
        # 相当于重启后再次投影：按不可变轨迹恢复资格
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.relevance, 'high')
            self.assertEqual(row.push_candidate, 1)

    # ── 反例：绝不抬升 ────────────────────────────────────────────────────
    def test_normal_publication_is_not_elevated(self):
        run_id = self._review_run(provenance='model_normal_accepted')
        self._review_turn(run_id)
        self._review_topic(run_id=run_id)
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.push_candidate, 0)
            self.assertEqual(row.high_relevance, 0)
            self.assertEqual(row.relevance, '')

    def test_default_high_publication_is_not_elevated(self):
        """模型未给档位的默认高相关（legacy_default_high）不算已确认。"""
        run_id = self._review_run(provenance='legacy_default_high')
        self._review_turn(run_id)
        self._review_topic(run_id=run_id)
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.push_candidate, 0)
            self.assertEqual(row.high_relevance, 0)

    def test_quiet_mode_without_delivery_is_not_elevated(self):
        """安静档：没有交付回合 → 交付未批准 → 不是推送候选。"""
        run_id = self._review_run()
        self._review_topic(run_id=run_id)      # 不建交付回合
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.published, 0)
            self.assertEqual(row.push_candidate, 0)
            self.assertEqual(row.delivery_approved, 0)

    def test_missing_evidence_is_not_elevated(self):
        """主题没有运行 id（证据缺失）→ 不抬升。"""
        self._review_topic()
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.push_candidate, 0)
            self.assertEqual(row.frozen_evidence, 0)

    def test_run_of_other_pet_is_not_elevated(self):
        run_id = self._review_run(pet_id=self.other_pet_id)
        self._review_turn(run_id, pet_id=self.other_pet_id)
        self._review_topic(run_id=run_id)
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.push_candidate, 0)

    def test_turn_not_linked_to_run_is_not_elevated(self):
        run_id = self._review_run()
        self._review_turn(run_id, link=False)     # care 块 runId 对不上
        self._review_topic(run_id=run_id)
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.push_candidate, 0)

    def test_window_mismatch_is_not_elevated(self):
        run_id = self._review_run(window='2026-11-16')
        self._review_turn(run_id, window='2026-11-16')
        self._review_topic(run_id=run_id)          # 主题窗口是 2026-11-23
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.push_candidate, 0)

    def test_sibling_finding_of_same_review_is_not_elevated(self):
        """同一次回顾的覆盖/观察类发现挂同一个 run id，但没有冻结证据 → 不抬升。

        取自真实模型回顾（E66）：一次回顾同时开出「上午走动明显高于平时」
        「昨天仅上午有数据覆盖」「声音维度仍在学习中」三个主题，只有前者是个人变化。
        """
        run_id = self._review_run()
        self._review_turn(run_id)
        self._review_topic(run_id=run_id, category='coverage', evidence_ids=(),
                           anchor='coverage_20261123')
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.relevance, '')
            self.assertEqual(row.frozen_evidence, 0)
            self.assertEqual(row.delivery_approved, 0)
            self.assertEqual(row.high_relevance, 0)
            self.assertEqual(row.push_candidate, 0)
            self.assertEqual(inbox.push_candidates(s, self.uid), [])

    def test_change_not_delivered_in_turn_is_not_elevated(self):
        """主题的冻结证据不在已交付回合的个人变化块里 → 主题与回合对不上，不抬升。"""
        run_id = self._review_run()
        self._review_turn(run_id, changes=())       # 回合没交付任何个人变化块
        self._review_topic(run_id=run_id)
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.push_candidate, 0)
            self.assertEqual(row.high_relevance, 0)

    def test_merged_evidence_row_is_recognized(self):
        """合并提报给既有主题追加的是 `evidence` 行（不是 `change` 行），同样算冻结证据。"""
        run_id = self._review_run()
        self._review_turn(run_id)
        topic_id = self._review_topic(run_id=run_id)
        with db.session_scope() as s:
            topic = s.get(AgentCareTopic, topic_id)
            topic.evidence = json.dumps([{
                'at': '2026-11-24T09:00:00.000Z', 'kind': 'review', 'note': '再次提到',
                'window': WINDOW, 'evidence': _frozen_evidence()}], ensure_ascii=False)
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.push_candidate, 1)
            self.assertEqual(row.frozen_evidence, 1)

    def test_change_of_other_slot_in_turn_is_not_elevated(self):
        """回合交付的是另一个时段的证据，不替本主题作数。"""
        run_id = self._review_run()
        self._review_turn(run_id, changes=('actions:2026-11-23:2',))
        self._review_topic(run_id=run_id)
        self._project()
        with db.session_scope() as s:
            row = self._care_item(s)
            self.assertEqual(row.push_candidate, 0)

    # ── 既有中心策略不受影响 ──────────────────────────────────────────────
    def test_device_and_reply_candidates_still_apply(self):
        from petsense.agent import settings as settings_service
        with db.session_scope() as s:
            settings_service.persist(s, self.uid, [('proactivity', 0)])   # 安静档
        self._enable()   # 先启用：来源在启用之后创建（历史补入不追溯推送）
        with db.session_scope() as s:
            topic = AgentCareTopic(pet_id=self.pet_id, kind='device', category='battery_low',
                                   anchor='dev-w11w12', instance=1, status='open',
                                   severity='critical', title='t', detail='d', evidence='[]',
                                   delivered=0, opened_at=self.now, updated_at=self.now)
            s.add(topic)
            s.flush()
            s.add(AgentTurn(pet_id=self.pet_id, conversation_id=self.conversations[self.pet_id],
                            request_id='care:event:%d:open' % topic.id,
                            text='', target='assistant', topic_id=topic.id, reply_to=None,
                            sources='[]', speaker='assistant', reply='设备告警', status='complete',
                            created_at=self.now, blocks='[]'))
            task = AgentTask(pet_id=self.pet_id, conversation_id=self.conversations[self.pet_id],
                             task_key='t1', current_version=1,
                             status='observing', timezone='Asia/Shanghai',
                             deadline_at=self.now + timedelta(days=1), effective_from=self.now,
                             paused_intervals='[]', created_at=self.now, updated_at=self.now)
            s.add(task)
            s.flush()
            s.add(AgentTaskPlan(task_id=task.id, version=1,
                                payload=json.dumps({'title': '电量观察', 'kind': 'fact_watch'}),
                                created_at=self.now))
            delivery = AgentTaskDelivery(task_id=task.id, plan_version=1, kind='letter',
                                         instance_key='letter:v1', turn_id=None,
                                         payload=json.dumps({'text': '回信正文'}, ensure_ascii=False),
                                         created_at=self.now)
            s.add(delivery)
            s.flush()
            s.add(AgentTurn(pet_id=self.pet_id, conversation_id=self.conversations[self.pet_id],
                            request_id='task-%d' % delivery.id,
                            text='', target='assistant', topic_id=None, reply_to=None,
                            sources='[]', speaker='assistant', reply='回信正文', status='complete',
                            created_at=self.now, blocks='[]'))
            delivery.turn_id = s.execute(select(AgentTurn.id).where(
                AgentTurn.request_id == 'task-%d' % delivery.id)).scalar_one()
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            kinds = {row.source_kind for row in inbox.push_candidates(s, self.uid)}
            self.assertEqual(kinds, {'device_fact', 'reply'})

    # ── 解析器的直接断言 ──────────────────────────────────────────────────
    def test_resolver_requires_strict_provenance(self):
        run_id = self._review_run(provenance='model_high_accepted')
        self._review_turn(run_id)
        topic_id = self._review_topic(run_id=run_id)
        with db.session_scope() as s:
            topic = s.get(AgentCareTopic, topic_id)
            evidence = inbox_publication.resolve_accepted_publication(s, topic)
            self.assertIsNotNone(evidence)
            self.assertEqual(evidence['provenance'], 'model_high_accepted')
            self.assertEqual(evidence['runId'], run_id)


if __name__ == '__main__':
    unittest.main()
