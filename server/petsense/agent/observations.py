"""Bounded, owner-scoped historical facts for the companion."""
import json
from datetime import timedelta
from sqlalchemy import case, func, select
from petsense.db import iso_z, utc_now
from petsense.db.models import TelemetryMinute, TelemetryEvent, TelemetryLatest
from petsense.agent.learning import progress
from petsense.telemetry.observations import _combine, _public_metrics, prediction, METRICS, number


def query(session, pet, start, end):
    if end <= start or end - start > timedelta(days=90):
        raise ValueError('时间范围应在90天以内')
    rows = session.execute(select(TelemetryMinute).where(
        TelemetryMinute.owner_id == pet.user_id, TelemetryMinute.device_id == pet.device_id,
        TelemetryMinute.minute >= start, TelemetryMinute.minute < end)
        .order_by(TelemetryMinute.minute)).scalars().all()
    latest_row = session.get(TelemetryLatest, pet.device_id)
    latest = None
    if latest_row and latest_row.owner_id == pet.user_id and start <= latest_row.received_at < end:
        payload = json.loads(latest_row.payload)
        health = payload.get('health') or {}
        if not isinstance(health, dict):
            health = {}
        latest = {'receivedAt': iso_z(latest_row.received_at),
            'ageSeconds': max(0, (utc_now() - latest_row.received_at).total_seconds()),
            'health': {key: health.get(key) if health.get('valid') is True and number(health.get(key)) and health[key] > 0 else None for key in METRICS},
            'action': prediction(payload, 'action') if payload.get('source') == 'esp32_mlp' else None,
            'sound': prediction(payload, 'sound') if payload.get('source') == 'esp32_mlp' else None}
    actions, metrics = {}, {}
    for row in rows:
        _combine(metrics, json.loads(row.metrics))
        for key, value in json.loads(row.actions).items():
            actions[key] = actions.get(key, 0) + value
    episodes = session.execute(select(TelemetryEvent).where(
        TelemetryEvent.owner_id == pet.user_id, TelemetryEvent.device_id == pet.device_id,
        TelemetryEvent.started_at < end, TelemetryEvent.last_seen_at >= start)
        .order_by(TelemetryEvent.started_at).limit(200)).scalars().all()
    sound_count = session.execute(select(func.count()).select_from(TelemetryEvent).where(
        TelemetryEvent.owner_id == pet.user_id, TelemetryEvent.device_id == pet.device_id,
        TelemetryEvent.started_at < end, TelemetryEvent.last_seen_at >= start)).scalar_one()
    # 全量集合聚合：总数之外的分类统计不受 200 条明细截断影响，
    # “1171 条里实验性占多少”这类追问由后端算好，不让模型去数子集。
    breakdown = session.execute(
        select(TelemetryEvent.label, func.count(),
               func.sum(case((TelemetryEvent.experimental != 0, 1), else_=0))).where(
        TelemetryEvent.owner_id == pet.user_id, TelemetryEvent.device_id == pet.device_id,
        TelemetryEvent.started_at < end, TelemetryEvent.last_seen_at >= start)
        .group_by(TelemetryEvent.label).order_by(func.count().desc())).all()
    learning_dimensions = progress(session, pet)
    source_versions = sorted({version for row in rows for version in json.loads(row.source_versions)})
    return {'soundEventCount': sound_count, 'soundEventsTruncated': sound_count > len(episodes),
            'soundEventsReturned': len(episodes),
            'soundBreakdown': [{'label': label, 'count': count, 'experimental': int(experimental or 0)}
                               for label, count, experimental in breakdown],
            'latest': latest, 'sourceVersions': source_versions, 'learningDimensions': learning_dimensions, 'resolution': 'minute', 'metrics': _public_metrics(metrics),
            'soundEvents': [{'label': e.label, 'startedAt': iso_z(e.started_at),
                             'lastSeenAt': iso_z(e.last_seen_at), 'experimental': bool(e.experimental)} for e in episodes],
            'region': {'state': 'unavailable'}, 'startAt': iso_z(start), 'endAt': iso_z(end),
            'observedSeconds': sum(min(60, r.observed_seconds) for r in rows),
            'actionObservedSeconds': sum(actions.values()),
            'soundObservedSeconds': sum(min(60, r.sound_observed_seconds) for r in rows),
            'actionSeconds': actions, 'minuteCount': len(rows),
            'learning': {'validDays': next((d['validDays'] for d in learning_dimensions if d['dimension'] == 'actions'), 0),
                         'requiredDays': 7, 'dimension': 'actions', 'state': 'learning'},
            'source': {'versions': source_versions, 'type': 'observations', 'deviceId': pet.device_id,
                       'startAt': iso_z(start), 'endAt': iso_z(end)}}
