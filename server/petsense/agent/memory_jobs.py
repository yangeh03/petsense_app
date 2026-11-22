"""记忆维护 worker（W04，agent-w04-spec.md §3.6–§3.8；决策 D3/D4）。

轻量后台线程（social jobs 同模式）。全部幂等：active 条目存在即跳过，
重启/重复 tick 不重复生成；回填窗口 30 天、每 tick 每宠 ≤3 天。
事实与门槛由代码组织，模型只写 summary 文字（D4）；无凭据/失败降级
facts-only（汇总，status=degraded）或保留旧卡重试（蒸馏）。单进程假设沿用。

作用域注意：runs.save 与 memory 存储各自开 session_scope（线程级共享会话），
生成器在自身作用域内完成写入、作用域外落轨迹，避免嵌套 begin。
"""
import json
import logging
import threading
import time
from datetime import timedelta

from sqlalchemy import func, select

from petsense.agent import memory, model, runs
from petsense.db import session_scope, utc_now
from petsense.db.models import Pet, TelemetryMinute
from petsense.db.models.memory import PetMemory

logger = logging.getLogger(__name__)

TICK_SECONDS = 30 * 60  # 30 分钟
BACKFILL_DAYS = 30
DAILY_PER_TICK = 3

DAILY_SUMMARY_PROMPT = (
    '你是宠物照料助手，为一份宠物日报写一段不超过120字的客观小结。'
    '只用给定事实与记忆条目，不编造；观测覆盖不足时如实带过，不推断为正常。输出纯文本。')
DISTILL_PROMPT = (
    '你是宠物照料助手，把关于一只宠物的记忆候选蒸馏成一张「认识卡」：'
    '不超过100字、客观、可长期成立的稳定认识（近期习惯、主人讲述的重要经历）。'
    '只用给定候选，不编造、不加入新猜测；候选不足以支撑的结论不要写。'
    '如提供了现有认识卡，在其基础上修订。输出纯文本，不要列表符号与换行。')


def _model_text(system_prompt, user_prompt, max_tokens=500):
    """一次无工具模型调用；失败/无凭据返回 None（调用方按 D4 降级）。"""
    try:
        result = model.respond([{'role': 'system', 'content': system_prompt},
                                {'role': 'user', 'content': user_prompt}],
                               tools=None, max_output_tokens=max_tokens)
    except Exception:
        return None
    return (result.get('text') or '').strip() or None


def _day_has_observation(session, pet, day):
    start, end = memory.shanghai_day_bounds(day)
    return session.execute(select(TelemetryMinute.minute).where(
        TelemetryMinute.owner_id == pet.user_id, TelemetryMinute.device_id == pet.device_id,
        TelemetryMinute.minute >= start, TelemetryMinute.minute < end).limit(1)).first() is not None


def _missing_daily_days(session, pet, now):
    """最近 BACKFILL_DAYS 天里有观测但尚无 active daily 的日子（新→旧，≤DAILY_PER_TICK）。"""
    days = []
    for offset in range(1, BACKFILL_DAYS + 1):
        day = (now.astimezone(memory.SHANGHAI) - timedelta(days=offset)).strftime('%Y-%m-%d')
        start, _ = memory.shanghai_day_bounds(day)
        if start >= now:
            continue
        if memory.active_daily(session, pet.id, start) is None and _day_has_observation(session, pet, day):
            days.append(day)
        if len(days) >= DAILY_PER_TICK:
            break
    return days


def generate_daily(pet, day_text, now):
    """生成一条日汇总（facts 代码组织、summary 模型组织、失败降级 facts-only）。幂等。"""
    start, end = memory.shanghai_day_bounds(day_text)
    with session_scope() as session:
        if memory.active_daily(session, pet.id, start) is not None:
            return {'day': day_text, 'created': False, 'reason': 'exists'}
        facts = memory.daily_facts(session, pet, start, end)
        day_memories = memory.memories_in_range(session, pet.id, start, end)
        refs = [{'type': 'observations', 'day': day_text}] + \
               [{'type': 'memory', 'id': row.id} for row in day_memories]
        memory_lines = [f'- [{row.kind}] {row.summary}' for row in day_memories]
        user_prompt = (f'宠物：{pet.name}\n日期：{day_text}\n'
                       f'当日观测事实（JSON）：{json.dumps(facts, ensure_ascii=False)}\n'
                       f'当日主人讲述/记忆条目：{chr(10).join(memory_lines) or "（无）"}')
        summary = _model_text(DAILY_SUMMARY_PROMPT, user_prompt)
        degraded = ['summary_model_unavailable'] if summary is None else []
        entry, error = memory.save_entry(
            session, pet.id, kind='daily', summary=summary or f'{day_text} 观测汇总（结构化事实）',
            subject='pet', start_at=start, end_at=end, facts={'day': day_text, **facts},
            source_type='worker', source_refs=refs, generated_at=now)
        if error:
            return {'day': day_text, 'created': False, 'error': error}
        entry_id = entry.id
        trace = {'form': 'summary', 'trigger': {'kind': 'daily', 'day': day_text},
                 'conclusion': {'day': day_text, 'memoryId': entry_id,
                                'minuteCount': facts['minuteCount'],
                                'modelSummary': summary is not None},
                 'messages': [{'role': 'user', 'prompt': user_prompt[:500]},
                              {'role': 'assistant', 'text': (summary or '')[:500]}],
                 'tool_summary': [], 'budget': {}, 'degraded': degraded}
    runs.save(pet.id, None, trace, 'degraded' if degraded else 'complete')
    return {'day': day_text, 'created': True, 'modelSummary': summary is not None}


def _last_week_range(now):
    """上一个完整上海自然周（周一至周日）的 (周一日期文本, 周日日期文本)。"""
    local = now.astimezone(memory.SHANGHAI)
    this_monday = local - timedelta(days=local.weekday())
    last_monday = this_monday - timedelta(days=7)
    return last_monday.strftime('%Y-%m-%d'), (this_monday - timedelta(days=1)).strftime('%Y-%m-%d')


def _week_plan(session, pet, now):
    """上周计划：该周 ≥1 个 daily、尚无 active weekly、且上周观测日的日报已补齐（避免聚合不完整）。"""
    week_start_text, week_end_text = _last_week_range(now)
    start, _ = memory.shanghai_day_bounds(week_start_text)
    _, end = memory.shanghai_day_bounds(week_end_text)
    daily_count = session.execute(select(func.count()).select_from(PetMemory).where(
        PetMemory.pet_id == pet.id, PetMemory.kind == 'daily', PetMemory.status == 'active',
        PetMemory.start_at >= start, PetMemory.start_at < end)).scalar_one()
    if daily_count == 0:
        return None
    existing = session.execute(select(PetMemory).where(
        PetMemory.pet_id == pet.id, PetMemory.kind == 'weekly', PetMemory.status == 'active',
        PetMemory.start_at == start)).scalars().first()
    if existing is not None:
        return None
    for offset in range(1, 8):  # 上周 7 天里还有观测日的日报未生成 → 本 tick 先补日报，下周计划顺延
        day = (now.astimezone(memory.SHANGHAI) - timedelta(days=offset)).strftime('%Y-%m-%d')
        day_start, _ = memory.shanghai_day_bounds(day)
        if start <= day_start < end and memory.active_daily(session, pet.id, day_start) is None \
                and _day_has_observation(session, pet, day):
            return None
    return {'weekStart': week_start_text, 'weekEnd': week_end_text, 'start': start, 'end': end,
            'dailyCount': daily_count}


def generate_weekly(pet, plan, now):
    """生成上周周汇总：聚合该周 daily 计数 + 周窗口总量（observations 同口径）。"""
    with session_scope() as session:
        facts = memory.daily_facts(session, pet, plan['start'], plan['end'])
        user_prompt = (f"宠物：{pet.name}\n周：{plan['weekStart']} 至 {plan['weekEnd']}\n"
                       f'全周观测事实（JSON）：{json.dumps(facts, ensure_ascii=False)}\n'
                       f"本周日报条数：{plan['dailyCount']}")
        summary = _model_text(DAILY_SUMMARY_PROMPT, user_prompt)
        degraded = ['summary_model_unavailable'] if summary is None else []
        entry, error = memory.save_entry(
            session, pet.id, kind='weekly',
            summary=summary or f"{plan['weekStart']} 周汇总（结构化事实）",
            subject='pet', start_at=plan['start'], end_at=plan['end'],
            facts={'weekStart': plan['weekStart'], 'weekEnd': plan['weekEnd'],
                   'dailyCount': plan['dailyCount'], **facts},
            source_type='worker', source_refs=[{'type': 'observations', 'week': plan['weekStart']}],
            generated_at=now)
        if error:
            return {'week': plan['weekStart'], 'created': False, 'error': error}
        entry_id = entry.id
        trace = {'form': 'summary', 'trigger': {'kind': 'weekly', 'week': plan['weekStart']},
                 'conclusion': {'week': plan['weekStart'], 'memoryId': entry_id,
                                'dailyCount': plan['dailyCount'],
                                'modelSummary': summary is not None},
                 'messages': [{'role': 'user', 'prompt': user_prompt[:500]},
                              {'role': 'assistant', 'text': (summary or '')[:500]}],
                 'tool_summary': [], 'budget': {}, 'degraded': degraded}
    runs.save(pet.id, None, trace, 'degraded' if degraded else 'complete')
    return {'week': plan['weekStart'], 'created': True, 'modelSummary': summary is not None}


def distill(pet, pool, now):
    """认识卡蒸馏：候选池与门槛在规划作用域由代码判定，模型只组织 ≤100 字文字。"""
    if not pool['due']:
        return {'distilled': False,
                'reason': 'not_eligible' if not pool['eligible'] else 'interval'}
    habits, episodes, current = pool['habits'], pool['episodes'], pool['current']
    lines = [f'- [habit_candidate] {row.summary}' for row in habits] + \
            [f'- [{row.kind}] {row.summary}' for row in episodes]
    user_prompt = (f'宠物：{pet.name}（{pet.species or ""}）\n候选记忆：{chr(10).join(lines)}\n'
                   f'现有认识卡：{current.summary if current else "（无）"}')
    text = _model_text(DISTILL_PROMPT, user_prompt, max_tokens=300)
    if text is None:
        return {'distilled': False, 'reason': 'model_unavailable'}  # 保留旧卡，下次 tick 重试
    with session_scope() as session:
        if current is not None and memory.active_insight(session, pet.id) is None:
            return {'distilled': False, 'reason': 'stale_plan'}
        entry, error = memory.save_entry(
            session, pet.id, kind='insight_card', summary=text[:memory.INSIGHT_CHAR_LIMIT],
            subject='pet', source_type='worker',
            source_refs=[{'type': 'memory', 'id': row.id} for row in list(habits) + list(episodes)],
            supersedes_id=current.id if current is not None else None, generated_at=now)
        if error:
            return {'distilled': False, 'error': error}
        entry_id, superseded_id = entry.id, current.id if current else None
        kept = entry.summary
        trace = {'form': 'distill', 'trigger': {'kind': 'scheduled', 'petId': pet.id},
                 'conclusion': {'decision': 'promoted', 'memoryId': entry_id,
                                'supersededInsightId': superseded_id,
                                'candidates': {'habitCandidates': len(habits), 'episodes': len(episodes)},
                                'kept': kept,
                                'reason': f'门槛满足（habit_candidate≥2 或 近30天经历≥3）：'
                                          f'habit_candidate×{len(habits)}、经历×{len(episodes)}；'
                                          '未入选候选保留原条目，淘汰随下轮蒸馏自然发生'},
                 'messages': [{'role': 'user', 'prompt': user_prompt[:500]},
                              {'role': 'assistant', 'text': kept}],
                 'tool_summary': [], 'budget': {}, 'degraded': []}
    runs.save(pet.id, None, trace, 'complete')
    return {'distilled': True, 'insightId': entry_id, 'superseded': superseded_id}


def process_pet(pet_id, now=None):
    """单宠一轮维护：先补日汇总，再在日报完整的基础上规划周汇总与蒸馏，最后落报告。全部幂等。"""
    now = now or utc_now()
    outcome = {'daily': [], 'weekly': None, 'distill': None, 'report': False}
    with session_scope() as session:
        pet = session.get(Pet, pet_id)
        if pet is None:
            return outcome
        days = _missing_daily_days(session, pet, now)
    for day in days:
        outcome['daily'].append(generate_daily(pet, day, now))
    with session_scope() as session:
        week_plan = _week_plan(session, pet, now)
        pool = memory.distill_candidates(session, pet.id, now)
        has_memory = session.execute(select(PetMemory.id).where(
            PetMemory.pet_id == pet_id, PetMemory.status == 'active').limit(1)).first() is not None
    if week_plan is not None:
        outcome['weekly'] = generate_weekly(pet, week_plan, now)
    outcome['distill'] = distill(pet, pool, now)
    if has_memory:
        with session_scope() as session:
            memory.write_report(session, pet)
        outcome['report'] = True
    return outcome


def tick():
    with session_scope() as session:
        pet_ids = session.execute(select(Pet.id)).scalars().all()
    for pet_id in pet_ids:
        try:
            process_pet(pet_id)
        except Exception:
            logger.exception('memory worker failed for pet %s', pet_id)


def start_memory_worker():
    """app.py start_background 内启动；daemon 线程常驻，异常不退出。"""
    def _loop():
        while True:
            try:
                tick()
            except Exception:
                logger.exception('memory worker tick failed')
            time.sleep(TICK_SECONDS)
    threading.Thread(target=_loop, name='agent-memory-worker', daemon=True).start()
