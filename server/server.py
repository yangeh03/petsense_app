#!/usr/bin/env python3
"""PetSense - MQTT Collector + Sensor & WAV Audio Dashboard"""

import json, time, threading, sqlite3, os, base64, queue, re, hashlib, secrets, hmac, math, csv
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import paho.mqtt.client as mqtt
import requests
from flask import Flask, send_from_directory, Response, request, make_response

ACTION_IMPORT_ERROR = None
try:
    from action_classifier import ActionClassifier
except Exception as error:
    ActionClassifier = None
    ACTION_IMPORT_ERROR = str(error)

MQTT_BROKER   = 'localhost'
MQTT_PORT     = 1883
MQTT_USER     = 'petsense-device'
MQTT_PASS     = ('' if os.environ.get('PETSENSE_MQTT_DISABLED') == '1' else open(os.environ.get('PETSENSE_MQTT_PASSWORD_FILE', os.path.expanduser('~/.petsense-mqtt-password'))).read().strip())
MQTT_TOPIC    = 'petsense/sensor/#'
CONTROL_TOPIC = 'petsense/device/esp32-001/command'
HTTP_PORT     = 8080
APP_DIR       = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR    = os.path.join(APP_DIR, 'static')
ANZUO_STATIC  = os.path.join(APP_DIR, 'anzuo-static')
ANZUO_DB      = os.path.join(APP_DIR, 'anzuo.db')
DB_PATH       = os.path.join(APP_DIR, 'data.db')
AUDIO_DIR     = os.path.join(APP_DIR, 'audio_files')
CLOUD_DATA_DIR = os.path.join(APP_DIR, 'cloud_data')
ACTION_DASHBOARD_DIR = os.path.join(APP_DIR, 'action-dashboard')
ACTION_MODEL_PATH = os.environ.get('PETSENSE_ACTION_MODEL', os.path.join(APP_DIR, 'models', 'random_forest_1s.joblib'))
MAX_HISTORY   = 10000

RAIL_STATION_URL = 'https://kyfw.12306.cn/otn/resources/js/framework/station_name.js'
RAIL_QUERY_BASE = 'https://kyfw.12306.cn/otn/leftTicket/'
RAIL_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Referer': 'https://kyfw.12306.cn/otn/leftTicket/init',
}
rail_station_cache = {'loaded_at': 0, 'by_name': {}, 'by_code': {}}
rail_query_cache = {}
rail_request_times = {}
rail_lock = threading.Lock()
RAIL_HUBS = {
    '北京站': (39.9028, 116.4270), '北京南': (39.8650, 116.3789), '北京西': (39.8947, 116.3213),
    '上海站': (31.2495, 121.4557), '上海虹桥': (31.1979, 121.3270), '广州南': (22.9907, 113.2690),
    '广州站': (23.1480, 113.2572), '深圳北': (22.6095, 114.0299), '杭州东': (30.2921, 120.2122),
    '南京南': (31.9688, 118.7917), '天津西': (39.1588, 117.1645), '济南西': (36.6701, 116.8922),
    '青岛北': (36.1694, 120.3820), '武汉': (30.6102, 114.4243), '汉口': (30.6180, 114.2550),
    '长沙南': (28.1466, 113.0647), '郑州东': (34.7590, 113.7729), '西安北': (34.3763, 108.9355),
    '成都东': (30.6305, 104.1411), '重庆北': (29.6085, 106.5506), '昆明南': (24.8806, 102.8617),
    '贵阳北': (26.6530, 106.6740), '南宁东': (22.8420, 108.4135), '福州南': (25.9855, 119.3900),
    '厦门北': (24.6362, 118.0749), '合肥南': (31.7988, 117.2907), '苏州北': (31.4211, 120.6404),
    '石家庄': (38.0106, 114.4846), '沈阳北': (41.8183, 123.4352), '哈尔滨西': (45.7079, 126.5856),
}

os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(CLOUD_DATA_DIR, exist_ok=True)
audio_recording_state = {'enabled': False, 'updated_at': None, 'recordId': None}
audio_stop_timer = None
experiment_state = {'active': False, 'experimentId': None, 'started_at': None, 'stopped_at': None, 'folder': None}
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
event_state = {'active': False, 'eventId': None, 'label': None, 'segmentId': None, 'started_at': None, 'stopped_at': None, 'folder': None}
control_request_times = {}
CONTROL_DEBOUNCE_SEC = 0.8

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()
cur.execute('CREATE TABLE IF NOT EXISTS readings (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, payload TEXT NOT NULL, received_at TEXT NOT NULL)')
cur.execute('''CREATE TABLE IF NOT EXISTS action_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    label TEXT NOT NULL,
    action_key TEXT NOT NULL,
    confidence REAL NOT NULL,
    scores_json TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    window_start_ts_ms INTEGER NOT NULL,
    window_end_ts_ms INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    predicted_at TEXT NOT NULL
)''')
cur.execute('CREATE INDEX IF NOT EXISTS action_predictions_device_time ON action_predictions(device_id, id DESC)')
cur.execute('PRAGMA journal_mode=WAL')
conn.commit()

action_classifier = None
action_classifier_error = None
if ActionClassifier is None:
    action_classifier_error = f'推理依赖不可用：{ACTION_IMPORT_ERROR}'
elif not os.path.isfile(ACTION_MODEL_PATH):
    action_classifier_error = f'模型文件不存在：{ACTION_MODEL_PATH}'
else:
    try:
        action_classifier = ActionClassifier(ACTION_MODEL_PATH)
        print(f'[ACTION] loaded {ACTION_MODEL_PATH}')
    except Exception as error:
        action_classifier_error = f'模型加载失败：{error}'
if action_classifier_error:
    print(f'[ACTION] disabled: {action_classifier_error}')

def control_debounced(key):
    now = time.monotonic()
    last = control_request_times.get(key, 0)
    if now - last < CONTROL_DEBOUNCE_SEC:
        return True
    control_request_times[key] = now
    return False

def init_anzuo_db():
    with sqlite3.connect(ANZUO_DB) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS seat_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            train_no TEXT NOT NULL,
            route TEXT NOT NULL,
            travel_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            carriage TEXT NOT NULL,
            area TEXT NOT NULL,
            reason TEXT NOT NULL,
            offer INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            helper_user_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        db.execute('CREATE INDEX IF NOT EXISTS seat_requests_status_created ON seat_requests(status, created_at DESC)')
        db.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            sender_user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            read_at TEXT,
            FOREIGN KEY(request_id) REFERENCES seat_requests(id),
            FOREIGN KEY(sender_user_id) REFERENCES users(id)
        )''')
        message_columns = {row[1] for row in db.execute('PRAGMA table_info(chat_messages)')}
        if 'read_at' not in message_columns:
            db.execute('ALTER TABLE chat_messages ADD COLUMN read_at TEXT')
        db.execute('CREATE INDEX IF NOT EXISTS chat_messages_request_id ON chat_messages(request_id, id)')

init_anzuo_db()

def password_digest(password, salt_hex):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), 180000).hex()

def current_anzuo_user():
    token = request.cookies.get('anzuo_session', '')
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(ANZUO_DB) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('''SELECT users.id, users.username, users.display_name
            FROM sessions JOIN users ON users.id=sessions.user_id
            WHERE sessions.token_hash=? AND sessions.expires_at>?''', (token_hash, now)).fetchone()
        return dict(row) if row else None

def require_anzuo_user():
    user = current_anzuo_user()
    if not user:
        return None, ({'success': False, 'message': '请先登录'}, 401)
    return user, None

def load_rail_stations():
    """Load the public 12306 station directory and cache it for one day."""
    with rail_lock:
        if rail_station_cache['by_name'] and time.time() - rail_station_cache['loaded_at'] < 86400:
            return rail_station_cache
    response = requests.get(RAIL_STATION_URL, headers=RAIL_HEADERS, timeout=12)
    response.raise_for_status()
    by_name, by_code = {}, {}
    for name, code in re.findall(r'@[^|]*\|([^|]+)\|([A-Z0-9]+)\|', response.text):
        by_name[name] = code
        by_code[code] = name
    if len(by_name) < 1000:
        raise RuntimeError('12306 station directory was incomplete')
    with rail_lock:
        rail_station_cache.update(loaded_at=time.time(), by_name=by_name, by_code=by_code)
    return rail_station_cache

def normalize_seat(value):
    if value in ('', '--', None):
        return '无'
    return value

def query_public_trains(from_name, to_name, travel_date):
    stations = load_rail_stations()
    from_code = stations['by_name'].get(from_name)
    to_code = stations['by_name'].get(to_name)
    if not from_code or not to_code:
        raise ValueError('请输入完整、准确的车站名称，例如“北京南”“上海虹桥”')

    cache_key = (from_code, to_code, travel_date)
    with rail_lock:
        cached = rail_query_cache.get(cache_key)
        if cached and time.time() - cached['cached_at'] < 90:
            return cached['payload'], True

    session = requests.Session()
    session.headers.update(RAIL_HEADERS)
    session.get('https://kyfw.12306.cn/otn/leftTicket/init', timeout=12)
    params = {
        'leftTicketDTO.train_date': travel_date,
        'leftTicketDTO.from_station': from_code,
        'leftTicketDTO.to_station': to_code,
        'purpose_codes': 'ADULT',
    }
    probe = session.get(RAIL_QUERY_BASE + 'query', params=params, timeout=15)
    probe.raise_for_status()
    probe_data = probe.json()
    query_path = probe_data.get('c_url', 'leftTicket/queryG') if isinstance(probe_data, dict) else 'leftTicket/queryG'
    if not query_path.startswith('leftTicket/'):
        query_path = 'leftTicket/queryG'
    response = session.get('https://kyfw.12306.cn/otn/' + query_path, params=params, timeout=18)
    response.raise_for_status()
    payload = response.json()
    if payload.get('httpstatus') != 200 or not isinstance(payload.get('data'), dict):
        raise RuntimeError('12306 暂时未返回可用数据，请稍后重试')

    station_map = payload['data'].get('map', {})
    rows = []
    for encoded in payload['data'].get('result', [])[:80]:
        fields = encoded.split('|')
        if len(fields) < 33:
            continue
        rows.append({
            'train_no': fields[3],
            'from_station': station_map.get(fields[6], stations['by_code'].get(fields[6], fields[6])),
            'to_station': station_map.get(fields[7], stations['by_code'].get(fields[7], fields[7])),
            'start_time': fields[8],
            'arrive_time': fields[9],
            'duration': fields[10],
            'bookable': fields[11] == 'Y',
            'business': normalize_seat(fields[32]),
            'first_class': normalize_seat(fields[31]),
            'second_class': normalize_seat(fields[30]),
            'soft_sleeper': normalize_seat(fields[23]),
            'hard_sleeper': normalize_seat(fields[28]),
            'hard_seat': normalize_seat(fields[29]),
            'no_seat': normalize_seat(fields[26]),
        })
    result = {
        'success': True,
        'from': from_name,
        'to': to_name,
        'date': travel_date,
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'source': '中国铁路12306公开余票查询',
        'notice': '数据按需查询并缓存90秒，仅供参考，以12306及车站公告为准。',
        'trains': rows,
    }
    with rail_lock:
        rail_query_cache[cache_key] = {'cached_at': time.time(), 'payload': result}
    return result, False

def store(topic, payload):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    cur.execute('INSERT INTO readings (topic, payload, received_at) VALUES (?,?,?)', (topic, payload, ts))
    conn.commit()
    cur.execute('SELECT COUNT(*) FROM readings')
    cnt = cur.fetchone()[0]
    if cnt > MAX_HISTORY:
        cur.execute('DELETE FROM readings WHERE id IN (SELECT id FROM readings ORDER BY id ASC LIMIT ?)', (cnt - MAX_HISTORY,))
        conn.commit()
    return ts

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
    cur.execute('''INSERT INTO action_predictions (
        device_id, event_id, label, action_key, confidence, scores_json, sample_count,
        window_start_ts_ms, window_end_ts_ms, model_version, predicted_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (
        prediction['deviceId'], prediction['eventId'], prediction['label'], prediction['actionKey'],
        prediction['confidence'], json.dumps(prediction['scores'], ensure_ascii=False), prediction['sampleCount'],
        prediction['windowStartTsMs'], prediction['windowEndTsMs'], prediction['modelVersion'], prediction['predictedAt'],
    ))
    conn.commit()

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

def list_at(value, index, default=None):
    if isinstance(value, (list, tuple)) and index < len(value):
        return value[index]
    return default

def compact_sample_to_payload(batch, sample):
    if not isinstance(sample, dict):
        return None

    if 'audio' in sample or 'imu' in sample or 'experiment' in sample:
        frame = dict(sample)
        frame.setdefault('deviceId', batch.get('deviceId'))
        return frame

    e = sample.get('e') or []
    b = sample.get('b') or []
    a = sample.get('a') or []
    i = sample.get('i') or []
    h = sample.get('h') or []
    u = sample.get('u') or []

    return {
        'deviceId': batch.get('deviceId'),
        'ts_ms': sample.get('t'),
        'experiment': {
            'active': bool(list_at(e, 0, False)),
            'id': list_at(e, 1, ''),
            'sdLogging': bool(list_at(e, 2, False)),
            'sdAudioBytes': 0,
            'sdRows': list_at(e, 3, 0),
            'sdAudioMode': 'features_only',
            'sdStatus': list_at(e, 4, ''),
            'eventActive': bool(list_at(e, 5, False)),
            'eventId': list_at(e, 6, ''),
            'eventLabel': list_at(e, 7, ''),
            'eventSegment': list_at(e, 8, ''),
            'eventAudioBytes': list_at(e, 9, 0),
        },
        'battery': {
            'ok': bool(list_at(b, 0, False)),
            'found': bool(list_at(b, 1, False)),
            'source': 'MAX17048',
            'sda': 15,
            'scl': 16,
            'voltage': list_at(b, 2, 0),
            'percent': list_at(b, 3, 0),
            'rawVcell': list_at(b, 4, 0),
            'rawSoc': list_at(b, 5, 0),
        },
        'audio': {
            'ok': bool(list_at(a, 0, False)),
            'recording': bool(list_at(a, 1, False)),
            'uploading': bool(list_at(a, 2, False)),
            'recordBytes': list_at(a, 3, 0),
            'recordCapacityBytes': list_at(a, 4, 0),
            'sampleRate': list_at(a, 5, 0),
            'bits': list_at(a, 6, 0),
            'frame': list_at(a, 7, 0),
            'rms': list_at(a, 8, 0),
            'zcr': list_at(a, 9, 0),
            'mel': list_at(a, 10, []),
        },
        'imu': {
            'ok': bool(list_at(i, 0, False)),
            'accel': list_at(i, 1, []),
            'gyro': list_at(i, 2, []),
            'mag': list_at(i, 3, []),
            'quat': list_at(i, 4, []),
            'euler': list_at(i, 5, []),
            'baro': list_at(i, 6, []),
        },
        'health': {
            'valid': bool(list_at(h, 0, False)),
            'heartRate': list_at(h, 1, 0),
            'spo2': list_at(h, 2, 0),
            'microCir': list_at(h, 3, 0),
            'systolic': list_at(h, 4, 0),
            'diastolic': list_at(h, 5, 0),
            'respiration': list_at(h, 6, 0),
            'fatigue': list_at(h, 7, 0),
            'rrInterval': list_at(h, 8, 0),
            'hrvSdnn': list_at(h, 9, 0),
            'hrvRmssd': list_at(h, 10, 0),
            'bodyTemp': list_at(h, 11, 0),
            'envTemp': list_at(h, 12, 0),
        },
        'uwb': {
            'valid': bool(list_at(u, 0, False)),
            'receivedMs': list_at(u, 1, 0),
            'timer': list_at(u, 2, 0),
            'tagId': list_at(u, 3, 0),
            'anchorId': list_at(u, 4, 0),
            'sequence': list_at(u, 5, 0),
            'anchorMask': list_at(u, 6, 0),
            'kalmanEnabled': bool(list_at(u, 7, False)),
            'distanceMm': list_at(u, 8, []),
            'rawMm': list_at(u, 9, []),
            'kalmanMm': list_at(u, 10, []),
        },
    }

def expand_sensor_batch(payload_dict):
    if not isinstance(payload_dict, dict):
        return []
    samples = payload_dict.get('samples') or payload_dict.get('batch')
    if not isinstance(samples, list):
        return []
    frames = []
    for sample in samples[:100]:
        frame = compact_sample_to_payload(payload_dict, sample)
        if frame:
            frames.append(frame)
    return frames

def safe_experiment_id(value):
    raw = str(value or '').strip()
    if not raw:
        raw = datetime.now(timezone.utc).strftime('exp-%Y%m%d-%H%M%S')
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', raw)[:80].strip('._-')
    return safe or datetime.now(timezone.utc).strftime('exp-%Y%m%d-%H%M%S')

def experiment_dir(experiment_id):
    exp_id = safe_experiment_id(experiment_id)
    path = os.path.join(CLOUD_DATA_DIR, exp_id)
    os.makedirs(path, exist_ok=True)
    return exp_id, path

def safe_event_id(value):
    raw = str(value or '').strip()
    if raw in EVENT_LABEL_BY_ID:
        return raw
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', raw)[:40].strip('._-')
    return safe if safe in EVENT_LABEL_BY_ID else safe

def ensure_event_dirs(folder):
    for event_id, label in EVENT_LABELS:
        event_folder = os.path.join(folder, event_id)
        os.makedirs(event_folder, exist_ok=True)
        manifest_path = os.path.join(event_folder, 'event_manifest.json')
        if not os.path.exists(manifest_path):
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump({'event_id': event_id, 'label': label, 'files': 'segment timestamp prefix: seg_YYYYmmdd-HHMMSS-*'}, f, ensure_ascii=False, indent=2)

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

# ─── WAV 分片重组状态 ───
# audio_buffers[fileId] = {"totalChunks": N, "chunks": [None]*N, "started_at": ts, "expected_bytes": ...}
audio_buffers = {}
audio_lock = threading.Lock()

def calculate_wav_rms(wav_data):
    """Return normalized RMS for 8-bit unsigned or 16-bit signed PCM WAV data."""
    if len(wav_data) <= 44:
        return 0.0

    import struct
    bits_per_sample = 16
    data_offset = 44
    try:
        if wav_data[0:4] == b'RIFF' and wav_data[8:12] == b'WAVE':
            pos = 12
            while pos + 8 <= len(wav_data):
                chunk_id = wav_data[pos:pos+4]
                chunk_size = struct.unpack('<I', wav_data[pos+4:pos+8])[0]
                chunk_start = pos + 8
                if chunk_id == b'fmt ' and chunk_start + 16 <= len(wav_data):
                    bits_per_sample = struct.unpack('<H', wav_data[chunk_start+14:chunk_start+16])[0]
                elif chunk_id == b'data':
                    data_offset = chunk_start
                    break
                pos = chunk_start + chunk_size + (chunk_size & 1)
    except Exception:
        data_offset = 44

    pcm_data = wav_data[data_offset:]
    if not pcm_data:
        return 0.0

    squares = 0.0
    count = 0
    if bits_per_sample == 8:
        for sample in pcm_data:
            value = (sample - 128) / 128.0
            squares += value * value
            count += 1
    elif bits_per_sample == 16:
        for i in range(0, len(pcm_data) - 1, 2):
            value = struct.unpack('<h', pcm_data[i:i+2])[0] / 32768.0
            squares += value * value
            count += 1
    else:
        return 0.0

    return (squares / count) ** 0.5 if count else 0.0

def handle_audio_wav_binary(payload):
    if len(payload) < 32 or payload[:7] != b'PSWAV01':
        print(f'[AUDIO] Invalid binary WAV payload bytes={len(payload)}')
        return None
    file_id = str(int.from_bytes(payload[8:12], 'little'))
    sample_rate = int.from_bytes(payload[12:16], 'little')
    wav_size = int.from_bytes(payload[16:20], 'little')
    duration_ms = int.from_bytes(payload[20:24], 'little')
    channels = int.from_bytes(payload[24:26], 'little')
    bits = int.from_bytes(payload[26:28], 'little')
    wav_data = bytes(payload[32:32 + wav_size])
    if len(wav_data) != wav_size or not wav_data.startswith(b'RIFF'):
        print(f'[AUDIO] Truncated binary WAV fileId={file_id} got={len(wav_data)} expected={wav_size}')
        return None

    wav_filename = f"audio_{file_id}_{int(time.time()*1000)}.wav"
    wav_path = os.path.join(AUDIO_DIR, wav_filename)
    with open(wav_path, 'wb') as f:
        f.write(wav_data)
    rms_val = calculate_wav_rms(wav_data)
    print(f'[AUDIO] Binary single {wav_filename} {len(wav_data)} bytes {sample_rate}Hz {bits}bit ch={channels} durationMs={duration_ms} rms={rms_val:.5f}')
    return {
        'type': 'audio_ready',
        'fileId': file_id,
        'filename': wav_filename,
        'url': f'/audio/{wav_filename}',
        'size': len(wav_data),
        'chunks': 1,
        'totalChunks': 1,
        'rms': round(rms_val, 5),
        'received_at': datetime.now(timezone.utc).isoformat()
    }

def handle_audio_file_chunk(topic, payload_dict, raw_str):
    """处理 audio_file 分片: start/chunk/end"""
    file_id = str(payload_dict.get('fileId', ''))
    chunk_type = payload_dict.get('type', '')
    chunk_index = int(payload_dict.get('chunkIndex', payload_dict.get('index', 0)) or 0)
    total_chunks = int(payload_dict.get('totalChunks', payload_dict.get('chunks', 0)) or 0)
    expected_size = int(payload_dict.get('size', 0) or 0)
    offset = payload_dict.get('offset')
    offset = int(offset) if offset is not None else None
    data_b64 = payload_dict.get('data', '')

    if not file_id:
        return None

    chunk_bytes = base64.b64decode(data_b64) if data_b64 else b''

    with audio_lock:
        if chunk_type == 'single' and chunk_bytes:
            wav_filename = f"audio_{file_id}_{int(time.time()*1000)}.wav"
            wav_path = os.path.join(AUDIO_DIR, wav_filename)
            with open(wav_path, 'wb') as f:
                f.write(chunk_bytes)
            rms_val = calculate_wav_rms(chunk_bytes)
            print(f'[AUDIO] Single file {wav_filename}  {len(chunk_bytes)} bytes  rms={rms_val:.5f}')
            return {
                'type': 'audio_ready',
                'fileId': file_id,
                'filename': wav_filename,
                'url': f'/audio/{wav_filename}',
                'size': len(chunk_bytes),
                'chunks': 1,
                'totalChunks': 1,
                'rms': round(rms_val, 5),
                'received_at': datetime.now(timezone.utc).isoformat()
            }

        if chunk_type == 'start':
            audio_buffers[file_id] = {
                'totalChunks': total_chunks,
                'chunks': [None] * total_chunks,
                'started_at': time.time(),
                'received': 0,
                'total_bytes': 0,
                'expected_size': expected_size,
                'sampleRate': int(payload_dict.get('sampleRate', 8000) or 8000),
                'channels': int(payload_dict.get('channels', 1) or 1),
                'bits': int(payload_dict.get('bits', 8) or 8)
            }
            print(f'[AUDIO] Start fileId={file_id} totalChunks={total_chunks}')
            return {'type': 'audio_progress', 'fileId': file_id, 'chunk': 0, 'received': 0, 'total': total_chunks}

        buf = audio_buffers.get(file_id)
        if not buf:
            if chunk_type != 'chunk':
                print(f'[AUDIO] Unknown fileId={file_id}, ignoring {chunk_type}')
                return None
            inferred_chunks = max(total_chunks, chunk_index + 1)
            audio_buffers[file_id] = {
                'totalChunks': inferred_chunks,
                'chunks': [None] * inferred_chunks,
                'started_at': time.time(),
                'received': 0,
                'total_bytes': 0,
                'expected_size': expected_size,
                'sampleRate': int(payload_dict.get('sampleRate', 16000) or 16000),
                'channels': int(payload_dict.get('channels', 1) or 1),
                'bits': int(payload_dict.get('bits', 16) or 16),
                'recovered_start': True
            }
            buf = audio_buffers[file_id]
            print(f'[AUDIO] Recovered missing start fileId={file_id} totalChunks={inferred_chunks}')

        if total_chunks and total_chunks > buf.get('totalChunks', 0):
            buf['chunks'].extend([None] * (total_chunks - len(buf['chunks'])))
            buf['totalChunks'] = total_chunks
        if expected_size:
            buf['expected_size'] = expected_size
        for key, default in (('sampleRate', 16000), ('channels', 1), ('bits', 16)):
            if payload_dict.get(key) is not None:
                buf[key] = int(payload_dict.get(key) or default)

        if chunk_type == 'chunk' and chunk_bytes and chunk_index >= len(buf['chunks']):
            buf['chunks'].extend([None] * (chunk_index + 1 - len(buf['chunks'])))
            buf['totalChunks'] = max(buf.get('totalChunks', 0), len(buf['chunks']))

        if chunk_type == 'chunk' and chunk_bytes and 0 <= chunk_index < len(buf['chunks']):
            if buf['chunks'][chunk_index] is None:
                buf['received'] += 1
                buf['total_bytes'] += len(chunk_bytes)
            buf['chunks'][chunk_index] = (offset, chunk_bytes)

        complete = buf['totalChunks'] > 0 and buf['received'] >= buf['totalChunks']
        if chunk_type == 'end' or complete:
            present_chunks = [c for c in buf['chunks'] if c is not None]
            if not complete and len(present_chunks) < buf['totalChunks']:
                print(f'[AUDIO] End with missing chunks fileId={file_id} chunks={len(present_chunks)}/{buf["totalChunks"]}; filling gaps with silence')

            if any(c[0] is not None for c in present_chunks):
                total_len = buf.get('expected_size') or max((c[0] or 0) + len(c[1]) for c in present_chunks)
                fill = 128 if int(buf.get('bits', 8)) == 8 else 0
                assembled = bytearray([fill] * total_len)
                if total_len >= 44:
                    sr = int(buf.get('sampleRate', 8000))
                    channels = int(buf.get('channels', 1))
                    bits = int(buf.get('bits', 8))
                    data_bytes = max(0, total_len - 44)
                    byte_rate = sr * channels * bits // 8
                    block_align = channels * bits // 8
                    header = bytearray(44)
                    header[0:4] = b'RIFF'
                    header[4:8] = (36 + data_bytes).to_bytes(4, 'little')
                    header[8:12] = b'WAVE'
                    header[12:16] = b'fmt '
                    header[16:20] = (16).to_bytes(4, 'little')
                    header[20:22] = (1).to_bytes(2, 'little')
                    header[22:24] = channels.to_bytes(2, 'little')
                    header[24:28] = sr.to_bytes(4, 'little')
                    header[28:32] = byte_rate.to_bytes(4, 'little')
                    header[32:34] = block_align.to_bytes(2, 'little')
                    header[34:36] = bits.to_bytes(2, 'little')
                    header[36:40] = b'data'
                    header[40:44] = data_bytes.to_bytes(4, 'little')
                    assembled[0:44] = header
                for chunk_offset, data in present_chunks:
                    if chunk_offset is None:
                        assembled.extend(data)
                    else:
                        assembled[chunk_offset:chunk_offset+len(data)] = data
                all_data = bytes(assembled[:buf.get('expected_size') or len(assembled)])
            else:
                all_data = b''.join([c[1] for c in present_chunks])

            if len(all_data) == 0:
                del audio_buffers[file_id]
                return None

            wav_filename = f"audio_{file_id}_{int(time.time()*1000)}.wav"
            wav_path = os.path.join(AUDIO_DIR, wav_filename)
            with open(wav_path, 'wb') as f:
                f.write(all_data)

            del audio_buffers[file_id]

            rms_val = calculate_wav_rms(all_data)
            print(f'[AUDIO] Assembled {wav_filename}  {len(all_data)} bytes  chunks={buf["received"]}/{buf["totalChunks"]}  rms={rms_val:.5f}')

            return {
                'type': 'audio_ready',
                'fileId': file_id,
                'filename': wav_filename,
                'url': f'/audio/{wav_filename}',
                'size': len(all_data),
                'chunks': buf['received'],
                'rms': round(rms_val, 5)
            }

        return {'type': 'audio_progress', 'fileId': file_id, 'chunk': chunk_index, 'received': buf['received'], 'total': buf['totalChunks']}

class Forwarder:
    def __init__(self):
        self.sse_queues = []
        self._lock = threading.Lock()
    def broadcast(self, data):
        with self._lock:
            for q in self.sse_queues:
                try: q.put_nowait(data)
                except queue.Full: pass
    def add_sse(self):
        q = queue.Queue(maxsize=500)
        with self._lock:
            self.sse_queues.append(q)
            return q
    def remove_sse(self, q):
        with self._lock:
            try: self.sse_queues.remove(q)
            except ValueError: pass

fwd = Forwarder()

mqtt_client = mqtt.Client(client_id='petsense-server')
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
ws_clients = set()

def publish_control_command(command):
    payload = json.dumps(command, separators=(',', ':'))
    info = mqtt_client.publish(CONTROL_TOPIC, payload, qos=1, retain=False)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        try:
            mqtt_client.reconnect()
            time.sleep(0.2)
            info = mqtt_client.publish(CONTROL_TOPIC, payload, qos=1, retain=False)
        except Exception as e:
            print(f'[MQTT] control publish reconnect failed: {e}')
    return payload, info.rc == mqtt.MQTT_ERR_SUCCESS

def on_connect(client, userdata, flags, rc):
    print(f'[MQTT] connected rc={rc}')
    client.subscribe(MQTT_TOPIC)

def on_message(client, userdata, msg):
    if msg.topic == 'petsense/sensor/audio_wav':
        summary = json.dumps({'binary_bytes': len(msg.payload), 'kind': 'audio_wav'})
        store(msg.topic, summary)
        try:
            result = handle_audio_wav_binary(msg.payload)
            if result:
                audio_packet = json.dumps(result)
                fwd.broadcast(audio_packet)
                for sock in ws_clients.copy():
                    try: sock.send(audio_packet)
                    except: ws_clients.discard(sock)
        except Exception as e:
            print(f'[AUDIO] Binary parse error: {e}')
        return

    payload_str = msg.payload.decode(errors='replace')
    try:
        payload_dict = json.loads(payload_str)
    except Exception:
        payload_dict = None

    if msg.topic == 'petsense/sensor/esp32-001' and isinstance(payload_dict, dict):
        frames = expand_sensor_batch(payload_dict)
        if frames:
            for frame in frames:
                enrich_frame_with_action(frame)
                frame_payload = json.dumps(frame, ensure_ascii=False, separators=(',', ':'))
                ts = store(msg.topic, frame_payload)
                packet = json.dumps({'topic': msg.topic, 'payload': frame_payload, 'received_at': ts})
                try:
                    append_cloud_experiment_frame(msg.topic, frame, ts)
                except Exception as e:
                    print(f'[EXPERIMENT] Cloud batch frame write error: {e}')
                try:
                    update_daily_stats(frame.get('deviceId'), frame, ts)
                    remember_uwb(frame, ts)
                except Exception as e:
                    print(f'[APP-STATS] batch hook error: {e}')
                fwd.broadcast(packet)
                for sock in ws_clients.copy():
                    try: sock.send(packet)
                    except: ws_clients.discard(sock)
            return

    if msg.topic == 'petsense/sensor/esp32-001' and isinstance(payload_dict, dict):
        enrich_frame_with_action(payload_dict)
        payload_str = json.dumps(payload_dict, ensure_ascii=False, separators=(',', ':'))

    ts = store(msg.topic, payload_str)
    packet = json.dumps({'topic': msg.topic, 'payload': payload_str, 'received_at': ts})
    if msg.topic == 'petsense/sensor/esp32-001' and isinstance(payload_dict, dict):
        try:
            append_cloud_experiment_frame(msg.topic, payload_dict, ts)
        except Exception as e:
            print(f'[EXPERIMENT] Cloud frame write error: {e}')
        try:
            update_daily_stats(payload_dict.get('deviceId'), payload_dict, ts)
            remember_uwb(payload_dict, ts)
        except Exception as e:
            print(f'[APP-STATS] frame hook error: {e}')

    # 处理 audio_file topic：重组分片
    if msg.topic == 'petsense/sensor/audio_file':
        try:
            result = handle_audio_file_chunk(msg.topic, payload_dict or {}, payload_str)
            if result:
                # 转发重组结果
                audio_packet = json.dumps(result)
                fwd.broadcast(audio_packet)
                for sock in ws_clients.copy():
                    try: sock.send(audio_packet)
                    except: ws_clients.discard(sock)
        except Exception as e:
            print(f'[AUDIO] Parse error: {e}')
        return

    # 所有消息都广播原始数据（传感器 + 音频原始分片）
    fwd.broadcast(packet)
    for sock in ws_clients.copy():
        try: sock.send(packet)
        except: ws_clients.discard(sock)

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
if os.environ.get('PETSENSE_MQTT_DISABLED') == '1':
    print('[INIT] PETSENSE_MQTT_DISABLED=1，跳过 MQTT 接入（本地开发模式）')
else:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()

# ─── PetSense App 服务：账号 / 社区 / 每日统计 / UWB 快照（/api/app/*） ───
APP_DB = os.path.join(APP_DIR, 'petsense_app.db')
UPLOADS_DIR = os.path.join(APP_DIR, 'uploads')
APP_TOKEN_HEADER = 'X-Petsense-Session'
APP_SESSION_DAYS = 30
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
STILL_DELTA_G = 0.10
ACTIVE_DELTA_G = 0.25
SLEEP_UTC_HOURS = set(range(14, 24)) | {0}  # 北京时间 22:00–08:00
os.makedirs(UPLOADS_DIR, exist_ok=True)

stats_last_ts = {}


def app_connect():
    db = sqlite3.connect(APP_DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_app_db():
    with app_connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            avatar_emoji TEXT NOT NULL DEFAULT '🙂',
            bio TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS pets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            species TEXT NOT NULL DEFAULT 'dog',
            breed TEXT NOT NULL DEFAULT '',
            avatar_emoji TEXT NOT NULL DEFAULT '🐾',
            device_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            pet_id INTEGER,
            image_path TEXT NOT NULL,
            caption TEXT NOT NULL DEFAULT '',
            location_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS post_likes (
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(post_id, user_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER NOT NULL,
            followee_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(follower_id, followee_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS daily_stats (
            pet_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            sleep_seconds INTEGER NOT NULL DEFAULT 0,
            active_seconds INTEGER NOT NULL DEFAULT 0,
            hr_sum REAL NOT NULL DEFAULT 0,
            hr_count INTEGER NOT NULL DEFAULT 0,
            samples INTEGER NOT NULL DEFAULT 0,
            UNIQUE(pet_id, date)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS home_layout (
            pet_id INTEGER PRIMARY KEY,
            rooms_json TEXT NOT NULL,
            safe_zone_json TEXT NOT NULL,
            anchors_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )''')

        # 增量列：PetGIF 数字形象绑定 / 头像图片 / 社区外部媒体
        pet_cols = {row[1] for row in db.execute('PRAGMA table_info(pets)')}
        if 'petgif_id' not in pet_cols:
            db.execute('ALTER TABLE pets ADD COLUMN petgif_id TEXT')
        if 'avatar_image' not in pet_cols:
            db.execute('ALTER TABLE pets ADD COLUMN avatar_image TEXT')
        post_cols = {row[1] for row in db.execute('PRAGMA table_info(posts)')}
        if 'media_url' not in post_cols:
            db.execute('ALTER TABLE posts ADD COLUMN media_url TEXT')
        if 'media_kind' not in post_cols:
            db.execute('ALTER TABLE posts ADD COLUMN media_kind TEXT')


init_app_db()


def app_user_from_request():
    token = request.headers.get(APP_TOKEN_HEADER, '')
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with app_connect() as db:
        row = db.execute('''SELECT u.id, u.username, u.display_name, u.avatar_emoji
            FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>?''', (token_hash, now)).fetchone()
        return dict(row) if row else None


def require_app_user():
    user = app_user_from_request()
    if not user:
        return None, ({'success': False, 'message': '请先登录'}, 401)
    return user, None


def issue_app_session(user_id):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=APP_SESSION_DAYS)
    with app_connect() as db:
        db.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
                   (hashlib.sha256(token.encode()).hexdigest(), user_id, expires.isoformat()))
    return token


def shanghai_now(utc_dt=None):
    return (utc_dt or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=8)))


def update_daily_stats(device_id, frame, received_at):
    '''按帧累加今日睡眠/活跃秒数与平均心率（IMU 幅值派生，部署日起累积）。'''
    try:
        if not device_id:
            return
        with app_connect() as db:
            pet = db.execute('SELECT id FROM pets WHERE device_id=? ORDER BY id LIMIT 1', (device_id,)).fetchone()
            if not pet:
                return
            try:
                utc_dt = datetime.fromisoformat(received_at.replace('Z', '+00:00'))
            except ValueError:
                return
            imu = frame.get('imu') or {}
            accel = imu.get('accel') if imu.get('ok') else None
            if not (isinstance(accel, list) and len(accel) == 3 and all(isinstance(v, (int, float)) for v in accel)):
                return
            delta = abs(math.sqrt(sum(float(v) ** 2 for v in accel)) - 1.0)
            ts_ms = frame.get('ts_ms')
            if not isinstance(ts_ms, (int, float)):
                return
            previous = stats_last_ts.get(device_id)
            stats_last_ts[device_id] = ts_ms
            if previous is None or ts_ms <= previous:
                return
            interval = min(max(ts_ms - previous, 500), 10_000)

            sleep_inc = interval if utc_dt.hour in SLEEP_UTC_HOURS and delta < STILL_DELTA_G else 0
            active_inc = interval if delta > ACTIVE_DELTA_G else 0
            health = frame.get('health') or {}
            hr = health.get('heartRate') if health.get('valid') else 0
            hr_sum = float(hr) if isinstance(hr, (int, float)) and hr > 0 else 0.0
            hr_count = 1 if hr_sum > 0 else 0
            db.execute('''INSERT INTO daily_stats(pet_id,date,sleep_seconds,active_seconds,hr_sum,hr_count,samples)
                VALUES(?,?,?,?,?,?,1)
                ON CONFLICT(pet_id,date) DO UPDATE SET
                    sleep_seconds=sleep_seconds+excluded.sleep_seconds,
                    active_seconds=active_seconds+excluded.active_seconds,
                    hr_sum=hr_sum+excluded.hr_sum,
                    hr_count=hr_count+excluded.hr_count,
                    samples=samples+1''',
                       (pet['id'], shanghai_now(utc_dt).date().isoformat(), sleep_inc, active_inc, hr_sum, hr_count))
    except Exception as exc:
        print(f'[APP-STATS] {exc}')


last_uwb_frame = None


def remember_uwb(frame, received_at):
    global last_uwb_frame
    uwb = frame.get('uwb')
    if isinstance(uwb, dict) and 'valid' in uwb:
        last_uwb_frame = {'payload': frame, 'received_at': received_at}


def pet_latest_telemetry(device_id):
    if not device_id:
        return None
    cur.execute('SELECT payload, received_at FROM readings WHERE topic=? ORDER BY id DESC LIMIT 5',
                (f'petsense/sensor/{device_id}',))
    for payload_text, received_at in cur.fetchall():
        try:
            payload = json.loads(payload_text)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        health = payload.get('health') or {}
        battery = payload.get('battery') or {}
        try:
            age = time.time() - datetime.fromisoformat(received_at.replace('Z', '+00:00')).timestamp()
        except ValueError:
            age = 9e9
        return {
            'heartRate': health.get('heartRate') or 0,
            'spo2': health.get('spo2') or 0,
            'bodyTemp': health.get('bodyTemp') or 0,
            'envTemp': health.get('envTemp') or 0,
            'respiration': health.get('respiration') or 0,
            'hrvSdnn': health.get('hrvSdnn') or 0,
            'microCir': health.get('microCir') or 0,
            'fatigue': health.get('fatigue') or 0,
            'systolic': health.get('systolic') or 0,
            'diastolic': health.get('diastolic') or 0,
            'valid': bool(health.get('valid')),
            'batteryPercent': round(battery.get('percent') or 0),
            'online': age < 60,
            'receivedAt': received_at,
        }
    return None


def pet_today_stats(pet_id):
    with app_connect() as db:
        row = db.execute('SELECT sleep_seconds, active_seconds, hr_sum, hr_count FROM daily_stats WHERE pet_id=? AND date=?',
                         (pet_id, shanghai_now().date().isoformat())).fetchone()
    if not row:
        return {'sleepMinutes': 0, 'activeMinutes': 0, 'avgHeartRate': 0}
    return {
        'sleepMinutes': round(row['sleep_seconds'] / 60),
        'activeMinutes': round(row['active_seconds'] / 60),
        'avgHeartRate': round(row['hr_sum'] / row['hr_count']) if row['hr_count'] else 0,
    }


def build_insight(pet, telemetry, today):
    name = pet['name']
    if not telemetry or not telemetry.get('online'):
        return {'icon': '📡', 'text': f'{name} 的项圈暂时离线，恢复连接后这里会自动更新'}
    if telemetry.get('valid') and telemetry.get('heartRate', 0) > 180:
        return {'icon': '💗', 'text': f'{name} 的心率有点快（{round(telemetry["heartRate"])} bpm），让它安静休息一会儿吧'}
    hour = shanghai_now().hour
    if 6 <= hour <= 9 and today['activeMinutes'] < 15:
        return {'icon': '🍖', 'text': f'早晨活动还不多，{name} 可能到饭点啦'}
    if 17 <= hour <= 19 and today['activeMinutes'] < 20:
        return {'icon': '🚶', 'text': f'{name} 今天还没怎么动，适合出门遛一圈'}
    if today['activeMinutes'] >= 60:
        return {'icon': '💪', 'text': f'{name} 今天已经活跃 {today["activeMinutes"]} 分钟，表现很棒'}
    if today['sleepMinutes'] >= 300:
        return {'icon': '😴', 'text': f'{name} 今天睡了 {today["sleepMinutes"]} 分钟，睡得很安心'}
    return {'icon': '🐾', 'text': f'{name} 状态平稳，陪你度过平常又安心的一天'}


IMAGE_MAGIC = [('image/jpeg', '.jpg', b'\xff\xd8\xff'), ('image/png', '.png', b'\x89PNG'),
               ('image/webp', '.webp', b'RIFF')]


def save_upload_image(file_storage, prefix):
    data = file_storage.read()
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('图片为空或超过 10MB')
    for content_type, ext, magic in IMAGE_MAGIC:
        if data.startswith(magic):
            break
    else:
        raise ValueError('仅支持 JPG / PNG / WebP 图片')
    filename = f'{prefix}_{secrets.token_hex(6)}_{int(time.time())}{ext}'
    with open(os.path.join(UPLOADS_DIR, filename), 'wb') as handle:
        handle.write(data)
    return filename, content_type


from flask_sock import Sock
app = Flask(__name__)
sock = Sock(app)

@app.route('/api/stream')
def sse_stream():
    q = fwd.add_sse()
    def generate():
        try:
            cur.execute('SELECT topic, payload, received_at FROM readings ORDER BY id DESC LIMIT 1')
            row = cur.fetchone()
            if row:
                yield f'data: {json.dumps({"topic": row[0], "payload": row[1], "received_at": row[2]})}\n\n'
            else:
                yield ': waiting\n\n'
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f'data: {msg}\n\n'
                except queue.Empty:
                    yield ': heartbeat\n\n'
        except GeneratorExit: pass
        finally: fwd.remove_sse(q)
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Access-Control-Allow-Origin': '*'})

@sock.route('/ws')
def ws_route(ws):
    ws_clients.add(ws)
    try:
        while True:
            if ws.receive() is None: break
    except: pass
    finally: ws_clients.discard(ws)

@app.route('/api/latest-full')
def api_latest_full():
    cur.execute('SELECT topic, payload, received_at FROM readings ORDER BY id DESC LIMIT 200')
    rows = []
    for r in cur.fetchall():
        try: p = json.loads(r[1])
        except: p = {'_raw': r[1]}
        rows.append({'topic': r[0], 'payload': p, 'received_at': r[2]})
    return {'count': len(rows), 'data': rows}

@app.route('/api/stats')
def api_stats():
    cur.execute('SELECT COUNT(*), MAX(received_at) FROM readings')
    cnt, latest = cur.fetchone()
    return {'total': cnt, 'latest': latest, 'sse_clients': len(fwd.sse_queues), 'ws_clients': len(ws_clients)}

@app.route('/api/action/status')
def api_action_status():
    payload = action_status_payload()
    payload['latest'] = action_classifier.latest() if action_classifier else None
    return payload

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
    with sqlite3.connect(DB_PATH) as action_db:
        rows = action_db.execute(query, params).fetchall()
    return [{
        'deviceId': row[0], 'eventId': row[1], 'label': row[2], 'actionKey': row[3],
        'confidence': row[4], 'scores': json.loads(row[5]), 'sampleCount': row[6],
        'windowStartTsMs': row[7], 'windowEndTsMs': row[8], 'modelVersion': row[9],
        'predictedAt': row[10], 'source': 'random_forest_1s',
    } for row in rows]

@app.route('/api/action/latest')
def api_action_latest():
    device_id = request.args.get('device_id')
    latest = action_classifier.latest(device_id) if action_classifier else None
    if not latest:
        rows = action_rows(1, device_id)
        latest = rows[0] if rows else None
    return {'data': latest}

@app.route('/api/action/history')
def api_action_history():
    try:
        limit = max(1, min(1000, int(request.args.get('limit', 300))))
    except (TypeError, ValueError):
        limit = 300
    rows = action_rows(limit, request.args.get('device_id'))
    rows.reverse()
    return {'count': len(rows), 'data': rows}

@app.route('/api/audio-recording', methods=['GET', 'POST'])
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
            'topic': CONTROL_TOPIC,
            'published': False,
            'debounced': True
        })
        return packet
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
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
            stop_ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            if audio_recording_state.get('recordId') != expected_id:
                return
            audio_recording_state.update({'enabled': False, 'updated_at': stop_ts, 'recordId': expected_id})
            stop_packet = {'type': 'audio_recording_control', 'enabled': False, 'recordId': expected_id, 'topic': CONTROL_TOPIC, 'published': True, 'updated_at': stop_ts}
            stop_packet_str = json.dumps(stop_packet)
            fwd.broadcast(stop_packet_str)
            for sock in ws_clients.copy():
                try: sock.send(stop_packet_str)
                except: ws_clients.discard(sock)
            audio_stop_timer = None
        audio_stop_timer = threading.Timer(duration_sec + 0.5, auto_stop_recording, args=(record_id,))
        audio_stop_timer.daemon = True
        audio_stop_timer.start()

    packet = {
        'type': 'audio_recording_control',
        'enabled': enabled,
        'recordId': record_id,
        'durationSec': duration_sec,
        'topic': CONTROL_TOPIC,
        'published': published,
        'updated_at': now
    }
    packet_str = json.dumps(packet)
    fwd.broadcast(packet_str)
    for sock in ws_clients.copy():
        try: sock.send(packet_str)
        except: ws_clients.discard(sock)
    return packet

@app.route('/api/experiment', methods=['GET', 'POST'])
def api_experiment():
    if request.method == 'GET':
        return experiment_state

    body = request.get_json(silent=True) or {}
    action = str(body.get('action') or '').lower()
    if control_debounced(f'experiment:{action}'):
        return {'type': 'experiment_control', 'state': experiment_state, 'published': False, 'debounced': True}
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    if action == 'start':
        exp_id = safe_experiment_id(body.get('experimentId') or datetime.now(timezone.utc).strftime('exp-%Y%m%d-%H%M%S'))
        exp_id, folder = experiment_dir(exp_id)
        experiment_state.update({'active': True, 'experimentId': exp_id, 'started_at': now, 'stopped_at': None, 'folder': folder})
        with open(os.path.join(folder, 'server_manifest.json'), 'w', encoding='utf-8') as f:
            json.dump({'schema_version': 1, 'experiment_id': exp_id, 'started_at': now, 'folder': folder, 'control_topic': CONTROL_TOPIC, 'events': [{'event_id': e, 'label': l} for e, l in EVENT_LABELS]}, f, ensure_ascii=False, indent=2)
        command = {'type': 'experiment_start', 'experimentId': exp_id, 'id': exp_id, 'ts': now}
    elif action == 'stop':
        exp_id = experiment_state.get('experimentId')
        experiment_state.update({'active': False, 'stopped_at': now})
        event_state.update({'active': False, 'stopped_at': now})
        command = {'type': 'experiment_stop', 'experimentId': exp_id, 'id': exp_id, 'ts': now}
        folder = experiment_state.get('folder')
        if folder:
            with open(os.path.join(folder, 'server_events.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'experiment_stop', 'ts': now}, ensure_ascii=False) + '\n')
    else:
        return {'success': False, 'error': 'action must be start or stop'}, 400

    payload, published = publish_control_command(command)
    packet = {'type': 'experiment_control', 'state': experiment_state, 'command': command, 'published': published, 'updated_at': now}
    packet_str = json.dumps(packet)
    fwd.broadcast(packet_str)
    for sock in ws_clients.copy():
        try: sock.send(packet_str)
        except: ws_clients.discard(sock)
    return packet

@app.route('/api/experiment-event', methods=['GET', 'POST'])
def api_experiment_event():
    if request.method == 'GET':
        return {'state': event_state, 'events': [{'eventId': e, 'label': l} for e, l in EVENT_LABELS]}

    body = request.get_json(silent=True) or {}
    action = str(body.get('action') or '').lower()
    raw_event_id = body.get('eventId')
    event_id = safe_event_id(raw_event_id)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    if not experiment_state.get('active') or not experiment_state.get('folder'):
        return {'success': False, 'error': 'experiment is not active', 'state': event_state}, 400
    if control_debounced('event:any'):
        return {'type': 'event_control', 'state': event_state, 'published': False, 'debounced': True}
    if control_debounced(f'event:{action}:{event_id}'):
        return {'type': 'event_control', 'state': event_state, 'published': False, 'debounced': True}

    if action == 'toggle':
        action = 'stop' if event_state.get('active') and event_state.get('eventId') == event_id else 'start'

    if action == 'start':
        if event_id not in EVENT_LABEL_BY_ID:
            return {'success': False, 'error': 'unknown eventId', 'events': [{'eventId': e, 'label': l} for e, l in EVENT_LABELS]}, 400
        if event_state.get('active') and event_state.get('eventId') == event_id:
            packet = {'type': 'event_control', 'state': event_state, 'published': False, 'unchanged': True, 'updated_at': now}
            packet_str = json.dumps(packet, ensure_ascii=False)
            fwd.broadcast(packet_str)
            for sock in ws_clients.copy():
                try: sock.send(packet_str)
                except: ws_clients.discard(sock)
            return packet
        if event_state.get('active') and event_state.get('eventId') != event_id:
            with open(os.path.join(experiment_state['folder'], 'server_events.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'event_stop', 'eventId': event_state.get('eventId'), 'segmentId': event_state.get('segmentId'), 'ts': now}, ensure_ascii=False) + '\n')
        label = EVENT_LABEL_BY_ID[event_id]
        _, efolder = event_folder_for(experiment_state['folder'], event_id)
        segment_id = datetime.now(timezone.utc).strftime('seg_%Y%m%d-%H%M%S')
        event_state.update({'active': True, 'eventId': event_id, 'label': label, 'segmentId': segment_id, 'started_at': now, 'stopped_at': None, 'folder': efolder})
        command = {'type': 'event_start', 'experimentId': experiment_state.get('experimentId'), 'eventId': event_id, 'label': label, 'id': experiment_state.get('experimentId'), 'e': event_id, 'l': label, 'segmentId': segment_id, 'ts': now}
    elif action == 'stop':
        command = {'type': 'event_stop', 'experimentId': experiment_state.get('experimentId'), 'eventId': event_state.get('eventId'), 'id': experiment_state.get('experimentId'), 'e': event_state.get('eventId'), 'segmentId': event_state.get('segmentId'), 'ts': now}
        event_state.update({'active': False, 'stopped_at': now})
    else:
        return {'success': False, 'error': 'action must be start, stop, or toggle'}, 400

    with open(os.path.join(experiment_state['folder'], 'server_events.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(command, ensure_ascii=False) + '\n')

    payload, published = publish_control_command(command)
    packet = {'type': 'event_control', 'state': event_state, 'command': command, 'published': published, 'updated_at': now}
    packet_str = json.dumps(packet, ensure_ascii=False)
    fwd.broadcast(packet_str)
    for sock in ws_clients.copy():
        try: sock.send(packet_str)
        except: ws_clients.discard(sock)
    return packet

@app.route('/api/experiment-files')
def api_experiment_files():
    experiments = []
    if os.path.isdir(CLOUD_DATA_DIR):
        for exp_id in sorted(os.listdir(CLOUD_DATA_DIR), reverse=True):
            folder = os.path.join(CLOUD_DATA_DIR, exp_id)
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

@app.route('/api/audio-list')
def api_audio_list():
    """列出所有已组装的 WAV 文件"""
    files = []
    if os.path.isdir(AUDIO_DIR):
        wav_names = [f for f in os.listdir(AUDIO_DIR) if f.endswith('.wav')]
        wav_names.sort(key=lambda name: os.path.getmtime(os.path.join(AUDIO_DIR, name)), reverse=True)
        for f in wav_names:
            if f.endswith('.wav'):
                fp = os.path.join(AUDIO_DIR, f)
                files.append({'filename': f, 'url': f'/audio/{f}', 'size': os.path.getsize(fp), 'mtime': os.path.getmtime(fp)})
    return {'count': len(files), 'files': files[:50]}

@app.route('/audio/<path:filename>')
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename, mimetype='audio/wav')

@app.route('/anzuo/api/auth/register', methods=['POST'])
def anzuo_register():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    display_name = str(data.get('display_name', '')).strip() or username
    password = str(data.get('password', ''))
    if not re.fullmatch(r'[A-Za-z0-9_\u4e00-\u9fff]{2,20}', username):
        return {'success': False, 'message': '账号需为2—20位中文、字母、数字或下划线'}, 400
    if len(display_name) > 20 or len(password) < 6 or len(password) > 72:
        return {'success': False, 'message': '昵称最多20字，密码需为6—72位'}, 400
    salt = secrets.token_hex(16)
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(ANZUO_DB) as db:
            cur = db.execute('INSERT INTO users(username,display_name,password_hash,salt,created_at) VALUES(?,?,?,?,?)',
                (username, display_name, password_digest(password, salt), salt, created_at))
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        return {'success': False, 'message': '这个账号已被使用'}, 409
    return create_anzuo_session(user_id, username, display_name)

def create_anzuo_session(user_id, username, display_name):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=30)
    with sqlite3.connect(ANZUO_DB) as db:
        db.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
            (hashlib.sha256(token.encode()).hexdigest(), user_id, expires.isoformat()))
    response = make_response({'success': True, 'user': {'id': user_id, 'username': username, 'display_name': display_name}})
    response.set_cookie('anzuo_session', token, max_age=2592000, httponly=True, samesite='Lax', path='/anzuo')
    return response

@app.route('/anzuo/api/auth/login', methods=['POST'])
def anzuo_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))
    with sqlite3.connect(ANZUO_DB) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT id,username,display_name,password_hash,salt FROM users WHERE username=? COLLATE NOCASE', (username,)).fetchone()
    if not row or not hmac.compare_digest(row['password_hash'], password_digest(password, row['salt'])):
        return {'success': False, 'message': '账号或密码不正确'}, 401
    return create_anzuo_session(row['id'], row['username'], row['display_name'])

@app.route('/anzuo/api/auth/me')
def anzuo_me():
    user = current_anzuo_user()
    return {'success': True, 'user': user}

@app.route('/anzuo/api/auth/logout', methods=['POST'])
def anzuo_logout():
    token = request.cookies.get('anzuo_session', '')
    if token:
        with sqlite3.connect(ANZUO_DB) as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
    response = make_response({'success': True})
    response.delete_cookie('anzuo_session', path='/anzuo')
    return response

@app.route('/anzuo/api/requests', methods=['GET', 'POST'])
def anzuo_requests():
    if request.method == 'GET':
        viewer = current_anzuo_user()
        with sqlite3.connect(ANZUO_DB) as db:
            db.row_factory = sqlite3.Row
            viewer_id = viewer['id'] if viewer else -1
            rows = db.execute('''SELECT r.id,r.train_no AS train,r.route,r.travel_date,r.start_time,r.carriage AS car,
                r.area,r.reason AS tag,r.offer AS price,r.status,r.created_at,u.display_name AS name,
                substr(u.display_name,1,1) AS avatar,h.display_name AS helper_name,r.user_id,r.helper_user_id,
                (SELECT COUNT(*) FROM chat_messages m WHERE m.request_id=r.id AND m.sender_user_id!=? AND m.read_at IS NULL) AS unread_count
                FROM seat_requests r JOIN users u ON u.id=r.user_id
                LEFT JOIN users h ON h.id=r.helper_user_id
                ORDER BY r.id DESC LIMIT 100''', (viewer_id,)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item['is_owner'] = bool(viewer and viewer['id'] == item['user_id'])
            item['is_helper'] = bool(viewer and viewer['id'] == item['helper_user_id'])
            item['can_chat'] = item['status'] == 'matched' and (item['is_owner'] or item['is_helper'])
            item.pop('user_id', None)
            item.pop('helper_user_id', None)
            items.append(item)
        return {'success': True, 'requests': items}

    user, error = require_anzuo_user()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    fields = {key: str(data.get(key, '')).strip() for key in ('train_no','route','travel_date','start_time','carriage','area','reason')}
    try:
        offer = int(data.get('offer', 0))
    except (TypeError, ValueError):
        offer = 0
    if not fields['train_no'] or not fields['route'] or not fields['travel_date'] or not fields['carriage'] or not fields['reason']:
        return {'success': False, 'message': '求座信息不完整'}, 400
    if any(len(value) > 80 for value in fields.values()) or offer < 0 or offer > 200:
        return {'success': False, 'message': '求座信息超出允许范围'}, 400
    with sqlite3.connect(ANZUO_DB) as db:
        cur = db.execute('''INSERT INTO seat_requests(user_id,train_no,route,travel_date,start_time,carriage,area,reason,offer,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''', (user['id'], fields['train_no'], fields['route'], fields['travel_date'], fields['start_time'],
            fields['carriage'], fields['area'] or '任意座位', fields['reason'], offer, datetime.now(timezone.utc).isoformat()))
    return {'success': True, 'id': cur.lastrowid}, 201

@app.route('/anzuo/api/requests/<int:request_id>/accept', methods=['POST'])
def anzuo_accept_request(request_id):
    user, error = require_anzuo_user()
    if error:
        return error
    with sqlite3.connect(ANZUO_DB) as db:
        owner = db.execute('SELECT user_id,status FROM seat_requests WHERE id=?', (request_id,)).fetchone()
        if not owner:
            return {'success': False, 'message': '求座请求不存在'}, 404
        if owner[0] == user['id']:
            return {'success': False, 'message': '不能回应自己发布的请求'}, 400
        if owner[1] != 'open':
            return {'success': False, 'message': '该请求已经得到回应'}, 409
        db.execute("UPDATE seat_requests SET status='matched',helper_user_id=? WHERE id=? AND status='open'", (user['id'], request_id))
    return {'success': True, 'message': '已发送换座意向', 'request_id': request_id}

@app.route('/anzuo/api/requests/<int:request_id>/messages', methods=['GET', 'POST'])
def anzuo_messages(request_id):
    user, error = require_anzuo_user()
    if error:
        return error
    with sqlite3.connect(ANZUO_DB) as db:
        db.row_factory = sqlite3.Row
        match = db.execute('''SELECT r.id,r.train_no,r.route,r.travel_date,r.start_time,r.carriage,r.area,r.offer,
            r.status,r.user_id,r.helper_user_id,owner.display_name AS owner_name,helper.display_name AS helper_name
            FROM seat_requests r JOIN users owner ON owner.id=r.user_id
            LEFT JOIN users helper ON helper.id=r.helper_user_id WHERE r.id=?''', (request_id,)).fetchone()
        if not match or match['status'] != 'matched' or user['id'] not in (match['user_id'], match['helper_user_id']):
            return {'success': False, 'message': '只有已达成换座的双方可以聊天'}, 403
        db.execute('UPDATE chat_messages SET read_at=? WHERE request_id=? AND sender_user_id!=? AND read_at IS NULL',
            (datetime.now(timezone.utc).isoformat(), request_id, user['id']))
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            body = str(data.get('body', '')).strip()
            if not body or len(body) > 300:
                return {'success': False, 'message': '消息需为1—300字'}, 400
            db.execute('INSERT INTO chat_messages(request_id,sender_user_id,body,created_at) VALUES(?,?,?,?)',
                (request_id, user['id'], body, datetime.now(timezone.utc).isoformat()))
        messages = db.execute('''SELECT m.id,m.sender_user_id,m.body,m.created_at,m.read_at,u.display_name AS sender_name
            FROM chat_messages m JOIN users u ON u.id=m.sender_user_id WHERE m.request_id=? ORDER BY m.id ASC LIMIT 200''', (request_id,)).fetchall()
    match_data = dict(match)
    match_data['current_user_id'] = user['id']
    return {'success': True, 'match': match_data, 'messages': [dict(row) for row in messages]}

@app.route('/anzuo/api/chats')
def anzuo_chat_list():
    user, error = require_anzuo_user()
    if error:
        return error
    with sqlite3.connect(ANZUO_DB) as db:
        db.row_factory = sqlite3.Row
        matches = db.execute('''SELECT r.id,r.train_no,r.route,r.travel_date,r.start_time,r.carriage,r.area,r.offer,
            r.user_id,r.helper_user_id,owner.display_name AS owner_name,helper.display_name AS helper_name
            FROM seat_requests r JOIN users owner ON owner.id=r.user_id JOIN users helper ON helper.id=r.helper_user_id
            WHERE r.status='matched' AND (r.user_id=? OR r.helper_user_id=?) ORDER BY r.id DESC LIMIT 100''', (user['id'], user['id'])).fetchall()
        conversations = []
        for row in matches:
            item = dict(row)
            last = db.execute('SELECT body,created_at,sender_user_id FROM chat_messages WHERE request_id=? ORDER BY id DESC LIMIT 1', (item['id'],)).fetchone()
            unread = db.execute('SELECT COUNT(*) FROM chat_messages WHERE request_id=? AND sender_user_id!=? AND read_at IS NULL', (item['id'], user['id'])).fetchone()[0]
            item['counterpart_name'] = item['helper_name'] if item['user_id'] == user['id'] else item['owner_name']
            item['last_message'] = last['body'] if last else '换座意向已达成，开始聊天吧'
            item['last_message_at'] = last['created_at'] if last else None
            item['unread_count'] = unread
            item.pop('user_id', None); item.pop('helper_user_id', None)
            conversations.append(item)
    conversations.sort(key=lambda item: item['last_message_at'] or '', reverse=True)
    return {'success': True, 'conversations': conversations, 'unread_total': sum(item['unread_count'] for item in conversations)}

@app.route('/anzuo/api/rail/nearest-station')
def anzuo_nearest_station():
    try:
        lat = float(request.args.get('lat', ''))
        lon = float(request.args.get('lon', ''))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except ValueError:
        return {'success': False, 'message': '位置信息无效'}, 400
    def distance_km(point):
        station_lat, station_lon = point
        p1, p2 = math.radians(lat), math.radians(station_lat)
        dp, dl = math.radians(station_lat-lat), math.radians(station_lon-lon)
        a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    station, point = min(RAIL_HUBS.items(), key=lambda item: distance_km(item[1]))
    distance = round(distance_km(point), 1)
    return {'success': True, 'station': station, 'distance_km': distance,
            'message': '根据当前位置推荐最近的主要铁路站，不代表已确认当前车次'}

@app.route('/anzuo/api/rail/stations')
def rail_stations():
    keyword = request.args.get('q', '').strip()
    if len(keyword) < 1:
        return {'success': True, 'stations': []}
    try:
        stations = load_rail_stations()['by_name']
        matches = [name for name in stations if keyword in name][:12]
        return {'success': True, 'stations': matches}
    except Exception as exc:
        return {'success': False, 'message': f'车站目录暂时不可用：{exc}'}, 502

@app.route('/anzuo/api/rail/trains')
def rail_trains():
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    now = time.time()
    with rail_lock:
        if now - rail_request_times.get(client_ip, 0) < 2:
            return {'success': False, 'message': '查询稍快，请两秒后再试'}, 429
        rail_request_times[client_ip] = now

    from_name = request.args.get('from', '').strip()
    to_name = request.args.get('to', '').strip()
    travel_date = request.args.get('date', '').strip()
    try:
        parsed_date = datetime.strptime(travel_date, '%Y-%m-%d').date()
        today = datetime.now().date()
        if parsed_date < today or parsed_date > today + timedelta(days=14):
            raise ValueError('仅支持查询今天起15天内的公开余票信息')
        result, cached = query_public_trains(from_name, to_name, travel_date)
        response = dict(result)
        response['cached'] = cached
        return response
    except ValueError as exc:
        return {'success': False, 'message': str(exc)}, 400
    except requests.RequestException:
        return {'success': False, 'message': '连接12306失败或受到临时限流，请稍后再试'}, 502
    except Exception as exc:
        print(f'[RAIL] query failed: {exc}')
        return {'success': False, 'message': '铁路公开数据暂时不可用，请稍后再试'}, 502

@app.route('/anzuo')
@app.route('/anzuo/')
def anzuo_index():
    return send_from_directory(ANZUO_STATIC, 'index.html')

@app.route('/anzuo/<path:path>')
def anzuo_assets(path):
    root = os.path.abspath(ANZUO_STATIC)
    full = os.path.abspath(os.path.join(root, path))
    if full.startswith(root + os.sep) and os.path.isfile(full):
        return send_from_directory(ANZUO_STATIC, path)
    return send_from_directory(ANZUO_STATIC, 'index.html')

@app.route('/assets/<path:filename>')
def anzuo_runtime_assets(filename):
    return send_from_directory(os.path.join(ANZUO_STATIC, 'assets'), filename)

@app.after_request
def app_cors_headers(response):
    origin = request.headers.get('Origin')
    if origin and (request.path.startswith('/api/app/') or request.path.startswith('/api/action/') or request.path.startswith('/uploads/')):
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = f'{APP_TOKEN_HEADER}, Content-Type'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Vary'] = 'Origin'
    return response


@app.route('/api/app/auth/register', methods=['POST'])
def app_register():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    display_name = str(data.get('display_name', '')).strip() or username
    password = str(data.get('password', ''))
    avatar_emoji = str(data.get('avatarEmoji', '')).strip() or '🙂'
    if not re.fullmatch(r'[A-Za-z0-9_\u4e00-\u9fff]{2,20}', username):
        return {'success': False, 'message': '账号需为2—20位中文、字母、数字或下划线'}, 400
    if len(display_name) > 20 or len(password) < 6 or len(password) > 72:
        return {'success': False, 'message': '昵称最多20字，密码需为6—72位'}, 400
    salt = secrets.token_hex(16)
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with app_connect() as db:
            user_id = db.execute(
                'INSERT INTO users(username,display_name,password_hash,salt,avatar_emoji,created_at) VALUES(?,?,?,?,?,?)',
                (username, display_name, password_digest(password, salt), salt, avatar_emoji, created_at)).lastrowid
    except sqlite3.IntegrityError:
        return {'success': False, 'message': '这个账号已被使用'}, 409
    token = issue_app_session(user_id)
    return {'success': True, 'token': token, 'user': {'id': user_id, 'username': username, 'displayName': display_name, 'avatarEmoji': avatar_emoji}}


@app.route('/api/app/auth/login', methods=['POST'])
def app_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))
    with app_connect() as db:
        row = db.execute('SELECT id,username,display_name,avatar_emoji,password_hash,salt FROM users WHERE username=? COLLATE NOCASE',
                         (username,)).fetchone()
    if not row or not hmac.compare_digest(row['password_hash'], password_digest(password, row['salt'])):
        return {'success': False, 'message': '账号或密码不正确'}, 401
    token = issue_app_session(row['id'])
    return {'success': True, 'token': token, 'user': {'id': row['id'], 'username': row['username'], 'displayName': row['display_name'], 'avatarEmoji': row['avatar_emoji']}}


@app.route('/api/app/auth/logout', methods=['POST'])
def app_logout():
    token = request.headers.get(APP_TOKEN_HEADER, '')
    if token:
        with app_connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
    return {'success': True}


@app.route('/api/app/auth/me')
def app_me():
    user = app_user_from_request()
    if not user:
        return {'success': True, 'user': None}
    user['displayName'] = user.pop('display_name')
    user['avatarEmoji'] = user.pop('avatar_emoji')
    return {'success': True, 'user': user}


@app.route('/api/app/pets', methods=['GET', 'POST'])
def app_pets():
    user, error = require_app_user()
    if error:
        return error
    if request.method == 'GET':
        with app_connect() as db:
            rows = db.execute('SELECT * FROM pets WHERE user_id=? ORDER BY id', (user['id'],)).fetchall()
        pets = []
        for row in rows:
            item = dict(row)
            telemetry = pet_latest_telemetry(item['device_id'])
            item['batteryPercent'] = telemetry.get('batteryPercent') if telemetry else None
            item['online'] = telemetry.get('online') if telemetry else False
            item['avatarImageUrl'] = '/uploads/' + item['avatar_image'] if item.get('avatar_image') else None
            pets.append(item)
        return {'success': True, 'pets': pets}

    data = request.get_json(silent=True) or {}
    name = str(data.get('name', '')).strip()
    species = str(data.get('species', 'dog')).strip()
    breed = str(data.get('breed', '')).strip()[:40]
    avatar_emoji = str(data.get('avatarEmoji', '')).strip() or '🐾'
    device_id = str(data.get('deviceId', '')).strip()[:40]
    if not name or len(name) > 20:
        return {'success': False, 'message': '请给宠物起个 20 字以内的名字'}, 400
    if species not in ('dog', 'cat', 'rabbit'):
        return {'success': False, 'message': '物种仅支持 dog / cat / rabbit'}, 400
    created_at = datetime.now(timezone.utc).isoformat()
    with app_connect() as db:
        pet_id = db.execute(
            'INSERT INTO pets(user_id,name,species,breed,avatar_emoji,device_id,created_at) VALUES(?,?,?,?,?,?,?)',
            (user['id'], name, species, breed, avatar_emoji, device_id, created_at)).lastrowid
    return {'success': True, 'pet': {'id': pet_id, 'name': name}}, 201


@app.route('/api/app/pets/<int:pet_id>/avatar-image', methods=['POST'])
def app_pet_avatar_image(pet_id):
    """把 PetGIF 生成的角色卡/媒体下载为宠物头像图。"""
    user, error = require_app_user()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    image_url = str(body.get('imageUrl', ''))
    if not image_url.startswith(PETGIF_STORAGE_PUBLIC):
        return {'success': False, 'message': '图片地址不受支持'}, 400
    try:
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        return {'success': False, 'message': '图片下载失败'}, 502
    filename = f'avatar_{secrets.token_hex(6)}_{int(time.time())}.png'
    with open(os.path.join(UPLOADS_DIR, filename), 'wb') as handle:
        handle.write(response.content)
    with app_connect() as db:
        owned = db.execute('SELECT 1 FROM pets WHERE id=? AND user_id=?', (pet_id, user['id'])).fetchone()
        if not owned:
            return {'success': False, 'message': '宠物不存在'}, 404
        db.execute('UPDATE pets SET avatar_image=? WHERE id=?', (filename, pet_id))
    return {'success': True, 'avatarImageUrl': '/uploads/' + filename}


@app.route('/api/app/pets/<int:pet_id>/petgif', methods=['POST'])
def app_pet_bind_petgif(pet_id):
    """把 PetGIF 数字形象绑定到宠物（看板 Hero / 位置页使用其行为 GIF）。"""
    user, error = require_app_user()
    if error:
        return error
    petgif_id = str((request.get_json(silent=True) or {}).get('petgifId', '')).strip()[:40]
    if not petgif_id:
        return {'success': False, 'message': '缺少 petgifId'}, 400
    with app_connect() as db:
        owned = db.execute('SELECT 1 FROM pets WHERE id=? AND user_id=?', (pet_id, user['id'])).fetchone()
        if not owned:
            return {'success': False, 'message': '宠物不存在'}, 404
        db.execute('UPDATE pets SET petgif_id=? WHERE id=?', (petgif_id, pet_id))
    return {'success': True}


@app.route('/api/app/dashboard')
def app_dashboard():
    user, error = require_app_user()
    if error:
        return error
    pet_id = request.args.get('pet_id', type=int)
    with app_connect() as db:
        if pet_id:
            pet = db.execute('SELECT * FROM pets WHERE id=? AND user_id=?', (pet_id, user['id'])).fetchone()
        else:
            pet = db.execute('SELECT * FROM pets WHERE user_id=? ORDER BY id LIMIT 1', (user['id'],)).fetchone()
    if not pet:
        return {'success': True, 'pet': None}
    pet = dict(pet)
    if pet.get('avatar_image'):
        pet['avatarImageUrl'] = '/uploads/' + pet['avatar_image']
    telemetry = pet_latest_telemetry(pet['device_id'])
    today = pet_today_stats(pet['id'])
    petgif = None
    if pet.get('petgif_id'):
        try:
            petgif = app_petgif_pet(pet['petgif_id'])
        except requests.RequestException:
            petgif = None
    return {
        'success': True,
        'pet': pet,
        'latest': telemetry,
        'today': today,
        'petgif': petgif,
        'insight': build_insight(pet, telemetry, today),
    }


# ─── PetBehaviorGIF 外部服务代理（key 只存服务端） ───
PETGIF_BASE = 'https://qioyo.site/petsenseIMG/api'
PETGIF_STORAGE_PUBLIC = 'https://qioyo.site/petsenseIMG/storage/'
try:
    PETGIF_KEY = open(os.environ.get('PETGIF_KEY_FILE', os.path.expanduser('~/.petgif-api-key'))).read().strip()
except OSError:
    PETGIF_KEY = ''
PETGIF_CACHE = {'at': 0.0, 'pets': {}}


def petgif_headers():
    return {'x-api-key': PETGIF_KEY} if PETGIF_KEY else {}


def petgif_get(path):
    response = requests.get(PETGIF_BASE + path, headers=petgif_headers(), timeout=20)
    response.raise_for_status()
    return response.json()


def petgif_public(url):
    """只下发可信存储域名的媒体地址。"""
    if url and str(url).startswith(PETGIF_STORAGE_PUBLIC):
        return str(url)
    return None


def petgif_simplify(pet):
    behaviors = {}
    for key, info in (pet.get('behaviors') or {}).items():
        assets = info.get('assets') or {}
        gif = petgif_public((assets.get('colorGif') or {}).get('publicUrl'))
        if gif:
            behaviors[key] = {
                'status': info.get('status'),
                'gifUrl': gif,
                'videoUrl': petgif_public((assets.get('video') or {}).get('publicUrl')),
            }
    card = petgif_public((pet.get('characterCard') or {}).get('publicUrl'))
    diaries = []
    for diary in pet.get('diaries') or []:
        image = (diary.get('assets') or {}).get('image') or {}
        poster = petgif_public(image.get('publicUrl'))
        stats = diary.get('stats') or {}
        diaries.append({
            'id': diary.get('id'),
            'date': stats.get('date'),
            'durationMin': stats.get('durationMin'),
            'steps': stats.get('steps'),
            'notes': diary.get('notes'),
            'posterUrl': poster,
        })
    profile = pet.get('profile') or {}
    return {
        'id': pet.get('id'),
        'name': pet.get('name') or profile.get('name'),
        'status': pet.get('status'),
        'stage': pet.get('stage'),
        'profile': {
            'name': profile.get('name'),
            'breed': profile.get('breed'),
            'traits': profile.get('traits') or [],
            'summary': profile.get('summary'),
        },
        'characterCardUrl': card,
        'behaviors': behaviors,
        'diaries': diaries,
    }


def app_petgif_pet(petgif_id):
    """带 10 分钟缓存的 petgif 宠物数据（供看板/社区取媒体地址）。"""
    now = time.time()
    if PETGIF_CACHE['at'] and now - PETGIF_CACHE['at'] < 600 and petgif_id in PETGIF_CACHE['pets']:
        return PETGIF_CACHE['pets'][petgif_id]
    data = petgif_get(f'/pets/{petgif_id}')
    pet = petgif_simplify(data.get('pet') or {})
    PETGIF_CACHE['at'] = now
    PETGIF_CACHE['pets'][petgif_id] = pet
    return pet


@app.route('/api/app/petgif/pet/<petgif_id>')
def app_petgif_pet_route(petgif_id):
    try:
        return {'success': True, 'pet': app_petgif_pet(petgif_id)}
    except requests.RequestException as exc:
        return {'success': False, 'message': f'PetGIF 服务不可达：{exc}'}, 502


@app.route('/api/app/petgif/pets', methods=['POST'])
def app_petgif_create():
    """转发宠物照片 → 外部生成专属数字形象（异步，App 轮询状态）。"""
    photo = request.files.get('photo') or request.files.get('image')
    name = str(request.form.get('name', '')).strip()[:20] or 'my pet'
    if photo is None:
        return {'success': False, 'message': '请选择一张宠物照片'}, 400
    forward_files = {'image': (photo.filename or 'photo.jpg', photo.stream, photo.mimetype or 'image/jpeg')}
    forward = {'name': name}
    response = requests.post(PETGIF_BASE + '/pets', headers=petgif_headers(), files=forward_files, data=forward, timeout=60)
    try:
        payload = response.json()
    except ValueError:
        payload = {'error': f'HTTP {response.status_code}'}
    return payload, (200 if response.ok else 502)


@app.route('/api/app/petgif/pets/sync', methods=['POST'])
def app_petgif_create_sync():
    """同步生成：上传宠物照片 → 阻塞轮询直到角色卡就绪（默认最长 150 秒）→ 返回完整形象。

    与异步版 /petgif/pets 的区别：App 端一次请求拿到结果，无需自行轮询。
    超时未完成时返回 202 + petgifId，可用异步接口继续查。
    """
    photo = request.files.get('photo') or request.files.get('image')
    name = str(request.form.get('name', '')).strip()[:20] or 'my pet'
    timeout_sec = max(30, min(240, int(request.form.get('timeout', 150) or 150)))
    if photo is None:
        return {'success': False, 'message': '请选择一张宠物照片'}, 400
    forward_files = {'image': (photo.filename or 'photo.jpg', photo.stream, photo.mimetype or 'image/jpeg')}
    response = requests.post(PETGIF_BASE + '/pets', headers=petgif_headers(),
                             files=forward_files, data={'name': name}, timeout=60)
    try:
        created = response.json()
    except ValueError:
        return {'success': False, 'message': f'生成任务创建失败（HTTP {response.status_code}）'}, 502
    petgif_id = (created.get('pet') or {}).get('id') or created.get('id') or created.get('petId')
    if not petgif_id:
        return {'success': False, 'message': created.get('error') or '生成任务创建失败'}, 502

    deadline = time.time() + timeout_sec
    pet = {}
    while time.time() < deadline:
        try:
            pet = petgif_get(f'/pets/{petgif_id}')
        except requests.RequestException:
            pet = {}
        stage_pet = pet.get('pet') or pet
        status = str(stage_pet.get('status', ''))
        if status in ('complete', 'failed', 'error'):
            break
        if (stage_pet.get('characterCard') or {}).get('publicUrl'):
            break
        time.sleep(4)

    stage_pet = pet.get('pet') or pet
    simplified = petgif_simplify(stage_pet) if stage_pet else None
    ready = bool(simplified and simplified.get('characterCardUrl'))
    PETGIF_CACHE['at'] = time.time()
    PETGIF_CACHE['pets'][petgif_id] = simplified
    payload = {'success': ready, 'petgifId': petgif_id, 'pet': simplified}
    if not ready:
        return payload, 202
    return payload


@app.route('/api/app/petgif/pet/<petgif_id>/diary', methods=['POST'])
def app_petgif_diary(petgif_id):
    """运动日记生成：兼容 JSON / form / multipart（App 端 JSON 无照片时自动用宠物形象照兜底）。

    外部服务要求 multipart 且至少一张照片；实测全流程约 2 分钟，轮询上限 200 秒。
    """
    payload_json = request.get_json(silent=True) or {}
    fields = {}
    for source in (request.form, payload_json):
        for key in ('durationMin', 'steps', 'places', 'notes'):
            if source.get(key) is not None:
                fields[key] = source[key]
    try:
        duration = max(1, min(600, int(float(fields.get('durationMin', 30) or 30))))
        steps = max(0, min(100000, int(float(fields.get('steps', 0) or 0))))
    except (TypeError, ValueError):
        return {'success': False, 'message': '时长与步数需为数字'}, 400
    places = str(fields.get('places', ''))[:200]
    notes = str(fields.get('notes', ''))[:200]

    forward_files = []
    for index, photo in enumerate(request.files.getlist('photos')[:6]):
        forward_files.append(
            ('photos', (photo.filename or f'photo_{index}.jpg', photo.stream, photo.mimetype or 'image/jpeg'))
        )

    # 无照片兜底：用宠物形象（角色卡 → 行为首帧）作为日记底图，保证一键可生成
    if not forward_files:
        fallback_url = None
        try:
            petgif = app_petgif_pet(petgif_id)
            fallback_url = petgif.get('characterCardUrl')
        except requests.RequestException:
            pass
        if not fallback_url:
            fallback_url = PETGIF_STORAGE_PUBLIC + f'pets/{petgif_id}/character-card.png'
        try:
            image = requests.get(fallback_url, timeout=30)
            image.raise_for_status()
            forward_files.append(('photos', ('cover.png', image.content, 'image/png')))
        except requests.RequestException:
            return {'success': False, 'message': '生成失败：缺少照片且兜底图不可用'}, 502

    data = {'durationMin': duration, 'steps': steps, 'places': places, 'notes': notes, 'petId': petgif_id}
    try:
        response = requests.post(
            PETGIF_BASE + f'/pets/{petgif_id}/diaries', headers=petgif_headers(),
            files=forward_files, data=data, timeout=120,
        )
        created = response.json() if response.ok else {}
    except (requests.RequestException, ValueError):
        created = {}
    diary_id = (created.get('diary') or {}).get('id') or created.get('id')
    if not diary_id:
        message = created.get('error') or f'日记生成任务创建失败（HTTP {response.status_code}）'
        return {'success': False, 'message': message}, 502

    deadline = time.time() + 200
    diary = {}
    while time.time() < deadline:
        try:
            raw = petgif_get(f'/pets/{petgif_id}/diaries/{diary_id}')
        except requests.RequestException:
            raw = {}
        # 响应为 {diary: {...}, active: ...} 嵌套结构
        diary = raw.get('diary') or raw
        if str(diary.get('status', '')) in ('complete', 'failed'):
            break
        time.sleep(4)
    image = (diary.get('assets') or {}).get('image') or {}
    success = str(diary.get('status', '')) == 'complete' and bool(image.get('publicUrl'))
    return {
        'success': success,
        'status': diary.get('status') or 'unknown',
        'posterUrl': petgif_public(image.get('publicUrl')),
        'stats': diary.get('stats') or {},
    }


@app.route('/api/app/petgif/diary-post', methods=['POST'])
def app_petgif_diary_post():
    """把生成的运动日记海报一键发到社区（后端代下载海报图）。"""
    user, error = require_app_user()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    poster_url = str(body.get('posterUrl', ''))
    caption = str(body.get('caption', '')).strip()[:500]
    location = str(body.get('location', '')).strip()[:80]
    pet_id = body.get('pet_id')
    if not poster_url.startswith(PETGIF_STORAGE_PUBLIC):
        return {'success': False, 'message': '海报地址不受支持'}, 400
    try:
        response = requests.get(poster_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        return {'success': False, 'message': '海报下载失败'}, 502
    filename = f'diary_{secrets.token_hex(6)}_{int(time.time())}.png'
    with open(os.path.join(UPLOADS_DIR, filename), 'wb') as handle:
        handle.write(response.content)
    if pet_id:
        with app_connect() as db:
            owned = db.execute('SELECT 1 FROM pets WHERE id=? AND user_id=?', (pet_id, user['id'])).fetchone()
        if not owned:
            pet_id = None
    created_at = datetime.now(timezone.utc).isoformat()
    with app_connect() as db:
        post_id = db.execute(
            'INSERT INTO posts(user_id,pet_id,image_path,caption,location_text,created_at) VALUES(?,?,?,?,?,?)',
            (user['id'], pet_id, filename, caption or '今天的运动日记 🏃', location, created_at),
        ).lastrowid
    return {'success': True, 'postId': post_id}, 201


@app.route('/api/app/petgif/pet/<petgif_id>/floor-plan', methods=['POST'])
def app_petgif_floorplan(petgif_id):
    """转发家居户型图渲染。"""
    plan = request.files.get('plan')
    if plan is None:
        return {'success': False, 'message': '请选择户型图'}, 400
    forward_files = {'plan': (plan.filename or 'plan.png', plan.stream, plan.mimetype or 'image/png')}
    response = requests.post(
        PETGIF_BASE + f'/pets/{petgif_id}/floor-plan', headers=petgif_headers(), files=forward_files,
        data={'petId': petgif_id, 'notes': str(request.form.get('notes', ''))[:200]}, timeout=120,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {'error': f'HTTP {response.status_code}'}
    return payload, (200 if response.ok else 502)


trend_cache = {'at': 0.0, 'daily': None}


def _aggregate_experiment_health():
    """聚合 cloud_data 全部实验 health.csv → 按北京时间日期的体征样本集（结果缓存 15 分钟）。"""
    result = {}
    if not os.path.isdir(CLOUD_DATA_DIR):
        return result
    for exp_id in sorted(os.listdir(CLOUD_DATA_DIR)):
        path = os.path.join(CLOUD_DATA_DIR, exp_id, 'health.csv')
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8-sig', newline='') as handle:
                for row in csv.DictReader(handle):
                    if str(row.get('valid', '')).lower() != 'true':
                        continue
                    try:
                        dt = datetime.fromisoformat(str(row.get('received_at', '')).replace('Z', '+00:00'))
                    except ValueError:
                        continue
                    date = (dt + timedelta(hours=8)).date().isoformat()
                    bucket = result.setdefault(date, {'hr': [], 'spo2': [], 'temp': []})

                    def put(key, raw):
                        try:
                            value = float(raw)
                            if value > 0:
                                bucket[key].append(value)
                        except (TypeError, ValueError):
                            pass

                    put('hr', row.get('heart_rate_bpm'))
                    put('spo2', row.get('spo2_pct'))
                    put('temp', row.get('body_temp_c'))
        except (OSError, UnicodeDecodeError):
            continue
    return result


def _bucket_summary(entry):
    def avg(seq, nd=0):
        return round(sum(seq) / len(seq), nd) if seq else None

    return {
        'avgHr': avg(entry['hr']),
        'minHr': round(min(entry['hr'])) if entry['hr'] else None,
        'maxHr': round(max(entry['hr'])) if entry['hr'] else None,
        'avgSpo2': avg(entry['spo2'], 1),
        'avgTemp': avg(entry['temp'], 1),
        'samples': len(entry['hr']) + len(entry['spo2']) + len(entry['temp']),
    }


def _daily_trend_buckets(days):
    if not trend_cache['daily'] or time.time() - trend_cache['at'] > 900:
        trend_cache['daily'] = _aggregate_experiment_health()
        trend_cache['at'] = time.time()
    stats = trend_cache['daily']
    today = shanghai_now().date()
    buckets = []
    for offset in range(days - 1, -1, -1):
        date = (today - timedelta(days=offset)).isoformat()
        entry = stats.get(date)
        bucket = {'date': date, 'label': date[5:].replace('-', '/'), **(_bucket_summary(entry) if entry else {
            'avgHr': None, 'minHr': None, 'maxHr': None, 'avgSpo2': None, 'avgTemp': None, 'samples': 0})}
        buckets.append(bucket)
    # 当天实时数据（readings 环冲）合并进今天的日桶
    for bucket in _hourly_trend_buckets():
        if bucket['date'] == date and bucket['samples'] > 0 and buckets[-1]['date'] == date and buckets[-1]['samples'] == 0:
            merged = {k: bucket[k] for k in ('avgHr', 'minHr', 'maxHr', 'avgSpo2', 'avgTemp', 'samples')}
            buckets[-1].update(merged)
    return buckets


def _hourly_trend_buckets():
    """最近 24 小时逐小时均值（来自 readings 环形缓冲，北京时间）。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cur.execute(
        'SELECT payload, received_at FROM readings WHERE topic=? AND received_at>=? ORDER BY id ASC',
        ('petsense/sensor/esp32-001', cutoff),
    )
    per_hour = {}
    for payload_text, received_at in cur.fetchall():
        try:
            payload = json.loads(payload_text)
            health = payload.get('health') or {}
            if not health.get('valid'):
                continue
            dt = datetime.fromisoformat(received_at.replace('Z', '+00:00')) + timedelta(hours=8)
            key = dt.strftime('%Y-%m-%d')
            hour_label = dt.strftime('%H时')
            bucket = per_hour.setdefault((key, hour_label), {'hr': [], 'spo2': [], 'temp': []})
            hr = health.get('heartRate') or 0
            spo2 = health.get('spo2') or 0
            temp = health.get('bodyTemp') or 0
            if hr > 0:
                bucket['hr'].append(float(hr))
            if spo2 > 0:
                bucket['spo2'].append(float(spo2))
            if temp > 0:
                bucket['temp'].append(float(temp))
        except (TypeError, ValueError):
            continue
    buckets = []
    for (date, hour_label), entry in sorted(per_hour.items()):
        buckets.append({
            'date': date,
            'label': hour_label,
            **_bucket_summary(entry),
        })
    return buckets


@app.route('/api/app/trends')
def app_trends():
    period = request.args.get('period', 'week')
    if period == 'day':
        return {'success': True, 'period': 'day', 'buckets': _hourly_trend_buckets()}
    if period == 'all':
        stats = trend_cache['daily'] or _aggregate_experiment_health()
        trend_cache['daily'] = stats
        trend_cache['at'] = time.time()
        buckets = []
        for date in sorted(stats):
            entry = stats[date]
            buckets.append({
                'date': date,
                'label': date[5:].replace('-', '/'),
                **_bucket_summary(entry),
            })
        return {'success': True, 'period': 'all', 'buckets': buckets}
    days = 30 if period == 'month' else 7
    return {'success': True, 'period': f'{days}d', 'buckets': _daily_trend_buckets(days)}


@app.route('/api/app/uwb/latest')
def app_uwb_latest():
    if not last_uwb_frame:
        return {'success': True, 'frame': None}
    payload = last_uwb_frame['payload']
    return {'success': True, 'frame': {'deviceId': payload.get('deviceId'), 'tsMs': payload.get('ts_ms'),
            'uwb': payload.get('uwb'), 'receivedAt': last_uwb_frame['received_at']}}


def post_rows(viewer_id, where_clause='', params=()):
    query = f'''SELECT p.id,p.caption,p.location_text,p.image_path,p.created_at,p.media_url,p.media_kind,
        u.id AS user_id,u.display_name,u.avatar_emoji,
        pet.id AS pet_id,pet.name AS pet_name,pet.avatar_emoji AS pet_emoji,
        (SELECT COUNT(*) FROM post_likes l WHERE l.post_id=p.id) AS likes,
        (SELECT COUNT(*) FROM comments c WHERE c.post_id=p.id) AS comments_count,
        EXISTS(SELECT 1 FROM post_likes l2 WHERE l2.post_id=p.id AND l2.user_id=?) AS liked_by_me
        FROM posts p JOIN users u ON u.id=p.user_id LEFT JOIN pets pet ON pet.id=p.pet_id
        {where_clause} ORDER BY p.id DESC LIMIT 60'''
    with app_connect() as db:
        rows = db.execute(query, (viewer_id, *params)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        image_path = item.pop('image_path') or ''
        item['imageUrl'] = f'/uploads/{image_path}' if image_path else ''
        item['mediaUrl'] = item.pop('media_url')
        item['mediaKind'] = item.pop('media_kind')
        item['isMine'] = item['user_id'] == viewer_id
        items.append(item)
    return items


@app.route('/api/app/feed')
def app_feed():
    viewer = app_user_from_request()
    user_id = request.args.get('user_id', type=int)
    if user_id:
        items = post_rows(viewer['id'] if viewer else -1, 'WHERE p.user_id=?', (user_id,))
    else:
        items = post_rows(viewer['id'] if viewer else -1)
    return {'success': True, 'posts': items}


@app.route('/api/app/posts', methods=['POST'])
def app_create_post():
    user, error = require_app_user()
    if error:
        return error
    caption = str(request.form.get('caption', '')).strip()
    location = str(request.form.get('location', '')).strip()[:80]
    pet_id = request.form.get('pet_id', type=int)
    if len(caption) > 500:
        return {'success': False, 'message': '文案最多 500 字'}, 400
    media_url = str(request.form.get('media_url', '')).strip()
    media_kind = str(request.form.get('media_kind', '')).strip()
    if media_url and not media_url.startswith(PETGIF_STORAGE_PUBLIC):
        media_url = ''
    if media_url:
        media_kind = 'gif' if media_kind == 'gif' else 'image'
        filename = None
    else:
        media_kind = None
        image = request.files.get('image')
        if image is None:
            return {'success': False, 'message': '请选择一张照片'}, 400
        try:
            filename, _ = save_upload_image(image, 'post')
        except ValueError as exc:
            return {'success': False, 'message': str(exc)}, 400
    if pet_id:
        with app_connect() as db:
            owned = db.execute('SELECT 1 FROM pets WHERE id=? AND user_id=?', (pet_id, user['id'])).fetchone()
        if not owned:
            pet_id = None
    created_at = datetime.now(timezone.utc).isoformat()
    with app_connect() as db:
        post_id = db.execute('INSERT INTO posts(user_id,pet_id,image_path,caption,location_text,created_at,media_url,media_kind) VALUES(?,?,?,?,?,?,?,?)',
                             (user['id'], pet_id, filename or '', caption, location, created_at, media_url or None, media_kind or None)).lastrowid
    return {'success': True, 'postId': post_id}, 201


@app.route('/api/app/posts/<int:post_id>', methods=['DELETE'])
def app_delete_post(post_id):
    user, error = require_app_user()
    if error:
        return error
    with app_connect() as db:
        row = db.execute('SELECT user_id, image_path FROM posts WHERE id=?', (post_id,)).fetchone()
        if not row:
            return {'success': False, 'message': '帖子不存在'}, 404
        if row['user_id'] != user['id']:
            return {'success': False, 'message': '只能删除自己的帖子'}, 403
        db.execute('DELETE FROM comments WHERE post_id=?', (post_id,))
        db.execute('DELETE FROM post_likes WHERE post_id=?', (post_id,))
        db.execute('DELETE FROM posts WHERE id=?', (post_id,))
    try:
        os.remove(os.path.join(UPLOADS_DIR, row['image_path']))
    except OSError:
        pass
    return {'success': True}


@app.route('/api/app/posts/<int:post_id>/like', methods=['POST'])
def app_like_post(post_id):
    user, error = require_app_user()
    if error:
        return error
    created_at = datetime.now(timezone.utc).isoformat()
    with app_connect() as db:
        if db.execute('SELECT 1 FROM post_likes WHERE post_id=? AND user_id=?', (post_id, user['id'])).fetchone():
            db.execute('DELETE FROM post_likes WHERE post_id=? AND user_id=?', (post_id, user['id']))
            liked = False
        else:
            db.execute('INSERT OR IGNORE INTO post_likes(post_id,user_id,created_at) VALUES(?,?,?)',
                       (post_id, user['id'], created_at))
            liked = True
        likes = db.execute('SELECT COUNT(*) FROM post_likes WHERE post_id=?', (post_id,)).fetchone()[0]
    return {'success': True, 'liked': liked, 'likes': likes}


@app.route('/api/app/posts/<int:post_id>/comments', methods=['GET', 'POST'])
def app_post_comments(post_id):
    if request.method == 'GET':
        with app_connect() as db:
            rows = db.execute('''SELECT c.id,c.body,c.created_at,u.id AS user_id,u.display_name,u.avatar_emoji
                FROM comments c JOIN users u ON u.id=c.user_id WHERE c.post_id=? ORDER BY c.id LIMIT 200''',
                              (post_id,)).fetchall()
        return {'success': True, 'comments': [dict(row) for row in rows]}
    user, error = require_app_user()
    if error:
        return error
    body = str((request.get_json(silent=True) or {}).get('body', '')).strip()
    if not body or len(body) > 300:
        return {'success': False, 'message': '评论需为 1—300 字'}, 400
    with app_connect() as db:
        if not db.execute('SELECT 1 FROM posts WHERE id=?', (post_id,)).fetchone():
            return {'success': False, 'message': '帖子不存在'}, 404
        db.execute('INSERT INTO comments(post_id,user_id,body,created_at) VALUES(?,?,?,?)',
                   (post_id, user['id'], body, datetime.now(timezone.utc).isoformat()))
    return {'success': True}, 201


@app.route('/api/app/users/<int:user_id>')
def app_user_profile(user_id):
    viewer = app_user_from_request()
    with app_connect() as db:
        row = db.execute('SELECT id,username,display_name,avatar_emoji,bio,created_at FROM users WHERE id=?', (user_id,)).fetchone()
        if not row:
            return {'success': False, 'message': '用户不存在'}, 404
        profile = dict(row)
        profile['posts_count'] = db.execute('SELECT COUNT(*) FROM posts WHERE user_id=?', (user_id,)).fetchone()[0]
        profile['followers'] = db.execute('SELECT COUNT(*) FROM follows WHERE followee_id=?', (user_id,)).fetchone()[0]
        profile['following'] = db.execute('SELECT COUNT(*) FROM follows WHERE follower_id=?', (user_id,)).fetchone()[0]
        profile['is_following'] = bool(viewer) and bool(db.execute(
            'SELECT 1 FROM follows WHERE follower_id=? AND followee_id=?', (viewer['id'], user_id)).fetchone())
        pets = [dict(r) for r in db.execute('SELECT id,name,species,breed,avatar_emoji,device_id FROM pets WHERE user_id=? ORDER BY id', (user_id,)).fetchall()]
    for pet in pets:
        telemetry = pet_latest_telemetry(pet['device_id'])
        pet['batteryPercent'] = telemetry.get('batteryPercent') if telemetry else None
        pet['online'] = telemetry.get('online') if telemetry else False
    profile['pets'] = pets
    profile['displayName'] = profile.pop('display_name')
    profile['avatarEmoji'] = profile.pop('avatar_emoji')
    return {'success': True, 'profile': profile}


@app.route('/api/app/users/<int:user_id>/followers')
def app_user_followers(user_id):
    viewer = app_user_from_request()
    viewer_id = viewer['id'] if viewer else -1
    with app_connect() as db:
        rows = db.execute('''SELECT u.id,u.display_name,u.avatar_emoji,
            EXISTS(SELECT 1 FROM follows f2 WHERE f2.follower_id=? AND f2.followee_id=u.id) AS is_following
            FROM follows f JOIN users u ON u.id=f.follower_id WHERE f.followee_id=? ORDER BY f.created_at DESC LIMIT 200''',
                          (viewer_id, user_id)).fetchall()
    return {'success': True, 'users': [dict(row) for row in rows]}


@app.route('/api/app/users/<int:user_id>/following')
def app_user_following(user_id):
    viewer = app_user_from_request()
    viewer_id = viewer['id'] if viewer else -1
    with app_connect() as db:
        rows = db.execute('''SELECT u.id,u.display_name,u.avatar_emoji,
            EXISTS(SELECT 1 FROM follows f2 WHERE f2.follower_id=? AND f2.followee_id=u.id) AS is_following
            FROM follows f JOIN users u ON u.id=f.followee_id WHERE f.follower_id=? ORDER BY f.created_at DESC LIMIT 200''',
                          (viewer_id, user_id)).fetchall()
    return {'success': True, 'users': [dict(row) for row in rows]}


@app.route('/api/app/users/<int:user_id>/follow', methods=['POST'])
def app_follow_user(user_id):
    user, error = require_app_user()
    if error:
        return error
    if user['id'] == user_id:
        return {'success': False, 'message': '不能关注自己'}, 400
    with app_connect() as db:
        if not db.execute('SELECT 1 FROM users WHERE id=?', (user_id,)).fetchone():
            return {'success': False, 'message': '用户不存在'}, 404
        if db.execute('SELECT 1 FROM follows WHERE follower_id=? AND followee_id=?', (user['id'], user_id)).fetchone():
            db.execute('DELETE FROM follows WHERE follower_id=? AND followee_id=?', (user['id'], user_id))
            following = False
        else:
            db.execute('INSERT OR IGNORE INTO follows(follower_id,followee_id,created_at) VALUES(?,?,?)',
                       (user['id'], user_id, datetime.now(timezone.utc).isoformat()))
            following = True
    return {'success': True, 'following': following}


@app.route('/uploads/<path:filename>')
def app_uploads(filename):
    return send_from_directory(UPLOADS_DIR, filename)


@app.route('/action')
@app.route('/action/')
def action_dashboard():
    return send_from_directory(ACTION_DASHBOARD_DIR, 'index.html')


@app.route('/')
def index():
    return send_from_directory(APP_DIR, 'dashboard.html')

@app.route('/<path:path>')
def serve_spa(path):
    full = os.path.join(STATIC_DIR, path)
    if os.path.isfile(full):
        return send_from_directory(STATIC_DIR, path)
    return send_from_directory(APP_DIR, 'dashboard.html')

if __name__ == '__main__':
    print(f'[INIT] PetSense  MQTT:{MQTT_BROKER}:{MQTT_PORT}  HTTP:0.0.0.0:{HTTP_PORT}')
    from werkzeug.serving import run_simple
    run_simple('0.0.0.0', HTTP_PORT, app, threaded=True)
