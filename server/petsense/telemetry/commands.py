"""设备控制路由：远程录音 / 实验开始停止 / 事件标注（发布到设备控制 topic）。

全部需要登录；目标设备 = 当前用户名下设备（一人一设备）。状态 per-device。
"""

import json
import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, request
from sqlalchemy import select

from petsense.auth.routes import require_app_user
from petsense.common import control_debounced
from petsense.db import session_scope, utc_now
from petsense.db.models import Device
from petsense.experiments import recorder
from petsense.telemetry.mqtt_ingest import publish_control_command
from petsense.telemetry.realtime import broadcast_packet

bp = Blueprint('commands', __name__)

audio_recording_states = {}   # device_id -> state
audio_stop_timers = {}       # device_id -> threading.Timer


def _now_str():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def my_device_id(user):
    """当前用户名下设备；未绑定返回 None。"""
    with session_scope() as s:
        return s.execute(select(Device.device_id)
                         .where(Device.owner_id == user['id'])).scalar_one_or_none()


@bp.route('/api/audio-recording', methods=['GET', 'POST'])
def api_audio_recording():
    user, error = require_app_user()
    if error:
        return error
    device_id = my_device_id(user)
    if not device_id:
        return {'success': False, 'message': '请先绑定项圈'}, 400
    state = audio_recording_states.setdefault(
        device_id, {'enabled': False, 'updated_at': None, 'recordId': None})
    if request.method == 'GET':
        return state

    body = request.get_json(silent=True) or {}
    enabled = bool(body.get('enabled'))
    duration_sec = max(1, min(10, int(body.get('durationSec', 5) or 5)))
    control_topic = f'petsense/device/{device_id}/command'
    if control_debounced(f'audio:{device_id}:{enabled}'):
        packet = dict(state)
        packet.update({
            'type': 'audio_recording_control',
            'durationSec': duration_sec,
            'topic': control_topic,
            'published': False,
            'debounced': True
        })
        return packet
    now = _now_str()
    record_id = state.get('recordId')
    if enabled and not state.get('enabled'):
        record_id = f"web-{int(time.time()*1000)}"

    state.update({'enabled': enabled, 'updated_at': now, 'recordId': record_id})
    command = {
        'type': 'record_audio_start' if enabled else 'record_audio_stop',
        'recordId': record_id,
        'durationSec': duration_sec,
        'ts': now
    }
    payload, published = publish_control_command(device_id, command)

    timer = audio_stop_timers.pop(device_id, None)
    if timer:
        timer.cancel()

    if enabled:
        def auto_stop_recording(expected_id, dev=device_id):
            stop_ts = _now_str()
            st = audio_recording_states.get(dev)
            if not st or st.get('recordId') != expected_id:
                audio_stop_timers.pop(dev, None)
                return
            st.update({'enabled': False, 'updated_at': stop_ts, 'recordId': expected_id})
            stop_packet = {'type': 'audio_recording_control', 'enabled': False, 'recordId': expected_id,
                           'topic': f'petsense/device/{dev}/command', 'published': True, 'updated_at': stop_ts}
            broadcast_packet(json.dumps(stop_packet), dev)
            audio_stop_timers.pop(dev, None)
        timer = threading.Timer(duration_sec + 0.5, auto_stop_recording, args=(record_id,))
        timer.daemon = True
        timer.start()
        audio_stop_timers[device_id] = timer

    packet = {
        'type': 'audio_recording_control',
        'enabled': enabled,
        'recordId': record_id,
        'durationSec': duration_sec,
        'topic': control_topic,
        'published': published,
        'updated_at': now
    }
    broadcast_packet(json.dumps(packet), device_id)
    return packet


@bp.route('/api/experiment', methods=['GET', 'POST'])
def api_experiment():
    user, error = require_app_user()
    if error:
        return error
    device_id = my_device_id(user)
    if not device_id:
        return {'success': False, 'message': '请先绑定项圈'}, 400
    experiment_state = recorder.experiment_state_for(device_id)

    if request.method == 'GET':
        return experiment_state

    body = request.get_json(silent=True) or {}
    action = str(body.get('action') or '').lower()
    if control_debounced(f'experiment:{device_id}:{action}'):
        return {'type': 'experiment_control', 'state': experiment_state, 'published': False, 'debounced': True}
    now = _now_str()

    if action == 'start':
        exp_id = recorder.safe_experiment_id(body.get('experimentId') or datetime.now(timezone.utc).strftime('exp-%Y%m%d-%H%M%S'))
        exp_id, folder = recorder.experiment_dir(exp_id)
        experiment_state.update({'active': True, 'experimentId': exp_id, 'started_at': now, 'stopped_at': None, 'folder': folder})
        with open(f'{folder}/server_manifest.json', 'w', encoding='utf-8') as f:
            json.dump({'schema_version': 1, 'experiment_id': exp_id, 'device_id': device_id, 'started_at': now, 'folder': folder,
                       'control_topic': f'petsense/device/{device_id}/command',
                       'events': [{'event_id': e, 'label': l} for e, l in recorder.EVENT_LABELS]},
                      f, ensure_ascii=False, indent=2)
        command = {'type': 'experiment_start', 'experimentId': exp_id, 'id': exp_id, 'ts': now}
    elif action == 'stop':
        exp_id = experiment_state.get('experimentId')
        experiment_state.update({'active': False, 'stopped_at': now})
        recorder.event_state_for(device_id).update({'active': False, 'stopped_at': now})
        command = {'type': 'experiment_stop', 'experimentId': exp_id, 'id': exp_id, 'ts': now}
        folder = experiment_state.get('folder')
        if folder:
            with open(f'{folder}/server_events.jsonl', 'a', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'experiment_stop', 'ts': now}, ensure_ascii=False) + '\n')
    else:
        return {'success': False, 'error': 'action must be start or stop'}, 400

    payload, published = publish_control_command(device_id, command)
    packet = {'type': 'experiment_control', 'state': experiment_state, 'command': command, 'published': published, 'updated_at': now}
    broadcast_packet(json.dumps(packet), device_id)
    return packet


@bp.route('/api/experiment-event', methods=['GET', 'POST'])
def api_experiment_event():
    user, error = require_app_user()
    if error:
        return error
    device_id = my_device_id(user)
    if not device_id:
        return {'success': False, 'message': '请先绑定项圈'}, 400
    experiment_state = recorder.experiment_state_for(device_id)
    event_state = recorder.event_state_for(device_id)

    if request.method == 'GET':
        return {'state': event_state, 'events': [{'eventId': e, 'label': l} for e, l in recorder.EVENT_LABELS]}

    body = request.get_json(silent=True) or {}
    action = str(body.get('action') or '').lower()
    raw_event_id = body.get('eventId')
    event_id = recorder.safe_event_id(raw_event_id)
    now = _now_str()

    if not experiment_state.get('active') or not experiment_state.get('folder'):
        return {'success': False, 'error': 'experiment is not active', 'state': event_state}, 400
    if control_debounced(f'event:{device_id}:any'):
        return {'type': 'event_control', 'state': event_state, 'published': False, 'debounced': True}
    if control_debounced(f'event:{device_id}:{action}:{event_id}'):
        return {'type': 'event_control', 'state': event_state, 'published': False, 'debounced': True}

    if action == 'toggle':
        action = 'stop' if event_state.get('active') and event_state.get('eventId') == event_id else 'start'

    if action == 'start':
        if event_id not in recorder.EVENT_LABEL_BY_ID:
            return {'success': False, 'error': 'unknown eventId', 'events': [{'eventId': e, 'label': l} for e, l in recorder.EVENT_LABELS]}, 400
        if event_state.get('active') and event_state.get('eventId') == event_id:
            packet = {'type': 'event_control', 'state': event_state, 'published': False, 'unchanged': True, 'updated_at': now}
            broadcast_packet(json.dumps(packet, ensure_ascii=False), device_id)
            return packet
        if event_state.get('active') and event_state.get('eventId') != event_id:
            with open(f"{experiment_state['folder']}/server_events.jsonl", 'a', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'event_stop', 'eventId': event_state.get('eventId'), 'segmentId': event_state.get('segmentId'), 'ts': now}, ensure_ascii=False) + '\n')
        label = recorder.EVENT_LABEL_BY_ID[event_id]
        _, efolder = recorder.event_folder_for(experiment_state['folder'], event_id)
        segment_id = datetime.now(timezone.utc).strftime('seg_%Y%m%d-%H%M%S')
        event_state.update({'active': True, 'eventId': event_id, 'label': label, 'segmentId': segment_id, 'started_at': now, 'stopped_at': None, 'folder': efolder})
        command = {'type': 'event_start', 'experimentId': experiment_state.get('experimentId'), 'eventId': event_id, 'label': label, 'id': experiment_state.get('experimentId'), 'e': event_id, 'l': label, 'segmentId': segment_id, 'ts': now}
    elif action == 'stop':
        command = {'type': 'event_stop', 'experimentId': experiment_state.get('experimentId'), 'eventId': event_state.get('eventId'), 'id': experiment_state.get('experimentId'), 'e': event_state.get('eventId'), 'segmentId': event_state.get('segmentId'), 'ts': now}
        event_state.update({'active': False, 'stopped_at': now})
    else:
        return {'success': False, 'error': 'action must be start, stop, or toggle'}, 400

    with open(f"{experiment_state['folder']}/server_events.jsonl", 'a', encoding='utf-8') as f:
        f.write(json.dumps(command, ensure_ascii=False) + '\n')

    payload, published = publish_control_command(device_id, command)
    packet = {'type': 'event_control', 'state': event_state, 'command': command, 'published': published, 'updated_at': now}
    broadcast_packet(json.dumps(packet, ensure_ascii=False), device_id)
    return packet
