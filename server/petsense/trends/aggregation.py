"""Owned-device health records, shared by every trend range."""
import csv
import math
import os
import time
from collections import Counter, OrderedDict
from datetime import datetime, timezone, timedelta
from threading import RLock
from zoneinfo import ZoneInfo

from petsense import config
from petsense.experiments.recorder import manifest_device

SHANGHAI = ZoneInfo('Asia/Shanghai')
METRICS = ('heartRate', 'spo2', 'bodyTemp')
CSV_FIELDS = ('heart_rate_bpm', 'spo2_pct', 'body_temp_c')
_archive_cache = OrderedDict()
_cache_lock = RLock()


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def timestamp(raw):
    dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    # Legacy received_at without an offset is server UTC.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def positive(raw):
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def archive_records(devices):
    """Cache only complete file snapshots; append/replace invalidates on next request."""
    records, warnings = [], []
    root = config.CLOUD_DATA_DIR
    if not os.path.exists(root):
        return records, warnings
    try:
        folders = sorted(os.scandir(root), key=lambda item: item.name)
    except OSError:
        return records, ['部分历史归档暂时无法读取']
    for folder in folders:
        if not folder.is_dir():
            continue
        device = manifest_device(folder.path)
        if device not in devices:
            continue
        path = os.path.join(folder.path, 'health.csv')
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            continue
        except OSError:
            warnings.append('部分历史归档暂时无法读取')
            continue
        signature = (stat.st_mtime_ns, stat.st_size, device)
        with _cache_lock:
            cached = _archive_cache.get(path)
            if cached and cached[0] == signature and time.monotonic() - cached[1] < 900:
                _archive_cache.move_to_end(path)
                records.extend(cached[2])
                warnings.extend(cached[3])
                continue
        rows, problems = [], []
        try:
            with open(path, encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                required = {'received_at', 'valid', *CSV_FIELDS}
                if not required.issubset(reader.fieldnames or []):
                    problems.append('部分历史归档格式不完整')
                else:
                    for row in reader:
                        if str(row.get('valid', '')).lower() != 'true':
                            continue
                        values = tuple(positive(row.get(key)) for key in CSV_FIELDS)
                        if all(value is None for value in values):
                            continue
                        try:
                            rows.append((device, timestamp(row['received_at']), values))
                        except (ValueError, TypeError, OverflowError):
                            problems.append('部分历史记录时间无效，已跳过')
        except (OSError, UnicodeError, csv.Error):
            problems.append('部分历史归档暂时无法读取')
        # A concurrent append is not cached under a new signature: next request re-reads.
        with _cache_lock:
            _archive_cache[path] = (signature, time.monotonic(), rows, list(set(problems)))
            _archive_cache.move_to_end(path)
            while len(_archive_cache) > 16:
                _archive_cache.popitem(last=False)
        records.extend(rows)
        warnings.extend(problems)
    return records, sorted(set(warnings))


def merge_records(archived, live):
    """Legacy CSV times have millisecond precision; DB times retain microseconds.

    Prefer DB records when the same sample is archived, without rounding or
    collapsing distinct DB frames that happened to arrive in one millisecond.
    """
    def key(record):
        device, received, values = record
        return device, received.replace(microsecond=received.microsecond // 1000 * 1000), values

    live = set(live)
    available = Counter(key(record) for record in live)
    merged = list(live)
    for record in sorted(set(archived), key=lambda row: row[1]):
        identity = key(record)
        if available[identity]:
            available[identity] -= 1
        else:
            merged.append(record)
    return merged


def stats(values):
    if not values:
        return {'count': 0, 'avg': None, 'min': None, 'max': None}
    return {'count': len(values), 'avg': math.fsum(values) / len(values),
            'min': min(values), 'max': max(values)}


def summarize(records):
    return {key: stats([record[2][i] for record in records if record[2][i] is not None])
            for i, key in enumerate(METRICS)}


def floor_time(dt, hourly):
    local = dt.astimezone(SHANGHAI)
    return local.replace(minute=0, second=0, microsecond=0,
                         **({} if hourly else {'hour': 0})).astimezone(timezone.utc)


def build_trends(records, period, now, warnings=(), has_devices=True):
    hourly = period == 'day'
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    start = (now - timedelta(hours=24) if hourly else
             floor_time(now, False) - timedelta(days=6 if period == 'week' else 29))
    # Exact duplicate records are also removed; source precision is reconciled before this step.
    unique = {record for record in records if record[1] <= now and
              (period == 'all' or record[1] >= start)}
    ordered = sorted(unique, key=lambda row: row[1])
    if period == 'all':
        start = floor_time(ordered[0][1], False) if ordered else floor_time(now, False)
    grouped = {}
    for record in ordered:
        key = floor_time(record[1], hourly)
        grouped.setdefault(key, []).append(record)
    if period == 'all':
        keys = sorted(grouped)
    elif has_devices:
        keys = []
        cursor = floor_time(start, hourly)
        while cursor <= floor_time(now, hourly):
            keys.append(cursor)
            cursor += step
    else:
        keys = []
    buckets = []
    for key in keys:
        rows = grouped.get(key, [])
        metrics = summarize(rows)
        hr, spo2, temp = (metrics[name] for name in METRICS)
        local = key.astimezone(SHANGHAI)
        buckets.append({
            'date': local.date().isoformat(),
            'label': local.strftime('%m/%d %H:%M' if hourly else '%m/%d'),
            'start': iso(max(key, start)), 'end': iso(min(key + step, now)),
            'metrics': metrics, 'samples': len(rows),
            'avgHr': hr['avg'], 'minHr': hr['min'], 'maxHr': hr['max'],
            'avgSpo2': spo2['avg'], 'minSpo2': spo2['min'], 'maxSpo2': spo2['max'],
            'avgTemp': temp['avg'], 'minTemp': temp['min'], 'maxTemp': temp['max'],
        })
    total = (int((floor_time(now, hourly) - floor_time(start, hourly)) / step) + 1
             if has_devices and (period != 'all' or ordered) else 0)
    return {
        'success': True, 'schemaVersion': 2, 'period': period,
        'generatedAt': iso(now),
        'window': {'start': iso(start), 'end': iso(now),
                   'granularity': 'hour' if hourly else 'day'},
        'actualRange': {'start': iso(ordered[0][1]), 'end': iso(ordered[-1][1])} if ordered else None,
        'summary': summarize(ordered), 'buckets': buckets,
        'coverage': {name: {'recordedBuckets': sum(b['metrics'][name]['count'] > 0 for b in buckets),
                            'totalBuckets': total} for name in METRICS},
        'warnings': list(warnings),
    }
