from flask import Blueprint, request

from petsense.auth.routes import require_app_user
from petsense.telemetry.realtime import device_ids_for_user
from .service import service
from .plan import read_plan

bp = Blueprint('uwb', __name__, url_prefix='/api/app/uwb')


@bp.get('/plan')
def get_plan():
    user, error = require_app_user()
    if error:
        return error
    device, error = owned_device(user)
    if error:
        return error
    try:
        plan = read_plan(device) if device else None
    except (ValueError, KeyError, OSError, TypeError):
        return {'message': '底图暂不可用，请重试'}, 503
    return {'success': True, 'deviceId': device, 'plan': plan}, 200, {'Cache-Control': 'no-store'}


def owned_device(user):
    devices = device_ids_for_user(user['id'])
    requested = request.args.get('deviceId')
    if requested and requested not in devices:
        return None, ({'message': '无权查看该设备'}, 403)
    return requested or next(iter(devices), None), None


@bp.get('/map')
@bp.get('/map-version')
@bp.get('/position')
def get_uwb():
    user, error = require_app_user()
    if error:
        return error
    device, error = owned_device(user)
    if error:
        return error
    try:
        kind = request.path.rsplit('/', 1)[-1]
        reader = {'map': service.map, 'map-version': service.map_version, 'position': service.latest}[kind]
        value = reader(device) if device else None
    except (ValueError, KeyError, OSError, TypeError):
        return {'message': '地图配置不可用，请检查标定配置'}, 503
    return {'success': True, 'deviceId': device,
            'position' if kind == 'position' else 'map': value}, 200, {'Cache-Control': 'no-store'}
