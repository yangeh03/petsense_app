"""趋势统计：聚合 cloud_data 实验 health.csv + readings 实时环形缓冲（按用户设备归属）。"""

import csv
import json
import os
import time
from datetime import datetime, timezone, timedelta

from flask import Blueprint, request
from sqlalchemy import select

from petsense import config
from petsense.auth.routes import require_app_user
from petsense.common import shanghai_now
from petsense.db import session_scope, utc_now
from petsense.db.models import Reading
from petsense.experiments.recorder import manifest_device
from petsense.telemetry.realtime import device_ids_for_user

bp = Blueprint('trends', __name__, url_prefix='/api/app')

# 缓存按设备集合分键（不同用户互不串数据），15 分钟
trend_caches = {}


def _cache_key(devices):
    return ','.join(sorted(devices))


def _aggregate_experiment_health(devices):
    """聚合归属内实验 health.csv → 按北京时间日期的体征样本集（结果缓存 15 分钟）。"""
    result = {}
    if not os.path.isdir(config.CLOUD_DATA_DIR):
        return result
    for exp_id in sorted(os.listdir(config.CLOUD_DATA_DIR)):
        folder = os.path.join(config.CLOUD_DATA_DIR, exp_id)
        path = os.path.join(folder, 'health.csv')
        if not os.path.isfile(path):
            continue
        if manifest_device(folder) not in devices:
            continue
        try:
            with open(path, encoding='utf-8-sig', newline='') as handle:
                for row in csv.DictReader(handle):
                    if str(row.get('valid', '')).lower() != 'true':
                        continue
                    try:
                        dt = datetime.fromisoformat(str(row.get('received_at', '')).replace('Z', '+00:00'))
                    except ValueError:
                        continue
                    date = (dt + timedelta(hours=8)).date().isoformat()
                    bucket = result.setdefault(date, {'hr': [], 'spo2': [], 'temp': []})

                    def put(key, raw):
                        try:
                            value = float(raw)
                            if value > 0:
                                bucket[key].append(value)
                        except (TypeError, ValueError):
                            pass

                    put('hr', row.get('heart_rate_bpm'))
                    put('spo2', row.get('spo2_pct'))
                    put('temp', row.get('body_temp_c'))
        except (OSError, UnicodeDecodeError):
            continue
    return result


def _bucket_summary(entry):
    def avg(seq, nd=0):
        return round(sum(seq) / len(seq), nd) if seq else None

    return {
        'avgHr': avg(entry['hr']),
        'minHr': round(min(entry['hr'])) if entry['hr'] else None,
        'maxHr': round(max(entry['hr'])) if entry['hr'] else None,
        'avgSpo2': avg(entry['spo2'], 1),
        'avgTemp': avg(entry['temp'], 1),
        'samples': len(entry['hr']) + len(entry['spo2']) + len(entry['temp']),
    }


def _daily_trend_buckets(days, devices):
    cache = trend_caches.get(_cache_key(devices))
    if not cache or time.time() - cache['at'] > 900:
        cache = {'at': time.time(), 'daily': _aggregate_experiment_health(devices)}
        trend_caches[_cache_key(devices)] = cache
    stats = cache['daily']
    today = shanghai_now().date()
    buckets = []
    for offset in range(days - 1, -1, -1):
        date = (today - timedelta(days=offset)).isoformat()
        entry = stats.get(date)
        bucket = {'date': date, 'label': date[5:].replace('-', '/'), **(_bucket_summary(entry) if entry else {
            'avgHr': None, 'minHr': None, 'maxHr': None, 'avgSpo2': None, 'avgTemp': None, 'samples': 0})}
        buckets.append(bucket)
    # 当天实时数据（readings 环冲）合并进今天的日桶
    for bucket in _hourly_trend_buckets(devices):
        if bucket['date'] == date and bucket['samples'] > 0 and buckets[-1]['date'] == date and buckets[-1]['samples'] == 0:
            merged = {k: bucket[k] for k in ('avgHr', 'minHr', 'maxHr', 'avgSpo2', 'avgTemp', 'samples')}
            buckets[-1].update(merged)
    return buckets


def _hourly_trend_buckets(devices):
    """最近 24 小时逐小时均值（我的设备 readings，北京时间）。"""
    if not devices:
        return []
    cutoff = utc_now() - timedelta(hours=24)
    with session_scope() as s:
        rows = s.execute(select(Reading.payload, Reading.received_at)
                         .where(Reading.device_id.in_(devices),
                                Reading.received_at >= cutoff)
                         .order_by(Reading.id.asc())).fetchall()
    per_hour = {}
    for payload_text, received_at in rows:
        try:
            payload = json.loads(payload_text)
            health = payload.get('health') or {}
            if not health.get('valid'):
                continue
            dt = received_at + timedelta(hours=8)
            key = dt.strftime('%Y-%m-%d')
            hour_label = dt.strftime('%H时')
            bucket = per_hour.setdefault((key, hour_label), {'hr': [], 'spo2': [], 'temp': []})
            hr = health.get('heartRate') or 0
            spo2 = health.get('spo2') or 0
            temp = health.get('bodyTemp') or 0
            if hr > 0:
                bucket['hr'].append(float(hr))
            if spo2 > 0:
                bucket['spo2'].append(float(spo2))
            if temp > 0:
                bucket['temp'].append(float(temp))
        except (TypeError, ValueError):
            continue
    buckets = []
    for (date, hour_label), entry in sorted(per_hour.items()):
        buckets.append({
            'date': date,
            'label': hour_label,
            **_bucket_summary(entry),
        })
    return buckets


@bp.route('/trends')
def app_trends():
    user, error = require_app_user()
    if error:
        return error
    devices = device_ids_for_user(user['id'])
    period = request.args.get('period', 'week')
    if period == 'day':
        return {'success': True, 'period': 'day', 'buckets': _hourly_trend_buckets(devices)}
    if period == 'all':
        cache = trend_caches.get(_cache_key(devices))
        if not cache:
            cache = {'at': time.time(), 'daily': _aggregate_experiment_health(devices)}
            trend_caches[_cache_key(devices)] = cache
        stats = cache['daily']
        buckets = []
        for date in sorted(stats):
            entry = stats[date]
            buckets.append({
                'date': date,
                'label': date[5:].replace('-', '/'),
                **_bucket_summary(entry),
            })
        return {'success': True, 'period': 'all', 'buckets': buckets}
    days = 30 if period == 'month' else 7
    return {'success': True, 'period': f'{days}d', 'buckets': _daily_trend_buckets(days, devices)}
