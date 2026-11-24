"""W11 个人变化合流（agent-w11-spec.md）：个人比较的确定性资格判定与冻结证据。

判断归模型、事实与执行归代码：本模块只回答一个确定性问题——「同一维度、同一时段、
覆盖达标、实际值严格超出 W05 的 p10–p90 吗」，并给出可冻结的证据字段（样本窗口、
基线版本与修订号、分位数、实际值、覆盖、方向）。

不够格的原因如实区分（D2 与验收 A1）：维度学习、时段样本不足、观测时段未结束、
当前覆盖不足、有效但处于范围内。边界值等于 p10 或 p90 一律视为范围内；目标日期
自身永不入样本（只取该日期之前的有效日）。

W11 决策点：比较单元统一为 (维度, 日期, 粗时段)。日回顾按对应粗时段比较，周回顾
汇总本周已结束且可比的日内时段（不作整周总量比较，D4）；委托交付按同一单元冻结
证据（排除确认前、等待期与暂停期）。
"""
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from petsense.agent import baseline
from petsense.agent.baseline import (DIMENSION_LABELS, METRIC_LABELS, REQUIRED_DAYS,
                                     SLOT_COVERAGE_S, SLOT_LABELS, SLOT_RANGE_MIN_SAMPLES,
                                     WINDOW_DAYS, band, dimension_states, slot_samples,
                                     states_before, target_slots, valid_days_before,
                                     window_values)
from petsense.common import shanghai_now
from petsense.db import iso_z, utc_now
from petsense.db.models import TelemetryEvent, TelemetryMinute
from petsense.db.models.agent import PetBaseline

SHANGHAI = timezone(timedelta(hours=8))
SLOT_HOURS = 6
FEATURES = ('actions', 'sounds')

# 单位：动作=走动秒数，声音=事件次数（两个维度独立统计，互不混用）
UNITS = {'actions': 'seconds', 'sounds': 'count'}

# 资格状态（与 W11-A1 的可区分原因一一对应）
STATUS_LEARNING = 'baseline_learning'      # 维度仍在学习：没有个人参照范围
STATUS_SAMPLE = 'slot_sample_insufficient'  # 该时段历史有效样本不足
STATUS_OPEN = 'slot_not_ended'            # 观测时段尚未结束
STATUS_COVERAGE = 'coverage_insufficient'  # 当前覆盖不足（<60 分钟）
STATUS_WITHIN = 'within'                   # 有效但处于范围内（含边界值）
STATUS_BELOW = 'below'                     # 严格低于 p10
STATUS_ABOVE = 'above'                     # 严格高于 p90

ELIGIBLE_STATUSES = (STATUS_BELOW, STATUS_ABOVE)
COMPARABLE_STATUSES = (STATUS_BELOW, STATUS_ABOVE, STATUS_WITHIN)

# 个人比较口播的识别词：没有合格证据时不得出现这些说法（服务端确定性兜底）
CLAIM_TOKENS = ('比平时', '高于平时', '低于平时', '平时范围', '比以往', '比往常',
                '高于它的平时', '低于它的平时', '超出平时', '偏离平时', '比平时少',
                '比平时多', '少于平时', '多于平时')


def format_value(value, unit):
    """指标数值按单位展示：走动=分钟，声音=次数。"""
    if value is None:
        return '无数据'
    if unit == 'count':
        return f'{value} 次'
    return f'{round(value / 60, 1)} 分钟'


def comparative_clause(evidence):
    """服务端生成的用户可见比较句（方向与数值只来自已验证证据，不依赖模型措辞）。"""
    dimension = evidence.get('dimension') or '指标'
    slot = evidence.get('slotLabel') or '该时段'
    unit = evidence.get('unit')
    direction = '高于' if evidence.get('direction') == 'above' else '低于'
    rng = evidence.get('range') or {}
    return (f'{dimension}{slot}实际 {format_value(evidence.get("value"), unit)}，'
            f'{direction}平时范围（{format_value(rng.get("p10"), unit)}–'
            f'{format_value(rng.get("p90"), unit)}）。')


# 模型措辞里的比较方向（用于校验措辞与已接受证据是否相反）
DIRECTION_CLAIMS = {
    'above': ('比平时多', '高于平时', '比平时高', '多于平时', '高于它的平时', '比平日多'),
    'below': ('比平时少', '低于平时', '比平时低', '少于平时', '低于它的平时', '比平日少'),
}


# 否定标记与分句边界：同一条分句里（结论词之前）出现否定标记时，后面的比较说法是
# 被否定的**说明**而不是断言——「无法判断这是否比平时少」「也不能说比平时多或少」
# 恰恰是如实交代。到期回信的守卫据此判定（真实模型轮次里 W08 的回信曾因纯子串匹配
# 稳定落降级信）；周期回顾另有更严的规则（学习期一律不提「比平时」），仍用纯子串。
NEGATION_MARKERS = ('不能', '无法', '没法', '不会', '没有', '尚未', '还没', '不可', '不足以',
                    '不应', '不要', '不宜', '不至于', '不代表', '不等于', '并非', '不是',
                    '无从', '难以', '谈不上', '称不上', '说不准', '不确定', '未能', '说不清',
                    '别', '无以')
CLAUSE_BOUNDARIES = '，。；;、！？!?,:\n'


def _affirmed_mentions(text, tokens):
    """tokens 里被**肯定**提及的说法（否定式说明不算）。"""
    text = text if isinstance(text, str) else ''
    affirmed = []
    for token in tokens:
        for match in re.finditer(re.escape(token), text):
            clause_start = max(text.rfind(boundary, 0, match.start())
                               for boundary in CLAUSE_BOUNDARIES) + 1
            clause = text[clause_start:match.start()]
            if not any(marker in clause for marker in NEGATION_MARKERS):
                affirmed.append(token)
                break
    return affirmed


def claim_directions(text):
    """文本里被**肯定断言**的比较方向集合（无方向词或只有否定式说明时为空集）。"""
    return {direction for direction, tokens in DIRECTION_CLAIMS.items()
            if _affirmed_mentions(text, tokens)}


def asserts_comparison(text):
    """文本是否**肯定地**断言了「与个人基线相比」；服务端据此拦截无依据的个人比较。

    否定式的说明（「无法判断是否比平时多」「不能说比平时少」）如实交代了不能比较，
    不算断言；只识别同一分句内的否定标记，分句里没有否定标记时仍判为断言。
    """
    return bool(_affirmed_mentions(text, CLAIM_TOKENS))


def claims_personal_change(text):
    """文本是否**提到**「与个人基线相比」（纯子串，不区分否定式说明）；只用于诊断字段。"""
    if not isinstance(text, str):
        return False
    return any(token in text for token in CLAIM_TOKENS)


def slot_bounds(date_text, slot):
    """粗时段边界（上海时间，左闭右开）：slot = 小时 // 6。"""
    start = datetime.strptime(date_text, '%Y-%m-%d').replace(tzinfo=SHANGHAI)
    return (start + timedelta(hours=SLOT_HOURS * slot)).astimezone(timezone.utc), \
           (start + timedelta(hours=SLOT_HOURS * (slot + 1))).astimezone(timezone.utc)


def slot_observations(session, pet, feature, version, windows):
    """按有效观察窗口汇总逐日逐时段的实际值与覆盖（秒）。

    windows 为绝对时间子窗口列表（已排除确认前、等待期与暂停期）。只统计落在窗口
    内的分钟与声音事件；分钟行按 source_versions 的 "feature:version" 标注归属，
    声音事件按 model_version 过滤——换版前的旧版本数据不用于补齐新版本。
    返回 {(日期, slot): {'value':…, 'cover':…}}。
    """
    spans = [(a, b) for a, b in windows if b > a]
    if not spans:
        return {}
    start = min(a for a, _ in spans)
    end = max(b for _, b in spans)
    tag = f'{feature}:{version}'
    buckets = {}

    def bucket_of(date_text, slot):
        return buckets.setdefault((date_text, slot), {'value': 0, 'cover': 0})

    minutes = session.execute(select(TelemetryMinute).where(
        TelemetryMinute.owner_id == pet.user_id, TelemetryMinute.device_id == pet.device_id,
        TelemetryMinute.minute >= start, TelemetryMinute.minute < end,
        TelemetryMinute.source_versions.like(f'%"{tag}"%'))).scalars().all()
    for minute in minutes:
        stamp = minute.minute
        if not any(a <= stamp < b for a, b in spans):
            continue
        local = stamp.astimezone(SHANGHAI)
        entry = bucket_of(local.strftime('%Y-%m-%d'), local.hour // SLOT_HOURS)
        entry['cover'] += min(60, minute.observed_seconds if feature == 'actions'
                              else minute.sound_observed_seconds)
        if feature == 'actions':
            actions = json.loads(minute.actions) if minute.actions else {}
            if isinstance(actions, dict):
                entry['value'] += sum(value for key, value in actions.items()
                                      if key in ('走动', 'walking'))
    if feature == 'sounds':
        events = session.execute(select(TelemetryEvent).where(
            TelemetryEvent.owner_id == pet.user_id, TelemetryEvent.device_id == pet.device_id,
            TelemetryEvent.model_version == version,
            TelemetryEvent.started_at >= start, TelemetryEvent.started_at < end)).scalars().all()
        for event in events:
            stamp = event.started_at
            if not any(a <= stamp < b for a, b in spans):
                continue
            local = stamp.astimezone(SHANGHAI)
            bucket_of(local.strftime('%Y-%m-%d'), local.hour // SLOT_HOURS)['value'] += 1
    return buckets


def compare_day(session, pet, date, now=None, features=None, targets=None, reference=None):
    """某一天逐维度逐时段的个人比较候选（比较单元 = 维度 × 日期 × 粗时段）。

    targets 可传入预置的实际值与覆盖（{'values': [4], 'cover': [4]}），用于委托
    按有效观察窗口统计的场景；缺省取当日原始实际值（回顾场景）。
    """
    now = now or utc_now()
    now_sh = shanghai_now(now)
    today = now_sh.date().isoformat()
    # 历史口径：状态与活跃版本取「目标日期当时」，后来凑够的有效日不得回头改变过去的判断
    states = None
    if reference:
        states = (reference.get('states_by_date') or {}).get(date)
    states = states or baseline.states_before(session, pet, date)
    records = []
    for feature in (features or FEATURES):
        state_row = next((s for s in states if s['dimension'] == feature and s.get('active')), None)
        version = state_row['version'] if state_row else None
        base = {'feature': feature, 'dimension': DIMENSION_LABELS.get(feature, feature),
                'metric': METRIC_LABELS.get(feature), 'unit': UNITS[feature],
                'date': date, 'version': version,
                'baselineReady': bool(state_row and state_row['state'] == 'ready'),
                'validDays': state_row['validDays'] if state_row else 0,
                'requiredDays': state_row['requiredDays'] if state_row else REQUIRED_DAYS}
        if not base['baselineReady']:
            for slot in range(4):
                records.append({**base, 'slot': slot, 'slotLabel': SLOT_LABELS[slot],
                                'revision': None, 'status': STATUS_LEARNING, 'comparable': False,
                                'eligible': False, 'direction': None, 'value': None,
                                'coverageSeconds': 0, 'coverageMinutes': 0, 'samples': 0,
                                'sampleStartDay': None, 'sampleEndDay': None, 'range': None,
                                'evidenceId': f'{feature}:{date}:{slot}',
                                'note': f"认识中（{base['validDays']}/{base['requiredDays']} 个有效日），"
                                        '个人参照范围还在建立中，只陈述绝对事实'})
            continue
        row = session.get(PetBaseline, (pet.id, feature, version))
        revision = row.revision if row is not None else None
        if reference:
            samples = reference.get('slots', {}).get(feature, {}).get(date, {})
        else:
            samples = baseline.reference_before(session, pet, feature, version, date)['slots']
        target = (targets or {}).get(feature) or target_slots(session, pet, feature, version, date)
        for slot in range(4):
            info = samples.get(slot) or {}
            stats = info.get('stats') or {'samples': 0}
            sample_days = info.get('sampleDays') or []
            value = target['values'][slot]
            cover = int(target['cover'][slot])
            record = {**base, 'slot': slot, 'slotLabel': SLOT_LABELS[slot], 'revision': revision,
                      'value': value, 'coverageSeconds': cover, 'coverageMinutes': cover // 60,
                      'samples': stats.get('samples', 0),
                      'sampleStartDay': sample_days[0] if sample_days else None,
                      'sampleEndDay': sample_days[-1] if sample_days else None,
                      'evidenceId': f'{feature}:{date}:{slot}'}
            slot_end = slot_bounds(date, slot)[1]
            if date < today or slot_end <= now_sh:
                if cover < SLOT_COVERAGE_S:
                    record.update({'status': STATUS_COVERAGE, 'comparable': False, 'eligible': False,
                                   'direction': None, 'range': None,
                                   'note': '该时段覆盖不足60分钟，不能当作活动减少或增加'})
                elif stats.get('samples', 0) < SLOT_RANGE_MIN_SAMPLES:
                    record.update({'status': STATUS_SAMPLE, 'comparable': False, 'eligible': False,
                                   'direction': None, 'range': None,
                                   'note': '该时段有效样本不足，暂无个人参照'})
                else:
                    level = band(value, stats['p10'], stats['p90'])
                    record.update({'status': level, 'comparable': True,
                                   'eligible': level in ELIGIBLE_STATUSES,
                                   'direction': level if level in ELIGIBLE_STATUSES else None,
                                   'range': {key: stats[key] for key in ('p10', 'p50', 'p90')},
                                   'note': None})
            else:
                record.update({'status': STATUS_OPEN, 'comparable': False, 'eligible': False,
                               'direction': None, 'range': None,
                               'note': '时段未结束，数值为截至今的累计'})
            records.append(record)
    return records


def freeze(record):
    """冻结证据（D6）：样本窗口、基线版本与修订号、分位数、实际值、覆盖与方向。

    冻结后不再依赖当时的基线行：之后基线重算、修订号变化、解绑或源明细裁剪，
    历史证据仍按冻结内容展示（读取时不重算比较、不调用模型）。
    """
    start, end = slot_bounds(record['date'], record['slot'])
    frozen = {'evidenceId': record.get('evidenceId'), 'feature': record.get('feature'),
            'dimension': record.get('dimension'), 'metric': record.get('metric'),
            'unit': record.get('unit'), 'date': record['date'], 'slot': record['slot'],
            'slotLabel': record.get('slotLabel'),
            'slotStartAt': iso_z(start), 'slotEndAt': iso_z(end),
            'value': record.get('value'), 'coverageSeconds': record.get('coverageSeconds'),
            'coverageMinutes': record.get('coverageMinutes'), 'samples': record.get('samples'),
            'sampleStartDay': record.get('sampleStartDay'),
            'sampleEndDay': record.get('sampleEndDay'),
            'version': record.get('version'), 'revision': record.get('revision'),
            'range': record.get('range'), 'direction': record.get('direction'),
            'deviceId': record.get('deviceId'),
            'sourceLabel': '项圈观测 · 个人基线参照'}
    frozen['note'] = comparative_clause(frozen)
    return frozen


def _reference_batch(session, pet, dates, features=None):
    """批量历史参照：一次取分钟，按各目标日期切出自己的参照窗口。

    每个目标日期的窗口 = 该日期之前最近的 28 个有效日（W05 规则）；明细已裁剪的
    有效日计入进度、不入样本。活跃版本按目标日期当时判定（周内换版时分别取用）。
    """
    wanted = list(features or FEATURES)
    states_by_date = {date: baseline.states_before(session, pet, date) for date in dates}
    out = {'states_by_date': states_by_date, 'slots': {}}
    for feature in wanted:
        by_version = {}
        for date in dates:
            row = next((s for s in states_by_date[date]
                        if s['dimension'] == feature and s.get('active')), None)
            if row is None:
                continue
            by_version.setdefault(row['version'], []).append(date)
        for version, version_dates in by_version.items():
            windows = {}
            needed = set()
            for date in version_dates:
                valid = valid_days_before(session, pet, feature, version, date)
                windows[date] = valid[-WINDOW_DAYS:]
                needed.update(windows[date])
            if not needed:
                continue
            values = window_values(session, pet, feature, version, sorted(needed))
            for date, window in windows.items():
                picked = {day: values[day] for day in window if day in values}
                per_slot = {}
                for slot in range(4):
                    slot_values = [item['slots'][str(slot)] for item in picked.values()
                                   if str(slot) in item['slots']]
                    sample_days = sorted(day for day, item in picked.items()
                                         if str(slot) in item['slots'])
                    per_slot[slot] = {'stats': baseline._stats(slot_values),
                                      'sampleDays': sample_days}
                out['slots'].setdefault(feature, {})[date] = per_slot
    return out


def review_candidates(session, pet, window_start, window_end, now=None, tz=None):
    """回顾窗口内的个人变化候选（D4）。

    日回顾按对应粗时段比较；周回顾汇总本周已结束且可比的日内时段，不作整周总量
    比较。返回 {'dates':[…], 'candidates':[…], 'eligible':[…], 'counts': {…}}：
    candidates 为全部时段记录（含不可比原因），eligible 仅含真正超出范围的时段。
    """
    now = now or utc_now()
    tz = tz or SHANGHAI
    baseline.ensure_baseline(session, pet, now)   # 回顾需要当期基线（惰性物化）
    day = window_start.astimezone(tz).date()
    last = (window_end - timedelta(microseconds=1)).astimezone(tz).date()
    dates = []
    while day <= last and len(dates) < 31:
        dates.append(day.isoformat())
        day += timedelta(days=1)
    reference = _reference_batch(session, pet, dates)
    records = []
    for date in dates:
        records.extend(compare_day(session, pet, date, now=now, reference=reference))
    eligible = [r for r in records if r.get('eligible')]
    comparable = [r for r in records if r.get('comparable')]
    return {'dates': dates,
            'candidates': records,
            'eligible': eligible,
            'counts': {'slots': len(records), 'comparable': len(comparable),
                       'eligible': len(eligible),
                       'notComparable': len(records) - len(comparable)}}


def eligible_map(records):
    """按 evidenceId 建索引（模型引用与服务端校验共用同一键空间）。"""
    return {record['evidenceId']: record for record in records}
