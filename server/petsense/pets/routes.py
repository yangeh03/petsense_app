"""宠物档案 / 仪表盘聚合 / UWB 快照路由。"""

import json
import time

import requests
from flask import Blueprint, request
from sqlalchemy import select, update

from petsense import config, stats
from petsense.auth.routes import require_app_user
from petsense.common import shanghai_now
from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models import DailyStat, Pet, Reading
from petsense.petgif.proxy import app_petgif_pet

bp = Blueprint('pets', __name__, url_prefix='/api/app')


def pet_latest_telemetry(device_id):
    if not device_id:
        return None
    with session_scope() as s:
        rows = s.execute(select(Reading.payload, Reading.received_at)
                         .where(Reading.topic == f'petsense/sensor/{device_id}')
                         .order_by(Reading.id.desc()).limit(5)).fetchall()
    for payload_text, received_at in rows:
        try:
            payload = json.loads(payload_text)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        health = payload.get('health') or {}
        battery = payload.get('battery') or {}
        age = time.time() - received_at.timestamp()
        return {
            'heartRate': health.get('heartRate') or 0,
            'spo2': health.get('spo2') or 0,
            'bodyTemp': health.get('bodyTemp') or 0,
            'envTemp': health.get('envTemp') or 0,
            'respiration': health.get('respiration') or 0,
            'hrvSdnn': health.get('hrvSdnn') or 0,
            'microCir': health.get('microCir') or 0,
            'fatigue': health.get('fatigue') or 0,
            'systolic': health.get('systolic') or 0,
            'diastolic': health.get('diastolic') or 0,
            'valid': bool(health.get('valid')),
            'batteryPercent': round(battery.get('percent') or 0),
            'online': age < config.DEVICE_ONLINE_TIMEOUT_S,
            'receivedAt': iso_z(received_at),
        }
    return None


def pet_today_stats(pet_id):
    with session_scope() as s:
        row = s.execute(select(DailyStat.sleep_seconds, DailyStat.active_seconds,
                               DailyStat.hr_sum, DailyStat.hr_count)
                        .where(DailyStat.pet_id == pet_id,
                               DailyStat.date == shanghai_now().date())).fetchone()
    if not row:
        return {'sleepMinutes': 0, 'activeMinutes': 0, 'avgHeartRate': 0}
    return {
        'sleepMinutes': round(row.sleep_seconds / 60),
        'activeMinutes': round(row.active_seconds / 60),
        'avgHeartRate': round(row.hr_sum / row.hr_count) if row.hr_count else 0,
    }


def build_insight(pet, telemetry, today):
    name = pet['name']
    if not telemetry or not telemetry.get('online'):
        return {'icon': '📡', 'text': f'{name} 的项圈暂时离线，恢复连接后这里会自动更新'}
    if telemetry.get('valid') and telemetry.get('heartRate', 0) > 180:
        return {'icon': '💗', 'text': f'{name} 的心率有点快（{round(telemetry["heartRate"])} bpm），让它安静休息一会儿吧'}
    hour = shanghai_now().hour
    if 6 <= hour <= 9 and today['activeMinutes'] < 15:
        return {'icon': '🍖', 'text': f'早晨活动还不多，{name} 可能到饭点啦'}
    if 17 <= hour <= 19 and today['activeMinutes'] < 20:
        return {'icon': '🚶', 'text': f'{name} 今天还没怎么动，适合出门遛一圈'}
    if today['activeMinutes'] >= 60:
        return {'icon': '💪', 'text': f'{name} 今天已经活跃 {today["activeMinutes"]} 分钟，表现很棒'}
    if today['sleepMinutes'] >= 300:
        return {'icon': '😴', 'text': f'{name} 今天睡了 {today["sleepMinutes"]} 分钟，睡得很安心'}
    return {'icon': '🐾', 'text': f'{name} 状态平稳，陪你度过平常又安心的一天'}


def pet_dict(pet):
    """pets 列表 / dashboard 共用的行序列化（键与旧 dict(row) 一致）。"""
    item = {'id': pet.id, 'user_id': pet.user_id, 'name': pet.name, 'species': pet.species,
            'breed': pet.breed, 'avatar_emoji': pet.avatar_emoji, 'device_id': pet.device_id,
            'petgif_id': pet.petgif_id, 'avatar_image': pet.avatar_image,
            'created_at': iso_z(pet.created_at)}
    telemetry = pet_latest_telemetry(pet.device_id)
    item['batteryPercent'] = telemetry.get('batteryPercent') if telemetry else None
    item['online'] = telemetry.get('online') if telemetry else False
    item['avatarImageUrl'] = '/uploads/' + pet.avatar_image if pet.avatar_image else None
    return item


@bp.route('/pets', methods=['GET', 'POST'])
def app_pets():
    user, error = require_app_user()
    if error:
        return error
    if request.method == 'GET':
        with session_scope() as s:
            pets = s.execute(select(Pet).where(Pet.user_id == user['id'])
                             .order_by(Pet.id)).scalars().all()
        return {'success': True, 'pets': [pet_dict(pet) for pet in pets]}

    data = request.get_json(silent=True) or {}
    name = str(data.get('name', '')).strip()
    species = str(data.get('species', 'dog')).strip()
    breed = str(data.get('breed', '')).strip()[:40]
    avatar_emoji = str(data.get('avatarEmoji', '')).strip() or '🐾'
    device_id = str(data.get('deviceId', '')).strip()[:40]
    if not name or len(name) > 20:
        return {'success': False, 'message': '请给宠物起个 20 字以内的名字'}, 400
    if species not in ('dog', 'cat', 'rabbit'):
        return {'success': False, 'message': '物种仅支持 dog / cat / rabbit'}, 400
    with session_scope() as s:
        pet = Pet(user_id=user['id'], name=name, species=species, breed=breed,
                  avatar_emoji=avatar_emoji, device_id=device_id, created_at=utc_now())
        s.add(pet)
        s.flush()
        pet_id = pet.id
    return {'success': True, 'pet': {'id': pet_id, 'name': name}}, 201


@bp.route('/pets/<int:pet_id>/avatar-image', methods=['POST'])
def app_pet_avatar_image(pet_id):
    """把 PetGIF 生成的角色卡/媒体下载为宠物头像图。"""
    user, error = require_app_user()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    image_url = str(body.get('imageUrl', ''))
    if not image_url.startswith(config.PETGIF_STORAGE_PUBLIC):
        return {'success': False, 'message': '图片地址不受支持'}, 400
    try:
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        return {'success': False, 'message': '图片下载失败'}, 502
    import os
    import secrets
    filename = f'avatar_{secrets.token_hex(6)}_{int(time.time())}.png'
    with open(os.path.join(config.UPLOADS_DIR, filename), 'wb') as handle:
        handle.write(response.content)
    with session_scope() as s:
        owned = s.execute(select(Pet.id).where(Pet.id == pet_id,
                                               Pet.user_id == user['id'])).scalar_one_or_none()
        if not owned:
            return {'success': False, 'message': '宠物不存在'}, 404
        s.execute(update(Pet).where(Pet.id == pet_id).values(avatar_image=filename))
    return {'success': True, 'avatarImageUrl': '/uploads/' + filename}


@bp.route('/pets/<int:pet_id>/petgif', methods=['POST'])
def app_pet_bind_petgif(pet_id):
    """把 PetGIF 数字形象绑定到宠物（看板 Hero / 位置页使用其行为 GIF）。"""
    user, error = require_app_user()
    if error:
        return error
    petgif_id = str((request.get_json(silent=True) or {}).get('petgifId', '')).strip()[:40]
    if not petgif_id:
        return {'success': False, 'message': '缺少 petgifId'}, 400
    with session_scope() as s:
        owned = s.execute(select(Pet.id).where(Pet.id == pet_id,
                                               Pet.user_id == user['id'])).scalar_one_or_none()
        if not owned:
            return {'success': False, 'message': '宠物不存在'}, 404
        s.execute(update(Pet).where(Pet.id == pet_id).values(petgif_id=petgif_id))
    return {'success': True}


@bp.route('/dashboard')
def app_dashboard():
    user, error = require_app_user()
    if error:
        return error
    pet_id = request.args.get('pet_id', type=int)
    with session_scope() as s:
        stmt = select(Pet).where(Pet.user_id == user['id'])
        if pet_id:
            pet = s.execute(stmt.where(Pet.id == pet_id)).scalar_one_or_none()
        else:
            pet = s.execute(stmt.order_by(Pet.id).limit(1)).scalar_one_or_none()
    if not pet:
        return {'success': True, 'pet': None}
    pet = pet_dict(pet)
    if pet.get('avatar_image'):
        pet['avatarImageUrl'] = '/uploads/' + pet['avatar_image']
    telemetry = pet_latest_telemetry(pet['device_id'])
    today = pet_today_stats(pet['id'])
    petgif = None
    if pet.get('petgif_id'):
        try:
            petgif = app_petgif_pet(pet['petgif_id'])
        except requests.RequestException:
            petgif = None
    return {
        'success': True,
        'pet': pet,
        'latest': telemetry,
        'today': today,
        'petgif': petgif,
        'insight': build_insight(pet, telemetry, today),
    }


@bp.route('/uwb/latest')
def app_uwb_latest():
    if not stats.last_uwb_frame:
        return {'success': True, 'frame': None}
    payload = stats.last_uwb_frame['payload']
    received_at = stats.last_uwb_frame['received_at']
    if not isinstance(received_at, str):
        received_at = iso_z(received_at)
    return {'success': True, 'frame': {'deviceId': payload.get('deviceId'), 'tsMs': payload.get('ts_ms'),
            'uwb': payload.get('uwb'), 'receivedAt': received_at}}
