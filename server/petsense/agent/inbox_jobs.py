"""W12 收件箱投影作业（agent-w12-spec.md）：复用 W08 持久作业队列的周期作业形态。

投影本身不产生主动消息、不推系统通知：站内补入只写收件箱索引（spec §服务端契约）。
读路径也会做一次投影（幂等），本作业负责 App 未打开时的最终可见性。
"""
import logging
from datetime import timedelta

from petsense.agent import inbox, jobs
from petsense.db import session_scope, utc_now

logger = logging.getLogger(__name__)

PROJECT_KIND = 'inbox_project'
PROJECT_INTERVAL_S = 60


def handle_project(job, now):
    """一轮投影：把各宠物的来源投影进收件箱（幂等，不重复、不复活已读）。"""
    with session_scope() as s:
        created = inbox.project_all(s, now)
    if created:
        logger.info('inbox projection created %d items', created)
    return {'next_run_at': now + timedelta(seconds=PROJECT_INTERVAL_S)}


def register():
    """注册执行器（app 启动时调用；重复注册覆盖，测试隔离用）。"""
    jobs.register_handler(PROJECT_KIND, handle_project)


def start_inbox():
    """注册执行器并确保周期作业存在（幂等；调度关闭时只注册不排队）。

    启动时先做一次同步投影：此刻确立「启用水位」（D5 的启用时刻=部署/启动时刻），
    只有当下仍开放的设备告警被补入；此后的来源按 id 水位增量入箱。
    """
    register()
    if not jobs.scheduler_enabled():
        return
    with session_scope() as s:
        inbox.project_all(s, utc_now())
        jobs.enqueue(s, PROJECT_KIND, {}, dedup_key='inbox:project:loop',
                     next_run_at=utc_now() + timedelta(seconds=10))
