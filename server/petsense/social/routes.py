"""社区：feed / 帖子 / 点赞 / 评论 / 关注 / 用户主页。"""

import os
import secrets
import time
from datetime import datetime, timezone

from flask import Blueprint, request

from petsense import config, db
from petsense.auth.routes import app_user_from_request, require_app_user
from petsense.pets.routes import pet_latest_telemetry

bp = Blueprint('social', __name__, url_prefix='/api/app')

IMAGE_MAGIC = [('image/jpeg', '.jpg', b'\xff\xd8\xff'), ('image/png', '.png', b'\x89PNG'),
               ('image/webp', '.webp', b'RIFF')]


def save_upload_image(file_storage, prefix):
    data = file_storage.read()
    if not data or len(data) > config.MAX_UPLOAD_BYTES:
        raise ValueError('图片为空或超过 10MB')
    for content_type, ext, magic in IMAGE_MAGIC:
        if data.startswith(magic):
            break
    else:
        raise ValueError('仅支持 JPG / PNG / WebP 图片')
    filename = f'{prefix}_{secrets.token_hex(6)}_{int(time.time())}{ext}'
    with open(os.path.join(config.UPLOADS_DIR, filename), 'wb') as handle:
        handle.write(data)
    return filename, content_type


def post_rows(viewer_id, where_clause='', params=()):
    query = f'''SELECT p.id,p.caption,p.location_text,p.image_path,p.created_at,p.media_url,p.media_kind,
        u.id AS user_id,u.display_name,u.avatar_emoji,
        pet.id AS pet_id,pet.name AS pet_name,pet.avatar_emoji AS pet_emoji,
        (SELECT COUNT(*) FROM post_likes l WHERE l.post_id=p.id) AS likes,
        (SELECT COUNT(*) FROM comments c WHERE c.post_id=p.id) AS comments_count,
        EXISTS(SELECT 1 FROM post_likes l2 WHERE l2.post_id=p.id AND l2.user_id=?) AS liked_by_me
        FROM posts p JOIN users u ON u.id=p.user_id LEFT JOIN pets pet ON pet.id=p.pet_id
        {where_clause} ORDER BY p.id DESC LIMIT 60'''
    with db.app_connect() as database:
        rows = database.execute(query, (viewer_id, *params)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        image_path = item.pop('image_path') or ''
        item['imageUrl'] = f'/uploads/{image_path}' if image_path else ''
        item['mediaUrl'] = item.pop('media_url')
        item['mediaKind'] = item.pop('media_kind')
        item['isMine'] = item['user_id'] == viewer_id
        items.append(item)
    return items


@bp.route('/feed')
def app_feed():
    viewer = app_user_from_request()
    user_id = request.args.get('user_id', type=int)
    if user_id:
        items = post_rows(viewer['id'] if viewer else -1, 'WHERE p.user_id=?', (user_id,))
    else:
        items = post_rows(viewer['id'] if viewer else -1)
    return {'success': True, 'posts': items}


@bp.route('/posts', methods=['POST'])
def app_create_post():
    user, error = require_app_user()
    if error:
        return error
    caption = str(request.form.get('caption', '')).strip()
    location = str(request.form.get('location', '')).strip()[:80]
    pet_id = request.form.get('pet_id', type=int)
    if len(caption) > 500:
        return {'success': False, 'message': '文案最多 500 字'}, 400
    media_url = str(request.form.get('media_url', '')).strip()
    media_kind = str(request.form.get('media_kind', '')).strip()
    if media_url and not media_url.startswith(config.PETGIF_STORAGE_PUBLIC):
        media_url = ''
    if media_url:
        media_kind = 'gif' if media_kind == 'gif' else 'image'
        filename = None
    else:
        media_kind = None
        image = request.files.get('image')
        if image is None:
            return {'success': False, 'message': '请选择一张照片'}, 400
        try:
            filename, _ = save_upload_image(image, 'post')
        except ValueError as exc:
            return {'success': False, 'message': str(exc)}, 400
    if pet_id:
        with db.app_connect() as database:
            owned = database.execute('SELECT 1 FROM pets WHERE id=? AND user_id=?', (pet_id, user['id'])).fetchone()
        if not owned:
            pet_id = None
    created_at = datetime.now(timezone.utc).isoformat()
    with db.app_connect() as database:
        post_id = database.execute('INSERT INTO posts(user_id,pet_id,image_path,caption,location_text,created_at,media_url,media_kind) VALUES(?,?,?,?,?,?,?,?)',
                                   (user['id'], pet_id, filename or '', caption, location, created_at, media_url or None, media_kind or None)).lastrowid
    return {'success': True, 'postId': post_id}, 201


@bp.route('/posts/<int:post_id>', methods=['DELETE'])
def app_delete_post(post_id):
    user, error = require_app_user()
    if error:
        return error
    with db.app_connect() as database:
        row = database.execute('SELECT user_id, image_path FROM posts WHERE id=?', (post_id,)).fetchone()
        if not row:
            return {'success': False, 'message': '帖子不存在'}, 404
        if row['user_id'] != user['id']:
            return {'success': False, 'message': '只能删除自己的帖子'}, 403
        database.execute('DELETE FROM comments WHERE post_id=?', (post_id,))
        database.execute('DELETE FROM post_likes WHERE post_id=?', (post_id,))
        database.execute('DELETE FROM posts WHERE id=?', (post_id,))
    try:
        os.remove(os.path.join(config.UPLOADS_DIR, row['image_path']))
    except OSError:
        pass
    return {'success': True}


@bp.route('/posts/<int:post_id>/like', methods=['POST'])
def app_like_post(post_id):
    user, error = require_app_user()
    if error:
        return error
    created_at = datetime.now(timezone.utc).isoformat()
    with db.app_connect() as database:
        if database.execute('SELECT 1 FROM post_likes WHERE post_id=? AND user_id=?', (post_id, user['id'])).fetchone():
            database.execute('DELETE FROM post_likes WHERE post_id=? AND user_id=?', (post_id, user['id']))
            liked = False
        else:
            database.execute('INSERT OR IGNORE INTO post_likes(post_id,user_id,created_at) VALUES(?,?,?)',
                             (post_id, user['id'], created_at))
            liked = True
        likes = database.execute('SELECT COUNT(*) FROM post_likes WHERE post_id=?', (post_id,)).fetchone()[0]
    return {'success': True, 'liked': liked, 'likes': likes}


@bp.route('/posts/<int:post_id>/comments', methods=['GET', 'POST'])
def app_post_comments(post_id):
    if request.method == 'GET':
        with db.app_connect() as database:
            rows = database.execute('''SELECT c.id,c.body,c.created_at,u.id AS user_id,u.display_name,u.avatar_emoji
                FROM comments c JOIN users u ON u.id=c.user_id WHERE c.post_id=? ORDER BY c.id LIMIT 200''',
                                    (post_id,)).fetchall()
        return {'success': True, 'comments': [dict(row) for row in rows]}
    user, error = require_app_user()
    if error:
        return error
    body = str((request.get_json(silent=True) or {}).get('body', '')).strip()
    if not body or len(body) > 300:
        return {'success': False, 'message': '评论需为 1—300 字'}, 400
    with db.app_connect() as database:
        if not database.execute('SELECT 1 FROM posts WHERE id=?', (post_id,)).fetchone():
            return {'success': False, 'message': '帖子不存在'}, 404
        database.execute('INSERT INTO comments(post_id,user_id,body,created_at) VALUES(?,?,?,?)',
                         (post_id, user['id'], body, datetime.now(timezone.utc).isoformat()))
    return {'success': True}, 201


@bp.route('/users/<int:user_id>')
def app_user_profile(user_id):
    viewer = app_user_from_request()
    with db.app_connect() as database:
        row = database.execute('SELECT id,username,display_name,avatar_emoji,bio,created_at FROM users WHERE id=?', (user_id,)).fetchone()
        if not row:
            return {'success': False, 'message': '用户不存在'}, 404
        profile = dict(row)
        profile['posts_count'] = database.execute('SELECT COUNT(*) FROM posts WHERE user_id=?', (user_id,)).fetchone()[0]
        profile['followers'] = database.execute('SELECT COUNT(*) FROM follows WHERE followee_id=?', (user_id,)).fetchone()[0]
        profile['following'] = database.execute('SELECT COUNT(*) FROM follows WHERE follower_id=?', (user_id,)).fetchone()[0]
        profile['is_following'] = bool(viewer) and bool(database.execute(
            'SELECT 1 FROM follows WHERE follower_id=? AND followee_id=?', (viewer['id'], user_id)).fetchone())
        pets = [dict(r) for r in database.execute('SELECT id,name,species,breed,avatar_emoji,device_id FROM pets WHERE user_id=? ORDER BY id', (user_id,)).fetchall()]
    for pet in pets:
        telemetry = pet_latest_telemetry(pet['device_id'])
        pet['batteryPercent'] = telemetry.get('batteryPercent') if telemetry else None
        pet['online'] = telemetry.get('online') if telemetry else False
    profile['pets'] = pets
    profile['displayName'] = profile.pop('display_name')
    profile['avatarEmoji'] = profile.pop('avatar_emoji')
    return {'success': True, 'profile': profile}


@bp.route('/users/<int:user_id>/followers')
def app_user_followers(user_id):
    viewer = app_user_from_request()
    viewer_id = viewer['id'] if viewer else -1
    with db.app_connect() as database:
        rows = database.execute('''SELECT u.id,u.display_name,u.avatar_emoji,
            EXISTS(SELECT 1 FROM follows f2 WHERE f2.follower_id=? AND f2.followee_id=u.id) AS is_following
            FROM follows f JOIN users u ON u.id=f.follower_id WHERE f.followee_id=? ORDER BY f.created_at DESC LIMIT 200''',
                                (viewer_id, user_id)).fetchall()
    return {'success': True, 'users': [dict(row) for row in rows]}


@bp.route('/users/<int:user_id>/following')
def app_user_following(user_id):
    viewer = app_user_from_request()
    viewer_id = viewer['id'] if viewer else -1
    with db.app_connect() as database:
        rows = database.execute('''SELECT u.id,u.display_name,u.avatar_emoji,
            EXISTS(SELECT 1 FROM follows f2 WHERE f2.follower_id=? AND f2.followee_id=u.id) AS is_following
            FROM follows f JOIN users u ON u.id=f.followee_id WHERE f.follower_id=? ORDER BY f.created_at DESC LIMIT 200''',
                                (viewer_id, user_id)).fetchall()
    return {'success': True, 'users': [dict(row) for row in rows]}


@bp.route('/users/<int:user_id>/follow', methods=['POST'])
def app_follow_user(user_id):
    user, error = require_app_user()
    if error:
        return error
    if user['id'] == user_id:
        return {'success': False, 'message': '不能关注自己'}, 400
    with db.app_connect() as database:
        if not database.execute('SELECT 1 FROM users WHERE id=?', (user_id,)).fetchone():
            return {'success': False, 'message': '用户不存在'}, 404
        if database.execute('SELECT 1 FROM follows WHERE follower_id=? AND followee_id=?', (user['id'], user_id)).fetchone():
            database.execute('DELETE FROM follows WHERE follower_id=? AND followee_id=?', (user['id'], user_id))
            following = False
        else:
            database.execute('INSERT OR IGNORE INTO follows(follower_id,followee_id,created_at) VALUES(?,?,?)',
                             (user['id'], user_id, datetime.now(timezone.utc).isoformat()))
            following = True
    return {'success': True, 'following': following}
