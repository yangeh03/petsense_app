"""MQTT 接入：订阅传感器主题、展开批量帧、落盘 readings、驱动下游钩子并广播。

collection_store（采集 API 的存储实例）也在这里装配——
它需要 MQTT client 作为命令下发通道。
"""

import json
import os
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from sqlalchemy import delete, func, insert, select, update

from petsense import config, stats
from petsense.actions import service as actions_service
from petsense.collection.api import CollectionStore
from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models import Device, Reading
from petsense.experiments import recorder
from petsense.telemetry import audio as audio_module
from petsense.telemetry import edge
from petsense.telemetry.realtime import broadcast_packet

_trim_counter = 0
_last_seen_updates = {}

SENSOR_PREFIX = 'petsense/sensor/'
# sensor/ 前缀下不属于"设备遥测"的专用 topic（其余尾段即设备 ID）
SENSOR_RESERVED_TAILS = {'clock_v2', 'collection_v2', 'collection_status', 'audio_wav', 'audio_file', 'edge_v1'}


def sensor_device(topic):
    """从 topic 提取设备 ID；非设备遥测 topic 返回 None。"""
    if not topic.startswith(SENSOR_PREFIX):
        return None
    tail = topic[len(SENSOR_PREFIX):]
    if not tail or '/' in tail or tail in SENSOR_RESERVED_TAILS:
        return None
    return tail


def _touch_device_last_seen(device_id):
    """每设备 60 秒节流更新 devices.last_seen_at（在线判定数据源）。"""
    if not device_id:
        return
    now = utc_now()
    last = _last_seen_updates.get(device_id)
    if last is not None and (now - last).total_seconds() < 60:
        return
    _last_seen_updates[device_id] = now
    try:
        with session_scope() as s:
            s.execute(update(Device).where(Device.device_id == device_id).values(last_seen_at=now))
    except Exception as error:
        print(f'[DEVICE] last_seen update failed: {error}')


def store(topic, payload, device_id=None):
    """落库一条原始帧，返回 aware UTC 时间（调用方用 iso_z() 序列化）。"""
    received = utc_now()
    with session_scope() as s:
        s.execute(insert(Reading).values(topic=topic, payload=payload,
                                         received_at=received, device_id=device_id))
    global _trim_counter
    _trim_counter += 1
    if _trim_counter >= 500:
        # 水位裁剪：均摊 O(1)，语义等价于"保留最近 MAX_HISTORY 条"（容差 ≤500）
        _trim_counter = 0
        with session_scope() as s:
            max_id = s.execute(select(func.max(Reading.id))).scalar_one()
            if max_id is not None:
                s.execute(delete(Reading).where(Reading.id <= max_id - config.MAX_HISTORY))
    return received


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


mqtt_client = mqtt.Client(client_id='petsense-server')
mqtt_client.username_pw_set(config.MQTT_USER, config.MQTT_PASS)


def publish_collection_command(device, payload):
    result = mqtt_client.publish(f'petsense/device/{device}/command', json.dumps(payload, separators=(',', ':')), qos=1, retain=False)
    return result.rc == mqtt.MQTT_ERR_SUCCESS


_collection_token_file = os.path.join(os.path.expanduser('~'), '.petsense-collection-token')
collection_store = CollectionStore(config.COLLECTION_ROOT,
    open(_collection_token_file).read().strip() if os.path.isfile(_collection_token_file) else '',
    publish_collection_command)


def publish_control_command(device_id, command):
    """发布设备控制命令到 petsense/device/<device_id>/command（qos1，失败重连一次）。"""
    topic = f'petsense/device/{device_id}/command'
    payload = json.dumps(command, separators=(',', ':'))
    info = mqtt_client.publish(topic, payload, qos=1, retain=False)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        try:
            mqtt_client.reconnect()
            import time
            time.sleep(0.2)
            info = mqtt_client.publish(topic, payload, qos=1, retain=False)
        except Exception as e:
            print(f'[MQTT] control publish reconnect failed: {e}')
    return payload, info.rc == mqtt.MQTT_ERR_SUCCESS


def on_connect(client, userdata, flags, rc):
    print(f'[MQTT] connected rc={rc}')
    client.subscribe(config.MQTT_TOPIC)


def on_message(client, userdata, msg):
    if msg.topic == edge.TOPIC:
        try:
            if len(msg.payload) > 65536:
                raise ValueError('Oversize edge summary')
            payload = json.loads(msg.payload)
            device_id = edge.validate(payload)
            serialized = json.dumps(payload, allow_nan=False, separators=(',', ':'))
            ts = store(msg.topic, serialized, device_id)
            _touch_device_last_seen(device_id)
            broadcast_packet(json.dumps({'topic': msg.topic, 'payload': serialized,
                                         'received_at': iso_z(ts)}), device_id)
        except Exception as error:
            print(f'[EDGE] rejected: {error}')
        return
    if msg.topic in ('petsense/sensor/clock_v2', 'petsense/sensor/collection_v2', 'petsense/sensor/collection_status'):
        try:
            collection_store.ingest(msg.topic, json.loads(msg.payload))
        except Exception as error:
            print(f'[COLLECTION] rejected without ACK: {error}')
        return
    if msg.topic == 'petsense/sensor/audio_wav':
        summary = json.dumps({'binary_bytes': len(msg.payload), 'kind': 'audio_wav'})
        store(msg.topic, summary)
        try:
            result = audio_module.handle_audio_wav_binary(msg.payload)
            if result:
                broadcast_packet(json.dumps(result))
        except Exception as e:
            print(f'[AUDIO] Binary parse error: {e}')
        return

    payload_str = msg.payload.decode(errors='replace')
    try:
        payload_dict = json.loads(payload_str)
    except Exception:
        payload_dict = None

    device_id = sensor_device(msg.topic)

    if device_id and isinstance(payload_dict, dict):
        frames = expand_sensor_batch(payload_dict)
        if frames:
            for frame in frames:
                actions_service.enrich_frame_with_action(frame)
                frame_payload = json.dumps(frame, ensure_ascii=False, separators=(',', ':'))
                ts = store(msg.topic, frame_payload, device_id)
                packet = json.dumps({'topic': msg.topic, 'payload': frame_payload, 'received_at': iso_z(ts)})
                try:
                    recorder.append_cloud_experiment_frame(msg.topic, frame, iso_z(ts), device_id)
                except Exception as e:
                    print(f'[EXPERIMENT] Cloud batch frame write error: {e}')
                try:
                    stats.update_daily_stats(device_id, frame, ts)
                    stats.remember_uwb(frame, iso_z(ts), device_id)
                except Exception as e:
                    print(f'[APP-STATS] batch hook error: {e}')
                broadcast_packet(packet, device_id)
            return

    if device_id and isinstance(payload_dict, dict):
        actions_service.enrich_frame_with_action(payload_dict)
        payload_str = json.dumps(payload_dict, ensure_ascii=False, separators=(',', ':'))

    ts = store(msg.topic, payload_str, device_id)
    packet = json.dumps({'topic': msg.topic, 'payload': payload_str, 'received_at': iso_z(ts)})
    if device_id and isinstance(payload_dict, dict):
        try:
            recorder.append_cloud_experiment_frame(msg.topic, payload_dict, iso_z(ts), device_id)
        except Exception as e:
            print(f'[EXPERIMENT] Cloud frame write error: {e}')
        try:
            stats.update_daily_stats(device_id, payload_dict, ts)
            stats.remember_uwb(payload_dict, iso_z(ts), device_id)
        except Exception as e:
            print(f'[APP-STATS] frame hook error: {e}')

    # 处理 audio_file topic：重组分片
    if msg.topic == 'petsense/sensor/audio_file':
        try:
            result = audio_module.handle_audio_file_chunk(payload_dict or {})
            if result:
                broadcast_packet(json.dumps(result))
        except Exception as e:
            print(f'[AUDIO] Parse error: {e}')
        return

    _touch_device_last_seen(device_id)
    # 所有消息都广播原始数据（传感器 + 音频原始分片）；WS 侧按设备归属过滤
    broadcast_packet(packet, device_id)


mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message


def start_mqtt():
    if os.environ.get('PETSENSE_MQTT_DISABLED') == '1':
        print('[INIT] PETSENSE_MQTT_DISABLED=1，跳过 MQTT 接入（本地开发模式）')
        return
    mqtt_client.connect(config.MQTT_BROKER, config.MQTT_PORT, 60)
    mqtt_client.loop_start()
