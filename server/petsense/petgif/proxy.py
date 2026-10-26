"""PetGIF 外部服务代理（API key 只存服务端）：形象生成 / 日记 / 户型图 / 一键发帖。"""

import os
import json
import secrets
import shutil
import time

import requests
from flask import Blueprint, request
from sqlalchemy import select

from petsense import config
from petsense.auth.routes import require_app_user
from petsense.db import session_scope, utc_now
from petsense.db.models import Pet, PetgifDiaryJob, Post

bp = Blueprint('petgif', __name__, url_prefix='/api/app/petgif')

try:
    PETGIF_KEY = open(os.environ.get('PETGIF_KEY_FILE', os.path.expanduser('~/.petgif-api-key'))).read().strip()
except OSError:
    PETGIF_KEY = ''
PETGIF_CACHE = {'at': 0.0, 'pets': {}}
PETGIF_AVATAR_TEMPLATES = {
    'marni': {'petgif_id': '0104965e-0ea3-4b79-92a6-975af775950b', 'name': 'Marni'},
    'donggua': {'petgif_id': 'f02154ee-193d-4c6c-98e7-26d86ebf67ba', 'name': '冬瓜'},
}


def petgif_headers():
    return {'x-api-key': PETGIF_KEY} if PETGIF_KEY else {}


def petgif_get(path):
    response = requests.get(config.PETGIF_BASE + path, headers=petgif_headers(), timeout=20)
    response.raise_for_status()
    return response.json()


def petgif_public(url):
    """只下发可信存储域名的媒体地址。"""
    if url and str(url).startswith(config.PETGIF_STORAGE_PUBLIC):
        return str(url)
    return None


def petgif_simplify(pet):
    behaviors = {}
    for key, info in (pet.get('behaviors') or {}).items():
        assets = info.get('assets') or {}
        gif = petgif_public((assets.get('colorGif') or {}).get('publicUrl'))
        if gif:
            behavior = {
                'status': info.get('status'),
                'gifUrl': gif,
                'videoUrl': petgif_public((assets.get('video') or {}).get('publicUrl')),
            }
            states = {}
            for state, state_info in (info.get('states') or {}).items():
                state_assets = state_info.get('assets') or {}
                state_gif = petgif_public((state_assets.get('colorGif') or {}).get('publicUrl'))
                if state_gif:
                    states[state] = {'gifUrl': state_gif}
            if states:
                behavior['states'] = states
            behaviors[key] = behavior
    card = petgif_public((pet.get('characterCard') or {}).get('publicUrl'))
    diaries = []
    for diary in pet.get('diaries') or []:
        image = (diary.get('assets') or {}).get('image') or {}
        poster = petgif_public(image.get('publicUrl'))
        stats = diary.get('stats') or {}
        diaries.append({
            'id': diary.get('id'),
            'date': stats.get('date'),
            'durationMin': stats.get('durationMin'),
            'steps': stats.get('steps'),
            'notes': diary.get('notes'),
            'posterUrl': poster,
        })
    profile = pet.get('profile') or {}
    return {
        'id': pet.get('id'),
        'name': pet.get('name') or profile.get('name'),
        'status': pet.get('status'),
        'stage': pet.get('stage'),
        'profile': {
            'name': profile.get('name'),
            'breed': profile.get('breed'),
            'traits': profile.get('traits') or [],
            'summary': profile.get('summary'),
        },
        'characterCardUrl': card,
        'behaviors': behaviors,
        'diaries': diaries,
    }


def app_petgif_pet(petgif_id):
    """带 10 分钟缓存的 petgif 宠物数据（供看板/社区取媒体地址）。"""
    now = time.time()
    if PETGIF_CACHE['at'] and now - PETGIF_CACHE['at'] < 600 and petgif_id in PETGIF_CACHE['pets']:
        return PETGIF_CACHE['pets'][petgif_id]
    data = petgif_get(f'/pets/{petgif_id}')
    pet = petgif_simplify(data.get('pet') or {})
    PETGIF_CACHE['at'] = now
    PETGIF_CACHE['pets'][petgif_id] = pet
    return pet


@bp.route('/pet/<petgif_id>')
def app_petgif_pet_route(petgif_id):
    try:
        return {'success': True, 'pet': app_petgif_pet(petgif_id)}
    except requests.RequestException as exc:
        return {'success': False, 'message': f'PetGIF 服务不可达：{exc}'}, 502


@bp.route('/avatar-templates')
def app_petgif_avatar_templates():
    """返回可复用动画目录；客户端只接触本地模板键，不接触上游 ID。"""
    user, error = require_app_user()
    if error:
        return error
    templates = []
    try:
        for template_id in PETGIF_AVATAR_TEMPLATES:
            templates.append(app_petgif_avatar_template(template_id))
    except requests.RequestException:
        return {'success': False, 'message': '动画模板暂时不可用'}, 502
    return {'success': True, 'templates': templates}


def app_petgif_avatar_template(template_id):
    metadata = PETGIF_AVATAR_TEMPLATES.get(template_id)
    if metadata is None:
        return None
    pet = app_petgif_pet(metadata['petgif_id'])
    return {
        'id': template_id,
        'name': metadata['name'],
        'previewUrl': pet.get('characterCardUrl'),
        'characterCardUrl': pet.get('characterCardUrl'),
        'behaviors': pet.get('behaviors') or {},
    }


@bp.route('/pets/<int:pet_id>/diaries', methods=['POST'])
def app_petgif_diary_job_create(pet_id):
    user, error = require_app_user()
    if error:
        return error
    from petsense.petgif import diary_jobs

    try:
        key = diary_jobs.validate_idempotency_key(request.headers.get('Idempotency-Key', ''))
    except ValueError as exc:
        return {'success': False, 'message': str(exc)}, 400
    with session_scope() as session:
        pet = session.execute(select(Pet).where(
            Pet.id == pet_id, Pet.user_id == user['id'],
        )).scalar_one_or_none()
        if pet is None:
            return {'success': False, 'message': '宠物档案不存在'}, 404
        existing = session.execute(select(PetgifDiaryJob).where(
            PetgifDiaryJob.user_id == user['id'], PetgifDiaryJob.pet_id == pet_id,
            PetgifDiaryJob.idempotency_key == key,
        )).scalar_one_or_none()
        if existing is not None:
            return {'success': True, **diary_jobs.job_dict(existing)}, 202
        preset_ids = {value['petgif_id'] for value in PETGIF_AVATAR_TEMPLATES.values()}
        if not pet.petgif_id or pet.petgif_id in preset_ids:
            return {'success': False, 'message': '运动日记需要你自己的 PetGIF 数字形象'}, 409
        petgif_id = pet.petgif_id
    source = request.form if request.mimetype == 'multipart/form-data' else (request.get_json(silent=True) or {})
    try:
        fields = diary_jobs.validate_input(source)
        paths = diary_jobs.store_photos(request.files.getlist('photos'))
    except ValueError as exc:
        return {'success': False, 'message': str(exc)}, 400
    try:
        job, reused = diary_jobs.create_job(user['id'], pet_id, petgif_id, key, fields, paths)
    except Exception:
        diary_jobs.remove_photos(paths)
        raise
    if not reused:
        diary_jobs.enqueue_diary_job(job.id)
    return {'success': True, **diary_jobs.job_dict(job)}, 202


@bp.route('/diary-jobs/<job_id>')
def app_petgif_diary_job_get(job_id):
    user, error = require_app_user()
    if error:
        return error
    from petsense.petgif.diary_jobs import job_dict
    with session_scope() as session:
        job = session.execute(select(PetgifDiaryJob).where(
            PetgifDiaryJob.id == job_id, PetgifDiaryJob.user_id == user['id'],
        )).scalar_one_or_none()
    if job is None:
        return {'success': False, 'message': '日记任务不存在'}, 404
    return {'success': True, **job_dict(job)}


@bp.route('/pets/<int:pet_id>/diary-jobs')
def app_petgif_diary_job_history(pet_id):
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as session:
        owned = session.execute(select(Pet.id).where(
            Pet.id == pet_id, Pet.user_id == user['id'],
        )).scalar_one_or_none()
    if owned is None:
        return {'success': False, 'message': '宠物档案不存在'}, 404
    from petsense.petgif.diary_jobs import diary_job_history
    return {'success': True, 'jobs': diary_job_history(user['id'], pet_id)}


@bp.route('/pets', methods=['POST'])
def app_petgif_create():
    """转发宠物照片 → 外部生成专属数字形象（异步，App 轮询状态）。"""
    photo = request.files.get('photo') or request.files.get('image')
    name = str(request.form.get('name', '')).strip()[:20] or 'my pet'
    if photo is None:
        return {'success': False, 'message': '请选择一张宠物照片'}, 400
    forward_files = {'image': (photo.filename or 'photo.jpg', photo.stream, photo.mimetype or 'image/jpeg')}
    forward = {'name': name}
    response = requests.post(config.PETGIF_BASE + '/pets', headers=petgif_headers(), files=forward_files, data=forward, timeout=60)
    try:
        payload = response.json()
    except ValueError:
        payload = {'error': f'HTTP {response.status_code}'}
    return payload, (200 if response.ok else 502)


@bp.route('/pets/sync', methods=['POST'])
def app_petgif_create_sync():
    """同步生成：上传宠物照片 → 阻塞轮询直到角色卡就绪（默认最长 150 秒）→ 返回完整形象。

    与异步版 /petgif/pets 的区别：App 端一次请求拿到结果，无需自行轮询。
    超时未完成时返回 202 + petgifId，可用异步接口继续查。
    """
    photo = request.files.get('photo') or request.files.get('image')
    name = str(request.form.get('name', '')).strip()[:20] or 'my pet'
    timeout_sec = max(30, min(240, int(request.form.get('timeout', 150) or 150)))
    if photo is None:
        return {'success': False, 'message': '请选择一张宠物照片'}, 400
    forward_files = {'image': (photo.filename or 'photo.jpg', photo.stream, photo.mimetype or 'image/jpeg')}
    response = requests.post(config.PETGIF_BASE + '/pets', headers=petgif_headers(),
                             files=forward_files, data={'name': name}, timeout=60)
    try:
        created = response.json()
    except ValueError:
        return {'success': False, 'message': f'生成任务创建失败（HTTP {response.status_code}）'}, 502
    petgif_id = (created.get('pet') or {}).get('id') or created.get('id') or created.get('petId')
    if not petgif_id:
        return {'success': False, 'message': created.get('error') or '生成任务创建失败'}, 502

    deadline = time.time() + timeout_sec
    pet = {}
    while time.time() < deadline:
        try:
            pet = petgif_get(f'/pets/{petgif_id}')
        except requests.RequestException:
            pet = {}
        stage_pet = pet.get('pet') or pet
        status = str(stage_pet.get('status', ''))
        if status in ('complete', 'failed', 'error'):
            break
        if (stage_pet.get('characterCard') or {}).get('publicUrl'):
            break
        time.sleep(4)

    stage_pet = pet.get('pet') or pet
    simplified = petgif_simplify(stage_pet) if stage_pet else None
    ready = bool(simplified and simplified.get('characterCardUrl'))
    PETGIF_CACHE['at'] = time.time()
    PETGIF_CACHE['pets'][petgif_id] = simplified
    payload = {'success': ready, 'petgifId': petgif_id, 'pet': simplified}
    if not ready:
        return payload, 202
    return payload


@bp.route('/pet/<petgif_id>/diary', methods=['POST'])
def app_petgif_diary_legacy(petgif_id):
    """Disable the old unauthenticated blocking route; use account-scoped job APIs."""
    return {'success': False, 'message': '请通过宠物档案创建运动日记'}, 410


@bp.route('/diary-post', methods=['POST'])
def app_petgif_diary_post():
    """把生成的运动日记海报一键发到社区（后端代下载海报图）。"""
    user, error = require_app_user()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    poster_url = str(body.get('posterUrl', ''))
    caption = str(body.get('caption', '')).strip()[:500]
    location = str(body.get('location', '')).strip()[:80]
    try:
        pet_id = int(body.get('petId', body.get('pet_id')))
    except (TypeError, ValueError):
        return {'success': False, 'message': '缺少有效的宠物档案'}, 400
    if not poster_url.startswith(config.PETGIF_STORAGE_PUBLIC):
        return {'success': False, 'message': '海报地址不受支持'}, 400
    with session_scope() as session:
        owned = session.execute(select(Pet.id).where(
            Pet.id == pet_id, Pet.user_id == user['id'],
        )).scalar_one_or_none()
    if not owned:
        return {'success': False, 'message': '宠物档案不存在'}, 404
    try:
        response = requests.get(poster_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        return {'success': False, 'message': '海报下载失败'}, 502
    filename = f'diary_{secrets.token_hex(6)}_{int(time.time())}.png'
    os.makedirs(config.UPLOADS_DIR, exist_ok=True)
    with open(os.path.join(config.UPLOADS_DIR, filename), 'wb') as handle:
        handle.write(response.content)
    with session_scope() as s:
        post = Post(user_id=user['id'], pet_id=pet_id, image_path=filename,
                    caption=caption or '今天的运动日记 🏃', location_text=location,
                    created_at=utc_now())
        s.add(post)
        s.flush()
        post_id = post.id
    return {'success': True, 'postId': post_id}, 201


@bp.route('/pet/<petgif_id>/floor-plan', methods=['POST'])
def app_petgif_floorplan(petgif_id):
    """转发家居户型图渲染。"""
    plan = request.files.get('plan')
    if plan is None:
        return {'success': False, 'message': '请选择户型图'}, 400
    forward_files = {'plan': (plan.filename or 'plan.png', plan.stream, plan.mimetype or 'image/png')}
    response = requests.post(
        config.PETGIF_BASE + f'/pets/{petgif_id}/floor-plan', headers=petgif_headers(), files=forward_files,
        data={'petId': petgif_id, 'notes': str(request.form.get('notes', ''))[:200]}, timeout=120,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {'error': f'HTTP {response.status_code}'}
    return payload, (200 if response.ok else 502)
