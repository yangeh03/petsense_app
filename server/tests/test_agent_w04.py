"""W04 单元测试：PetMemory 存储层、工具门控、快照认识卡、维护 worker（agent-w04-spec.md §6）。"""
import json
import secrets
import unittest
from datetime import timedelta
from unittest.mock import patch

from flask import Flask
from helpers import fresh_db
from test_pets import seed_user_with_pet

from petsense import db
from petsense.agent import loop, memory, memory_jobs, registry, runs, snapshot
from petsense.agent.routes import bp
from petsense.db import iso_z, utc_now
from petsense.db.models import Pet, TelemetryMinute
from petsense.db.models.agent import AgentRun
from petsense.db.models.memory import PetMemory
from sqlalchemy import select

SH = memory.SHANGHAI


def _pet():
    with db.session_scope() as s:
        return s.execute(select(Pet)).scalars().first()


def seed_minute(uid, device_id, minute, actions='{"走动": 40}', observed=60):
    with db.session_scope() as s:
        s.add(TelemetryMinute(owner_id=uid, device_id=device_id, minute=minute,
                              metrics='{}', actions=actions,
                              observed_seconds=observed, frames=observed))


class SaveModelWriteTests(unittest.TestCase):
    """write_memory 落库前校验矩阵（spec §3.3）：白名单/tentative/时间/上限/去重/supersede。"""

    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()

    def _write(self, args, context=None):
        with db.session_scope() as s:
            pet = s.execute(select(Pet)).scalars().first()
            return memory.save_model_write(s, pet.id, args, context if context is not None else {})

    def test_episode_write_ok_with_turn_ref(self):
        entry, error = self._write({'kind': 'episode', 'summary': '今天下午去公园玩了飞盘',
                                    'startAt': '2026-11-22T15:00:00+08:00'},
                                   {'turn_id': 42})
        self.assertIsNone(error)
        self.assertEqual(entry.kind, 'episode')
        self.assertEqual(entry.version, 1)
        self.assertEqual(json.loads(entry.source_refs), [{'type': 'turn', 'id': 42}])
        self.assertIsNotNone(entry.start_at)

    def test_preference_not_writable(self):
        _, error = self._write({'kind': 'preference', 'summary': '它不喜欢鸡肉'})
        self.assertIn('确认写', error)

    def test_daily_not_model_writable(self):
        _, error = self._write({'kind': 'daily', 'summary': 'x'})
        self.assertIn('不在可写类型', error)

    def test_tentative_only_for_habit(self):
        _, error = self._write({'kind': 'episode', 'summary': '可能去过公园', 'tentative': True})
        self.assertIn('habit_candidate', error)

    def test_tentative_habit_ok(self):
        entry, error = self._write({'kind': 'habit_candidate', 'summary': '傍晚常在门口踱步',
                                    'tentative': True})
        self.assertIsNone(error)
        self.assertEqual(entry.certainty, 'tentative')

    def test_empty_or_overlong_summary(self):
        _, error = self._write({'kind': 'episode', 'summary': '   '})
        self.assertIn('summary', error)
        _, error = self._write({'kind': 'episode', 'summary': '长' * 301})
        self.assertIn('300', error)

    def test_bad_time_and_range(self):
        _, error = self._write({'kind': 'episode', 'summary': 'x', 'startAt': 'not-a-time'})
        self.assertIn('startAt', error)
        _, error = self._write({'kind': 'episode', 'summary': 'x',
                                'startAt': '2026-11-22T15:00:00+08:00',
                                'endAt': '2026-11-22T14:00:00+08:00'})
        self.assertIn('结束时间', error)

    def test_other_subject_rejected(self):
        _, error = self._write({'kind': 'episode', 'summary': 'x', 'subject': 'neighbor'})
        self.assertIn('主体', error)

    def test_per_turn_limit_and_dedup(self):
        context = {}
        for i in range(3):
            entry, error = self._write({'kind': 'episode', 'summary': f'经历{i}'}, context)
            self.assertIsNone(error)
        _, error = self._write({'kind': 'episode', 'summary': '第四条'}, context)
        self.assertIn('上限', error)
        _, error = self._write({'kind': 'episode', 'summary': '经历0'}, context)
        self.assertIn('相同内容', error)
        fresh_context = {}
        entry, error = self._write({'kind': 'episode', 'summary': '经历0'}, fresh_context)
        self.assertIsNone(error)  # 新回合不受上一回合上限约束

    def test_supersede_chain(self):
        first, _ = self._write({'kind': 'episode', 'summary': '上周三去的公园'})
        second, error = self._write({'kind': 'episode', 'summary': '其实是上周六去的公园',
                                     'supersedesId': first.id})
        self.assertIsNone(error)
        self.assertEqual(second.version, 2)
        with db.session_scope() as s:
            old = s.get(PetMemory, first.id)
            self.assertEqual(old.status, 'superseded')
            chain = memory.history_of(s, second.id)
            self.assertEqual([row.id for row in chain], [first.id, second.id])
        _, error = self._write({'kind': 'episode', 'summary': '再改一次', 'supersedesId': first.id})
        self.assertIn('不是当前有效版本', error)  # 已被替换的条目不能再被纠正
        _, error = self._write({'kind': 'episode', 'summary': 'x', 'supersedesId': 99999})
        self.assertIn('不存在', error)

    def test_supersede_rejects_cross_kind(self):
        """原生验收缺陷2（2026-11-23）：episode 不得替换 active 的 insight_card——版本链同类型约束。"""
        with db.session_scope() as s:
            pet = s.execute(select(Pet)).scalars().first()
            card, error = memory.save_entry(s, pet.id, kind='insight_card',
                                            summary='它常去公园玩飞盘，喜欢搬家后的新窝',
                                            source_type='worker')
            self.assertIsNone(error)
        _, error = self._write({'kind': 'episode', 'summary': '只记得搬家适应',
                                'supersedesId': card.id})
        self.assertIn('相同类型', error)
        self.assertIn('insight_card', error)
        with db.session_scope() as s:
            row = s.get(PetMemory, card.id)
            self.assertEqual(row.status, 'active')  # 认识卡未被替换，蒸馏 worker 仍持有
        # worker 同类重建路径不受影响（insight_card → insight_card）
        with db.session_scope() as s:
            pet = s.execute(select(Pet)).scalars().first()
            rebuilt, error = memory.save_entry(s, pet.id, kind='insight_card',
                                               summary='只含搬家适应的新认识',
                                               source_type='worker', supersedes_id=card.id)
            self.assertIsNone(error)
            self.assertEqual(rebuilt.version, 2)
        with db.session_scope() as s:
            self.assertEqual(s.get(PetMemory, card.id).status, 'superseded')

    def test_model_write_cannot_target_system_kinds(self):
        """模型写入路径对认识卡等系统条目无入口：白名单拒绝 + 同类型约束兜底。"""
        _, error = self._write({'kind': 'insight_card', 'summary': '手工造认识卡'})
        self.assertIn('不在可写类型', error)
        with db.session_scope() as s:
            pet = s.execute(select(Pet)).scalars().first()
            daily, error = memory.save_entry(s, pet.id, kind='daily', summary='当日汇总',
                                             source_type='worker')
            self.assertIsNone(error)
        _, error = self._write({'kind': 'episode', 'summary': 'x', 'supersedesId': daily.id})
        self.assertIn('相同类型', error)

    def test_forget_idempotent(self):
        entry, _ = self._write({'kind': 'episode', 'summary': '要忘掉的事'})
        with db.session_scope() as s:
            row, error = memory.forget(s, entry.pet_id, entry.id)
            self.assertIsNone(error)
            self.assertEqual(row.status, 'retired')
            row2, error2 = memory.forget(s, entry.pet_id, entry.id)
            self.assertIsNone(error2)  # 幂等
            self.assertEqual(row2.status, 'retired')
        with db.session_scope() as s:
            pet = s.execute(select(Pet)).scalars().first()
            _, error = memory.forget(s, pet.id, 424242)
            self.assertIsNotNone(error)


class SearchTests(unittest.TestCase):
    """检索：FTS5 trigram 中文召回、短词 LIKE 回退、条件组合、空结果语义（spec §3.4）。"""

    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        pet = _pet()
        with db.session_scope() as s:
            memory.save_entry(s, pet.id, kind='episode', summary='上周三半夜狗叫了好久，后来发现是隔壁装修',
                              start_at=memory.parse_time('2026-11-18T00:30:00+08:00'))
            memory.save_entry(s, pet.id, kind='episode', summary='今天下午去公园玩了飞盘，它很开心',
                              start_at=memory.parse_time('2026-11-22T15:00:00+08:00'))
            memory.save_entry(s, pet.id, kind='habit_candidate', summary='傍晚常在门口踱步',
                              certainty='tentative')

    def _search(self, **kwargs):
        with db.session_scope() as s:
            pet = s.execute(select(Pet)).scalars().first()
            return memory.search(s, pet.id, **kwargs)

    def test_fts_chinese_substring(self):
        result = self._search(keyword='半夜狗叫')
        self.assertEqual(result['matched'], 1)
        self.assertIn('狗叫', result['memories'][0]['summary'])

    def test_short_keyword_like_fallback(self):
        result = self._search(keyword='飞盘')
        self.assertEqual(result['matched'], 1)
        self.assertIn('公园', result['memories'][0]['summary'])

    def test_kind_filter(self):
        result = self._search(kinds=['habit_candidate'])
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['memories'][0]['kind'], 'habit_candidate')

    def test_time_window_filter(self):
        result = self._search(from_dt=memory.parse_time('2026-11-22T00:00:00+08:00'))
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['memories'][0]['summary'], '今天下午去公园玩了飞盘，它很开心')

    def test_no_match_semantics(self):
        result = self._search(keyword='完全不存在的关键词xyz')
        self.assertEqual(result['matched'], 0)
        self.assertIn('无匹配不等于没有记忆', result['note'])
        self.assertEqual(result['totalActive'], 3)

    def test_excludes_non_active(self):
        pet = _pet()
        with db.session_scope() as s:
            rows = s.execute(select(PetMemory)).scalars().all()
            memory.forget(s, pet.id, rows[0].id)
        result = self._search()
        self.assertEqual(result['totalActive'], 2)


class RegistryToolTests(unittest.TestCase):
    """三工具注册与权限矩阵：out_of_scope 拒、未知工具拒、成功路径结构化返回。"""

    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        pet = _pet()
        self.context = {'pet_id': pet.id, 'user_id': self.uid, 'turn_id': 7}

    def test_tools_registered(self):
        self.assertIn('write_memory', registry.tool_names())
        self.assertIn('search_memories', registry.tool_names())
        self.assertIn('forget_memory', registry.tool_names())

    def test_out_of_scope_blocks_memory_tools(self):
        gate = registry.execute_tool('write_memory', {'kind': 'episode', 'summary': 'x'},
                                     registry.DIALOGUE, 'out_of_scope', self.context)
        self.assertFalse(gate['allowed'])
        gate = registry.execute_tool('search_memories', {}, registry.DIALOGUE, 'out_of_scope', self.context)
        self.assertFalse(gate['allowed'])

    def test_write_and_search_and_forget_roundtrip(self):
        gate = registry.execute_tool('write_memory',
                                     {'kind': 'episode', 'summary': '今天去公园玩了飞盘'},
                                     registry.DIALOGUE, 'allowed', self.context)
        self.assertTrue(gate['allowed'], gate['result'])
        memory_id = gate['result']['memoryId']
        gate = registry.execute_tool('search_memories', {'keyword': '公园'},
                                     registry.DIALOGUE, 'allowed', self.context)
        self.assertTrue(gate['allowed'])
        self.assertEqual(gate['result']['matched'], 1)
        gate = registry.execute_tool('forget_memory', {'memoryId': memory_id},
                                     registry.DIALOGUE, 'allowed', self.context)
        self.assertTrue(gate['allowed'])
        self.assertTrue(gate['result']['forgotten'])
        gate = registry.execute_tool('search_memories', {'keyword': '公园'},
                                     registry.DIALOGUE, 'allowed', self.context)
        self.assertEqual(gate['result']['matched'], 0)

    def test_write_bad_args_structured_error(self):
        gate = registry.execute_tool('write_memory', {'kind': 'preference', 'summary': 'x'},
                                     registry.DIALOGUE, 'allowed', self.context)
        self.assertTrue(gate['allowed'])  # 门控通过、业务拒绝：结构化 error 而非异常
        self.assertFalse(gate['result']['saved'])
        self.assertIn('确认写', gate['result']['error'])


class SnapshotMemorySectionTests(unittest.TestCase):
    """快照 memory 节与裁剪顺序（spec §3.9：learning → memory → current.secondary）。"""

    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()

    def test_no_card_line(self):
        pet = _pet()
        with db.session_scope() as s:
            snap = snapshot.assemble(s, pet, utc_now())
        self.assertEqual(snap['sections']['memory'], '尚无稳定认识')

    def test_card_line_and_trim_order(self):
        pet = _pet()
        with db.session_scope() as s:
            memory.save_entry(s, pet.id, kind='insight_card', summary='近一个月傍晚常在门口踱步；喜欢去公园玩飞盘。')
        with db.session_scope() as s:
            snap = snapshot.assemble(s, pet, utc_now())
        self.assertIn('门口踱步', snap['sections']['memory'])
        self.assertEqual(snap['meta']['trimmed'], [])
        # 直接对 _trim 断言裁剪顺序：learning → tasks → memory → current.secondary，核心项永不裁（W08 加 tasks 行）
        sections = {'profile': {'name': '长' * 520},
                    'current': {'action': '走动', 'online': False, 'ageSeconds': 5,
                                'batteryPercent': 88, 'receivedAt': '2026-11-22T00:00:00Z'},
                    'learning': '学' * 100, 'memory': '认' * 80,
                    'tasks': '目' * 60,
                    'now': {'shanghai': '2026-11-22T20:00:00', 'utc': '2026-11-22T12:00:00Z'}}
        trimmed = snapshot._trim(sections)
        self.assertEqual(trimmed, ['learning', 'tasks', 'memory', 'current.secondary'])
        self.assertNotIn('learning', sections)
        self.assertNotIn('memory', sections)
        self.assertIn('action', sections['current'])
        self.assertIn('online', sections['current'])
        self.assertIn('ageSeconds', sections['current'])
        self.assertNotIn('batteryPercent', sections['current'])

    def test_card_bounded_150(self):
        pet = _pet()
        with db.session_scope() as s:
            memory.save_entry(s, pet.id, kind='insight_card', summary='长' * 300)
        with db.session_scope() as s:
            snap = snapshot.assemble(s, pet, utc_now())
        self.assertLessEqual(len(snap['sections']['memory']), memory.INSIGHT_CHAR_LIMIT)


class MemoryJobsTests(unittest.TestCase):
    """维护 worker：日汇总回填与幂等、周汇总、蒸馏门槛与降级、报告落盘（spec §3.6–§3.8）。"""

    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        # 默认 facts-only：模型小结按 D4 降级（单测不依赖凭据）
        self._model_patcher = patch.object(memory_jobs, '_model_text', return_value=None)
        self._model_patcher.start()
        self.addCleanup(self._model_patcher.stop)

    def _yesterday(self):
        local_now = memory.parse_time('2026-11-22T20:00:00+08:00')  # 周日
        day = (local_now.astimezone(SH) - timedelta(days=1)).strftime('%Y-%m-%d')
        start, _ = memory.shanghai_day_bounds(day)
        return local_now, day, start

    def _seed_yesterday_minutes(self):
        now_local, day, start = self._yesterday()
        for i in range(60):
            seed_minute(self.uid, 'esp32-001', start + timedelta(minutes=i))
        return now_local, day

    def test_daily_backfill_and_idempotent(self):
        now_local, day = self._seed_yesterday_minutes()
        pet = _pet()
        outcome = memory_jobs.process_pet(pet.id, now=now_local)
        self.assertTrue(outcome['daily'] and outcome['daily'][0]['created'])
        self.assertFalse(outcome['daily'][0]['modelSummary'])  # facts-only 降级
        with db.session_scope() as s:
            row = s.execute(select(PetMemory).where(PetMemory.kind == 'daily')).scalars().first()
            facts = json.loads(row.facts)
            self.assertEqual(facts['day'], day)
            self.assertEqual(facts['minuteCount'], 60)
            self.assertEqual(facts['observedSeconds'], 3600)
            runs_rows = s.execute(select(AgentRun).where(AgentRun.form == 'summary')).scalars().all()
            self.assertEqual(len(runs_rows), 1)
            self.assertEqual(runs_rows[0].status, 'degraded')  # 模型小结缺失
            # §3.11 双向溯源：条目 source_refs 引 run id，轨迹结论回填 memoryId
            self.assertIn({'type': 'run', 'id': runs_rows[0].id},
                          json.loads(row.source_refs))
            self.assertEqual(json.loads(runs_rows[0].conclusion)['memoryId'], row.id)
        outcome2 = memory_jobs.process_pet(pet.id, now=now_local + timedelta(minutes=31))
        self.assertEqual(outcome2['daily'], [])  # 幂等：已存在即跳过
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(AgentRun).where(AgentRun.form == 'summary')).scalars().all().__len__(), 1)

    def test_no_observation_no_daily(self):
        pet = _pet()
        now_local = memory.parse_time('2026-11-22T20:00:00+08:00')
        outcome = memory_jobs.process_pet(pet.id, now=now_local)
        self.assertEqual(outcome['daily'], [])

    def test_weekly_after_dailies(self):
        # 种上周（11-16 周一 至 11-21 周六）每天的观测，11-23 周一跑 worker：先补日报再出上周周汇总
        now_local = memory.parse_time('2026-11-23T20:00:00+08:00')  # 周一
        pet = _pet()
        for offset in range(6, 0, -1):  # 11-16..11-21
            day = (now_local.astimezone(SH) - timedelta(days=offset)).strftime('%Y-%m-%d')
            start, _ = memory.shanghai_day_bounds(day)
            seed_minute(self.uid, 'esp32-001', start)
        outcome = memory_jobs.process_pet(pet.id, now=now_local)
        created_days = [item['day'] for item in outcome['daily'] if item['created']]
        self.assertEqual(len(created_days), 3)  # 每 tick ≤3 天
        outcome = memory_jobs.process_pet(pet.id, now=now_local)  # 第二 tick 补齐剩余
        self.assertEqual(len(outcome['daily']), 3)
        weekly = outcome['weekly']
        self.assertIsNotNone(weekly)
        self.assertTrue(weekly['created'])
        self.assertEqual(weekly['week'], '2026-11-16')
        with db.session_scope() as s:
            row = s.execute(select(PetMemory).where(PetMemory.kind == 'weekly')).scalars().first()
            self.assertEqual(json.loads(row.facts)['dailyCount'], 6)
            run_row = s.execute(select(AgentRun).where(
                AgentRun.form == 'summary').order_by(AgentRun.id.desc())).scalars().first()
            self.assertEqual(json.loads(row.source_refs)[-1], {'type': 'run', 'id': run_row.id})
        outcome = memory_jobs.process_pet(pet.id, now=now_local)
        self.assertIsNone(outcome['weekly'])  # 周汇总已存在，计划阶段即跳过
        with db.session_scope() as s:
            weekly_count = len(s.execute(select(PetMemory).where(PetMemory.kind == 'weekly')).scalars().all())
        self.assertEqual(weekly_count, 1)

    def test_distill_threshold_and_card_chain(self):
        pet = _pet()
        now = utc_now()
        with db.session_scope() as s:
            for i in range(2):
                memory.save_entry(s, pet.id, kind='habit_candidate',
                                  summary=f'规律{i}：傍晚常在门口踱步', certainty='tentative')
        with patch.object(memory_jobs, '_model_text', return_value='近一个月傍晚常在门口踱步，喜欢互动。'):
            outcome = memory_jobs.process_pet(pet.id, now=now)
        self.assertTrue(outcome['distill']['distilled'])
        with db.session_scope() as s:
            card = memory.active_insight(s, pet.id)
            self.assertIsNotNone(card)
            trace_rows = s.execute(select(AgentRun).where(AgentRun.form == 'distill')).scalars().all()
            self.assertEqual(len(trace_rows), 1)
            conclusion = json.loads(trace_rows[0].conclusion)
            self.assertEqual(conclusion['decision'], 'promoted')
            self.assertEqual(conclusion['memoryId'], card.id)  # 条目落库后回填结论
            self.assertIn({'type': 'run', 'id': trace_rows[0].id}, json.loads(card.source_refs))
            self.assertIn('门槛满足', conclusion['reason'])
            snap_line = memory.insight_line(s, pet.id)
            self.assertIn('门口踱步', snap_line)
        # 24h 内无新记忆 → 不重复蒸馏；有新记忆 → supersede 换新卡
        outcome = memory_jobs.process_pet(pet.id, now=now + timedelta(hours=2))
        self.assertFalse(outcome['distill']['distilled'])
        with db.session_scope() as s:
            memory.save_entry(s, pet.id, kind='episode', summary='主人今天带它去了宠物医院')
        with patch.object(memory_jobs, '_model_text', return_value='新认识：近期去过医院。'):
            outcome = memory_jobs.process_pet(pet.id, now=now + timedelta(hours=25))
        self.assertTrue(outcome['distill']['distilled'])
        with db.session_scope() as s:
            cards = s.execute(select(PetMemory).where(PetMemory.kind == 'insight_card')).scalars().all()
            active = [row for row in cards if row.status == 'active']
            superseded = [row for row in cards if row.status == 'superseded']
            self.assertEqual(len(active), 1)
            self.assertEqual(len(superseded), 1)
            self.assertEqual(active[0].supersedes_id, superseded[0].id)

    def test_distill_not_eligible_and_model_fail(self):
        pet = _pet()
        outcome = memory_jobs.process_pet(pet.id, now=utc_now())
        self.assertEqual(outcome['distill']['reason'], 'not_eligible')
        with db.session_scope() as s:
            memory.save_entry(s, pet.id, kind='episode', summary='经历A')
            memory.save_entry(s, pet.id, kind='episode', summary='经历B')
            memory.save_entry(s, pet.id, kind='episode', summary='经历C')
        with patch.object(memory_jobs, '_model_text', return_value=None):
            outcome = memory_jobs.process_pet(pet.id, now=utc_now())
        self.assertEqual(outcome['distill']['reason'], 'model_unavailable')
        with db.session_scope() as s:  # 模型失败不落卡，下次重试
            self.assertIsNone(memory.active_insight(s, pet.id))

    def test_report_written_and_consistent(self):
        import tempfile
        pet = _pet()
        with db.session_scope() as s:
            memory.save_entry(s, pet.id, kind='episode', summary='今天去公园玩了飞盘')
        with db.session_scope() as s:
            path = memory.write_report(s, pet, root=tempfile.mkdtemp())
        content = path.read_text(encoding='utf-8')
        self.assertIn('Max', content)
        self.assertIn('公园', content)
        self.assertIn('数据库为唯一真相', content)


class LeakControlTests(unittest.TestCase):
    """忘掉/纠正后的泄露控制：认识卡强制重建且不回喂旧文、候选全失效退役、快照与导出即时反映。"""

    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        pet = _pet()
        self.pet_id = pet.id
        self._model_prompts = []

        def _record(system_prompt, user_prompt, max_tokens=500):
            self._model_prompts.append(user_prompt)
            return '稳定认识：近期规律可见。'

        self._patcher = patch.object(memory_jobs, '_model_text', side_effect=_record)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _seed_two_habits(self):
        with db.session_scope() as s:
            memory.save_entry(s, self.pet_id, kind='habit_candidate', summary='傍晚常在门口踱步等主人回家')
            memory.save_entry(s, self.pet_id, kind='habit_candidate', summary='听到钥匙声会跑到玄关')

    def _habits(self):
        with db.session_scope() as s:
            return [(r.id, r.summary) for r in
                    memory.active_of_kinds(s, self.pet_id, ('habit_candidate',))]

    def _forget(self, memory_id):
        with db.session_scope() as s:
            row, error = memory.forget(s, self.pet_id, memory_id)
            self.assertIsNone(error)

    def _card(self):
        with db.session_scope() as s:
            card = memory.active_insight(s, self.pet_id)
            return card

    def test_forget_cited_candidate_rebuilds_without_old_text(self):
        now = utc_now()
        self._seed_two_habits()
        outcome = memory_jobs.process_pet(self.pet_id, now=now)
        self.assertTrue(outcome['distill']['distilled'])
        old_card = self._card()
        (forgotten_id, forgotten_summary), (kept_id, _) = self._habits()
        self._forget(forgotten_id)
        with db.session_scope() as s:
            card = memory.active_insight(s, self.pet_id)
            self.assertTrue(memory.stale_citations(s, card))
            self.assertEqual(memory.insight_line(s, self.pet_id),
                             '认识卡待更新（引用的部分记忆已被遗忘或纠正）')
            report = memory.render_report(s, s.get(Pet, self.pet_id))
        self.assertIn('（待更新：认识卡引用的部分记忆已被遗忘或纠正）', report)
        self._model_prompts.clear()
        outcome = memory_jobs.process_pet(self.pet_id, now=now + timedelta(minutes=10))
        self.assertTrue(outcome['distill']['distilled'])
        self.assertEqual(len(self._model_prompts), 1)
        self.assertNotIn(forgotten_summary, self._model_prompts[0])  # 被遗忘候选与旧卡文字都不回喂
        self.assertIn('已被遗忘或纠正', self._model_prompts[0])
        new_card = self._card()
        self.assertIsNotNone(new_card)
        self.assertNotEqual(new_card.id, old_card.id)
        with db.session_scope() as s:
            card = memory.active_insight(s, self.pet_id)
            cited = {ref['id'] for ref in json.loads(card.source_refs) if ref.get('type') == 'memory'}
            self.assertNotIn(forgotten_id, cited)
            self.assertIn(kept_id, cited)
            self.assertEqual(memory.insight_line(s, self.pet_id), '稳定认识：近期规律可见。')

    def test_forget_all_cited_candidates_retires_card(self):
        now = utc_now()
        self._seed_two_habits()
        memory_jobs.process_pet(self.pet_id, now=now)
        old_card = self._card()
        for memory_id, _ in self._habits():
            self._forget(memory_id)
        self._model_prompts.clear()
        outcome = memory_jobs.process_pet(self.pet_id, now=now + timedelta(minutes=10))
        self.assertEqual(outcome['distill']['reason'], 'stale_card_retired')
        self.assertEqual(self._model_prompts, [])  # 确定性退役，不需要模型
        self.assertIsNone(self._card())
        with db.session_scope() as s:
            self.assertEqual(s.get(PetMemory, old_card.id).status, 'retired')
            self.assertEqual(memory.insight_line(s, self.pet_id), '尚无稳定认识')
            runs_rows = s.execute(select(AgentRun).where(AgentRun.form == 'distill')).scalars().all()
            self.assertEqual(json.loads(runs_rows[-1].conclusion)['decision'], 'retired_stale')

    def test_correction_supersede_marks_card_stale_and_rebuilds(self):
        now = utc_now()
        with db.session_scope() as s:
            for i in range(3):
                memory.save_entry(s, self.pet_id, kind='episode', summary=f'第{i}次去公园玩飞盘')
        memory_jobs.process_pet(self.pet_id, now=now)
        old_card = self._card()
        self.assertIsNotNone(old_card)
        with db.session_scope() as s:
            oldest = min(memory.active_of_kinds(s, self.pet_id, ('episode',)), key=lambda r: r.id)
            superseded_summary = oldest.summary
            new, error = memory.save_entry(s, self.pet_id, kind='episode',
                                           summary='其实是上周日去的公园', supersedes_id=oldest.id)
            self.assertIsNone(error)
            corrected_id = new.id
        self._model_prompts.clear()
        # 纠正发生在蒸馏后 1 小时内：仍强制重建（不受 24h 节奏限制）
        outcome = memory_jobs.process_pet(self.pet_id, now=now + timedelta(hours=1))
        self.assertTrue(outcome['distill']['distilled'])
        self.assertNotIn(superseded_summary, self._model_prompts[0])
        new_card = self._card()
        self.assertNotEqual(new_card.id, old_card.id)
        with db.session_scope() as s:
            refs = [ref for ref in json.loads(memory.active_insight(s, self.pet_id).source_refs)
                    if ref.get('type') == 'memory']
            self.assertIn(corrected_id, {ref['id'] for ref in refs})  # 新卡引用纠正后的有效版本

    def test_forget_tool_refreshes_report_immediately(self):
        pet = _pet()
        context = {'pet_id': self.pet_id, 'user_id': self.uid, 'turn_id': 1}
        with db.session_scope() as s:
            entry, _ = memory.save_entry(s, self.pet_id, kind='episode', summary='上周三半夜狗叫了好久')
        with db.session_scope() as s:
            path = memory.write_report(s, pet)  # fresh_db 已把 RUNTIME_ROOT 指向临时目录
        self.assertIn('上周三半夜狗叫了好久', path.read_text(encoding='utf-8'))
        gate = registry.execute_tool('forget_memory', {'memoryId': entry.id},
                                     registry.DIALOGUE, 'allowed', context)
        self.assertTrue(gate['allowed'], gate)
        self.assertTrue(gate['result']['forgotten'])
        content = path.read_text(encoding='utf-8')
        self.assertNotIn('- [episode] 上周三半夜狗叫了好久', content)  # 不再出现在有效经历里
        self.assertIn('[episode/retired] 上周三半夜狗叫了好久', content)  # 历史版本保留可审计

    def test_forget_cited_memory_hides_daily_text_from_search_and_report(self):
        """日汇总文字编入当日记忆原文（2026-11-22 集成核查）：引用失效后不得从当前记忆复述。

        检索不再返回、报告展示层占位；条目不物理删除、文字不回溯改写，历史章节保持可审计。
        """
        now_local = memory.parse_time('2026-11-22T20:00:00+08:00')
        day = (now_local.astimezone(SH) - timedelta(days=1)).strftime('%Y-%m-%d')
        start, _ = memory.shanghai_day_bounds(day)
        for i in range(60):
            seed_minute(self.uid, 'esp32-001', start + timedelta(minutes=i))
        fact = '昨天下午去了海边看日出'
        with db.session_scope() as s:
            memory.save_entry(s, self.pet_id, kind='episode', summary=fact,
                              start_at=start + timedelta(hours=6),
                              generated_at=start + timedelta(hours=6))  # 当天讲述进当天日报
        with patch.object(memory_jobs, '_model_text',
                          return_value=f'当日小结：{fact}。观测覆盖充足。'):
            outcome = memory_jobs.process_pet(self.pet_id, now=now_local)
        self.assertTrue(outcome['daily'] and outcome['daily'][0]['created'])
        with db.session_scope() as s:
            daily = s.execute(select(PetMemory).where(PetMemory.kind == 'daily')).scalars().first()
            self.assertIn(fact, daily.summary)
            self.assertEqual(memory.search(s, self.pet_id, keyword='海边')['matched'], 2)  # 遗忘前：经历与日报都可检索
        with db.session_scope() as s:
            ep = s.execute(select(PetMemory).where(PetMemory.kind == 'episode')).scalars().first()
            _, error = memory.forget(s, self.pet_id, ep.id)
            self.assertIsNone(error)
        with db.session_scope() as s:
            daily = s.execute(select(PetMemory).where(PetMemory.kind == 'daily')).scalars().first()
            self.assertEqual(daily.status, 'active')  # 不物理删除
            self.assertIn(fact, daily.summary)  # 文字不回溯改写（DB 历史可审计）
            self.assertEqual(memory.search(s, self.pet_id, keyword='海边')['matched'], 0)  # 检索不复述
            report = memory.render_report(s, s.get(Pet, self.pet_id))
            self.assertIn(f'- daily {day}：（待更新：引用的部分记忆已被遗忘或纠正）', report)
            self.assertIn('## 历史版本', report)
            self.assertIn('[episode/retired]', report)

    def test_stale_insight_card_excluded_from_search(self):
        """认识卡引用失效后同样退出检索结果（此前仅快照/报告暂停展示）。"""
        self._seed_two_habits()
        outcome = memory_jobs.process_pet(self.pet_id, now=utc_now())
        self.assertTrue(outcome['distill']['distilled'])
        with db.session_scope() as s:
            self.assertEqual(memory.search(s, self.pet_id, keyword='稳定认识')['matched'], 1)
        (forgotten_id, _), _ = self._habits()
        self._forget(forgotten_id)
        with db.session_scope() as s:
            card = memory.active_insight(s, self.pet_id)
            self.assertTrue(memory.stale_citations(s, card))
            self.assertEqual(memory.search(s, self.pet_id, keyword='稳定认识')['matched'], 0)


class FalseWriteReceiptGuardTests(unittest.TestCase):
    """虚假写入回执兜底（原生验收缺陷1，2026-11-23）：最终回复声称「记下了」但本运行
    无任何 write_memory 保存成功时，确定性注入一次性系统提醒；覆盖无执行（被拒/漏重发）
    与结构化未写入（执行但 saved=false）两种形态；无写入调用的合规回显不拦截。"""

    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet_id = s.execute(select(Pet)).scalar_one().id
        self.snap = {'sections': {}, 'meta': {'chars': 10, 'trimmed': [], 'degraded': []}}

    def _run(self, turns, patch_observations=True):
        context = {'pet_id': self.pet_id, 'user_id': self.uid, 'text': '上周末我们搬家了',
                   'target': 'auto', 'history': [], 'snapshot': self.snap,
                   'now_iso': iso_z(utc_now())}
        with patch('petsense.agent.model.respond', side_effect=turns) as respond:
            if patch_observations:
                with patch('petsense.agent.registry.observations_query') as tool:
                    tool.return_value = {'observedSeconds': 0, 'source': {'type': 'observations'}}
                    outcome = loop.run_dialogue(dict(context))
            else:
                outcome = loop.run_dialogue(dict(context))
        return outcome, respond

    def _memory_count(self):
        with db.session_scope() as s:
            return len(s.execute(select(PetMemory)).scalars().all())

    def test_rejected_write_then_false_receipt_gets_one_time_reminder(self):
        """缺陷路径回放：write_memory 被 stance_missing 拒后漏重发，回复却称「记下了」。"""
        write_call = {'name': 'write_memory',
                      'arguments': json.dumps({'kind': 'episode', 'summary': '上周末搬家了'},
                                              ensure_ascii=False),
                      'call_id': 'c1'}
        read_call = {'name': 'query_observations',
                     'arguments': json.dumps({'start': '2026-11-01T00:00:00+08:00',
                                              'end': '2026-11-02T00:00:00+08:00'}),
                     'call_id': 'c2'}
        turns = [
            {'text': '', 'function_calls': [write_call], 'usage': {}},  # 无 stance → 被拒（无执行）
            {'text': '{"scope":"allowed","intent":"write","speaker":"assistant"}\n',
             'function_calls': [read_call], 'usage': {}},               # 只重发读工具，漏写
            {'text': '【照料助手】我把这件事记下了。', 'function_calls': [], 'usage': {}},  # 虚假回执
            {'text': '【照料助手】刚才那条还没有保存下来，要我现在记下来吗？',
             'function_calls': [], 'usage': {}},                        # 提醒后如实说明
        ]
        outcome, respond = self._run(turns)
        self.assertEqual(respond.call_count, 4, '虚假回执后应补跑一轮而不是直接结束')
        self.assertIn({'write_receipt_reminder': True}, outcome['trace']['messages'])
        self.assertEqual(outcome['reply'], '【照料助手】刚才那条还没有保存下来，要我现在记下来吗？')
        self.assertEqual(self._memory_count(), 0, '自始至终不应有记忆落库')

    def test_executed_but_not_saved_still_flagged(self):
        """结构化未写入：write_memory 执行了但 result.saved=false（supersedesId 不存在）。"""
        write_call = {'name': 'write_memory',
                      'arguments': json.dumps({'kind': 'episode', 'summary': '上周末搬家了',
                                               'supersedesId': 99999}, ensure_ascii=False),
                      'call_id': 'c1'}
        turns = [
            {'text': '{"scope":"allowed","intent":"write","speaker":"assistant"}\n',
             'function_calls': [write_call], 'usage': {}},  # 执行了但 saved=false
            {'text': '【照料助手】这条我已经记下了。', 'function_calls': [], 'usage': {}},
            {'text': '【照料助手】抱歉，刚才那条没有保存成功。', 'function_calls': [], 'usage': {}},
        ]
        outcome, respond = self._run(turns, patch_observations=False)
        self.assertEqual(respond.call_count, 3)
        self.assertIn({'write_receipt_reminder': True}, outcome['trace']['messages'])
        self.assertEqual(self._memory_count(), 0)

    def test_claim_without_write_call_is_audited_not_reminded(self):
        """本运行从未发起写入调用的「已记下」不拦截（早前回合写入的合规回显），只落审计标记。"""
        turns = [{'text': '{"scope":"allowed","intent":"social","speaker":"assistant"}\n'
                          '【照料助手】嗯，之前已经记下来了。',
                  'function_calls': [], 'usage': {}}]
        outcome, respond = self._run(turns)
        self.assertEqual(respond.call_count, 1, '合规回显不得触发额外轮次')
        self.assertNotIn({'write_receipt_reminder': True}, outcome['trace']['messages'])
        self.assertIn({'write_receipt_audit': 'claim_without_saved_write'},
                      outcome['trace']['messages'])
        self.assertEqual(outcome['reply'], '【照料助手】嗯，之前已经记下来了。')

    def test_real_write_claim_passes_untouched(self):
        """真正写入成功后的回显直通：不提醒、不降级。"""
        write_call = {'name': 'write_memory',
                      'arguments': json.dumps({'kind': 'episode', 'summary': '上周末搬家了'},
                                              ensure_ascii=False),
                      'call_id': 'c1'}
        turns = [
            {'text': '{"scope":"allowed","intent":"write","speaker":"assistant"}\n',
             'function_calls': [write_call], 'usage': {}},
            {'text': '【照料助手】我把这件事记下了。', 'function_calls': [], 'usage': {}},
        ]
        outcome, respond = self._run(turns, patch_observations=False)
        self.assertEqual(respond.call_count, 2)
        self.assertEqual(outcome['reply'], '【照料助手】我把这件事记下了。')
        self.assertEqual(self._memory_count(), 1)
        self.assertNotIn({'write_receipt_reminder': True}, outcome['trace']['messages'])
        self.assertNotIn({'write_receipt_audit': 'claim_without_saved_write'},
                         outcome['trace']['messages'])

    def test_negation_is_not_a_claim(self):
        """否定句（「还没保存」）不算声称，不触发兜底提醒。"""
        turns = [{'text': '{"scope":"allowed","intent":"write","speaker":"assistant"}\n'
                          '【照料助手】这条我还没有帮你记下来。',
                  'function_calls': [], 'usage': {}}]
        outcome, respond = self._run(turns)
        self.assertEqual(respond.call_count, 1)
        self.assertNotIn({'write_receipt_reminder': True}, outcome['trace']['messages'])
        self.assertNotIn({'write_receipt_audit': 'claim_without_saved_write'},
                         outcome['trace']['messages'])


class DialogueIntegrationTests(unittest.TestCase):
    """POST 链路：context.turn_id 溯源、检索结果不进 sources、快照含 memory 节。"""

    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def test_prompt_contains_memory_rules(self):
        from petsense.agent.loop import SYSTEM_PROMPT
        self.assertIn('search_memories', SYSTEM_PROMPT)
        self.assertIn('宁少勿错', SYSTEM_PROMPT)
        self.assertIn('长期记忆读写', SYSTEM_PROMPT)
        self.assertNotIn('目前只支持聊天与只读观测查询', SYSTEM_PROMPT)

    def test_write_via_loop_records_turn_ref(self):
        """经 registry 全链路写一条记忆并核验来源引用落 turn_id。"""
        pet = _pet()
        context = {'pet_id': pet.id, 'user_id': self.uid, 'turn_id': 99}
        gate = registry.execute_tool('write_memory',
                                     {'kind': 'owner_event', 'summary': '主人本周出差三天',
                                      'subject': 'owner'},
                                     registry.DIALOGUE, 'allowed', context)
        self.assertTrue(gate['allowed'])
        with db.session_scope() as s:
            row = s.get(PetMemory, gate['result']['memoryId'])
            self.assertEqual(row.subject, 'owner')
            self.assertEqual(json.loads(row.source_refs), [{'type': 'turn', 'id': 99}])


if __name__ == '__main__':
    unittest.main()
