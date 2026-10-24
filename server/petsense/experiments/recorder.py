"""实验录制：cloud_data 目录落盘（sensor_frames.jsonl + 各类 CSV）、事件标注与文件列表。"""

import csv
import json
import os
import re
from datetime import datetime, timezone

from flask import Blueprint

from petsense import config

bp = Blueprint('experiments', __name__)

experiment_state = {'active': False, 'experimentId': None, 'started_at': None, 'stopped_at': None, 'folder': None}
event_state = {'active': False, 'eventId': None, 'label': None, 'segmentId': None, 'started_at': None, 'stopped_at': None, 'folder': None}

EVENT_LABELS = [
    ('01_standing', '站立'),
    ('02_sitting', '坐下'),
    ('03_resting', '趴下 / 休息'),
    ('04_sleeping', '睡觉'),
    ('05_walking', '走路'),
    ('06_running', '跑步'),
    ('07_eating', '吃东西'),
    ('08_rolling', '打滚'),
    ('09_toilet', '上厕所'),
    ('10_jumping', '跳跃'),
]
EVENT_LABEL_BY_ID = dict(EVENT_LABELS)


def safe_experiment_id(value):
    raw = str(value or '').strip()
    if not raw:
        raw = datetime.now(timezone.utc).strftime('exp-%Y%m%d-%H%M%S')
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', raw)[:80].strip('._-')
    return safe or datetime.now(timezone.utc).strftime('exp-%Y%m%d-%H%M%S')


def experiment_dir(experiment_id):
    exp_id = safe_experiment_id(experiment_id)
    path = os.path.join(config.CLOUD_DATA_DIR, exp_id)
    os.makedirs(path, exist_ok=True)
    return exp_id, path


def safe_event_id(value):
    raw = str(value or '').strip()
    if raw in EVENT_LABEL_BY_ID:
        return raw
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', raw)[:40].strip('._-')
    return safe if safe in EVENT_LABEL_BY_ID else safe


def event_folder_for(exp_folder, event_id):
    event_id = safe_event_id(event_id)
    folder = os.path.join(exp_folder, event_id)
    os.makedirs(folder, exist_ok=True)
    return event_id, folder


def append_csv_row(path, header, row):
    new_file = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(header)
        writer.writerow(row)


def append_cloud_experiment_frame(topic, payload_dict, received_at):
    if topic != 'petsense/sensor/esp32-001' or not isinstance(payload_dict, dict):
        return

    exp = payload_dict.get('experiment') or {}
    exp_id = exp.get('id') or experiment_state.get('experimentId')
    active = bool(exp.get('active')) or bool(experiment_state.get('active'))
    if not active or not exp_id:
        return

    exp_id, folder = experiment_dir(exp_id)
    manifest_path = os.path.join(folder, 'manifest.json')
    if not os.path.exists(manifest_path):
        manifest = {
            'schema_version': 1,
            'experiment_id': exp_id,
            'server_started_at': experiment_state.get('started_at'),
            'device_topic': topic,
            'files': {
                'sensor_frames': 'sensor_frames.jsonl',
                'audio_features': 'audio_features.csv',
                'imu': 'imu.csv',
                'health': 'health.csv',
                'battery': 'battery.csv',
                'event_folders': [event_id for event_id, _ in EVENT_LABELS]
            },
            'time_fields': {
                'received_at': 'server UTC receive time after cellular/MQTT delay',
                'payload.ts_ms': 'device millis timestamp'
            }
        }
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    with open(os.path.join(folder, 'sensor_frames.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps({'received_at': received_at, 'topic': topic, 'payload': payload_dict}, ensure_ascii=False, separators=(',', ':')) + '\n')

    audio = payload_dict.get('audio') or {}
    mel = audio.get('mel') if isinstance(audio.get('mel'), list) else []
    audio_header = ['received_at', 'device_ts_ms', 'frame', 'rms', 'zcr'] + [f'mel_{i:02d}' for i in range(40)]
    audio_row = [received_at, payload_dict.get('ts_ms'), audio.get('frame'), audio.get('rms'), audio.get('zcr')] + [(mel[i] if i < len(mel) else '') for i in range(40)]
    append_csv_row(os.path.join(folder, 'audio_features.csv'), audio_header, audio_row)

    imu = payload_dict.get('imu') or {}
    accel = imu.get('accel') if isinstance(imu.get('accel'), list) else []
    gyro = imu.get('gyro') if isinstance(imu.get('gyro'), list) else []
    mag = imu.get('mag') if isinstance(imu.get('mag'), list) else []
    quat = imu.get('quat') if isinstance(imu.get('quat'), list) else []
    euler = imu.get('euler') if isinstance(imu.get('euler'), list) else []
    baro = imu.get('baro') if isinstance(imu.get('baro'), list) else []
    imu_header = ['received_at', 'device_ts_ms', 'ok', 'ax_g', 'ay_g', 'az_g', 'gx_dps', 'gy_dps', 'gz_dps', 'mx', 'my', 'mz', 'qw', 'qx', 'qy', 'qz', 'roll_deg', 'pitch_deg', 'yaw_deg', 'baro0', 'baro1', 'baro2', 'baro3']
    imu_row = [received_at, payload_dict.get('ts_ms'), imu.get('ok')]
    imu_row += [(accel[i] if i < len(accel) else '') for i in range(3)]
    imu_row += [(gyro[i] if i < len(gyro) else '') for i in range(3)]
    imu_row += [(mag[i] if i < len(mag) else '') for i in range(3)]
    imu_row += [(quat[i] if i < len(quat) else '') for i in range(4)]
    imu_row += [(euler[i] if i < len(euler) else '') for i in range(3)]
    imu_row += [(baro[i] if i < len(baro) else '') for i in range(4)]
    append_csv_row(os.path.join(folder, 'imu.csv'), imu_header, imu_row)

    health = payload_dict.get('health') or {}
    health_header = ['received_at', 'device_ts_ms', 'valid', 'heart_rate_bpm', 'spo2_pct', 'micro_circulation', 'systolic_mmhg', 'diastolic_mmhg', 'respiration_rpm', 'fatigue', 'rr_interval', 'hrv_sdnn', 'hrv_rmssd', 'body_temp_c', 'env_temp_c']
    health_row = [received_at, payload_dict.get('ts_ms'), health.get('valid'), health.get('heartRate'), health.get('spo2'), health.get('microCir'), health.get('systolic'), health.get('diastolic'), health.get('respiration'), health.get('fatigue'), health.get('rrInterval'), health.get('hrvSdnn'), health.get('hrvRmssd'), health.get('bodyTemp'), health.get('envTemp')]
    append_csv_row(os.path.join(folder, 'health.csv'), health_header, health_row)

    battery = payload_dict.get('battery') or {}
    battery_header = ['received_at', 'device_ts_ms', 'ok', 'found', 'voltage_v', 'percent', 'raw_vcell', 'raw_soc']
    battery_row = [received_at, payload_dict.get('ts_ms'), battery.get('ok'), battery.get('found'), battery.get('voltage'), battery.get('percent'), battery.get('rawVcell'), battery.get('rawSoc')]
    append_csv_row(os.path.join(folder, 'battery.csv'), battery_header, battery_row)

    event_active = bool(exp.get('eventActive')) or bool(event_state.get('active'))
    event_id = exp.get('eventId') or event_state.get('eventId')
    segment_id = exp.get('eventSegment') or event_state.get('segmentId') or datetime.now(timezone.utc).strftime('seg_%Y%m%d-%H%M%S')
    if event_active and event_id:
        event_id, efolder = event_folder_for(folder, event_id)
        prefix = safe_experiment_id(segment_id)
        with open(os.path.join(efolder, f'{prefix}_sensor_frames.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps({'received_at': received_at, 'topic': topic, 'payload': payload_dict}, ensure_ascii=False, separators=(',', ':')) + '\n')
        append_csv_row(os.path.join(efolder, f'{prefix}_audio_features.csv'), audio_header, audio_row)
        append_csv_row(os.path.join(efolder, f'{prefix}_imu.csv'), imu_header, imu_row)
        append_csv_row(os.path.join(efolder, f'{prefix}_health.csv'), health_header, health_row)
        append_csv_row(os.path.join(efolder, f'{prefix}_battery.csv'), battery_header, battery_row)


@bp.route('/api/experiment-files')
def api_experiment_files():
    experiments = []
    if os.path.isdir(config.CLOUD_DATA_DIR):
        for exp_id in sorted(os.listdir(config.CLOUD_DATA_DIR), reverse=True):
            folder = os.path.join(config.CLOUD_DATA_DIR, exp_id)
            if not os.path.isdir(folder):
                continue
            files = []
            for root, _, names in os.walk(folder):
                rel_root = os.path.relpath(root, folder)
                for name in sorted(names):
                    fp = os.path.join(root, name)
                    rel_name = name if rel_root == '.' else os.path.join(rel_root, name)
                    files.append({'name': rel_name, 'size': os.path.getsize(fp)})
            experiments.append({'experimentId': exp_id, 'folder': folder, 'files': files})
    return {'count': len(experiments), 'experiments': experiments[:20]}
