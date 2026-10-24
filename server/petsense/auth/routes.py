"""App 账号体系：注册 / 登录 / 登出 / 会话（X-Petsense-Session token）。"""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from flask import Blueprint, request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from petsense import config
from petsense.db import session_scope, utc_now
from petsense.db.models import AuthSession, User

bp = Blueprint('auth', __name__, url_prefix='/api/app/auth')


def password_digest(password, salt_hex):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), 180000).hex()


def user_for_token(token):
    """会话 token → 用户（id/username/display_name/avatar_emoji）；无效或过期返回 None。"""
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with session_scope() as s:
        row = s.execute(
            select(User.id, User.username, User.display_name, User.avatar_emoji)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(AuthSession.token_hash == token_hash, AuthSession.expires_at > utc_now())
        ).fetchone()
        return {'id': row.id, 'username': row.username,
                'display_name': row.display_name, 'avatar_emoji': row.avatar_emoji} if row else None


def app_user_from_request():
    return user_for_token(request.headers.get(config.APP_TOKEN_HEADER, ''))


def require_app_user():
    user = app_user_from_request()
    if not user:
        return None, ({'success': False, 'message': '请先登录'}, 401)
    return user, None


def issue_app_session(user_id):
    token = secrets.token_urlsafe(32)
    expires = utc_now() + timedelta(days=config.APP_SESSION_DAYS)
    with session_scope() as s:
        s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                          user_id=user_id, expires_at=expires))
    return token


@bp.route('/register', methods=['POST'])
def app_register():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    display_name = str(data.get('display_name', '')).strip() or username
    password = str(data.get('password', ''))
    avatar_emoji = str(data.get('avatarEmoji', '')).strip() or '🙂'
    if not re.fullmatch(r'[A-Za-z0-9_\u4e00-\u9fff]{2,20}', username):
        return {'success': False, 'message': '账号需为2—20位中文、字母、数字或下划线'}, 400
    if len(display_name) > 20 or len(password) < 6 or len(password) > 72:
        return {'success': False, 'message': '昵称最多20字，密码需为6—72位'}, 400
    salt = secrets.token_hex(16)
    user = User(username=username, display_name=display_name,
                password_hash=password_digest(password, salt), salt=salt,
                avatar_emoji=avatar_emoji, bio='', created_at=utc_now())
    try:
        with session_scope() as s:
            s.add(user)
            s.flush()
    except IntegrityError:
        return {'success': False, 'message': '这个账号已被使用'}, 409
    token = issue_app_session(user.id)
    return {'success': True, 'token': token, 'user': {'id': user.id, 'username': username, 'displayName': display_name, 'avatarEmoji': avatar_emoji}}


@bp.route('/login', methods=['POST'])
def app_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))
    with session_scope() as s:
        row = s.execute(select(User.id, User.username, User.display_name, User.avatar_emoji,
                               User.password_hash, User.salt)
                        .where(func.lower(User.username) == username.lower())).fetchone()
    if not row or not hmac.compare_digest(row.password_hash, password_digest(password, row.salt)):
        return {'success': False, 'message': '账号或密码不正确'}, 401
    token = issue_app_session(row.id)
    return {'success': True, 'token': token, 'user': {'id': row.id, 'username': row.username, 'displayName': row.display_name, 'avatarEmoji': row.avatar_emoji}}


@bp.route('/logout', methods=['POST'])
def app_logout():
    token = request.headers.get(config.APP_TOKEN_HEADER, '')
    if token:
        from sqlalchemy import delete
        with session_scope() as s:
            s.execute(delete(AuthSession).where(
                AuthSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    return {'success': True}


@bp.route('/me')
def app_me():
    user = app_user_from_request()
    if not user:
        return {'success': True, 'user': None}
    user['displayName'] = user.pop('display_name')
    user['avatarEmoji'] = user.pop('avatar_emoji')
    return {'success': True, 'user': user}
