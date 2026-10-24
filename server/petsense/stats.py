"""App 统计钩子：遥测帧驱动的每日统计与 UWB 快照（由 MQTT ingest 调用）。"""

import math
from datetime import datetime, timezone

from sqlalchemy import select

from petsense import config, db
from petsense.common import shanghai_now
from petsense.db import session_scope
from petsense.db.models import DailyStat, Device, Pet

stats_last_ts = {}
last_uwb_frame = None       # 兼容：最近一帧 UWB（T6 移除）
last_uwb_frames = {}        # per-device UWB 快照


def update_daily_stats(device_id, frame, received_at):
    '''按帧累加今日睡眠/活跃秒数与平均心率（received_at 为 aware UTC）。

    归属链：device → devices.owner → 该用户的宠物；未认领设备跳过。
    '''
    try:
        if not device_id:
            return
        utc_dt = received_at if received_at.tzinfo else received_at.replace(tzinfo=timezone.utc)
        imu = frame.get('imu') or {}
        accel = imu.get('accel') if imu.get('ok') else None
        if not (isinstance(accel, list) and len(accel) == 3 and all(isinstance(v, (int, float)) for v in accel)):
            return
        delta = abs(math.sqrt(sum(float(v) ** 2 for v in accel)) - 1.0)
        ts_ms = frame.get('ts_ms')
        if not isinstance(ts_ms, (int, float)):
            return
        previous = stats_last_ts.get(device_id)
        stats_last_ts[device_id] = ts_ms
        if previous is None or ts_ms <= previous:
            return
        interval = min(max(ts_ms - previous, 500), 10_000)

        sleep_inc = interval if utc_dt.hour in config.SLEEP_UTC_HOURS and delta < config.STILL_DELTA_G else 0
        active_inc = interval if delta > config.ACTIVE_DELTA_G else 0
        health = frame.get('health') or {}
        hr = health.get('heartRate') if health.get('valid') else 0
        hr_sum = float(hr) if isinstance(hr, (int, float)) and hr > 0 else 0.0
        hr_count = 1 if hr_sum > 0 else 0
        with session_scope() as s:
            pet_id = s.execute(select(Pet.id)
                               .join(Device, Device.owner_id == Pet.user_id)
                               .where(Device.device_id == device_id)).scalar_one_or_none()
            if pet_id is None:
                return
            stmt = db.dialect_insert(s, DailyStat).values(
                pet_id=pet_id, date=shanghai_now(utc_dt).date(),
                sleep_seconds=sleep_inc, active_seconds=active_inc,
                hr_sum=hr_sum, hr_count=hr_count, samples=1)
            stmt = stmt.on_conflict_do_update(
                index_elements=['pet_id', 'date'],
                set_={'sleep_seconds': DailyStat.__table__.c.sleep_seconds + stmt.excluded.sleep_seconds,
                      'active_seconds': DailyStat.__table__.c.active_seconds + stmt.excluded.active_seconds,
                      'hr_sum': DailyStat.__table__.c.hr_sum + stmt.excluded.hr_sum,
                      'hr_count': DailyStat.__table__.c.hr_count + stmt.excluded.hr_count,
                      'samples': DailyStat.__table__.c.samples + 1})
            s.execute(stmt)
    except Exception as exc:
        print(f'[APP-STATS] {exc}')


def remember_uwb(frame, received_at, device_id=None):
    global last_uwb_frame
    uwb = frame.get('uwb')
    if isinstance(uwb, dict) and 'valid' in uwb:
        entry = {'payload': frame, 'received_at': received_at}
        if device_id:
            last_uwb_frames[device_id] = entry
        last_uwb_frame = entry
