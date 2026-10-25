"""Owned-device health statistics over live readings, shared by every trend range."""
import math
from datetime import timezone, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo('Asia/Shanghai')
METRICS = ('heartRate', 'spo2', 'bodyTemp')


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def positive(raw):
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


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


def build_trends(records, period, now, has_devices=True):
    hourly = period == 'day'
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    start = (now - timedelta(hours=24) if hourly else
             floor_time(now, False) - timedelta(days=6 if period == 'week' else 29))
    # Exact duplicate rows collapse; distinct frames keep their own sample.
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
    }
