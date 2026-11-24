"""通用 SQLite 持久作业队列与调度 worker（W08，agent-w08-spec.md §3；供 W09 事件运行复用）。

- 追加式入队：dedup_key 为稳定结果键，唯一约束 + ON CONFLICT 语义保证同一键只建一个作业；
- 领取即租约：短事务把 due 作业置 working 并写 lease_until，租约过期的行可被重新领取；
- 有界重试：handler 抛错时 attempts+1 并按退避重排，达到 max_attempts 置 dead；
- 重启恢复：单进程假设下（与 memory/social worker 相同），启动时仍处 working 的行即
  中断证据，recover_interrupted() 统一回置 pending；作业级重复执行由交付层的
  稳定结果键（如 agent_task_deliveries 唯一键）兜底去重；
- 测试时钟：tick/claim_due/handler 都接受注入 now，固定时钟下可确定性验收；
- 开关：PETSENSE_SCHEDULER_DISABLED=1 时不启动线程（测试与隔离实例可关闭）。

W09 接入点：register_handler(kind, fn) 注册事件运行等新 kind；fn(job_dict, now)
内部自行开 session_scope；enqueue(session, ...) 可在任意业务事务内追加作业，
与业务写入同事务提交；handler 返回 {'next_run_at': …} 表示作业保持 pending 等待
下次触发（周期检查点），返回 None 表示本作业完成。

群聊回合（W 系列 design doc §3）等需要及时执行的作业入队后调用 wake()，调度线程立即跑一轮，
不必等满 TICK_SECONDS；requeue() 把已结束的作业按原稳定键重新排队（用户主动重试）。
"""
import json
import logging
import os
import threading
from datetime import timedelta

from sqlalchemy import or_, select

from petsense.db import dialect_insert, session_scope, utc_now
from petsense.db.models.agent import AgentJob

logger = logging.getLogger(__name__)

TICK_SECONDS = 15
LEASE_SECONDS = 120
DEFAULT_MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 60

_HANDLERS = {}
_wake = threading.Event()   # 入队聊天回合等需要及时执行的作业后置位，调度线程不再等满一个 tick


def register_handler(kind, handler):
    """注册 kind 的执行器；重复注册覆盖旧值（测试隔离用）。"""
    _HANDLERS[kind] = handler


def handler_for(kind):
    return _HANDLERS.get(kind)


def scheduler_enabled():
    """PETSENSE_SCHEDULER_DISABLED=1 时关闭业务调度线程（测试/隔离实例）。"""
    return os.environ.get('PETSENSE_SCHEDULER_DISABLED', '') not in ('1',)


def enqueue(session, kind, payload, dedup_key, next_run_at=None, max_attempts=DEFAULT_MAX_ATTEMPTS):
    """追加作业（幂等）：dedup_key 已存在时不建新行。返回 (job_id, created)。

    用方言 upsert（on_conflict_do_nothing）而不是「先查后插」：并发同键写入时
    各得一个幂等结果且不产生重复行（先查后插在两个事务同时查空时会各插一行）。
    """
    now = utc_now()
    stmt = dialect_insert(session, AgentJob).values(
        kind=kind, dedup_key=dedup_key,
        payload_json=json.dumps(payload, ensure_ascii=False),
        status='pending', attempts=0, max_attempts=max_attempts,
        next_run_at=next_run_at or now, created_at=now, updated_at=now)
    stmt = stmt.on_conflict_do_nothing(index_elements=['dedup_key'])
    result = session.execute(stmt)
    if result.rowcount == 1:
        return result.inserted_primary_key[0], True
    existing = session.execute(select(AgentJob.id).where(
        AgentJob.dedup_key == dedup_key)).scalar_one()
    return existing, False


def requeue(session, kind, payload, dedup_key, now=None):
    """把一个已结束（done/dead）的作业重新排队；不存在则新建。返回 (job_id, created)。

    用于用户主动重试（如群聊里失败的那条消息）：重试沿用同一个稳定键，所以不会出现第二个作业。
    """
    now = now or utc_now()
    row = session.execute(select(AgentJob).where(AgentJob.dedup_key == dedup_key)).scalar_one_or_none()
    if row is None:
        return enqueue(session, kind, payload, dedup_key, next_run_at=now)
    if row.status in ('done', 'dead'):
        row.status, row.attempts, row.next_run_at, row.lease_until = 'pending', 0, now, None
        row.last_error = None
        row.updated_at = now
    elif row.status == 'pending' and row.next_run_at > now:
        row.next_run_at = now  # 还在排队或等待下次触发的作业立即到期
    row.payload_json = json.dumps(payload, ensure_ascii=False)
    return row.id, False


def cancel(session, dedup_key):
    """按稳定键终止作业（任务取消/完成时）：pending → done，working 行留给领取方复核状态。"""
    row = session.execute(select(AgentJob).where(AgentJob.dedup_key == dedup_key)).scalar_one_or_none()
    if row is None or row.status not in ('pending', 'working'):
        return False
    if row.status == 'pending':
        row.status = 'done'
        row.lease_until = None
        row.updated_at = utc_now()
    return True


def cancel_prefix(session, prefix):
    """按稳定键前缀终止作业（委托取消：清掉该委托所有版本的检查点作业）。返回取消数。"""
    rows = session.execute(select(AgentJob).where(
        AgentJob.dedup_key.like(prefix + '%'),
        AgentJob.status == 'pending')).scalars().all()
    for row in rows:
        row.status = 'done'
        row.lease_until = None
        row.updated_at = utc_now()
    return len(rows)


def _job_dict(row):
    return {'id': row.id, 'kind': row.kind, 'dedup_key': row.dedup_key,
            'payload': json.loads(row.payload_json), 'attempts': row.attempts}


def claim_due(now, limit=1):
    """领取到期作业（到期优先：按 next_run_at 升序）。返回 job dict 列表。"""
    claimed = []
    with session_scope() as s:
        rows = s.execute(select(AgentJob).where(or_(
            (AgentJob.status == 'pending') & (AgentJob.next_run_at <= now),
            (AgentJob.status == 'working') & (AgentJob.lease_until <= now)))
            .order_by(AgentJob.next_run_at, AgentJob.id).limit(limit)).scalars().all()
        for row in rows:
            row.status = 'working'
            row.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            row.updated_at = utc_now()
            claimed.append(_job_dict(row))
    return claimed


def _finish(job_id, result, error=None):
    with session_scope() as s:
        row = s.get(AgentJob, job_id)
        if row is None:
            return
        row.updated_at = utc_now()
        if error is not None:
            row.attempts += 1
            row.last_error = str(error)[:500]
            if row.attempts >= row.max_attempts:
                row.status = 'dead'
                row.lease_until = None
            else:
                row.status = 'pending'
                row.next_run_at = utc_now() + timedelta(
                    seconds=BACKOFF_BASE_S * 2 ** row.attempts)
                row.lease_until = None
            return
        if isinstance(result, dict) and result.get('next_run_at') is not None:
            row.status = 'pending'
            row.next_run_at = result['next_run_at']
            row.lease_until = None
            if result.get('payload') is not None:
                row.payload_json = json.dumps(result['payload'], ensure_ascii=False)
        else:
            row.status = 'done'
            row.lease_until = None


def run_one(now):
    """执行一个到期作业。返回 True 表示执行了一个（含失败），False 表示无到期作业。"""
    jobs = claim_due(now, limit=1)
    if not jobs:
        return False
    job = jobs[0]
    handler = handler_for(job['kind'])
    try:
        if handler is None:
            raise ValueError(f"作业 kind={job['kind']} 尚未注册执行器")
        result = handler(job, now)
    except Exception as exc:  # noqa: BLE001 作业失败不退出 worker，按退避重试
        logger.exception('agent job %s(%s) failed', job['kind'], job['id'])
        _finish(job['id'], None, error=exc)
        return True
    _finish(job['id'], result)
    return True


def tick(now=None, limit=10):
    """一轮调度：最多执行 limit 个到期作业。返回执行数。"""
    now = now or utc_now()
    count = 0
    while count < limit and run_one(now):
        count += 1
    return count


def recover_interrupted():
    """启动恢复：单进程内 working 状态即重启中断证据，回置 pending 立即可重领。"""
    with session_scope() as s:
        rows = s.execute(select(AgentJob).where(AgentJob.status == 'working')).scalars().all()
        for row in rows:
            row.status = 'pending'
            row.lease_until = None
            row.updated_at = utc_now()
        return len(rows)


def wake():
    """让调度线程立即跑一轮（入队需要及时执行的作业后调用；调度线程未启动时无效果）。"""
    _wake.set()


def start_scheduler():
    """app.py start_background 内启动；daemon 线程常驻，异常不退出（同 memory worker 模式）。"""
    if not scheduler_enabled():
        return
    recovered = recover_interrupted()
    if recovered:
        logger.info('agent scheduler recovered %d interrupted jobs', recovered)

    def _loop():
        while True:
            try:
                tick()
            except Exception:
                logger.exception('agent scheduler tick failed')
            _wake.wait(TICK_SECONDS)
            _wake.clear()

    threading.Thread(target=_loop, name='agent-scheduler', daemon=True).start()
