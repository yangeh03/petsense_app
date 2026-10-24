"""动作识别装配：加载随机森林模型、帧 enrich、预测持久化与查询路由。"""

import json
import os
import sqlite3

from flask import Blueprint, request

from petsense import config, db
from petsense.actions.classifier import ActionClassifier

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
    db.cur.execute('''INSERT INTO action_predictions (
        device_id, event_id, label, action_key, confidence, scores_json, sample_count,
        window_start_ts_ms, window_end_ts_ms, model_version, predicted_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (
        prediction['deviceId'], prediction['eventId'], prediction['label'], prediction['actionKey'],
        prediction['confidence'], json.dumps(prediction['scores'], ensure_ascii=False), prediction['sampleCount'],
        prediction['windowStartTsMs'], prediction['windowEndTsMs'], prediction['modelVersion'], prediction['predictedAt'],
    ))
    db.conn.commit()


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


def action_rows(limit=100, device_id=None):
    query = '''SELECT device_id, event_id, label, action_key, confidence, scores_json,
        sample_count, window_start_ts_ms, window_end_ts_ms, model_version, predicted_at
        FROM action_predictions'''
    params = []
    if device_id:
        query += ' WHERE device_id=?'
        params.append(device_id)
    query += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    with sqlite3.connect(config.DB_PATH) as action_db:
        rows = action_db.execute(query, params).fetchall()
    return [{
        'deviceId': row[0], 'eventId': row[1], 'label': row[2], 'actionKey': row[3],
        'confidence': row[4], 'scores': json.loads(row[5]), 'sampleCount': row[6],
        'windowStartTsMs': row[7], 'windowEndTsMs': row[8], 'modelVersion': row[9],
        'predictedAt': row[10], 'source': 'random_forest_1s',
    } for row in rows]


@bp.route('/status')
def api_action_status():
    payload = action_status_payload()
    payload['latest'] = action_classifier.latest() if action_classifier else None
    return payload


@bp.route('/latest')
def api_action_latest():
    device_id = request.args.get('device_id')
    latest = action_classifier.latest(device_id) if action_classifier else None
    if not latest:
        rows = action_rows(1, device_id)
        latest = rows[0] if rows else None
    return {'data': latest}


@bp.route('/history')
def api_action_history():
    try:
        limit = max(1, min(1000, int(request.args.get('limit', 300))))
    except (TypeError, ValueError):
        limit = 300
    rows = action_rows(limit, request.args.get('device_id'))
    rows.reverse()
    return {'count': len(rows), 'data': rows}
