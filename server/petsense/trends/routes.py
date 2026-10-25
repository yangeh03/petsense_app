"""Trend API: raw statistics over owned-device live readings."""
import json
from datetime import timedelta

from flask import Blueprint, request
from sqlalchemy import select

from petsense.auth.routes import require_app_user
from petsense.db import session_scope, utc_now
from petsense.db.models import Reading
from petsense.telemetry.realtime import device_ids_for_user
from petsense.trends.aggregation import build_trends, floor_time, positive

bp = Blueprint('trends', __name__, url_prefix='/api/app')


@bp.route('/trends')
def app_trends():
    user, error = require_app_user()
    if error:
        return error
    period = request.args.get('period', 'week')
    if period not in ('day', 'week', 'month', 'all'):
        return {'success': False, 'message': '不支持的趋势时间范围'}, 400
    now = utc_now()
    devices = device_ids_for_user(user['id'])
    live = []
    cutoff = (now - timedelta(hours=24) if period == 'day' else
              floor_time(now, False) - timedelta(days=6 if period == 'week' else 29))
    if devices:
        query = select(Reading.device_id, Reading.payload, Reading.received_at).where(
            Reading.device_id.in_(devices), Reading.received_at <= now)
        if period != 'all':
            query = query.where(Reading.received_at >= cutoff)
        with session_scope() as s:
            rows = s.execute(query).all()
        for device, payload, received in rows:
            try:
                health = json.loads(payload).get('health')
                if not isinstance(health, dict) or health.get('valid') is not True:
                    continue
                values = tuple(positive(health.get(key)) for key in ('heartRate', 'spo2', 'bodyTemp'))
                if any(value is not None for value in values):
                    live.append((device, received, values))
            except (TypeError, ValueError, AttributeError):
                continue
    return build_trends(live, period, now, bool(devices))
