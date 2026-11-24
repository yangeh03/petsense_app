"""Authenticated durable companion API（W01：统一运行循环，无前置路由；W02：氛围快照组装；
W03：助手设定注入、确认提案决策与设定端点）。

POST turns 组装初始包（含 W02 常驻快照与 W03 助手设定）并触发 loop.run_dialogue；
判断（范围/意图/角色）全部由模型在循环内完成（agent-w01-spec.md §3.7）。
GET 保留为 WS 断线的降级轮询路径。
W07：POST images 上传聊天图片（内容寻址、按用户隔离），turns 请求可携带 images 引用。
"""
import json
import os
import time
from datetime import datetime, timedelta

from flask import Blueprint, request, current_app, send_file
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from petsense.auth.routes import app_user_from_request, require_app_user
from petsense.db import session_scope, utc_now, iso_z
from petsense.db.models import Pet
from petsense.db.models.agent import AgentAction, AgentCareTopic, AgentProposal, AgentTurn
from petsense.agent.observations import query
from petsense.agent import baseline as baseline_service
from petsense.agent import care, jobs
from petsense.agent import attachments, loop, runs, snapshot
from petsense.agent import actions as actions_service
from petsense.agent import proposals as proposal_service
from petsense.agent import settings as settings_service
from petsense.agent import tasks as task_service
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


@bp.route('/pets/<int:pet_id>/baseline')
def baseline(pet_id):
    """W05「能否比较/比较结果」业务接口：学习卡、维度详情与今日状态共用此结果。"""
    user, error = require_app_user()
    if error:
        return error
    date = request.args.get('date')
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        if not pet or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
        try:
            return baseline_service.read_baseline(s, pet, date_str=date or None)
        except ValueError as exc:
            return {'message': str(exc)}, 400


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


@bp.route('/recordings/<int:action_id>/audio')
def serve_recording_audio(action_id):
    """W10 录音鉴权读取（D5）：验证账号、宠物与动作引用；仅用户播放时按需读取。

    WAV 存 agent-recordings/（独立目录），不经旧 /audio/ 公共路由暴露（spec §3）。
    支持 ?token= 查询鉴权：RN WebView/audio 标签发不了自定义 header（同 /ws 模式）。
    """
    from petsense.auth.routes import user_for_token
    user = app_user_from_request() or user_for_token(request.args.get('token', ''))
    if not user:
        return {'message': '请先登录'}, 401
    path = actions_service.recording_audio_path(user['id'], action_id)
    if path is None:
        return {'message': '录音不存在'}, 404
    response = send_file(path, mimetype='audio/wav', conditional=True)
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@bp.route('/pets/<int:pet_id>/social-draft', methods=['GET', 'DELETE'])
def social_draft(pet_id):
    """W10 私有社区草稿槽（D2）：发布页独立加载/移除；原有手写草稿不受影响。"""
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        if pet is None or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
        row = s.execute(select(AgentAction).where(
            AgentAction.pet_id == pet_id, AgentAction.user_id == user['id'],
            AgentAction.kind == actions_service.KIND_DRAFT,
            AgentAction.status == 'completed'
        ).order_by(AgentAction.id.desc())).scalars().first()
        if request.method == 'DELETE':
            if row is not None:
                row.status = 'dismissed'
                row.updated_at = utc_now()
                row.finished_at = utc_now()
            return {'success': True, 'draft': None}
        if row is None:
            return {'success': True, 'draft': None}
        result = json.loads(row.result_json or '{}')
        return {'success': True, 'draft': {
            'actionId': row.id, 'caption': (result.get('draft') or {}).get('caption'),
            'location': (result.get('draft') or {}).get('location'),
            'savedAt': iso_z(row.created_at)}}


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
        companion = settings_service.load(s, user['id'])
    context = {'pet_id': pet_id, 'user_id': user['id'], 'text': text.strip(),
               'target': target, 'history': history, 'images': images_meta, 'snapshot': snap, 'now_iso': iso_z(now),
               'turn_id': turn_id, 'settings': companion}
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
    refreshed_turn_ids = []
    try:
        outcome = loop.run_dialogue(context, on_progress=progress)
        reply, blocks = outcome['reply'], outcome['blocks']
        speaker[0] = outcome['speaker']
        sources = outcome['sources']
        refreshed_turn_ids = [tid for tid in outcome.get('refreshedTurnIds') or [] if tid != turn_id]
        if outcome.get('proposals'):
            # W03 确认卡：挂最后一张（同回合多次写入调用时后一张已替代前一张 pending）
            blocks = list(blocks or []) + [{'type': 'proposal',
                                            'proposal': outcome['proposals'][-1]}]
        seen_action_ids = set()
        for action_payload in outcome.get('actions') or []:
            # W10 直写动作回执（明确保存草稿等）；确认类动作的回执在决策后由 actions 写回
            if action_payload.get('id') in seen_action_ids:
                continue
            seen_action_ids.add(action_payload.get('id'))
            blocks = list(blocks or []) + [{'type': 'action', 'action': action_payload}]
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
    # W03：被 supersede 的旧卡所在回合已重写 blocks，提交后推帧让历史卡片翻「已有新候选」态
    for stale_turn_id in refreshed_turn_ids:
        with session_scope() as s:
            stale_turn = s.get(AgentTurn, stale_turn_id)
            if stale_turn is not None and stale_turn.pet_id == pet_id:
                ws.push_turn(user['id'], pet_id, public(stale_turn))
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


@bp.route('/assistant-settings', methods=['GET', 'PATCH'])
def assistant_settings():
    """助手设定页读写（W03，D3 四字段全直写；PATCH 白名单校验）。"""
    user, error = require_app_user()
    if error:
        return error
    if request.method == 'GET':
        with session_scope() as s:
            return {'settings': settings_service.load(s, user['id'])}
    data = request.get_json(silent=True)
    try:
        patch = settings_service.validate_patch(data)
    except ValueError as exc:
        return {'message': str(exc)}, 400
    with session_scope() as s:
        row = settings_service.persist(s, user['id'], patch)
        settings = settings_service.public(row)
        # 主动程度切档后实际生效（W09 §4 A4）：已开题但还没发出首发消息的事件
        # 按新档位补排作业（消息本身仍按 request key 去重，同实例只发一条）
        if any(column == 'proactivity' for column, _ in patch):
            level = settings['proactivity']
            pets = s.execute(select(Pet).where(Pet.user_id == user['id'])).scalars().all()
            for pet in pets:
                topics = s.execute(select(AgentCareTopic).where(
                    AgentCareTopic.pet_id == pet.id,
                    AgentCareTopic.kind.in_(('device', 'learning')),
                    AgentCareTopic.status.in_(('open', 'ongoing')),
                    AgentCareTopic.delivered == 0)).scalars().all()
                for topic in topics:
                    jobs.enqueue(s, 'care_event', {'topic': topic.id, 'phase': 'open'},
                                 dedup_key=care.event_job_key(topic.id, 'open', level),
                                 next_run_at=utc_now())
        return {'settings': settings}


@bp.post('/pets/<int:pet_id>/proposals/<int:proposal_id>/decision')
def proposal_decision(pet_id, proposal_id):
    """确认卡决策（W03 §3.7；W08 task_change 与 W10 动作类经 kind 分派）：confirm 落库 / reject 仅翻转状态；幂等，异态 409。

    决策为确定性代码（不触发模型运行）；卡片状态即回执，重写 blocks 后按 id 推 turn 帧刷新。
    W10 动作类提案在状态翻转提交后交 actions.execute_confirmed 执行（复核、MQTT、回执），
    执行后再次重写 blocks 并以最终回执为准推送。
    """
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True)
    action = data.get('action') if isinstance(data, dict) else None
    if action not in ('confirm', 'reject'):
        return {'message': '决策动作无效'}, 400
    # W11 D3：主人对「等基线 / 先记录绝对事实」的实际选择（仅个人比较类候选需要）
    baseline_strategy = data.get('baselineStrategy') if isinstance(data, dict) else None
    if baseline_strategy is not None and baseline_strategy not in ('fact_first', 'wait_baseline'):
        return {'message': '基线策略需为 fact_first / wait_baseline'}, 400
    result = None
    changed = False
    kind = None
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        if pet is None or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
        try:
            row, turn_id, changed = proposal_service.decide(
                s, pet, proposal_id, action, baseline_strategy=baseline_strategy)
            kind = row.kind
        except proposal_service.ProposalError as exc:
            # 409（已被替代/已处理过）带当前提案与其回合快照：App 按响应覆盖，卡片翻终态禁按钮（§5）
            body = {'message': exc.message}
            stale = s.get(AgentProposal, proposal_id)
            if stale is not None and stale.pet_id == pet.id:
                body['proposal'] = proposal_service.public(stale)
                if exc.status == 409 and stale.turn_id:
                    stale_turn = s.get(AgentTurn, stale.turn_id)
                    body['turn'] = public(stale_turn) if stale_turn else None
            return body, exc.status
        turn = s.get(AgentTurn, turn_id) if turn_id else None
        result = {'proposal': proposal_service.public(row),
                  'turn': public(turn) if turn else None}
    if changed and action == 'confirm' and kind in actions_service.CONFIRM_KINDS:
        # 提交后执行（spec §2）：重复确认被 execute_confirmed 幂等挡住，不重复提交外部系统
        actions_service.execute_confirmed(pet_id, proposal_id)
        with session_scope() as s:
            fresh = s.get(AgentProposal, proposal_id)
            result['proposal'] = proposal_service.public(fresh)
            if fresh and fresh.turn_id:
                fresh_turn = s.get(AgentTurn, fresh.turn_id)
                result['turn'] = public(fresh_turn) if fresh_turn else result['turn']
    if result['turn']:
        ws.push_turn(user['id'], pet_id, result['turn'])
    return result


@bp.route('/pets/<int:pet_id>/tasks')
def agent_tasks_list(pet_id):
    """委托簿（W08，agent-w08-spec.md §2）：状态、当前计划、提醒与回信计数、深链主题。"""
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        if pet is None or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
        return {'tasks': task_service.list_tasks(s, pet)}


@bp.route('/pets/<int:pet_id>/tasks/<int:task_id>')
def agent_task_detail(pet_id, task_id):
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        if pet is None or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
        task = s.get(task_service.AgentTask, task_id)
        if task is None or task.pet_id != pet.id:
            return {'message': '委托不存在'}, 404
        return {'task': task_service.public(s, task, include_detail=True)}


@bp.post('/pets/<int:pet_id>/tasks/<int:task_id>/ops')
def agent_task_op(pet_id, task_id):
    """委托簿直接操作（D3：暂停/恢复/取消直接生效）；幂等，非法转换 409 带当前状态。"""
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True)
    op = data.get('op') if isinstance(data, dict) else None
    if op not in ('pause', 'resume', 'cancel'):
        return {'message': 'op 需为 pause / resume / cancel'}, 400
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        if pet is None or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
        try:
            task = task_service.task_op(s, pet, task_id, op, now=utc_now())
        except task_service.TaskError as exc:
            body = {'message': exc.message}
            task = s.get(task_service.AgentTask, task_id)
            if task is not None and task.pet_id == pet.id:
                body['task'] = task_service.public(s, task)  # 409 带当前状态（App 按响应覆盖）
            return body, exc.status
        return {'task': task_service.public(s, task)}


@bp.post('/pets/<int:pet_id>/task-proposals')
def agent_task_proposal_edit(pet_id):
    """确认卡上编辑并重新提交归一化计划（spec §2）：基于 pending task_change 候选生成新版本。"""
    user, error = require_app_user()
    if error:
        return error
    data = request.get_json(silent=True)
    base_id = data.get('baseProposalId') if isinstance(data, dict) else None
    plan_fields = data.get('plan') if isinstance(data, dict) else None
    if isinstance(base_id, bool) or not isinstance(base_id, int) or not isinstance(plan_fields, dict):
        return {'message': '请提供 baseProposalId 与修改后的 plan'}, 400
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        if pet is None or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
        base = s.get(AgentProposal, base_id)
        if base is None or base.pet_id != pet.id or base.kind != proposal_service.KIND_TASK \
                or base.status != 'pending':
            return {'message': '候选已处理或不存在，请刷新确认卡'}, 409
        base_payload = json.loads(base.payload)
        if base_payload.get('action') not in ('create', 'update', 'renew'):
            return {'message': '该候选不包含可编辑的计划'}, 400
        merged = dict(base_payload.get('plan') or {})
        merged.update(plan_fields)
        arguments = {'action': base_payload['action'], 'plan': merged}
        if base_payload.get('taskId') is not None:
            arguments.update({'taskId': base_payload['taskId'],
                              'expectedVersion': base_payload['expectedVersion']})
        try:
            payload = task_service.normalize_proposal_fields(arguments)
        except task_service.TaskError as exc:
            return {'message': exc.message}, 400
        # W11 D3：编辑入口不得丢掉「须由主人明确选择」的要求，也不得悄悄换策略——
        # 按所选宠物的最新基线状态重判，并保留主人已做过的明确选择。
        plan = payload.get('plan') or {}
        base_plan = base_payload.get('plan') or {}
        if plan.get('kind') == 'comparison':
            if base_plan.get('baselineStrategyExplicit'):
                plan['baselineStrategyExplicit'] = True
            if not task_service.baseline_ready(s, pet, plan):
                plan['baselineStrategyChoiceRequired'] = True
            else:
                plan.pop('baselineStrategyChoiceRequired', None)
        row, refreshed = proposal_service.create_task_proposal(
            s, pet, base.turn_id, payload, now=utc_now())
        result = {'proposal': proposal_service.public(row)}
        refreshed_ids = [tid for tid in refreshed if tid != base.turn_id]
    for stale_turn_id in [base.turn_id] + refreshed_ids:
        with session_scope() as s:
            stale_turn = s.get(AgentTurn, stale_turn_id) if stale_turn_id else None
            if stale_turn is not None and stale_turn.pet_id == pet_id:
                ws.push_turn(user['id'], pet_id, public(stale_turn))  # 旧卡翻「已有新候选」
    return result


@bp.get('/pets/<int:pet_id>/care/rules')
def care_rules(pet_id):
    """W09 受管理规则能力（D3）：新 App 据此抑制同类本地重复提醒。

    旧客户端忽略本端点继续本地告警；请求失败/404 时 App 恢复原逻辑。
    """
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        if pet is None or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
    return {'capability': 'care-v1', 'managedRules': list(care.MANAGED_RULES)}


@bp.get('/pets/<int:pet_id>/care/topics')
def care_topics(pet_id):
    """W09 主题追溯：伙伴页按状态查看主动提醒主题与依据（含已恢复/关闭）。"""
    user, error = require_app_user()
    if error:
        return error
    status = request.args.get('status')
    if status not in (None, 'open', 'ongoing', 'recovered', 'closed'):
        return {'message': '状态过滤无效'}, 400
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        if pet is None or pet.user_id != user['id']:
            return {'message': '宠物不存在'}, 404
        stmt = select(AgentCareTopic).where(AgentCareTopic.pet_id == pet_id)
        if status:
            stmt = stmt.where(AgentCareTopic.status == status)
        rows = s.execute(stmt.order_by(AgentCareTopic.updated_at.desc()).limit(50)).scalars().all()
        return {'topics': [care.public(row) for row in rows]}
