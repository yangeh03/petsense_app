"""Versioned observation coverage, independent of model inference or App lifetime."""
import json
from zoneinfo import ZoneInfo
from sqlalchemy import select
from petsense.db.models import Pet, TelemetryMinute
from petsense.db import iso_z
from petsense.db.models.agent import AgentLearningDay


def record_interval(session, owner, device, feature, version, moment, seconds):
    if not isinstance(version, str) or not version.strip() or len(version) > 80:
        return
    pet_id = session.execute(select(Pet.id).where(Pet.user_id == owner, Pet.device_id == device)).scalar_one_or_none()
    if pet_id is None:
        return
    local = moment.astimezone(ZoneInfo('Asia/Shanghai'))
    key = (pet_id, device, feature, version, str(local.date()))
    row = session.get(AgentLearningDay, key)
    if row is None:
        row = AgentLearningDay(pet_id=pet_id, device_id=device, feature=feature, version=version,
                               day=str(local.date()), seconds=0, slots='[]', last_seen_at=moment)
        session.add(row)
    row.last_seen_at = moment
    minute = session.get(TelemetryMinute, (owner, device, moment.replace(second=0, microsecond=0)))
    if minute is not None:
        minute.source_versions = json.dumps(sorted(set(json.loads(minute.source_versions)) | {feature + ':' + version}))
    row.seconds += seconds
    row.slots = json.dumps(sorted(set(json.loads(row.slots)) | {local.hour // 6}))


def progress(session, pet):
    rows = session.execute(select(AgentLearningDay).where(AgentLearningDay.pet_id == pet.id,
        AgentLearningDay.device_id == pet.device_id)).scalars().all()
    versions = {}
    for row in rows:
        value = versions.setdefault((row.feature, row.version), {'days': 0, 'seconds': 0, 'last': row.last_seen_at})
        value['seconds'] += row.seconds
        value['last'] = max(value['last'], row.last_seen_at)
        if row.seconds >= 21600 and len(json.loads(row.slots)) >= 2:
            value['days'] += 1
    return [{'dimension': feature, 'version': version, 'validDays': value['days'],
             'lastObservedAt': iso_z(value['last']), 'observedSeconds': value['seconds'], 'requiredDays': 7, 'state': 'learning'}
            for (feature, version), value in sorted(versions.items(), key=lambda item: item[1]['last'], reverse=True)]
