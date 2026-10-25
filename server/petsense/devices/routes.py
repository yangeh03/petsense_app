"""设备域：项圈认领 / 解绑 / 我的设备（一人一设备，认领即绑定宠物）。"""
import re

from flask import Blueprint, request
from sqlalchemy import select

from petsense.auth.routes import require_app_user
from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models import Device, Pet
from petsense.telemetry import realtime
from petsense.telemetry.status import latest_telemetry

bp = Blueprint('devices', __name__, url_prefix='/api/app')

DEVICE_ID_PATTERN = re.compile(r'[A-Za-z0-9_-]{1,40}')


def device_dict(device, pet=None):
    if pet is not None and pet.device_id != device.device_id:
        pet = None
    telemetry = latest_telemetry(device.device_id)
    return {
        'deviceId': device.device_id,
        'name': device.name,
        'claimedAt': iso_z(device.claimed_at),
        'lastSeenAt': iso_z(device.last_seen_at) if device.last_seen_at else None,
        'petId': pet.id if pet else None,
        'petName': pet.name if pet else None,
        'online': telemetry.get('online') if telemetry else False,
        'batteryPercent': telemetry.get('batteryPercent') if telemetry else None,
    }


def claim_device(session, user_id, device_id, name=''):
    """在既有事务内认领设备（无人认领 → 认领并绑定宠物；他人 → 冲突）。

    返回 (device, pet, error)；error 为 (message, status) 元组时表示拒绝。
    """
    device = session.get(Device, device_id)
    if device is not None and device.owner_id != user_id:
        return None, None, ('设备已被其他账号绑定', 409)
    owned = session.execute(select(Device).where(Device.owner_id == user_id)).scalar_one_or_none()
    if owned is not None and owned.device_id != device_id:
        return None, None, ('请先解绑当前设备，再绑定新的项圈', 409)
    pet = session.execute(select(Pet).where(Pet.user_id == user_id)).scalar_one_or_none()
    if device is None:
        device = Device(device_id=device_id, owner_id=user_id, name=name, claimed_at=utc_now())
        session.add(device)
    if pet is not None:
        pet.device_id = device_id
    return device, pet, None


@bp.route('/device')
def app_device():
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        device = s.execute(select(Device).where(Device.owner_id == user['id'])).scalar_one_or_none()
        pet = s.execute(select(Pet).where(Pet.user_id == user['id'])).scalar_one_or_none()
    return {'success': True, 'device': device_dict(device, pet) if device else None}


@bp.route('/device/claim', methods=['POST'])
def app_device_claim():
    user, error = require_app_user()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    device_id = str(body.get('deviceId', '')).strip()
    name = str(body.get('name', '')).strip()[:40]
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        return {'success': False, 'message': '设备 ID 需为 1—40 位字母、数字、下划线或连字符'}, 400
    with session_scope() as s:
        device, pet, error = claim_device(s, user['id'], device_id, name)
        if error:
            return {'success': False, 'message': error[0]}, error[1]
    # 事务外序列化：device_dict 内部会另开 session 查最新遥测
    result = device_dict(device, pet)
    realtime.refresh_scopes()
    return {'success': True, 'device': result}


@bp.route('/device/release', methods=['POST'])
def app_device_release():
    user, error = require_app_user()
    if error:
        return error
    device_id = str((request.get_json(silent=True) or {}).get('deviceId', '')).strip()
    with session_scope() as s:
        device = s.get(Device, device_id)
        if device is None or device.owner_id != user['id']:
            return {'success': False, 'message': '设备不存在'}, 404
        pet = s.execute(select(Pet).where(Pet.user_id == user['id'])).scalar_one_or_none()
        if pet is not None and pet.device_id == device_id:
            pet.device_id = ''
        s.delete(device)
    realtime.refresh_scopes()
    return {'success': True}
