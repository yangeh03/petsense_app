"""App 统计钩子：遥测帧驱动的每日统计与 UWB 快照（由 MQTT ingest 调用）。"""

import math
from datetime import datetime, timezone

from petsense import config, db
from petsense.common import shanghai_now

stats_last_ts = {}
last_uwb_frame = None


def update_daily_stats(device_id, frame, received_at):
    '''按帧累加今日睡眠/活跃秒数与平均心率（IMU 幅值派生，部署日起累积）。'''
    try:
        if not device_id:
            return
        with db.app_connect() as database:
            pet = database.execute('SELECT id FROM pets WHERE device_id=? ORDER BY id LIMIT 1', (device_id,)).fetchone()
            if not pet:
                return
            try:
                utc_dt = datetime.fromisoformat(received_at.replace('Z', '+00:00'))
            except ValueError:
                return
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
            database.execute('''INSERT INTO daily_stats(pet_id,date,sleep_seconds,active_seconds,hr_sum,hr_count,samples)
                VALUES(?,?,?,?,?,?,1)
                ON CONFLICT(pet_id,date) DO UPDATE SET
                    sleep_seconds=sleep_seconds+excluded.sleep_seconds,
                    active_seconds=active_seconds+excluded.active_seconds,
                    hr_sum=hr_sum+excluded.hr_sum,
                    hr_count=hr_count+excluded.hr_count,
                    samples=samples+1''',
                       (pet['id'], shanghai_now(utc_dt).date().isoformat(), sleep_inc, active_inc, hr_sum, hr_count))
    except Exception as exc:
        print(f'[APP-STATS] {exc}')


def remember_uwb(frame, received_at):
    global last_uwb_frame
    uwb = frame.get('uwb')
    if isinstance(uwb, dict) and 'valid' in uwb:
        last_uwb_frame = {'payload': frame, 'received_at': received_at}
