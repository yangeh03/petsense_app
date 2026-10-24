"""社区：feed / 帖子 / 点赞 / 评论 / 关注 / 用户主页。"""

import os
import secrets
import time

from flask import Blueprint, request
from sqlalchemy import delete, func, literal, select

from petsense import config
from petsense.auth.routes import app_user_from_request, require_app_user
from petsense.db import dialect_insert, iso_z, session_scope, utc_now
from petsense.db.models import Comment, Follow, Pet, Post, PostLike, User
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


def post_rows(viewer_id, user_id=None):
    likes_count = select(func.count()).select_from(PostLike).where(PostLike.post_id == Post.id).scalar_subquery()
    comments_count = select(func.count()).select_from(Comment).where(Comment.post_id == Post.id).scalar_subquery()
    liked_by_me = select(literal(1)).where(PostLike.post_id == Post.id,
                                           PostLike.user_id == viewer_id).exists()
    stmt = (
        select(Post.id, Post.caption, Post.location_text, Post.image_path, Post.created_at,
               Post.media_url, Post.media_kind,
               User.id.label('user_id'), User.display_name, User.avatar_emoji,
               Pet.id.label('pet_id'), Pet.name.label('pet_name'), Pet.avatar_emoji.label('pet_emoji'),
               likes_count.label('likes'), comments_count.label('comments_count'),
               liked_by_me.label('liked_by_me'))
        .join(User, User.id == Post.user_id)
        .outerjoin(Pet, Pet.id == Post.pet_id)
        .order_by(Post.id.desc()).limit(60))
    if user_id is not None:
        stmt = stmt.where(Post.user_id == user_id)
    with session_scope() as s:
        rows = s.execute(stmt).mappings().fetchall()
    items = []
    for row in rows:
        item = dict(row)
        image_path = item.pop('image_path') or ''
        item['imageUrl'] = f'/uploads/{image_path}' if image_path else ''
        item['mediaUrl'] = item.pop('media_url')
        item['mediaKind'] = item.pop('media_kind')
        item['created_at'] = iso_z(item['created_at'])
        item['isMine'] = item['user_id'] == viewer_id
        items.append(item)
    return items


@bp.route('/feed')
def app_feed():
    viewer = app_user_from_request()
    user_id = request.args.get('user_id', type=int)
    items = post_rows(viewer['id'] if viewer else -1, user_id)
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
    with session_scope() as s:
        if pet_id:
            owned = s.execute(select(Pet.id).where(Pet.id == pet_id,
                                                   Pet.user_id == user['id'])).scalar_one_or_none()
            if not owned:
                pet_id = None
        post = Post(user_id=user['id'], pet_id=pet_id, image_path=filename or '',
                    caption=caption, location_text=location,
                    media_url=media_url or None, media_kind=media_kind, created_at=utc_now())
        s.add(post)
        s.flush()
        post_id = post.id
    return {'success': True, 'postId': post_id}, 201


@bp.route('/posts/<int:post_id>', methods=['DELETE'])
def app_delete_post(post_id):
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        post = s.get(Post, post_id)
        if not post:
            return {'success': False, 'message': '帖子不存在'}, 404
        if post.user_id != user['id']:
            return {'success': False, 'message': '只能删除自己的帖子'}, 403
        s.execute(delete(Comment).where(Comment.post_id == post_id))
        s.execute(delete(PostLike).where(PostLike.post_id == post_id))
        s.execute(delete(Post).where(Post.id == post_id))
        image_path = post.image_path
    try:
        os.remove(os.path.join(config.UPLOADS_DIR, image_path))
    except OSError:
        pass
    return {'success': True}


@bp.route('/posts/<int:post_id>/like', methods=['POST'])
def app_like_post(post_id):
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        existing = s.execute(select(PostLike).where(PostLike.post_id == post_id,
                                                    PostLike.user_id == user['id'])).scalar_one_or_none()
        if existing:
            s.execute(delete(PostLike).where(PostLike.post_id == post_id,
                                             PostLike.user_id == user['id']))
            liked = False
        else:
            stmt = dialect_insert(s, PostLike).values(post_id=post_id, user_id=user['id'],
                                                      created_at=utc_now())
            s.execute(stmt.on_conflict_do_nothing(index_elements=['post_id', 'user_id']))
            liked = True
        likes = s.execute(select(func.count()).select_from(PostLike)
                          .where(PostLike.post_id == post_id)).scalar_one()
    return {'success': True, 'liked': liked, 'likes': likes}


@bp.route('/posts/<int:post_id>/comments', methods=['GET', 'POST'])
def app_post_comments(post_id):
    if request.method == 'GET':
        with session_scope() as s:
            rows = s.execute(
                select(Comment.id, Comment.body, Comment.created_at,
                       User.id.label('user_id'), User.display_name, User.avatar_emoji)
                .join(User, User.id == Comment.user_id)
                .where(Comment.post_id == post_id)
                .order_by(Comment.id).limit(200)).mappings().fetchall()
        return {'success': True, 'comments': [
            {'id': row['id'], 'body': row['body'], 'created_at': iso_z(row['created_at']),
             'user_id': row['user_id'], 'display_name': row['display_name'],
             'avatar_emoji': row['avatar_emoji']} for row in rows]}
    user, error = require_app_user()
    if error:
        return error
    body = str((request.get_json(silent=True) or {}).get('body', '')).strip()
    if not body or len(body) > 300:
        return {'success': False, 'message': '评论需为 1—300 字'}, 400
    with session_scope() as s:
        if not s.get(Post, post_id):
            return {'success': False, 'message': '帖子不存在'}, 404
        s.add(Comment(post_id=post_id, user_id=user['id'], body=body, created_at=utc_now()))
    return {'success': True}, 201


@bp.route('/users/<int:user_id>')
def app_user_profile(user_id):
    viewer = app_user_from_request()
    with session_scope() as s:
        row = s.execute(select(User.id, User.username, User.display_name, User.avatar_emoji,
                               User.bio, User.created_at).where(User.id == user_id)).fetchone()
        if not row:
            return {'success': False, 'message': '用户不存在'}, 404
        profile = {'id': row.id, 'username': row.username, 'display_name': row.display_name,
                   'avatar_emoji': row.avatar_emoji, 'bio': row.bio,
                   'created_at': iso_z(row.created_at)}
        profile['posts_count'] = s.execute(select(func.count()).select_from(Post)
                                           .where(Post.user_id == user_id)).scalar_one()
        profile['followers'] = s.execute(select(func.count()).select_from(Follow)
                                         .where(Follow.followee_id == user_id)).scalar_one()
        profile['following'] = s.execute(select(func.count()).select_from(Follow)
                                         .where(Follow.follower_id == user_id)).scalar_one()
        profile['is_following'] = bool(viewer) and bool(s.execute(
            select(Follow).where(Follow.follower_id == viewer['id'],
                                 Follow.followee_id == user_id)).scalar_one_or_none())
        pets = s.execute(select(Pet).where(Pet.user_id == user_id)
                         .order_by(Pet.id)).scalars().all()
    profile['pets'] = [{'id': pet.id, 'name': pet.name, 'species': pet.species,
                        'breed': pet.breed, 'avatar_emoji': pet.avatar_emoji,
                        'device_id': pet.device_id} for pet in pets]
    for pet in profile['pets']:
        telemetry = pet_latest_telemetry(pet['device_id'])
        pet['batteryPercent'] = telemetry.get('batteryPercent') if telemetry else None
        pet['online'] = telemetry.get('online') if telemetry else False
    profile['displayName'] = profile.pop('display_name')
    profile['avatarEmoji'] = profile.pop('avatar_emoji')
    return {'success': True, 'profile': profile}


def _follow_user_rows(stmt):
    with session_scope() as s:
        return s.execute(stmt).mappings().fetchall()


@bp.route('/users/<int:user_id>/followers')
def app_user_followers(user_id):
    viewer = app_user_from_request()
    viewer_id = viewer['id'] if viewer else -1
    is_following = (select(literal(1)).select_from(Follow)
                    .where(Follow.follower_id == viewer_id, Follow.followee_id == User.id)
                    .correlate(User.__table__).exists())
    rows = _follow_user_rows(
        select(User.id, User.display_name, User.avatar_emoji, is_following.label('is_following'))
        .join(Follow, Follow.follower_id == User.id)
        .where(Follow.followee_id == user_id)
        .order_by(Follow.created_at.desc()).limit(200))
    return {'success': True, 'users': [dict(row) for row in rows]}


@bp.route('/users/<int:user_id>/following')
def app_user_following(user_id):
    viewer = app_user_from_request()
    viewer_id = viewer['id'] if viewer else -1
    is_following = (select(literal(1)).select_from(Follow)
                    .where(Follow.follower_id == viewer_id, Follow.followee_id == User.id)
                    .correlate(User.__table__).exists())
    rows = _follow_user_rows(
        select(User.id, User.display_name, User.avatar_emoji, is_following.label('is_following'))
        .join(Follow, Follow.followee_id == User.id)
        .where(Follow.follower_id == user_id)
        .order_by(Follow.created_at.desc()).limit(200))
    return {'success': True, 'users': [dict(row) for row in rows]}


@bp.route('/users/<int:user_id>/follow', methods=['POST'])
def app_follow_user(user_id):
    user, error = require_app_user()
    if error:
        return error
    if user['id'] == user_id:
        return {'success': False, 'message': '不能关注自己'}, 400
    with session_scope() as s:
        if not s.get(User, user_id):
            return {'success': False, 'message': '用户不存在'}, 404
        existing = s.execute(select(Follow).where(Follow.follower_id == user['id'],
                                                  Follow.followee_id == user_id)).scalar_one_or_none()
        if existing:
            s.execute(delete(Follow).where(Follow.follower_id == user['id'],
                                           Follow.followee_id == user_id))
            following = False
        else:
            stmt = dialect_insert(s, Follow).values(follower_id=user['id'], followee_id=user_id,
                                                    created_at=utc_now())
            s.execute(stmt.on_conflict_do_nothing(index_elements=['follower_id', 'followee_id']))
            following = True
    return {'success': True, 'following': following}
