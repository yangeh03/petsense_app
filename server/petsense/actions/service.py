"""动作识别装配：加载随机森林模型、帧 enrich、预测持久化与查询路由。"""

import json
import os
from datetime import datetime

from flask import Blueprint, request
from sqlalchemy import insert, select

from petsense import config
from petsense.actions.classifier import ActionClassifier
from petsense.auth.routes import require_app_user
from petsense.db import iso_z, session_scope
from petsense.db.models import ActionPrediction

bp = Blueprint('actions', __name__, url_prefix='/api/action')

action_classifier = None
action_classifier_error = None
if not os.path.isfile(config.ACTION_MODEL_PATH):
    action_classifier_error = f'模型文件不存在：{config.ACTION_MODEL_PATH}'
else:
    try:
        action_classifier = ActionClassifier(config.ACTION_MODEL_PATH)
        print(f'[ACTION] loaded {config.ACTION_MODEL_PATH}')
    except Exception as error:
        action_classifier_error = f'模型加载失败：{error}'
if action_classifier_error:
    print(f'[ACTION] disabled: {action_classifier_error}')


def action_status_payload():
    if action_classifier:
        return action_classifier.status()
    return {
        'enabled': False,
        'source': 'random_forest_1s',
        'modelVersion': '2026-10-14-device-clock-v1',
        'error': action_classifier_error or '分类器未启用',
    }


def persist_action_prediction(prediction):
    predicted_at = prediction['predictedAt']
    if isinstance(predicted_at, str):
        predicted_at = datetime.fromisoformat(predicted_at.replace('Z', '+00:00'))
    with session_scope() as s:
        s.execute(insert(ActionPrediction).values(
            device_id=prediction['deviceId'], event_id=prediction['eventId'],
            label=prediction['label'], action_key=prediction['actionKey'],
            confidence=prediction['confidence'],
            scores_json=json.dumps(prediction['scores'], ensure_ascii=False),
            sample_count=prediction['sampleCount'],
            window_start_ts_ms=prediction['windowStartTsMs'],
            window_end_ts_ms=prediction['windowEndTsMs'],
            model_version=prediction['modelVersion'], predicted_at=predicted_at,
        ))


def enrich_frame_with_action(frame):
    if not action_classifier or not isinstance(frame, dict):
        return frame
    try:
        prediction, is_new = action_classifier.ingest(frame)
        if prediction:
            frame['action'] = prediction
        if is_new:
            persist_action_prediction(prediction)
    except Exception as error:
        print(f'[ACTION] inference error: {error}')
    return frame


def action_rows(limit=100, device_id=None, device_ids=None):
    stmt = select(ActionPrediction).order_by(ActionPrediction.id.desc()).limit(limit)
    if device_id:
        stmt = stmt.where(ActionPrediction.device_id == device_id)
    elif device_ids is not None:
        if not device_ids:
            return []
        stmt = stmt.where(ActionPrediction.device_id.in_(device_ids))
    with session_scope() as s:
        rows = s.execute(stmt).scalars().all()
    return [{
        'deviceId': row.device_id, 'eventId': row.event_id, 'label': row.label, 'actionKey': row.action_key,
        'confidence': row.confidence, 'scores': json.loads(row.scores_json), 'sampleCount': row.sample_count,
        'windowStartTsMs': row.window_start_ts_ms, 'windowEndTsMs': row.window_end_ts_ms,
        'modelVersion': row.model_version, 'predictedAt': iso_z(row.predicted_at),
        'source': 'random_forest_1s',
    } for row in rows]


@bp.route('/status')
def api_action_status():
    # 部署健康检查依赖本接口（enabled 字段），保持匿名；不再暴露 latest 数据帧
    return action_status_payload()


@bp.route('/latest')
def api_action_latest():
    user, error = require_app_user()
    if error:
        return error
    from petsense.telemetry.realtime import device_ids_for_user
    devices = device_ids_for_user(user['id'])
    device_id = request.args.get('device_id')
    if device_id and device_id not in devices:
        return {'data': None}
    latest = action_classifier.latest(device_id) if action_classifier else None
    if not latest:
        rows = action_rows(1, device_id, device_ids=None if device_id else devices)
        latest = rows[0] if rows else None
    return {'data': latest}


@bp.route('/history')
def api_action_history():
    user, error = require_app_user()
    if error:
        return error
    from petsense.telemetry.realtime import device_ids_for_user
    devices = device_ids_for_user(user['id'])
    try:
        limit = max(1, min(1000, int(request.args.get('limit', 300))))
    except (TypeError, ValueError):
        limit = 300
    rows = action_rows(limit, request.args.get('device_id'),
                       device_ids=None if request.args.get('device_id') else devices)
    rows.reverse()
    return {'count': len(rows), 'data': rows}
