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
METRICS = ('heartRate', 'spo2', 'bodyTemp', 'envTemp')
# A minute only paints as walking when the classifier attributed enough of it to walking,
# so single-frame misreads do not colour the day timeline.
TIMELINE_WALK_MINIMUM_SECONDS = 10
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


def _minute(session, device, owner, moment):
    minute = moment.replace(second=0, microsecond=0)
    row = session.get(TelemetryMinute, (owner, device, minute))
    if row is None:
        row = TelemetryMinute(owner_id=owner, device_id=device, minute=minute, metrics='{}', actions='{}',
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
    binding = session.get(Device, device)
    if binding is None or received_at < binding.claimed_at:
        return False
    owner = binding.owner_id
    boot = str(payload.get('boot_id') or 'legacy')[:80]
    cursor = session.get(TelemetryLatest, device, with_for_update=True)
    if not payload.get('boot_id') and cursor and cursor.boot_id.startswith('legacy'):
        boot = cursor.boot_id
        receipt_gap = (received_at - cursor.received_at).total_seconds()
        # Older firmware has no boot identity. A large rollback to the first minute
        # starts a new epoch; reject subsequent buffered pre-reboot uptime jumps.
        if ordinal < cursor.ordinal and ((ordinal <= 60_000 and cursor.ordinal - ordinal >= 30_000) or receipt_gap >= 10):
            boot = f'legacy:{received_at.timestamp()}'
        elif ordinal - cursor.ordinal > max(0, receipt_gap) * 1000 + 10_000:
            return False
    previous = json.loads(cursor.payload) if cursor else None
    retired = json.loads(cursor.retired_boots) if cursor else []
    if cursor and (received_at <= cursor.received_at or boot in retired or
                   (boot == cursor.boot_id and ordinal <= cursor.ordinal)):
        return False
    same_boot = cursor is not None and boot == cursor.boot_id and cursor.owner_id == owner
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
            minute = _minute(session, device, owner, start)
            minute.observed_seconds += seconds
            if label in ACTIONS:
                actions = json.loads(minute.actions)
                actions[label] = actions.get(label, 0) + seconds
                minute.actions = json.dumps(actions)
            start = end
    minute = _minute(session, device, owner, received_at)
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
            TelemetryEvent.device_id == device, TelemetryEvent.owner_id == owner).order_by(TelemetryEvent.id.desc()).limit(1)).scalar_one_or_none()
        continuation = (episode is not None and episode.label == label and episode.boot_id == boot and
                        episode.model_version == model and
                        (received_at - episode.last_seen_at).total_seconds() <= GAP_SECONDS)
        if (episode is None or episode.label != label or episode.boot_id != boot or
                episode.model_version != model or
                (received_at - episode.last_seen_at).total_seconds() > GAP_SECONDS):
            episode = TelemetryEvent(owner_id=owner, device_id=device, boot_id=boot, model_version=model, label=label,
                                     started_at=received_at, last_seen_at=received_at, frames=0,
                                     confidence_sum=0, observed_seconds=0, experimental=0)
            session.add(episode)
        previous_sound = prediction(previous, 'sound') if previous else None
        if continuation and valid_gap and previous_sound and previous_sound['label'] == label:
            episode.observed_seconds += gap
        episode.frames += 1
        episode.confidence_sum += sound['confidence']
        episode.experimental = int(bool(episode.experimental or sound.get('experimental') or label == 'whine'))
        episode.last_seen_at = received_at
    if cursor is None:
        cursor = TelemetryLatest(device_id=device)
        session.add(cursor)
    elif boot != cursor.boot_id:
        retired.append(cursor.boot_id)
    cursor.owner_id = owner
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
        window_start = (now - timedelta(hours=24)).replace(second=0, microsecond=0)
        start = window_start.replace(minute=0)
    else:
        step = timedelta(days=1)
        end = today + step
        start = end - timedelta(days=7 if period == 'week' else 30)
        window_start = start
    buckets = []
    at = start
    while at < end:
        local = at.astimezone(SHANGHAI)
        buckets.append({'startAt': iso_z(at), 'label': local.strftime('%H:%M' if period == 'day' else '%m/%d'),
                        'metrics': {}, 'observedSeconds': 0})
        at += step
    with session_scope() as session:
        devices = session.execute(select(Device.device_id).where(Device.owner_id == user['id'])).scalars().all()
        latest = session.execute(select(TelemetryLatest).where(TelemetryLatest.device_id.in_(devices), TelemetryLatest.owner_id == user['id'])
                                 .order_by(TelemetryLatest.received_at.desc()).limit(1)).scalar_one_or_none()
        rows = session.execute(select(TelemetryMinute).where(TelemetryMinute.device_id.in_(devices), TelemetryMinute.owner_id == user['id'],
                               TelemetryMinute.minute >= min(window_start, today), TelemetryMinute.minute <= now)).scalars().all()
        episodes = session.execute(select(TelemetryEvent).where(TelemetryEvent.device_id.in_(devices), TelemetryEvent.owner_id == user['id'],
                                   TelemetryEvent.last_seen_at >= today, TelemetryEvent.last_seen_at <= now)
                                   .order_by(TelemetryEvent.last_seen_at.desc())).scalars().all()
        summary = {'observedSeconds': 0, 'walkingSeconds': 0, 'stationarySeconds': 0,
                   'soundEpisodeCount': len(episodes), 'firstObservedAt': None}
        combined = {}
        by_minute = {}
        for row in rows:
            if row.minute >= window_start:
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
                by_minute[row.minute] = ('walk' if actions.get('walking', 0) >= TIMELINE_WALK_MINIMUM_SECONDS
                                         else 'idle' if row.observed_seconds > 0 else 'none')
        for bucket in buckets:
            bucket['metrics'] = _public_metrics(bucket['metrics'])
        events = [{'id': e.id, 'label': e.label, 'startedAt': iso_z(e.started_at),
                   'lastSeenAt': iso_z(e.last_seen_at), 'frames': e.frames,
                   'confidence': round(e.confidence_sum / e.frames, 4),
                   'observedSeconds': e.observed_seconds, 'experimental': bool(e.experimental)} for e in episodes[:20]]
        # Day timeline: merged per-minute runs of walk / idle / no data, for the honest day bar.
        timeline = None
        if period == 'day':
            runs = []
            cursor = today
            while cursor <= now:
                state = by_minute.get(cursor, 'none')
                if runs and runs[-1]['state'] == state:
                    runs[-1]['minutes'] += 1
                else:
                    runs.append({'state': state, 'minutes': 1})
                cursor += timedelta(minutes=1)
            timeline = {'startAt': iso_z(today), 'slotSeconds': 60, 'runs': runs}
        result = {'deviceId': devices[0] if devices else None, 'period': period, 'latest': {'payload': json.loads(latest.payload), 'receivedAt': iso_z(latest.received_at)} if latest else None,
                  'summary': summary, 'events': events, 'metrics': _public_metrics(combined), 'buckets': buckets,
                  'range': {'startAt': iso_z(window_start), 'endAt': iso_z(now)}, 'retentionDays': RETENTION_DAYS,
                  'timeline': timeline}
    return result, 200, {'Cache-Control': 'no-store'}
