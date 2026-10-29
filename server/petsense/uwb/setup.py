"""Owner-scoped touch calibration, boundary capture and map onboarding."""
import json
import csv
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
from .engine import PositionEngine
from .maps import EngineConfig
from .measurement import MeasurementFrame
from . import setup_jobs

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
        self.jobs = set()

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
        if state['phase'] == 'walking' and identity not in self.active:
            enough = state['walk']['points'] >= 30 and state['walk']['lengthM'] >= 3
            state['walk']['endedAt'] = self.clock()
            state.update(phase='walk_ready' if enough else 'anchors_ready',
                         message='服务重启已停止绕边采集，原始记录已保留，请检查或重新采集')
            self.save(identity, state)
        if state['phase'] == 'walking' and self.clock() - state['walk']['startedAt'] > 1800:
            self.stop_walk(identity, state)
        if state['phase'] == 'processing' and state.get('jobId') not in self.jobs:
            state.update(phase='walk_ready', message='后台任务中断，原始数据已保留，请重新发起对齐')
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
        if state['phase'] in ('touching', 'walking', 'processing'):
            raise ValueError('正在采集，请先等待结束或取消')
        if any(state['rows'][i] is None for i in range(index)):
            raise ValueError('请按 A0、A1、A2 顺序完成标定')
        self.active[identity] = {'samples': [], 'seen': set()}
        state.update(phase='touching', anchors=None,
                     capture={'anchor': index, 'endsAt': self.clock() + 6, 'samples': 0},
                     message=f'请保持 Tag 贴近 A{index}，不要移动')
        state.update(walk=None, alignment=None, published=None)
        state['rows'][index:] = [None] * (3 - index)
        self.save(identity, state)

    def start_walk(self, identity, state):
        if not state.get('anchors') or state['phase'] in ('touching', 'walking', 'processing'):
            raise ValueError('请先完成三个基站标定，并等待当前任务结束')
        walk_id = uuid.uuid4().hex
        folder = self.folder(identity) / 'walks' / walk_id
        folder.mkdir(parents=True)
        with (folder / 'trail.csv').open('w', encoding='utf-8', newline='') as stream:
            csv.writer(stream).writerow(['time_s', 'x_m', 'y_m', 'raw_x_m', 'raw_y_m',
                'd0_m', 'd1_m', 'd2_m', 'seq', 'tagid', 'valid_fix', 'pos_updated'])
        anchors = tuple(tuple(xy) for xy in state['anchors'])
        engine = PositionEngine(EngineConfig((0, 1, 2), anchors,
            ((-100, -100), (100, -100), (100, 100), (-100, 100)),
            1, ((1, 0), (0, 1)), (0, 0), 2), state['tagId'])
        self.active[identity] = {'engine': engine, 'seen': set(), 'last': None}
        state.update(phase='walking', alignment=None, published=None,
            walk={'id': walk_id, 'startedAt': self.clock(), 'points': 0, 'rejected': 0,
                  'trail': [], 'lastAt': None, 'closureM': None, 'lengthM': 0},
            message='请沿真实场地边缘缓慢走一圈，覆盖凹凸转角，最后回到起点附近')
        self.save(identity, state)

    def stop_walk(self, identity, state):
        if state['phase'] != 'walking':
            raise ValueError('当前没有正在进行的绕边采集')
        self.active.pop(identity, None)
        walk = state['walk']
        walk['endedAt'] = self.clock()
        enough = walk['points'] >= 30 and walk['lengthM'] >= 3
        state.update(phase='walk_ready' if enough else 'anchors_ready',
            message='绕边轨迹已保存，请导入点云和确认地面参数，然后开始自动对齐' if enough
            else '有效轨迹不足（至少 30 点、3 米），原始记录已保留，请重新采集')
        self.save(identity, state)

    def record_walk(self, identity, state, uwb, distances, mask):
        walk = state['walk']
        if self.clock() - walk['startedAt'] > 1800 or walk['points'] >= 20000:
            self.stop_walk(identity, state)
            return
        if any(not mask & (1 << i) or type(distances[i]) not in (int, float)
               or not math.isfinite(distances[i]) or not 50 <= distances[i] <= 30000 for i in range(3)):
            walk['rejected'] += 1
            self.save(identity, state)
            return
        active = self.active[identity]
        key = (uwb['timer'], uwb['sequence'])
        if key in active['seen']:
            return
        active['seen'].add(key)
        now = self.clock()
        previous = active['engine'].position
        if active['last'] is not None and now - active['last'] > 2:
            # Do not smooth across a telemetry gap or module restart.
            active['engine'] = PositionEngine(active['engine'].config, state['tagId'])
        active['last'] = now
        result = active['engine'].update(MeasurementFrame({i: distances[i] / 1000 for i in range(3)},
            tag_id=state['tagId'], sequence=uwb['sequence'], device_timer=uwb['timer']), now_monotonic=now)
        folder = self.folder(identity) / 'walks' / walk['id']
        with (folder / 'ranges.ndjson').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'time': now, 'uwb': uwb}, allow_nan=False) + '\n')
        position, raw = result['position_uwb_m'], result['raw_position_uwb_m']
        valid = result['status'] == 'ok' and position is not None
        if position is not None and raw is not None:
            updated = previous != (position['x'], position['y'])
            with (folder / 'trail.csv').open('a', encoding='utf-8', newline='') as stream:
                csv.writer(stream).writerow([now - walk['startedAt'], position['x'], position['y'],
                    raw['x'], raw['y'], *[result['distances_m'][f'A{i}']['filtered'] for i in range(3)],
                    uwb['sequence'], state['tagId'], int(valid), int(updated)])
        if valid:
            xy = [position['x'], position['y']]
            if walk['trail']:
                walk['lengthM'] += math.dist(walk['trail'][-1], xy)
            walk['points'] += 1
            walk['lastAt'] = now
            walk['trail'].append(xy)
            walk['first'] = walk.get('first', xy)
            walk['closureM'] = math.dist(walk['first'], xy)
            # Keep status small; the complete unsampled trajectory remains in CSV.
            if len(walk['trail']) > 600:
                walk['trail'] = walk['trail'][::2]
        else:
            walk['rejected'] += 1
        self.save(identity, state)

    def public(self, identity, state):
        if state is None:
            return None
        state = dict(state)
        inputs = self.folder(identity) / 'inputs'
        state['inputs'] = {key: {'present': (inputs / name).is_file(),
                              'path': str(inputs / name),
                              'url': f'/api/app/uwb/setup/input/{key}?deviceId={identity[0]}'}
                          for key, name in [('pointcloud', 'Pointcloud.ply'), ('floor', 'confirmed-floor.json')]}
        state['inputs']['alignment'] = {'present': (inputs / 'alignment.json').is_file(),
            'path': str(inputs / 'alignment.json'),
            'url': f'/api/app/uwb/setup/input/alignment?deviceId={identity[0]}'}
        if state.get('walk'):
            walk = dict(state['walk'])
            walk['ageMs'] = round((self.clock() - walk['lastAt']) * 1000) if walk.get('lastAt') else None
            walk['elapsedMs'] = round((walk.get('endedAt', self.clock()) - walk['startedAt']) * 1000)
            state['walk'] = walk
        return state

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
            if not state or state['phase'] not in ('touching', 'walking'):
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
            if state['phase'] == 'walking':
                self.record_walk(identity, state, uwb, distances, mask)
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
@bp.post('/walk/start')
@bp.post('/walk/stop')
@bp.post('/align')
@bp.post('/publish')
@bp.get('/preview')
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
                action = request.path.removeprefix('/api/app/uwb/setup').strip('/') or 'setup'
                if action == 'setup':
                    state = store.create(identity, body.get('tagId', 0))
                elif not state:
                    raise ValueError('请先创建标定任务')
                elif action == 'touch':
                    store.touch(identity, state, body.get('anchor'))
                elif action == 'walk/start':
                    store.start_walk(identity, state)
                elif action == 'walk/stop':
                    store.stop_walk(identity, state)
                elif action == 'align':
                    setup_jobs.start(store, identity, state, device_identity)
                elif action == 'publish':
                    setup_jobs.publish(store, identity, state, body.get('candidate'), body.get('jobId'), body.get('confirmed'))
                elif action == 'cancel':
                    if state['phase'] != 'touching':
                        raise ValueError('当前没有正在进行的触碰采集')
                    store.active.pop(identity, None)
                    state.update(phase='touch_ready', capture=None, message='已取消采集，可重新触碰')
                    store.save(identity, state)
            if request.path.endswith('/preview'):
                if not state:
                    raise ValueError('请先创建标定任务')
                if request.args.get('jobId') != state.get('jobId'):
                    raise ValueError('配准任务已变化，请刷新预览')
                source, candidate = setup_jobs.preview(store, identity, state, request.args.get('candidate'))
                return {'jobId': state['jobId'], 'candidate': candidate,
                        'map': setup_jobs.MapBundle(source).public()}, 200, {'Cache-Control': 'no-store'}
            return {'session': store.public(identity, state)}, 200, {'Cache-Control': 'no-store'}
        except ValueError as error:
            return {'message': str(error)}, 400


@bp.put('/input/<kind>')
def upload_input(kind):
    user, error = require_app_user()
    if error:
        return error
    device = request.args.get('deviceId')
    if not isinstance(device, str):
        return {'message': '请指定设备'}, 400
    identity = device_identity(device, user['id'])
    if not identity:
        return {'message': '无权操作该设备'}, 403
    slots = {'pointcloud': ('Pointcloud.ply', 2_000_000_000), 'floor': ('confirmed-floor.json', 64000),
             'alignment': ('alignment.json', 64000)}
    if kind not in slots:
        return {'message': '未知文件入口'}, 400
    with store.lock:
        state = store.read(identity)
        if not state or state['phase'] == 'processing':
            return {'message': '请先创建任务，并等待点云处理结束'}, 409
        folder = store.folder(identity) / 'inputs'
    name, limit = slots[kind]
    temporary = folder / (uuid.uuid4().hex + '.upload')
    try:
        count = 0
        with temporary.open('wb') as output:
            while chunk := request.stream.read(1024 * 1024):
                count += len(chunk)
                if count > limit:
                    return {'message': '文件超过大小限制'}, 413
                output.write(chunk)
        if not count:
            return {'message': '文件为空'}, 400
        with store.lock:
            current = store.read(identity)
            if current['phase'] == 'processing' or device_identity(device, user['id']) != identity:
                return {'message': '任务或设备归属已变化，请重试'}, 409
            temporary.replace(folder / name)
            if current['phase'] in ('alignment_review', 'published'):
                current.update(phase='walk_ready', alignment=None,
                    message='人工输入已更新，请重新生成对齐；现有地图会保留到新结果确认启用')
                store.save(identity, current)
        return {'success': True, 'bytes': count}
    finally:
        temporary.unlink(missing_ok=True)
