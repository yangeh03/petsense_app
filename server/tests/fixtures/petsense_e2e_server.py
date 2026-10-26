"""Disposable PetSense backend for simulator E2E; exposes a local frame injector."""
import json
import os
import time
from datetime import datetime, timezone

from petsense.app import create_app
from petsense.telemetry import realtime

app = create_app(start_background=False)


@app.post('/__test__/frame')
def inject_frame():
    body = __import__('flask').request.get_json(silent=True) or {}
    device_id = str(body.get('deviceId', ''))
    if not device_id:
        return {'success': False, 'message': 'deviceId required'}, 400
    action = str(body.get('action', 'walking'))
    payload = {
        'deviceId': device_id,
        'ts_ms': int(time.time() * 1000),
        'source': 'esp32_mlp',
        'modelVersion': 'simulator-e2e',
        'action': {'label': action, 'confidence': 0.99},
        'health': {
            'valid': True, 'heartRate': 108, 'spo2': 98, 'bodyTemp': 38.4,
            'envTemp': 22, 'respiration': 24, 'microCir': 0, 'fatigue': 0,
            'hrvSdnn': 22, 'hrvRmssd': 18, 'rrInterval': 0, 'systolic': 0, 'diastolic': 0,
        },
        'battery': {'ok': True, 'found': True, 'percent': 87, 'voltage': 3.9},
    }
    packet = json.dumps({
        'topic': f'petsense/sensor/{device_id}',
        'payload': payload,
        'received_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    })
    realtime.broadcast_packet(packet, device_id)
    return {'success': True}


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PETSENSE_E2E_PORT', '8093')), threaded=True)
