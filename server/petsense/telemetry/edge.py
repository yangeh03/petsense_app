"""Owner-scoped access to compact on-device inference; never reclassify it."""
import json
import math
import re
from pathlib import Path

from flask import Blueprint, jsonify, send_from_directory
from sqlalchemy import select

from petsense.auth.routes import require_app_user
from petsense.db import session_scope, utc_now
from petsense.db.models import Device, Reading

bp = Blueprint('edge', __name__)
TOPIC = 'petsense/sensor/edge_v1'


def validate(payload):
    if not isinstance(payload, dict) or payload.get('source') != 'esp32_mlp':
        raise ValueError('Invalid edge source')
    device = payload.get('deviceId')
    if not isinstance(device, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', device):
        raise ValueError('Invalid deviceId')
    for key in ('action', 'sound'):
        value = payload.get(key)
        if not isinstance(value, dict) or not isinstance(value.get('label'), str):
            raise ValueError('Missing prediction')
        score = value.get('confidence')
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('Invalid confidence')
    if not isinstance(payload.get('health'), dict):
        raise ValueError('Missing health')
    json.dumps(payload, allow_nan=False)
    return device


@bp.get('/api/app/edge/latest')
def latest():
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as session:
        row = session.execute(
            select(Reading.payload, Reading.received_at)
            .join(Device, Reading.device_id == Device.device_id)
            .where(Device.owner_id == user['id'], Reading.topic == TOPIC)
            .order_by(Reading.id.desc()).limit(1)
        ).first()
    age = max(0, (utc_now() - row.received_at).total_seconds()) if row else None
    response = jsonify(online=age is not None and age < 10,
                       age_s=round(age, 2) if age is not None else None,
                       payload=json.loads(row.payload) if row else None)
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.get('/edge')
@bp.get('/edge/')
def dashboard():
    return send_from_directory(str(Path(__file__).parent), 'edge_dashboard.html')
