"""W11 个人变化合流单元测试（agent-w11-spec.md 验收 A1–A4 的自动部分）。

不依赖真实模型：比较引擎是确定性代码（agent/change.py），回顾与委托交付用假模型
驱动，验证服务端对证据引用的校验与降级。真实 MiniMax 行为评测见
agent_behavior.test_agent_behavior 的 E66–E72。
"""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import fresh_db
from test_pets import seed_user_with_pet

from petsense import db
from petsense.agent import baseline, care_jobs, change, jobs
from petsense.agent import proposals as proposal_service
from petsense.agent import tasks as task_service
from petsense.db import utc_now
from petsense.db.models import Pet, TelemetryEvent, TelemetryMinute
from petsense.db.models.agent import (AgentCareTopic, AgentLearningDay, AgentTask,
                                       AgentTaskDelivery, PetBaseline)
from sqlalchemy import select

SH = ZoneInfo('Asia/Shanghai')
SHANGHAI = SH
UTC = timezone.utc


def _at(day_offset=0, hour=9, minute=0, second=0):
    """以上海时间构造测试时钟（相对今天）。"""
    local = datetime.now(SH).replace(hour=hour, minute=minute, second=second,
                                      microsecond=0) + timedelta(days=day_offset)
    return local.astimezone(UTC)


def _day(offset):
    return (datetime.now(SH) + timedelta(days=offset)).date().isoformat()


def _fake_model(script):
    """假模型：按脚本依次返回文本，末项之后重复最后一项。"""
    queue = list(script)

    def respond(_items, tools=None, on_delta=None, max_output_tokens=1800, web_search=False):
        item = queue.pop(0) if queue else script[-1]
        if isinstance(item, Exception):
            raise item
        return {'text': item.get('text', '') if isinstance(item, dict) else str(item),
                'function_calls': [], 'web_search_calls': [], 'citations': [], 'usage': {}}
    return respond


class ChangeTestBase(unittest.TestCase):
    """共用的隔离夹具：独立临时库 + 一只宠物 + 一台设备。"""

    def setUp(self):
        fresh_db(self)
        care_jobs.register()          # 注册 care_scan / care_review / care_event 执行器
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet_id, self.device = s.execute(
                select(Pet.id, Pet.device_id)).first()

    # ── 夹具 ──────────────────────────────────────────────────────────────
    def seed_learning(self, days, feature='actions', version='v1', seconds=21600,
                      slots='[1,2]', last_offset_days=1):
        with db.session_scope() as s:
            for day in days:
                s.add(AgentLearningDay(pet_id=self.pet_id, device_id=self.device,
                                       feature=feature, version=version, day=day,
                                       seconds=seconds,
                                       last_seen_at=utc_now() - timedelta(days=last_offset_days),
                                       slots=slots))

    def seed_minutes(self, day, slot, count=70, walk=30, observed=60, version='v1',
                     features=('actions',)):
        with db.session_scope() as s:
            start = datetime.strptime(day, '%Y-%m-%d').replace(hour=slot * 6, tzinfo=SH)
            for i in range(count):
                s.add(TelemetryMinute(
                    owner_id=self.uid, device_id=self.device, minute=start + timedelta(minutes=i),
                    metrics='{}',
                    actions=json.dumps({'走动': walk} if 'actions' in features else {}),
                    observed_seconds=observed if 'actions' in features else 0,
                    sound_observed_seconds=observed if 'sounds' in features else 0,
                    source_versions=json.dumps([f'{f}:{version}' for f in features])))

    def set_walk(self, day, slot, walk, count=70):
        """改写某日某时段已有分钟行的走动值（不新增行，避免唯一键冲突）。"""
        with db.session_scope() as s:
            start = datetime.strptime(day, '%Y-%m-%d').replace(hour=slot * 6, tzinfo=SH)
            rows = s.execute(select(TelemetryMinute).where(
                TelemetryMinute.owner_id == self.uid, TelemetryMinute.device_id == self.device,
                TelemetryMinute.minute >= start,
                TelemetryMinute.minute < start + timedelta(minutes=count))).scalars().all()
            for row in rows:
                row.actions = json.dumps({'走动': walk})

    def seed_events(self, day, slot, count, version='v1'):
        with db.session_scope() as s:
            start = datetime.strptime(day, '%Y-%m-%d').replace(hour=slot * 6, tzinfo=SH)
            for i in range(count):
                moment = start + timedelta(minutes=i)
                s.add(TelemetryEvent(owner_id=self.uid, device_id=self.device, boot_id='b1',
                                     model_version=version, label='bark', started_at=moment,
                                     last_seen_at=moment + timedelta(seconds=5), frames=1,
                                     confidence_sum=0.9, observed_seconds=5))

    def seed_baseline(self, feature='actions', version='v1', days=7, slot=1,
                      walks=None, end_offset=-2, slots=None):
        """种 n 个有效日（每分钟 walk 秒），返回日期列表。

        默认 7 天、每天 70 分钟：每分钟 10/20/…/70 秒 → 日值 700…4900，
        nearest-rank 分位 p10=700、p50=2800、p90=4900。
        """
        walks = walks or [10 * (i + 1) for i in range(days)]
        dates = [_day(end_offset - days + 1 + i) for i in range(days)]
        self.seed_learning(dates, feature=feature, version=version)
        for day, walk in zip(dates, walks):
            for one_slot in (slots or [slot]):
                self.seed_minutes(day, one_slot, walk=walk, version=version,
                                  features=(feature,))
        return dates

    def seed_sound_baseline(self, version='v1', days=7, slot=1, counts=None, end_offset=-2):
        counts = counts or [1 + i for i in range(days)]
        dates = [_day(end_offset - days + 1 + i) for i in range(days)]
        self.seed_learning(dates, feature='sounds', version=version)
        for day, count in zip(dates, counts):
            self.seed_minutes(day, slot, count=70, observed=60, version=version,
                              features=('sounds',))
            self.seed_events(day, slot, count, version=version)
        return dates

    # ── 工具 ──────────────────────────────────────────────────────────────
    def compare(self, date, feature='actions', now=None, materialize=True):
        now = now or _at(0, 12)
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            if materialize:
                baseline.ensure_baseline(s, pet, now)
            records = change.compare_day(s, pet, date, now=now, features=[feature])
        return {r['slot']: r for r in records}

    def baseline_row(self, feature='actions', version='v1'):
        with db.session_scope() as s:
            row = s.get(PetBaseline, (self.pet_id, feature, version))
            return {'revision': row.revision, 'valid_days': row.valid_days} if row else None


# ── A1：比较资格与边界 ─────────────────────────────────────────────────────

class EligibilityTests(ChangeTestBase):
    def test_below_p10_is_eligible(self):
        """实际值严格低于 p10 → 合格候选（方向 below）。"""
        self.seed_baseline()
        self.seed_minutes(_day(-1), 1, walk=5)   # 日值 350 < p10 700
        record = self.compare(_day(-1))[1]
        self.assertEqual(record['status'], 'below')
        self.assertTrue(record['eligible'])
        self.assertEqual(record['direction'], 'below')
        self.assertEqual(record['range']['p10'], 700)
        self.assertEqual(record['value'], 350)
        self.assertEqual(record['evidenceId'], f"actions:{_day(-1)}:1")

    def test_above_p90_is_eligible(self):
        """实际值严格高于 p90 → 合格候选（方向 above）。"""
        self.seed_baseline()
        self.seed_minutes(_day(-1), 1, walk=100)  # 日值 7000 > p90 4900
        record = self.compare(_day(-1))[1]
        self.assertEqual(record['status'], 'above')
        self.assertTrue(record['eligible'])
        self.assertEqual(record['direction'], 'above')
        self.assertEqual(record['range']['p90'], 4900)

    def test_boundary_equals_p10_or_p90_is_within(self):
        """边界值等于 p10 或 p90 一律视为范围内（不算变化）。"""
        self.seed_baseline(slots=[1, 2])
        self.seed_minutes(_day(-1), 1, walk=10)   # = p10
        self.seed_minutes(_day(-1), 2, walk=70)   # = p90
        slots = self.compare(_day(-1))
        self.assertEqual(slots[1]['status'], 'within')
        self.assertFalse(slots[1]['eligible'])
        self.assertEqual(slots[2]['status'], 'within')
        self.assertFalse(slots[2]['eligible'])

    def test_empty_window_reports_learning(self):
        """空窗（没有任何有效日）→ 维度学习中，没有个人参照范围。"""
        record = self.compare(_day(-1))[1]
        self.assertEqual(record['status'], 'baseline_learning')
        self.assertFalse(record['comparable'])
        self.assertFalse(record['eligible'])
        self.assertIsNone(record['direction'])
        self.assertIn('认识中', record['note'])

    def test_slot_not_ended(self):
        """观测时段未结束 → 不可比（数值为截至今的累计）。"""
        self.seed_baseline()
        self.seed_minutes(_day(0), 1, walk=100)
        slots = self.compare(_day(0), now=_at(0, 3))[1]
        self.assertEqual(slots['status'], 'slot_not_ended')
        self.assertFalse(slots['comparable'])
        self.assertIn('时段未结束', slots['note'])

    def test_coverage_just_met_and_insufficient(self):
        """覆盖刚满 60 分钟可比较；差一分钟即覆盖不足。"""
        self.seed_baseline()
        self.seed_minutes(_day(-1), 1, count=60, walk=100)   # 3600 秒
        self.seed_minutes(_day(-1), 2, count=59, walk=100)   # 3540 秒
        slots = self.compare(_day(-1))
        self.assertEqual(slots[1]['coverageSeconds'], 3600)
        self.assertEqual(slots[1]['status'], 'above')
        self.assertEqual(slots[2]['coverageSeconds'], 3540)
        self.assertEqual(slots[2]['status'], 'coverage_insufficient')
        self.assertFalse(slots[2]['eligible'])

    def test_actions_ready_but_sounds_learning(self):
        """动作与声音就绪状态相互独立（两个维度独立判断）。"""
        self.seed_baseline(feature='actions')
        self.seed_minutes(_day(-1), 1, walk=10, features=('actions',))
        self.seed_learning([_day(-3), _day(-2), _day(-1)], feature='sounds')
        action = self.compare(_day(-1), feature='actions')[1]
        sound = self.compare(_day(-1), feature='sounds')[1]
        self.assertTrue(action['baselineReady'])
        self.assertFalse(sound['baselineReady'])
        self.assertEqual(action['status'], 'within')
        self.assertEqual(sound['status'], 'baseline_learning')
        self.assertFalse(sound['comparable'])

    def test_slot_sample_insufficient(self):
        """时段样本不足（该时段历史覆盖不足）→ 不可比，区别于「处于范围内」。"""
        days = self.seed_baseline(days=7)
        # 维度仍然就绪（7 个有效日），但 7 天里只有 2 天的时段 2 覆盖满 60 分钟
        for day in days[2:]:
            self.seed_minutes(day, 2, count=30, walk=10)
        self.seed_minutes(days[0], 2, count=70, walk=10)
        self.seed_minutes(days[1], 2, count=70, walk=10)
        self.seed_minutes(_day(-1), 2, walk=100)
        record = self.compare(_day(-1))[2]
        self.assertEqual(record['status'], 'slot_sample_insufficient')
        self.assertFalse(record['eligible'])
        self.assertTrue(record['baselineReady'])

    def test_baseline_version_switch_uses_active_version(self):
        """换版后使用新版本的样本（不得用旧版本补齐新版本）。"""
        # v1：D-15..D-9；v2：D-8..D-2（日期不重叠，各自独立分钟行）
        old_days = [_day(-15 + i) for i in range(7)]
        self.seed_learning(old_days, version='v1', last_offset_days=5)
        for day in old_days:
            self.seed_minutes(day, 1, walk=10, version='v1')
        new_days = [_day(-8 + i) for i in range(7)]
        self.seed_learning(new_days, version='v2', last_offset_days=1)
        for day in new_days:
            self.seed_minutes(day, 1, walk=100, version='v2')
        self.seed_minutes(_day(-1), 1, walk=100, version='v2')
        record = self.compare(_day(-1))[1]
        self.assertEqual(record['version'], 'v2')
        self.assertEqual(record['range']['p90'], 7000)
        self.assertEqual(record['status'], 'within')   # 新版本 p10=p90=7000

    def test_target_date_self_exclusion(self):
        """目标日期自身不入样本（比较只取该日期之前的基线样本）。"""
        self.seed_baseline(walks=[10] * 7)
        self.seed_minutes(_day(-1), 1, walk=100)   # 目标日 7000
        record = self.compare(_day(-1))[1]
        self.assertEqual(record['samples'], 7)
        self.assertEqual(record['range']['p90'], 700)  # 样本只来自之前 7 天
        self.assertEqual(record['status'], 'above')

    def test_sound_dimension_uses_event_count(self):
        """声音维度按事件次数比较，与动作维度相互独立。"""
        self.seed_sound_baseline(counts=[1] * 7)
        self.seed_minutes(_day(-1), 1, count=70, walk=0, observed=60, version='v1',
                          features=('sounds',))
        self.seed_events(_day(-1), 1, 9)
        record = self.compare(_day(-1), feature='sounds')[1]
        self.assertEqual(record['value'], 9)
        self.assertEqual(record['unit'], 'count')
        self.assertEqual(record['status'], 'above')
        self.assertEqual(record['range']['p90'], 1)


# ── 比较断言的识别：否定式说明不算 ──────────────────────────────────────────

class ComparisonClaimTests(unittest.TestCase):
    """原句取自真实模型轮次里被误拒的回信（W08 E48、W11 E72）。"""

    def test_negated_disclaimers_are_not_claims(self):
        for text in (
                '它目前仍在学习期（有效观测 0 天，需要 7 天），还没有个人参照范围，所以无法判断这是否比平时少。',
                '个人基线仍在学习，有效0/7天，无可用基线范围，因此无法判断比平时多或少。',
                '所以没有可参照的走动范围，也不能说比平时多或少。',
                '无法就“上午走动是否高于平时”下结论。',
        ):
            self.assertFalse(change.asserts_comparison(text), text)
            self.assertEqual(change.claim_directions(text), set(), text)

    def test_affirmed_comparisons_are_claims(self):
        for text in ('它昨天下午走动比平时多很多。', '上午走动明显少于平时。',
                     '走动 7000 秒，高于该时段的平时范围。'):
            self.assertTrue(change.asserts_comparison(text), text)

    def test_mixed_sentence_keeps_the_affirmed_direction(self):
        text = '不是比平时多，而是明显少于平时。'
        self.assertTrue(change.asserts_comparison(text))
        self.assertEqual(change.claim_directions(text), {'below'})

    def test_negation_does_not_leak_across_clauses(self):
        # 否定只作用于同一条分句：下一条分句里的比较仍是断言
        self.assertTrue(change.asserts_comparison('无法判断它是否休息好，上午走动明显比平时多。'))

    def test_substring_helper_still_reports_mentions(self):
        self.assertTrue(change.claims_personal_change('无法判断是否比平时多'))


# ── A2：周期回顾合流 ───────────────────────────────────────────────────────

class ReviewChangeTests(ChangeTestBase):
    """日回顾/周回顾的个人变化候选、证据引用校验与降级。"""

    def _seed_review_ready(self, target_walk=100, days=7):
        """种一个可比的昨天：7 个历史有效日 + 昨天超出 p90。"""
        self.seed_baseline(walks=[10] * days)
        self.seed_minutes(_day(-1), 1, walk=target_walk)

    def _scan(self, now):
        """直接驱动一轮调度扫描（排上周期回顾作业）。"""
        job = {'id': 0, 'kind': care_jobs.SCAN_KIND, 'dedup_key': 'care:scan:loop',
               'payload': {}, 'attempts': 0}
        return care_jobs.handle_scan(job, now)

    def _run_review(self, model_output, kind='daily', now=None):
        now = now or _at(0, 9, 30, 5)
        self._scan(_at(0, 8, 0))
        self._scan(_at(0, 9, 30))
        with patch('petsense.agent.model.respond', _fake_model([model_output])):
            jobs.tick(now=now, limit=10)
        with db.session_scope() as s:
            turns = s.execute(select(AgentTaskDelivery)).scalars().all()
            topics = s.execute(select(AgentCareTopic)).scalars().all()
            from petsense.db.models.agent import AgentTurn
            agent_turns = s.execute(select(AgentTurn).order_by(AgentTurn.id)).scalars().all()
        return topics, agent_turns

    def test_daily_review_uses_actual_date_slot(self):
        """日回顾按对应日期与粗时段比较，候选带真实 evidenceId。"""
        self._seed_review_ready()
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            facts = care_jobs.review_facts(s, pet, _at(-1, 0, 0), _at(0, 0, 0))
        change_facts = facts['personalChange']
        self.assertEqual(change_facts['dates'], [_day(-1)])
        eligible = [c for c in change_facts['candidates'] if c['status'] == 'above']
        self.assertEqual(len(eligible), 1)
        self.assertEqual(eligible[0]['evidenceId'], f"actions:{_day(-1)}:1")
        self.assertEqual(eligible[0]['value'], 7000)
        self.assertEqual(eligible[0]['direction'], 'above')

    def test_weekly_review_mixes_comparable_and_incomparable(self):
        """周回顾汇总本周已结束且可比的日内时段，不作整周总量比较。

        目标日期的就绪状态按「该日期当时」判定：_day(-2) 之前只有 6 个有效日，
        后来才凑够的第 7 天不得回头让它变成已就绪（历史口径回归）。
        """
        self.seed_baseline(walks=[10] * 7, slots=[1, 2])
        self.seed_minutes(_day(-1), 1, walk=100)   # 超出（时段 1 的历史样本未被改动）
        self.set_walk(_day(-2), 2, 100)            # 数值很大，但当时基线未就绪
        # _day(-1) 的时段 3 只有 30 分钟 → 覆盖不足（不可比）
        self.seed_minutes(_day(-1), 3, count=30, walk=100)
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            facts = care_jobs.review_facts(s, pet, _at(-7, 0, 0), _at(0, 0, 0))
        candidates = facts['personalChange']['candidates']
        statuses = {(c['feature'], c['date'], c['slot']): c['status'] for c in candidates}
        self.assertEqual(statuses[('actions', _day(-1), 1)], 'above')
        self.assertEqual(statuses[('actions', _day(-1), 3)], 'coverage_insufficient')
        self.assertEqual(statuses[('actions', _day(-2), 2)], 'baseline_learning')
        counts = facts['personalChange']['counts']
        self.assertEqual(counts['eligible'], 1)
        self.assertGreater(counts['notComparable'], 0)

    def test_later_learning_days_do_not_reach_gate_for_past_target(self):
        """回归：后来才凑够的有效日不得让过去的目标日期变成「已就绪」。

        第 7 个有效日落在 _day(-1)，因此 _day(-2) 当时仍未就绪。
        """
        self.seed_baseline(walks=[10] * 7, slots=[1])
        self.seed_minutes(_day(-1), 1, walk=100)
        self.set_walk(_day(-2), 1, 100)
        slots = self.compare(_day(-2))
        self.assertEqual(slots[1]['status'], 'baseline_learning')
        self.assertFalse(slots[1]['eligible'])
        # 目标日期之后的有效日不参与该日期的参照样本
        self.assertEqual(slots[1]['samples'], 0)

    def test_target_day_uses_version_active_at_that_day(self):
        """回归：目标日期的数据按当时活跃的算法版本取，不用别的版本补齐。"""
        old_days = [_day(-15 + i) for i in range(7)]
        self.seed_learning(old_days, version='v1', last_offset_days=5)
        for day in old_days:
            self.seed_minutes(day, 1, walk=10, version='v1')
        new_days = [_day(-8 + i) for i in range(7)]
        self.seed_learning(new_days, version='v2', last_offset_days=1)
        for day in new_days:
            self.seed_minutes(day, 1, walk=100, version='v2')
        self.seed_minutes(_day(-1), 1, walk=100, version='v2')
        record = self.compare(_day(-1))[1]
        self.assertEqual(record['version'], 'v2')
        self.assertEqual(record['range']['p90'], 7000)
        # 旧版本的数据不混进新版本的参照范围
        self.assertEqual(record['samples'], 7)

    def test_review_rejects_fake_evidence(self):
        """伪造证据 ID / 方向 / 数值 → 服务端拒绝该发现（不授权交付）。"""
        self._seed_review_ready()
        output = json.dumps({'say': '它昨天下午走动明显多于平时。', 'level': 'high', 'findings': [
            {'category': 'personal_change', 'anchor': 'change:actions:%s:1' % _day(-1),
             'evidenceId': 'actions:2099-01-01:1', 'title': '异常', 'text': '比平时多'},
            {'category': 'personal_change', 'anchor': 'change:actions:%s:2' % _day(-1),
             'evidenceId': f"actions:{_day(-1)}:1", 'direction': 'below', 'value': 7000,
             'title': '方向反了', 'text': '比平时少'},
            {'category': 'personal_change', 'anchor': 'change:actions:%s:3' % _day(-1),
             'evidenceId': f"actions:{_day(-1)}:1", 'direction': 'above', 'value': 123,
             'title': '数值不符', 'text': '比平时多'},
        ]})
        topics, agent_turns = self._run_review(output)
        change_topics = [t for t in topics if t.category == 'personal_change']
        self.assertEqual(change_topics, [], '无合格证据时不得开个人变化主题')
        replies = [t.reply for t in agent_turns]
        self.assertTrue(any('比平时' not in r for r in replies))
        self.assertTrue(any('观察小结' in r for r in replies), '应回退绝对事实描述')

    def test_review_accepts_valid_evidence_and_freezes_it(self):
        """引用真实证据 → 交付个人变化块并冻结证据字段（D6）。"""
        self._seed_review_ready()
        output = json.dumps({'say': '它昨天下午走动明显多于平时。', 'level': 'high', 'findings': [
            {'category': 'personal_change', 'anchor': f"change:actions:{_day(-1)}:1",
             'evidenceId': f"actions:{_day(-1)}:1", 'direction': 'above', 'value': 7000,
             'title': '走动偏多', 'text': '昨天下午走动比平时多'}]})
        topics, agent_turns = self._run_review(output)
        change_topics = [t for t in topics if t.category == 'personal_change']
        self.assertEqual(len(change_topics), 1)
        evidence = json.loads(change_topics[0].evidence)[0]['change']
        self.assertEqual(evidence['evidenceId'], f"actions:{_day(-1)}:1")
        self.assertEqual(evidence['range']['p90'], 700)
        self.assertEqual(evidence['revision'], self.baseline_row()['revision'])
        change_blocks = []
        for turn in agent_turns:
            for block in json.loads(turn.blocks or '[]'):
                if block.get('type') == 'personal_change':
                    change_blocks.append(block)
        self.assertEqual(len(change_blocks), 1)
        self.assertEqual(change_blocks[0]['change']['value'], 7000)

    def test_review_without_candidates_cannot_claim_comparison(self):
        """没有合格候选时，模型说「比平时」也回退绝对事实（服务端兜底）。"""
        self.seed_baseline(walks=[10] * 7)
        self.seed_minutes(_day(-1), 1, walk=10)   # 在范围内
        output = json.dumps({'say': '它昨天下午走动比平时多很多。', 'level': 'high',
                             'findings': []})
        topics, agent_turns = self._run_review(output)
        self.assertEqual([t for t in topics if t.category == 'personal_change'], [])
        self.assertTrue(any('观察小结' in t.reply for t in agent_turns))

    def test_review_keeps_negated_comparison_disclaimer(self):
        """没有合格候选时，否定式的比较说明（「无法判断是否比平时多」）不被回退。

        原句取自真实模型轮次：模型在基线学习期如实交代不能比较，此前被子串匹配
        当成「无证据的个人比较」而整段替换。
        """
        self.seed_baseline(walks=[10] * 7)
        self.seed_minutes(_day(-1), 1, walk=10)   # 在范围内
        say = '它昨天上午走动约 11 分钟；个人基线还在认识中，无法判断是否比平时多。'
        output = json.dumps({'say': say, 'level': 'high', 'findings': [
            {'category': 'coverage', 'anchor': 'coverage-morning-only', 'text': '昨天只有上午有数据。'}]})
        topics, agent_turns = self._run_review(output)
        self.assertTrue(any(say in t.reply for t in agent_turns), [t.reply for t in agent_turns])
        self.assertFalse(any('观察小结' in t.reply for t in agent_turns))

    def test_checkpoint_letter_with_negated_disclaimer_is_kept(self):
        """到期回信如实说明「无法判断是否比平时少」（无合格证据）不得被拒。

        原句取自真实模型轮次（W08 E48）：基线学习期的阈值委托回信因此稳定落降级信。
        """
        from petsense.agent import checkpoint
        letter = ('这两分钟它走动约 45 秒，没有达到你设定的 60 秒门槛。'
                  '它目前仍在学习期，还没有个人参照范围，所以无法判断这是否比平时少。')
        ctx = {'mode': 'due', 'plan': {'kind': 'fact_watch', 'question': '它这两分钟走动多吗'},
               'evidence': {'changeSlots': [], 'observedSeconds': 120, 'gapSeconds': 0,
                            'pausedSeconds': 0, 'windowStart': None, 'windowEnd': None},
               'pet_id': self.pet_id, 'user_id': self.uid, 'status': 'observing',
               'version': 1, 'task_id': 1}
        script = [json.dumps({'decision': 'letter', 'text': letter}, ensure_ascii=False)]
        with patch('petsense.agent.model.respond', _fake_model(script)):
            outcome = checkpoint.evaluate(ctx, utc_now())
        self.assertEqual(outcome['decision'], 'letter')
        self.assertIsNone(outcome['error'])
        self.assertEqual(outcome['text'], letter)

    def test_checkpoint_letter_affirming_comparison_without_evidence_is_rejected(self):
        from petsense.agent import checkpoint
        ctx = {'mode': 'due', 'plan': {'kind': 'fact_watch', 'question': '它这两分钟走动多吗'},
               'evidence': {'changeSlots': [], 'observedSeconds': 120, 'gapSeconds': 0,
                            'pausedSeconds': 0, 'windowStart': None, 'windowEnd': None},
               'pet_id': self.pet_id, 'user_id': self.uid, 'status': 'observing',
               'version': 1, 'task_id': 1}
        for text in ('这两分钟它走动比平时多。', '不是比平时多，而是明显少于平时。'):
            with self.subTest(text=text):
                script = [json.dumps({'decision': 'letter', 'text': text}, ensure_ascii=False)]
                with patch('petsense.agent.model.respond', _fake_model(script)):
                    outcome = checkpoint.evaluate(ctx, utc_now())
                self.assertIsNone(outcome['decision'])
                self.assertEqual(outcome['error'], 'comparison_without_evidence')

    def test_same_window_rerun_is_deduplicated(self):
        """同一窗口重跑不重复开题（同锚点合并）。"""
        self._seed_review_ready()
        output = json.dumps({'say': '它昨天下午走动明显多于平时。', 'level': 'high', 'findings': [
            {'category': 'personal_change', 'anchor': f"change:actions:{_day(-1)}:1",
             'evidenceId': f"actions:{_day(-1)}:1", 'direction': 'above', 'value': 7000,
             'title': '走动偏多', 'text': '昨天下午走动比平时多'}]})
        topics, _ = self._run_review(output)
        self.assertEqual(len([t for t in topics if t.category == 'personal_change']), 1)

    def test_cross_pet_isolation(self):
        """证据与主题按宠物隔离，另一只宠物拿不到。"""
        self._seed_review_ready()
        with db.session_scope() as s:
            other = Pet(user_id=self.uid, name='另一只', species='dog', device_id='collar-2',
                        created_at=utc_now())
            s.add(other)
            s.flush()
            other_id = other.id
        with db.session_scope() as s:
            topics = s.execute(select(AgentCareTopic).where(
                AgentCareTopic.pet_id == other_id)).scalars().all()
        self.assertEqual(topics, [])


# ── A3：确认选择与观察窗口 ────────────────────────────────────────────────

class ConfirmationTests(ChangeTestBase):
    """D3 两种确认选择、取消、重复提交、切换须重新确认、旧计划回归。"""

    def _comparison_proposal(self, strategy='fact_first', choice_required=False):
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            payload = task_service.normalize_proposal_fields({
                'action': 'create',
                'plan': {'kind': 'comparison', 'question': '它下午走动是不是比平时少',
                         'feature': 'actions', 'comparison': {'band': 'below'},
                         'baselineStrategy': strategy,
                         'deadlineAt': _at(3, 12).isoformat()}})
            if choice_required:
                payload['plan']['baselineStrategyChoiceRequired'] = True
            row, _ = proposal_service.create_task_proposal(s, pet, None, payload)
            return row.id

    def test_choice_required_rejects_decision_without_choice(self):
        """新版候选缺少明确选择不静默生效（400）。"""
        proposal_id = self._comparison_proposal(choice_required=True)
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            with self.assertRaises(proposal_service.ProposalError) as ctx:
                proposal_service.decide(s, pet, proposal_id, 'confirm')
            self.assertIn('基线', ctx.exception.message)
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(AgentTask)).scalars().all(), [])

    def test_two_choices_are_applied(self):
        """两种选择都生效，并写入实际策略。"""
        for strategy in ('fact_first', 'wait_baseline'):
            proposal_id = self._comparison_proposal(choice_required=True)
            with db.session_scope() as s:
                pet = s.get(Pet, self.pet_id)
                proposal_service.decide(s, pet, proposal_id, 'confirm',
                                        baseline_strategy=strategy)
            with db.session_scope() as s:
                task = s.execute(select(AgentTask).order_by(AgentTask.id.desc())).scalars().first()
                plan = task_service.plan_of(s, task)
            self.assertEqual(plan['baselineStrategy'], strategy)
            self.assertFalse(plan.get('baselineStrategyChoiceRequired'))
            self.assertEqual(task_service.iso_z_parse(plan['deadlineAt']), _at(3, 12))

    def test_reject_cancels_without_task(self):
        """取消（拒绝）不创建委托。"""
        proposal_id = self._comparison_proposal(choice_required=True)
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            proposal_service.decide(s, pet, proposal_id, 'reject')
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(AgentTask)).scalars().all(), [])

    def test_duplicate_decision_is_idempotent(self):
        """重复提交同一决策幂等（不重复创建委托）。"""
        proposal_id = self._comparison_proposal(choice_required=True)
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            proposal_service.decide(s, pet, proposal_id, 'confirm',
                                    baseline_strategy='wait_baseline')
            proposal_service.decide(s, pet, proposal_id, 'confirm',
                                    baseline_strategy='wait_baseline')
        with db.session_scope() as s:
            self.assertEqual(len(s.execute(select(AgentTask)).scalars().all()), 1)

    def test_old_proposal_without_choice_flag_keeps_compat(self):
        """旧候选（旧客户端）沿用兼容解析：计划里的既定策略直接生效。"""
        proposal_id = self._comparison_proposal(strategy='fact_first')
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            proposal_service.decide(s, pet, proposal_id, 'confirm')
        with db.session_scope() as s:
            task = s.execute(select(AgentTask)).scalars().first()
            plan = task_service.plan_of(s, task)
        self.assertEqual(plan['baselineStrategy'], 'fact_first')

    def test_switching_strategy_requires_new_confirmation(self):
        """从事实策略切到相对比较须重新确认（生成新版本）。"""
        proposal_id = self._comparison_proposal(choice_required=True)
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            proposal_service.decide(s, pet, proposal_id, 'confirm',
                                    baseline_strategy='fact_first')
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            task = s.execute(select(AgentTask)).scalars().first()
            payload = task_service.normalize_proposal_fields({
                'action': 'update', 'taskId': task.id, 'expectedVersion': task.current_version,
                'plan': {'kind': 'comparison', 'question': '它下午走动是不是比平时少',
                         'feature': 'actions', 'comparison': {'band': 'below'},
                         'baselineStrategy': 'wait_baseline',
                         'deadlineAt': _at(3, 12).isoformat()}})
            row, _ = proposal_service.create_task_proposal(s, pet, None, payload)
            self.assertEqual(row.status, 'pending')
            # 未确认前计划不变
            self.assertEqual(task_service.plan_of(s, task)['baselineStrategy'], 'fact_first')

    def test_wait_baseline_enters_observing_when_ready(self):
        """等待基线：就绪后转入观察中并重设观察起点。"""
        proposal_id = self._comparison_proposal(choice_required=True)
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            proposal_service.decide(s, pet, proposal_id, 'confirm',
                                    baseline_strategy='wait_baseline')
        with db.session_scope() as s:
            task = s.execute(select(AgentTask)).scalars().first()
            self.assertEqual(task.status, 'waiting_baseline')
            self.assertIsNone(task.observing_since)
        # 基线就绪后进入观察
        self.seed_baseline(walks=[10] * 7)
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            task = s.execute(select(AgentTask)).scalars().first()
            plan = task_service.plan_of(s, task)
            task_service._plan_phase(s, task, pet, plan, _at(1, 12))
            self.assertEqual(task.status, 'observing')
            self.assertIsNotNone(task.observing_since)


# ── A3/A4：委托证据窗口与冻结证据 ─────────────────────────────────────────

class DeliveryEvidenceTests(ChangeTestBase):
    """观察窗口排除确认前/等待期/暂停期；交付冻结证据不被后续变更影响。"""

    def _comparison_task(self, strategy='wait_baseline'):
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            payload = task_service.normalize_proposal_fields({
                'action': 'create',
                'plan': {'kind': 'comparison', 'question': '它下午走动是不是比平时少',
                         'feature': 'actions', 'comparison': {'band': 'below'},
                         'baselineStrategy': strategy,
                         'startAt': _at(-1, 0).isoformat(),
                         'deadlineAt': _at(2, 12).isoformat()}})
            row, _ = proposal_service.create_task_proposal(s, pet, None, payload)
            task = task_service.apply_proposal(s, pet, row, baseline_strategy=strategy)
            return task.id

    def test_observation_excludes_pause_and_preconfirm(self):
        """观察证据排除确认前与暂停期（只算有效观察窗口）。"""
        self.seed_baseline(walks=[10] * 7)
        task_id = self._comparison_task()
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            task = s.get(AgentTask, task_id)
            plan = task_service.plan_of(s, task)
            task.paused_intervals = json.dumps(
                [{'start': _at(-1, 6).isoformat(), 'end': _at(-1, 12).isoformat()}])
            task.effective_from = _at(-1, 12)
            windows = [( _at(-1, 12), _at(0, 12))]
            slots = task_service.change_slots(s, pet, plan, windows, _at(0, 12))
        covered = [s_ for s_ in slots if s_['coverageSeconds'] > 0]
        self.assertTrue(all(s_['coverageSeconds'] < 3600 for s_ in covered),
                        '暂停期与确认前的数据不得计入')

    def test_fact_first_has_no_personal_comparison(self):
        """事实策略只给绝对事实，不把原比较目标当作达成。"""
        self.seed_baseline(walks=[10] * 7)
        task_id = self._comparison_task(strategy='fact_first')
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            task = s.get(AgentTask, task_id)
            plan = task_service.plan_of(s, task)
            task.effective_from = _at(-1, 0)
            evidence = task_service.materialize(s, pet, task, plan, _at(0, 12))
            condition = task_service.condition_state(plan, evidence, _at(0, 12))
        self.assertFalse(condition['met'])
        self.assertEqual(condition['reason'], 'fact_first_strategy')
        self.assertEqual(evidence.get('changeSlots'), [])

    def test_delivery_freezes_evidence(self):
        """交付冻结证据：样本窗口、版本与修订号、分位数、实际值与方向。"""
        self.seed_baseline(walks=[10] * 7)
        self.seed_minutes(_day(-1), 1, walk=100)   # 超出 p90
        task_id = self._comparison_task()
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            task = s.get(AgentTask, task_id)
            plan = task_service.plan_of(s, task)
            task.effective_from = _at(-1, 0)
            evidence = task_service.materialize(s, pet, task, plan, _at(0, 12))
            delivery = task_service._deliver(
                s, pet, task, plan, kind='letter', text='它下午走动比平时多。',
                evidence=evidence, run_id=None, now=_at(0, 12), reason=None)
            frozen = json.loads(delivery.payload)['changeEvidence']
        self.assertEqual(len(frozen), 1)
        item = frozen[0]
        self.assertEqual(item['direction'], 'above')
        self.assertEqual(item['range']['p90'], 700)
        self.assertEqual(item['sampleStartDay'], _day(-8))
        self.assertEqual(item['sampleEndDay'], _day(-2))
        self.assertIsNotNone(item['revision'])
        self.assertEqual(item['value'], 7000)

    def test_frozen_evidence_survives_baseline_revision_change(self):
        """交付后基线重算（修订号变化）不改写历史证据。"""
        self.seed_baseline(walks=[10] * 7)
        self.seed_minutes(_day(-1), 1, walk=100)
        task_id = self._comparison_task()
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            task = s.get(AgentTask, task_id)
            plan = task_service.plan_of(s, task)
            task.effective_from = _at(-1, 0)
            evidence = task_service.materialize(s, pet, task, plan, _at(0, 12))
            task_service._deliver(s, pet, task, plan, kind='letter', text='x',
                                  evidence=evidence, run_id=None, now=_at(0, 12), reason=None)
            before = json.loads(
                s.execute(select(AgentTaskDelivery)).scalars().first().payload)['changeEvidence']
        # 基线重算：新增一个有效日 → revision +1
        self.seed_learning([_day(-1)], feature='actions', version='v1')
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            baseline.ensure_baseline(s, pet, _at(0, 12))
            after = json.loads(
                s.execute(select(AgentTaskDelivery)).scalars().first().payload)['changeEvidence']
        after_row = self.baseline_row()
        self.assertGreater(after_row['revision'], before[0]['revision'])
        self.assertEqual(before, after, '历史证据不随基线重算而变化')

    def test_second_delivery_does_not_duplicate(self):
        """同一窗口二次交付按稳定实例键去重。"""
        self.seed_baseline(walks=[10] * 7)
        task_id = self._comparison_task()
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            task = s.get(AgentTask, task_id)
            plan = task_service.plan_of(s, task)
            evidence = task_service.materialize(s, pet, task, plan, _at(0, 12))
            first = task_service._deliver(s, pet, task, plan, kind='letter', text='x',
                                          evidence=evidence, run_id=None, now=_at(0, 12),
                                          reason=None)
            second = task_service._deliver(s, pet, task, plan, kind='letter', text='x',
                                           evidence=evidence, run_id=None, now=_at(0, 12),
                                           reason=None)
            self.assertEqual(first.id, second.id)
            self.assertEqual(len(s.execute(select(AgentTaskDelivery)).scalars().all()), 1)


class DelegationFixtureTests(ChangeTestBase):
    """本地（无凭据）证明委托夹具真的走到目标评估。

    E71/E72 的行为评测需要真实凭据；这里用固定夹具时钟 + 假模型证明同一条路径：
    比较委托在截止时产出逐时段冻结证据，事实策略委托不产出个人比较。
    """

    def setUp(self):
        super().setUp()
        jobs.register_handler(task_service.CHECKPOINT_KIND, task_service.handle_checkpoint_job)

    def _window(self):
        """确认=现在，截止=次日 20:00（上海）；目标日=次日；基线=目标日之前 7 个有效日。"""
        now = datetime.now(SHANGHAI)
        start = now.astimezone(UTC)
        deadline = (now + timedelta(days=1)).replace(hour=20, minute=0, second=0,
                                                    microsecond=0).astimezone(UTC)
        target = (now + timedelta(days=1)).date()
        for offset in range(7, 0, -1):
            day = (target - timedelta(days=offset)).isoformat()
            self.seed_learning([day], slots='[1,2]')
            self.seed_minutes(day, 1, walk=10)
        self.seed_minutes(target.isoformat(), 1, walk=100)
        return start, deadline, target.isoformat()

    def _run(self, strategy):
        start, deadline, target = self._window()
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            payload = task_service.normalize_proposal_fields({
                'action': 'create',
                'plan': {'kind': 'comparison', 'question': '它上午走动是不是比平时多',
                         'feature': 'actions', 'comparison': {'band': 'above'},
                         'baselineStrategy': strategy,
                         'startAt': start.isoformat(),
                         'deadlineAt': deadline.isoformat()}})
            row, _ = proposal_service.create_task_proposal(s, pet, None, payload)
            task_service.apply_proposal(s, pet, row, baseline_strategy=strategy)
        with patch('petsense.agent.checkpoint.evaluate',
                   return_value={'decision': 'letter', 'text': '它上午走动 70 分钟。',
                                 'run_id': 7, 'error': None}):
            jobs.tick(now=deadline + timedelta(minutes=1), limit=5)
        with db.session_scope() as s:
            rows = s.execute(select(AgentTaskDelivery)).scalars().all()
        self.assertEqual(len(rows), 1, '委托应交付一条回信')
        return json.loads(rows[0].payload), target

    def test_comparison_delegation_freezes_slot_evidence(self):
        """比较委托：回信冻结目标日该时段的个人变化证据。"""
        payload, target = self._run('wait_baseline')
        frozen = payload.get('changeEvidence') or []
        self.assertEqual(len(frozen), 1, f'应冻结一条时段证据：{payload.get("changeEvidence")}')
        self.assertEqual(frozen[0]['date'], target)
        self.assertEqual(frozen[0]['direction'], 'above')
        self.assertGreater(frozen[0]['value'], frozen[0]['range']['p90'])
        self.assertTrue(frozen[0]['note'])

    def test_absolute_facts_delegation_has_no_personal_evidence(self):
        """事实策略（主人明确选择）：回信不冻结个人比较证据。"""
        payload, _target = self._run('fact_first')
        self.assertEqual(payload.get('changeEvidence') or [], [],
                         '事实策略不得冻结个人比较证据')


if __name__ == '__main__':
    unittest.main()
