"""W03 确认提案服务：确认门的唯一实现（agent-w03-spec.md §3.3），W08/W10 按 kind 复用。

明确指令直写（explicit）+ 模糊候选确认卡（pending → confirm/reject）两条生效路径；
结构校验为落库前复核第二层（模型结构化提案 + 业务复核，P00 §4）。
决策幂等：终态同 action 重复返回当前态；superseded / 异 action → 409。
"""
import json
from datetime import date

from sqlalchemy import func, select

from petsense.db import iso_z, utc_now
from petsense.db.models import PetWeightRecord
from petsense.db.models.agent import AgentProposal, AgentTurn
from petsense.pets import profile as pet_profile

KIND_PROFILE = 'profile_update'
PROPOSAL_FIELDS = ('name', 'breed', 'birthDate', 'birthDatePrecision', 'birthYear',
                   'weightKg', 'weightMeasuredOn', 'weightRawText')


class ProposalError(Exception):
    """结构化错误：status 为 HTTP 状态码，message 面向模型/客户端如实转述。"""

    def __init__(self, message, status=400, errors=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.errors = errors or {}


def public(row):
    """提案序列化：payload 为归一化候选字段，卡片据此渲染（历史回合无需额外请求）。"""
    return {'id': row.id, 'kind': row.kind, 'status': row.status, 'version': row.version,
            'fields': json.loads(row.payload), 'createdAt': iso_z(row.created_at),
            'decidedAt': iso_z(row.decided_at) if row.decided_at else None}


def normalize_fields(fields):
    """候选字段归一化与结构校验（落库前复核）；通过返回归一化 payload，不通过抛 ProposalError。

    输入为模型结构化提案（camelCase）；输出同样为 camelCase 供卡片与回放展示。
    """
    if not isinstance(fields, dict) or not fields:
        raise ProposalError('请提供要保存的档案字段', errors={'fields': '候选字段为空'})
    unknown = set(fields) - set(PROPOSAL_FIELDS)
    if unknown:
        raise ProposalError(f'不支持的档案字段：{", ".join(sorted(unknown))}',
                            errors={'fields': f'未知字段 {sorted(unknown)}'})
    errors = {}
    payload = {}

    name = fields.get('name')
    if name is not None:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 20:
            errors['name'] = '名字需为 1—20 个字符'
        else:
            payload['name'] = name.strip()

    breed = fields.get('breed')
    if breed is not None:
        if not isinstance(breed, str) or not 0 <= len(breed.strip()) <= 40:
            errors['breed'] = '品种需为 0—40 个字符'
        else:
            payload['breed'] = breed.strip()

    if 'birthDate' in fields and 'birthYear' in fields:
        errors['birthDate'] = '生日与估算年份只能提供其一'
    elif fields.get('birthDate') is not None:
        try:
            parsed, precision = pet_profile.parse_birth_date(fields['birthDate'],
                                                             fields.get('birthDatePrecision'))
            payload['birthDate'] = parsed.isoformat()
            payload['birthDatePrecision'] = precision
        except ValueError as exc:
            errors['birthDate'] = str(exc)
    elif fields.get('birthYear') is not None:
        year = fields['birthYear']
        if isinstance(year, bool) or not isinstance(year, int):
            errors['birthYear'] = '估算年份需为整数'
        else:
            try:  # 统一走 parse_birth_date 拿同一套合理窗口与精度规则
                parsed, precision = pet_profile.parse_birth_date(f'{year:04d}')
                payload['birthDate'] = parsed.isoformat()
                payload['birthDatePrecision'] = precision
            except ValueError as exc:
                errors['birthYear'] = str(exc)

    if fields.get('weightKg') is not None:
        try:
            payload['weightG'] = pet_profile.validate_weight_kg(fields['weightKg'])
        except ValueError as exc:
            errors['weightKg'] = str(exc)
        try:
            payload['weightMeasuredOn'] = pet_profile.parse_measured_on(
                fields.get('weightMeasuredOn')).isoformat()
        except ValueError as exc:
            errors['weightMeasuredOn'] = str(exc)
        raw = fields.get('weightRawText')
        if raw is not None:
            if not isinstance(raw, str) or len(raw.strip()) > 40:
                errors['weightRawText'] = '原始表述需为 40 字以内文字'
            else:
                payload['weightRawText'] = raw.strip()

    if not payload or errors:
        raise ProposalError('候选字段未通过校验', errors=errors or {'fields': '候选字段为空'})
    # 展示与提案负载分开：卡片显示 kg，落库用克
    if 'weightG' in payload:
        payload['weightKg'] = round(payload.pop('weightG') / 1000, 2)
    return payload


def _apply_writes(session, pet, payload):
    """把归一化候选写入档案真值（pets 域）：字段覆盖 + 体重按日 upsert。"""
    updated = {}
    if 'name' in payload:
        pet.name = payload['name']
        updated['name'] = payload['name']
    if 'breed' in payload:
        pet.breed = payload['breed']
        updated['breed'] = payload['breed']
    if 'birthDate' in payload:
        pet.birth_date = date.fromisoformat(payload['birthDate'])
        pet.birth_date_precision = payload['birthDatePrecision']
        updated['birthDate'] = payload['birthDate']
        updated['birthDatePrecision'] = payload['birthDatePrecision']
    if 'weightKg' in payload:
        pet_profile.upsert_weight_record(
            session, pet.id, date.fromisoformat(payload['weightMeasuredOn']),
            int(round(payload['weightKg'] * 1000)), source='agent',
            raw_text=payload.get('weightRawText'))
        updated['weightKg'] = payload['weightKg']
        updated['weightMeasuredOn'] = payload['weightMeasuredOn']
    session.flush()
    return updated


def _next_version(session, pet_id, kind):
    latest = session.execute(select(AgentProposal.version).where(
        AgentProposal.pet_id == pet_id, AgentProposal.kind == kind)
        .order_by(AgentProposal.version.desc())).scalars().first()
    return (latest or 0) + 1


def create(session, pet, turn_id, fields, explicit, kind=KIND_PROFILE, now=None):
    """创建提案：explicit=true 直接写入并记 confirmed；否则建 pending 并替代旧 pending。

    归一化失败抛 ProposalError（errors 字典带字段级原因），不建提案不落库。
    旧 pending 提案置 superseded 时同步重写其所属回合 blocks（旧卡翻「已有新候选」态），
    返回被刷新的回合 id 列表，调用方提交后据此推 WS turn 帧（agent-w03-spec.md §3.5）。
    """
    payload = normalize_fields(fields)
    now = now or utc_now()
    row = AgentProposal(pet_id=pet.id, turn_id=turn_id, kind=kind,
                        payload=json.dumps(payload, ensure_ascii=False),
                        version=_next_version(session, pet.id, kind),
                        status='confirmed' if explicit else 'pending',
                        created_at=now, decided_at=now if explicit else None)
    session.add(row)
    session.flush()
    refreshed_turn_ids = []
    for old in session.execute(select(AgentProposal).where(
            AgentProposal.pet_id == pet.id, AgentProposal.kind == kind,
            AgentProposal.status == 'pending', AgentProposal.id != row.id)).scalars():
        old.status = 'superseded'
        old.superseded_by = row.id
        _refresh_turn_blocks(session, old)
        if old.turn_id and old.turn_id not in refreshed_turn_ids:
            refreshed_turn_ids.append(old.turn_id)
    session.flush()
    updated = _apply_writes(session, pet, payload) if explicit else {}
    return row, updated, refreshed_turn_ids


def decide(session, pet, proposal_id, action, now=None):
    """主人决策：confirm 落库 / reject 仅翻转状态；幂等，异态 409。

    档案类（profile_update）在 confirm 时落库；W10 动作类只翻状态，
    实际执行由 routes 在事务提交后交给 agent.actions.execute_confirmed
    （录音/日记的复核与副作用都在自带会话中执行，agent-w10-spec.md §2）。
    返回 (提案行, 需刷新 blocks 的 turn_id, 本次是否发生状态翻转)；
    turn 为 None 表示提案不挂回合。
    """
    if action not in ('confirm', 'reject'):
        raise ProposalError('决策动作无效', status=400)
    row = session.get(AgentProposal, proposal_id)
    if row is None or row.pet_id != pet.id:
        raise ProposalError('提案不存在', status=404)
    now = now or utc_now()
    changed = False
    if row.status == 'pending':
        if action == 'confirm' and row.kind == KIND_PROFILE:
            payload = json.loads(row.payload)
            _apply_writes(session, pet, payload)
        row.status = 'confirmed' if action == 'confirm' else 'rejected'
        row.decided_at = now
        changed = True
        session.flush()
        _refresh_turn_blocks(session, row)
    elif row.status == ('confirmed' if action == 'confirm' else 'rejected'):
        pass  # 幂等：重复提交同一决策返回当前态，不重复执行
    elif row.status == 'superseded':
        raise ProposalError('提案已被新版本替代，请确认最新候选', status=409)
    else:
        raise ProposalError('提案已处理过另一种决策', status=409)
    return row, row.turn_id, changed


def _refresh_turn_blocks(session, row):
    """决策后重写回合 blocks 中对应提案卡的状态（卡片状态即回执，agent-w03-spec.md §3.5）。"""
    if not row.turn_id:
        return
    turn = session.get(AgentTurn, row.turn_id)
    if turn is None or not turn.blocks:
        return
    blocks = json.loads(turn.blocks)
    changed = False
    for block in blocks:
        if isinstance(block, dict) and block.get('type') == 'proposal' \
                and isinstance(block.get('proposal'), dict) and block['proposal'].get('id') == row.id:
            block['proposal'] = public(row)
            changed = True
    if changed:
        turn.blocks = json.dumps(blocks, ensure_ascii=False)


def latest_weight_payload(session, pet_id):
    """read_pet_profile 的体重节：最新记录 + 最近 5 条 + 总数。"""
    rows = session.execute(select(PetWeightRecord).where(PetWeightRecord.pet_id == pet_id)
                           .order_by(PetWeightRecord.measured_on.desc()).limit(5)).scalars().all()
    count = session.execute(select(func.count()).select_from(PetWeightRecord).where(
        PetWeightRecord.pet_id == pet_id)).scalar_one()
    items = [pet_profile.weight_record_dict(r) for r in rows]
    return {'latestWeight': items[0] if items else None,
            'weightRecordCount': count, 'recentWeights': items}


def profile_payload(session, pet):
    """read_pet_profile 结果组装：档案 + 年龄推导 + 体重节。"""
    result = {'name': pet.name, 'species': pet.species, 'breed': pet.breed,
              'birthDate': pet.birth_date.isoformat() if pet.birth_date else None,
              'birthDatePrecision': pet.birth_date_precision}
    age = pet_profile.age_years(pet.birth_date, pet.birth_date_precision)
    result['ageYears'], result['ageApproximate'] = (age if age else (None, False))
    result.update(latest_weight_payload(session, pet.id))
    return result
