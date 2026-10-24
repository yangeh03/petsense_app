"""集中配置：路径、MQTT、端口与业务常量。

运行时数据根目录（数据库、上传、音频、实验数据）默认取包目录的上一级，
即 server/ 安装根；生产上包位于 /home/ubuntu/petsense-server/petsense/，
数据目录与旧版单文件布局完全一致，可用 PETSENSE_ROOT 覆盖。
"""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
RUNTIME_ROOT = Path(os.environ.get('PETSENSE_ROOT', str(PACKAGE_DIR.parent)))

MQTT_BROKER = 'localhost'
MQTT_PORT = 1883
MQTT_USER = 'petsense-device'
MQTT_PASS = ('' if os.environ.get('PETSENSE_MQTT_DISABLED') == '1'
             else open(os.environ.get('PETSENSE_MQTT_PASSWORD_FILE',
                                      os.path.expanduser('~/.petsense-mqtt-password'))).read().strip())
MQTT_TOPIC = 'petsense/sensor/#'
CONTROL_TOPIC = 'petsense/device/esp32-001/command'
HTTP_PORT = 8080

STATIC_DIR = str(RUNTIME_ROOT / 'static')
DATABASE_URL = os.environ.get('PETSENSE_DATABASE_URL',
                              f'sqlite:///{RUNTIME_ROOT / "petsense.db"}')
AUDIO_DIR = str(RUNTIME_ROOT / 'audio_files')
CLOUD_DATA_DIR = str(RUNTIME_ROOT / 'cloud_data')
ACTION_DASHBOARD_DIR = str(RUNTIME_ROOT / 'action-dashboard')
UPLOADS_DIR = str(RUNTIME_ROOT / 'uploads')
COLLECTION_ROOT = str(RUNTIME_ROOT / 'collection_data')
ACTION_MODEL_PATH = os.environ.get('PETSENSE_ACTION_MODEL',
                                   str(RUNTIME_ROOT / 'models' / 'random_forest_1s.joblib'))
MAX_HISTORY = 10000

# ── 在线检测 / 心跳参数（与 App 端对齐：App 静默看门狗 25s，帧新鲜窗口 10s）──
DEVICE_ONLINE_TIMEOUT_S = 60   # 设备在线判定：最近一帧距今的窗口
WS_PING_INTERVAL_S = 10        # /ws 应用层心跳间隔，兼做僵尸连接清理

CONTROL_DEBOUNCE_SEC = 0.8

# ── App 服务常量 ──
APP_TOKEN_HEADER = 'X-Petsense-Session'
APP_SESSION_DAYS = 30
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
STILL_DELTA_G = 0.10
ACTIVE_DELTA_G = 0.25
SLEEP_UTC_HOURS = set(range(14, 24)) | {0}  # 北京时间 22:00–08:00

# ── PetGIF 外部服务（key 只存服务端）──
PETGIF_BASE = 'https://qioyo.site/petsenseIMG/api'
PETGIF_STORAGE_PUBLIC = 'https://qioyo.site/petsenseIMG/storage/'
