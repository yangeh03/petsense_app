"""W12 收件箱 HTTP 端点（agent-w12-spec.md §服务端和跨端契约）。

独立 blueprint（不进 agent/routes.py，避免与并行开发中的 W11 改动冲突）：
- GET  /api/app/agent/inbox                 列表 + 未读总数 + 今日预览 + 接管能力
- GET  /api/app/agent/inbox/<id>            详情（来源正文 + 稳定目标 + 锚点）
- POST /api/app/agent/inbox/<id>/read       单条已读（幂等；不解除告警）
- POST /api/app/agent/inbox/read-all        全部已读（按请求生效时的可见水位）
- GET  /api/app/agent/inbox/resolve/<id>    深链解析（只读，不改已读；返回锚点）

归属检查：列表/详情/已读/解析各端点分别校验「条目归属本人」与「宠物仍是本人名下」；
宠物过滤区分「未指定」（全部本人宠物）与「无效/他人」（直接拒绝，不落到写操作）。
来源必须能在同一账号 + 同一宠物下解析，否则给出明确不可用状态（不泄露原绑定）。
"""
from flask import Blueprint, request

from petsense.agent import inbox
from petsense.auth.routes import require_app_user
from petsense.db import session_scope
from petsense.db.models import Pet
from petsense.db.models.agent import AgentInboxItem

bp = Blueprint('agent_inbox', __name__, url_prefix='/api/app/agent/inbox')


def _pet_filter(session, user_id):
    """宠物过滤：返回 (pet_id, error)。

    - 未指定 → (None, None)：覆盖本人全部宠物；
    - 指定但不是本人名下 → (None, 404)：写操作前先拒绝，避免误伤其他宠物。
    """
    raw = request.args.get('pet')
    if raw is None or raw == '':
        return None, None
    try:
        pet_id = int(raw)
    except (TypeError, ValueError):
        return None, ({'message': '宠物标识无效'}, 400)
    pet = session.get(Pet, pet_id)
    if pet is None or pet.user_id != user_id:
        return None, ({'message': '宠物不存在'}, 404)
    return pet_id, None


def _own_item(session, user_id, item_id):
    """取本人名下、且宠物仍在本人名下的条目；否则返回 None。"""
    row = session.get(AgentInboxItem, item_id)
    if row is None or row.user_id != user_id:
        return None
    pet = session.get(Pet, row.pet_id)
    if pet is None or pet.user_id != user_id:
        return None
    return row


@bp.get('')
def list_inbox():
    """收件箱列表：稳定游标分页 + 未读总数 + 今日预览 + 接管能力。"""
    user, error = require_app_user()
    if error:
        return error
    before = request.args.get('before', type=int)
    limit = request.args.get('limit', type=int) or inbox.PAGE_LIMIT
    with session_scope() as s:
        pet_id, failure = _pet_filter(s, user['id'])
        if failure:
            return failure
        inbox.project_user(s, user['id'])      # 可恢复投影：读路径保证「创建后最终可见」
        rows, has_more = inbox.list_items(s, user['id'], pet_id=pet_id, before=before,
                                           limit=limit)
        items = inbox.public_list(s, rows)
        preview = inbox.preview_item(s, user['id'], pet_id=pet_id)
        preview_payload = None
        if preview is not None:
            fields, _detail = inbox.hydrate(s, preview)
            preview_payload = inbox.public(preview, fields=fields)
        return {'items': items,
                'unreadCount': inbox.unread_count(s, user['id'], pet_id=pet_id),
                'preview': preview_payload,
                'nextCursor': rows[-1].id if (has_more and rows) else None,
                'capability': inbox.capability(user['id'])}


@bp.get('/<int:item_id>')
def inbox_detail(item_id):
    """条目详情：来源正文与稳定目标（深链入参只有收件箱 ID）。"""
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        inbox.project_user(s, user['id'])
        row = _own_item(s, user['id'], item_id)
        if row is None:
            return {'message': '消息不存在'}, 404
        source = inbox.source_detail(s, row)
        target = inbox.resolve_target(s, row)
        fields, _detail = inbox.hydrate(s, row)
        return {'item': inbox.public(row, source=source, target=target, fields=fields),
                'unreadCount': inbox.unread_count(s, user['id'], pet_id=row.pet_id)}


@bp.post('/<int:item_id>/read')
def mark_read(item_id):
    """单条已读（幂等）。已读不解除告警：来源状态各自持久化。"""
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        row = _own_item(s, user['id'], item_id)
        if row is None:
            return {'message': '消息不存在'}, 404
        row = inbox.mark_read(s, user['id'], item_id)
        return {'item': inbox.public(row),
                'unreadCount': inbox.unread_count(s, user['id'], pet_id=row.pet_id)}


@bp.post('/read-all')
def mark_all_read():
    """全部已读：按请求生效时的可见水位（并发新建不被一并处理）。"""
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True)
    through = data.get('throughId') if isinstance(data, dict) else None
    if through is not None and (not isinstance(through, int) or isinstance(through, bool)):
        return {'message': '水位无效'}, 400
    with session_scope() as s:
        pet_id, failure = _pet_filter(s, user['id'])
        if failure:
            return failure
        updated, watermark = inbox.mark_all_read(s, user['id'], pet_id=pet_id,
                                                 through_id=through)
        return {'updated': updated, 'watermark': watermark,
                'unreadCount': inbox.unread_count(s, user['id'], pet_id=pet_id)}


@bp.get('/turn/<int:turn_id>')
def inbox_turn(turn_id):
    """按锚点取回**那一条**来源消息（不依赖会话分页/话题过滤）。

    深链命中历史回合（早于已加载的一页）或当前话题过滤掉的回合时，客户端凭稳定
    回合 id 精确取回并展示；跨账号/不存在一律 404。
    """
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        turn = inbox.find_turn(s, user['id'], turn_id)
        if turn is None:
            return {'message': '消息不存在'}, 404
        from petsense.agent import group
        if group.client_group(user):
            # 群聊协议客户端：与 WS/轮询同形状（带按消息拆分的 messages）
            return {'turn': group.turn_public(s, turn), 'protocol': group.PROTOCOL}
        payload = inbox.turn_public(turn)
        if payload.get('target') == 'both':
            payload['target'] = 'auto'  # 旧客户端不认识「两位都回复」，等同未指定
        return {'turn': payload}


@bp.get('/resolve/<int:item_id>')
def resolve(item_id):
    """深链解析：只返回稳定路由、目标标识与锚点（纯读，不改已读状态）。"""
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        inbox.project_user(s, user['id'])
        row = _own_item(s, user['id'], item_id)
        if row is None:
            return {'message': '消息不存在'}, 404
        fields, _detail = inbox.hydrate(s, row)
        return {'item': inbox.public(row, fields=fields),
                'target': inbox.resolve_target(s, row),
                'unreadCount': inbox.unread_count(s, user['id'], pet_id=row.pet_id)}
