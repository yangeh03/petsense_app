#!/usr/bin/env python3
"""PetSense 设备模拟器：模拟 ESP32 向 MQTT broker 周期上报传感器帧。

部署在阿里云（与 mosquitto 同机），帧格式与真实设备一致，
server.py 会入库并通过 /ws 实时广播给 APP。

用法：nohup python3 device-simulator.py >/dev/null 2>&1 &
停止：pkill -f device-simulator.py
"""

import json
import random
import time

import paho.mqtt.client as mqtt

BROKER = 'localhost'
PORT = 1883
USER = 'petsense-device'
PASS = open('/root/.petsense-mqtt-password').read().strip()
TOPIC = 'petsense/sensor/esp32-001'
INTERVAL_SEC = 2.0

state = {'heartRate': 112.0, 'spo2': 97.5, 'bodyTemp': 38.6, 'respiration': 24.0, 'battery': 82.0}


def walk(key, low, high, step):
    state[key] = min(high, max(low, state[key] + random.uniform(-step, step)))


def build_frame():
    walk('heartRate', 88, 132, 3)
    walk('spo2', 94, 99.5, 0.4)
    walk('bodyTemp', 38.1, 39.1, 0.05)
    walk('respiration', 18, 30, 1)
    walk('battery', 5, 100, 0.02)

    return {
        'deviceId': 'esp32-001',
        'ts_ms': int(time.time() * 1000),
        'health': {
            'valid': True,
            'heartRate': round(state['heartRate']),
            'spo2': round(state['spo2']),
            'bodyTemp': round(state['bodyTemp'], 2),
            'envTemp': round(state['bodyTemp'] - 4, 2),
            'respiration': round(state['respiration']),
            'microCir': round(random.uniform(3, 5)),
            'fatigue': round(random.uniform(0, 20)),
            'hrvSdnn': round(random.uniform(28, 46)),
            'hrvRmssd': round(random.uniform(22, 37)),
            'rrInterval': round(60000 / state['heartRate']),
            'systolic': round(random.uniform(110, 122)),
            'diastolic': round(random.uniform(68, 78)),
        },
        'battery': {
            'ok': True,
            'found': True,
            'percent': round(state['battery'], 1),
            'voltage': round(random.uniform(3.7, 3.9), 3),
        },
    }


def main():
    client = mqtt.Client(client_id='petsense-simulator')
    client.username_pw_set(USER, PASS)
    client.connect(BROKER, PORT, 60)
    client.loop_start()
    print('simulator started, publishing to', TOPIC, f'every {INTERVAL_SEC}s')
    while True:
        payload = json.dumps(build_frame(), ensure_ascii=False, separators=(',', ':'))
        info = client.publish(TOPIC, payload, qos=0)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            print('publish failed rc=', info.rc, 'reconnecting...')
            try:
                client.reconnect()
            except Exception as exc:
                print('reconnect error:', exc)
        time.sleep(INTERVAL_SEC)


if __name__ == '__main__':
    main()
