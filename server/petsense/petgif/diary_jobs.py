"""Durable asynchronous diary jobs, isolated from request-scoped Flask state."""
import io
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from petsense import config
from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models import Pet, PetgifDiaryJob

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTOS = 3
ALLOWED_IMAGE_TYPES = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}
JOB_TTL_DAYS = 90
HISTORY_LIMIT = 20
IDEMPOTENCY_PATTERN = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='petgif-diary')
_active_lock = threading.Lock()
_active_jobs = set()
# 终态钩子（W10）：任务到终态时按 jobId 回调（Agent 动作回执收敛），钩子异常不影响任务流
_terminal_hooks = []


def add_terminal_hook(fn):
    if fn not in _terminal_hooks:
        _terminal_hooks.append(fn)


def _fire_terminal_hooks(job_id):
    for fn in list(_terminal_hooks):
        try:
            fn(job_id)
        except Exception as error:  # noqa: BLE001 钩子失败只记录，不阻断任务状态
            print(f'[PETGIF] terminal hook error for {job_id}: {error}')


def validate_input(fields):
    def integer(name, minimum, maximum):
        raw = fields.get(name)
        if raw is None or not re.fullmatch(r'\d+', str(raw).strip()):
            raise ValueError('请填写有效的运动时长与步数')
        value = int(raw)
        if not minimum <= value <= maximum:
            raise ValueError('运动时长需为 1—600 分钟，步数需为 0—100000')
        return value

    duration = integer('durationMin', 1, 600)
    steps = integer('steps', 0, 100000)
    places = str(fields.get('places', '')).strip()
    notes = str(fields.get('notes', '')).strip()
    if len(places) > 200 or len(notes) > 200:
        raise ValueError('地点和备注最多 200 个字符')
    return {'durationMin': duration, 'steps': steps, 'places': places, 'notes': notes}


def validate_idempotency_key(value):
    if not value or not IDEMPOTENCY_PATTERN.fullmatch(value):
        raise ValueError('缺少有效的 Idempotency-Key')
    return value


def store_photos(photos):
    if len(photos) > MAX_PHOTOS:
        raise ValueError('最多选择 3 张照片')
    paths = []
    os.makedirs(config.PETGIF_TEMP_DIR, exist_ok=True)
    try:
        for photo in photos:
            mime = (photo.mimetype or '').lower()
            extension = ALLOWED_IMAGE_TYPES.get(mime)
            if extension is None:
                raise ValueError('照片仅支持 JPG、PNG 或 WebP')
            content = photo.stream.read(MAX_PHOTO_BYTES + 1)
            if not content or len(content) > MAX_PHOTO_BYTES:
                raise ValueError('每张照片需小于 10MB')
            try:
                with Image.open(io.BytesIO(content)) as image:
                    if image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > 16_000_000:
                        raise ValueError('照片格式或分辨率不受支持')
                    image.verify()
            except (UnidentifiedImageError, OSError, SyntaxError) as exc:
                raise ValueError('照片无法读取，请选择有效的图片') from exc
            path = os.path.join(config.PETGIF_TEMP_DIR, f'{uuid.uuid4().hex}{extension}')
            with open(path, 'xb') as handle:
                handle.write(content)
            paths.append(path)
        return paths
    except Exception:
        remove_photos(paths)
        raise


def remove_photos(paths):
    for path in paths or []:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def get_existing_job(user_id, pet_id, idempotency_key):
    with session_scope() as session:
        return session.execute(select(PetgifDiaryJob).where(
            PetgifDiaryJob.user_id == user_id,
            PetgifDiaryJob.pet_id == pet_id,
            PetgifDiaryJob.idempotency_key == idempotency_key,
        )).scalar_one_or_none()


def create_job(user_id, pet_id, petgif_id, idempotency_key, fields, photo_paths):
    now = utc_now()
    job = PetgifDiaryJob(
        id=str(uuid.uuid4()), user_id=user_id, pet_id=pet_id, petgif_id=petgif_id,
        idempotency_key=idempotency_key, status='queued', input_json=json.dumps(fields, ensure_ascii=False),
        photo_paths_json=json.dumps(photo_paths), result_json='{}', created_at=now, updated_at=now,
    )
    try:
        with session_scope() as session:
            session.add(job)
            session.flush()
        return job, False
    except IntegrityError:
        remove_photos(photo_paths)
        existing = get_existing_job(user_id, pet_id, idempotency_key)
        if existing is None:
            raise
        return existing, True


def job_dict(job):
    result = json.loads(job.result_json or '{}')
    fields = json.loads(job.input_json or '{}')
    payload = {
        'jobId': job.id,
        'petId': job.pet_id,
        'status': job.status,
        'createdAt': iso_z(job.created_at),
        'updatedAt': iso_z(job.updated_at),
        'posterUrl': result.get('posterUrl'),
        'stats': result.get('stats') or {},
        'input': {key: fields.get(key) for key in ('durationMin', 'steps', 'places', 'notes')},
        'message': job.error_message,
    }
    if job.idempotency_key.startswith('daily:'):
        payload.update(date=fields['date'], mode=fields['mode'])
    return payload


def _update_job(job_id, **updates):
    with session_scope() as session:
        job = session.get(PetgifDiaryJob, job_id)
        if job is None:
            return None
        for key, value in updates.items():
            setattr(job, key, value)
        job.updated_at = utc_now()
        return job


def _read_job(job_id):
    with session_scope() as session:
        return session.get(PetgifDiaryJob, job_id)


def enqueue_diary_job(job_id):
    with _active_lock:
        if job_id in _active_jobs:
            return False
        _active_jobs.add(job_id)
    try:
        _executor.submit(_run_registered, job_id)
        return True
    except RuntimeError:
        with _active_lock:
            _active_jobs.discard(job_id)
        _update_job(job_id, status='failed', error_message='日记服务暂时繁忙，请重新提交')
        return False


def _run_registered(job_id):
    try:
        run_diary_job(job_id)
    finally:
        with _active_lock:
            _active_jobs.discard(job_id)


def _set_terminal(job_id, status, message=None, result=None):
    row = _update_job(job_id, status=status, error_message=message,
                      result_json=json.dumps(result or {}, ensure_ascii=False), finished_at=utc_now())
    if row is not None:
        _fire_terminal_hooks(job_id)
    return row


def run_diary_job(job_id, poll_timeout=None, poll_interval=None):
    """Create an upstream diary once, then poll its known ID; ambiguous create stays unknown."""
    from petsense.petgif import proxy

    poll_timeout = config.PETGIF_DIARY_POLL_TIMEOUT_S if poll_timeout is None else poll_timeout
    poll_interval = config.PETGIF_DIARY_POLL_INTERVAL_S if poll_interval is None else poll_interval

    job = _read_job(job_id)
    if job is None or job.status in ('completed', 'failed'):
        return
    if job.status == 'unknown' and not job.upstream_diary_id:
        return
    _update_job(job_id, status='running', error_message=None)
    fields = json.loads(job.input_json)
    mode = fields.get('mode')
    if mode == 'template':
        from petsense.petgif.daily_diary import render_template
        try:
            poster = render_template(job_id, fields)
            _set_terminal(job_id, 'completed', result={'posterUrl': poster})
        except (OSError, ValueError):
            _set_terminal(job_id, 'failed', '海报暂时无法生成，请重试')
        return
    paths = json.loads(job.photo_paths_json or '[]')
    diary_id = job.upstream_diary_id
    upstream_attempted = False
    try:
        if not diary_id:
            uploads = []
            for index, path in enumerate(paths):
                extension = Path(path).suffix.lower()
                content_type = next((mime for mime, ext in ALLOWED_IMAGE_TYPES.items() if ext == extension), 'image/jpeg')
                uploads.append(('photos', (f'photo_{index}{extension}', open(path, 'rb'), content_type)))
            if not uploads and mode == 'ai':
                _set_terminal(job_id, 'failed', '请从宠物档案照片重新生成 AI 海报')
                return
            if not uploads:
                pet_data = proxy.app_petgif_pet(job.petgif_id)
                cover_url = proxy.petgif_public(pet_data.get('characterCardUrl'))
                if not cover_url:
                    _set_terminal(job_id, 'failed', '缺少照片且个人形象图暂不可用')
                    return
                cover_bytes = proxy.download_petgif_image(cover_url)
                uploads.append(('photos', ('cover.png', io.BytesIO(cover_bytes), 'image/png')))
            try:
                upstream_attempted = True
                response = requests.post(
                    config.PETGIF_BASE + f'/pets/{job.petgif_id}/diaries',
                    headers=proxy.petgif_headers(), files=uploads,
                    data=({
                        'date': fields['date'], 'durationMin': fields['durationMin'],
                        'steps': fields['steps'],
                        'notes': (
                            '仅用本次上传照片里的宠物与场景作为绘制素材。'
                            '忽略档案里的默认角色、名字和外观。'
                            '贴纸和正文不要写宠物名字，也不要补写没有提供的地点。'
                            '将步数标为估算值。'
                        ),
                        'petId': job.petgif_id,
                    } if mode == 'ai' else {**fields, 'petId': job.petgif_id}), timeout=45,
                    allow_redirects=False,
                )
            finally:
                for _, (_, stream, _) in uploads:
                    stream.close()
                remove_photos(paths)
            if response.status_code >= 500 or response.status_code == 408:
                _set_terminal(job_id, 'unknown', '上游响应不确定，暂不自动重试')
                return
            if not 200 <= response.status_code < 300:
                _set_terminal(job_id, 'failed', 'PetGIF 暂时无法创建日记，请调整后重试')
                return
            try:
                created = response.json()
            except ValueError:
                _set_terminal(job_id, 'unknown', '上游任务状态不确定，暂不自动重试')
                return
            diary_id = (created.get('diary') or {}).get('id') or created.get('id')
            if mode == 'ai' and diary_id:
                try:
                    diary_id = str(uuid.UUID(str(diary_id)))
                except ValueError:
                    diary_id = None
            if not diary_id:
                _set_terminal(job_id, 'unknown', '未收到有效的上游日记编号，暂不自动重试')
                return
            _update_job(job_id, upstream_diary_id=str(diary_id), status='running')

        deadline = time.monotonic() + poll_timeout
        while time.monotonic() < deadline:
            try:
                response = requests.get(
                    config.PETGIF_BASE + f'/pets/{job.petgif_id}/diaries/{diary_id}',
                    headers=proxy.petgif_headers(), timeout=20, allow_redirects=mode != 'ai',
                )
                if mode == 'ai' and response.status_code != 200:
                    time.sleep(poll_interval)
                    continue
                response.raise_for_status()
                raw = response.json()
            except (requests.RequestException, ValueError):
                time.sleep(poll_interval)
                continue
            diary = raw.get('diary') or raw
            status = str(diary.get('status', '')).lower()
            if status == 'complete':
                image = (diary.get('assets') or {}).get('image') or {}
                poster_url = proxy.petgif_public(image.get('publicUrl'))
                if mode == 'ai':
                    from petsense.petgif.daily_diary import store_ai_poster
                    with requests.get(
                        config.PETGIF_BASE + f'/pets/{job.petgif_id}/diaries/{diary_id}/image',
                        headers=proxy.petgif_headers(), timeout=30, stream=True, allow_redirects=False,
                    ) as media:
                        if media.status_code != 200:
                            raise ValueError('海报读取失败')
                        content = media.raw.read(10 * 1024 * 1024 + 1)
                        if len(content) > 10 * 1024 * 1024:
                            raise ValueError('海报文件大小超限')
                    poster_url = store_ai_poster(job_id, content)
                if not poster_url:
                    _set_terminal(job_id, 'unknown', '日记已生成，但海报暂不可用；可稍后查询')
                    return
                _set_terminal(job_id, 'completed', result={
                    'posterUrl': poster_url, 'stats': diary.get('stats') or {},
                })
                return
            if status in ('failed', 'error'):
                _set_terminal(job_id, 'failed', diary.get('error') or 'PetGIF 日记生成失败')
                return
            time.sleep(poll_interval)
        _set_terminal(job_id, 'unknown', '生成仍在处理中，可稍后查询；为防重复暂不重新提交')
    except requests.Timeout:
        _set_terminal(job_id, 'unknown' if upstream_attempted else 'failed',
                      'PetGIF 响应超时，任务状态不确定' if upstream_attempted else '连接 PetGIF 超时，请重试')
    except requests.RequestException:
        _set_terminal(job_id, 'unknown' if upstream_attempted else 'failed',
                      'PetGIF 连接中断，任务状态不确定' if upstream_attempted else '读取个人形象失败，请重试')
    except Exception:
        _set_terminal(job_id, 'unknown' if upstream_attempted or diary_id else 'failed',
                      'PetGIF 任务状态不确定，可稍后查询' if upstream_attempted or diary_id else '日记任务处理失败，请稍后重试')
    finally:
        remove_photos(paths)


def recover_diary_jobs():
    cutoff = utc_now() - timedelta(days=JOB_TTL_DAYS)
    with session_scope() as session:
        old_jobs = session.execute(select(PetgifDiaryJob).where(
            PetgifDiaryJob.created_at < cutoff,
            PetgifDiaryJob.status.in_(('completed', 'failed', 'unknown')),
        )).scalars().all()
        for old in old_jobs:
            remove_photos(json.loads(old.photo_paths_json or '[]'))
            if old.idempotency_key.startswith('daily:'):
                from petsense.petgif.daily_diary import poster_path
                poster_path(old.id).unlink(missing_ok=True)
            session.delete(old)
        jobs = session.execute(select(PetgifDiaryJob).where(
            PetgifDiaryJob.status.in_(('queued', 'running', 'unknown')),
        )).scalars().all()
        recover = []
        for job in jobs:
            if job.idempotency_key.startswith('daily:') and json.loads(job.input_json).get('mode') == 'template':
                job.status = 'queued'
                recover.append(job.id)
            elif job.upstream_diary_id or (
                job.idempotency_key.startswith('daily:') and job.status == 'queued' and
                json.loads(job.photo_paths_json or '[]')
            ):
                recover.append(job.id)
            else:
                remove_photos(json.loads(job.photo_paths_json or '[]'))
                job.status = 'unknown'
                job.error_message = '服务重启时上游任务编号尚未保存，暂不自动重试'
                job.updated_at = utc_now()
                job.finished_at = utc_now()
    for job_id in recover:
        enqueue_diary_job(job_id)


def diary_job_history(user_id, pet_id):
    cutoff = utc_now() - timedelta(days=JOB_TTL_DAYS)
    with session_scope() as session:
        expired = session.execute(select(PetgifDiaryJob).where(
            PetgifDiaryJob.user_id == user_id,
            PetgifDiaryJob.pet_id == pet_id,
            ~PetgifDiaryJob.idempotency_key.startswith('daily:'),
            PetgifDiaryJob.created_at < cutoff,
            PetgifDiaryJob.status.in_(('completed', 'failed', 'unknown')),
        )).scalars().all()
        for job in expired:
            remove_photos(json.loads(job.photo_paths_json or '[]'))
            session.delete(job)
        jobs = session.execute(select(PetgifDiaryJob).where(
            PetgifDiaryJob.user_id == user_id, PetgifDiaryJob.pet_id == pet_id,
            ~PetgifDiaryJob.idempotency_key.startswith('daily:'),
        ).order_by(PetgifDiaryJob.created_at.desc()).limit(HISTORY_LIMIT)).scalars().all()
        return [job_dict(job) for job in jobs]
