"""社区：feed / 帖子 / 点赞 / 评论 / 关注 / 用户主页。"""

import os
import secrets
import time
import json
import math
from io import BytesIO
from datetime import timedelta

from PIL import Image, ImageOps, UnidentifiedImageError

from flask import Blueprint, request, send_from_directory
from sqlalchemy import delete, func, literal, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from petsense import config
from petsense.auth.routes import app_user_from_request, require_app_user
from petsense.db import dialect_insert, iso_z, session_scope, utc_now
from petsense.db.models import (Comment, DiscoveryLocation, DiscoveryQuery, Follow, Pet,
                                Post, PostBookmark, PostLike, PostMedia, Story, StoryView, User)
from petsense.pets.routes import pet_dict, pet_latest_telemetry
from petsense.social.jobs import queue_social_event
from petsense.db.models import SocialNotification

bp = Blueprint('social', __name__, url_prefix='/api/app')

def visible_post_filter(viewer_id):
    following = select(Follow.followee_id).where(Follow.follower_id == viewer_id)
    return (Post.deleted_at.is_(None),
            or_(Post.audience == 'public', Post.user_id == viewer_id,
                Post.user_id.in_(following)))


def get_visible_post(session, post_id, viewer_id):
    return session.execute(select(Post).where(Post.id == post_id,
                    *visible_post_filter(viewer_id))).scalar_one_or_none()


def find_mutation_row(session, model, user_id, mutation_id):
    """按 (作者, 幂等键) 查已有写入；并发测试通过替换此函数模拟预检查未命中。"""
    return session.execute(select(model).where(model.user_id == user_id,
                    model.client_mutation_id == mutation_id)).scalar_one_or_none()


def insert_with_mutation_key(session, row, model, user_id, mutation_id, lookup=find_mutation_row):
    """在 SAVEPOINT 内插入；并发同键撞唯一约束时回滚到保存点并返回赢家行。

    返回 (row, None) 表示插入成功；(None, winner) 表示应按幂等重放处理；
    (None, None) 表示撞了约束但当前事务快照里还看不到赢家，调用方应返回可重试错误。
    """
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
        return row, None
    except IntegrityError:
        if not mutation_id:
            raise
    return None, lookup(session, model, user_id, mutation_id)


MUTATION_RETRY = {'success': False, 'message': '操作正在处理，请稍后重试'}, 409


def remove_social_media(*paths):
    for path in paths:
        try:
            os.remove(os.path.join(config.SOCIAL_MEDIA_DIR, path))
        except OSError:
            pass


def save_upload_image(file_storage, prefix):
    data = file_storage.read(config.MAX_UPLOAD_BYTES + 1)
    if not data or len(data) > config.MAX_UPLOAD_BYTES:
        raise ValueError('图片为空或超过 10MB')
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValueError('仅支持 JPG / PNG / WebP 图片')
            if source.width * source.height > 36_000_000:
                raise ValueError('图片像素过大')
            source.verify()
        with Image.open(BytesIO(data)) as source:
            oriented = ImageOps.exif_transpose(source)
            image = oriented.convert('RGB')
            output = BytesIO()
            image.save(output, format='JPEG', quality=85, optimize=True)
            cleaned = output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('图片已损坏或格式不支持') from None
    if len(cleaned) > config.MAX_UPLOAD_BYTES:
        raise ValueError('处理后的图片超过 10MB')
    if not cleaned:
        raise ValueError('仅支持 JPG / PNG / WebP 图片')
    filename = f'{prefix}_{secrets.token_hex(6)}_{int(time.time())}.jpg'
    with open(os.path.join(config.SOCIAL_MEDIA_DIR, filename), 'wb') as handle:
        handle.write(cleaned)
    return filename, 'image/jpeg'


def post_rows(viewer_id, user_id=None, *, limit=60, cursor=None, scope='discover', post_id=None,
              search=None, bookmark_user_id=None, author_ids=None, place=None):
    likes_count = select(func.count()).select_from(PostLike).where(PostLike.post_id == Post.id).scalar_subquery()
    comments_count = select(func.count()).select_from(Comment).where(
        Comment.post_id == Post.id, Comment.deleted_at.is_(None)).scalar_subquery()
    liked_by_me = select(literal(1)).where(PostLike.post_id == Post.id,
                                           PostLike.user_id == viewer_id).exists()
    bookmarked_by_me = select(literal(1)).where(PostBookmark.post_id == Post.id,
                                               PostBookmark.user_id == viewer_id).exists()
    stmt = (
        select(Post.id, Post.caption, Post.location_text, Post.image_path, Post.created_at,
               Post.updated_at, Post.version, Post.audience, Post.media_url, Post.media_kind,
               User.id.label('user_id'), User.display_name, User.avatar_emoji,
               Pet.id.label('pet_id'), Pet.name.label('pet_name'), Pet.avatar_emoji.label('pet_emoji'),
               likes_count.label('likes'), comments_count.label('comments_count'),
               liked_by_me.label('liked_by_me'),
               bookmarked_by_me.label('bookmarked_by_me'))
        .join(User, User.id == Post.user_id)
        .outerjoin(Pet, Pet.id == Post.pet_id)
        .where(*visible_post_filter(viewer_id))
        .order_by(Post.id.desc()).limit(limit + 1))
    if user_id is not None:
        stmt = stmt.where(Post.user_id == user_id)
    if author_ids is not None:
        stmt = stmt.where(Post.user_id.in_(author_ids))
    if cursor is not None:
        stmt = stmt.where(Post.id < cursor)
    if post_id is not None:
        stmt = stmt.where(Post.id == post_id)
    if search:
        stmt = stmt.where(or_(Post.caption.icontains(search, autoescape=True),
                              Post.location_text.icontains(search, autoescape=True),
                              User.display_name.icontains(search, autoescape=True),
                              Pet.name.icontains(search, autoescape=True)))
    if place is not None:
        stmt = stmt.where(Post.location_text == place)
    if bookmark_user_id is not None:
        stmt = stmt.where(Post.id.in_(select(PostBookmark.post_id).where(
            PostBookmark.user_id == bookmark_user_id)))
    if scope == 'following':
        stmt = stmt.where(or_(Post.user_id == viewer_id, Post.user_id.in_(
            select(Follow.followee_id).where(Follow.follower_id == viewer_id))))
    with session_scope() as s:
        rows = s.execute(stmt).mappings().fetchall()
        media_rows = s.execute(select(PostMedia).where(PostMedia.post_id.in_(
            [row['id'] for row in rows[:limit]]))).scalars().all() if rows else []
    media_by_post = {}
    for media in media_rows:
        media_by_post.setdefault(media.post_id, []).append(media)
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for row in rows:
        item = dict(row)
        image_path = item.pop('image_path') or ''
        media_items = sorted(media_by_post.get(item['id'], []), key=lambda media: media.position)
        item['imageUrl'] = (f'/api/app/posts/{item["id"]}/media/0' if media_items else
                            f'/uploads/{image_path}' if image_path else '')
        item['mediaUrl'] = item.pop('media_url')
        item['mediaKind'] = item.pop('media_kind')
        item['media'] = [{'url': f'/api/app/posts/{item["id"]}/media/{media.position}',
                          'kind': media.kind} for media in media_items]
        if not item['media'] and image_path:
            item['media'] = [{'url': f'/uploads/{image_path}', 'kind': 'image'}]
        item['created_at'] = iso_z(item['created_at'])
        item['updated_at'] = iso_z(item['updated_at']) if item['updated_at'] else None
        item['isMine'] = item['user_id'] == viewer_id
        items.append(item)
    return items, has_more


@bp.route('/feed')
def app_feed():
    viewer = app_user_from_request()
    user_id = request.args.get('user_id', type=int)
    raw_cursor = request.args.get('cursor')
    raw_limit = request.args.get('limit')
    scope = request.args.get('scope', 'discover')
    if scope not in ('discover', 'following'):
        return {'success': False, 'message': '无效的动态范围'}, 400
    if scope == 'following' and not viewer:
        return {'success': False, 'message': '请先登录'}, 401
    try:
        cursor = int(raw_cursor) if raw_cursor is not None else None
        limit = int(raw_limit) if raw_limit is not None else 60
    except ValueError:
        return {'success': False, 'message': '无效的分页参数'}, 400
    if (cursor is not None and cursor <= 0) or not 1 <= limit <= 60:
        return {'success': False, 'message': '无效的分页参数'}, 400
    items, has_more = post_rows(viewer['id'] if viewer else -1, user_id,
                                limit=limit, cursor=cursor, scope=scope)
    return {'success': True, 'posts': items,
            'page': {'hasMore': has_more,
                     'nextCursor': str(items[-1]['id']) if has_more and items else None}}


@bp.route('/posts/<int:post_id>', methods=['GET'])
def app_get_post(post_id):
    viewer = app_user_from_request()
    items, _ = post_rows(viewer['id'] if viewer else -1, limit=1, post_id=post_id)
    if not items:
        return {'success': False, 'message': '帖子不存在'}, 404
    return {'success': True, 'post': items[0]}


@bp.route('/search')
def app_search():
    viewer = app_user_from_request()
    viewer_id = viewer['id'] if viewer else -1
    query = request.args.get('q', '').strip()
    kind = request.args.get('type', 'all')
    place = request.args.get('place')
    if kind not in ('all', 'posts', 'users', 'places'):
        return {'success': False, 'message': '无效的搜索分类'}, 400
    if place is not None and (kind != 'posts' or not place or len(place) > 80):
        return {'success': False, 'message': '无效的地点筛选'}, 400
    if not 2 <= len(query) <= 50:
        return {'success': False, 'message': '请输入 2—50 个字搜索'}, 400
    try:
        raw_cursor = request.args.get('cursor')
        cursor = raw_cursor if kind == 'places' else (int(raw_cursor) if raw_cursor else None)
        limit = int(request.args.get('limit', 20))
    except ValueError:
        return {'success': False, 'message': '无效的分页参数'}, 400
    if not 1 <= limit <= 50 or (isinstance(cursor, int) and cursor <= 0) or (
            isinstance(cursor, str) and len(cursor) > 80):
        return {'success': False, 'message': '无效的分页参数'}, 400
    result = {'success': True, 'posts': [], 'users': [], 'places': [],
              'page': {'nextCursor': None, 'hasMore': False}}
    if kind in ('all', 'posts'):
        posts, more = post_rows(viewer_id, limit=limit if kind == 'posts' else 5,
                                cursor=cursor if kind == 'posts' else None,
                                search=query, place=place)
        result['posts'] = posts
        if kind == 'posts':
            result['page'] = {'hasMore': more,
                              'nextCursor': str(posts[-1]['id']) if more and posts else None}
    with session_scope() as s:
        if kind in ('all', 'users'):
            user_match = or_(User.username.icontains(query, autoescape=True),
                             User.display_name.icontains(query, autoescape=True),
                             User.id.in_(select(Pet.user_id).where(
                                 Pet.name.icontains(query, autoescape=True))))
            stmt = select(User.id, User.display_name, User.avatar_emoji).where(user_match)
            if kind == 'users' and cursor:
                stmt = stmt.where(User.id < cursor)
            rows = s.execute(stmt.order_by(User.id.desc()).limit(
                (limit if kind == 'users' else 5) + 1)).mappings().all()
            page_limit = limit if kind == 'users' else 5
            result['users'] = [dict(row) for row in rows[:page_limit]]
            if kind == 'users':
                result['page'] = {'hasMore': len(rows) > page_limit,
                    'nextCursor': str(rows[page_limit - 1]['id']) if len(rows) > page_limit else None}
        if kind in ('all', 'places'):
            stmt = select(Post.location_text.label('name'),
                          func.count().label('postsCount')).where(
                *visible_post_filter(viewer_id), Post.location_text != '',
                Post.location_text.icontains(query, autoescape=True))
            if kind == 'places' and cursor:
                stmt = stmt.where(Post.location_text > cursor)
            page_limit = limit if kind == 'places' else 5
            rows = s.execute(stmt.group_by(Post.location_text).order_by(Post.location_text)
                             .limit(page_limit + 1)).mappings().all()
            result['places'] = [dict(row) for row in rows[:page_limit]]
            if kind == 'places':
                result['page'] = {'hasMore': len(rows) > page_limit,
                    'nextCursor': result['places'][-1]['name'] if len(rows) > page_limit else None}
    return result


@bp.route('/posts', methods=['POST'])
def app_create_post():
    user, error = require_app_user()
    if error:
        return error
    caption = str(request.form.get('caption', '')).strip()
    location = str(request.form.get('location', '')).strip()
    pet_id_raw = request.form.get('pet_id')
    try:
        pet_id = int(pet_id_raw) if pet_id_raw else None
    except ValueError:
        return {'success': False, 'message': '关联宠物无效'}, 400
    if pet_id is not None:
        if pet_id <= 0:
            return {'success': False, 'message': '关联宠物无效'}, 400
        with session_scope() as s:
            owned = s.execute(select(Pet.id).where(Pet.id == pet_id,
                        Pet.user_id == user['id'])).scalar_one_or_none()
        if not owned:
            return {'success': False, 'message': '关联宠物不存在或不属于你'}, 400
    if len(caption) > 500:
        return {'success': False, 'message': '文案最多 500 字'}, 400
    if len(location) > 80:
        return {'success': False, 'message': '地点名称最多 80 字'}, 400
    audience = str(request.form.get('audience', 'public'))
    if audience not in ('public', 'followers'):
        return {'success': False, 'message': '无效的可见范围'}, 400
    mutation_id = str(request.form.get('client_mutation_id', '')).strip() or None
    if mutation_id and len(mutation_id) > 80:
        return {'success': False, 'message': '无效的操作标识'}, 400
    media_url = str(request.form.get('media_url', '')).strip()
    media_kind = str(request.form.get('media_kind', '')).strip()
    if media_url and not media_url.startswith(config.PETGIF_STORAGE_PUBLIC):
        return {'success': False, 'message': '外部媒体地址无效'}, 400
    if media_url and audience == 'followers':
        return {'success': False, 'message': '外部媒体暂不支持仅粉丝可见'}, 400
    if media_url:
        media_kind = 'gif' if media_kind == 'gif' else 'image'
        filename = None
        filenames = []
    else:
        media_kind = None
        images = request.files.getlist('images') or request.files.getlist('image')
        if not images and not caption:
            return {'success': False, 'message': '请输入文字或选择照片'}, 400
        if len(images) > 10:
            return {'success': False, 'message': '最多选择 10 张照片'}, 400
        filenames = []
        try:
            for image in images:
                path, _ = save_upload_image(image, 'post')
                filenames.append(path)
        except ValueError as exc:
            remove_social_media(*filenames)
            return {'success': False, 'message': str(exc)}, 400
        filename = filenames[0] if filenames else None

    def replay_response(old):
        remove_social_media(*filenames)
        if old.deleted_at is not None:
            return {'success': False, 'message': '帖子已删除'}, 410
        if old.caption != caption or old.audience != audience or old.pet_id != pet_id:
            return {'success': False, 'message': '操作标识已用于其他帖子'}, 409
        return {'success': True, 'postId': old.id}, 200

    with session_scope() as s:
        if mutation_id:
            old = find_mutation_row(s, Post, user['id'], mutation_id)
            if old:
                return replay_response(old)
        post = Post(user_id=user['id'], pet_id=pet_id, image_path=filename or '',
                    caption=caption, location_text=location,
                    media_url=media_url or None, media_kind=media_kind, created_at=utc_now(),
                    audience=audience, client_mutation_id=mutation_id)
        post, winner = insert_with_mutation_key(s, post, Post, user['id'], mutation_id)
        if post is None:
            if winner is None:
                remove_social_media(*filenames)
                return MUTATION_RETRY
            return replay_response(winner)
        for position, path in enumerate(filenames):
            s.add(PostMedia(post_id=post.id, position=position, path=path, kind='image'))
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
        if post.deleted_at is not None:
            return {'success': True}
        media_rows = s.execute(select(PostMedia)
                 .where(PostMedia.post_id == post_id)).scalars().all()
        paths = [(config.SOCIAL_MEDIA_DIR, row.path) for row in media_rows]
        if post.image_path and not media_rows:
            paths.append((config.UPLOADS_DIR, post.image_path))
        post.deleted_at = utc_now()
        post.image_path = ''
        post.media_url = None
        s.execute(delete(PostMedia).where(PostMedia.post_id == post_id))
        s.execute(delete(PostLike).where(PostLike.post_id == post_id))
        s.execute(delete(PostBookmark).where(PostBookmark.post_id == post_id))
    for directory, path in paths:
        try:
            os.remove(os.path.join(directory, path))
        except OSError:
            pass
    return {'success': True}


@bp.route('/posts/<int:post_id>/media/<int:position>')
def app_post_media(post_id, position):
    viewer = app_user_from_request()
    with session_scope() as s:
        post = get_visible_post(s, post_id, viewer['id'] if viewer else -1)
        if not post:
            return {'success': False, 'message': '图片不可用'}, 404
        media = s.execute(select(PostMedia).where(PostMedia.post_id == post_id,
            PostMedia.position == position)).scalar_one_or_none()
        if not media:
            return {'success': False, 'message': '图片不可用'}, 404
        path = media.path
    return send_from_directory(config.SOCIAL_MEDIA_DIR, path)


@bp.route('/posts/<int:post_id>', methods=['PATCH'])
def app_edit_post(post_id):
    user, error = require_app_user()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    version = payload.get('version')
    if not isinstance(version, int) or version <= 0:
        return {'success': False, 'message': '请提供帖子版本'}, 400
    caption = payload.get('caption')
    audience = payload.get('audience')
    location = payload.get('location')
    if caption is not None and (not isinstance(caption, str) or len(caption.strip()) > 500):
        return {'success': False, 'message': '文案最多 500 字'}, 400
    if audience is not None and audience not in ('public', 'followers'):
        return {'success': False, 'message': '无效的可见范围'}, 400
    if location is not None and (not isinstance(location, str) or len(location.strip()) > 80):
        return {'success': False, 'message': '地点名称最多 80 字'}, 400
    with session_scope() as s:
        post = s.get(Post, post_id)
        if not post or post.deleted_at is not None:
            return {'success': False, 'message': '帖子不存在'}, 404
        if post.user_id != user['id']:
            return {'success': False, 'message': '只能编辑自己的帖子'}, 403
        if post.version != version:
            return {'success': False, 'message': '帖子已在其他设备更新，请刷新后重试'}, 409
        new_caption = caption.strip() if caption is not None else post.caption
        if not new_caption and not post.image_path and not post.media_url:
            return {'success': False, 'message': '请保留文字或照片'}, 400
        if audience == 'followers' and (post.media_url or (post.image_path and not s.execute(
                select(PostMedia.id).where(PostMedia.post_id == post_id).limit(1)).scalar_one_or_none())):
            return {'success': False, 'message': '这条历史媒体暂不能改为仅粉丝可见'}, 400
        post.caption = new_caption
        if location is not None:
            post.location_text = location.strip()
        if audience is not None:
            post.audience = audience
        post.version += 1
        post.updated_at = utc_now()
    items, _ = post_rows(user['id'], limit=1, post_id=post_id)
    return {'success': True, 'post': items[0]}


@bp.route('/posts/<int:post_id>/like', methods=['POST', 'PUT', 'DELETE'])
def app_like_post(post_id):
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        post = get_visible_post(s, post_id, user['id'])
        if not post:
            return {'success': False, 'message': '帖子不存在'}, 404
        existing = s.execute(select(PostLike).where(PostLike.post_id == post_id,
                                                    PostLike.user_id == user['id'])).scalar_one_or_none()
        desired = request.method == 'PUT' or (request.method == 'POST' and not existing)
        if existing and not desired:
            s.execute(delete(PostLike).where(PostLike.post_id == post_id,
                                             PostLike.user_id == user['id']))
            liked = False
        elif not existing and desired:
            stmt = dialect_insert(s, PostLike).values(post_id=post_id, user_id=user['id'],
                                                      created_at=utc_now())
            s.execute(stmt.on_conflict_do_nothing(index_elements=['post_id', 'user_id']))
            liked = True
            queue_social_event(s, f'like:{user["id"]}:{post_id}', 'like',
                               user['id'], post.user_id, post_id=post_id)
        else:
            liked = bool(existing)
        likes = s.execute(select(func.count()).select_from(PostLike)
                          .where(PostLike.post_id == post_id)).scalar_one()
    return {'success': True, 'liked': liked, 'likes': likes}


@bp.route('/posts/<int:post_id>/bookmark', methods=['PUT', 'DELETE'])
def app_bookmark_post(post_id):
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        post = get_visible_post(s, post_id, user['id'])
        if not post:
            return {'success': False, 'message': '帖子不存在'}, 404
        if request.method == 'PUT':
            stmt = dialect_insert(s, PostBookmark).values(post_id=post_id, user_id=user['id'],
                                                            created_at=utc_now())
            s.execute(stmt.on_conflict_do_nothing(index_elements=['post_id', 'user_id']))
        else:
            s.execute(delete(PostBookmark).where(PostBookmark.post_id == post_id,
                                                 PostBookmark.user_id == user['id']))
    return {'success': True, 'bookmarked': request.method == 'PUT'}


@bp.route('/me/bookmarks')
def app_my_bookmarks():
    user, error = require_app_user()
    if error:
        return error
    try:
        cursor = int(request.args['cursor']) if 'cursor' in request.args else None
        limit = int(request.args.get('limit', 20))
    except ValueError:
        return {'success': False, 'message': '无效的分页参数'}, 400
    if not 1 <= limit <= 50 or (cursor is not None and cursor <= 0):
        return {'success': False, 'message': '无效的分页参数'}, 400
    items, more = post_rows(user['id'], limit=limit, cursor=cursor,
                            bookmark_user_id=user['id'])
    return {'success': True, 'posts': items,
            'page': {'hasMore': more,
                     'nextCursor': str(items[-1]['id']) if more and items else None}}


@bp.route('/posts/<int:post_id>/comments', methods=['GET', 'POST'])
def app_post_comments(post_id):
    if request.method == 'GET':
        viewer = app_user_from_request()
        try:
            cursor = int(request.args['cursor']) if 'cursor' in request.args else 0
            limit = int(request.args.get('limit', 20))
        except ValueError:
            return {'success': False, 'message': '无效的分页参数'}, 400
        if cursor < 0 or not 1 <= limit <= 50:
            return {'success': False, 'message': '无效的分页参数'}, 400
        with session_scope() as s:
            if not get_visible_post(s, post_id, viewer['id'] if viewer else -1):
                return {'success': False, 'message': '帖子不存在'}, 404
            active_reply = aliased(Comment)
            has_active_replies = select(literal(1)).where(
                active_reply.root_id == Comment.id,
                active_reply.deleted_at.is_(None)).exists()
            stmt = (select(Comment, User.display_name, User.avatar_emoji)
                    .join(User, User.id == Comment.user_id)
                    .where(Comment.post_id == post_id, Comment.root_id.is_(None),
                           or_(Comment.deleted_at.is_(None), has_active_replies),
                           Comment.id > cursor)
                    .order_by(Comment.id).limit(limit + 1))
            rows = s.execute(stmt).all()
            has_more = len(rows) > limit
            comments = [comment_dict(row[0], row[1], row[2]) for row in rows[:limit]]
        return {'success': True, 'comments': comments,
                'page': {'hasMore': has_more,
                         'nextCursor': str(comments[-1]['id']) if has_more and comments else None}}
    user, error = require_app_user()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    body = str(payload.get('body', '')).strip()
    if not body or len(body) > 300:
        return {'success': False, 'message': '评论需为 1—300 字'}, 400
    mutation_id = str(payload.get('clientMutationId') or '').strip() or None
    if mutation_id and len(mutation_id) > 80:
        return {'success': False, 'message': '无效的操作标识'}, 400
    reply_to_id = payload.get('replyToCommentId')
    if reply_to_id is not None and (not isinstance(reply_to_id, int) or reply_to_id <= 0):
        return {'success': False, 'message': '无效的回复目标'}, 400
    def replay_response(old):
        if old.post_id != post_id or old.body != body or old.reply_to_comment_id != reply_to_id:
            return {'success': False, 'message': '操作标识已用于其他评论'}, 409
        return {'success': True, 'comment': comment_dict(old, user['display_name'],
                user['avatar_emoji'])}, 200

    with session_scope() as s:
        post = get_visible_post(s, post_id, user['id'])
        if not post:
            return {'success': False, 'message': '帖子不存在'}, 404
        if mutation_id:
            old = find_mutation_row(s, Comment, user['id'], mutation_id)
            if old:
                return replay_response(old)
        root_id = None
        if reply_to_id is not None:
            target = s.get(Comment, reply_to_id)
            if not target or target.post_id != post_id or target.deleted_at is not None:
                return {'success': False, 'message': '回复目标不可用'}, 404
            root_id = target.root_id or target.id
        comment = Comment(post_id=post_id, user_id=user['id'], body=body,
                          root_id=root_id, reply_to_comment_id=reply_to_id,
                          client_mutation_id=mutation_id, created_at=utc_now())
        comment, winner = insert_with_mutation_key(s, comment, Comment, user['id'], mutation_id)
        if comment is None:
            return MUTATION_RETRY if winner is None else replay_response(winner)
        queue_social_event(s, f'comment:{comment.id}:post', 'comment',
                           user['id'], post.user_id, post_id=post_id,
                           comment_id=comment.id)
        if reply_to_id is not None and target.user_id != post.user_id:
            queue_social_event(s, f'comment:{comment.id}:reply', 'reply',
                               user['id'], target.user_id, post_id=post_id,
                               comment_id=comment.id)
        result = comment_dict(comment, user['display_name'], user['avatar_emoji'])
    return {'success': True, 'comment': result}, 201


def comment_dict(comment, display_name, avatar_emoji):
    return {'id': comment.id, 'body': comment.body if comment.deleted_at is None else '',
            'created_at': iso_z(comment.created_at), 'user_id': comment.user_id,
            'display_name': display_name, 'avatar_emoji': avatar_emoji,
            'root_id': comment.root_id, 'reply_to_comment_id': comment.reply_to_comment_id,
            'deleted': comment.deleted_at is not None}


@bp.route('/comments/<int:root_id>/replies')
def app_comment_replies(root_id):
    viewer = app_user_from_request()
    try:
        cursor = int(request.args.get('cursor', 0))
        limit = int(request.args.get('limit', 20))
    except ValueError:
        return {'success': False, 'message': '无效的分页参数'}, 400
    if cursor < 0 or not 1 <= limit <= 50:
        return {'success': False, 'message': '无效的分页参数'}, 400
    with session_scope() as s:
        root = s.get(Comment, root_id)
        if not root or root.root_id is not None or not get_visible_post(
                s, root.post_id, viewer['id'] if viewer else -1):
            return {'success': False, 'message': '评论不存在'}, 404
        rows = s.execute(select(Comment, User.display_name, User.avatar_emoji)
                         .join(User, User.id == Comment.user_id)
                         .where(Comment.root_id == root_id, Comment.deleted_at.is_(None),
                                Comment.id > cursor)
                         .order_by(Comment.id).limit(limit + 1)).all()
        has_more = len(rows) > limit
        comments = [comment_dict(row[0], row[1], row[2]) for row in rows[:limit]]
    return {'success': True, 'comments': comments,
            'page': {'hasMore': has_more,
                     'nextCursor': str(comments[-1]['id']) if has_more and comments else None}}


@bp.route('/comments/<int:comment_id>/context')
def app_comment_context(comment_id):
    """通知定位用：告诉客户端这条评论属于哪个帖子、是根评论还是哪一楼的回复。"""
    viewer = app_user_from_request()
    with session_scope() as s:
        comment = s.get(Comment, comment_id)
        if not comment or not get_visible_post(s, comment.post_id,
                                                viewer['id'] if viewer else -1):
            return {'success': False, 'message': '评论不存在'}, 404
        context = {'commentId': comment.id, 'rootId': comment.root_id, 'postId': comment.post_id}
    return {'success': True, 'context': context}


@bp.route('/comments/<int:comment_id>', methods=['DELETE'])
def app_delete_comment(comment_id):
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        comment = s.get(Comment, comment_id)
        if not comment:
            return {'success': False, 'message': '评论不存在'}, 404
        if comment.user_id != user['id']:
            return {'success': False, 'message': '只能删除自己的评论'}, 403
        if comment.deleted_at is None:
            comment.body = ''
            comment.deleted_at = utc_now()
    return {'success': True}


@bp.route('/users/<int:user_id>')
def app_user_profile(user_id):
    viewer = app_user_from_request()
    with session_scope() as s:
        row = s.execute(select(User.id, User.username, User.display_name, User.avatar_emoji,
                               User.bio, User.profile_version, User.created_at)
                        .where(User.id == user_id)).fetchone()
        if not row:
            return {'success': False, 'message': '用户不存在'}, 404
        profile = {'id': row.id, 'username': row.username, 'display_name': row.display_name,
                   'avatar_emoji': row.avatar_emoji, 'bio': row.bio,
                   'created_at': iso_z(row.created_at)}
        if viewer and viewer['id'] == user_id:
            profile['version'] = row.profile_version
        profile['posts_count'] = s.execute(select(func.count()).select_from(Post)
                                           .where(Post.user_id == user_id,
                                                  *visible_post_filter(viewer['id'] if viewer else -1))
                                           ).scalar_one()
        profile['followers'] = s.execute(select(func.count()).select_from(Follow)
                                         .where(Follow.followee_id == user_id)).scalar_one()
        profile['following'] = s.execute(select(func.count()).select_from(Follow)
                                         .where(Follow.follower_id == user_id)).scalar_one()
        profile['is_following'] = bool(viewer) and bool(s.execute(
            select(Follow).where(Follow.follower_id == viewer['id'],
                                 Follow.followee_id == user_id)).scalar_one_or_none())
        pets = s.execute(select(Pet).where(Pet.user_id == user_id)
                         .order_by(Pet.id)).scalars().all()
    profile['pets'] = [pet_dict(pet, public=not viewer or viewer['id'] != user_id)
                       for pet in pets]
    profile['displayName'] = profile.pop('display_name')
    profile['avatarEmoji'] = profile.pop('avatar_emoji')
    return {'success': True, 'profile': profile}


@bp.route('/me/public-profile', methods=['GET', 'PATCH'])
def app_my_public_profile():
    user, error = require_app_user()
    if error:
        return error
    if request.method == 'GET':
        return app_user_profile(user['id'])
    payload = request.get_json(silent=True) or {}
    version = payload.get('version')
    if not isinstance(version, int) or version <= 0:
        return {'success': False, 'message': '请提供资料版本'}, 400
    name = payload.get('displayName')
    bio = payload.get('bio')
    emoji = payload.get('avatarEmoji')
    if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 40):
        return {'success': False, 'message': '昵称需为 1—40 字'}, 400
    if bio is not None and (not isinstance(bio, str) or len(bio) > 300):
        return {'success': False, 'message': '简介最多 300 字'}, 400
    if emoji is not None and (not isinstance(emoji, str) or not 1 <= len(emoji) <= 16):
        return {'success': False, 'message': '头像表情无效'}, 400
    with session_scope() as s:
        row = s.get(User, user['id'])
        if row.profile_version != version:
            return {'success': False, 'message': '资料已在其他设备更新，请刷新后重试'}, 409
        if name is not None:
            row.display_name = name.strip()
        if bio is not None:
            row.bio = bio.strip()
        if emoji is not None:
            row.avatar_emoji = emoji
        row.profile_version += 1
    return app_user_profile(user['id'])


def follow_list(user_id, kind):
    viewer = app_user_from_request()
    viewer_id = viewer['id'] if viewer else -1
    try:
        cursor = int(request.args['cursor']) if 'cursor' in request.args else None
        limit = int(request.args.get('limit', 20))
    except ValueError:
        return {'success': False, 'message': '无效的分页参数'}, 400
    if not 1 <= limit <= 50 or (cursor is not None and cursor <= 0):
        return {'success': False, 'message': '无效的分页参数'}, 400
    is_following = (select(literal(1)).select_from(Follow)
                    .where(Follow.follower_id == viewer_id, Follow.followee_id == User.id)
                    .correlate(User.__table__).exists())
    stmt = (select(User.id, User.display_name, User.avatar_emoji,
                   is_following.label('is_following')).join(Follow,
                   Follow.follower_id == User.id if kind == 'followers' else
                   Follow.followee_id == User.id)
            .where(Follow.followee_id == user_id if kind == 'followers' else
                   Follow.follower_id == user_id))
    if cursor is not None:
        stmt = stmt.where(User.id < cursor)
    with session_scope() as s:
        if not s.get(User, user_id):
            return {'success': False, 'message': '用户不存在'}, 404
        rows = s.execute(stmt.order_by(User.id.desc()).limit(limit + 1)).mappings().all()
    items = [dict(row) for row in rows[:limit]]
    has_more = len(rows) > limit
    return {'success': True, 'users': items,
            'page': {'hasMore': has_more,
                     'nextCursor': str(items[-1]['id']) if has_more and items else None}}


@bp.route('/users/<int:user_id>/followers')
def app_user_followers(user_id):
    return follow_list(user_id, 'followers')


@bp.route('/users/<int:user_id>/following')
def app_user_following(user_id):
    return follow_list(user_id, 'following')


@bp.route('/users/<int:user_id>/follow', methods=['POST', 'PUT', 'DELETE'])
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
        desired = request.method == 'PUT' or (request.method == 'POST' and not existing)
        if existing and not desired:
            s.execute(delete(Follow).where(Follow.follower_id == user['id'],
                                           Follow.followee_id == user_id))
            following = False
        elif not existing and desired:
            stmt = dialect_insert(s, Follow).values(follower_id=user['id'], followee_id=user_id,
                                                    created_at=utc_now())
            s.execute(stmt.on_conflict_do_nothing(index_elements=['follower_id', 'followee_id']))
            following = True
            queue_social_event(s, f'follow:{user["id"]}:{user_id}', 'follow',
                               user['id'], user_id)
        else:
            following = bool(existing)
    return {'success': True, 'following': following}


def accessible_story(session, story_id, viewer_id):
    story = session.get(Story, story_id)
    if not story or story.deleted_at is not None or story.expires_at <= utc_now():
        return None
    if story.user_id == viewer_id or story.audience == 'public':
        return story
    followed = session.execute(select(Follow.follower_id).where(
        Follow.follower_id == viewer_id, Follow.followee_id == story.user_id)).scalar_one_or_none()
    return story if followed else None


def story_dict(story, user, viewed=False):
    return {'id': story.id, 'user_id': story.user_id,
            'display_name': user.display_name, 'avatar_emoji': user.avatar_emoji,
            'imageUrl': f'/api/app/stories/{story.id}/media', 'caption': story.caption,
            'audience': story.audience, 'created_at': iso_z(story.created_at),
            'expires_at': iso_z(story.expires_at), 'viewed': viewed}


@bp.route('/stories', methods=['POST'])
def app_create_story():
    user, error = require_app_user()
    if error:
        return error
    audience = request.form.get('audience', 'followers')
    caption = request.form.get('caption', '').strip()
    mutation_id = request.form.get('client_mutation_id', '').strip() or None
    if audience not in ('public', 'followers') or len(caption) > 300 or (
            mutation_id and len(mutation_id) > 80):
        return {'success': False, 'message': '故事参数无效'}, 400
    image = request.files.get('image')
    if image is None:
        return {'success': False, 'message': '请选择故事照片'}, 400
    try:
        path, _ = save_upload_image(image, 'story')
    except ValueError as exc:
        return {'success': False, 'message': str(exc)}, 400

    def replay_response(s, old):
        remove_social_media(path)
        if old.caption != caption or old.audience != audience:
            return {'success': False, 'message': '操作标识已用于其他故事'}, 409
        return {'success': True, 'story': story_dict(old, s.get(User, user['id']))}, 200

    with session_scope() as s:
        if mutation_id:
            old = find_mutation_row(s, Story, user['id'], mutation_id)
            if old:
                return replay_response(s, old)
        now = utc_now()
        story = Story(user_id=user['id'], image_path=path, caption=caption,
                      audience=audience, created_at=now, expires_at=now + timedelta(hours=24),
                      client_mutation_id=mutation_id)
        story, winner = insert_with_mutation_key(s, story, Story, user['id'], mutation_id)
        if story is None:
            if winner is None:
                remove_social_media(path)
                return MUTATION_RETRY
            return replay_response(s, winner)
        author = s.get(User, user['id'])
        result = story_dict(story, author)
    return {'success': True, 'story': result}, 201


@bp.route('/stories/tray')
def app_story_tray():
    viewer = app_user_from_request()
    viewer_id = viewer['id'] if viewer else -1
    with session_scope() as s:
        followed = select(Follow.followee_id).where(Follow.follower_id == viewer_id)
        rows = s.execute(select(Story, User).join(User, User.id == Story.user_id)
            .where(Story.deleted_at.is_(None), Story.expires_at > utc_now(),
                   or_(Story.audience == 'public', Story.user_id == viewer_id,
                       Story.user_id.in_(followed)))
            .order_by(Story.user_id, Story.id).limit(200)).all()
        ids = [row[0].id for row in rows]
        viewed = set(s.execute(select(StoryView.story_id).where(
            StoryView.user_id == viewer_id, StoryView.story_id.in_(ids))).scalars()) if ids else set()
        stories = [story_dict(story, author, story.id in viewed) for story, author in rows]
    groups = {}
    for story in stories:
        groups.setdefault(story['user_id'], {'user_id': story['user_id'],
            'display_name': story['display_name'], 'avatar_emoji': story['avatar_emoji'],
            'stories': [], 'unseen': 0})
        groups[story['user_id']]['stories'].append(story)
        groups[story['user_id']]['unseen'] += int(not story['viewed'])
    return {'success': True, 'groups': list(groups.values())}


@bp.route('/users/<int:user_id>/stories')
def app_user_stories(user_id):
    viewer = app_user_from_request()
    viewer_id = viewer['id'] if viewer else -1
    with session_scope() as s:
        ids = s.execute(select(Story.id).where(Story.user_id == user_id,
            Story.deleted_at.is_(None), Story.expires_at > utc_now())
            .order_by(Story.id)).scalars().all()
        stories = []
        for story_id in ids:
            story = accessible_story(s, story_id, viewer_id)
            if story:
                author = s.get(User, story.user_id)
                viewed = viewer_id > 0 and s.get(StoryView, (story.id, viewer_id)) is not None
                stories.append(story_dict(story, author, viewed))
    return {'success': True, 'stories': stories}


@bp.route('/stories/<int:story_id>', methods=['GET', 'DELETE'])
def app_story(story_id):
    if request.method == 'GET':
        viewer = app_user_from_request()
        viewer_id = viewer['id'] if viewer else -1
        with session_scope() as s:
            story = accessible_story(s, story_id, viewer_id)
            if not story:
                return {'success': False, 'message': '故事不可用'}, 404
            author = s.get(User, story.user_id)
            viewed = viewer_id > 0 and s.get(StoryView, (story.id, viewer_id)) is not None
            return {'success': True, 'story': story_dict(story, author, viewed)}
    viewer, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        story = s.get(Story, story_id)
        if not story:
            return {'success': False, 'message': '故事不存在'}, 404
        if story.user_id != viewer['id']:
            return {'success': False, 'message': '只能删除自己的故事'}, 403
        if story.deleted_at is None:
            story.deleted_at = utc_now()
            path = story.image_path
        else:
            path = None
    if path:
        try:
            os.remove(os.path.join(config.SOCIAL_MEDIA_DIR, path))
        except OSError:
            pass
    return {'success': True}


@bp.route('/stories/<int:story_id>/media')
def app_story_media(story_id):
    viewer = app_user_from_request()
    with session_scope() as s:
        story = accessible_story(s, story_id, viewer['id'] if viewer else -1)
        if not story:
            return {'success': False, 'message': '故事图片不可用'}, 404
        path = story.image_path
    return send_from_directory(config.SOCIAL_MEDIA_DIR, path)


@bp.route('/stories/<int:story_id>/view', methods=['PUT'])
def app_story_view(story_id):
    viewer, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        story = accessible_story(s, story_id, viewer['id'])
        if not story:
            return {'success': False, 'message': '故事不可用'}, 404
        if story.user_id != viewer['id']:
            stmt = dialect_insert(s, StoryView).values(story_id=story_id,
                    user_id=viewer['id'], viewed_at=utc_now())
            s.execute(stmt.on_conflict_do_nothing(index_elements=['story_id', 'user_id']))
    return {'success': True}


@bp.route('/stories/<int:story_id>/viewers')
def app_story_viewers(story_id):
    viewer, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        story = s.get(Story, story_id)
        if not story or story.user_id != viewer['id']:
            return {'success': False, 'message': '故事不存在'}, 404
        rows = s.execute(select(User.id, User.display_name, User.avatar_emoji,
                                StoryView.viewed_at)
                .join(StoryView, StoryView.user_id == User.id)
                .where(StoryView.story_id == story_id)
                .order_by(StoryView.viewed_at.desc()).limit(200)).mappings().all()
        viewers = [{**dict(row), 'viewed_at': iso_z(row['viewed_at'])} for row in rows]
    return {'success': True, 'viewers': viewers}


def coarse_coordinate(value):
    """Persist only approximately 1 km WGS84 grid centers, never the raw GPS sample."""
    return math.floor(value * 100) / 100 + 0.005


def coordinates_from_payload(data):
    try:
        latitude = float(data['latitude'])
        longitude = float(data['longitude'])
        accuracy = float(data['accuracyMeters'])
    except (KeyError, ValueError, TypeError):
        raise ValueError('缺少有效位置') from None
    if not (math.isfinite(latitude) and math.isfinite(longitude) and
            math.isfinite(accuracy) and -90 <= latitude <= 90 and
            -180 <= longitude <= 180 and 0 < accuracy <= 1000):
        raise ValueError('位置精度不足或坐标无效')
    return coarse_coordinate(latitude), coarse_coordinate(longitude), accuracy


def distance_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(a)))


@bp.route('/me/discovery-location', methods=['GET', 'PUT', 'DELETE'])
def app_discovery_location():
    user, error = require_app_user()
    if error:
        return error
    if request.method == 'GET':
        with session_scope() as s:
            location = s.get(DiscoveryLocation, user['id'])
            return {'success': True, 'enabled': bool(location and location.enabled and
                    location.expires_at > utc_now()),
                    'expiresAt': iso_z(location.expires_at) if location else None}
    if request.method == 'DELETE':
        with session_scope() as s:
            s.execute(delete(DiscoveryLocation).where(DiscoveryLocation.user_id == user['id']))
        return {'success': True, 'enabled': False}
    data = request.get_json(silent=True) or {}
    try:
        lat, lon, accuracy = coordinates_from_payload(data)
    except ValueError as exc:
        return {'success': False, 'message': str(exc)}, 400
    enabled = data.get('enabled') is True
    now = utc_now()
    with session_scope() as s:
        location = s.get(DiscoveryLocation, user['id'])
        if not location:
            location = DiscoveryLocation(user_id=user['id'])
            s.add(location)
        location.latitude_grid = lat
        location.longitude_grid = lon
        location.accuracy_meters = accuracy
        location.observed_at = now
        location.expires_at = now + timedelta(hours=24)
        location.enabled = enabled
    return {'success': True, 'enabled': enabled, 'expiresAt': iso_z(now + timedelta(hours=24))}


@bp.route('/nearby/queries', methods=['POST'])
def app_create_nearby_query():
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        lat, lon, _ = coordinates_from_payload(data)
        radius = int(data.get('radiusKm', 5))
    except (ValueError, TypeError) as exc:
        return {'success': False, 'message': str(exc)}, 400
    if radius not in (1, 5, 10, 20):
        return {'success': False, 'message': '无效的搜索范围'}, 400
    now = utc_now()
    with session_scope() as s:
        recent = s.execute(select(DiscoveryQuery).where(
            DiscoveryQuery.user_id == user['id'], DiscoveryQuery.created_at > now - timedelta(seconds=10))
            .order_by(DiscoveryQuery.created_at.desc()).limit(1)).scalar_one_or_none()
        if recent and (recent.center_latitude_grid, recent.center_longitude_grid,
                       recent.radius_km) == (lat, lon, radius):
            return {'success': True, 'queryId': recent.id, 'expiresAt': iso_z(recent.expires_at)}, 200
        lat_span = radius / 111 + 0.02
        lon_span = radius / max(10, 111 * abs(math.cos(math.radians(lat)))) + 0.02
        locations = s.execute(select(DiscoveryLocation).where(
            DiscoveryLocation.user_id != user['id'], DiscoveryLocation.enabled.is_(True),
            DiscoveryLocation.expires_at > now,
            DiscoveryLocation.latitude_grid.between(lat - lat_span, lat + lat_span),
            DiscoveryLocation.longitude_grid.between(lon - lon_span, lon + lon_span))).scalars().all()
        candidates = sorted((distance_km(lat, lon, row.latitude_grid,
                                          row.longitude_grid), row.user_id)
                            for row in locations)
        candidates = [user_id for distance, user_id in candidates if distance <= radius]
        query = DiscoveryQuery(id=secrets.token_urlsafe(24), user_id=user['id'],
                               center_latitude_grid=lat, center_longitude_grid=lon,
                               radius_km=radius, candidate_ids_json=json.dumps(candidates),
                               created_at=now, expires_at=now + timedelta(minutes=10))
        s.add(query)
        query_id = query.id
        expires_at = query.expires_at
    return {'success': True, 'queryId': query_id, 'expiresAt': iso_z(expires_at)}, 201


@bp.route('/nearby/users')
def app_nearby_users():
    user, error = require_app_user()
    if error:
        return error
    query_id = request.args.get('queryId', '')
    try:
        cursor = int(request.args.get('cursor', 0))
        limit = int(request.args.get('limit', 20))
    except ValueError:
        return {'success': False, 'message': '无效的分页参数'}, 400
    if cursor < 0 or not 1 <= limit <= 50:
        return {'success': False, 'message': '无效的分页参数'}, 400
    with session_scope() as s:
        query = s.get(DiscoveryQuery, query_id)
        if not query or query.user_id != user['id']:
            return {'success': False, 'message': '附近查询不存在'}, 404
        if query.expires_at <= utc_now():
            return {'success': False, 'message': '附近查询已过期，请刷新'}, 410
        candidates = json.loads(query.candidate_ids_json)
        items = []
        offset = cursor
        while offset < len(candidates) and len(items) < limit:
            target_id = candidates[offset]
            offset += 1
            location = s.get(DiscoveryLocation, target_id)
            target = s.get(User, target_id)
            if not target or not location or not location.enabled or location.expires_at <= utc_now():
                continue
            distance = distance_km(query.center_latitude_grid, query.center_longitude_grid,
                                   location.latitude_grid, location.longitude_grid)
            if distance > query.radius_km:
                continue
            band = '1km 内' if distance <= 1 else ('1–3km' if distance <= 3 else
                    ('3–5km' if distance <= 5 else ('5–10km' if distance <= 10 else '10–20km')))
            items.append({'id': target.id, 'display_name': target.display_name,
                          'avatar_emoji': target.avatar_emoji, 'distanceBand': band})
    return {'success': True, 'users': items,
            'page': {'hasMore': offset < len(candidates),
                     'nextCursor': str(offset) if offset < len(candidates) else None}}


@bp.route('/nearby/posts')
def app_nearby_posts():
    viewer, error = require_app_user()
    if error:
        return error
    query_id = request.args.get('queryId', '')
    try:
        cursor = int(request.args['cursor']) if 'cursor' in request.args else None
        limit = int(request.args.get('limit', 20))
    except ValueError:
        return {'success': False, 'message': '无效的分页参数'}, 400
    if (cursor is not None and cursor <= 0) or not 1 <= limit <= 50:
        return {'success': False, 'message': '无效的分页参数'}, 400
    with session_scope() as s:
        query = s.get(DiscoveryQuery, query_id)
        if not query or query.user_id != viewer['id']:
            return {'success': False, 'message': '附近查询不存在'}, 404
        if query.expires_at <= utc_now():
            return {'success': False, 'message': '附近查询已过期，请刷新'}, 410
        candidate_ids = json.loads(query.candidate_ids_json)
        locations = s.execute(select(DiscoveryLocation).where(
            DiscoveryLocation.user_id.in_(candidate_ids),
            DiscoveryLocation.enabled.is_(True),
            DiscoveryLocation.expires_at > utc_now())).scalars().all()
        active_ids = [row.user_id for row in locations if distance_km(
            query.center_latitude_grid, query.center_longitude_grid,
            row.latitude_grid, row.longitude_grid) <= query.radius_km]
    posts, more = post_rows(viewer['id'], limit=limit, cursor=cursor, author_ids=active_ids)
    return {'success': True, 'posts': posts,
            'page': {'hasMore': more,
                     'nextCursor': str(posts[-1]['id']) if more and posts else None}}


def unread_notification_count(session, user_id):
    return session.execute(select(func.count()).select_from(SocialNotification).where(
        SocialNotification.recipient_id == user_id,
        SocialNotification.read_at.is_(None))).scalar_one()


@bp.route('/notifications')
def app_social_notifications():
    viewer, error = require_app_user()
    if error:
        return error
    try:
        cursor = int(request.args['cursor']) if 'cursor' in request.args else None
        limit = int(request.args.get('limit', 20))
    except ValueError:
        return {'success': False, 'message': '无效的分页参数'}, 400
    if not 1 <= limit <= 50 or (cursor is not None and cursor <= 0):
        return {'success': False, 'message': '无效的分页参数'}, 400
    with session_scope() as s:
        stmt = select(SocialNotification, User.display_name, User.avatar_emoji).join(
            User, User.id == SocialNotification.actor_id).where(
            SocialNotification.recipient_id == viewer['id'])
        if cursor is not None:
            stmt = stmt.where(SocialNotification.id < cursor)
        rows = s.execute(stmt.order_by(SocialNotification.id.desc()).limit(limit + 1)).all()
        has_more = len(rows) > limit
        result = []
        for note, name, avatar in rows[:limit]:
            post = get_visible_post(s, note.post_id, viewer['id']) if note.post_id else None
            comment = s.get(Comment, note.comment_id) if note.comment_id and post else None
            target_available = note.post_id is None or (post is not None and
                (note.comment_id is None or (comment is not None and comment.deleted_at is None)))
            result.append({'id': note.id, 'kind': note.kind, 'actorId': note.actor_id,
                           'actorName': name, 'actorEmoji': avatar,
                           'postId': note.post_id if target_available else None,
                           'commentId': note.comment_id if target_available else None,
                           'targetAvailable': target_available,
                           'createdAt': iso_z(note.created_at),
                           'readAt': iso_z(note.read_at) if note.read_at else None})
        unread_count = unread_notification_count(s, viewer['id'])
    return {'success': True, 'notifications': result, 'unreadCount': unread_count,
            'page': {'hasMore': has_more,
                     'nextCursor': str(result[-1]['id']) if has_more and result else None}}


@bp.route('/notifications/<int:notification_id>/read', methods=['PUT'])
def app_read_notification(notification_id):
    viewer, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        note = s.get(SocialNotification, notification_id)
        if not note or note.recipient_id != viewer['id']:
            return {'success': False, 'message': '通知不存在'}, 404
        if note.read_at is None:
            note.read_at = utc_now()
        s.flush()
        count = unread_notification_count(s, viewer['id'])
    return {'success': True, 'unreadCount': count}


@bp.route('/notifications/read-through', methods=['PUT'])
def app_read_notifications_through():
    viewer, error = require_app_user()
    if error:
        return error
    high_water = (request.get_json(silent=True) or {}).get('highWaterId')
    if not isinstance(high_water, int) or high_water <= 0:
        return {'success': False, 'message': '无效的已读位置'}, 400
    with session_scope() as s:
        s.execute(update(SocialNotification).where(
            SocialNotification.recipient_id == viewer['id'],
            SocialNotification.id <= high_water,
            SocialNotification.read_at.is_(None)).values(read_at=utc_now()))
        count = unread_notification_count(s, viewer['id'])
    return {'success': True, 'unreadCount': count}
