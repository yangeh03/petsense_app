"""双 Agent 群聊：接收、路由持久化、逐条生成与消息协议（docs/agent-dual-agent-chat-design.md §3–§4）。

支持 group-v1 的客户端默认进入双 Agent 群聊。设置 PETSENSE_DUAL_AGENT_ENABLED=0 可全局回退。
新协议由客户端声明 protocol=group-v1（POST 正文 / GET 与 WS 的查询参数）。总开关或调度线程关闭时
（PETSENSE_SCHEDULER_DISABLED=1），即使客户端声明也走旧路径。未声明的客户端始终走旧协议。

一个回合的流程：
  POST 只做校验、幂等去重与入队（202，立即返回）→ 调度线程把回合交给本模块的线程池 →
  1. 路由：router.route 一次不带工具的模型调用，异常时走有限澄清的兜底路由；路由与发言计划落库，
     重试沿用同一份分发结果；
  2. 生成：按计划顺序逐条运行 loop.run_dialogue 的单角色模式（每位一条、各自独立轨迹）。
     首版串行：下一条开始前上一条已提交，所以后发的可以承接先发的；
  3. 收尾：全部终态后镜像成旧回合结构（reply/blocks/speaker/sources）并推 turn 帧。
关掉开关即回退：新消息回到旧路径，已入队的回合按持久化的计划收尾。
"""
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from flask import request
from sqlalchemy import select, update

from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models import Pet
from petsense.db.models.agent import AgentMessage, AgentRoute, AgentTurn
from petsense.db.models.memory import PetMemory
from petsense.agent import jobs, loop, model, router, runs, snapshot, ws
from petsense.agent import settings as settings_service
from petsense.agent.registry import ASSISTANT_ROLE, PET_ROLE

logger = logging.getLogger(__name__)

PROTOCOL = 'group-v1'
ENABLED_ENV = 'PETSENSE_DUAL_AGENT_ENABLED'
TURN_KIND = 'agent_group_turn'
TARGETS = ('auto', 'pet', 'assistant', 'both')
ROLE_NAMES = {PET_ROLE: '爱宠', ASSISTANT_ROLE: '照料助手'}
MARKS = {PET_ROLE: '【爱宠】', ASSISTANT_ROLE: '【照料助手】'}
TURN_WALL_S = 240.0        # 整轮时限（路由 + 两位）：从主人发送算起，之后不再开始新的生成
TURN_CALL_BUDGET = 20      # 整轮模型调用预算（两位合计），防失控循环
STALE_PENDING_S = 360.0    # 等待超过即按中断收尾（旧路径为 5 分钟）
POOL_SIZE = 4
WS_THROTTLE_S = 0.06
DB_THROTTLE_S = 0.5
MESSAGE_REF_RE = re.compile(r'^(?:m\d+|t\d+-\d+)$')
FAILED_TURN_NOTE = '暂时没能接上照料助手，你的话已经保留。稍后可以重新发送。'
ERROR_NOTES = {
    'model_error': '这条消息没有生成成功，请稍后重试。',
    'interrupted': '上次生成中断了，这条消息没有完成，可以重试。',
    'budget_exhausted': '这一轮用时太长，这条消息没有完成，可以重试。',
    'timeout': '这一轮用时太长，这条消息没有完成，可以重试。',
}
TOOL_LABELS = {
    'forget_memory': '忘掉记忆', 'prepare_profile_update': '准备档案修改', 'prepare_role_settings': '修改助手设定',
    'draft_social_post': '保存社区草稿', 'create_diary': '生成运动日记', 'control_device': '发起远程录音',
    'propose_task_change': '生成委托候选', 'write_memory': '保存记忆',
}
_PET_TASKS = {
    'respond': '回应主人对你说的话：用一两句陪伴表达接住主人的情绪或寒暄。',
    'acknowledge': '主人点名了你，但这句话里需要查证、知识或操作的部分归照料助手。'
                   '你只用一句话接住主人的话（例如把那部分交给照料助手），不回答专业内容，也不报任何数据。',
    'clarify': '这句话还看不清主人想聊什么：用你的口吻问一个简短的必要澄清问题，不猜测主题，也不展开。',
    'answer': '回应主人这句话：用一两句陪伴表达自然接话。',
}
_ASSISTANT_TASKS = {
    'read': '回答主人关于本宠已观测数据的问题：需要数据时用观测工具现场查询，结论只来自本次工具结果。',
    'knowledge': '回答主人的养宠知识问题，第一句先给结论。',
    'search': '主人要求联网核实，或答案依赖会过期的信息：判断后决定是否搜索，搜索了就给出来源。',
    'write': '处理主人的修改或保存请求：明确指令按规则直写，其余出确认卡。',
    'observe': '主人希望你持续留意或到时回信：按委托协议生成待确认候选。',
    'social': '回应主人对你说的话。',
    'unknown': '回答主人这条消息。',
}

RECHECK_S = 5                 # 回合作业的复查间隔：回合在后台线程里生成，作业保持到期复查直到回合结束
_pool = None
_pool_lock = threading.Lock()
_inflight = set()              # 本进程正在生成的消息 id：区分「在跑」与「上次进程中断」
_active = set()                # 本进程正在处理的回合 id：复查时发现已在处理就不再重复提交
_busy = threading.Condition()  # 保护 _inflight 与待处理任务数
_pending = 0
_turn_locks = {}
_turn_locks_guard = threading.Lock()


# ---------------------------------------------------------------------------
# 全局回退开关与协议声明
# ---------------------------------------------------------------------------

def enabled():
    return os.environ.get(ENABLED_ENV, '1').strip().lower() in ('1', 'true', 'yes', 'on')


def active_for(user):
    """双 Agent 适用于所有已登录用户，回合生成需要调度线程。"""
    return isinstance(user, dict) and enabled() and jobs.scheduler_enabled()


def declared(data=None):
    """客户端是否声明群聊协议：POST 正文 protocol=group-v1，或查询参数 protocol=group-v1（GET/WS）。"""
    if request.args.get('protocol') == PROTOCOL:
        return True
    if data is None:
        data = request.get_json(silent=True)
    return isinstance(data, dict) and data.get('protocol') == PROTOCOL


def client_group(user, data=None):
    return declared(data) and active_for(user)


def job_key(turn_id):
    return f'group-turn:{turn_id}'


def task_text(role, kind, intent, scope, together):
    """服务端按路由生成的任务说明（确定性文本）：两位的分工在这里写死，不交给模型自行商量。

    together 表示本轮两位都发言。
    """
    if role == PET_ROLE:
        task = _PET_TASKS[kind]
        if together:
            task += '事实、查证或操作的部分由照料助手另发一条，你不展开。'
        elif kind == 'respond':
            task += '这句话里如果还有要查证或操作的部分，用一句话交给照料助手，自己不展开。'
        return task
    if kind == 'clarify':
        return '这句话还看不清主人想要什么：只问一个简短的必要澄清问题，不猜测主题，不调用工具。'
    if scope == 'out_of_scope':
        task = '这句话不在服务范围内：简短说明不处理的原因；如果其中有与宠物相关的部分，只回应那一部分。'
    else:
        task = _ASSISTANT_TASKS.get(intent, _ASSISTANT_TASKS['unknown'])
    if together:
        task += ('爱宠负责陪伴表达：你不替它说话，也不复述或评价它的感受。'
                 '只回答主人明确请求的专业部分；陪伴话语中的动作不是另一个求助。'
                 '例如主人说想抱抱爱宠，再问今天的活动记录，只回答活动记录和必要的数据边界。')
    return task


# ---------------------------------------------------------------------------
# 序列化：消息视图与旧回合投影
# ---------------------------------------------------------------------------

def _blocks(raw):
    return [block for block in (json.loads(raw) if raw else []) if isinstance(block, dict)]


def _load(text, fallback):
    try:
        value = json.loads(text) if text else fallback
    except ValueError:
        value = fallback
    return value if isinstance(value, list) else fallback


def message_rows(session, turn_ids):
    """按回合取消息行（已路由的群聊回合才有）：{turn_id: [AgentMessage 按发言顺序]}。"""
    grouped = {}
    if turn_ids:
        rows = session.execute(select(AgentMessage).where(AgentMessage.turn_id.in_(turn_ids))
                               .order_by(AgentMessage.position, AgentMessage.id)).scalars().all()
        for row in rows:
            grouped.setdefault(row.turn_id, []).append(row)
    return grouped


def message_public(row):
    """一条回复消息的公开形态：id 自创建起稳定（m<行 id>），version 随每次持久化变更递增。"""
    return {'id': f'm{row.id}', 'turnId': row.turn_id, 'role': row.role, 'position': row.position,
            'kind': row.kind, 'status': row.status, 'text': row.text, 'version': row.version,
            'sources': _load(row.sources, []),
            'blocks': _blocks(row.blocks) or None,
            'error': row.error, 'errorNote': ERROR_NOTES.get(row.error) if row.error else None,
            'readOnly': False, 'createdAt': iso_z(row.created_at),
            'finishedAt': iso_z(row.finished_at) if row.finished_at else None}


def _projected(message_id, turn, position, role, owned, status, sources):
    texts = [block.get('text') or '' for block in owned if block.get('type') == 'text']
    return {'id': message_id, 'turnId': turn.id, 'role': role, 'position': position, 'kind': 'answer',
            'status': status, 'text': '\n'.join(texts), 'version': 1, 'sources': sources,
            'blocks': owned or None, 'error': None, 'errorNote': None, 'readOnly': True,
            'createdAt': iso_z(turn.created_at), 'finishedAt': None}


def project(turn):
    """旧回合（旧路径与后台回信）投影成只读消息，形状与真实消息一致。

    有内容块按块投影（文本块各成一条，卡片与来源跟在最后一条后面）；没有内容块时按回合的发言人与
    正文回退（旧路径的流式中间态与失败说明）。ID 形如 t<回合 id>-<块下标>，在同一份历史里稳定，可被引用。
    """
    blocks = _blocks(turn.blocks)
    texts = [(index, block) for index, block in enumerate(blocks) if block.get('type') == 'text']
    if not texts and turn.status == 'pending' and not (turn.reply or '').strip():
        return []  # 还没有任何可见内容（含尚未完成路由的群聊回合）
    status = 'running' if turn.status == 'pending' else ('complete' if turn.status == 'complete' else 'failed')
    cards = [block for block in blocks if block.get('type') != 'text']
    sources = _load(turn.sources, [])
    if not texts and (turn.reply or '').strip():
        # 没有内容块但已有正文（旧路径的流式中间态、失败说明）：正文里的身份标记按同一规则拆开，
        # 角色已由消息的 role 表达，不再把标记留在正文里
        speaker = turn.speaker if turn.speaker in (PET_ROLE, ASSISTANT_ROLE) else ASSISTANT_ROLE
        texts = [(None, block) for block in loop.split_blocks(turn.reply, speaker)[0]]
    if not texts and not cards:
        return []  # 没有任何可见内容
    items = []
    if texts:
        for position, (index, block) in enumerate(texts):
            last = position == len(texts) - 1
            speaker = block.get('speaker')
            role = speaker if speaker in (PET_ROLE, ASSISTANT_ROLE) else turn.speaker or ASSISTANT_ROLE
            items.append(_projected(f't{turn.id}-{index if index is not None else position}', turn, position, role,
                                    [block] + (cards if last else []), status, sources if last else []))
    else:
        items.append(_projected(f't{turn.id}-0', turn, 0, turn.speaker or ASSISTANT_ROLE, cards, status, sources))
    return items


def _public(turn, messages):
    from petsense.agent.routes import public  # 延迟导入：routes 依赖本模块
    return public(turn, messages)


def message_list(session, turn):
    rows = message_rows(session, [turn.id]).get(turn.id)
    return [message_public(row) for row in rows] if rows is not None else project(turn)


def turn_public(session, turn):
    """回合公开快照（带 messages）：群聊回合取真实消息行，旧回合投影成只读消息。"""
    return _public(turn, message_list(session, turn))


def list_public(session, turns):
    """一组回合的公开快照（带 messages）。"""
    grouped = message_rows(session, [turn.id for turn in turns])
    return [_public(turn, [message_public(row) for row in grouped[turn.id]] if turn.id in grouped
                    else project(turn)) for turn in turns]


def view(user, session, turn):
    """按客户端协议取回合视图：群聊协议带 messages；旧客户端是旧结构（「两位」等同未指定）。"""
    return turn_public(session, turn) if client_group(user) else _public(turn, None)


def routed_ids(session, turns):
    """这些回合里已完成路由（有消息行）的回合 id。"""
    ids = [turn.id for turn in turns if turn.status == 'pending']
    if not ids:
        return set()
    return {turn_id for (turn_id,) in session.execute(
        select(AgentMessage.turn_id).where(AgentMessage.turn_id.in_(ids)).distinct())}


def route_view(session, turn_id):
    """路由与消息的只读视图（验收夹具与测试取证：路由 → 消息 → 运行轨迹）。"""
    route = session.execute(select(AgentRoute).where(AgentRoute.turn_id == turn_id)).scalar_one_or_none()
    view = None
    if route is not None:
        try:
            raw = json.loads(route.raw_json) if route.raw_json else {}
        except ValueError:
            raw = {}
        view = {'id': route.id, 'scope': route.scope, 'intent': route.intent,
                'companion': bool(route.companion), 'professional': bool(route.professional),
                'follows': bool(route.follows), 'reason': route.reason, 'source': route.source,
                'plan': _load(route.plan_json, []), 'raw': raw if isinstance(raw, dict) else {}}
    return {'route': view, 'messages': message_list(session, session.get(AgentTurn, turn_id))}


# ---------------------------------------------------------------------------
# 引用与话题参考
# ---------------------------------------------------------------------------

def message_ref_exists(session, turn, ref):
    """消息级引用是否指向该回合里的一条消息（调用方已确认回合归属同一宠物）。"""
    return (isinstance(ref, str) and MESSAGE_REF_RE.match(ref) is not None
            and any(item['id'] == ref for item in message_list(session, turn)))


def quoted_message(session, turn):
    """被引用的消息 {'id','speaker','text'}：消息级引用精确到一条；只有回合级引用时取该回合最后一条有正文的消息。"""
    if turn.reply_to is None:
        return None
    related = session.get(AgentTurn, turn.reply_to)
    if related is None or related.pet_id != turn.pet_id:
        return None
    items = message_list(session, related)
    if turn.reply_to_message is not None:
        items = [item for item in items if item['id'] == turn.reply_to_message]
    for item in reversed(items):
        if (item['text'] or '').strip():
            return {'id': item['id'], 'speaker': item['role'], 'text': item['text']}
    return None


def topic_lines(session, turn):
    """路由参考的近期话题：话题内最近几轮，由早到晚，每位发言人各一行（历史查询与旧路径同口径）。"""
    from petsense.agent.routes import history_rows
    lines = []
    for row in reversed(history_rows(session, turn.pet_id, turn.id, turn.topic_id,
                                     limit=router.TOPIC_TURNS)):
        lines.append({'speaker': '主人', 'text': row.text or '（图片）'})
        if not (row.reply or '').strip():
            continue
        # 回复带身份标记（群聊镜像与旧路径的双人回复）：按实际发言人拆开，不让一位的名字底下出现两位的话
        default = row.speaker if row.speaker in (PET_ROLE, ASSISTANT_ROLE) else ASSISTANT_ROLE
        for block in loop.split_blocks(row.reply, default)[0]:
            lines.append({'speaker': ROLE_NAMES.get(block['speaker'], ASSISTANT_ROLE), 'text': block['text']})
    return lines


# ---------------------------------------------------------------------------
# 作业与线程池：作业只是持久触发器，生成交给本模块的线程池
# ---------------------------------------------------------------------------

def start():
    """app.py 启动时注册：群聊回合作业的执行器（开关关闭时不会有作业入队）。"""
    jobs.register_handler(TURN_KIND, handle_job)


def handle_job(job, now):
    """作业只是回合的持久触发器：把回合交给线程池后立即返回，不占调度线程。

    回合结束前作业保持到期复查（next_run_at），所以进程在生成中途重启后调度会重新领取它：
    已完成的消息跳过、中断的那条如实标记失败、尚未开始的消息继续。回合结束后作业才结束。
    """
    turn_id = (job.get('payload') or {}).get('turn_id')
    if isinstance(turn_id, int):
        with session_scope() as s:
            turn = s.get(AgentTurn, turn_id)
            pending = turn is not None and turn.status == 'pending'
        if pending:
            if submit(turn_id):
                logger.info('agent group turn %s accepted by worker', turn_id)
            return {'next_run_at': now + timedelta(seconds=RECHECK_S)}
    return None


def submit(turn_id):
    """把回合交给线程池；本进程已在处理的回合不重复提交。返回是否新提交了任务。"""
    global _pool, _pending
    with _busy:
        if turn_id in _active:
            return False
        _active.add(turn_id)
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=POOL_SIZE, thread_name_prefix='agent-group')
        pool = _pool
    with _busy:
        _pending += 1
    try:
        pool.submit(_guarded, turn_id)
    except RuntimeError:  # 解释器退出阶段线程池已关闭
        with _busy:
            _active.discard(turn_id)
            _pending -= 1
            _busy.notify_all()
    return True


def wait_idle(timeout=60.0):
    """等本进程已提交的生成任务结束（测试与验收夹具同步用，生产路径不调用）。"""
    with _busy:
        return _busy.wait_for(lambda: _pending == 0, timeout)


def _guarded(turn_id):
    global _pending
    try:
        process_turn(turn_id)
    except Exception:  # noqa: BLE001 生成失败不退出线程，回合按失败收尾
        logger.exception('agent group turn %s crashed', turn_id)
        _abandon(turn_id)
    finally:
        with _busy:
            _active.discard(turn_id)
            _pending -= 1
            _busy.notify_all()


def _turn_lock(turn_id):
    with _turn_locks_guard:
        return _turn_locks.setdefault(turn_id, threading.Lock())


def process_turn(turn_id, now=None):
    """处理一个回合（可重复进入）：已完成的消息跳过；上次进程中断的消息标记失败后继续其余部分。"""
    now = now or utc_now()
    with _turn_lock(turn_id):
        _process(turn_id, now)


def _process(turn_id, now):
    with session_scope() as s:
        turn = s.get(AgentTurn, turn_id)
        if turn is None or turn.status != 'pending':
            return
        pet = s.get(Pet, turn.pet_id)
        if pet is None:
            turn.status, turn.reply = 'failed', FAILED_TURN_NOTE
            return
        # 整轮时限从主人发送（重试过的回合从重新开始等待）算起；重启后接着处理时至少留出一位完整生成的窗口
        elapsed = max(0.0, (utc_now() - (turn.pending_at or turn.created_at)).total_seconds())
        remaining = max(TURN_WALL_S - elapsed, loop.RUN_WALL_LIMIT_S)
    deadline = time.monotonic() + remaining
    plan = _route_stage(turn_id, now)
    if not plan:
        return
    budget = {'calls_left': TURN_CALL_BUDGET}
    for item in plan:
        _run_one(turn_id, item, budget, deadline, now)
    _finalize(turn_id, utc_now())


def _route_stage(turn_id, now):
    """阶段 1：路由并落库（路由记录 + 排队中的消息行）。返回发言计划；回合已不在等待时返回 None。"""
    with session_scope() as s:
        existing = s.execute(select(AgentRoute).where(AgentRoute.turn_id == turn_id)).scalar_one_or_none()
        if existing is not None:
            return _load(existing.plan_json, [])  # 重试沿用同一份分发结果，不重新路由
        turn = s.get(AgentTurn, turn_id)
        pet = s.get(Pet, turn.pet_id) if turn else None
        if turn is None or pet is None or turn.status != 'pending':
            return None
        inputs = {'pet_name': pet.name, 'target': turn.target, 'text': turn.text,
                  'quoted': quoted_message(s, turn), 'topic': topic_lines(s, turn),
                  'image_count': len(_load(turn.images, []))}
        pet_id = pet.id
    route, trace, status = router.route(**inputs)
    runs.save(pet_id, turn_id, trace, status)
    with session_scope() as s:
        row = s.execute(select(AgentRoute).where(AgentRoute.turn_id == turn_id)).scalar_one_or_none()
        if row is not None:
            return _load(row.plan_json, [])  # 并发触发下另一位已写
        turn = s.get(AgentTurn, turn_id)
        pet = s.get(Pet, pet_id)
        if turn is None or pet is None or turn.status != 'pending':
            return None
        plan = route['plan']
        s.add(AgentRoute(
            turn_id=turn_id, pet_id=pet_id, scope=route['scope'], intent=route['intent'],
            companion=int(route['companion']), professional=int(route['professional']),
            follows=int(route['follows']), reason=route['reason'], source=route['source'],
            plan_json=json.dumps(plan, ensure_ascii=False),
            raw_json=json.dumps(trace['messages'][-1], ensure_ascii=False), created_at=now))
        for position, item in enumerate(plan):
            s.add(AgentMessage(turn_id=turn_id, pet_id=pet_id, role=item['role'], position=position,
                               kind=item['kind'], follows=int(item['follows']), status='queued',
                               text='', version=1, sources='[]', effect_json='[]', attempts=0,
                               created_at=now))
        s.flush()
        payload = turn_public(s, turn)
        user_id = pet.user_id
    ws.push_turn(user_id, pet_id, payload)  # 让客户端尽早拿到消息 ID 与排队状态
    return plan


def _run_one(turn_id, item, budget, deadline, now):
    with session_scope() as s:
        row = s.execute(select(AgentMessage).where(
            AgentMessage.turn_id == turn_id, AgentMessage.role == item['role'])).scalar_one_or_none()
        if row is None:
            return
        message_id, status = row.id, row.status
    if status in ('complete', 'failed'):
        return
    if status == 'running':
        with _busy:
            live = message_id in _inflight
        if not live:
            # 上次进程在这里中断：没有结果可恢复，如实标记后继续后面的消息（主人可以只重试这一条）
            _mark_interrupted(message_id, now)
        return
    _generate(message_id, item, budget, deadline, now)


def _mark_interrupted(message_id, now):
    """上次进程在生成中途退出的消息：落失败并补一条失败轨迹（那条消息此前没有轨迹，回放时才查得到）。"""
    with session_scope() as s:
        row = s.get(AgentMessage, message_id)
        turn = s.get(AgentTurn, row.turn_id) if row else None
        pet = s.get(Pet, row.pet_id) if row else None
        if row is None or turn is None or pet is None:
            return
        route = s.execute(select(AgentRoute).where(AgentRoute.turn_id == turn.id)).scalar_one_or_none()
        trace = {'form': 'dialogue', 'trigger': {'target': turn.target, 'text': (turn.text or '')[:500]},
                 'conclusion': {'scope': route.scope if route else 'unknown',
                                'intent': route.intent if route else 'unknown',
                                'speaker': row.role, 'reason_code': 'interrupted'},
                 'messages': [{'interrupted': True, 'attempts': row.attempts}],
                 'tool_summary': [], 'budget': {}, 'degraded': [],
                 'role': row.role, 'messageId': row.id, 'routeId': route.id if route else None}
        pet_id, turn_id = pet.id, turn.id
    runs.save(pet_id, turn_id, trace, 'failed', error='interrupted', message_id=message_id)
    _finalize_message(message_id, status='failed', error='interrupted', now=now)


def _error_code(exc, deadline):
    if isinstance(exc, ValueError) and str(exc) == loop._TURN_BUDGET_ERROR:
        return 'budget_exhausted'
    if time.monotonic() >= deadline:
        return 'timeout'
    return 'model_error'


def _outcome_blocks(outcome):
    """与旧路径同口径：正文块 + 最后一张确认卡 + 去重后的动作回执。"""
    blocks = list(outcome['blocks'] or [])
    if outcome.get('proposals'):
        blocks.append({'type': 'proposal', 'proposal': outcome['proposals'][-1]})
    seen = set()
    for action_payload in outcome.get('actions') or []:
        if action_payload.get('id') not in seen:
            seen.add(action_payload.get('id'))
            blocks.append({'type': 'action', 'action': action_payload})
    return blocks


def _failed_trace(context, code):
    return {'form': 'dialogue', 'trigger': {'target': context['target'], 'text': context['text'][:500]},
            'conclusion': {'scope': context['route']['scope'], 'intent': context['route']['intent'],
                           'speaker': context['role'], 'reason_code': code},
            'messages': [], 'tool_summary': [], 'budget': {}, 'degraded': [],
            'role': context['role'], 'messageId': context['message_id'], 'routeId': context['route_id']}


def _push_stale(user_id, pet_id, turn_id):
    """被替代的旧卡所在回合：提交后推帧让历史卡片翻「已有新候选」（与旧路径一致）。"""
    with session_scope() as s:
        turn = s.get(AgentTurn, turn_id)
        if turn is None or turn.pet_id != pet_id:
            return
        payload = turn_public(s, turn)
    ws.push_turn(user_id, pet_id, payload)


def _generate(message_id, item, budget, deadline, now):
    """阶段 2：生成一条消息。领取用条件更新，重复触发不会并发生成同一条。"""
    role = item['role']
    with _busy:
        _inflight.add(message_id)
    try:
        with session_scope() as s:
            claimed = s.execute(update(AgentMessage).where(
                AgentMessage.id == message_id, AgentMessage.status == 'queued').values(
                status='running', started_at=now, attempts=AgentMessage.attempts + 1,
                version=AgentMessage.version + 1)).rowcount
            if not claimed:
                return
            s.expire_all()
            row = s.get(AgentMessage, message_id)
            turn = s.get(AgentTurn, row.turn_id)
            pet = s.get(Pet, row.pet_id)
            abandoned = pet is None or turn.pet_id != pet.id
            if not abandoned:
                pet_id, user_id, turn_id = pet.id, pet.user_id, turn.id
                context = _context(s, row, turn, pet, now, budget, deadline, item.get('request'))
                opened = message_public(row)
        if abandoned:
            _finalize_message(message_id, status='failed', error='model_error', now=utc_now())
            return
        ws.push_message(user_id, pet_id, opened)
        on_progress = context.pop('on_progress')
        try:
            outcome = loop.run_dialogue(context, on_progress=on_progress)
        except Exception as exc:  # noqa: BLE001 模型或工具异常：这一位按失败收尾，另一位照常
            code = _error_code(exc, deadline)
            logger.warning('Agent group message %s failed: %s', message_id, type(exc).__name__)
            runs.save(pet_id, turn_id, _failed_trace(context, code), 'failed',
                      error=f'{type(exc).__name__}: {str(exc)[:300]}', message_id=message_id)
            _finalize_message(message_id, status='failed', error=code, now=utc_now())
            return
        runs.save(pet_id, turn_id, outcome['trace'], 'degraded' if outcome['degraded'] else 'complete',
                  message_id=message_id)
        _finalize_message(message_id, status='complete', text=outcome['reply'],
                          blocks=_outcome_blocks(outcome), sources=outcome['sources'], now=utc_now())
        for stale_turn_id in [tid for tid in outcome.get('refreshedTurnIds') or [] if tid != turn_id]:
            _push_stale(user_id, pet_id, stale_turn_id)
    finally:
        with _busy:
            _inflight.discard(message_id)


def _context(session, row, turn, pet, now, budget, deadline, assigned_request=None):
    """单角色运行的输入：范围与任务取自已持久化的路由，承接与引用取自已提交的消息。"""
    from petsense.agent.routes import history_rows
    siblings = message_rows(session, [turn.id])[turn.id]
    route = session.execute(select(AgentRoute).where(AgentRoute.turn_id == turn.id)).scalar_one_or_none()
    scope, intent = (route.scope, route.intent) if route is not None else ('unknown', 'unknown')
    context = {
        'pet_id': pet.id, 'user_id': pet.user_id, 'text': turn.text, 'target': turn.target,
        'history': [{'text': old.text, 'reply': old.reply, 'images': _load(old.images, [])}
                    for old in reversed(history_rows(session, pet.id, turn.id, turn.topic_id))],
        'images': _load(turn.images, []), 'snapshot': snapshot.assemble(session, pet, now),
        'now_iso': iso_z(now), 'turn_id': turn.id, 'settings': settings_service.load(session, pet.user_id),
        'role': row.role, 'route': {'scope': scope, 'intent': intent},
        'pet_handoff': row.role == PET_ROLE and row.kind == 'acknowledge',
        'assigned_request': assigned_request or turn.text,
        'task': task_text(row.role, row.kind, intent, scope, len(siblings) > 1),
        'message_id': row.id, 'route_id': route.id if route is not None else None, 'pet_name': pet.name,
        'deadline': deadline, 'turn_budget': budget,
    }
    earlier = [old for old in siblings if old.position < row.position]
    if row.follows and earlier and earlier[-1].status == 'complete' and (earlier[-1].text or '').strip():
        context['follows_text'] = earlier[-1].text
    quoted = quoted_message(session, turn)
    if quoted:
        context.update(quoted_speaker=quoted['speaker'], quoted_text=quoted['text'])
    replayed, retry_note = _replay_state(session, row)
    context['replayed'] = replayed
    if replayed:
        # 回放的写入计入「本回合已保存」：模型换个说法再写同一句时由工具按既有规则去重
        context['memory_writes'] = [((entry.get('arguments') or {}).get('kind'),
                                     ((entry.get('arguments') or {}).get('summary') or '').strip())
                                    for entry in replayed]
    if retry_note:
        context['retry_note'] = retry_note
    # 副作用调用先落写前意图再执行：重试时据此核对哪些操作已经生效（design doc §2.1）
    context['on_effect'] = _effect_journal(row.id)
    # 帧里的 messageId 用公开 id（m<行 id>）：客户端按它对上快照与增量，两种帧必须是同一个值
    context['on_progress'] = _progress(pet.user_id, pet.id, turn.id, row.id, row.role, row.version, earlier)
    return context


def _progress(user_id, pet_id, turn_id, row_id, role, version, earlier):
    """流式回调：按消息 ID/版本推增量；同时把累计正文镜像给旧协议连接与轮询快照。"""
    message_id = f'm{row_id}'
    state = {'pushed': None, 'last_push': None, 'last_write': None, 'version': version}
    settled = [(old.role, old.text.strip()) for old in earlier
               if old.status == 'complete' and (old.text or '').strip()]

    def legacy_text(text):
        return '\n'.join([MARKS[owner] + body for owner, body in settled] + [MARKS[role] + text])

    def on_progress(speaker, text):
        if text == model.SEARCH_PROGRESS_HINT:
            # 联网进度是状态而不是正文：只推状态帧，不进消息文本
            ws.push_message_status(user_id, pet_id, turn_id, message_id, role, 'searching')
            return
        if not text.strip():
            return  # 空白首帧与工具轮之间的空文本不覆盖当前内容（与旧路径一致）
        moment = time.monotonic()
        # 「累计正文」只在一次模型调用内只增不减：工具轮之后模型会重新起头。这种替换不能等到下一次
        # 节流落库——先落库再推，增量带着更高的版本，客户端据此判定是替换而不是乱序的旧帧
        rewritten = state['pushed'] is not None and not text.startswith(state['pushed'])
        if rewritten:
            stored = _persist_progress(row_id, turn_id, text, legacy_text(text), role)
            if stored is not None:
                state['version'] = stored
            state['last_write'] = moment
        if text != state['pushed'] and (rewritten or state['last_push'] is None
                                        or moment - state['last_push'] >= WS_THROTTLE_S):
            ws.push_message_delta(user_id, pet_id, turn_id, message_id, role, text, state['version'])
            ws.push_delta(user_id, pet_id, turn_id, legacy_text(text), role)
            state['pushed'], state['last_push'] = text, moment
        if not rewritten and (state['last_write'] is None
                              or moment - state['last_write'] >= DB_THROTTLE_S):
            # 轮询恢复需要中间快照；落库频率不随 WS 提高
            stored = _persist_progress(row_id, turn_id, text, legacy_text(text), role)
            if stored is not None:
                state['version'] = stored
            state['last_write'] = moment
    return on_progress


def _persist_progress(message_id, turn_id, text, legacy, role):
    with session_scope() as s:
        moved = s.execute(update(AgentMessage).where(
            AgentMessage.id == message_id, AgentMessage.status == 'running').values(
            text=text, version=AgentMessage.version + 1)).rowcount
        if not moved:
            return None
        s.expire_all()  # 先刷新再改：expire_all 会丢弃尚未落库的属性改动，顺序不能反
        turn = s.get(AgentTurn, turn_id)
        if turn is not None and turn.status == 'pending':
            turn.reply, turn.speaker = legacy, role
        return s.get(AgentMessage, message_id).version


def _effect_journal(message_id):
    """副作用调用日志（写前意图 → 结果回填）：重试时据此核对哪些操作已经生效。"""
    def on_effect(name, arguments, outcome):
        try:
            with session_scope() as s:
                row = s.get(AgentMessage, message_id)
                entries = _load(row.effect_json, [])
                key = loop._canonical_arguments(arguments)
                if outcome == 'intent':
                    entries.append({'seq': len(entries) + 1, 'name': name, 'args_key': key,
                                    'arguments': arguments, 'status': 'pending', 'at': iso_z(utc_now())})
                else:
                    target = entries[-1] if entries else None
                    if target is not None and target.get('status') == 'pending' \
                            and target.get('name') == name and target.get('args_key') == key:
                        target['status'] = 'done' if isinstance(outcome, dict) else 'failed'
                        if isinstance(outcome, dict):
                            target['result'] = outcome
                row.effect_json = json.dumps(entries, ensure_ascii=False, default=str)
            return True
        except Exception:  # noqa: BLE001 日志写不成功就不执行这次操作，宁可少做不可重复
            logger.exception('agent group effect journal failed')
            return outcome != 'intent'
    return on_effect


def _memory_entry_saved(session, row, entry):
    """写前意图已落库、结果未回填的 write_memory：按内容与来源回合核对是否其实已经保存。"""
    arguments = entry.get('arguments')
    if not isinstance(arguments, dict):
        return None
    try:
        since = datetime.fromisoformat(str(entry.get('at')).replace('Z', '+00:00'))
    except ValueError:
        return None
    candidates = session.execute(select(PetMemory).where(
        PetMemory.pet_id == row.pet_id, PetMemory.kind == arguments.get('kind'),
        PetMemory.created_at >= since - timedelta(seconds=5))).scalars().all()
    summary = str(arguments.get('summary') or '').strip()
    for memory in candidates:
        if (memory.summary or '').strip() == summary and {'type': 'turn', 'id': row.turn_id} \
                in _load(memory.source_refs, []):
            return memory
    return None


def _replay_state(session, row):
    """重试前核对这条消息此前尝试留下的副作用：已生效的不可重放调用回放结果，其余如实告知模型。"""
    replayed, applied, changed = [], [], False
    for entry in _load(row.effect_json, []):
        if not isinstance(entry, dict):
            continue
        name, arguments = entry.get('name'), entry.get('arguments') or {}
        if name in loop.NO_REPLAY_TOOLS:
            if entry.get('status') != 'done':
                memory = _memory_entry_saved(session, row, entry)
                if memory is not None:
                    entry['status'], changed = 'done', True
                    entry['result'] = {'saved': True, 'memoryId': memory.id, 'kind': memory.kind,
                                       'subject': memory.subject, 'version': memory.version,
                                       'summary': memory.summary, 'certainty': memory.certainty,
                                       'note': '这条在上次生成时已经保存，本次没有重复写入'}
            if entry.get('status') == 'done':
                replayed.append(entry)
                applied.append(f'已保存一条记忆（{str(arguments.get("summary") or "")[:40]}）')
        elif entry.get('status') == 'done':
            applied.append(f'已执行「{TOOL_LABELS.get(name, name)}」')
    if changed:
        row.effect_json = json.dumps(_load(row.effect_json, []), ensure_ascii=False)
    if not applied:
        return [], None
    return replayed, ('（系统补充）这条消息上一次没有生成完成，但其中有些操作已经生效，不要重复执行：'
                      + '；'.join(applied) + '。需要提到它们时按已有结果如实说明。')


def _finalize_message(message_id, *, status, text='', blocks=None, sources=None, error=None, now=None):
    """把生成中的消息落到终态（条件更新：已被收尾或不在生成中则不动），提交后推消息帧。"""
    now = now or utc_now()
    values = {'status': status, 'finished_at': now, 'version': AgentMessage.version + 1}
    if status == 'complete':
        values.update(text=text, error=None,
                      blocks=json.dumps(blocks, ensure_ascii=False) if blocks else None,
                      sources=json.dumps(sources or [], ensure_ascii=False))
    else:
        values.update(text='', blocks=None, sources='[]', error=error)
    with session_scope() as s:
        moved = s.execute(update(AgentMessage).where(
            AgentMessage.id == message_id, AgentMessage.status == 'running').values(**values)).rowcount
        if not moved:
            return None
        s.expire_all()
        row = s.get(AgentMessage, message_id)
        payload, pet_id = message_public(row), row.pet_id
        pet = s.get(Pet, pet_id)
        user_id = pet.user_id if pet else None
    ws.push_message(user_id, pet_id, payload)
    return payload


# ---------------------------------------------------------------------------
# 收尾：镜像成旧回合结构、卡片同步、过期与重试
# ---------------------------------------------------------------------------

def _block_key(block):
    if block.get('type') == 'proposal' and isinstance(block.get('proposal'), dict):
        return ('proposal', block['proposal'].get('id'))
    if block.get('type') == 'action' and isinstance(block.get('action'), dict):
        return ('action', block['action'].get('id'))
    return None


def sync_blocks(session, turn):
    """回合级 blocks 被既有路径改写后（确认卡决策、动作回执、编辑候选），同步到所属消息。

    群聊里卡片属于发出它的那条照料助手消息。每条消息的卡片按身份键（提案 id / 动作 id）回到回合
    blocks 取最新状态；回合里新出现、还没有归属的卡片归照料助手那条（没有时归最后一条）。
    内容有变化的消息递增版本。返回这些消息的 id。
    """
    messages = message_rows(session, [turn.id]).get(turn.id)
    if not messages:
        return []
    latest = {}
    for block in _blocks(turn.blocks):
        key = _block_key(block)
        if key is not None:
            latest[key] = block
    if not latest:
        return []
    owner = next((row for row in messages if row.role == ASSISTANT_ROLE), messages[-1])
    owned = {key for row in messages for key in map(_block_key, _blocks(row.blocks)) if key is not None}
    extras = [block for key, block in latest.items() if key not in owned]
    changed = []
    for row in messages:
        blocks = _blocks(row.blocks)
        keys = [_block_key(block) for block in blocks]
        removed = [index for index, key in enumerate(keys) if key is not None and key not in latest]
        merged = [latest.get(key, block) for key, block in zip(keys, blocks) if key is None or key in latest]
        if extras and row is owner:
            # 确认后候选卡被动作回执取代：回执卡回到候选卡原来的位置；其余新卡接在最后
            at = removed[0] if removed else len(merged)
            merged[at:at] = extras
        if merged != blocks:
            row.blocks = json.dumps(merged, ensure_ascii=False) if merged else None
            row.version += 1
            changed.append(row.id)
    return changed


def _mirror(turn, messages):
    """把消息镜像成旧回合结构：旧客户端与既有读取方（收件箱水合、委托簿、深链）不改动即可读。"""
    lines, blocks, sources, speaker, seen = [], [], [], None, set()
    for row in messages:
        if row.status == 'complete':
            speaker = speaker or row.role
            body = row.text.strip()
            if body:
                lines.append(MARKS[row.role] + body)
            blocks.extend(_blocks(row.blocks) or ([{'type': 'text', 'speaker': row.role, 'text': body}]
                                                  if body else []))
            for source in _load(row.sources, []):
                key = json.dumps(source, ensure_ascii=False, sort_keys=True, default=str)
                if key not in seen:
                    seen.add(key)
                    sources.append(source)
        elif row.status == 'failed':
            note = ERROR_NOTES.get(row.error, ERROR_NOTES['model_error'])
            lines.append(MARKS[row.role] + '（' + note + '）')
            blocks.append({'type': 'text', 'speaker': row.role, 'text': '（' + note + '）'})
            speaker = speaker or row.role
    turn.reply = '\n'.join(lines)
    turn.speaker = speaker or (messages[0].role if messages else ASSISTANT_ROLE)
    turn.blocks = json.dumps(blocks, ensure_ascii=False) if blocks else None
    turn.sources = json.dumps(sources, ensure_ascii=False)
    turn.status = 'complete' if any(row.status == 'complete' for row in messages) else 'failed'


def _finalize(turn_id, now=None):
    """阶段 3：全部消息终态后收尾（镜像 + 推 turn 帧）。仍有消息在生成时不收尾。"""
    now = now or utc_now()
    with session_scope() as s:
        turn = s.get(AgentTurn, turn_id)
        messages = message_rows(s, [turn_id]).get(turn_id) if turn else None
        if turn is None or not messages or turn.status not in ('pending', 'failed'):
            return None
        if any(row.status in ('queued', 'running') for row in messages):
            return None
        sync_blocks(s, turn)
        _mirror(turn, message_rows(s, [turn_id])[turn_id])
        payload = turn_public(s, turn)
        pet = s.get(Pet, turn.pet_id)
        user_id, pet_id = (pet.user_id if pet else None), turn.pet_id
    ws.push_turn(user_id, pet_id, payload)
    return payload


def _abandon(turn_id):
    """处理过程抛出未预期异常：未完成的消息按失败收尾，不把回合留在等待。

    路由阶段就崩溃时还没有消息行：回合按失败收尾（与旧路径的失败说明一致），作业随之结束——
    否则回合一直等待，作业每次复查都重新触发同一个崩溃。
    """
    now = utc_now()
    with session_scope() as s:
        turn = s.get(AgentTurn, turn_id)
        if turn is None or turn.status not in ('pending', 'failed'):
            return
        messages = message_rows(s, [turn_id]).get(turn_id)
        if not messages:
            turn.status, turn.reply, turn.speaker = 'failed', FAILED_TURN_NOTE, ASSISTANT_ROLE
        for row in messages or ():
            if row.status in ('queued', 'running'):
                row.status, row.error, row.text, row.blocks = 'failed', 'model_error', '', None
                row.sources, row.finished_at, row.version = '[]', now, row.version + 1
        if messages:
            _mirror(turn, messages)
        payload = turn_public(s, turn)
        pet = s.get(Pet, turn.pet_id)
        user_id = pet.user_id if pet else None
    ws.push_turn(user_id, turn.pet_id, payload)


def expire(session, turn, now):
    """等待超过上限的群聊回合按中断收尾：未完成的消息落失败，回合镜像成失败态（可只重试失败的那条）。"""
    for row in message_rows(session, [turn.id]).get(turn.id) or []:
        if row.status in ('queued', 'running'):
            row.status, row.error, row.text, row.blocks, row.sources = 'failed', 'interrupted', '', None, '[]'
            row.finished_at, row.version = now, row.version + 1
    _mirror(turn, message_rows(session, [turn.id])[turn.id])


def retry(user, pet_id, turn_id):
    """只重试回合里失败的消息：回到排队并重新入队，成功的消息不动。返回 (body, status)。"""
    if not active_for(user):
        return {'message': '双 Agent 已停用，这条消息请重新发送', 'code': 'dual_agent_disabled'}, 409
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        turn = s.get(AgentTurn, turn_id)
        if pet is None or pet.user_id != user['id'] or turn is None or turn.pet_id != pet_id:
            return {'message': '宠物不存在'}, 404
        messages = message_rows(s, [turn_id]).get(turn_id)
        if not messages:
            return {'message': '这条消息不支持重试，请重新发送', 'code': 'not_group_turn'}, 409
        if turn.status == 'pending' or any(row.status in ('queued', 'running') for row in messages):
            return {'message': '回合还在进行中', 'code': 'turn_running'}, 409
        failed = [row for row in messages if row.status == 'failed']
        if not failed:
            return {'message': '没有需要重试的消息', 'code': 'nothing_to_retry'}, 409
        now = utc_now()
        for row in failed:
            row.status, row.error, row.text, row.blocks, row.sources = 'queued', None, '', None, '[]'
            row.started_at = row.finished_at = None
            row.version += 1
        turn.status, turn.pending_at = 'pending', now  # 等待过期与整轮时限从这次重试算起
        jobs.requeue(s, TURN_KIND, {'turn_id': turn_id}, job_key(turn_id), now=now)
        payload = turn_public(s, turn)
    ws.push_turn(user['id'], pet_id, payload)
    jobs.wake()
    return payload, 202


# ---------------------------------------------------------------------------
# POST 接受回执
# ---------------------------------------------------------------------------

def accept(session, turn):
    """POST 已通过校验并落了回合行：作业与会话同事务入队，返回带消息视图的回合快照。"""
    jobs.enqueue(session, TURN_KIND, {'turn_id': turn.id}, dedup_key=job_key(turn.id))
    return turn_public(session, turn)


def accepted(turn_payload, *, settled=False):
    """新协议的 POST 回执：声明协议、表示已受理（settled 为幂等重发时回合已结束）。"""
    return {'turn': turn_payload, 'protocol': PROTOCOL, 'accepted': True}, (200 if settled else 202)
