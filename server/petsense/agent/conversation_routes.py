"""W16 会话接口（docs/agent-w16-spec.md §5.2）：列表、新建、改名、删除、标已读。

独立 blueprint（前缀 /api/app/agent），注册方式同 inbox_routes；回合的读写与历史分页仍在
routes.turns（`conversationId` 必填）。写操作提交后推 conversation 帧，同一账号的其他连接据此更新列表。
"""
from flask import Blueprint, request

from petsense.agent import conversations as service
from petsense.agent import group as group_service
from petsense.agent import routes as turn_routes
from petsense.agent import ws
from petsense.auth.routes import require_app_user
from petsense.db import session_scope, utc_now
from petsense.db.models import Pet

bp = Blueprint('agent_conversations', __name__, url_prefix='/api/app/agent')


def _pet(session, pet_id, user):
    pet = session.get(Pet, pet_id)
    return pet if pet is not None and pet.user_id == user['id'] else None


def _own(session, conversation_id, user):
    """本人名下的会话（含已删除的，删除因此可以幂等）；不存在时返回 None。"""
    row = session.get(service.AgentConversation, conversation_id)
    if row is None:
        return None
    pet = session.get(Pet, row.pet_id)
    return row if pet is not None and pet.user_id == user['id'] else None


def _turns_view(user, session, rows):
    if group_service.client_group(user):
        return group_service.list_public(session, rows)
    return [turn_routes.public(row) for row in rows]


@bp.get('/pets/<int:pet_id>/conversations')
def list_conversations(pet_id):
    user, error = require_app_user()
    if error:
        return error
    try:
        limit = max(1, min(int(request.args.get('limit', service.LIST_PAGE)), 100))
        offset = max(0, int(request.args.get('offset', 0)))
    except ValueError:
        return {'message': '分页参数无效'}, 400
    with session_scope() as s:
        pet = _pet(s, pet_id, user)
        if pet is None:
            return {'message': '宠物不存在'}, 404
        items, total = service.listing(s, pet, limit, offset)
        return {'conversations': items, 'total': total,
                'hasMore': offset + len(items) < total}


@bp.post('/pets/<int:pet_id>/conversations')
def create_conversation(pet_id):
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        pet = _pet(s, pet_id, user)
        if pet is None:
            return {'message': '宠物不存在'}, 404
        row, created = service.create_chat(s, pet)
        result = service.public(s, row)
    ws.push_conversation(user['id'], result)
    return {'conversation': result, 'created': created}, (201 if created else 200)


@bp.patch('/conversations/<int:conversation_id>')
def rename_conversation(conversation_id):
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True)
    title = data.get('title') if isinstance(data, dict) else None
    with session_scope() as s:
        row = _own(s, conversation_id, user)
        if row is None or row.deleted_at is not None:
            return {'message': '会话不存在'}, 404
        try:
            service.rename(row, title, utc_now())
        except service.ConversationError as exc:
            return {'message': exc.message}, exc.status
        result = service.public(s, row)
    ws.push_conversation(user['id'], result)
    return {'conversation': result}


@bp.delete('/conversations/<int:conversation_id>')
def delete_conversation(conversation_id):
    """软删除；有进行中的委托或未结束的关怀主题时 409（带数量），重复删除幂等。"""
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        row = _own(s, conversation_id, user)
        if row is None:
            return {'message': '会话不存在'}, 404
        if row.deleted_at is None:
            try:
                service.delete(s, row, utc_now())
            except service.ConversationError as exc:
                return {'message': exc.message, 'code': exc.code, **exc.extra}, exc.status
        result = service.public(s, row)
    ws.push_conversation(user['id'], result)
    return {'success': True}


@bp.post('/conversations/<int:conversation_id>/read')
def read_conversation(conversation_id):
    """标已读：不带 ids 标该会话全部未读，带 ids 只标这些。返回会话摘要与最近被标的回合。"""
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True)
    ids = data.get('ids') if isinstance(data, dict) else None
    if ids is not None and (not isinstance(ids, list) or not 1 <= len(ids) <= service.TURNS_IN_READ_RESPONSE
                            or any(not isinstance(i, int) or isinstance(i, bool) for i in ids)):
        return {'message': '消息列表无效'}, 400
    with session_scope() as s:
        row = _own(s, conversation_id, user)
        if row is None or row.deleted_at is not None:
            return {'message': '会话不存在'}, 404
        marked = service.mark_read(s, row, ids)
        recent = marked[:service.TURNS_IN_READ_RESPONSE]
        result = {'conversation': service.public(s, row), 'turns': _turns_view(user, s, recent),
                  'markedCount': len(marked)}
    return result
