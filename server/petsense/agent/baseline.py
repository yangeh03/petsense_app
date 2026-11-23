"""个人基线计算与 read_baseline 组装（W05，agent-w05-spec.md §3.3–§3.8）。

证据层（agent_learning_days，阶段 1）只累计；本模块按维度×粗时段把有效日物化为
个人参照范围：绝对值 + 覆盖门槛（D1）、p10–p90 分位（D2）、全证据首次计算与惰性
重算（D3/D4）。基线是确定性统计工具：同一证据集可复算；「是否显著」归模型，
代码只给能否比较、范围与当日事实。样本只统计当前绑定设备；明细已过 90 天保留期
的有效日计入进度、不入样本（P00 §1）。
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from petsense.agent.learning import progress
from petsense.common import shanghai_now
from petsense.db import iso_z, utc_now
from petsense.db.models import Pet, TelemetryEvent, TelemetryMinute
from petsense.db.models.agent import AgentLearningDay, PetBaseline

SHANGHAI = ZoneInfo('Asia/Shanghai')
FEATURES = ('actions', 'sounds')
REQUIRED_DAYS = 7
WINDOW_DAYS = 28
SLOT_COVERAGE_S = 3600          # D1：时段样本需该时段覆盖 ≥ 60 分钟
DAY_COVERAGE_S = 21600          # 当天可比门槛：与有效日同口径（6 小时）
DAY_RANGE_MIN_SAMPLES = 7       # D2：全天范围就绪样本门槛
SLOT_RANGE_MIN_SAMPLES = 4      # D2：时段范围就绪样本门槛
WALK_LABEL = '走动'
# 读法纪元：走动秒数改为同时识别英文标签（walking/走动，2026-11-23）后 +1。
# 旧缓存（label_epoch 为 NULL）即使水位不变也须重算一次，否则保留按单一中文
# 标签算出的错误范围；只作废旧派生缓存，不动遥测与学习累计。
LABEL_EPOCH = 2


def walk_seconds(actions):
    """走动秒数：遥测按英文标签入库（walking），Agent 侧统一中文口径，两种都识别。"""
    if not isinstance(actions, dict):
        return 0
    return sum(value for key, value in actions.items()
               if key in (WALK_LABEL, 'walking'))

DIMENSION_LABELS = {'actions': '动作', 'sounds': '声音'}
SLOT_LABELS = {0: '夜间 00–06 点', 1: '上午 06–12 点', 2: '下午 12–18 点', 3: '晚间 18–24 点'}
METRIC_LABELS = {'actions': '走动秒数', 'sounds': '声音事件次数'}


def _percentile(ordered, q):
    """nearest-rank 分位（确定性可复算，spec §3.4）。"""
    rank = -(-len(ordered) * q // 100)  # ceil(q*n/100)，至少 1
    return ordered[max(1, min(len(ordered), rank)) - 1]


def _stats(values):
    ordered = sorted(values)
    if not ordered:
        return None
    return {'samples': len(ordered), 'p10': _percentile(ordered, 10),
            'p50': _percentile(ordered, 50), 'p90': _percentile(ordered, 90)}


def _day_bounds(day):
    start = datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=SHANGHAI)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def dimension_states(session, pet):
    """快照学习行 / observations / 学习卡共用的单一结果源（spec §3.6）。

    在 learning.progress 证据行上补真实状态与活跃版本标记；progress 已按
    lastObservedAt 降序，每个维度首个版本即活跃版本（D8）。
    """
    rows = progress(session, pet)
    active = {}
    for row in rows:
        active.setdefault(row['dimension'], row['version'])
    result = []
    for row in rows:
        item = dict(row)
        item['state'] = 'ready' if row['validDays'] >= row['requiredDays'] else 'learning'
        item['active'] = active[row['dimension']] == row['version']
        result.append(item)
    return result


def _valid_days_by_key(session, pet):
    rows = session.execute(select(AgentLearningDay).where(
        AgentLearningDay.pet_id == pet.id,
        AgentLearningDay.device_id == pet.device_id)).scalars().all()
    grouped = {}
    for row in rows:
        if row.seconds >= 21600 and len(json.loads(row.slots)) >= 2:
            grouped.setdefault((row.feature, row.version), set()).add(row.day)
    return grouped


def _window_values(session, pet, feature, version, window_days):
    """窗口内逐日采样：day_values {day: {'slots': {slot: 值(覆盖达标)}, 'day': 全天值}}。

    分钟行按 source_versions 的 "feature:version" 标注归属（阶段 1 record_interval 写入）；
    无标注的历史不归集任何版本（D3/P00 §1）。声音事件按 TelemetryEvent.model_version 过滤。
    无分钟明细的有效日（已过 90 天保留期）不入样本，由 sample_days 如实反映。
    """
    if not window_days:
        return {}
    start, _ = _day_bounds(min(window_days))
    _, end = _day_bounds(max(window_days))
    tag = f'{feature}:{version}'
    minutes = session.execute(select(TelemetryMinute).where(
        TelemetryMinute.owner_id == pet.user_id, TelemetryMinute.device_id == pet.device_id,
        TelemetryMinute.minute >= start, TelemetryMinute.minute < end,
        TelemetryMinute.source_versions.like(f'%"{tag}"%'))
        .order_by(TelemetryMinute.minute)).scalars().all()
    events = []
    if feature == 'sounds':
        events = session.execute(select(TelemetryEvent).where(
            TelemetryEvent.owner_id == pet.user_id, TelemetryEvent.device_id == pet.device_id,
            TelemetryEvent.model_version == version,
            TelemetryEvent.started_at >= start, TelemetryEvent.started_at < end)).scalars().all()
    per_day = {}

    def entry(local_day):
        return per_day.setdefault(local_day, {'value': [0] * 4, 'cover': [0] * 4})

    for minute in minutes:
        local = minute.minute.astimezone(SHANGHAI)
        day = entry(local.strftime('%Y-%m-%d'))
        slot = local.hour // 6
        day['cover'][slot] += min(60, minute.observed_seconds if feature == 'actions'
                                  else minute.sound_observed_seconds)
        if feature == 'actions':
            actions = json.loads(minute.actions) if minute.actions else {}
            day['value'][slot] += walk_seconds(actions) if isinstance(actions, dict) else 0
    for event in events:
        day = entry(event.started_at.astimezone(SHANGHAI).strftime('%Y-%m-%d'))
        day['value'][event.started_at.astimezone(SHANGHAI).hour // 6] += 1
    values = {}
    for day in window_days:
        entry_data = per_day.get(day)
        if entry_data is None:
            continue  # 明细已裁剪：计入进度、不入样本
        slots = {str(slot): entry_data['value'][slot] for slot in range(4)
                 if entry_data['cover'][slot] >= SLOT_COVERAGE_S}
        values[day] = {'slots': slots, 'day': sum(entry_data['value'])}
    return values


def ensure_baseline(session, pet, now=None):
    """惰性重算入口（D4）：水位（最新有效日、有效日数）变化才重算落库，幂等。

    由 read_baseline 与快照组装触发；W08 执行器上线后可换调度物化，语义不变。
    水位不变但读法纪元落后（读口径修复部署后的旧缓存）也重算一次。
    """
    if not pet.device_id:
        return
    grouped = _valid_days_by_key(session, pet)
    if not grouped:
        return
    stored = {(row.feature, row.version): row for row in session.execute(
        select(PetBaseline).where(PetBaseline.pet_id == pet.id)).scalars().all()}
    for (feature, version), days in grouped.items():
        ordered = sorted(days, reverse=True)
        watermark = (ordered[0], len(ordered))
        row = stored.get((feature, version))
        if (row is not None and row.label_epoch == LABEL_EPOCH
                and (row.window_last_day, row.valid_days) == watermark):
            continue
        # 比较基准只含今天（上海）之前的有效日；当天永不入样（spec §3.4）
        cutoff = (shanghai_now(now or utc_now()).date() + timedelta(days=1)).isoformat()
        window = [day for day in ordered if day < cutoff][:WINDOW_DAYS]
        day_values = _window_values(session, pet, feature, version, window)
        day_stats = _stats([item['day'] for item in day_values.values()]) or {'samples': 0}
        slot_stats = []
        for slot in range(4):
            stats = _stats([item['slots'][str(slot)] for item in day_values.values()
                            if str(slot) in item['slots']])
            slot_stats.append({'slot': slot, **stats} if stats else {'slot': slot, 'samples': 0})
        if row is None:
            row = PetBaseline(pet_id=pet.id, feature=feature, version=version,
                              revision=0, computed_at=utc_now())
            session.add(row)
        row.state = 'ready' if len(ordered) >= REQUIRED_DAYS else 'learning'
        row.valid_days = len(ordered)
        row.window_last_day = ordered[0]
        row.label_epoch = LABEL_EPOCH
        row.sample_days = json.dumps(sorted(day_values))
        row.day_values = json.dumps(day_values, ensure_ascii=False)
        row.day_stats = json.dumps(day_stats, ensure_ascii=False)
        row.slot_stats = json.dumps(slot_stats, ensure_ascii=False)
        row.revision += 1
        row.computed_at = utc_now()


# 公开别名：W11 历史参照与 W05 基线共用同一取数与裁剪规则
window_values = _window_values


def _target_day(session, pet, feature, version, date):
    """当日（或指定日）逐时段与全天事实：值、覆盖、可比性与相对范围的落带。"""
    start, end = _day_bounds(date)
    tag = f'{feature}:{version}'
    minutes = session.execute(select(TelemetryMinute).where(
        TelemetryMinute.owner_id == pet.user_id, TelemetryMinute.device_id == pet.device_id,
        TelemetryMinute.minute >= start, TelemetryMinute.minute < end,
        TelemetryMinute.source_versions.like(f'%"{tag}"%'))).scalars().all()
    value = [0] * 4
    cover = [0] * 4
    for minute in minutes:
        local = minute.minute.astimezone(SHANGHAI)
        slot = local.hour // 6
        cover[slot] += min(60, minute.observed_seconds if feature == 'actions'
                           else minute.sound_observed_seconds)
        if feature == 'actions':
            actions = json.loads(minute.actions) if minute.actions else {}
            value[slot] += walk_seconds(actions) if isinstance(actions, dict) else 0
    if feature == 'sounds':
        events = session.execute(select(TelemetryEvent).where(
            TelemetryEvent.owner_id == pet.user_id, TelemetryEvent.device_id == pet.device_id,
            TelemetryEvent.model_version == version,
            TelemetryEvent.started_at >= start, TelemetryEvent.started_at < end)).scalars().all()
        value = [0] * 4
        for event in events:
            local = event.started_at.astimezone(SHANGHAI)
            value[local.hour // 6] += 1
    return value, cover


def _band(value, p10, p90):
    if value < p10:
        return 'below'
    if value > p90:
        return 'above'
    return 'within'


# 公开别名：W11 个人比较与 W05 共用同一条落带规则（边界值等于 p10/p90 视为范围内）
band = _band


def valid_days_before(session, pet, feature, version, date):
    """目标日期之前的有效日（升序，唯一）：历史参照窗口的输入。"""
    rows = session.execute(select(AgentLearningDay).where(
        AgentLearningDay.pet_id == pet.id, AgentLearningDay.device_id == pet.device_id,
        AgentLearningDay.feature == feature, AgentLearningDay.version == version,
        AgentLearningDay.day < date)).scalars().all()
    return sorted({row.day for row in rows
                   if row.seconds >= 21600 and len(json.loads(row.slots)) >= 2})


def states_before(session, pet, date):
    """目标日期「当时」的维度状态（历史口径）。

    只统计 day < date 的学习日：后来才凑够的有效日不得回头让过去的日期变成
    「已就绪」；活跃版本也按当时的最后观测时间判定（与 progress 的口径一致，
    只是限定在目标日期之前）。返回结构与 dimension_states 对齐。
    """
    rows = session.execute(select(AgentLearningDay).where(
        AgentLearningDay.pet_id == pet.id, AgentLearningDay.device_id == pet.device_id,
        AgentLearningDay.day < date)).scalars().all()
    versions = {}
    for row in rows:
        value = versions.setdefault((row.feature, row.version),
                                    {'days': 0, 'last': row.last_seen_at})
        if row.seconds >= 21600 and len(json.loads(row.slots)) >= 2:
            value['days'] += 1
        value['last'] = max(value['last'], row.last_seen_at)
    ordered = sorted(versions.items(), key=lambda item: (item[1]['last'], item[0][1]),
                     reverse=True)
    active = {}
    for (feature, version), _value in ordered:
        active.setdefault(feature, version)
    result = []
    for (feature, version), value in ordered:
        result.append({'dimension': feature, 'version': version,
                       'validDays': value['days'], 'lastObservedAt': iso_z(value['last']),
                       'observedSeconds': 0, 'requiredDays': REQUIRED_DAYS,
                       'state': 'ready' if value['days'] >= REQUIRED_DAYS else 'learning',
                       'active': active[feature] == version})
    return result


def reference_before(session, pet, feature, version, date):
    """目标日期之前的参照样本（历史口径）。

    与 W05 同规则：只取 date 之前的有效日，按最近 28 个有效日物化；明细已过
    保留期而裁剪的有效日计入进度、不入样本（sampleDays 如实反映）。
    返回逐时段的分位统计与样本起止日期，供 W11 冻结证据引用。
    """
    valid = valid_days_before(session, pet, feature, version, date)
    window = valid[-WINDOW_DAYS:]
    day_values = _window_values(session, pet, feature, version, window)
    slots = {}
    for slot in range(4):
        values = [item['slots'][str(slot)] for item in day_values.values()
                  if str(slot) in item['slots']]
        sample_days = sorted(day for day, item in day_values.items() if str(slot) in item['slots'])
        slots[slot] = {'stats': _stats(values), 'sampleDays': sample_days}
    return {'validDaysBefore': len(valid), 'windowDays': WINDOW_DAYS,
            'sampleDays': sorted(day_values), 'slots': slots}


def slot_samples(session, pet, feature, version, date):
    """逐时段的参照样本（只取 date 之前的有效日，目标日期自身不入样本）。

    返回 {slot: {'stats': 分位统计|None, 'sampleDays': [日期…]}}，与 W05 的时段
    样本口径一致（覆盖达标才算样本）；供 W11 个人比较与冻结证据复用。
    """
    row = session.get(PetBaseline, (pet.id, feature, version))
    if row is None:
        return {}
    day_values = {day: item for day, item in json.loads(row.day_values).items() if day < date}
    result = {}
    for slot in range(4):
        values = [item['slots'][str(slot)] for item in day_values.values()
                  if str(slot) in item['slots']]
        sample_days = sorted(day for day, item in day_values.items() if str(slot) in item['slots'])
        result[slot] = {'stats': _stats(values), 'sampleDays': sample_days}
    return result


def target_slots(session, pet, feature, version, date):
    """当日逐时段实际值与覆盖（秒）：{'values': [4], 'cover': [4]}。"""
    values, cover = _target_day(session, pet, feature, version, date)
    return {'values': values, 'cover': cover}


def _dimension_payload(session, pet, state_row, date, today, now):
    feature, version = state_row['dimension'], state_row['version']
    label = DIMENSION_LABELS.get(feature, feature)
    base = {'feature': feature, 'label': label, 'metric': METRIC_LABELS.get(feature),
            'version': version, 'state': state_row['state'],
            'validDays': state_row['validDays'], 'requiredDays': state_row['requiredDays']}
    row = session.get(PetBaseline, (pet.id, feature, version))
    if row is None or state_row['state'] != 'ready':
        base.update({'comparable': False,
                     'note': f"认识中（{state_row['validDays']}/{state_row['requiredDays']} 个有效日），"
                             '还没有个人参照范围，不能说「比平时」，只能给当天事实或两段观测的对比。'
                             '详情可用 query_observations 查询。'})
        return base
    base.update({'revision': row.revision, 'comparable': True})
    # 指定日期截断：样本只取该日期之前（spec §3.4）；窗口外的更早日期样本数如实减少
    day_values = {day: item for day, item in json.loads(row.day_values).items() if day < date}
    sample_days = sorted(day_values)
    day_stats = _stats([item['day'] for item in day_values.values()]) or {'samples': 0}
    day_ready = day_stats['samples'] >= DAY_RANGE_MIN_SAMPLES
    slot_stats = []
    samples = slot_samples(session, pet, feature, version, date)
    for slot in range(4):
        stats = (samples.get(slot) or {}).get('stats')
        slot_stats.append({'slot': slot, **stats} if stats else {'slot': slot, 'samples': 0})
    target = target_slots(session, pet, feature, version, date)
    values, cover = target['values'], target['cover']
    now_sh = shanghai_now(now)
    slots_payload = []
    for slot in range(4):
        stats = slot_stats[slot]
        range_ready = stats.get('samples', 0) >= SLOT_RANGE_MIN_SAMPLES
        entry = {'slot': slot, 'label': SLOT_LABELS[slot],
                 'ready': range_ready, 'samples': stats.get('samples', 0)}
        if range_ready:
            entry['range'] = {key: stats[key] for key in ('p10', 'p50', 'p90')}
        slot_end = _day_bounds(date)[0] + timedelta(hours=6 * (slot + 1))
        target = {'value': values[slot], 'coverageMinutes': int(cover[slot] // 60)}
        if date < today or slot_end <= now_sh:
            if cover[slot] < SLOT_COVERAGE_S:
                target.update({'comparable': False, 'note': '该时段覆盖不足60分钟，不能当作活动减少或增加'})
            elif not range_ready:
                target.update({'comparable': False, 'note': '该时段有效样本不足，暂无个人参照'})
            else:
                target.update({'comparable': True,
                               'band': _band(values[slot], stats['p10'], stats['p90'])})
        else:
            target.update({'comparable': False, 'note': '时段未结束，数值为截至今的累计'})
        entry['target'] = target
        slots_payload.append(entry)
    day_coverage = sum(cover)
    day_target = {'value': sum(values), 'coverageMinutes': int(day_coverage // 60)}
    if date < today or day_coverage >= DAY_COVERAGE_S:
        if day_ready:
            day_target.update({'comparable': True,
                               'band': _band(sum(values), day_stats['p10'], day_stats['p90'])})
        else:
            day_target.update({'comparable': False, 'note': '全天有效样本不足，暂无个人参照'})
    else:
        day_target.update({'comparable': False, 'note': '当天覆盖不足6小时，数值不完整，不作比较'})
    base.update({'window': {'firstSampleDay': sample_days[0] if sample_days else None,
                            'lastSampleDay': sample_days[-1] if sample_days else None,
                            'sampleDays': len(sample_days), 'windowDays': WINDOW_DAYS},
                 'day': {'ready': day_ready, 'samples': day_stats['samples'],
                         'range': {key: day_stats[key] for key in ('p10', 'p50', 'p90')} if day_ready else None,
                         'target': day_target},
                 'slots': slots_payload})
    return base


def read_baseline(session, pet, features=None, date_str=None, now=None):
    """工具与 HTTP 共用的组装（spec §3.5/§3.7）。date 缺省今天，按上海零点解释。"""
    now = now or utc_now()
    today = shanghai_now(now).date().isoformat()
    date = date_str or today
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise ValueError('date 应为 YYYY-MM-DD（上海日期）')
    if date > today:
        raise ValueError('date 不能晚于今天')
    ensure_baseline(session, pet, now)
    states = dimension_states(session, pet)
    wanted = list(features) if features else list(FEATURES)
    dims = []
    for feature in wanted:
        state_row = next((item for item in states if item['dimension'] == feature and item['active']), None)
        if state_row is None:
            dims.append({'feature': feature, 'label': DIMENSION_LABELS.get(feature, feature),
                         'metric': METRIC_LABELS.get(feature), 'state': 'learning',
                         'validDays': 0, 'requiredDays': REQUIRED_DAYS, 'comparable': False,
                         'note': '尚无有效观测累计（学习中），不能说「比平时」。'})
            continue
        dims.append(_dimension_payload(session, pet, state_row, date, today, now))
    window_days = []
    for dim in dims:
        window = dim.get('window') or {}
        if window.get('firstSampleDay'):
            window_days.append(window['firstSampleDay'])
        if window.get('lastSampleDay'):
            window_days.append(window['lastSampleDay'])
    result = {'date': date, 'dimensions': dims,
              'coverageRules': {'slotSampleMinutes': SLOT_COVERAGE_S // 60,
                                'dayComparableHours': DAY_COVERAGE_S // 3600,
                                'note': '参照范围来自最近有效日样本；覆盖不足的时段不算样本、当天不可比，'
                                        '不能当作活动减少或增加。'},
              'source': {'type': 'baseline', 'deviceId': pet.device_id,
                         'features': [dim['feature'] for dim in dims],
                         'versions': {dim['feature']: dim.get('version') for dim in dims},
                         'startAt': iso_z(_day_bounds(min(window_days))[0]) if window_days else None,
                         'endAt': iso_z(_day_bounds(max(window_days))[1]) if window_days else None}}
    return result
