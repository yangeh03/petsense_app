"""Opt-in hardware test: short recording, stop, then CRC readback of that file."""
import json
import re
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt

root = Path(__file__).resolve().parents[1]
password = re.search(r'#define\s+PETSENSE_MQTT_PASSWORD\s+"([^"]+)"',
                     (root / 'edge_firmware/secrets.h').read_text())[1]
events = []
session = 'imu-verify-' + uuid.uuid4().hex[:12]
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='verify-' + uuid.uuid4().hex[:10])
client.username_pw_set('petsense-device', password)
def message(c, u, msg):
    data = json.loads(msg.payload)
    if data.get('device') == 'petsense-esp32-001':
        events.append(data)
client.on_message = message
client.connect('81.71.71.31', 1883, 30)
client.subscribe('petsense/sensor/collection_status', 1)
client.loop_start()
def wait(predicate, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if events and predicate(events[-1]):
            return events[-1]
        time.sleep(.25)
    raise TimeoutError('Device status deadline exceeded')
def send(action):
    client.publish('petsense/device/petsense-esp32-001/command', json.dumps({
        'type': 'collection_' + action, 'session': session, 'request_id': session + '-' + action,
    }), qos=1).wait_for_publish(5)
started = False
try:
    status = wait(lambda d: d['frame'].get('actionModelVersion') == 'edge-imu-1s-5class-mapped-20261125'
                  and d['frame'].get('utc_us'), 150)
    if status['frame']['capture_active']:
        raise RuntimeError('Existing capture; refusing to interrupt')
    started = True
    send('start')
    wait(lambda d: d['frame'].get('session_id') == session and d['frame']['capture_active'], 20)
    time.sleep(12)
    send('stop')
    stop = wait(lambda d: not d['frame']['capture_active'] and not d['frame']['storage_draining']
                and (d['frame'].get('command_ack') or {}).get('request_id') == session + '-stop', 20)
    send('audit')
    status = wait(lambda d: any(session in f['path'] for f in (d.get('audit') or {}).get('files', [])), 40)
    report = status['audit']['files'][0]
    assert report['rows'] == stop['frame']['persisted_rows'] and report['rows'] >= 100, report
    assert report['bytes'] == report['valid_bytes'], report
    assert all(report[k] == 0 for k in ['gaps', 'errors', 'missing_imu', 'timing_errors']), report
    assert report['predictions'] >= 10, report
    print('PASS', json.dumps(report), flush=True)
finally:
    if started:
        send('stop')
        wait(lambda d: not d['frame']['capture_active'] and not d['frame']['storage_draining'], 20)
    client.disconnect()
    client.loop_stop()
    out = root.parent / ('sd-verification-' + session + '.json')
    out.write_text(json.dumps(events, indent=2), encoding='utf-8')
    print('Evidence:', out, flush=True)
