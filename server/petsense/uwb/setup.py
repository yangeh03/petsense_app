"""Persistent, owner-scoped collection for the three-anchor onboarding workflow."""
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

import numpy as np
from flask import Blueprint, request

from petsense import config, db
from petsense.auth.routes import require_app_user
from petsense.db import iso_z
from petsense.db.models import Device
from .service import unsigned, MASK

bp = Blueprint('uwb_setup', __name__, url_prefix='/api/app/uwb/setup')


def solve_anchors(rows):
    """Classical MDS; fix A0 at origin, A1 on +x, A2 above x axis."""
    distances = np.asarray(rows, dtype=float)
    if distances.shape != (3, 3) or not np.isfinite(distances).all():
        raise ValueError('三个基站的测距不完整，请重采')
    if np.max(np.abs(distances - distances.T)) > .35:
        raise ValueError('两次触碰得到的距离相差超过 35 cm，请重采')
    distances = (distances + distances.T) / 2
    np.fill_diagonal(distances, 0)
    sides = distances[np.triu_indices(3, 1)]
    if min(sides) < .3 or 2 * max(sides) >= sum(sides) - .05:
        raise ValueError('基站过近或接近共线，请调整布置后重新标定')
    center = np.eye(3) - np.ones((3, 3)) / 3
    values, vectors = np.linalg.eigh(-.5 * center @ distances ** 2 @ center)
    if values[-2] <= 0 or values[-2] / values[-1] < .01:
        raise ValueError('基站接近共线，无法建立稳定的二维坐标系')
    xy = vectors[:, -2:] * np.sqrt(values[-2:])
    xy -= xy[0].copy()
    axis = xy[1] / np.linalg.norm(xy[1])
    xy = xy @ np.array([axis, [-axis[1], axis[0]]]).T
    if xy[2, 1] < 0:
        xy[:, 1] *= -1
    return xy.tolist()


class SetupStore:
    def __init__(self, root, clock=time.time):
        self.root = Path(root)
        self.clock = clock
        self.lock = threading.RLock()
        self.active = {}

    def folder(self, identity):
        # Identity comes from the DB, never from a client-provided filesystem path.
        key = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(identity)).hex
        return self.root / key

    def read(self, identity):
        path = self.folder(identity) / 'session.json'
        if not path.exists():
            return None
        state = json.loads(path.read_text(encoding='utf-8'))
        # A server restart cannot silently resume a physical sampling window.
        if state['phase'] == 'touching' and identity not in self.active:
            state.update(phase='touch_ready', message='采集中断，请重新触碰当前基站', capture=None)
            self.save(identity, state)
        self.finish(identity, state)
        return state

    def save(self, identity, state):
        folder = self.folder(identity)
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / 'session.tmp'
        temp.write_text(json.dumps(state, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temp.replace(folder / 'session.json')

    def create(self, identity, tag):
        if not unsigned(tag, 65535):
            raise ValueError('Tag ID 必须是 0–65535 的整数')
        old = self.read(identity)
        if old:
            return old  # Resume rather than accidentally destroy a previous survey.
        folder = self.folder(identity)
        inputs = folder / 'inputs'
        inputs.mkdir(parents=True, exist_ok=True)
        state = dict(id=uuid.uuid4().hex, deviceId=identity[0], tagId=tag,
                     phase='touch_ready', rows=[None] * 3, anchors=None, capture=None,
                     message='请固定三个基站，依次将 Tag 贴近 A0、A1、A2',
                     files={'pointcloud': str(inputs / 'Pointcloud.ply'),
                            'floor': str(inputs / 'confirmed-floor.json')})
        self.save(identity, state)
        return state

    def touch(self, identity, state, index):
        if type(index) is not int or index not in range(3):
            raise ValueError('请选择 A0、A1 或 A2')
        if state['phase'] == 'touching':
            raise ValueError('正在采集，请先等待结束或取消')
        if any(state['rows'][i] is None for i in range(index)):
            raise ValueError('请按 A0、A1、A2 顺序完成标定')
        self.active[identity] = {'samples': [], 'seen': set()}
        state.update(phase='touching', anchors=None,
                     capture={'anchor': index, 'endsAt': self.clock() + 6, 'samples': 0},
                     message=f'请保持 Tag 贴近 A{index}，不要移动')
        state['rows'][index:] = [None] * (3 - index)
        self.save(identity, state)

    def finish(self, identity, state):
        capture = state.get('capture')
        if state['phase'] != 'touching' or self.clock() < capture['endsAt']:
            return
        active = self.active.pop(identity, {'samples': []})
        samples = active['samples']
        state.update(phase='touch_ready', capture=None)
        if len(samples) < 3:
            state['message'] = '有效测距不足 3 帧，请保持贴近并重试；检查其余两个基站是否在线'
        else:
            values = np.asarray(samples)
            if np.max(np.ptp(values, axis=0)) > .4:
                state['message'] = '测距波动超过 40 cm，请保持静止并重试'
            else:
                index = capture['anchor']
                state['rows'][index] = np.median(values, axis=0).tolist()
                state['rows'][index][index] = 0
                state['message'] = f'A{index} 采集完成'
                if all(row is not None for row in state['rows']):
                    try:
                        state['anchors'] = solve_anchors(state['rows'])
                        state.update(phase='anchors_ready', message='三个基站标定完成，坐标单位为米')
                    except ValueError as error:
                        state['message'] = str(error)
        self.save(identity, state)

    def ingest(self, identity, frame, batch_age_ms=0):
        with self.lock:
            if identity not in self.active:
                return
            state = self.read(identity)
            if not state or state['phase'] != 'touching':
                return
            uwb = frame.get('uwb') or {}
            if (not isinstance(uwb, dict) or uwb.get('valid') is not True
                    or not unsigned(uwb.get('tagId'), 65535) or uwb.get('tagId') != state['tagId']):
                return
            stamp, received = frame.get('ts_ms'), uwb.get('receivedMs')
            timer, sequence = uwb.get('timer'), uwb.get('sequence')
            if not all(unsigned(v) for v in [stamp, received, timer]) or not unsigned(sequence, 255):
                return
            if ((stamp - received) & MASK) + batch_age_ms > 2000:
                return
            mask, distances = uwb.get('anchorMask'), uwb.get('distanceMm')
            if not unsigned(mask, 255) or not isinstance(distances, list) or len(distances) < 3:
                return
            index = state['capture']['anchor']
            # The touched anchor's own zero range need not be marked valid by BU03.
            if any(i != index and not mask & (1 << i) for i in range(3)):
                return
            if any(type(distances[i]) not in (int, float) or not math.isfinite(distances[i])
                   or not 50 <= distances[i] <= 30000 for i in range(3) if i != index):
                return
            if type(distances[index]) not in (int, float) or not 0 <= distances[index] <= 250:
                return
            active = self.active[identity]
            key = (timer, sequence)
            if key in active['seen'] or len(active['samples']) >= 120:
                return
            active['seen'].add(key)
            values = [distances[i] / 1000 if i != index else 0 for i in range(3)]
            active['samples'].append(values)
            with (self.folder(identity) / 'touch.ndjson').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'anchor': index, 'time': self.clock(), 'uwb': uwb}) + '\n')
            state['capture']['samples'] = len(active['samples'])
            self.save(identity, state)


store = SetupStore(os.environ.get('PETSENSE_UWB_SETUP_ROOT', str(config.RUNTIME_ROOT / 'uwb_setup')))


def device_identity(device_id, owner=None):
    with db.session_scope() as session:
        device = session.get(Device, device_id)
        if not device or device.owner_id is None or (owner is not None and device.owner_id != owner):
            return None
        return device.device_id, device.owner_id, iso_z(device.claimed_at)


def ingest(frame, device_id, batch_age_ms=0):
    try:
        with store.lock:
            if not any(key[0] == device_id for key in store.active):
                return
        identity = device_identity(device_id)
        with store.lock:
            for key in list(store.active):
                if key[0] == device_id and key != identity:
                    store.active.pop(key, None)
        if identity and frame.get('deviceId') == device_id:
            store.ingest(identity, frame, batch_age_ms)
    except (ValueError, KeyError, OSError, TypeError):
        # Survey failure must not interrupt live health telemetry.
        import logging
        logging.getLogger(__name__).exception('UWB setup collection failed')


@bp.route('', methods=['GET', 'POST'])
@bp.post('/touch')
@bp.post('/cancel')
def setup_route():
    user, error = require_app_user()
    if error:
        return error
    body = (request.get_json(silent=True) or {}) if request.method == 'POST' else {}
    if not isinstance(body, dict):
        return {'message': '请求格式错误'}, 400
    device = body.get('deviceId') or request.args.get('deviceId')
    if not isinstance(device, str):
        return {'message': '请先绑定设备'}, 400
    identity = device_identity(device, user['id'])
    if identity is None:
        return {'message': '无权操作该设备'}, 403
    with store.lock:
        try:
            state = store.read(identity)
            if request.method == 'POST':
                action = request.path.rsplit('/', 1)[-1]
                if action == 'setup':
                    state = store.create(identity, body.get('tagId', 0))
                elif not state:
                    raise ValueError('请先创建标定任务')
                elif action == 'touch':
                    store.touch(identity, state, body.get('anchor'))
                elif action == 'cancel':
                    if state['phase'] != 'touching':
                        raise ValueError('当前没有正在进行的触碰采集')
                    store.active.pop(identity, None)
                    state.update(phase='touch_ready', capture=None, message='已取消采集，可重新触碰')
                    store.save(identity, state)
            return {'session': state}, 200, {'Cache-Control': 'no-store'}
        except ValueError as error:
            return {'message': str(error)}, 400
