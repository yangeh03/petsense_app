"""W08 通用持久作业队列单测：去重、租约、退避重试、重启恢复与测试时钟（供 W09 复用的边界）。"""
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import fresh_db

from petsense import db
from petsense.agent import jobs
from petsense.db import utc_now
from petsense.db.models.agent import AgentJob
from sqlalchemy import select


class AgentJobQueueTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        jobs._HANDLERS.clear()
        self.addCleanup(jobs._HANDLERS.clear)
        self.now = utc_now()

    def _job(self, dedup_key='k1'):
        with db.session_scope() as s:
            return s.execute(select(AgentJob).where(AgentJob.dedup_key == dedup_key)).scalar_one()

    def test_enqueue_dedup_by_stable_key(self):
        with db.session_scope() as s:
            first, created = jobs.enqueue(s, 'demo', {'a': 1}, 'k1', next_run_at=self.now)
            second, created_again = jobs.enqueue(s, 'demo', {'a': 2}, 'k1')
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first, second)
        row = self._job()
        self.assertEqual(row.status, 'pending')
        self.assertEqual(row.attempts, 0)

    def test_tick_executes_due_and_finishes(self):
        seen = []
        jobs.register_handler('demo', lambda job, now: seen.append((job['payload'], now)))
        with db.session_scope() as s:
            jobs.enqueue(s, 'demo', {'a': 1}, 'k1', next_run_at=self.now)
        self.assertEqual(jobs.tick(now=self.now, limit=3), 1)
        self.assertEqual(seen, [({'a': 1}, self.now)])
        self.assertEqual(self._job().status, 'done')
        self.assertEqual(jobs.tick(now=self.now + timedelta(minutes=5)), 0)  # 完成后不再执行

    def test_not_due_job_skipped_until_clock_advances(self):
        seen = []
        jobs.register_handler('demo', lambda job, now: seen.append(job))
        with db.session_scope() as s:
            jobs.enqueue(s, 'demo', {}, 'k1', next_run_at=self.now + timedelta(hours=1))
        self.assertEqual(jobs.tick(now=self.now), 0)
        self.assertEqual(jobs.tick(now=self.now + timedelta(hours=1, seconds=1)), 1)

    def test_handler_reschedule_keeps_job_pending(self):
        def handler(job, now):
            return {'next_run_at': now + timedelta(hours=1)}
        jobs.register_handler('demo', handler)
        with db.session_scope() as s:
            jobs.enqueue(s, 'demo', {}, 'k1', next_run_at=self.now)
        jobs.tick(now=self.now)
        self.assertEqual(self._job().status, 'pending')
        self.assertGreater(self._job().next_run_at, self.now)
        self.assertEqual(jobs.tick(now=self.now + timedelta(minutes=1)), 0)

    def test_failure_backoff_and_dead_after_max_attempts(self):
        def boom(job, now):
            raise RuntimeError('模型超时')
        jobs.register_handler('demo', boom)
        with db.session_scope() as s:
            jobs.enqueue(s, 'demo', {}, 'k1', max_attempts=2, next_run_at=self.now)
        jobs.tick(now=self.now)
        row = self._job()
        self.assertEqual((row.status, row.attempts), ('pending', 1))
        self.assertIn('模型超时', row.last_error)
        # 退避未到不重试；到点重试后耗尽置 dead
        self.assertEqual(jobs.tick(now=self.now + timedelta(seconds=1)), 0)
        jobs.tick(now=row.next_run_at + timedelta(seconds=1))
        self.assertEqual((self._job().status, self._job().attempts), ('dead', 2))

    def test_unknown_kind_fails_without_killing_worker(self):
        with db.session_scope() as s:
            jobs.enqueue(s, 'mystery', {}, 'k1', next_run_at=self.now)
        self.assertEqual(jobs.tick(now=self.now), 1)
        self.assertIn('尚未注册', self._job().last_error)

    def test_recover_interrupted_resets_working(self):
        with db.session_scope() as s:
            jobs.enqueue(s, 'demo', {}, 'k1', next_run_at=self.now)
        # 模拟作业领取后进程中断（无 handler 完成，行停在 working + 租约）
        claimed = jobs.claim_due(now=self.now)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(self._job().status, 'working')
        self.assertEqual(jobs.recover_interrupted(), 1)
        self.assertEqual(self._job().status, 'pending')
        seen = []
        jobs.register_handler('demo', lambda job, now: seen.append(job['id']))
        jobs.tick(now=self.now + timedelta(seconds=1))
        self.assertEqual(len(seen), 1)  # 恢复后可重领执行

    def test_expired_lease_reclaimed_without_recovery(self):
        with db.session_scope() as s:
            jobs.enqueue(s, 'demo', {}, 'k1', next_run_at=self.now)
        claimed = jobs.claim_due(now=self.now)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(jobs.tick(now=self.now + timedelta(minutes=1)), 0)  # 租约内不重复领取
        self.assertEqual(self._job().status, 'working')
        # 租约过期（执行线程死亡）：下一个 tick 直接重领（单进程兜底）
        seen = []
        jobs.register_handler('demo', lambda job, now: seen.append(job['id']))
        self.assertEqual(jobs.tick(now=self.now + timedelta(hours=1)), 1)
        self.assertEqual(len(seen), 1)

    def test_cancel_stops_pending_job(self):
        with db.session_scope() as s:
            jobs.enqueue(s, 'demo', {}, 'k1', next_run_at=self.now)
            self.assertTrue(jobs.cancel(s, 'k1'))
        self.assertEqual(self._job().status, 'done')
        seen = []
        jobs.register_handler('demo', lambda job, now: seen.append(job))
        self.assertEqual(jobs.tick(now=self.now), 0)
        self.assertEqual(seen, [])


if __name__ == '__main__':
    unittest.main()
