"""W08 单元/集成测试：确认与计划版本、状态机、检查点调度、交付与恢复（agent-w08-spec.md §4）。"""
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from helpers import fresh_db
from test_agent import model_turn
from test_pets import seed_user_with_pet

from petsense import db
from petsense.agent import checkpoint as checkpoint_service
from petsense.agent import jobs, proposals as proposal_service
from petsense.agent import tasks as task_service
from petsense.agent.routes import bp as agent_bp
from petsense.db import utc_now
from petsense.db.models import Pet, TelemetryMinute, User
from petsense.db.models.agent import (AgentJob, AgentProposal, AgentRun, AgentTask,
                                      AgentTaskDelivery, AgentTaskPlan, AgentTurn)
from petsense.db.models.memory import PetMemory
from sqlalchemy import select

SHANGHAI = ZoneInfo('Asia/Shanghai')


def seed_minutes(uid, device, start_local, hours, walk_per_minute=30):
    """按上海本地时间起种 hours 小时有效分钟观测（每分钟走动 walk_per_minute 秒）。"""
    minute = start_local.astimezone(ZoneInfo('UTC'))
    with db.session_scope() as s:
        for i in range(hours * 60):
            s.add(TelemetryMinute(owner_id=uid, device_id=device, minute=minute + timedelta(minutes=i),
                                  metrics='{}', actions=json.dumps({'走动': walk_per_minute}),
                                  observed_seconds=60, frames=60))


class W08TestCase(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        self.app = Flask(__name__)
        self.app.register_blueprint(agent_bp)
        self.client = self.app.test_client()
        self.auth = patch('petsense.agent.routes.require_app_user',
                          return_value=({'id': self.uid}, None))
        self.auth.start()
        self.addCleanup(self.auth.stop)
        with db.session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id
        self.now = utc_now()
        jobs.register_handler(task_service.CHECKPOINT_KIND, task_service.handle_checkpoint_job)
        self.addCleanup(lambda: jobs._HANDLERS.pop(task_service.CHECKPOINT_KIND, None))
        with db.session_scope() as s:
            turn = AgentTurn(pet_id=self.pet, request_id='seed-turn', text='帮我留意一下',
                             target='auto', reply='', sources='[]', speaker='assistant',
                             status='complete', created_at=self.now)
            s.add(turn)
            s.flush()
            self.turn1 = turn.id

    def _pet_row(self, s):
        return s.get(Pet, self.pet)

    def _propose(self, plan=None, action='create', task_id=None, version=None, turn_id=None):
        """经工具执行器生成 task_change 候选（走 registry 权限矩阵），返回 (proposal_row, gate)。"""
        from petsense.agent import registry
        args = {'action': action, 'plan': plan or self._plan()}
        if task_id is not None:
            args.update({'taskId': task_id, 'expectedVersion': version})
        context = {'pet_id': self.pet, 'user_id': self.uid, 'turn_id': turn_id or self.turn1}
        gate = registry.execute_tool('propose_task_change', args, registry.DIALOGUE, 'allowed', context)
        with db.session_scope() as s:
            row = s.execute(select(AgentProposal).where(
                AgentProposal.kind == 'task_change').order_by(AgentProposal.id.desc())).scalars().first()
        return row, gate

    def _plan(self, **overrides):
        plan = {'kind': 'fact_watch', 'question': '今天下午它走动会不会超过两小时',
                'feature': 'actions',
                'condition': {'metric': 'walkSeconds', 'op': 'at_least', 'value': 7200, 'scope': 'window'},
                'startAt': self.now.isoformat(), 'deadlineAt': (self.now + timedelta(days=1)).isoformat()}
        plan.update(overrides)
        return {k: v for k, v in plan.items() if v is not None}

    def _decide(self, proposal_id, action='confirm'):
        response = self.client.post(f'/api/app/agent/pets/{self.pet}/proposals/{proposal_id}/decision',
                                    json={'action': action})
        return response

    def _task(self):
        with db.session_scope() as s:
            return s.execute(select(AgentTask)).scalar_one()

    def _job(self):
        with db.session_scope() as s:
            return s.execute(select(AgentJob)).scalar_one_or_none()


class PlanNormalizationTests(W08TestCase):
    def test_default_deadline_and_week_semantics_guard(self):
        payload = task_service.normalize_plan({'kind': 'scheduled_review',
                                               'question': '周五晚上告诉我这周的情况'}, now=self.now)
        deadline = task_service.iso_z_parse(payload['deadlineAt'])
        start = task_service.iso_z_parse(payload['startAt'])
        self.assertEqual(deadline - start, timedelta(days=3))  # D1：缺省 3 天

    def test_span_and_past_deadline_rejected(self):
        with self.assertRaises(task_service.TaskError):
            task_service.normalize_plan({'kind': 'fact_watch', 'question': '太长的委托',
                                        'feature': 'actions',
                                        'startAt': self.now.isoformat(),
                                        'deadlineAt': (self.now + timedelta(days=31)).isoformat()},
                                       now=self.now)
        with self.assertRaises(task_service.TaskError):
            task_service.normalize_plan({'kind': 'fact_watch', 'question': '过期委托',
                                        'feature': 'actions',
                                        'startAt': (self.now - timedelta(days=2)).isoformat(),
                                        'deadlineAt': (self.now - timedelta(hours=1)).isoformat()},
                                       now=self.now)

    def test_comparison_requires_band_and_strategy_default(self):
        payload = task_service.normalize_plan({'kind': 'comparison', 'question': '它是不是比平时走得少',
                                               'feature': 'actions',
                                               'comparison': {'band': 'below'}}, now=self.now)
        self.assertEqual(payload['comparison'], {'band': 'below'})
        self.assertEqual(payload['baselineStrategy'], 'fact_first')
        with self.assertRaises(task_service.TaskError):
            task_service.normalize_plan({'kind': 'comparison', 'question': '没有方向的比较',
                                        'feature': 'actions'}, now=self.now)

    def test_condition_metric_must_match_feature(self):
        with self.assertRaises(task_service.TaskError) as ctx:
            task_service.normalize_plan({'kind': 'fact_watch', 'question': '盯叫声次数',
                                        'feature': 'actions',
                                        'condition': {'metric': 'soundEventCount', 'op': 'at_least',
                                                      'value': 10, 'scope': 'daily'}}, now=self.now)
        self.assertIn('不匹配', str(ctx.exception))


class ConfirmationTests(W08TestCase):
    """W08-A1 确认与计划版本。"""

    def test_pending_proposal_creates_no_task_or_job(self):
        row, gate = self._propose()
        self.assertEqual(row.status, 'pending')
        self.assertFalse(gate['result']['written'])
        self.assertIn('不得声称已开始观察', gate['result']['guidance'])
        self.assertIsNone(self._task() if False else None)
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(AgentTask)).scalars().all(), [])
            self.assertEqual(s.execute(select(AgentJob)).scalars().all(), [])

    def test_confirm_creates_single_task_and_job(self):
        row, _ = self._propose()
        response = self._decide(row.id)
        self.assertEqual(response.status_code, 200)
        task = self._task()
        self.assertEqual(task.status, 'observing')
        self.assertEqual(task.current_version, 1)
        job = self._job()
        self.assertEqual(job.kind, 'task.checkpoint')
        self.assertEqual(job.dedup_key, f'task:{task.id}:v1')
        self.assertEqual(json.loads(job.payload_json), {'taskId': task.id, 'version': 1})
        # 重复决定幂等：不新建任务/作业
        self.assertEqual(self._decide(row.id).status_code, 200)
        with db.session_scope() as s:
            self.assertEqual(len(s.execute(select(AgentTask)).scalars().all()), 1)
            self.assertEqual(len(s.execute(select(AgentJob)).scalars().all()), 1)
        with db.session_scope() as s:
            run_ids = s.execute(select(AgentRun.id)).scalars().all()
        self.assertEqual(run_ids, [])

    def test_edit_supersedes_and_only_chosen_version_lives(self):
        first, _ = self._propose(turn_id=1)
        second, _ = self._propose(plan=self._plan(question='改成今天下午走动超过三小时提醒我',
                                                 condition={'metric': 'walkSeconds', 'op': 'at_least',
                                                            'value': 10800, 'scope': 'window'}), turn_id=2)
        with db.session_scope() as s:
            refreshed = s.get(AgentProposal, first.id)
            self.assertEqual(refreshed.status, 'superseded')
            self.assertEqual(refreshed.superseded_by, second.id)
        self.assertEqual(self._decide(first.id).status_code, 409)  # 旧决策拒绝
        self.assertEqual(self._decide(second.id).status_code, 200)
        task = self._task()
        plan = plan_of_db(task)
        self.assertEqual(plan['condition']['value'], 10800)

    def test_cross_account_denied(self):
        row, _ = self._propose()
        with db.session_scope() as s:
            other = User(username='other', display_name='o', password_hash='x', salt='s',
                         avatar_emoji='🙂', bio='', created_at=utc_now())
            s.add(other)
            s.flush()
            other_id = other.id
        self.auth.stop()
        with patch('petsense.agent.routes.require_app_user', return_value=({'id': other_id}, None)):
            response = self.client.post(
                f'/api/app/agent/pets/{self.pet}/proposals/{row.id}/decision', json={'action': 'confirm'})
            self.assertEqual(response.status_code, 404)
            tasks_response = self.client.get(f'/api/app/agent/pets/{self.pet}/tasks')
            self.assertEqual(tasks_response.status_code, 404)
        self.auth.start()

    def test_tool_rejects_stale_version_reference(self):
        row, _ = self._propose()
        self.assertEqual(self._decide(row.id).status_code, 200)
        task = self._task()
        _, gate = self._propose(action='pause', task_id=task.id, version=99)
        self.assertIn('已更新', gate['result']['error'])


class LifecycleTests(W08TestCase):
    """W08-A2 暂停、恢复、取消与续期（API 侧；窗口证据在 EvidenceTests）。"""

    def _confirmed_task(self, **plan_overrides):
        row, _ = self._propose(plan=self._plan(**plan_overrides))
        self.assertEqual(self._decide(row.id).status_code, 200)
        return self._task()

    def test_pause_resume_via_book(self):
        task = self._confirmed_task()
        response = self.client.post(f'/api/app/agent/pets/{self.pet}/tasks/{task.id}/ops',
                                    json={'op': 'pause'})
        self.assertEqual(response.status_code, 200)
        task = self._task()
        self.assertEqual(task.status, 'paused')
        intervals = json.loads(task.paused_intervals)
        self.assertEqual(len(intervals), 1)
        self.assertIsNone(intervals[0].get('end'))
        # 幂等：重复暂停返回当前态
        self.assertEqual(self.client.post(f'/api/app/agent/pets/{self.pet}/tasks/{task.id}/ops',
                                          json={'op': 'pause'}).status_code, 200)
        response = self.client.post(f'/api/app/agent/pets/{self.pet}/tasks/{task.id}/ops',
                                    json={'op': 'resume'})
        self.assertEqual(response.status_code, 200)
        intervals = json.loads(self._task().paused_intervals)
        self.assertIsNotNone(intervals[0]['end'])
        self.assertEqual(self._task().status, 'observing')

    def test_cancel_stops_job_and_inflight_delivery(self):
        task = self._confirmed_task()
        # 模拟在途检查：先入队领取后取消 —— 提交阶段应丢弃结果
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'remind', 'text': '它走动超过两小时了',
                                 'run_id': None, 'error': None}):
            with db.session_scope() as s:
                jobs.enqueue(s, task_service.CHECKPOINT_KIND,
                             {'taskId': task.id, 'version': task.current_version},
                             dedup_key=f'task:{task.id}:v{task.current_version}',
                             next_run_at=self.now)
            cancel = self.client.post(f'/api/app/agent/pets/{self.pet}/tasks/{task.id}/ops',
                                      json={'op': 'cancel'})
            self.assertEqual(cancel.status_code, 200)
            self.assertEqual(self._task().status, 'cancelled')
            # 取消后跑一次调度：不投递、作业完成
            jobs.tick(now=self.now + timedelta(seconds=1), limit=2)
            with db.session_scope() as s:
                self.assertEqual(s.execute(select(AgentTaskDelivery)).scalars().all(), [])
                self.assertEqual(s.execute(select(AgentTurn).where(AgentTurn.request_id.like('task-%'))
                                           ).scalars().all(), [])

    def test_invalid_transitions_409_with_state(self):
        task = self._confirmed_task()
        response = self.client.post(f'/api/app/agent/pets/{self.pet}/tasks/{task.id}/ops',
                                    json={'op': 'resume'})
        self.assertEqual(response.status_code, 200)  # 幂等：非暂停态 resume 返回当前态
        self.assertEqual(self.client.post(f'/api/app/agent/pets/{self.pet}/tasks/{task.id}/ops',
                                          json={'op': 'noop'}).status_code, 400)

    def test_renew_appends_version_and_keeps_letters(self):
        task = self._confirmed_task()
        # 交付一封 v1 回信后续期
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'letter', 'text': 'v1 的回信：观察期内走动 60 分钟。',
                                 'run_id': None, 'error': None}):
            with db.session_scope() as s:
                t = s.get(AgentTask, task.id)
                t.deadline_at = self.now  # 到期
                s.flush()
            jobs.tick(now=self.now + timedelta(seconds=5), limit=2)
        self.assertEqual(self._task().status, 'completed')
        row, _ = self._propose(action='renew', task_id=task.id, version=1,
                               plan=self._plan(question='再观察一天', deadlineAt=(self.now + timedelta(days=1)).isoformat()))
        self.assertEqual(self._decide(row.id).status_code, 200)
        task = self._task()
        self.assertEqual(task.status, 'observing')
        self.assertEqual(task.current_version, 2)
        with db.session_scope() as s:
            letters = s.execute(select(AgentTaskDelivery)).scalars().all()
            self.assertEqual(len(letters), 1)  # 旧回信保留
            plans = s.execute(select(AgentTaskPlan).where(AgentTaskPlan.task_id == task.id)
                              .order_by(AgentTaskPlan.version)).scalars().all()
            self.assertEqual([p.version for p in plans], [1, 2])

    def test_update_rejected_on_stale_version(self):
        task = self._confirmed_task()
        # 生成基于 v1 的修改候选（尚未确认），随后另一候选先确认把任务推进到 v2
        stale, _ = self._propose(action='update', task_id=task.id, version=1,
                                 plan=self._plan(question='基于旧版本的修改'))
        newer, _ = self._propose(action='update', task_id=task.id, version=1,
                                 plan=self._plan(question='基于旧版本的另一修改'))
        with db.session_scope() as s:
            self.assertEqual(s.get(AgentProposal, stale.id).status, 'superseded')  # 同 kind pending 后者替代前者
        self.assertEqual(self._decide(newer.id).status_code, 200)
        self.assertEqual(self._task().current_version, 2)
        # 再造一条真正过期的 v1 候选并决策 → 409（并发旧决策返回最新状态语义）
        # 该路径模拟「候选生成时版本尚新、决策前被并发推进」，绕过工具层预检直接落候选
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            payload = task_service.normalize_proposal_fields(
                {'action': 'update', 'plan': self._plan(question='迟到一拍的修改'),
                 'taskId': task.id, 'expectedVersion': 1})
            row, _ = proposal_service.create_task_proposal(s, pet, None, payload)
        response = self._decide(row.id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.data)['message'], '委托计划已更新，请基于最新版本重新操作')

    def test_chat_pause_goes_through_confirmation(self):
        task = self._confirmed_task()
        row, gate = self._propose(action='pause', task_id=task.id, version=1)
        self.assertEqual(row.status, 'pending')
        self.assertEqual(self._task().status, 'observing')  # 未确认不生效
        self.assertEqual(self._decide(row.id).status_code, 200)
        self.assertEqual(self._task().status, 'paused')


class EvidenceTests(W08TestCase):
    """窗口物化：暂停缺口、确认截断、水位。"""

    def _confirmed_task(self, plan):
        row, _ = self._propose(plan=plan)
        self.assertEqual(self._decide(row.id).status_code, 200)
        return self._task()

    def test_pause_window_counts_as_gap(self):
        start = self.now.replace(minute=0, second=0, microsecond=0)
        plan = self._plan(startAt=start.isoformat(),
                          deadlineAt=(start + timedelta(hours=4)).isoformat())
        task = self._confirmed_task(plan)
        seed_minutes(self.uid, 'esp32-001', start.astimezone(SHANGHAI), 1)
        # 暂停 [start+2h, start+3h)，其中后 1 小时也有观测但必须计为缺口
        seed_minutes(self.uid, 'esp32-001', (start + timedelta(hours=2)).astimezone(SHANGHAI), 1)
        with db.session_scope() as s:
            t = s.get(AgentTask, task.id)
            t.effective_from = start  # 直接固定窗口边界（确认截断语义由专项测试覆盖）
            t.paused_intervals = json.dumps([
                {'start': (start + timedelta(hours=2)).isoformat(),
                 'end': (start + timedelta(hours=3)).isoformat()}])
            s.flush()
            pet = s.get(Pet, self.pet)
            plan = task_service.plan_of(s, t)
            evidence = task_service.materialize(s, pet, t, plan, start + timedelta(hours=4))
        self.assertEqual(evidence['observedSeconds'], 3600)   # 只算第一个小时
        self.assertEqual(evidence['pausedSeconds'], 3600)
        # 有效观察 3 小时（4h − 1h 暂停），已观测 1 小时 → 缺口 2 小时（第 2、4 小时无数据）
        self.assertEqual(evidence['gapSeconds'], 7200)

    def test_confirmation_after_start_clamps_window(self):
        planned_start = self.now - timedelta(hours=2)
        plan = self._plan(startAt=planned_start.isoformat(),
                          deadlineAt=(self.now + timedelta(hours=2)).isoformat())
        row, _ = self._propose(plan=plan)
        self.assertEqual(self._decide(row.id).status_code, 200)
        task = self._task()
        seed_minutes(self.uid, 'esp32-001', planned_start.astimezone(SHANGHAI), 2)  # 确认前的数据
        with db.session_scope() as s:
            t = s.get(AgentTask, task.id)
            pet = s.get(Pet, self.pet)
            evidence = task_service.materialize(s, pet, t, task_service.plan_of(s, t), self.now)
        self.assertEqual(evidence['observedSeconds'], 0)  # 确认晚于原定开始：先前窗口不算已观察

    def test_condition_daily_scope_uses_today(self):
        plan = self._plan(condition={'metric': 'soundEventCount', 'op': 'at_least', 'value': 3,
                                     'scope': 'daily'}, feature='sounds')
        task = self._confirmed_task(plan)
        with db.session_scope() as s:
            t = s.get(AgentTask, task.id)
            pet = s.get(Pet, self.pet)
            evidence = task_service.materialize(s, pet, t, task_service.plan_of(s, t), self.now)
            state = task_service.condition_state(task_service.plan_of(s, t), evidence, self.now)
        self.assertEqual(state['met'], False)  # 无声音事件：at_least 3 未达成


class CheckpointTests(W08TestCase):
    """W08-A4/A5：三类委托、提醒去重、到期回信与失败降级（固定时钟）。"""

    def _tick_to(self, when, limit=3):
        return jobs.tick(now=when, limit=limit)

    def _confirmed_task(self, plan=None, deadline=None):
        plan = plan or self._plan(deadlineAt=(deadline or self.now + timedelta(days=1)).isoformat())
        row, _ = self._propose(plan=plan)
        self.assertEqual(self._decide(row.id).status_code, 200)
        return self._task()

    def test_threshold_reminder_once_per_day(self):
        task = self._confirmed_task()
        seed_minutes(self.uid, 'esp32-001', self.now.astimezone(SHANGHAI), 3,
                     walk_per_minute=45)  # 确认后 3h × 45s ≈ 2.25h 走动 > 2h 阈值
        decision = {'decision': 'remind', 'text': '它今天累计走动已经超过两小时了。', 'run_id': 1, 'error': None}
        with patch('petsense.agent.checkpoint.evaluate', return_value=decision):
            self._tick_to(self.now + timedelta(hours=3, minutes=5))
        with db.session_scope() as s:
            deliveries = s.execute(select(AgentTaskDelivery)).scalars().all()
            self.assertEqual(len(deliveries), 1)
            self.assertEqual(deliveries[0].kind, 'reminder')
            turn = s.get(AgentTurn, deliveries[0].turn_id)
            self.assertEqual(turn.status, 'complete')
            self.assertEqual(turn.topic_id, self.turn1)  # 深链原会话（提案所在回合）
            blocks = json.loads(turn.blocks)
            self.assertEqual(blocks[0]['type'], 'text')
            self.assertEqual(blocks[1]['type'], 'task_result')
            self.assertEqual(blocks[1]['task']['taskId'], task.id)
        # 同日再检查：条件仍成立但实例去重，不再投递
        with patch('petsense.agent.checkpoint.evaluate', return_value=decision):
            self._tick_to(self.now + timedelta(hours=3, minutes=6))
        with db.session_scope() as s:
            self.assertEqual(len(s.execute(select(AgentTaskDelivery)).scalars().all()), 1)

    def test_reminder_rejected_without_evidence_condition(self):
        """模型候选提醒但证据不支持 → 代码拦截不投递（spec §3 可交付校验）。"""
        self._confirmed_task()
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'remind', 'text': '它走动超过两小时了', 'run_id': 1,
                                 'error': None}):
            self._tick_to(self.now + timedelta(minutes=70))
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(AgentTaskDelivery)).scalars().all(), [])

    def test_due_letter_with_archive(self):
        deadline = self.now + timedelta(hours=2)
        task = self._confirmed_task(deadline=deadline)
        seed_minutes(self.uid, 'esp32-001', self.now.replace(minute=0, second=0, microsecond=0)
                     .astimezone(SHANGHAI), 1)
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'letter', 'text': '观察到截止：今天累计走动 30 分钟，'
                                                              '覆盖 1 小时，其余时间没有观测。',
                                 'run_id': 9, 'error': None}):
            self._tick_to(deadline + timedelta(seconds=1))
        task = self._task()
        self.assertEqual(task.status, 'completed')
        with db.session_scope() as s:
            delivery = s.execute(select(AgentTaskDelivery)).scalar_one()
            self.assertEqual(delivery.kind, 'letter')
            self.assertEqual(delivery.instance_key, 'letter:v1')
            self.assertIsNotNone(delivery.archived_memory_id)  # 正常回信归档（D6）
            memory = s.get(PetMemory, delivery.archived_memory_id)
            self.assertEqual(memory.kind, 'task_result')
            refs = json.loads(memory.source_refs)
            self.assertIn({'type': 'task', 'id': task.id}, refs)
            self.assertIn({'type': 'delivery', 'id': delivery.id}, refs)
            turn = s.get(AgentTurn, delivery.turn_id)
            sources = json.loads(turn.sources)
            self.assertEqual(sources[0]['type'], 'observations')
        # 重复 tick 不再投递（稳定键；必须在会话作用域外调度）
        self._tick_to(deadline + timedelta(minutes=5))
        with db.session_scope() as s:
            self.assertEqual(len(s.execute(select(AgentTaskDelivery)).scalars().all()), 1)
            self.assertEqual([row.id for row in s.execute(
                select(AgentTurn).where(AgentTurn.request_id.like('task-%'))).scalars().all()], [turn.id])

    def test_model_failure_degrades_after_bounded_retry(self):
        deadline = self.now + timedelta(hours=1)
        self._confirmed_task(deadline=deadline)
        # 两次评估失败（首试 + 一次重试）后仅一封降级回信（D6），不归档伪事实
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': None, 'text': '', 'run_id': 1,
                                 'error': 'model_unavailable'}) as evaluate:
            self._tick_to(deadline + timedelta(seconds=1))
            self.assertEqual(evaluate.call_count, 1)
            self.assertEqual(self._task().due_attempts, 1)
            self._tick_to(deadline + timedelta(seconds=61))  # 重试（到期优先，60s 后）
            self.assertEqual(evaluate.call_count, 2)
            self.assertEqual(self._task().due_attempts, 2)
            self._tick_to(deadline + timedelta(seconds=130))
            self.assertEqual(evaluate.call_count, 2)  # 重试耗尽后不再调模型
        task = self._task()
        self.assertEqual(task.status, 'completed')
        with db.session_scope() as s:
            delivery = s.execute(select(AgentTaskDelivery)).scalar_one()
            self.assertEqual(delivery.kind, 'degraded_letter')
            self.assertIsNone(delivery.archived_memory_id)
            payload = json.loads(delivery.payload)
            self.assertTrue(payload['degraded'])
            self.assertIn('评估未能完成', payload['text'])
            memories = s.execute(select(PetMemory).where(PetMemory.kind == 'task_result')).scalars().all()
            self.assertEqual(memories, [])  # 长期记忆无伪事实

    def test_scheduled_review_letter_with_and_without_data(self):
        deadline = self.now + timedelta(hours=1)
        plan = {'kind': 'scheduled_review', 'question': '到时告诉我这一小时的情况',
                'startAt': self.now.isoformat(), 'deadlineAt': deadline.isoformat()}
        self._confirmed_task(plan=plan)
        # 无数据也交付（不等待普通检查节流）
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'letter', 'text': '这一小时没有任何观测数据，'
                                                              '无法告诉你它的活动情况。',
                                 'run_id': 3, 'error': None}) as evaluate:
            self._tick_to(deadline + timedelta(seconds=1))
            self.assertEqual(evaluate.call_count, 1)
        with db.session_scope() as s:
            delivery = s.execute(select(AgentTaskDelivery)).scalar_one()
            self.assertEqual(delivery.kind, 'letter')

    def test_short_delegation_not_throttled(self):
        """短于一小时的委托仍在截止时评估，不等待普通检查节流（spec §2）。"""
        deadline = self.now + timedelta(minutes=20)
        self._confirmed_task(deadline=deadline)
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'letter', 'text': '短窗回信。',
                                 'run_id': 2, 'error': None}) as evaluate:
            self._tick_to(deadline + timedelta(seconds=1))
            self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(self._task().status, 'completed')

    def test_ordinary_check_throttled_hourly_and_merges_watermark(self):
        task = self._confirmed_task()
        seed_minutes(self.uid, 'esp32-001', self.now.replace(minute=0, second=0, microsecond=0)
                     .astimezone(SHANGHAI), 1)
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'continue', 'text': '', 'run_id': 1,
                                 'error': None}) as evaluate:
            self._tick_to(self.now + timedelta(minutes=5))
            self.assertEqual(evaluate.call_count, 1)  # 首查建立水位
            self._tick_to(self.now + timedelta(minutes=20))
            self.assertEqual(evaluate.call_count, 1)  # 1 小时节流
            seed_minutes(self.uid, 'esp32-001',
                         (self.now + timedelta(minutes=60)).astimezone(SHANGHAI), 1)
            self._tick_to(self.now + timedelta(minutes=70))
            self.assertEqual(evaluate.call_count, 2)  # 新数据 + 超过 1 小时 → 合并检查
        task = self._task()
        self.assertEqual(task.status, 'observing')

    def test_wait_baseline_transitions_and_honest_letter(self):
        plan = {'kind': 'comparison', 'question': '它是不是比平时走得少', 'feature': 'actions',
                'comparison': {'band': 'below'}, 'baselineStrategy': 'wait_baseline',
                'startAt': self.now.isoformat(),
                'deadlineAt': (self.now + timedelta(hours=1)).isoformat()}
        self._confirmed_task(plan=plan)
        self.assertEqual(self._task().status, 'waiting_baseline')
        # 基线就绪（伪造维度状态）→ 转入观察
        with patch('petsense.agent.tasks.baseline_ready', return_value=True):
            self._tick_to(self.now + timedelta(minutes=1))
        task = self._task()
        self.assertEqual(task.status, 'observing')
        self.assertIsNotNone(task.observing_since)
        # 截止回信
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'letter', 'text': '基线对比回信。', 'run_id': 4,
                                 'error': None}):
            self._tick_to(self.now + timedelta(hours=1, seconds=1))
        self.assertEqual(self._task().status, 'completed')


class RecoveryTests(W08TestCase):
    """W08-A3：领取后/交付前重启，固定时钟下回信与归档各一条。"""

    def test_restart_between_claim_and_delivery(self):
        deadline = self.now + timedelta(hours=1)
        row, _ = self._propose(plan=self._plan(deadlineAt=deadline.isoformat()))
        self.assertEqual(self._decide(row.id).status_code, 200)
        task = self._task()
        seed_minutes(self.uid, 'esp32-001', self.now.replace(minute=0, second=0, microsecond=0)
                     .astimezone(SHANGHAI), 1)
        decision = {'decision': 'letter', 'text': '重启后的回信：覆盖 1 小时。', 'run_id': 5, 'error': None}
        with patch('petsense.agent.checkpoint.evaluate', return_value=decision):
            # 领取后进程中断：行停在 working（先把截止拉到当下，恢复后按到期评估）
            with db.session_scope() as s:
                t = s.get(AgentTask, task.id)
                t.deadline_at = self.now
                jobs.enqueue(s, task_service.CHECKPOINT_KIND,
                             {'taskId': task.id, 'version': task.current_version},
                             dedup_key=f'task:{task.id}:v{task.current_version}',
                             next_run_at=self.now)
            claimed = jobs.claim_due(now=self.now + timedelta(seconds=1))
            self.assertEqual(len(claimed), 1)
            self.assertEqual(jobs.recover_interrupted(), 1)  # 重启恢复
            jobs.tick(now=self.now + timedelta(seconds=2), limit=2)  # 恢复后执行到交付
        with db.session_scope() as s:
            deliveries = s.execute(select(AgentTaskDelivery)).scalars().all()
            self.assertEqual(len(deliveries), 1)
            turns = s.execute(select(AgentTurn).where(AgentTurn.request_id.like('task-%'))).scalars().all()
            self.assertEqual(len(turns), 1)
            memories = s.execute(select(PetMemory).where(PetMemory.kind == 'task_result')).scalars().all()
            self.assertEqual(len(memories), 1)  # 归档恰好一条
            self.assertEqual(s.get(AgentTask, task.id).status, 'completed')
        # 再跑也不重复
        jobs.tick(now=self.now + timedelta(minutes=3), limit=2)
        with db.session_scope() as s:
            self.assertEqual(len(s.execute(select(AgentTaskDelivery)).scalars().all()), 1)
            self.assertEqual(len(s.execute(select(AgentTurn).where(AgentTurn.request_id.like('task-%'))
                                           ).scalars().all()), 1)

    def test_scheduler_disabled_switch(self):
        with patch.dict('os.environ', {'PETSENSE_SCHEDULER_DISABLED': '1'}):
            self.assertFalse(jobs.scheduler_enabled())
        self.assertTrue(jobs.scheduler_enabled())


class RegistryMatrixTests(W08TestCase):
    """R1 自治工具权限：检查点形态只放行事实读取，其余一律拒绝。"""

    def test_checkpoint_form_allows_fact_reads_only(self):
        from petsense.agent import registry
        names = {spec['name'] for spec in registry.schemas_for(registry.CHECKPOINT, 'allowed')}
        self.assertEqual(names, {'query_observations', 'read_baseline'})
        context = {'pet_id': self.pet, 'user_id': self.uid}
        for name in ('read_current_status', 'write_memory', 'search_memories', 'forget_memory',
                     'prepare_profile_update', 'prepare_role_settings', 'read_web_page',
                     'read_task', 'propose_task_change'):
            gate = registry.execute_tool(name, {}, registry.CHECKPOINT, 'allowed', context)
            self.assertFalse(gate['allowed'], name)
            self.assertEqual(gate['reason'], 'form_not_allowed')
        # 对话工具在对话形态仍可用（W01–W07 回归由全仓测试覆盖）
        self.assertIn('read_current_status',
                      {spec['name'] for spec in registry.schemas_for(registry.DIALOGUE, 'allowed')})

    def test_checkpoint_evaluate_mounts_only_fact_tools(self):
        with patch('petsense.agent.model.respond', return_value=model_turn(
                '{"decision":"continue"}')) as respond:
            outcome = checkpoint_service.evaluate(
                {'mode': 'ordinary', 'task_id': 1, 'pet_id': self.pet, 'user_id': self.uid,
                 'version': 1, 'plan': self._plan(), 'status': 'observing',
                 'evidence': {'daily': [], 'actionSeconds': {}}}, self.now)
        self.assertEqual(outcome['decision'], 'continue')
        self.assertIsNone(outcome['error'])
        tools = respond.call_args.kwargs.get('tools') or []
        self.assertEqual({t['name'] for t in tools}, {'query_observations', 'read_baseline'})

    def test_checkpoint_invalid_output_degrades(self):
        with patch('petsense.agent.model.respond', return_value=model_turn('我觉得还行吧')):
            outcome = checkpoint_service.evaluate(
                {'mode': 'due', 'task_id': 1, 'pet_id': self.pet, 'user_id': self.uid,
                 'version': 1, 'plan': self._plan(), 'status': 'observing',
                 'evidence': {'daily': [], 'actionSeconds': {}}}, self.now)
        self.assertIsNone(outcome['decision'])
        self.assertEqual(outcome['error'], 'model_invalid_output')
        with db.session_scope() as s:
            run = s.execute(select(AgentRun).where(AgentRun.form == 'checkpoint')).scalar_one()
            self.assertEqual(run.status, 'degraded')

    def test_read_task_scoped_to_pet(self):
        from petsense.agent import registry
        row, _ = self._propose()
        self.assertEqual(self._decide(row.id).status_code, 200)
        gate = registry.execute_tool('read_task', {}, registry.DIALOGUE, 'allowed',
                                     {'pet_id': self.pet, 'user_id': self.uid})
        result = gate['result']
        self.assertEqual(len(result['tasks']), 1)
        self.assertEqual(result['tasks'][0]['statusLabel'], '观察中')


class BookAndSnapshotTests(W08TestCase):
    def test_task_book_endpoints(self):
        row, _ = self._propose()
        self.assertEqual(self._decide(row.id).status_code, 200)
        task = self._task()
        listing = self.client.get(f'/api/app/agent/pets/{self.pet}/tasks')
        self.assertEqual(listing.status_code, 200)
        body = listing.get_json()
        self.assertEqual(len(body['tasks']), 1)
        item = body['tasks'][0]
        self.assertEqual(item['kindLabel'], '事实观察')
        self.assertEqual(item['reminders'], 0)
        detail = self.client.get(f'/api/app/agent/pets/{self.pet}/tasks/{task.id}')
        self.assertEqual(detail.status_code, 200)
        self.assertIn('deliveries', detail.get_json()['task'])
        self.assertEqual(self.client.get(f'/api/app/agent/pets/{self.pet}/tasks/9999').status_code, 404)

    def test_card_edit_resubmits_normalized_plan(self):
        row, _ = self._propose()
        response = self.client.post(f'/api/app/agent/pets/{self.pet}/task-proposals',
                                    json={'baseProposalId': row.id,
                                          'plan': {'condition': {'metric': 'walkSeconds',
                                                                 'op': 'at_least', 'value': 5400,
                                                                 'scope': 'window'}}})
        self.assertEqual(response.status_code, 200)
        with db.session_scope() as s:
            fresh = s.execute(select(AgentProposal).where(
                AgentProposal.kind == 'task_change', AgentProposal.status == 'pending')).scalar_one()
            payload = json.loads(fresh.payload)
            self.assertEqual(payload['plan']['condition']['value'], 5400)
            self.assertEqual(s.get(AgentProposal, row.id).status, 'superseded')
        # 编辑出非法计划 → 400，不生成
        bad = self.client.post(f'/api/app/agent/pets/{self.pet}/task-proposals',
                               json={'baseProposalId': fresh.id, 'plan': {'deadlineAt': '昨天'}})
        self.assertEqual(bad.status_code, 400)

    def test_snapshot_includes_active_tasks(self):
        from petsense.agent import snapshot
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            self.assertIsNone(snapshot._tasks_line(s, pet))
        row, _ = self._propose()
        self.assertEqual(self._decide(row.id).status_code, 200)
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            line = snapshot._tasks_line(s, pet)
        self.assertIn('目标', line)
        self.assertIn('截止', line)



class ObservationLabelTests(W08TestCase):
    """真实遥测按英文动作标签入库（walking），Agent 侧走动秒数必须照样算得出。"""

    def _seed_english_minutes(self, minutes=30, walk_per_minute=40):
        # 落在确认之后的观察窗口内（effective_from=确认时刻）
        minute = (self.now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        with db.session_scope() as s:
            for i in range(minutes):
                s.add(TelemetryMinute(owner_id=self.uid, device_id='esp32-001',
                                      minute=minute + timedelta(minutes=i), metrics='{}',
                                      actions=json.dumps({'walking': walk_per_minute}),
                                      observed_seconds=60, frames=60))

    def test_walk_seconds_read_english_stored_labels(self):
        self._seed_english_minutes(minutes=30, walk_per_minute=40)
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            from petsense.agent.observations import query
            result = query(s, pet, self.now - timedelta(hours=2), self.now + timedelta(hours=1))
        self.assertEqual(result['actionSeconds'].get('walking'), 1200)

    def test_task_evidence_counts_walk_seconds_from_english_labels(self):
        self._seed_english_minutes(minutes=30, walk_per_minute=40)
        row, _ = self._propose(plan=self._plan(deadlineAt=(self.now + timedelta(days=1)).isoformat()))
        self.assertEqual(self._decide(row.id).status_code, 200)
        task = self._task()
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            plan = task_service.plan_of(s, task)
            evidence = task_service.materialize(s, pet, task, plan, self.now + timedelta(hours=1))
        self.assertEqual(evidence['walkSeconds'], 1200, '委托证据的走动秒数须来自真实标签')
        self.assertEqual(evidence['observedSeconds'], 30 * 60)
class CheckpointSchedulingRegressionTests(W08TestCase):
    """W08 评审修复的确定性回归：续期重排、在途换版、幂等入队与计划异常保作业。"""

    def _tick_to(self, when, limit=3):
        return jobs.tick(now=when, limit=limit)

    def _job_rows(self):
        with db.session_scope() as s:
            return s.execute(select(AgentJob).order_by(AgentJob.id)).scalars().all()

    def _confirm_with_deadline(self, deadline):
        plan = self._plan(deadlineAt=deadline.isoformat())
        row, _ = self._propose(plan=plan)
        self.assertEqual(self._decide(row.id).status_code, 200)
        return self._task()

    def test_renew_after_completion_rearms_checkpoint_job(self):
        """已到期交付后再续期：必须真的排上新的检查点作业（旧版本作业已是 done）。"""
        deadline = self.now + timedelta(hours=1)
        task = self._confirm_with_deadline(deadline)
        seed_minutes(self.uid, 'esp32-001',
                     self.now.replace(minute=0, second=0, microsecond=0).astimezone(SHANGHAI), 1)
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'letter', 'text': '到期回信。', 'run_id': 1,
                                 'error': None}):
            self._tick_to(deadline + timedelta(seconds=1))
        self.assertEqual(self._task().status, 'completed')
        keys = [row.dedup_key for row in self._job_rows()]
        self.assertEqual(keys, [f'task:{task.id}:v1'], '旧版本作业应为唯一一条且已完成')

        # 续期（新版本）→ 新作业
        row, _ = self._propose(action='renew', task_id=task.id, version=1)
        self.assertEqual(self._decide(row.id).status_code, 200)
        renewed = self._task()
        self.assertEqual(renewed.status, 'observing')
        self.assertEqual(renewed.current_version, 2)
        keys = [row.dedup_key for row in self._job_rows()]
        self.assertIn(f'task:{task.id}:v2', keys, '续期必须重新建立检查点作业')
        v2 = next(row for row in self._job_rows() if row.dedup_key == f'task:{task.id}:v2')
        self.assertEqual(v2.status, 'pending')
        self.assertEqual(json.loads(v2.payload_json), {'taskId': task.id, 'version': 2})

        # 新版本真的会被调度执行（不是只建了行）
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'continue', 'text': '', 'run_id': 2,
                                 'error': None}) as evaluate:
            self._tick_to(self.now + timedelta(minutes=2))
            self.assertEqual(evaluate.call_count, 1)

    def test_inflight_version_change_keeps_new_checkpoint(self):
        """在途换版：旧版本结果作废，新版本作业独立存在并真的执行。"""
        task = self._confirm_with_deadline(self.now + timedelta(days=1))
        seed_minutes(self.uid, 'esp32-001',
                     self.now.replace(minute=0, second=0, microsecond=0).astimezone(SHANGHAI), 1)
        # 模拟在途：v1 作业已被领取（working）
        claimed = jobs.claim_due(now=self.now + timedelta(seconds=1))
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]['payload']['version'], 1)
        # 作业在途期间用户改计划 → v2
        row, _ = self._propose(action='update', task_id=task.id, version=1)
        self.assertEqual(self._decide(row.id).status_code, 200)
        self.assertEqual(self._task().current_version, 2)
        keys = [row.dedup_key for row in self._job_rows()]
        self.assertIn(f'task:{task.id}:v2', keys, '换版必须排上新版本作业')
        # 旧作业作废（重启恢复后按 pending 重领 → 版本已变，结果丢弃）
        jobs.recover_interrupted()
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'letter', 'text': '旧版本回信。', 'run_id': 3,
                                 'error': None}) as evaluate:
            self._tick_to(self.now + timedelta(minutes=2))
        # 只有新版本执行了检查点，旧版本结果不投递
        self.assertEqual(evaluate.call_count, 1)
        with db.session_scope() as s:
            deliveries = s.execute(select(AgentTaskDelivery)).scalars().all()
            self.assertEqual(deliveries, [], '换版前的在途结果不得交付')

    def test_plan_read_failure_keeps_job_pending(self):
        """计划读取异常：作业保留并退避重排，不被静默结束。"""
        task = self._confirm_with_deadline(self.now + timedelta(days=1))
        with patch('petsense.agent.tasks.plan_of', side_effect=task_service.TaskError('计划缺失', status=500)):
            self._tick_to(self.now + timedelta(minutes=2))
        rows = self._job_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, 'pending', '作业应保留待重试，不得静默结束')
        self.assertGreater(rows[0].next_run_at, self.now + timedelta(minutes=2))

    def test_concurrent_enqueue_same_key_is_idempotent(self):
        """并发同键入队：只落一条作业（方言 upsert，不是先查后插）。"""
        with db.session_scope() as s:
            first, created_first = jobs.enqueue(
                s, task_service.CHECKPOINT_KIND, {'taskId': 1, 'version': 1},
                dedup_key='task:1:v1', next_run_at=self.now)
        self.assertTrue(created_first)
        with db.session_scope() as s:
            second, created_second = jobs.enqueue(
                s, task_service.CHECKPOINT_KIND, {'taskId': 1, 'version': 1},
                dedup_key='task:1:v1', next_run_at=self.now)
        self.assertFalse(created_second)
        self.assertEqual(first, second)
        with db.session_scope() as s:
            self.assertEqual(len(s.execute(select(AgentJob)).scalars().all()), 1)

    def test_concurrent_background_delivery_same_key_is_idempotent(self):
        """并发同键后台交付：只落一条回合（稳定 request key 去重）。"""
        from petsense.agent import deliver as deliver_service
        blocks = [{'type': 'text', 'speaker': 'assistant', 'text': '提醒正文。'}]
        with db.session_scope() as s:
            turn_one, created_one = deliver_service.deliver_background_message(
                s, self.pet, 'task-999', blocks, now=self.now)
        with db.session_scope() as s:
            turn_two, created_two = deliver_service.deliver_background_message(
                s, self.pet, 'task-999', blocks, now=self.now)
        self.assertTrue(created_one)
        self.assertFalse(created_two)
        self.assertEqual(turn_one.id, turn_two.id)
        with db.session_scope() as s:
            self.assertEqual(len(s.execute(select(AgentTurn).where(
                AgentTurn.request_id == 'task-999')).scalars().all()), 1)


class PlanValidationRegressionTests(W08TestCase):
    """计划归一化的边界回归：非法时间与非法数值一律拒绝，绝不静默改值。"""

    def test_invalid_explicit_deadline_rejected(self):
        for value in ('昨天', 'not-a-date', '', 12345, None):
            fields = {'kind': 'fact_watch', 'question': '它今天走动吗', 'feature': 'actions',
                      'deadlineAt': value}
            if value in (None, ''):
                # 空值按缺省处理（3 天），不是非法输入
                payload = task_service.normalize_plan(fields, now=self.now)
                self.assertEqual(task_service.iso_z_parse(payload['deadlineAt'])
                                 - task_service.iso_z_parse(payload['startAt']),
                                 timedelta(days=3))
                continue
            with self.assertRaises(task_service.TaskError):
                task_service.normalize_plan(fields, now=self.now)

    def test_past_deadline_rejected(self):
        with self.assertRaises(task_service.TaskError):
            task_service.normalize_plan({'kind': 'fact_watch', 'question': '过期委托',
                                         'feature': 'actions',
                                         'deadlineAt': (self.now - timedelta(hours=1)).isoformat()},
                                        now=self.now)

    def test_non_finite_threshold_rejected(self):
        for value in (float('nan'), float('inf'), -1, True, '5', None):
            with self.assertRaises(task_service.TaskError):
                task_service.normalize_plan({
                    'kind': 'fact_watch', 'question': '阈值委托', 'feature': 'actions',
                    'condition': {'metric': 'walkSeconds', 'op': 'at_least', 'value': value,
                                  'scope': 'window'}}, now=self.now)

    def test_sound_event_count_keeps_integer_precision(self):
        payload = task_service.normalize_plan({
            'kind': 'fact_watch', 'question': '叫太多次就提醒', 'feature': 'sounds',
            'condition': {'metric': 'soundEventCount', 'op': 'at_least', 'value': 3,
                          'scope': 'daily'}}, now=self.now)
        self.assertEqual(payload['condition']['value'], 3)


def plan_of_db(task):
    with db.session_scope() as s:
        return task_service.plan_of(s, s.get(AgentTask, task.id))


if __name__ == '__main__':
    unittest.main()
