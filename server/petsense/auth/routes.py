"""App 账号体系：注册 / 登录 / 登出 / 会话（X-Petsense-Session token）。"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request

from petsense import config, db

bp = Blueprint('auth', __name__, url_prefix='/api/app/auth')


def password_digest(password, salt_hex):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), 180000).hex()


def app_user_from_request():
    token = request.headers.get(config.APP_TOKEN_HEADER, '')
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with db.app_connect() as database:
        row = database.execute('''SELECT u.id, u.username, u.display_name, u.avatar_emoji
            FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>?''', (token_hash, now)).fetchone()
        return dict(row) if row else None


def require_app_user():
    user = app_user_from_request()
    if not user:
        return None, ({'success': False, 'message': '请先登录'}, 401)
    return user, None


def issue_app_session(user_id):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=config.APP_SESSION_DAYS)
    with db.app_connect() as database:
        database.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
                         (hashlib.sha256(token.encode()).hexdigest(), user_id, expires.isoformat()))
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
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with db.app_connect() as database:
            user_id = database.execute(
                'INSERT INTO users(username,display_name,password_hash,salt,avatar_emoji,created_at) VALUES(?,?,?,?,?,?)',
                (username, display_name, password_digest(password, salt), salt, avatar_emoji, created_at)).lastrowid
    except db.sqlite3.IntegrityError:
        return {'success': False, 'message': '这个账号已被使用'}, 409
    token = issue_app_session(user_id)
    return {'success': True, 'token': token, 'user': {'id': user_id, 'username': username, 'displayName': display_name, 'avatarEmoji': avatar_emoji}}


@bp.route('/login', methods=['POST'])
def app_login():
    import hmac
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))
    with db.app_connect() as database:
        row = database.execute('SELECT id,username,display_name,avatar_emoji,password_hash,salt FROM users WHERE username=? COLLATE NOCASE',
                               (username,)).fetchone()
    if not row or not hmac.compare_digest(row['password_hash'], password_digest(password, row['salt'])):
        return {'success': False, 'message': '账号或密码不正确'}, 401
    token = issue_app_session(row['id'])
    return {'success': True, 'token': token, 'user': {'id': row['id'], 'username': row['username'], 'displayName': row['display_name'], 'avatarEmoji': row['avatar_emoji']}}


@bp.route('/logout', methods=['POST'])
def app_logout():
    token = request.headers.get(config.APP_TOKEN_HEADER, '')
    if token:
        with db.app_connect() as database:
            database.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
    return {'success': True}


@bp.route('/me')
def app_me():
    user = app_user_from_request()
    if not user:
        return {'success': True, 'user': None}
    user['displayName'] = user.pop('display_name')
    user['avatarEmoji'] = user.pop('avatar_emoji')
    return {'success': True, 'user': user}
