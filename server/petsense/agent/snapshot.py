"""常驻氛围快照（W02，agent-w02-spec.md §3.2–§3.4）。

每轮对话运行组装一次：档案 + 环境一行（动作/在线/电量/数据年龄）+ 学习一行 + 当前时间。
体征明细、位置、今日统计、动作序列与声音事件不进快照，由 read_current_status 按需提供。
各节独立降级；体积上限 600 字符，超限先裁 learning 行、再裁 current 次要字段，核心项永不裁。
"""
import json

from petsense import config
from petsense.agent import learning
from petsense.common import shanghai_now
from petsense.db import iso_z
from petsense.db.models import TelemetryLatest
from petsense.telemetry.observations import prediction

SNAPSHOT_CHAR_LIMIT = 600

_ACTION_LABELS = {'standing': '站立', 'sitting': '坐着', 'lying down': '躺着', 'walking': '走动'}
_DIMENSION_LABELS = {'actions': '动作', 'sounds': '声音'}


def _ambient(session, pet, now):
    """环境一行：当前动作 + 在线 + 电量 + 数据年龄（口径沿用 observations 的 latest 规则）。"""
    if not pet.device_id:
        return {'state': 'unbound'}
    row = session.get(TelemetryLatest, pet.device_id)
    if row is None or row.owner_id != pet.user_id:
        return {'state': 'no_data'}
    age = max(0, int((now - row.received_at).total_seconds()))
    payload = {}
    try:
        loaded = json.loads(row.payload)
        if isinstance(loaded, dict):
            payload = loaded
    except (TypeError, ValueError):
        pass
    action = prediction(payload, 'action') if payload.get('source') == 'esp32_mlp' else None
    label = action.get('label') if isinstance(action, dict) else None
    battery = payload.get('battery') if isinstance(payload.get('battery'), dict) else {}
    percent = battery.get('percent')
    return {'action': _ACTION_LABELS.get(label, label),
            'online': age < config.DEVICE_ONLINE_TIMEOUT_S,
            'batteryPercent': round(percent) if isinstance(percent, (int, float)) and percent > 0 else None,
            'receivedAt': iso_z(row.received_at),
            'ageSeconds': age}


def _learning_line(session, pet):
    dimensions = learning.progress(session, pet)
    if not dimensions:
        return '尚无有效观测累计（学习中）'
    parts = [f"{_DIMENSION_LABELS.get(d['dimension'], d['dimension'])} {d['validDays']}/{d['requiredDays']} 天"
             for d in dimensions[:3]]
    ready = all(d['validDays'] >= d['requiredDays'] for d in dimensions)
    return '、'.join(parts) + ('（已就绪）' if ready else '（学习中）')


def _trim(sections, limit=None):
    """超限裁剪（spec §3.4）：先 learning 行，再 current 次要字段；profile/current 核心/now 永不裁。"""
    limit = limit if limit is not None else SNAPSHOT_CHAR_LIMIT
    trimmed = []

    def size():
        return len(json.dumps(sections, ensure_ascii=False))

    if size() > limit:
        trimmed.append('learning')
        sections.pop('learning', None)
    if size() > limit and isinstance(sections.get('current'), dict):
        trimmed.append('current.secondary')
        for key in ('batteryPercent', 'receivedAt'):
            sections['current'].pop(key, None)
    return trimmed


def assemble(session, pet, now):
    """组装氛围快照（W02 spec §3.3）。返回 {'sections': {...}, 'meta': {'chars','trimmed','degraded'}}。

    sections 注入系统提示；meta 仅供轨迹审计。单节失败降级为 unavailable 状态，不影响运行。
    """
    sections = {'profile': {'name': pet.name, 'species': pet.species, 'breed': pet.breed}}
    degraded = []
    try:
        sections['current'] = _ambient(session, pet, now)
    except Exception:
        sections['current'] = {'state': 'unavailable'}
        degraded.append('current')
    try:
        sections['learning'] = _learning_line(session, pet)
    except Exception:
        sections['learning'] = '学习进度暂不可用'
        degraded.append('learning')
    sections['now'] = {'shanghai': shanghai_now(now).isoformat(timespec='seconds'),
                       'utc': iso_z(now)}
    trimmed = _trim(sections)
    return {'sections': sections,
            'meta': {'chars': len(json.dumps(sections, ensure_ascii=False)),
                     'trimmed': trimmed, 'degraded': degraded}}
