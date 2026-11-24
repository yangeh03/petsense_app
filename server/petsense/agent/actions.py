"""W10 动作工具服务（agent-w10-spec.md §2–§4）：远程录音、运动日记、私有社区草稿。

三个动作共用 AgentAction 回执（稳定动作 ID + 结构化结果，W10-A1 可追溯）与 W03 提案
确认门：录音与日记生成只走「已确认提案」路径，草稿明确指令可直写（D6）。
录音按 D3 默认关停：PETSENSE_AGENT_RECORDING_ENABLED=1 才开放（生产真项圈门槛通过前保持
关闭，其他两个动作独立可用）。

录音协议 psrec01（D4）：命令走 petsense/device/<id>/command，回传走设备作用域 topic
petsense/sensor/<id>/agent_audio，请求与回传携带同一高熵 requestId；服务端在读文件、
落库前验证期望设备、宠物归属、请求有效性与去重，旧 PSWAV01 共享 topic 链路不承载
Agent 音频，WAV 存独立目录不经 /audio/ 公开（spec §3）。

执行会话注意：本模块的执行入口（execute_confirmed 等）在事务外调用、自开 session_scope；
latest_telemetry 等自开会话的能力不得嵌套在外层 session_scope 内（W02 教训）。
"""
import base64
import hashlib
import json
import logging
import os
import secrets
import struct
import threading
from pathlib import Path

from sqlalchemy import select, update

from petsense import config
from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models import Device, Pet
from petsense.db.models.agent import AgentAction, AgentProposal, AgentTurn

logger = logging.getLogger(__name__)

KIND_RECORD = 'record_audio'
KIND_DIARY = 'create_diary'
KIND_DRAFT = 'social_draft'
CONFIRM_KINDS = (KIND_RECORD, KIND_DIARY, KIND_DRAFT)
PROTO = 'psrec01'

MIN_DURATION_S = 1
MAX_DURATION_S = 10
MAX_WAV_BYTES = 44 + 32000 * MAX_DURATION_S + 2048   # 16 kHz 16 bit 单声道 10 秒 + 头 + 余量
MAX_CHUNKS = 400
MAX_CHUNK_BYTES = 8192
UPLOAD_GRACE_S = 60.0                                # 录音时长之外允许的回传窗口
AUDIO_TOPIC_SUFFIX = 'agent_audio'

# 协议声明的音频格式（psrec01 固定 mono 16 bit 16 kHz，其他一律拒收）
AUDIO_SAMPLE_RATE = 16000
AUDIO_BITS = 16
AUDIO_CHANNELS = 1

RECORDING_ENABLED_ENV = 'PETSENSE_AGENT_RECORDING_ENABLED'

# 可被状态迁移改写的非终态：终态动作不再被迟到帧或超时覆盖
MUTABLE_STATUSES = ('executing', 'unknown')

# 录音回传重组缓冲：token -> 期望/已收分片（MQTT 线程内串行访问）
_audio_buffers = {}
# 单动作完成锁：同一动作的收尾只跑一次，避免超时与音频到达交叉留下孤儿文件
_finish_locks = {}
_finish_locks_guard = threading.Lock()
# 录音超时定时器：action_id -> Timer（单进程假设，同 commands.py 模式）
_recording_timers = {}
_recording_timers_lock = threading.Lock()

# 设备协议能力（固件在遥测 envelope 上报 agentAudio 字段，mqtt_ingest 记入）
_device_caps = {}


def recording_enabled():
    """D3 开关：默认关闭，显式置 1 才开放（与 W06 搜索开关方向相反：录音从严）。"""
    return os.environ.get(RECORDING_ENABLED_ENV, '') == '1'


# ---------------------------------------------------------------------------
# 序列化与确认卡回执
# ---------------------------------------------------------------------------

_RECORD_TEXT = {
    'executing': '已向项圈发送录音指令，等待音频回传…',
    'completed': '项圈录音完成，可点击播放',
    'failed': '项圈录音未完成',
    'unknown': '录音指令结果未知，请等待原结果后再重新发起',
}
_DIARY_TEXT = {
    'executing': '运动日记生成中，完成后可在「今日记录」查看',
    'completed': '运动日记已生成，可打开详情查看海报',
    'failed': '运动日记未生成',
    'unknown': '日记任务结果未知，请稍后在「今日记录」查看',
}
_DRAFT_TEXT = {
    'completed': '社区动态草稿已保存（仅自己可见），可到发布页查看',
    'executing': '正在保存社区草稿…',
    'failed': '社区草稿未保存',
    'unknown': '社区草稿保存结果未知',
}


def action_public(row):
    """动作回执序列化：text 为旧客户端可读降级（P00 §2 未知块安全保留）。"""
    result = json.loads(row.result_json or '{}')
    payload = {'id': row.id, 'kind': row.kind, 'status': row.status,
               'createdAt': iso_z(row.created_at), 'updatedAt': iso_z(row.updated_at)}
    if row.kind == KIND_RECORD:
        payload['durationSec'] = row.duration_sec
        payload['deviceId'] = row.device_id
        payload['deviceName'] = (json.loads(row.input_json or '{}') or {}).get('deviceName')
        payload['commandSent'] = result.get('commandSent')
        payload['audioReceived'] = result.get('audioReceived')
        payload['audio'] = result.get('audio')
        if row.status == 'completed':
            payload['audioUrl'] = f'/api/app/agent/recordings/{row.id}/audio'
    elif row.kind == KIND_DIARY:
        payload['job'] = result.get('job')
    elif row.kind == KIND_DRAFT:
        payload['draft'] = result.get('draft')
    if row.error:
        payload['error'] = row.error
    table = {KIND_RECORD: _RECORD_TEXT, KIND_DIARY: _DIARY_TEXT, KIND_DRAFT: _DRAFT_TEXT}.get(row.kind, {})
    payload['text'] = table.get(row.status, row.status)
    if row.error and row.status in ('failed', 'unknown'):
        payload['text'] = f"{payload['text']}：{row.error}"
    return payload


def _upsert_action_block(session, action_id, action_payload, proposal_id=None, turn_id=None):
    """把动作回执写回提案所属回合 blocks（持久结果为真，spec §2）。

    直写动作（无提案）由调用方传 turn_id；确认类动作经 proposal 定位回合。
    确认后原候选卡被动作卡取代（同一位置只留一个可交互卡），避免旧卡与真实回执并存。
    """
    if turn_id is None and proposal_id:
        proposal = session.get(AgentProposal, proposal_id)
        turn_id = proposal.turn_id if proposal else None
    if turn_id is None:
        return None
    turn = session.get(AgentTurn, turn_id)
    if turn is None:
        return None
    blocks = json.loads(turn.blocks) if turn.blocks else []
    block = {'type': 'action', 'action': action_payload}
    replaced = False
    merged = []
    for existing in blocks:
        if not isinstance(existing, dict):
            merged.append(existing)
            continue
        if existing.get('type') == 'action' and isinstance(existing.get('action'), dict) \
                and existing['action'].get('id') == action_id:
            merged.append(block)
            replaced = True
            continue
        if proposal_id and existing.get('type') == 'proposal' \
                and isinstance(existing.get('proposal'), dict) \
                and existing['proposal'].get('id') == proposal_id:
            merged.append(block)
            replaced = True
            continue
        merged.append(existing)
    if not replaced:
        merged.append(block)
    turn.blocks = json.dumps(merged, ensure_ascii=False)
    from petsense.agent import group
    group.sync_blocks(session, turn)  # 群聊回合：回执卡同步到发出它的那条照料助手消息
    return turn_id


def _publish_turn(user_id, pet_id, turn_id):
    """重写 blocks 后推送 turn 帧（无 WS 客户端时静默，拉取以持久结果为准）。"""
    if turn_id is None:
        return
    from petsense.agent import ws
    with session_scope() as s:
        turn = s.get(AgentTurn, turn_id)
        if turn is None or turn.pet_id != pet_id:
            return
        from petsense.agent import group
        ws.push_turn(user_id, pet_id, group.turn_public(s, turn))


def _transition(action_id, *, from_statuses, status=None, error=None, result_updates=None):
    """原子状态迁移：只在当前状态属于 from_statuses 时改写，迟到帧与超时不会互相覆盖。

    返回 (是否迁移, 新回执)；迁移失败时不动状态、不写块（终态动作不被复活）。
    """
    with session_scope() as s:
        row = s.get(AgentAction, action_id)
        if row is None:
            return False, None
        result = json.loads(row.result_json or '{}')
        if result_updates:
            result.update(result_updates)
        values = {'result_json': json.dumps(result, ensure_ascii=False), 'updated_at': utc_now()}
        if status is not None:
            values['status'] = status
        if error is not None:
            values['error'] = error
        if status in ('completed', 'failed', 'unknown', 'dismissed'):
            values['finished_at'] = utc_now()
        moved = s.execute(update(AgentAction).where(
            AgentAction.id == action_id, AgentAction.status.in_(from_statuses)
        ).values(**values)).rowcount
        if not moved:
            return False, None
        s.expire_all()
        row = s.get(AgentAction, action_id)
        payload = action_public(row)
        turn_id = _upsert_action_block(s, row.id, payload, row.proposal_id)
        pet_id, user_id = row.pet_id, row.user_id
        if row.proposal_id:
            _refresh_proposal_block(s, row.proposal_id)
    _publish_turn(user_id, pet_id, turn_id)
    return True, payload


def _update_action(action_id, *, status=None, error=None, result_updates=None):
    """更新回执行、重写 blocks 并推帧；返回 action_public。"""
    _, payload = _transition(action_id, from_statuses=MUTABLE_STATUSES, status=status,
                             error=error, result_updates=result_updates)
    return payload


def _refresh_proposal_block(session, proposal_id):
    """决策后同步提案卡状态（复用 W03 的 blocks 重写语义）。"""
    from petsense.agent import proposals as proposal_service
    row = session.get(AgentProposal, proposal_id)
    if row is not None:
        proposal_service._refresh_turn_blocks(session, row)


# ---------------------------------------------------------------------------
# 设备能力与在线（供确认时复核）
# ---------------------------------------------------------------------------

def note_device_caps(device_id, payload):
    """记录固件在遥测 envelope 上报的 Agent 录音协议能力（mqtt_ingest 每帧调用）。"""
    if not device_id or not isinstance(payload, dict):
        return
    proto = payload.get('agentAudio')
    if isinstance(proto, str) and proto:
        _device_caps[device_id] = {'proto': proto[:24], 'at': utc_now()}


def device_capability(device_id, max_age_s=600):
    """设备当前声明的录音协议；超期视为未上报（设备每秒发帧，10 分钟足够宽）。"""
    cap = _device_caps.get(device_id)
    if not cap or (utc_now() - cap['at']).total_seconds() > max_age_s:
        return None
    return cap['proto']


def active_recording(device_id):
    """设备上是否有未终态的 Agent 录音（单设备互斥，spec §3）。"""
    if not device_id:
        return False
    with session_scope() as s:
        return s.execute(select(AgentAction.id).where(
            AgentAction.kind == KIND_RECORD, AgentAction.device_id == device_id,
            AgentAction.status.in_(('executing', 'unknown'))).limit(1)).scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# 工具执行器（registry 调用；只建提案/直写草稿，不执行录音与日记生成）
# ---------------------------------------------------------------------------

def _pet_of(session, context):
    pet = session.get(Pet, context['pet_id'])
    if pet is None or pet.user_id != context['user_id']:
        raise ValueError('宠物归属已变化')
    return pet


def _device_for(session, pet):
    if not pet.device_id:
        return None
    device = session.get(Device, pet.device_id)
    if device is None or device.owner_id != pet.user_id:
        return None
    return device


def prepare_recording(args, context):
    """control_device(record_audio)：校验时长与设备绑定，生成待确认提案（不发送指令）。"""
    duration = args.get('durationSec')
    if isinstance(duration, bool) or not isinstance(duration, int) \
            or not MIN_DURATION_S <= duration <= MAX_DURATION_S:
        return {'error': f'录音时长需为 {MIN_DURATION_S}—{MAX_DURATION_S} 的整数秒'}
    with session_scope() as s:
        pet = _pet_of(s, context)
        device = _device_for(s, pet)
        if device is None:
            return {'error': '本宠还没有绑定项圈，无法远程录音'}
        row = _create_proposal(s, pet, context.get('turn_id'), KIND_RECORD,
                               {'durationSec': duration, 'deviceId': device.device_id,
                                'deviceName': device.name or device.device_id})
    return {'written': False, 'proposal': row,
            'guidance': '已生成录音确认卡（含目标项圈与时长）。未经主人确认前不得声称已发送指令'
                        '或已录音；确认后以动作卡状态为准，指令发送与音频回传分开显示。'}


def _diary_view(pet_id, user_id):
    from petsense.petgif import daily_diary
    payload, _status = daily_diary.read_daily(user_id, pet_id)
    return payload


def prepare_diary(args, context):
    """create_diary：read 直接返回现状；generate 预检后生成待确认提案（D1）。"""
    op = args.get('op')
    if op not in ('read', 'generate'):
        return {'error': 'op 需为 read（查看今天已有日记）或 generate（生成/重新生成）'}
    with session_scope() as s:
        pet = _pet_of(s, context)
    if op == 'read':
        return _diary_view(context['user_id'], context['pet_id'])
    from petsense.petgif import daily_diary
    precheck = daily_diary.precheck_generate(context['user_id'], context['pet_id'])
    if precheck is not None:   # (message, status) 缺口如实返回，不建提案
        return {'error': precheck[0]}
    snapshot = daily_diary.today_snapshot(context['user_id'], context['pet_id'])
    fields = snapshot['proposal_fields']
    if fields['steps'] is None or fields['walkingSeconds'] < 60:
        return {'error': '今天的有效走动记录不足 1 分钟'}
    with session_scope() as s:
        pet = _pet_of(s, context)
        row = _create_proposal(s, pet, context.get('turn_id'), KIND_DIARY, snapshot['proposal_fields'])
    return {'written': False, 'proposal': row,
            'guidance': '已生成运动日记确认卡：会使用宠物档案照片并交由 PetGIF 生成'
                        '（海报链接可被公开访问）。未经主人确认不得声称已生成；'
                        '步数与走动时长只能引用确认卡上的估算值，不得虚构数据来源。'}


def _draft_intent_key(user_id, pet_id, turn_id, caption, location):
    """授权意图键：账号+宠物+回合+正文+地点；同一意图只对应一条草稿行（幂等）。"""
    raw = f'{user_id}|{pet_id}|{turn_id or 0}|{KIND_DRAFT}|{caption}|{location or ""}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def save_draft(args, context, explicit):
    """draft_social_post：明确指令直写（D6）；模糊意图走提案。

    explicit 只决定「直接保存」还是「先出确认卡」，不授予任何发布权限：
    两条路径都只写私有草稿对象，不触碰社区发布表，发布始终由用户在发布页手动完成。
    """
    caption = args.get('caption')
    location = args.get('location')
    if not isinstance(caption, str) or not 1 <= len(caption.strip()) <= 500:
        return {'error': '草稿文字需为 1—500 字'}
    if location is not None and (not isinstance(location, str) or len(location) > 80):
        return {'error': '地点最多 80 字'}
    caption = caption.strip()
    location = location.strip() if isinstance(location, str) and location.strip() else None
    if explicit:
        payload = _store_draft(context, caption, location)
        return {'saved': True, 'action': payload,
                'guidance': '草稿已保存（仅主人可见）。请如实回显草稿内容；正式发布由主人'
                            '在社区发布页手动完成，不得声称已发布。'}
    with session_scope() as s:
        pet = _pet_of(s, context)
        row = _create_proposal(s, pet, context.get('turn_id'), KIND_DRAFT,
                               {'caption': caption, 'location': location})
    return {'written': False, 'proposal': row,
            'guidance': '已生成草稿确认卡。未经主人确认不得声称已保存草稿。'}


def _store_draft(context, caption, location):
    """写入（或复用）一条私有草稿行：同一授权意图只落一行，重复调用不产生第二份。"""
    intent_key = _draft_intent_key(context['user_id'], context['pet_id'],
                                   context.get('turn_id'), caption, location)
    with session_scope() as s:
        pet = _pet_of(s, context)
        row = s.execute(select(AgentAction).where(
            AgentAction.intent_key == intent_key)).scalar_one_or_none()
        if row is None:
            row = AgentAction(pet_id=pet.id, user_id=context['user_id'], kind=KIND_DRAFT,
                              status='completed', intent_key=intent_key,
                              input_json=json.dumps({'caption': caption, 'location': location},
                                                     ensure_ascii=False),
                              result_json=json.dumps(
                                  {'draft': {'caption': caption, 'location': location}},
                                  ensure_ascii=False),
                              created_at=utc_now(), updated_at=utc_now(), finished_at=utc_now())
            s.add(row)
            s.flush()
        payload = action_public(row)
        turn_id = _upsert_action_block(s, row.id, payload, row.proposal_id,
                                       turn_id=context.get('turn_id'))
        pet_id, user_id = pet.id, context['user_id']
    _publish_turn(user_id, pet_id, turn_id)
    return payload


def _create_proposal(session, pet, turn_id, kind, fields):
    """W10 通用提案创建：复用 W03 supersede/版本语义，字段校验在各动作准备器内完成。"""
    from petsense.agent import proposals as proposal_service
    payload = {k: v for k, v in fields.items() if v is not None}
    row = AgentProposal(pet_id=pet.id, turn_id=turn_id, kind=kind,
                        payload=json.dumps(payload, ensure_ascii=False),
                        version=proposal_service._next_version(session, pet.id, kind),
                        status='pending', created_at=utc_now())
    session.add(row)
    session.flush()
    refreshed = []
    for old in session.execute(select(AgentProposal).where(
            AgentProposal.pet_id == pet.id, AgentProposal.kind == kind,
            AgentProposal.status == 'pending', AgentProposal.id != row.id)).scalars():
        old.status = 'superseded'
        old.superseded_by = row.id
        proposal_service._refresh_turn_blocks(session, old)
        if old.turn_id and old.turn_id not in refreshed:
            refreshed.append(old.turn_id)
    session.flush()
    return proposal_service.public(row)


# ---------------------------------------------------------------------------
# 确认后执行（routes 在 decide 提交后调用；自开会话，勿嵌套 session_scope）
# ---------------------------------------------------------------------------

def execute_confirmed(pet_id, proposal_id):
    """确认后的统一执行入口：每次确认恰好产生一条可追溯回执（W10-A1）。

    意图键 `proposal:<id>` 唯一：重复确认（含并发）只对应一条动作行，不会重复提交外部系统。
    返回 (action_payload|None, refreshed_turn_ids)；重复确认/无提案返回 None。
    """
    with session_scope() as s:
        pet = s.get(Pet, pet_id)
        proposal = s.get(AgentProposal, proposal_id)
        if pet is None or proposal is None or proposal.pet_id != pet.id \
                or proposal.kind not in CONFIRM_KINDS or proposal.status != 'confirmed':
            return None, []
        intent_key = f'proposal:{proposal_id}'
        existing = s.execute(select(AgentAction).where(
            AgentAction.intent_key == intent_key).order_by(AgentAction.id.desc())).scalars().first()
        if existing is not None:
            return action_public(existing), []   # 幂等：重复确认不重复执行
        fields = json.loads(proposal.payload)
        kind = proposal.kind
        row = AgentAction(pet_id=pet.id, user_id=pet.user_id, kind=kind,
                          proposal_id=proposal.id, status='executing',
                          intent_key=intent_key,
                          device_id=pet.device_id if kind == KIND_RECORD else None,
                          duration_sec=fields.get('durationSec') if kind == KIND_RECORD else None,
                          input_json=proposal.payload, created_at=utc_now(), updated_at=utc_now())
        if kind == KIND_RECORD:
            row.request_token = secrets.token_urlsafe(24)
        s.add(row)
        s.flush()
        action_id, pet_id, user_id = row.id, row.pet_id, row.user_id
    if kind == KIND_RECORD:
        return _run_recording(action_id, fields), _refreshed(proposal_id)
    if kind == KIND_DIARY:
        return _run_diary(action_id, fields, user_id, pet_id), _refreshed(proposal_id)
    return _run_draft(action_id, fields), _refreshed(proposal_id)


def _refreshed(proposal_id):
    with session_scope() as s:
        proposal = s.get(AgentProposal, proposal_id)
        return [proposal.turn_id] if proposal and proposal.turn_id else []


def _run_recording(action_id, fields):
    """确认后的录音执行：复核全部条件（spec §3），失败如实落回执，无 MQTT 副作用。"""
    from petsense.common import control_debounced
    duration = fields.get('durationSec')
    failure = None
    with session_scope() as s:
        row = s.get(AgentAction, action_id)
        if row is None:
            return None
        token = row.request_token
        pet = s.get(Pet, row.pet_id)
        device_id = row.device_id
        device = _device_for(s, pet)
        if not recording_enabled():
            failure = '录音能力未开放'
        elif isinstance(duration, bool) or not isinstance(duration, int) \
                or not MIN_DURATION_S <= duration <= MAX_DURATION_S:
            failure = '录音时长超出 1—10 秒范围'
        elif device is None or pet.device_id != device_id:
            failure = '项圈绑定已变化，请重新确认'
    if failure is not None:
        return _update_action(action_id, status='failed', error=failure)
    from petsense.telemetry.commands import audio_recording_states
    if audio_recording_states.get(device_id, {}).get('enabled'):
        return _update_action(action_id, status='failed', error='项圈正在旧入口录音，请稍后再试')
    if active_recording(device_id) and not _is_only(action_id, device_id):
        return _update_action(action_id, status='failed', error='项圈有未完成的录音，请稍后再试')
    if device_capability(device_id) != PROTO:
        return _update_action(action_id, status='failed',
                              error='项圈固件不支持远程录音（需升级到支持 psrec01 的固件）')
    latest = _device_online(device_id)
    if latest is None or not latest.get('online'):
        return _update_action(action_id, status='failed', error='项圈当前离线')
    if control_debounced(f'agent-audio:{device_id}:{token}'):
        return _update_action(action_id, status='failed', error='指令重复发送被防抖拦截，请重新确认')
    from petsense.telemetry.mqtt_ingest import publish_control_command
    command = {'type': 'agent_record_audio', 'proto': PROTO,
               'requestId': token, 'durationSec': duration, 'ts': iso_z(utc_now())}
    try:
        _, published = publish_control_command(device_id, command)
    except Exception as exc:  # 发布异常按未知处理（spec §2：外部提交结果不明确）
        logger.warning('agent recording publish error: %s', exc)
        published = None
    if published is None or published is False:
        # 结果不明确/未送达：进入 unknown 等待原结果，禁止自动重试，再次发起需新确认（spec §2）
        _clear_audio_buffer(token)
        payload = _update_action(action_id, status='unknown',
                                 error='录音指令发送结果未知，等待原结果；如需重试请再次确认新候选',
                                 result_updates={'commandSent': False if published is False else None})
        # unknown 也必须有到期边界：等待窗口结束仍未收到音频即收敛为失败，不长期悬置
        _schedule_recording_timeout(action_id, duration)
        return payload
    _update_action(action_id, result_updates={'commandSent': True})
    _schedule_recording_timeout(action_id, duration)
    return _public_after(action_id)


def _is_only(action_id, device_id):
    with session_scope() as s:
        rows = s.execute(select(AgentAction.id).where(
            AgentAction.kind == KIND_RECORD, AgentAction.device_id == device_id,
            AgentAction.status.in_(('executing', 'unknown')))).scalars().all()
    return rows == [action_id]


def _device_online(device_id):
    from petsense.telemetry.status import latest_telemetry
    return latest_telemetry(device_id)


def _public_after(action_id):
    with session_scope() as s:
        row = s.get(AgentAction, action_id)
        return action_public(row) if row else None


def _run_diary(action_id, fields, user_id, pet_id):
    """确认后的日记执行：核对日期/档案照/目标任务后走共用提交入口（spec §4）。"""
    from petsense.petgif import daily_diary
    stale = daily_diary.verify_generate_context(user_id, pet_id, fields)
    if stale:
        return _update_action(action_id, status='failed', error=stale)
    payload, status = daily_diary.submit_daily_ai(user_id, pet_id, fields.get('replaceJobId'))
    job = {'jobId': payload.get('jobId'), 'status': payload.get('status'),
           'posterUrl': payload.get('posterUrl'), 'message': payload.get('message'),
           'date': payload.get('date')}
    if status >= 400:
        return _update_action(action_id, status='failed',
                              error=payload.get('message') or '日记任务未能发起',
                              result_updates={'job': job})
    if payload.get('status') == 'completed':
        return _update_action(action_id, status='completed', result_updates={'job': job})
    return _update_action(action_id, status='executing', result_updates={'job': job})


def _run_draft(action_id, fields):
    result = {'draft': {'caption': fields.get('caption'), 'location': fields.get('location')}}
    return _update_action(action_id, status='completed', result_updates=result)


# ---------------------------------------------------------------------------
# 录音超时与恢复
# ---------------------------------------------------------------------------

def _schedule_recording_timeout(action_id, duration_sec):
    timer = threading.Timer(duration_sec + UPLOAD_GRACE_S, _on_recording_timeout, args=(action_id,))
    timer.daemon = True
    with _recording_timers_lock:
        old = _recording_timers.pop(action_id, None)
        if old:
            old.cancel()
        _recording_timers[action_id] = timer
    timer.start()


def _cancel_recording_timeout(action_id):
    with _recording_timers_lock:
        timer = _recording_timers.pop(action_id, None)
    if timer:
        timer.cancel()


def _on_recording_timeout(action_id):
    """录音到期：executing/unknown 收敛为失败；已是终态的动作不再被覆盖。"""
    _cancel_recording_timeout(action_id)
    with session_scope() as s:
        row = s.get(AgentAction, action_id)
        if row is None or row.kind != KIND_RECORD:
            return
        token = row.request_token
        waiting = row.status == 'unknown'
    _clear_audio_buffer(token)
    error = '等待录音结果超时，未收到音频' if waiting else '录音超时未收到完整音频'
    _update_action(action_id, status='failed', error=error,
                   result_updates={'audioReceived': False})


def recover_stale_actions():
    """启动恢复：重启遗留的 executing/unknown 录音按超时失败终态化（日记交给完成钩子核对）。"""
    cutoff = utc_now().timestamp() - 600
    with session_scope() as s:
        rows = s.execute(select(AgentAction).where(
            AgentAction.kind == KIND_RECORD, AgentAction.status.in_(('executing', 'unknown')))).scalars().all()
        stale = [row.id for row in rows
                 if row.created_at.timestamp() < cutoff]
    for action_id in stale:
        _update_action(action_id, status='failed', error='服务重启导致录音中断，请重新发起')
    _recover_diary_actions()


def _recover_diary_actions():
    """服务重启后按任务真实状态收敛日记回执（unknown 任务保持等待原结果）。"""
    from petsense.db.models import PetgifDiaryJob
    from petsense.petgif import diary_jobs
    with session_scope() as s:
        rows = s.execute(select(AgentAction).where(
            AgentAction.kind == KIND_DIARY, AgentAction.status == 'executing')).scalars().all()
        pending = []
        for row in rows:
            result = json.loads(row.result_json or '{}')
            job_id = (result.get('job') or {}).get('jobId')
            if job_id:
                pending.append((row.id, job_id))
    for action_id, job_id in pending:
        with session_scope() as s:
            job = s.get(PetgifDiaryJob, str(job_id))
        if job is not None:
            _apply_diary_job_state(action_id, diary_jobs.job_dict(job))


# ---------------------------------------------------------------------------
# 日记完成钩子（diary_jobs 终态回调注册）
# ---------------------------------------------------------------------------

def on_diary_job_terminal(job_id):
    """日记任务到终态时按 jobId 找回执并收敛状态（completed 才呈现结果，spec §2/§4）。

    会话纪律（W02 教训）：_apply_diary_job_state → _update_action 自开 session_scope，
    必须在其外调用，嵌套会话会关闭外层事务。
    """
    from petsense.db.models import PetgifDiaryJob
    from petsense.petgif import diary_jobs
    with session_scope() as s:
        rows = s.execute(select(AgentAction).where(
            AgentAction.kind == KIND_DIARY, AgentAction.status == 'executing')).scalars().all()
        target = None
        for row in rows:
            result = json.loads(row.result_json or '{}')
            if (result.get('job') or {}).get('jobId') == str(job_id):
                target = row.id
                break
    if target is None:
        return
    with session_scope() as s:
        job = s.get(PetgifDiaryJob, str(job_id))
        if job is None:
            return
        payload = diary_jobs.job_dict(job)
    _apply_diary_job_state(target, payload)


def _apply_diary_job_state(action_id, job_payload):
    status = job_payload.get('status')
    job = {'jobId': job_payload.get('jobId'), 'status': status,
           'posterUrl': job_payload.get('posterUrl'), 'message': job_payload.get('message'),
           'date': job_payload.get('date')}
    if status == 'completed':
        _update_action(action_id, status='completed', result_updates={'job': job})
    elif status == 'failed':
        _update_action(action_id, status='failed', error=job_payload.get('message') or '日记生成失败',
                       result_updates={'job': job})
    elif status == 'unknown':
        _update_action(action_id, status='unknown', error=job_payload.get('message') or '任务结果未知',
                       result_updates={'job': job})


# ---------------------------------------------------------------------------
# MQTT 回传接入（mqtt_ingest 调用；设备作用域 topic + 高熵凭据）
# ---------------------------------------------------------------------------

def _clear_audio_buffer(token):
    _audio_buffers.pop(token, None)


def ingest_agent_audio(device_id, payload):
    """psrec01 回传帧处理：归属/有效性/去重全部先于读文件与落库（spec §3）。

    unknown（发布结果不明确）同样接受后续有效回传：等待原结果是契约的一部分，
    但仍要求凭据、设备与宠物归属一致且未过期，绝不自动重发指令。
    返回 True 表示该帧已被接受（可落审计行）。
    """
    if not isinstance(payload, dict):
        return False
    token = payload.get('requestId')
    frame = payload.get('type')
    if not isinstance(token, str) or not 8 <= len(token) <= 64:
        return False
    proto = payload.get('proto')
    if proto is not None and proto != PROTO:
        return False
    if frame not in ('agent_audio_status', 'agent_audio_start',
                     'agent_audio_chunk', 'agent_audio_end'):
        return False
    binding_broken = False
    with session_scope() as s:
        row = s.execute(select(AgentAction).where(
            AgentAction.request_token == token)).scalar_one_or_none()
        if row is None or row.kind != KIND_RECORD:
            return False
        action_id, pet_id, user_id = row.id, row.pet_id, row.user_id
        if row.device_id != device_id or row.status not in MUTABLE_STATUSES:
            return False  # 错设备 / 已终态（过期、重放）按相应状态丢弃，不串到下一请求
        pet = s.get(Pet, pet_id)
        device = s.get(Device, device_id) if device_id else None
        if pet is None or pet.user_id != user_id or pet.device_id != device_id \
                or device is None or device.owner_id != user_id:
            binding_broken = True
    if binding_broken:
        # 会话纪律：_update_action 自开事务，必须在 session_scope 外调用（W02 教训）
        _update_action(action_id, status='failed', error='设备归属已变化，音频已拒绝',
                       result_updates={'audioReceived': False})
        return False
    if frame == 'agent_audio_status':
        _handle_status_frame(action_id, payload)
        return True
    if frame == 'agent_audio_start':
        _handle_start_frame(action_id, token, payload)
    elif frame == 'agent_audio_chunk':
        _handle_chunk_frame(action_id, token, payload)
    elif frame == 'agent_audio_end':
        _handle_end_frame(action_id, token)
    return True


def _handle_status_frame(action_id, payload):
    status = payload.get('status')
    if status == 'started':
        return
    if status in ('busy', 'rejected', 'invalid'):
        _update_action(action_id, status='failed',
                       error={'busy': '项圈正在录音，请稍后再试',
                              'rejected': payload.get('reason') or '项圈拒绝本次录音',
                              'invalid': '项圈无法解析录音指令'}.get(status, '项圈录音失败'),
                       result_updates={'commandSent': True, 'audioReceived': False})


def _handle_start_frame(action_id, token, payload):
    try:
        total = int(payload.get('size') or 0)
        chunks = int(payload.get('chunks') or 0)
        sample_rate = int(payload.get('sampleRate') or 0)
        bits = int(payload.get('bits') or 0)
        channels = int(payload.get('channels') or 0)
        requested_ms = int(payload.get('requestedMs') or 0)
    except (TypeError, ValueError):
        return
    if not 44 <= total <= MAX_WAV_BYTES or not 1 <= chunks <= MAX_CHUNKS \
            or not 1 <= sample_rate <= 48000 or bits not in (8, 16, 24, 32) \
            or not 1 <= channels <= 2:
        _update_action(action_id, status='failed', error='回传音频参数无效',
                       result_updates={'audioReceived': False})
        _clear_audio_buffer(token)
        return
    if (sample_rate, bits, channels) != (AUDIO_SAMPLE_RATE, AUDIO_BITS, AUDIO_CHANNELS):
        # 协议声明固定 mono 16 bit 16 kHz：其他格式按无效处理，不做隐式转换
        _update_action(action_id, status='failed',
                       error=f'回传音频格式不符（需 {AUDIO_SAMPLE_RATE} Hz {AUDIO_BITS} bit '
                             f'单声道）',
                       result_updates={'audioReceived': False})
        _clear_audio_buffer(token)
        return
    with session_scope() as s:
        row = s.get(AgentAction, action_id)
        expected_ms = (row.duration_sec or 0) * 1000 if row else 0
    if expected_ms and requested_ms and abs(requested_ms - expected_ms) > 1000:
        # 设备声明的请求时长与本动作不一致：不是本次请求的回传
        _update_action(action_id, status='failed', error='回传音频与本次请求不匹配',
                       result_updates={'audioReceived': False})
        _clear_audio_buffer(token)
        return
    _audio_buffers[token] = {'action_id': action_id, 'total': total, 'chunks': chunks,
                             'sample_rate': sample_rate, 'bits': bits, 'channels': channels,
                             'received': {}, 'bytes': 0, 'started_at': utc_now()}


def _handle_chunk_frame(action_id, token, payload):
    buffer = _audio_buffers.get(token)
    if buffer is None or buffer.get('action_id') != action_id:
        return
    try:
        index = int(payload.get('chunkIndex'))
        data = base64.b64decode(payload.get('data') or '', validate=False)
    except (TypeError, ValueError):
        return
    if not 0 <= index < buffer['chunks'] or not data or len(data) > MAX_CHUNK_BYTES:
        return
    if index in buffer['received']:   # QoS1 重复投递静默去重
        return
    buffer['received'][index] = data
    buffer['bytes'] += len(data)
    if buffer['bytes'] > buffer['total'] + 1024:
        _update_action(action_id, status='failed', error='回传音频超出声明大小',
                       result_updates={'audioReceived': False})
        _clear_audio_buffer(token)
        return
    if len(buffer['received']) >= buffer['chunks']:
        _finish_recording(action_id, token, buffer)


def _handle_end_frame(action_id, token):
    buffer = _audio_buffers.get(token)
    if buffer is None or buffer.get('action_id') != action_id:
        return
    if len(buffer['received']) < buffer['chunks']:
        _update_action(action_id, status='failed',
                       error=f"音频分片不完整（{len(buffer['received'])}/{buffer['chunks']}）",
                       result_updates={'audioReceived': False})
    _clear_audio_buffer(token)


def _parse_wav(wav):
    """解析 WAV 头：返回 (rate, channels, bits, data_bytes, data_offset) 或 None。"""
    if len(wav) < 44 or wav[0:4] != b'RIFF' or wav[8:12] != b'WAVE':
        return None
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(wav):
        chunk_id = wav[pos:pos + 4]
        chunk_size = struct.unpack('<I', wav[pos + 4:pos + 8])[0]
        start = pos + 8
        if chunk_id == b'fmt ' and start + 16 <= len(wav):
            channels, rate = struct.unpack('<HI', wav[start + 2:start + 8])
            bits = struct.unpack('<H', wav[start + 14:start + 16])[0]
            fmt = (rate, channels, bits)
        elif chunk_id == b'data':
            data = (chunk_size, start)
            break
        pos = start + chunk_size + (chunk_size & 1)
    if fmt is None or data is None:
        return None
    return fmt[0], fmt[1], fmt[2], data[0], data[1]


def _finish_lock(action_id):
    with _finish_locks_guard:
        lock = _finish_locks.get(action_id)
        if lock is None:
            lock = threading.Lock()
            _finish_locks[action_id] = lock
        return lock


def _finish_recording(action_id, token, buffer):
    """分片齐备后的收尾：格式/字节数/时长核对 → 授权复核 → 落盘 → 原子提交完成。

    超时与音频到达交叉时以原子迁移为准：迁移失败即删除本次落盘的文件，
    不留下「已暴露但无回执」的孤儿音频；已完成的动作不会被迟到帧改写。
    """
    with _finish_lock(action_id):
        wav = b''.join(buffer['received'][i] for i in range(buffer['chunks']))
        _clear_audio_buffer(token)
        if len(wav) != buffer['total']:
            return _update_action(action_id, status='failed',
                                  error='回传音频字节数与声明不一致',
                                  result_updates={'audioReceived': False})
        parsed = _parse_wav(wav)
        if parsed is None:
            return _update_action(action_id, status='failed', error='回传音频不是有效 WAV',
                                  result_updates={'audioReceived': False})
        rate, channels, bits, data_bytes, data_offset = parsed
        if (rate, channels, bits) != (AUDIO_SAMPLE_RATE, AUDIO_CHANNELS, AUDIO_BITS):
            return _update_action(action_id, status='failed',
                                  error=f'回传音频格式不符（需 {AUDIO_SAMPLE_RATE} Hz '
                                        f'{AUDIO_BITS} bit 单声道）',
                                  result_updates={'audioReceived': False})
        block_align = channels * bits // 8
        if data_bytes % block_align or data_offset + data_bytes != len(wav):
            return _update_action(action_id, status='failed', error='回传音频数据不完整',
                                  result_updates={'audioReceived': False})
        byte_rate = rate * block_align
        computed_ms = data_bytes * 1000 // byte_rate if byte_rate else 0
        with session_scope() as s:
            row = s.get(AgentAction, action_id)
            if row is None or row.status not in MUTABLE_STATUSES:
                return None
            requested_ms = (row.duration_sec or 0) * 1000
            pet = s.get(Pet, row.pet_id)
            device = s.get(Device, row.device_id) if row.device_id else None
            # 终态提交前复核授权：宠物与设备绑定在录音期间被改动则一律拒收
            authorized = (pet is not None and pet.user_id == row.user_id
                          and pet.device_id == row.device_id
                          and device is not None and device.owner_id == row.user_id)
        if not authorized:
            return _update_action(action_id, status='failed', error='设备归属已变化，音频已拒绝',
                                  result_updates={'audioReceived': False})
        if computed_ms + 400 < requested_ms:
            return _update_action(action_id, status='failed',
                                  error=f'实际录音约 {computed_ms / 1000:.1f} 秒，'
                                        f'短于请求的 {requested_ms // 1000} 秒',
                                  result_updates={'audioReceived': False})
        if computed_ms > requested_ms + 2000:
            return _update_action(action_id, status='failed', error='回传音频时长超出请求范围',
                                  result_updates={'audioReceived': False})
        directory = Path(config.RUNTIME_ROOT) / 'agent-recordings'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{action_id}.wav'
        temporary = path.with_suffix('.tmp')
        try:
            with open(temporary, 'wb') as handle:
                handle.write(wav)
            temporary.replace(path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            logger.warning('agent recording write failed: %s', exc)
            return _update_action(action_id, status='failed', error='录音文件写入失败',
                                  result_updates={'audioReceived': False})
        audio = {'durationMs': computed_ms, 'sampleRate': rate, 'bits': bits,
                 'channels': channels, 'bytes': len(wav)}
        _cancel_recording_timeout(action_id)
        moved, _ = _transition(action_id, from_statuses=MUTABLE_STATUSES, status='completed',
                               result_updates={'audioReceived': True, 'audio': audio})
        if not moved:
            path.unlink(missing_ok=True)   # 终态已定：本次落盘作废，不留孤儿音频
            return None
    return _public_after(action_id)


def recording_audio_path(user_id, action_id):
    """鉴权读取前的归属校验：验证账号、宠物、设备绑定与动作引用（spec §3）。"""
    with session_scope() as s:
        row = s.get(AgentAction, action_id)
        if row is None or row.kind != KIND_RECORD or row.status != 'completed' \
                or row.user_id != user_id:
            return None
        pet = s.get(Pet, row.pet_id)
        if pet is None or pet.user_id != user_id:
            return None
        if row.device_id:
            device = s.get(Device, row.device_id)
            if device is None or device.owner_id != user_id or pet.device_id != row.device_id:
                return None
    path = Path(config.RUNTIME_ROOT) / 'agent-recordings' / f'{action_id}.wav'
    return path if path.is_file() else None


# 日记终态钩子注册（导入即生效；diary_jobs 不反向依赖 agent，避免环）
def _register_diary_hook():
    from petsense.petgif import diary_jobs
    diary_jobs.add_terminal_hook(on_diary_job_terminal)


_register_diary_hook()
