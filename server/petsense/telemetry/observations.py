"""Hardware observations → durable minute statistics and sound episodes, without reclassification."""
import json
import math
from datetime import timedelta, timezone

from flask import Blueprint, request
from sqlalchemy import delete, select

from petsense.auth.routes import require_app_user
from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models import Device, Reading, TelemetryLatest, TelemetryMinute, TelemetryEvent

bp = Blueprint('observations', __name__, url_prefix='/api/app')
METRICS = ('heartRate', 'spo2', 'bodyTemp')
ACTIONS = {'standing', 'sitting', 'lying down', 'walking'}
SOUNDS = {'bark', 'howl', 'whine'}
RETENTION_DAYS = 90
GAP_SECONDS = 3
SHANGHAI = timezone(timedelta(hours=8))


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def prediction(payload, kind):
    value = payload.get(kind)
    if not isinstance(value, dict) or not number(value.get('confidence')):
        return None
    return value if .6 <= value['confidence'] <= 1 else None


def _minute(session, device, moment):
    minute = moment.replace(second=0, microsecond=0)
    row = session.get(TelemetryMinute, (device, minute))
    if row is None:
        row = TelemetryMinute(device_id=device, minute=minute, metrics='{}', actions='{}',
                              observed_seconds=0, frames=0)
        session.add(row)
        session.flush()
    return row


def ingest_observation(session, payload, received_at):
    """Run inside the raw Reading transaction; duplicate/late deliveries are no-ops."""
    device = payload.get('deviceId')
    ordinal = payload.get('seq', payload.get('ts_ms'))
    if not isinstance(device, str) or not number(ordinal) or ordinal < 0:
        return False
    boot = str(payload.get('boot_id') or 'legacy')[:80]
    cursor = session.get(TelemetryLatest, device, with_for_update=True)
    previous = json.loads(cursor.payload) if cursor else None
    retired = json.loads(cursor.retired_boots) if cursor else []
    if cursor and (received_at <= cursor.received_at or boot in retired or
                   (boot == cursor.boot_id and ordinal <= cursor.ordinal)):
        return False
    same_boot = cursor is not None and boot == cursor.boot_id
    gap = (received_at - cursor.received_at).total_seconds() if cursor else 0
    valid_gap = same_boot and 0 < gap <= GAP_SECONDS
    if valid_gap:
        previous_action = prediction(previous, 'action') if previous.get('source') == 'esp32_mlp' else None
        label = previous_action.get('label') if previous_action else None
        # Split short measured intervals at minute/day boundaries; never fill offline gaps.
        start = cursor.received_at
        while start < received_at:
            end = min(received_at, start.replace(second=0, microsecond=0) + timedelta(minutes=1))
            seconds = (end - start).total_seconds()
            minute = _minute(session, device, start)
            minute.observed_seconds += seconds
            if label in ACTIONS:
                actions = json.loads(minute.actions)
                actions[label] = actions.get(label, 0) + seconds
                minute.actions = json.dumps(actions)
            start = end
    minute = _minute(session, device, received_at)
    minute.frames += 1
    health = payload.get('health') or {}
    metrics = json.loads(minute.metrics)
    if isinstance(health, dict) and health.get('valid') is True:
        for key in METRICS:
            value = health.get(key)
            if not number(value) or value <= 0:
                continue
            entry = metrics.setdefault(key, {'sum': 0, 'count': 0, 'min': value, 'max': value})
            entry['sum'] += value
            entry['count'] += 1
            entry['min'] = min(entry['min'], value)
            entry['max'] = max(entry['max'], value)
    minute.metrics = json.dumps(metrics)
    sound = prediction(payload, 'sound') if payload.get('source') == 'esp32_mlp' else None
    if sound and sound.get('label') in SOUNDS:
        label = sound['label']
        model = str(payload.get('modelVersion') or '')[:80]
        episode = session.execute(select(TelemetryEvent).where(
            TelemetryEvent.device_id == device).order_by(TelemetryEvent.id.desc()).limit(1)).scalar_one_or_none()
        if (episode is None or episode.label != label or episode.boot_id != boot or
                episode.model_version != model or
                (received_at - episode.last_seen_at).total_seconds() > GAP_SECONDS):
            episode = TelemetryEvent(device_id=device, boot_id=boot, model_version=model, label=label,
                                     started_at=received_at, last_seen_at=received_at, frames=0,
                                     confidence_sum=0, observed_seconds=0, experimental=0)
            session.add(episode)
        if valid_gap and (previous.get('sound') or {}).get('label') == label:
            episode.observed_seconds += gap
        episode.frames += 1
        episode.confidence_sum += sound['confidence']
        episode.experimental = int(bool(episode.experimental or sound.get('experimental') or label == 'whine'))
        episode.last_seen_at = received_at
    if cursor is None:
        cursor = TelemetryLatest(device_id=device)
        session.add(cursor)
    elif not same_boot:
        retired.append(cursor.boot_id)
    cursor.boot_id = boot
    cursor.ordinal = int(ordinal)
    cursor.retired_boots = json.dumps(retired)
    # Raw payload was validated by ingress; tolerate nonfinite legacy values only in aggregates.
    cursor.payload = json.dumps(payload)
    cursor.received_at = received_at
    return True


def prune(session, now):
    cutoff = now - timedelta(days=RETENTION_DAYS)
    session.execute(delete(TelemetryMinute).where(TelemetryMinute.minute < cutoff))
    session.execute(delete(TelemetryEvent).where(TelemetryEvent.last_seen_at < cutoff))


def backfill():
    """At startup before MQTT subscription. Checkpoints make repeated runs harmless."""
    with session_scope() as session:
        rows = session.execute(select(Reading).where(Reading.device_id.is_not(None)).order_by(Reading.id)).scalars().all()
        for row in rows:
            try:
                payload = json.loads(row.payload)
                if isinstance(payload, dict) and payload.get('deviceId') == row.device_id:
                    ingest_observation(session, payload, row.received_at)
            except (TypeError, ValueError, OverflowError):
                continue
        prune(session, utc_now())


def _combine(target, source):
    for key, value in source.items():
        if key not in METRICS:
            continue
        entry = target.setdefault(key, {'sum': 0, 'count': 0, 'min': value['min'], 'max': value['max']})
        entry['sum'] += value['sum']
        entry['count'] += value['count']
        entry['min'] = min(entry['min'], value['min'])
        entry['max'] = max(entry['max'], value['max'])


def _public_metrics(metrics):
    return {key: {'avg': round(metrics[key]['sum'] / metrics[key]['count'], 2),
                  'min': metrics[key]['min'], 'max': metrics[key]['max'], 'count': metrics[key]['count']}
            if metrics.get(key, {}).get('count') else {'avg': None, 'min': None, 'max': None, 'count': 0}
            for key in METRICS}


@bp.get('/observations')
def observations():
    user, error = require_app_user()
    if error:
        return error
    period = request.args.get('period', 'day')
    if period not in ('day', 'week', 'month'):
        return {'message': '请选择24小时、7天或30天'}, 400
    now = utc_now()
    today = now.astimezone(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    if period == 'day':
        step = timedelta(hours=1)
        end = now.replace(minute=0, second=0, microsecond=0) + step
        start = end - timedelta(hours=24)
    else:
        step = timedelta(days=1)
        end = today + step
        start = end - timedelta(days=7 if period == 'week' else 30)
    buckets = []
    at = start
    while at < end:
        local = at.astimezone(SHANGHAI)
        buckets.append({'startAt': iso_z(at), 'label': local.strftime('%H:%M' if period == 'day' else '%m/%d'),
                        'metrics': {}, 'observedSeconds': 0})
        at += step
    with session_scope() as session:
        devices = session.execute(select(Device.device_id).where(Device.owner_id == user['id'])).scalars().all()
        latest = session.execute(select(TelemetryLatest).where(TelemetryLatest.device_id.in_(devices))
                                 .order_by(TelemetryLatest.received_at.desc()).limit(1)).scalar_one_or_none()
        rows = session.execute(select(TelemetryMinute).where(TelemetryMinute.device_id.in_(devices),
                               TelemetryMinute.minute >= min(start, today), TelemetryMinute.minute <= now)).scalars().all()
        episodes = session.execute(select(TelemetryEvent).where(TelemetryEvent.device_id.in_(devices),
                                   TelemetryEvent.last_seen_at >= today, TelemetryEvent.last_seen_at <= now)
                                   .order_by(TelemetryEvent.last_seen_at.desc())).scalars().all()
        summary = {'observedSeconds': 0, 'walkingSeconds': 0, 'stationarySeconds': 0,
                   'soundEpisodeCount': len(episodes), 'firstObservedAt': None}
        combined = {}
        for row in rows:
            if row.minute >= start:
                index = int((row.minute - start).total_seconds() / step.total_seconds())
                if 0 <= index < len(buckets):
                    _combine(buckets[index]['metrics'], json.loads(row.metrics))
                    buckets[index]['observedSeconds'] += row.observed_seconds
                    _combine(combined, json.loads(row.metrics))
            if row.minute >= today:
                summary['observedSeconds'] += row.observed_seconds
                actions = json.loads(row.actions)
                summary['walkingSeconds'] += actions.get('walking', 0)
                summary['stationarySeconds'] += sum(actions.get(label, 0) for label in ACTIONS - {'walking'})
                first = iso_z(row.minute)
                if summary['firstObservedAt'] is None or first < summary['firstObservedAt']:
                    summary['firstObservedAt'] = first
        for bucket in buckets:
            bucket['metrics'] = _public_metrics(bucket['metrics'])
        events = [{'id': e.id, 'label': e.label, 'startedAt': iso_z(e.started_at),
                   'lastSeenAt': iso_z(e.last_seen_at), 'frames': e.frames,
                   'confidence': round(e.confidence_sum / e.frames, 4),
                   'observedSeconds': e.observed_seconds, 'experimental': bool(e.experimental)} for e in episodes[:20]]
        result = {'latest': {'payload': json.loads(latest.payload), 'receivedAt': iso_z(latest.received_at)} if latest else None,
                  'summary': summary, 'events': events, 'metrics': _public_metrics(combined), 'buckets': buckets,
                  'range': {'startAt': iso_z(start), 'endAt': iso_z(now)}, 'retentionDays': RETENTION_DAYS}
    return result, 200, {'Cache-Control': 'no-store'}
