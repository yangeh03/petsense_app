"""W13 推送 HTTP 接口（/api/app/push/*）：注册、开关、状态与投递审计。

只对登录用户开放。安全约定：

- **token 只进不出**：接口不回传 Expo token，也不写日志；客户端只把 token 提交给
  本服务端（spec：客户端只向本服务端提交 token，日志中不打印 token、凭据或私人内容）。
- **本轮仅 iOS**：Android 与 Web 不注册、不申请系统权限，保持站内收件箱；服务端
  对非 iOS 注册请求直接拒绝并说明原因。
- **权限与开关分开**：系统权限由客户端上报（granted/denied/revoked/…），用户开关
  由服务端持久化；两者都通过时才可能发送。
"""
import re

from flask import Blueprint, request
from sqlalchemy import select

from petsense.agent import push
from petsense.auth.routes import require_app_user
from petsense.db import session_scope, utc_now
from petsense.db.models.push import PushDelivery, PushInstallation

bp = Blueprint('push', __name__, url_prefix='/api/app/push')

INSTALLATION_ID_RE = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
EXPO_TOKEN_RE = re.compile(r'^(Expo|Exponent)PushToken\[[A-Za-z0-9_\-]+\]$')
MAX_TOKEN_LEN = 255


def _error(message, code=400):
    return {'success': False, 'message': message}, code


def _platform():
    return str((request.get_json(silent=True) or {}).get('platform') or '').strip().lower()


def _permission_status(value):
    value = str(value or '').strip().lower()
    return value if value in push.PERMISSION_VALUES else push.PERMISSION_UNDETERMINED


@bp.route('/status', methods=['GET'])
def status():
    """平台闸门 + 本机注册状态 + 最近投递记录。"""
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        rows = s.execute(select(PushInstallation).where(
            PushInstallation.user_id == user['id']).order_by(
            PushInstallation.id)).scalars().all()
        deliveries = s.execute(select(PushDelivery).where(
            PushDelivery.user_id == user['id']).order_by(
            PushDelivery.id.desc()).limit(20)).scalars().all()
        return {
            'success': True,
            'gates': push.platform_gate_status(),
            'enabled': push.user_enabled(s, user['id']),
            'installations': [push.installation_public(row) for row in rows],
            'deliveries': [push.delivery_public(row) for row in deliveries],
        }


@bp.route('/preferences', methods=['GET'])
def get_preferences():
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        return {'success': True, 'enabled': push.user_enabled(s, user['id']),
                'gates': push.platform_gate_status()}


@bp.route('/preferences', methods=['PUT', 'POST'])
def put_preferences():
    """用户开关。关闭时服务端立即停发并抑制待发件。"""
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    enabled = data.get('enabled')
    if not isinstance(enabled, bool):
        return _error('enabled 需为布尔值')
    with session_scope() as s:
        push.set_user_enabled(s, user['id'], enabled, now=utc_now())
        return {'success': True, 'enabled': enabled}


@bp.route('/installations', methods=['POST'])
def register_installation():
    """登录后注册安装实例（iOS）。同一安装换账号时归属整体迁移。"""
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    installation_id = str(data.get('installationId') or '').strip()
    platform = _platform()
    expo_token = str(data.get('expoToken') or '').strip()
    permission_status = _permission_status(data.get('permissionStatus'))
    if not INSTALLATION_ID_RE.match(installation_id):
        return _error('安装实例 ID 无效')
    if platform != push.PLATFORM_IOS:
        return _error('当前版本仅支持 iOS 推送，Android 与 Web 请继续使用站内收件箱')
    if not EXPO_TOKEN_RE.match(expo_token) or len(expo_token) > MAX_TOKEN_LEN:
        return _error('推送 token 无效')
    with session_scope() as s:
        row, action = push.register_installation(
            s, user['id'], installation_id, platform, expo_token,
            permission_status=permission_status, now=utc_now())
        return {'success': True, 'action': action,
                'installation': push.installation_public(row)}


@bp.route('/installations/<installation_id>/token', methods=['POST'])
def rotate_token(installation_id):
    """token 轮换（系统重新下发）。安装实例 ID 不变，因此不会产生重复投递。"""
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    expo_token = str(data.get('expoToken') or '').strip()
    permission_status = data.get('permissionStatus')
    if not EXPO_TOKEN_RE.match(expo_token) or len(expo_token) > MAX_TOKEN_LEN:
        return _error('推送 token 无效')
    with session_scope() as s:
        row = push.update_installation_token(
            s, user['id'], installation_id, expo_token,
            permission_status=_permission_status(permission_status)
            if permission_status else None,
            now=utc_now())
        if row is None:
            return _error('安装实例不存在', 404)
        return {'success': True, 'installation': push.installation_public(row)}


@bp.route('/installations/<installation_id>/permission', methods=['POST'])
def report_permission(installation_id):
    """客户端上报系统权限变化（撤回 / 拒绝）。撤回后服务端立即停发。"""
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    permission_status = _permission_status(data.get('permissionStatus'))
    with session_scope() as s:
        row = push.update_installation_permission(
            s, user['id'], installation_id, permission_status, now=utc_now())
        if row is None:
            return _error('安装实例不存在', 404)
        return {'success': True, 'installation': push.installation_public(row)}


@bp.route('/installations/<installation_id>', methods=['DELETE'])
def unregister_installation(installation_id):
    """退出登录或解除绑定：安装实例停用，旧归属的待发件作废。"""
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        row = push.unregister_installation(s, user['id'], installation_id, now=utc_now())
        if row is None:
            return _error('安装实例不存在', 404)
        return {'success': True, 'installation': push.installation_public(row)}
