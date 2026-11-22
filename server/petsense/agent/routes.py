"""Authenticated durable companion API（W01：统一运行循环，无前置路由；W02：氛围快照组装；W07：图片附件）。

POST turns 组装初始包（含 W02 常驻快照）并触发 loop.run_dialogue；判断（范围/意图/角色）
全部由模型在循环内完成（agent-w01-spec.md §3.7）。GET 保留为 WS 断线的降级轮询路径。
W07：POST images 上传聊天图片（内容寻址、按用户隔离），turns 请求可携带 images 引用。
"""
import json
import os
import time
from datetime import datetime, timedelta

from flask import Blueprint, request, current_app, send_file
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from petsense.auth.routes import require_app_user
from petsense.db import session_scope, utc_now, iso_z
from petsense.db.models import Pet
from petsense.db.models.agent import AgentTurn
from petsense.agent.observations import query
from petsense.agent import attachments, loop, runs, snapshot
from petsense.agent import ws  # noqa: F401  import 即注册 /api/app/agent/ws 路由

bp = Blueprint('agent', __name__, url_prefix='/api/app/agent')

MAX_IMAGES_PER_TURN = 3  # D2


def public(turn):
    return {'id': turn.id, 'requestId': turn.request_id, 'text': turn.text,
            'topicId': turn.topic_id, 'replyTo': turn.reply_to, 'sources': json.loads(turn.sources),
            'speaker': turn.speaker, 'readAt': iso_z(turn.read_at) if turn.read_at else None,
            'target': turn.target, 'reply': turn.reply, 'status': turn.status,
            'blocks': json.loads(turn.blocks) if turn.blocks else None,
            'images': json.loads(turn.images) if turn.images else [],
            'createdAt': iso_z(turn.created_at)}


@bp.route('/pets/<int:pet_id>/images', methods=['POST'])
def upload_image(pet_id):
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        if not pet or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
    uploaded = request.files.get('image')
    if uploaded is None:
        return {'message': '请提供 image 文件'}, 400
    try:
        return attachments.save_agent_image(uploaded, user['id']), 201
    except attachments.ImageRejected as exc:
        return {'message': str(exc), 'code': exc.code}, 400


@bp.route('/images/<image_id>')
def serve_image(image_id):
    user, error = require_app_user()
    if error:
        return error
    path = attachments.image_path(user['id'], image_id)  # 归属即路径：仅本人目录
    if not path or not os.path.isfile(path):
        return {'message': '图片不存在'}, 404
    response = send_file(path, mimetype='image/jpeg')  # 内容寻址，永不变化
    # private：鉴权端点（D3 仅本人可读），内容寻址故 immutable，但不进共享缓存
    response.headers['Cache-Control'] = 'private, max-age=31536000, immutable'
    return response


@bp.route('/pets/<int:pet_id>/observations')
def observations(pet_id):
    user, error = require_app_user()
    if error:
        return error
    try:
        end = datetime.fromisoformat(request.args['end'].replace('Z', '+00:00'))
        start = datetime.fromisoformat(request.args['start'].replace('Z', '+00:00'))
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError('请使用带时区的时间')
        with session_scope() as s:
            pet = s.get(Pet, pet_id)
            if not pet or pet.user_id != user['id']:
                return {'message': '宠物不存在'}, 404
            return query(s, pet, start, end)
    except (KeyError, ValueError):
        return {'message': '请提供90天以内、带时区的起止时间'}, 400


@bp.route('/pets/<int:pet_id>/turns', methods=['GET', 'POST'])
def turns(pet_id):
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        if not pet or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
        if request.method == 'GET':
            before = request.args.get('before', type=int)
            stmt = select(AgentTurn).where(AgentTurn.pet_id == pet_id)
            if before:
                stmt = stmt.where(AgentTurn.id < before)
            rows = s.execute(stmt.order_by(AgentTurn.id.desc()).limit(40)).scalars().all()
            for row in rows:
                if row.status == 'pending' and utc_now() - row.created_at > timedelta(minutes=5):
                    row.status = 'failed'
                    row.reply = '上次接话中断了，你的话已经保留，可以重新发送。'
            return {'turns': [public(t) for t in reversed(rows)]}
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {'message': '请输入消息'}, 400
        text, key, target = data.get('text'), data.get('requestId'), data.get('target', 'auto')
        raw_images = data.get('images') if data.get('images') is not None else []
        # 先校验元素类型（非字符串不可哈希，set() 去重必须放在后面，否则 500）
        if not isinstance(raw_images, list) or len(raw_images) > MAX_IMAGES_PER_TURN \
                or any(not isinstance(i, str) or not attachments.IMAGE_ID_RE.match(i) for i in raw_images) \
                or len(set(raw_images)) != len(raw_images):
            return {'message': '图片引用无效'}, 400
        # 有图可纯图发送（W07 D2/实现默认）：text 允许为空，但仍须是字符串且 ≤4000
        if not isinstance(text, str) or len(text) > 4000 or (not text.strip() and not raw_images) \
                or not isinstance(key, str) or not 1 <= len(key) <= 80 or target not in ('auto', 'pet', 'assistant'):
            return {'message': '消息、指向或请求标识无效'}, 400
        images_meta = []
        for image_id in raw_images:
            meta = attachments.read_meta(user['id'], image_id)
            if meta is None:
                return {'message': '图片不存在，请重新上传'}, 404
            images_meta.append(meta)
        images_json = json.dumps(images_meta, ensure_ascii=False) if images_meta else None
        topic_id, reply_to = data.get('topicId'), data.get('replyTo')
        for reference in (topic_id, reply_to):
            if reference is not None:
                if not isinstance(reference, int) or isinstance(reference, bool):
                    return {'message': '消息引用无效'}, 400
                related = s.get(AgentTurn, reference)
                if related is None or related.pet_id != pet_id:
                    return {'message': '消息引用不存在'}, 404
        existing = s.execute(select(AgentTurn).where(AgentTurn.pet_id == pet_id, AgentTurn.request_id == key)).scalar_one_or_none()
        if existing:
            if (existing.text, existing.target, existing.topic_id, existing.reply_to, existing.images) \
                    != (text.strip(), target, topic_id, reply_to, images_json):
                return {'message': '请求标识已用于另一条消息'}, 409
            return {'turn': public(existing)}
        turn = AgentTurn(pet_id=pet_id, request_id=key, text=text.strip(), target=target,
                         reply='', sources='[]', topic_id=topic_id, reply_to=reply_to, images=images_json,
                         status='pending', created_at=utc_now())
        try:
            with s.begin_nested():
                s.add(turn)
                s.flush()
        except IntegrityError:
            return {'message': '消息正在处理中，请刷新会话'}, 409
        turn_id = turn.id
        history_query = select(AgentTurn).where(AgentTurn.pet_id == pet_id, AgentTurn.id < turn_id,
            AgentTurn.status == 'complete')
        if topic_id is not None:
            history_query = history_query.where((AgentTurn.topic_id == topic_id) | (AgentTurn.id == topic_id))
        history_rows = s.execute(history_query.order_by(AgentTurn.id.desc()).limit(12)).scalars().all()
        history = [{'text': row.text, 'reply': row.reply,
                    'images': json.loads(row.images) if row.images else []}
                   for row in reversed(history_rows)]
        now = utc_now()
        snap = snapshot.assemble(s, pet, now)
    context = {'pet_id': pet_id, 'user_id': user['id'], 'text': text.strip(),
               'target': target, 'history': history, 'images': images_meta,
               'snapshot': snap, 'now_iso': iso_z(now)}
    speaker = ['assistant']
    last_write = [0.0]

    def progress(current_speaker, content):
        speaker[0] = current_speaker
        if time.monotonic() - last_write[0] < .5:
            return
        with session_scope() as session:
            current_turn = session.get(AgentTurn, turn_id)
            if current_turn is not None and current_turn.status == 'pending':
                current_turn.reply = content
                current_turn.speaker = current_speaker
        ws.push_delta(user['id'], pet_id, turn_id, content, current_speaker)
        last_write[0] = time.monotonic()

    status = 'complete'
    try:
        outcome = loop.run_dialogue(context, on_progress=progress)
        reply, blocks = outcome['reply'], outcome['blocks']
        speaker[0] = outcome['speaker']
        sources = outcome['sources']
        run_status = 'degraded' if outcome['degraded'] else 'complete'
        runs.save(pet_id, turn_id, outcome['trace'], run_status)
    except Exception as exc:
        current_app.logger.warning('Agent turn %s failed: %s', turn_id, type(exc).__name__)
        reply, blocks, sources = '暂时没能接上照料助手，你的话已经保留。稍后可以重新发送。', None, []
        status = 'failed'
        runs.save(pet_id, turn_id, {'form': 'dialogue',
                                    'trigger': {'target': target, 'text': text.strip()[:500]},
                                    'conclusion': {'scope': 'unknown', 'intent': 'unknown',
                                                   'speaker': 'assistant', 'reason_code': 'model_error'},
                                    'messages': [], 'tool_summary': [],
                                    'budget': {}, 'degraded': []},
                   'failed', error=f'{type(exc).__name__}: {str(exc)[:300]}')
    with session_scope() as s:
        current_turn = s.get(AgentTurn, turn_id)
        current_turn.reply, current_turn.status, current_turn.speaker = reply, status, speaker[0]
        current_turn.sources = json.dumps(sources)
        current_turn.blocks = json.dumps(blocks, ensure_ascii=False) if blocks else None
        result = {'turn': public(current_turn)}
    ws.push_turn(user['id'], pet_id, result['turn'])
    return result


@bp.post('/pets/<int:pet_id>/read')
def read_turns(pet_id):
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True)
    ids = data.get('ids') if isinstance(data, dict) else None
    if not isinstance(ids, list) or not 1 <= len(ids) <= 40 or any(not isinstance(i, int) or isinstance(i, bool) for i in ids):
        return {'message': '消息列表无效'}, 400
    with session_scope() as session:
        pet = session.get(Pet, pet_id)
        if pet is None or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
        rows = session.execute(select(AgentTurn).where(AgentTurn.pet_id == pet_id,
            AgentTurn.id.in_(ids), AgentTurn.status != 'pending')).scalars().all()
        now = utc_now()
        for row in rows:
            row.read_at = row.read_at or now
        return {'turns': [public(row) for row in rows]}
