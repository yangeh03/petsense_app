"""PetMemory 存储层（W04，agent-w04-spec.md §3.2–§3.8）。

职责：落库前校验（判断归模型、校验归代码）、结构化 + FTS5 trigram 检索、
版本链/回滚、认识卡读取、markdown 报告渲染。工具执行器与维护 worker 都经本层写库。
失败返回结构化 error 不抛裸异常；无匹配不等于没有记忆（结果 note 如实说明）。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select, text

from petsense import config
from petsense.db import iso_z, utc_now
from petsense.db.models.memory import PetMemory

SHANGHAI = ZoneInfo('Asia/Shanghai')

KINDS = ('episode', 'owner_event', 'habit_candidate', 'session_takeaway',
         'daily', 'weekly', 'insight_card', 'preference', 'change_event', 'task_result')
MODEL_WRITABLE = ('episode', 'owner_event', 'habit_candidate', 'session_takeaway')
EXPERIENCE_KINDS = ('episode', 'owner_event')
MAX_SUMMARY_CHARS = 300
MAX_FACTS_BYTES = 2048
MAX_WRITES_PER_TURN = 3
INSIGHT_CHAR_LIMIT = 150
REPORT_DIR_NAME = 'agent-memory'


def parse_time(value):
    """宽容解析 ISO 时间（缺时区按 Asia/Shanghai），失败返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed


def save_entry(session, pet_id, *, kind, summary, subject='pet', start_at=None, end_at=None,
               facts=None, certainty='stated', source_type='agent', source_refs=None,
               supersedes_id=None, write_level='autonomous', generated_at=None):
    """落库一条记忆（结构性校验；spec §3.2/§3.3）。返回 (entry, error)，error 非空则未落库。

    supersede：被替换条目必须为同宠 active；成功后置 superseded，新条目 version 递增。
    """
    if kind not in KINDS:
        return None, f'未知的记忆类型 {kind}（全集：{"、".join(KINDS)}）'
    if subject not in ('pet', 'owner'):
        return None, '记忆主体只能是本宠或主人；其他主体的内容不写入长期记忆'
    summary = (summary or '').strip()
    if not summary:
        return None, 'summary 不能为空'
    if len(summary) > MAX_SUMMARY_CHARS:
        return None, f'summary 超过 {MAX_SUMMARY_CHARS} 字，请精简为一句话'
    if certainty not in ('stated', 'tentative'):
        certainty = 'stated'
    facts_json = '{}'
    if isinstance(facts, dict) and facts:
        facts_json = json.dumps(facts, ensure_ascii=False)
        if len(facts_json.encode('utf-8')) > MAX_FACTS_BYTES:
            return None, 'facts 结构化字段过大'
    if start_at is not None and end_at is not None and end_at < start_at:
        return None, '结束时间早于开始时间'
    supersedes = None
    if supersedes_id is not None:
        supersedes = session.get(PetMemory, supersedes_id)
        if supersedes is None or supersedes.pet_id != pet_id:
            return None, '被纠正的记忆不存在'
        if supersedes.status != 'active':
            return None, '被纠正的记忆已不是当前有效版本'
    entry = PetMemory(pet_id=pet_id, kind=kind, subject=subject,
                      start_at=start_at, end_at=end_at,
                      generated_at=generated_at or utc_now(),
                      facts=facts_json, summary=summary,
                      source_refs=json.dumps(source_refs or [], ensure_ascii=False),
                      source_type=source_type, write_level=write_level, certainty=certainty,
                      version=(supersedes.version + 1) if supersedes is not None else 1,
                      supersedes_id=supersedes_id, status='active', created_at=utc_now())
    session.add(entry)
    session.flush()
    if supersedes is not None:
        supersedes.status = 'superseded'
    return entry, None


def save_model_write(session, pet_id, args, context):
    """write_memory 工具路径：模型白名单 + tentative 规则 + 每回合上限/去重（spec §3.3）。"""
    kind = args.get('kind')
    if kind not in MODEL_WRITABLE:
        return None, (f'{kind} 不在可写类型内。可自主写：episode（宠物经历）、owner_event（主人的事）、'
                       'habit_candidate（规律候选）、session_takeaway（会话要点）；'
                       '长期偏好等确认写尚未接入，请如实向主人说明')
    tentative = args.get('tentative') is True
    if tentative and kind != 'habit_candidate':
        return None, '不确定的内容只有习惯候选（habit_candidate）可以保存；请先与主人确认，或仅记明确的经历'
    summary = (args.get('summary') or '').strip()
    start, end = parse_time(args.get('startAt')), parse_time(args.get('endAt'))
    if args.get('startAt') and start is None:
        return None, 'startAt 不是合法的 ISO 时间'
    if args.get('endAt') and end is None:
        return None, 'endAt 不是合法的 ISO 时间'
    log = context.setdefault('memory_writes', []) if isinstance(context, dict) else None
    if log is not None:
        if any(kind == k and summary == s for k, s in log):
            return None, '本回合已保存过相同内容'
        if len(log) >= MAX_WRITES_PER_TURN:
            return None, f'本回合写入已达上限（{MAX_WRITES_PER_TURN} 条）；其余内容可在之后回合再记'
    turn_id = context.get('turn_id') if isinstance(context, dict) else None
    entry, error = save_entry(session, pet_id=pet_id, kind=kind, summary=summary,
                              subject=args.get('subject') if args.get('subject') is not None else 'pet',
                              start_at=start, end_at=end,
                              facts=args.get('facts') if isinstance(args.get('facts'), dict) else None,
                              certainty='tentative' if tentative else 'stated', source_type='agent',
                              source_refs=[{'type': 'turn', 'id': turn_id}] if turn_id else [],
                              supersedes_id=args.get('supersedesId')
                              if isinstance(args.get('supersedesId'), int) else None)
    if error is None and log is not None:
        log.append((kind, summary))
    return entry, error


def forget(session, pet_id, memory_id):
    """retire 一条记忆（主人明确要求忘掉）。条目保留、版本链完整；幂等。"""
    row = session.get(PetMemory, memory_id)
    if row is None or row.pet_id != pet_id:
        return None, '该记忆不存在'
    if row.status != 'retired':
        row.status = 'retired'
    return row, None


def _fts_ids(session, pet_id, keyword):
    """FTS5 trigram 召回（≥3 字）。不可用时返回 None 由调用方回退 LIKE。"""
    if len(keyword) < 3 or session.get_bind().dialect.name != 'sqlite':
        return None
    phrase = '"' + keyword.replace('"', '""') + '"'
    try:
        return session.execute(text(
            'SELECT f.rowid FROM pet_memories_fts f JOIN pet_memories m ON m.id = f.rowid '
            "WHERE pet_memories_fts MATCH :q AND m.pet_id = :pid AND m.status = 'active'"),
            {'q': phrase, 'pid': pet_id}).scalars().all()
    except Exception:
        return None


def search(session, pet_id, kinds=None, from_dt=None, to_dt=None, keyword=None, limit=10):
    """结构化条件 + summary 关键词检索（只返回 active）。无发生时间的条目仅在无时间条件时返回。"""
    stmt = select(PetMemory).where(PetMemory.pet_id == pet_id, PetMemory.status == 'active')
    if kinds:
        valid = [k for k in kinds if k in KINDS]
        if valid:
            stmt = stmt.where(PetMemory.kind.in_(valid))
    if from_dt is not None or to_dt is not None:
        bounds = []
        if from_dt is not None:
            bounds.append(PetMemory.start_at >= from_dt)
        if to_dt is not None:
            bounds.append(PetMemory.start_at < to_dt)
        stmt = stmt.where(and_(*bounds))
    keyword = (keyword or '').strip()
    if keyword:
        ids = _fts_ids(session, pet_id, keyword)
        if ids is not None:
            stmt = stmt.where(PetMemory.id.in_(ids))
        else:
            stmt = stmt.where(PetMemory.summary.like(f'%{keyword}%'))
    rows = session.execute(stmt.order_by(PetMemory.generated_at.desc())
                           .limit(max(1, min(limit, 30)))).scalars().all()
    total = session.execute(select(func.count()).select_from(PetMemory).where(
        PetMemory.pet_id == pet_id, PetMemory.status == 'active')).scalar_one()
    result = {'memories': [entry_public(row) for row in rows], 'matched': len(rows),
              'totalActive': total,
              'note': '检索只覆盖当前有效记忆；无匹配不等于没有记忆，可换关键词或条件再试'}
    return result


def entry_public(row):
    return {'id': row.id, 'kind': row.kind, 'subject': row.subject, 'summary': row.summary,
            'startAt': iso_z(row.start_at) if row.start_at else None,
            'endAt': iso_z(row.end_at) if row.end_at else None,
            'generatedAt': iso_z(row.generated_at), 'certainty': row.certainty,
            'version': row.version, 'supersedesId': row.supersedes_id}


def history_of(session, memory_id):
    """版本链（含 superseded/retired），按版本正序：先回溯到链头，再沿 supersedes 正向收集。"""
    row = session.get(PetMemory, memory_id)
    if row is None:
        return []
    chain = [row]
    head = row
    while head.supersedes_id:
        older = session.get(PetMemory, head.supersedes_id)
        if older is None:
            break
        chain.append(older)
        head = older
    root = chain[-1]
    full = [root]
    current_id, seen = root.id, {root.id}
    while True:
        nxt = session.execute(select(PetMemory).where(PetMemory.supersedes_id == current_id)
                              .order_by(PetMemory.id)).scalars().first()
        if nxt is None or nxt.id in seen:
            break
        full.append(nxt)
        seen.add(nxt.id)
        current_id = nxt.id
    return full


def active_insight(session, pet_id):
    return session.execute(select(PetMemory).where(
        PetMemory.pet_id == pet_id, PetMemory.kind == 'insight_card',
        PetMemory.status == 'active').order_by(PetMemory.id.desc()).limit(1)).scalars().first()


def insight_line(session, pet_id):
    """快照 memory 节的一行（spec §3.9）：有界认识卡或如实说明。"""
    card = active_insight(session, pet_id)
    if card is None or not (card.summary or '').strip():
        return '尚无稳定认识'
    return card.summary.strip()[:INSIGHT_CHAR_LIMIT]


# ---- 日/周汇总与蒸馏的确定性事实组织（worker 调用，spec §3.6/§3.7）----

def shanghai_day_bounds(day_text):
    """上海自然日 [start, end)（aware，入库由 UTCDatetime 归一为 UTC）。"""
    local = datetime.strptime(day_text, '%Y-%m-%d').replace(tzinfo=SHANGHAI)
    return local, local + timedelta(days=1)


def day_text(moment):
    return moment.astimezone(SHANGHAI).strftime('%Y-%m-%d')


def has_observation(session, pet, start, end):
    from petsense.db.models import TelemetryMinute
    if not pet.device_id:
        return False
    return session.execute(select(func.count()).select_from(TelemetryMinute).where(
        TelemetryMinute.owner_id == pet.user_id, TelemetryMinute.device_id == pet.device_id,
        TelemetryMinute.minute >= start, TelemetryMinute.minute < end).limit(1)).scalar_one() > 0


def daily_facts(session, pet, start, end):
    """日汇总 facts（代码组织，D2 同口径原则：复用 observations.query，不造第二套数）。"""
    from petsense.agent.observations import query as observations_query
    result = observations_query(session, pet, start, end)
    return {'actionSeconds': result.get('actionSeconds') or {},
            'observedSeconds': result.get('observedSeconds') or 0,
            'soundEventCount': result.get('soundEventCount') or 0,
            'minuteCount': result.get('minuteCount') or 0,
            'learningDimensions': [{'dimension': d['dimension'], 'validDays': d['validDays']}
                                   for d in result.get('learningDimensions') or []]}


def active_daily(session, pet_id, start):
    return session.execute(select(PetMemory).where(
        PetMemory.pet_id == pet_id, PetMemory.kind == 'daily',
        PetMemory.status == 'active', PetMemory.start_at == start)).scalars().first()


def memories_in_range(session, pet_id, start, end):
    """窗口内产生（generated_at）的 active 经历类条目，供汇总引用与蒸馏候选。"""
    return session.execute(select(PetMemory).where(
        PetMemory.pet_id == pet_id, PetMemory.kind.in_(MODEL_WRITABLE),
        PetMemory.status == 'active',
        PetMemory.generated_at >= start, PetMemory.generated_at < end)
        .order_by(PetMemory.generated_at)).scalars().all()


def active_of_kinds(session, pet_id, kinds):
    return session.execute(select(PetMemory).where(
        PetMemory.pet_id == pet_id, PetMemory.kind.in_(kinds), PetMemory.status == 'active')
        .order_by(PetMemory.generated_at.desc())).scalars().all()


def distill_candidates(session, pet_id, now):
    """蒸馏候选池与门槛（确定性代码，spec §3.7）。eligible=False 时不触发蒸馏。"""
    habits = active_of_kinds(session, pet_id, ('habit_candidate',))
    episodes = [row for row in active_of_kinds(session, pet_id, EXPERIENCE_KINDS)
                if row.generated_at >= now - timedelta(days=30)][:15]
    current = active_insight(session, pet_id)
    eligible = len(habits) >= 2 or len(episodes) >= 3
    due = eligible and (current is None or (
        current.generated_at <= now - timedelta(hours=24)
        and _has_newer_memory(session, pet_id, current.generated_at)))
    return {'habits': habits, 'episodes': episodes, 'current': current, 'eligible': eligible, 'due': due}


def _has_newer_memory(session, pet_id, since):
    return session.execute(select(func.count()).select_from(PetMemory).where(
        PetMemory.pet_id == pet_id, PetMemory.status == 'active',
        PetMemory.kind != 'insight_card',
        PetMemory.generated_at > since).limit(1)).scalar_one() > 0


# ---- markdown 报告（DB 为唯一真相，报告为视图，spec §3.8）----

def render_report(session, pet):
    lines = [f'# {pet.name} 的长期记忆报告', '',
             f'生成时间：{iso_z(utc_now())}。数据库为唯一真相，本报告为只读视图。', '']
    card = active_insight(session, pet.id)
    lines += ['## 认识卡', (card.summary if card and card.summary else '（尚无稳定认识）'), '']
    lines.append('## 经历（按月）')
    experiences = active_of_kinds(session, pet.id, EXPERIENCE_KINDS)
    if experiences:
        months = {}
        for row in experiences:
            label = row.start_at.astimezone(SHANGHAI).strftime('%Y-%m') if row.start_at \
                else row.generated_at.astimezone(SHANGHAI).strftime('%Y-%m')
            months.setdefault(label, []).append(row)
        for label in sorted(months, reverse=True):
            lines.append(f'### {label}')
            for row in months[label]:
                when = row.start_at.astimezone(SHANGHAI).strftime('%Y-%m-%d') if row.start_at else '时间未知'
                lines.append(f'- [{row.kind}] {row.summary}（{when}，v{row.version}，来源 {row.source_refs}）')
    else:
        lines.append('（暂无）')
    lines.append('')
    lines.append('## 习惯候选')
    habits = active_of_kinds(session, pet.id, ('habit_candidate',))
    lines += [f'- {row.summary}（{"不确定" if row.certainty == "tentative" else "明确"}）' for row in habits] \
        or ['（暂无）']
    lines.append('')
    lines.append('## 日/周汇总（最近 14 条）')
    summaries = session.execute(select(PetMemory).where(
        PetMemory.pet_id == pet.id, PetMemory.kind.in_(('daily', 'weekly')), PetMemory.status == 'active')
        .order_by(PetMemory.generated_at.desc()).limit(14)).scalars().all()
    if summaries:
        for row in summaries:
            when = row.start_at.astimezone(SHANGHAI).strftime('%Y-%m-%d') if row.start_at else '?'
            lines.append(f'- {row.kind} {when}：{row.summary or "（仅结构化事实，无文字小结）"}')
    else:
        lines.append('（暂无）')
    lines.append('')
    lines.append('## 历史版本（已被替换或遗忘）')
    archived = session.execute(select(PetMemory).where(
        PetMemory.pet_id == pet.id, PetMemory.status != 'active')
        .order_by(PetMemory.id)).scalars().all()
    if archived:
        for row in archived:
            lines.append(f'- v{row.version} [{row.kind}/{row.status}] {row.summary}（被 #{row.id} 链替换/遗忘）')
    else:
        lines.append('（无）')
    return '\n'.join(lines) + '\n'


def write_report(session, pet, root=None):
    """把报告写到运行目录（默认 RUNTIME_ROOT/agent-memory/pet-<id>/memory.md）。"""
    target_dir = Path(root if root is not None else config.RUNTIME_ROOT) / REPORT_DIR_NAME / f'pet-{pet.id}'
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / 'memory.md'
    path.write_text(render_report(session, pet), encoding='utf-8')
    return path
