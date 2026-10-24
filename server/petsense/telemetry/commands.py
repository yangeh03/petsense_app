"""设备控制路由：远程录音 / 实验开始停止 / 事件标注（发布到 MQTT 控制 topic）。"""

import json
import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, request

from petsense import config
from petsense.common import control_debounced
from petsense.experiments import recorder
from petsense.telemetry.mqtt_ingest import publish_control_command
from petsense.telemetry.realtime import broadcast_packet

bp = Blueprint('commands', __name__)

audio_recording_state = {'enabled': False, 'updated_at': None, 'recordId': None}
audio_stop_timer = None


def _now_str():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


@bp.route('/api/audio-recording', methods=['GET', 'POST'])
def api_audio_recording():
    global audio_stop_timer
    if request.method == 'GET':
        return audio_recording_state

    body = request.get_json(silent=True) or {}
    enabled = bool(body.get('enabled'))
    duration_sec = max(1, min(10, int(body.get('durationSec', 5) or 5)))
    if control_debounced(f'audio:{enabled}'):
        packet = dict(audio_recording_state)
        packet.update({
            'type': 'audio_recording_control',
            'durationSec': duration_sec,
            'topic': config.CONTROL_TOPIC,
            'published': False,
            'debounced': True
        })
        return packet
    now = _now_str()
    record_id = audio_recording_state.get('recordId')
    if enabled and not audio_recording_state.get('enabled'):
        record_id = f"web-{int(time.time()*1000)}"

    audio_recording_state.update({'enabled': enabled, 'updated_at': now, 'recordId': record_id})
    command = {
        'type': 'record_audio_start' if enabled else 'record_audio_stop',
        'recordId': record_id,
        'durationSec': duration_sec,
        'ts': now
    }
    payload, published = publish_control_command(command)

    if audio_stop_timer:
        audio_stop_timer.cancel()
        audio_stop_timer = None

    if enabled:
        def auto_stop_recording(expected_id):
            global audio_stop_timer
            stop_ts = _now_str()
            if audio_recording_state.get('recordId') != expected_id:
                return
            audio_recording_state.update({'enabled': False, 'updated_at': stop_ts, 'recordId': expected_id})
            stop_packet = {'type': 'audio_recording_control', 'enabled': False, 'recordId': expected_id, 'topic': config.CONTROL_TOPIC, 'published': True, 'updated_at': stop_ts}
            broadcast_packet(json.dumps(stop_packet))
            audio_stop_timer = None
        audio_stop_timer = threading.Timer(duration_sec + 0.5, auto_stop_recording, args=(record_id,))
        audio_stop_timer.daemon = True
        audio_stop_timer.start()

    packet = {
        'type': 'audio_recording_control',
        'enabled': enabled,
        'recordId': record_id,
        'durationSec': duration_sec,
        'topic': config.CONTROL_TOPIC,
        'published': published,
        'updated_at': now
    }
    broadcast_packet(json.dumps(packet))
    return packet


@bp.route('/api/experiment', methods=['GET', 'POST'])
def api_experiment():
    if request.method == 'GET':
        return recorder.experiment_state

    body = request.get_json(silent=True) or {}
    action = str(body.get('action') or '').lower()
    if control_debounced(f'experiment:{action}'):
        return {'type': 'experiment_control', 'state': recorder.experiment_state, 'published': False, 'debounced': True}
    now = _now_str()

    if action == 'start':
        exp_id = recorder.safe_experiment_id(body.get('experimentId') or datetime.now(timezone.utc).strftime('exp-%Y%m%d-%H%M%S'))
        exp_id, folder = recorder.experiment_dir(exp_id)
        recorder.experiment_state.update({'active': True, 'experimentId': exp_id, 'started_at': now, 'stopped_at': None, 'folder': folder})
        with open(f'{folder}/server_manifest.json', 'w', encoding='utf-8') as f:
            json.dump({'schema_version': 1, 'experiment_id': exp_id, 'started_at': now, 'folder': folder, 'control_topic': config.CONTROL_TOPIC, 'events': [{'event_id': e, 'label': l} for e, l in recorder.EVENT_LABELS]}, f, ensure_ascii=False, indent=2)
        command = {'type': 'experiment_start', 'experimentId': exp_id, 'id': exp_id, 'ts': now}
    elif action == 'stop':
        exp_id = recorder.experiment_state.get('experimentId')
        recorder.experiment_state.update({'active': False, 'stopped_at': now})
        recorder.event_state.update({'active': False, 'stopped_at': now})
        command = {'type': 'experiment_stop', 'experimentId': exp_id, 'id': exp_id, 'ts': now}
        folder = recorder.experiment_state.get('folder')
        if folder:
            with open(f'{folder}/server_events.jsonl', 'a', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'experiment_stop', 'ts': now}, ensure_ascii=False) + '\n')
    else:
        return {'success': False, 'error': 'action must be start or stop'}, 400

    payload, published = publish_control_command(command)
    packet = {'type': 'experiment_control', 'state': recorder.experiment_state, 'command': command, 'published': published, 'updated_at': now}
    broadcast_packet(json.dumps(packet))
    return packet


@bp.route('/api/experiment-event', methods=['GET', 'POST'])
def api_experiment_event():
    if request.method == 'GET':
        return {'state': recorder.event_state, 'events': [{'eventId': e, 'label': l} for e, l in recorder.EVENT_LABELS]}

    body = request.get_json(silent=True) or {}
    action = str(body.get('action') or '').lower()
    raw_event_id = body.get('eventId')
    event_id = recorder.safe_event_id(raw_event_id)
    now = _now_str()

    if not recorder.experiment_state.get('active') or not recorder.experiment_state.get('folder'):
        return {'success': False, 'error': 'experiment is not active', 'state': recorder.event_state}, 400
    if control_debounced('event:any'):
        return {'type': 'event_control', 'state': recorder.event_state, 'published': False, 'debounced': True}
    if control_debounced(f'event:{action}:{event_id}'):
        return {'type': 'event_control', 'state': recorder.event_state, 'published': False, 'debounced': True}

    if action == 'toggle':
        action = 'stop' if recorder.event_state.get('active') and recorder.event_state.get('eventId') == event_id else 'start'

    if action == 'start':
        if event_id not in recorder.EVENT_LABEL_BY_ID:
            return {'success': False, 'error': 'unknown eventId', 'events': [{'eventId': e, 'label': l} for e, l in recorder.EVENT_LABELS]}, 400
        if recorder.event_state.get('active') and recorder.event_state.get('eventId') == event_id:
            packet = {'type': 'event_control', 'state': recorder.event_state, 'published': False, 'unchanged': True, 'updated_at': now}
            broadcast_packet(json.dumps(packet, ensure_ascii=False))
            return packet
        if recorder.event_state.get('active') and recorder.event_state.get('eventId') != event_id:
            with open(f"{recorder.experiment_state['folder']}/server_events.jsonl", 'a', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'event_stop', 'eventId': recorder.event_state.get('eventId'), 'segmentId': recorder.event_state.get('segmentId'), 'ts': now}, ensure_ascii=False) + '\n')
        label = recorder.EVENT_LABEL_BY_ID[event_id]
        _, efolder = recorder.event_folder_for(recorder.experiment_state['folder'], event_id)
        segment_id = datetime.now(timezone.utc).strftime('seg_%Y%m%d-%H%M%S')
        recorder.event_state.update({'active': True, 'eventId': event_id, 'label': label, 'segmentId': segment_id, 'started_at': now, 'stopped_at': None, 'folder': efolder})
        command = {'type': 'event_start', 'experimentId': recorder.experiment_state.get('experimentId'), 'eventId': event_id, 'label': label, 'id': recorder.experiment_state.get('experimentId'), 'e': event_id, 'l': label, 'segmentId': segment_id, 'ts': now}
    elif action == 'stop':
        command = {'type': 'event_stop', 'experimentId': recorder.experiment_state.get('experimentId'), 'eventId': recorder.event_state.get('eventId'), 'id': recorder.experiment_state.get('experimentId'), 'e': recorder.event_state.get('eventId'), 'segmentId': recorder.event_state.get('segmentId'), 'ts': now}
        recorder.event_state.update({'active': False, 'stopped_at': now})
    else:
        return {'success': False, 'error': 'action must be start, stop, or toggle'}, 400

    with open(f"{recorder.experiment_state['folder']}/server_events.jsonl", 'a', encoding='utf-8') as f:
        f.write(json.dumps(command, ensure_ascii=False) + '\n')

    payload, published = publish_control_command(command)
    packet = {'type': 'event_control', 'state': recorder.event_state, 'command': command, 'published': published, 'updated_at': now}
    broadcast_packet(json.dumps(packet, ensure_ascii=False))
    return packet
