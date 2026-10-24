#!/usr/bin/env python3
"""PetSense 服务端入口（systemd ExecStart 指向这里）。"""

from petsense import config
from petsense.app import create_app

app = create_app()

if __name__ == '__main__':
    print(f'[INIT] PetSense  MQTT:{config.MQTT_BROKER}:{config.MQTT_PORT}  HTTP:0.0.0.0:{config.HTTP_PORT}')
    from werkzeug.serving import run_simple
    run_simple('0.0.0.0', config.HTTP_PORT, app, threaded=True)
