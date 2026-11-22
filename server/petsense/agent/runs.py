"""AgentRun 轨迹写入与查询（W01，agent-w01-spec.md §3.5）。"""
import json

from sqlalchemy import select

from petsense.db import session_scope, utc_now, iso_z
from petsense.db.models.agent import AgentRun


def save(pet_id, turn_id, trace, status, error=None):
    """落库一次运行轨迹；trace 由 loop 产出（messages/tool_summary/budget/conclusion/snapshot）。"""
    snapshot = trace.get('snapshot')
    with session_scope() as s:
        run = AgentRun(pet_id=pet_id, turn_id=turn_id, form=trace.get('form', 'dialogue'),
                       status=status, error=error, created_at=utc_now(),
                       conclusion=json.dumps(trace.get('conclusion', {}), ensure_ascii=False),
                       messages=json.dumps(trace.get('messages', []), ensure_ascii=False, default=str),
                       tool_summary=json.dumps(trace.get('tool_summary', []), ensure_ascii=False, default=str),
                       budget=json.dumps(trace.get('budget', {}), ensure_ascii=False, default=str),
                       snapshot=json.dumps(snapshot, ensure_ascii=False, default=str) if snapshot is not None else None)
        s.add(run)
        s.flush()
        return run.id


def update_conclusion(run_id, patch):
    """运行落库后回填结论字段（W04 §3.11：条目落库后回填 memoryId，双向可溯源）。

    run 不存在时静默跳过；只合并 patch 的键，不动其余结论。
    """
    with session_scope() as s:
        row = s.get(AgentRun, run_id)
        if row is None:
            return
        conclusion = json.loads(row.conclusion or '{}')
        conclusion.update(patch)
        row.conclusion = json.dumps(conclusion, ensure_ascii=False)


def summarize(run):
    """回放视图：完整结论与消息序列。"""
    return {'id': run.id, 'petId': run.pet_id, 'turnId': run.turn_id, 'form': run.form,
            'status': run.status, 'error': run.error, 'createdAt': iso_z(run.created_at),
            'conclusion': json.loads(run.conclusion), 'messages': json.loads(run.messages),
            'toolSummary': json.loads(run.tool_summary), 'budget': json.loads(run.budget),
            'snapshot': json.loads(run.snapshot) if run.snapshot else None}


def latest(pet_id, limit=20):
    with session_scope() as s:
        rows = s.execute(select(AgentRun).where(AgentRun.pet_id == pet_id)
                         .order_by(AgentRun.id.desc()).limit(limit)).scalars().all()
        return [summarize(row) for row in rows]


def by_id(run_id):
    with session_scope() as s:
        row = s.get(AgentRun, run_id)
        return summarize(row) if row else None
