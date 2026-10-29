"""Bounded subprocess jobs and atomic publication of reviewed survey maps."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import uuid

from petsense import config
from .maps import MapBundle
from .service import service

WORKER_SLOT = threading.Lock()


def input_signature(inputs):
    return {name: [path.stat().st_size, path.stat().st_mtime_ns] if path.is_file() else None
            for name in ('Pointcloud.ply', 'confirmed-floor.json', 'alignment.json')
            for path in [inputs / name]}


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def tools_root():
    supplied = os.environ.get('PETSENSE_UWB_MAP_TOOLS')
    if supplied:
        return Path(supplied)
    installed = config.RUNTIME_ROOT / 'map_tools'
    return installed if installed.is_dir() else Path(__file__).resolve().parents[3] / 'tools/offline-map'


def run_worker(job):
    python = os.environ.get('PETSENSE_UWB_MAP_PYTHON', sys.executable)
    script = tools_root() / 'setup_align.py'
    if not script.is_file():
        raise ValueError('点云处理工具未安装，请检查 PETSENSE_UWB_MAP_TOOLS')
    env = {**os.environ, 'PYTHONPATH': str(config.PACKAGE_DIR.parent),
           'MPLCONFIGDIR': str(job / 'matplotlib'), 'MPLBACKEND': 'Agg',
           'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    with (job / 'worker.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([python, str(script), str(job / 'job.json')],
                     stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=os.name != 'nt')
        try:
            code = process.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            if os.name != 'nt':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
            raise ValueError('点云处理超过 30 分钟，请检查输入规模和后台日志') from None
    if code:
        raise ValueError('点云或轨迹处理失败，请检查人工输入及 worker.log；可修正后重试')


def start(store, identity, state, identity_reader):
    if state['phase'] not in ('walk_ready', 'alignment_review', 'published'):
        raise ValueError('请先完成一圈绕边采集')
    inputs = store.folder(identity) / 'inputs'
    for name in ('Pointcloud.ply', 'confirmed-floor.json'):
        path = inputs / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            raise ValueError(f'请先将 {name} 放入后端预留地址：{path}')
    optional = inputs / 'alignment.json'
    if optional.exists() and (optional.is_symlink() or not optional.is_file() or optional.stat().st_size > 64000):
        raise ValueError('已确认变换必须是本任务目录内不超过 64 KB 的 JSON 文件')
    if (inputs / 'Pointcloud.ply').stat().st_size > 2_000_000_000 or (inputs / 'confirmed-floor.json').stat().st_size > 64000:
        raise ValueError('点云不得超过 2 GB，地面参数不得超过 64 KB')
    if not WORKER_SLOT.acquire(blocking=False):
        raise ValueError('已有点云任务正在运行，请稍后重试')
    job_id = uuid.uuid4().hex
    job = store.folder(identity) / 'jobs' / job_id
    try:
        job.mkdir(parents=True)
        write_json(job / 'job.json', {'mapId': 'survey-' + state['id'], 'version': 'v-' + job_id,
                                     'walkId': state['walk']['id']})
        write_json(job / 'anchors.json', {'anchors_xyz_m': {
            f'A{i}': xy + [0] for i, xy in enumerate(state['anchors'])}})
        store.jobs.add(job_id)
        state.update(phase='processing', jobId=job_id, alignment=None,
                     inputSignature=input_signature(inputs),
                     message='正在生成点云俯视图并配准绕边轨迹，可离开页面，稍后查看进度')
        store.save(identity, state)
    except BaseException:
        WORKER_SLOT.release()
        raise

    def work():
        try:
            # Snapshot inputs away from mutable manual input slots, outside the MQTT lock.
            for name in ('Pointcloud.ply', 'confirmed-floor.json'):
                shutil.copyfile(inputs / name, job / name)
            if (inputs / 'alignment.json').is_file():
                shutil.copyfile(inputs / 'alignment.json', job / 'alignment.json')
            if input_signature(inputs) != state['inputSignature']:
                raise ValueError('输入文件在拷贝过程中发生变化，请等待上传结束后重试')
            shutil.copyfile(store.folder(identity) / 'walks' / state['walk']['id'] / 'trail.csv', job / 'trail.csv')
            run_worker(job)
            result = json.loads((job / 'result.json').read_text(encoding='utf-8'))
            for candidate in result['candidates']:
                if candidate['id'] not in ('normal', 'mirror', 'grid', 'manual'):
                    raise ValueError('配准候选格式错误')
                MapBundle(job / ('bundle-' + candidate['id']))
            with store.lock:
                current = store.read(identity)
                if current['jobId'] == job_id and identity_reader(identity[0]) == identity:
                    current.update(phase='alignment_review', alignment=result, message=result['message'])
                    store.save(identity, current)
        except Exception as error:
            import logging
            logging.getLogger(__name__).exception('UWB alignment job failed')
            with store.lock:
                current = store.read(identity)
                if current and current.get('jobId') == job_id:
                    message = str(error) if isinstance(error, ValueError) else '后台处理失败，请检查任务日志后重试'
                    current.update(phase='walk_ready', message=message)
                    store.save(identity, current)
        finally:
            with store.lock:
                store.jobs.discard(job_id)
            WORKER_SLOT.release()

    try:
        threading.Thread(target=work, name='uwb-map-' + job_id[:8], daemon=True).start()
    except Exception:
        store.jobs.discard(job_id)
        WORKER_SLOT.release()
        state.update(phase='walk_ready', message='后台任务未能启动，请重试')
        store.save(identity, state)
        raise


def preview(store, identity, state, candidate_id):
    if state['phase'] not in ('alignment_review', 'published') or not state.get('alignment'):
        raise ValueError('配准结果尚未就绪')
    candidate = next((c for c in state['alignment']['candidates'] if c['id'] == candidate_id), None)
    if candidate is None:
        raise ValueError('请选择有效的配准候选')
    source = store.folder(identity) / 'jobs' / state['jobId'] / ('bundle-' + candidate_id)
    return source, candidate


def publish(store, identity, state, candidate_id, job_id, confirmed=False):
    if state.get('jobId') != job_id:
        raise ValueError('配准任务已变化，请刷新预览后重试')
    if state.get('inputSignature') and input_signature(store.folder(identity) / 'inputs') != state['inputSignature']:
        raise ValueError('人工输入文件已更新，请重新生成对齐后再启用')
    source, candidate = preview(store, identity, state, candidate_id)
    if confirmed is not True:
        raise ValueError('请先在 App 预览并确认方向、轮廓与尺度，再启用此结果')
    if not candidate['eligible']:
        raise ValueError('覆盖率或误差未达标，请重新绕边，不能启用此结果')
    # Use the positioning service's lock so no reader observes a partial binding update.
    with service.lock:
        path = service.registry.path
        root = path.parent
        root.mkdir(parents=True, exist_ok=True)
        directory = 'survey-' + state['jobId'] + '-' + candidate_id
        target = root / directory
        if not target.exists():
            staging = root / ('.staging-' + uuid.uuid4().hex)
            try:
                staging.mkdir()
                for name in ('map.json', 'floorplan.png'):
                    shutil.copyfile(source / name, staging / name)
                MapBundle(staging)
                staging.rename(target)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        bundle = MapBundle(target)
        bindings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'devices': {}}
        bindings['devices'][identity[0]] = {'directory': directory, 'tagId': state['tagId'],
                                            'ownerId': identity[1], 'claimedAt': identity[2]}
        temporary = path.with_name(path.name + '.tmp')
        write_json(temporary, bindings)
        temporary.replace(path)
        service.states.pop(identity[0], None)
    state.update(phase='published', published={'mapId': bundle.data['mapId'],
                 'version': bundle.data['version'], 'candidate': candidate_id},
                 message='地图已启用，等待新鲜 UWB 数据后将在位置页实时显示')
    store.save(identity, state)
