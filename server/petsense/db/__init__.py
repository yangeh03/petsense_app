"""统一数据层：engine + scoped_session + 迁移入口 + 时间序列化。

会话规则：HTTP 请求由 app teardown 调 ``Session.remove()``；
后台线程（MQTT/分类器）用 ``session_scope()`` 用完即摘除。
schema 唯一入口是 alembic（``init_db`` 幂等执行 upgrade head）。
"""

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker


def _default_url():
    from petsense import config
    return f'sqlite:///{config.RUNTIME_ROOT / "petsense.db"}'


_engine = None
_session_factory = None
Session = scoped_session(lambda: _session_factory())


def _build_engine(url):
    connect_args = {'check_same_thread': False, 'timeout': 20} if url.startswith('sqlite') else {}
    eng = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    if eng.dialect.name == 'sqlite':
        @event.listens_for(eng, 'connect')
        def _sqlite_pragma(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA synchronous=FULL')
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.close()
    return eng


def configure(url=None):
    """（重）构建 engine 与 session 工厂：测试与未来切 PG 用。"""
    global _engine, _session_factory
    Session.remove()
    _engine = _build_engine(url or os.environ.get('PETSENSE_DATABASE_URL') or _default_url())
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def engine():
    if _engine is None:
        configure()
    return _engine


@contextmanager
def session_scope():
    """事务作用域：成功提交、异常回滚、退出关闭并从线程注册表摘除。"""
    session = Session()
    try:
        with session.begin():
            yield session
    finally:
        Session.remove()


def iso_z(value):
    """API 时间字段唯一出口：与旧线上格式逐字节一致（毫秒 + Z 后缀）。"""
    moment = value.astimezone(timezone.utc)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def utc_now():
    return datetime.now(timezone.utc)


def dialect_insert(session, *entities):
    """SQLite/PG 双方言 upsert 基座（on_conflict_do_* 两方言同名 API）。"""
    from sqlalchemy.dialects import postgresql, sqlite
    dialect = postgresql if session.get_bind().dialect.name != 'sqlite' else sqlite
    return dialect.insert(*entities)


def _alembic_config():
    from alembic.config import Config
    return Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))


def init_db():
    """幂等执行 alembic upgrade head（alembic 环境在 Task 3 提供）。"""
    from alembic import command
    engine()
    command.upgrade(_alembic_config(), 'head')


def reset_schema():
    """测试用：downgrade base + upgrade head，得到全新 schema。"""
    from alembic import command
    from sqlalchemy import inspect
    engine()
    if inspect(engine()).has_table('alembic_version'):
        command.downgrade(_alembic_config(), 'base')
    command.upgrade(_alembic_config(), 'head')


# 模型注册（导入即挂到 Base.metadata；Task 2 提供后启用）
from petsense.db.base import Base  # noqa: F401,E402

# 过渡垫片：旧 db.py 的符号，未迁移域仍在用（Task 11 整体删除）
from petsense.db._legacy import app_connect, conn, cur, init_app_db, sqlite3  # noqa: F401,E402
