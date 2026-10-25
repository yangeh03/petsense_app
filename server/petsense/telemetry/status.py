"""设备最新遥测解析：readings 取最新帧，输出在线状态与体征（pets/devices 域共用）。"""

import json
import time

from sqlalchemy import and_, or_, select

from petsense import config
from petsense.db import iso_z, session_scope
from petsense.db.models import Reading, Device, TelemetryLatest


def latest_telemetry(device_id):
    """设备最新有效帧的体征/电量快照；无数据返回 None。

    过渡期兼容：旧帧 device_id 为 NULL，按 topic 回退匹配（多设备管道切换后简化为仅 device_id）。
    """
    if not device_id:
        return None
    with session_scope() as s:
        binding = s.get(Device, device_id)
        if binding is None:
            return None
        checkpoint = s.get(TelemetryLatest, device_id)
        if checkpoint:
            if checkpoint.owner_id != binding.owner_id or checkpoint.received_at < binding.claimed_at:
                return None
            rows = [(checkpoint.payload, checkpoint.received_at)]
        else:
            rows = s.execute(select(Reading.payload, Reading.received_at)
                             .where(or_(Reading.device_id == device_id,
                                        and_(Reading.device_id.is_(None),
                                             Reading.topic == f'petsense/sensor/{device_id}')),
                                    Reading.received_at >= binding.claimed_at)
                             .order_by(Reading.id.desc()).limit(5)).fetchall()
    for payload_text, received_at in rows:
        try:
            payload = json.loads(payload_text)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        health = payload.get('health') or {}
        battery = payload.get('battery') or {}
        age = time.time() - received_at.timestamp()
        return {
            'heartRate': health.get('heartRate') or 0,
            'spo2': health.get('spo2') or 0,
            'bodyTemp': health.get('bodyTemp') or 0,
            'envTemp': health.get('envTemp') or 0,
            'respiration': health.get('respiration') or 0,
            'hrvSdnn': health.get('hrvSdnn') or 0,
            'microCir': health.get('microCir') or 0,
            'fatigue': health.get('fatigue') or 0,
            'systolic': health.get('systolic') or 0,
            'diastolic': health.get('diastolic') or 0,
            'valid': bool(health.get('valid')),
            'batteryPercent': round(battery.get('percent') or 0),
            'online': age < config.DEVICE_ONLINE_TIMEOUT_S,
            'receivedAt': iso_z(received_at),
        }
    return None
