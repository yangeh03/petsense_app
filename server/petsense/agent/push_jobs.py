"""W13 推送 worker：挂在 W08 持久作业队列上的周期派发与回执扫描。

沿用 W08 的作业语义：稳定 dedup_key、自排 next_run_at、租约、退避与重启恢复。
本包不自建调度线程。

每轮做三件事（顺序固定）：

1. ``push.recover_stuck``：把停在 sending 且超时的行标成「结果未知」并停止重试
   （进程在外部调用之后、落库之前中断的场景）。
2. ``push.dispatch_pending``：领取并投递到期待发件。写前意图（pending → sending）
   在独立事务里先提交，外部调用之后的落库若中断，行停在 sending，下一轮按未知处理。
3. ``push.sweep_receipts``：查 Expo 回执，更新 delivered / rejected / failed，
   明确可恢复的失败回到 pending 并按退避重试。

平台闸门默认关闭：闸门关闭时本轮不调用服务方，待发件保留在队列里（不消耗
重试次数），闸门重新打开后仍可发送——这是「快速停发手段」而不是「销毁队列」。
"""
import logging
from datetime import timedelta

from petsense.agent import jobs, push
from petsense.db import session_scope, utc_now

logger = logging.getLogger(__name__)

DISPATCH_KIND = 'push_dispatch'
TICK_SECONDS = 30
DISPATCH_BATCH = 20


def handle_dispatch(job, now):
    """一轮派发。返回 next_run_at 保持作业 pending（周期自排）。"""
    push.recover_stuck(now)
    push.dispatch_due(now, limit=DISPATCH_BATCH)
    push.sweep_receipts(now)
    return {'next_run_at': now + timedelta(seconds=TICK_SECONDS)}


def register():
    """注册执行器（app 启动时调用；重复注册覆盖，测试隔离用）。"""
    jobs.register_handler(DISPATCH_KIND, handle_dispatch)


def start_push():
    """注册执行器并确保周期作业存在（幂等）。

    ``PETSENSE_SCHEDULER_DISABLED=1`` 时只注册不排队（测试与隔离实例）。
    """
    register()
    if not jobs.scheduler_enabled():
        return
    with session_scope() as s:
        jobs.enqueue(s, DISPATCH_KIND, {}, dedup_key='push:dispatch:loop',
                     next_run_at=utc_now() + timedelta(seconds=10))
