"""PetGIF 外部服务代理（API key 只存服务端）：形象生成 / 日记 / 户型图 / 一键发帖。"""

import os
import secrets
import time
from datetime import datetime, timezone

import requests
from flask import Blueprint, request

from petsense import config, db
from petsense.auth.routes import require_app_user

bp = Blueprint('petgif', __name__, url_prefix='/api/app/petgif')

try:
    PETGIF_KEY = open(os.environ.get('PETGIF_KEY_FILE', os.path.expanduser('~/.petgif-api-key'))).read().strip()
except OSError:
    PETGIF_KEY = ''
PETGIF_CACHE = {'at': 0.0, 'pets': {}}


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
            behaviors[key] = {
                'status': info.get('status'),
                'gifUrl': gif,
                'videoUrl': petgif_public((assets.get('video') or {}).get('publicUrl')),
            }
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
def app_petgif_diary(petgif_id):
    """运动日记生成：兼容 JSON / form / multipart（App 端 JSON 无照片时自动用宠物形象照兜底）。

    外部服务要求 multipart 且至少一张照片；实测全流程约 2 分钟，轮询上限 200 秒。
    """
    payload_json = request.get_json(silent=True) or {}
    fields = {}
    for source in (request.form, payload_json):
        for key in ('durationMin', 'steps', 'places', 'notes'):
            if source.get(key) is not None:
                fields[key] = source[key]
    try:
        duration = max(1, min(600, int(float(fields.get('durationMin', 30) or 30))))
        steps = max(0, min(100000, int(float(fields.get('steps', 0) or 0))))
    except (TypeError, ValueError):
        return {'success': False, 'message': '时长与步数需为数字'}, 400
    places = str(fields.get('places', ''))[:200]
    notes = str(fields.get('notes', ''))[:200]

    forward_files = []
    for index, photo in enumerate(request.files.getlist('photos')[:6]):
        forward_files.append(
            ('photos', (photo.filename or f'photo_{index}.jpg', photo.stream, photo.mimetype or 'image/jpeg'))
        )

    # 无照片兜底：用宠物形象（角色卡 → 行为首帧）作为日记底图，保证一键可生成
    if not forward_files:
        fallback_url = None
        try:
            petgif = app_petgif_pet(petgif_id)
            fallback_url = petgif.get('characterCardUrl')
        except requests.RequestException:
            pass
        if not fallback_url:
            fallback_url = config.PETGIF_STORAGE_PUBLIC + f'pets/{petgif_id}/character-card.png'
        try:
            image = requests.get(fallback_url, timeout=30)
            image.raise_for_status()
            forward_files.append(('photos', ('cover.png', image.content, 'image/png')))
        except requests.RequestException:
            return {'success': False, 'message': '生成失败：缺少照片且兜底图不可用'}, 502

    data = {'durationMin': duration, 'steps': steps, 'places': places, 'notes': notes, 'petId': petgif_id}
    try:
        response = requests.post(
            config.PETGIF_BASE + f'/pets/{petgif_id}/diaries', headers=petgif_headers(),
            files=forward_files, data=data, timeout=120,
        )
        created = response.json() if response.ok else {}
    except (requests.RequestException, ValueError):
        created = {}
    diary_id = (created.get('diary') or {}).get('id') or created.get('id')
    if not diary_id:
        message = created.get('error') or f'日记生成任务创建失败（HTTP {response.status_code}）'
        return {'success': False, 'message': message}, 502

    deadline = time.time() + 200
    diary = {}
    while time.time() < deadline:
        try:
            raw = petgif_get(f'/pets/{petgif_id}/diaries/{diary_id}')
        except requests.RequestException:
            raw = {}
        # 响应为 {diary: {...}, active: ...} 嵌套结构
        diary = raw.get('diary') or raw
        if str(diary.get('status', '')) in ('complete', 'failed'):
            break
        time.sleep(4)
    image = (diary.get('assets') or {}).get('image') or {}
    success = str(diary.get('status', '')) == 'complete' and bool(image.get('publicUrl'))
    return {
        'success': success,
        'status': diary.get('status') or 'unknown',
        'posterUrl': petgif_public(image.get('publicUrl')),
        'stats': diary.get('stats') or {},
    }


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
    pet_id = body.get('pet_id')
    if not poster_url.startswith(config.PETGIF_STORAGE_PUBLIC):
        return {'success': False, 'message': '海报地址不受支持'}, 400
    try:
        response = requests.get(poster_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        return {'success': False, 'message': '海报下载失败'}, 502
    filename = f'diary_{secrets.token_hex(6)}_{int(time.time())}.png'
    with open(os.path.join(config.UPLOADS_DIR, filename), 'wb') as handle:
        handle.write(response.content)
    if pet_id:
        with db.app_connect() as database:
            owned = database.execute('SELECT 1 FROM pets WHERE id=? AND user_id=?', (pet_id, user['id'])).fetchone()
        if not owned:
            pet_id = None
    created_at = datetime.now(timezone.utc).isoformat()
    with db.app_connect() as database:
        post_id = database.execute(
            'INSERT INTO posts(user_id,pet_id,image_path,caption,location_text,created_at) VALUES(?,?,?,?,?,?)',
            (user['id'], pet_id, filename, caption or '今天的运动日记 🏃', location, created_at),
        ).lastrowid
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
