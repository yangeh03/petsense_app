# P0-1 数据层重建实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把三个 SQLite 库收敛为单一 SQLAlchemy 2.0 模型 + Alembic 管理的库（SQLite 现行 / PostgreSQL 未来切云双方言），消灭共享 cursor，API 行为逐字节等价。

**Architecture:** 新建 `petsense/db/` 包（engine + scoped_session + 15 张 ORM 表 + Alembic），旧 `db.py` 的 API 以 `_legacy.py` 垫片保留，各业务域逐个切换到新 API，最后一个任务删除垫片。任何中间提交都保持测试全绿。

**Tech Stack:** Python 3.12 / Flask 3.1 / SQLAlchemy 2.0 / Alembic / sqlite3（现有）+ psycopg2-binary（仅 CI 验证用）

**Spec:** `docs/superpowers/specs/2026-10-24-p0-sub1-datalayer-design.md`

## Global Constraints

- API 响应 JSON 形状不变；时间字段统一经 `iso_z()` 输出 `YYYY-MM-DDTHH:MM:SS.mmmZ`（客户端零改动）
- SQLite 与 PostgreSQL 双方言兼容（upsert 只用两方言同名的 `on_conflict_do_*`；禁用 SQLite-only 语法如 `COLLATE NOCASE`、`INSERT OR IGNORE`）
- 每个任务结束：`cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v` 全绿（等价于 CI 的 `python -m unittest discover -s server/tests -v`）
- 提交信息用中文，遵循仓库现有风格（`refactor:`/`test:`/`feat:` 前缀）
- 不改 `app/`（客户端）任何文件；不动进程模型（WS/SSE/MQTT 内存态原样）
- 旧库文件 `data.db`、`petsense_app.db`、`collection_data/collection.sqlite3` 不迁移不删除
- 依赖固定版本进 `requirements.txt` + `requirements.lock`（deploy/CI 都从 lock 安装）

---

### Task 1: db 包基础（engine/session/时间工具）+ 旧 API 垫片 + 依赖

**Files:**
- Delete: `server/petsense/db.py`（内容整体移入垫片）
- Create: `server/petsense/db/__init__.py`、`server/petsense/db/base.py`、`server/petsense/db/_legacy.py`
- Modify: `server/requirements.txt`、`server/requirements.lock`
- Modify: `.github/workflows/ci.yml`（unittest 步骤加 env）、`server/deploy/deploy_server.py:161`（unittest 子进程加 env）
- Test: `server/tests/test_db_base.py`

**Interfaces:**
- Produces（后续所有任务依赖）:
  - `db.Session`（scoped_session）、`db.session_scope()`（上下文管理器：成功提交/异常回滚/退出从线程注册表摘除）
  - `db.engine()`、`db.configure(url=None)`、`db.init_db()`、`db.reset_schema()`
  - `db.iso_z(dt: datetime) -> str`、`db.utc_now() -> datetime`
  - `db.dialect_insert(session, *entities)`（双方言 upsert 基座）
  - `db.Base`（来自 base.py）、`base.UTCDatetime`（跨方言时间类型）
  - 垫片转发旧符号：`db.conn`、`db.cur`、`db.app_connect()`、`db.init_app_db()`、`db.sqlite3`

- [ ] **Step 1: 安装并锁定依赖**

```bash
cd server && pip install SQLAlchemy alembic && pip freeze | grep -iE '^(SQLAlchemy|alembic|Mako|typing_extensions|typing-extensions|greenlet)='
```
把输出的精确版本追加进 `requirements.txt`（加注释 `# 数据层：ORM + 迁移框架`）和 `requirements.lock`（按字母序插入）。若 `greenlet`/`typing_extensions` 出现则一并进 lock。

- [ ] **Step 2: 写失败测试 `server/tests/test_db_base.py`**

```python
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Table, Column, Integer, MetaData
from sqlalchemy import text

from petsense.db import configure, engine, iso_z, session_scope, utc_now
from petsense.db.base import UTCDatetime


class DbBaseTests:
    def make_db(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        configure('sqlite:///' + holder.name + '/t.db')
        return engine()

    def test_iso_z_matches_wire_format(self):
        dt = datetime(2026, 10, 24, 4, 5, 6, 123456, tzinfo=timezone.utc)
        assert iso_z(dt) == '2026-10-24T04:05:06.123Z'

    def test_utc_now_is_aware(self):
        assert utc_now().tzinfo is not None

    def test_sqlite_pragmas_active(self):
        eng = self.make_db()
        with eng.connect() as conn:
            mode = conn.execute(text('PRAGMA journal_mode')).scalar()
            sync = conn.execute(text('PRAGMA synchronous')).scalar()
            fk = conn.execute(text('PRAGMA foreign_keys')).scalar()
        assert mode == 'wal'
        assert sync == 2  # FULL
        assert fk == 1

    def test_utc_datetime_roundtrip_keeps_awareness(self):
        metadata = MetaData()
        table = Table('probe', metadata, Column('id', Integer, primary_key=True),
                      Column('at', UTCDatetime, nullable=False))
        metadata.create_all(self.make_db())
        original = datetime(2026, 10, 24, 8, 0, 0, 500000, tzinfo=timezone.utc)
        with session_scope() as s:
            s.execute(table.insert().values(id=1, at=original))
        with session_scope() as s:
            loaded = s.execute(table.select()).mappings().first()['at']
        assert loaded.tzinfo is not None and loaded == original
```
测试类骨架用仓库现有风格包一层 `unittest.TestCase`（`assert` 换 `self.assertEqual/assertIsNotNone` 等，方法体同上）。文件末尾 `if __name__ == '__main__': unittest.main()`。

- [ ] **Step 3: 运行确认失败**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -p test_db_base.py -v
```
预期：ImportError（`petsense.db` 无 `configure` 等）。

- [ ] **Step 4: 实现 `server/petsense/db/base.py`**

```python
"""DeclarativeBase 与跨方言类型。"""
from datetime import datetime

from sqlalchemy import DateTime, TypeDecorator


class Base:
    """占位：真正的 DeclarativeBase 在 models 就位后由 Task 2 的 base.py 提供。"""
```
（Task 1 阶段 `base.py` 先只放 `UTCDatetime`；`Base` 在 Task 2 加入同文件，本任务先不导出。）

实际写入内容：

```python
"""DeclarativeBase 与跨方言类型。"""
from datetime import timezone

from sqlalchemy import DateTime, TypeDecorator


class UTCDatetime(TypeDecorator):
    """统一时间类型：进出都是 aware UTC。

    SQLite 方言原生 DateTime 不带时区（存取会丢 offset），这里在 bind 时
    归一成 naive-UTC 文本、result 时补回 UTC；PG 方言直接用 timestamptz。
    """
    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'sqlite':
            return dialect.type_descriptor(DateTime())
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is not None and dialect.name == 'sqlite' and value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and dialect.name == 'sqlite' and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
```

- [ ] **Step 5: 实现 `server/petsense/db/__init__.py`**

```python
"""统一数据层：engine + scoped_session + 迁移入口 + 时间序列化。

规则：HTTP 请求由 app teardown 调 Session.remove()；后台线程用完
session_scope() 自行摘除。schema 唯一入口是 alembic（init_db）。
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
    if Session is not None:
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
    """API 时间字段唯一出口：与旧线上格式逐字节一致。"""
    moment = value.astimezone(timezone.utc)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def utc_now():
    return datetime.now(timezone.utc)


def dialect_insert(session, *entities):
    """SQLite/PG 双方言 upsert 基座（on_conflict_do_* 两方言同名）。"""
    from sqlalchemy.dialects import postgresql, sqlite
    dialect = postgresql if session.get_bind().dialect.name != 'sqlite' else sqlite
    return dialect.insert(*entities)


def _alembic_config():
    from alembic.config import Config
    return Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))


def init_db():
    """幂等执行 alembic upgrade head（Task 3 提供 alembic 环境）。"""
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


# 过渡垫片：旧 db.py 的符号，未迁移域仍在用（Task 11 整体删除）
from petsense.db._legacy import app_connect, conn, cur, init_app_db, sqlite3  # noqa: F401,E402
```

- [ ] **Step 6: 实现 `server/petsense/db/_legacy.py`**

把现 `server/petsense/db.py` 的全部内容原样复制进来（`_connect_readings`、模块级 `conn, cur`、`app_connect`、`init_app_db`），仅改头部注释为「旧数据访问垫片：仅过渡期使用，Task 11 删除。读写旧库文件（data.db / petsense_app.db）。」然后 `git rm server/petsense/db.py`。

- [ ] **Step 7: CI 与 deploy 的 unittest 步骤加环境变量**

`.github/workflows/ci.yml` 的 `Python 语法和隔离测试` 步骤改为：
```yaml
      - name: Python 语法和隔离测试
        env:
          PETSENSE_MQTT_DISABLED: '1'
        run: |
          python -m compileall -q server
          python -m unittest discover -s server/tests -v
```
`deploy/deploy_server.py:161` 的 unittest 子进程加 `env={**os.environ, "PETSENSE_MQTT_DISABLED": "1"}`。
原因：新测试会 import `petsense.config`，无此变量时会在没有 `~/.petsense-mqtt-password` 的环境（CI/部署机测试步骤）炸掉。

- [ ] **Step 8: 全量测试 + 提交**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v
git add -A server/petsense/db server/requirements.txt server/requirements.lock .github/workflows/ci.yml server/deploy/deploy_server.py server/tests/test_db_base.py
git rm --cached server/petsense/db.py 2>/dev/null || true
git commit -m "feat: petsense/db 包统一 engine/session 与时间工具，旧 db.py 以垫片过渡"
```

---

### Task 2: ORM 模型（15 张表）

**Files:**
- Modify: `server/petsense/db/base.py`（加 `Base`）
- Create: `server/petsense/db/models/__init__.py`、`auth.py`、`pets.py`、`social.py`、`telemetry.py`、`collection.py`
- Modify: `server/petsense/db/__init__.py`（导入模型注册）
- Test: `server/tests/test_models_schema.py`

**Interfaces:**
- Produces: `Base`（`DeclarativeBase`）；模型类 `User`、`AuthSession`、`Pet`、`DailyStat`、`HomeLayout`、`Post`、`PostLike`、`Comment`、`Follow`、`Reading`、`ActionPrediction`、`CollectionSession`、`CollectionFrame`、`CollectionVideo`、`JournalCrc`
- 表名：`users`、`auth_sessions`、`pets`、`daily_stats`、`home_layout`、`posts`、`post_likes`、`comments`、`follows`、`readings`、`action_predictions`、`collection_sessions`、`collection_frames`、`collection_videos`、`journal_crc`

- [ ] **Step 1: 写失败测试（结构冒烟）**

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from petsense.db.base import Base
import petsense.db.models  # noqa: F401  触发注册


EXPECTED_TABLES = {'users', 'auth_sessions', 'pets', 'daily_stats', 'home_layout', 'posts',
                   'post_likes', 'comments', 'follows', 'readings', 'action_predictions',
                   'collection_sessions', 'collection_frames', 'collection_videos', 'journal_crc'}


class ModelSchemaTests(unittest.TestCase):
    def test_all_tables_registered(self):
        self.assertEqual(set(Base.metadata.tables), EXPECTED_TABLES)

    def test_key_columns(self):
        readings = Base.metadata.tables['readings']
        self.assertEqual([c.name for c in readings.primary_key], ['id'])
        frames = Base.metadata.tables['collection_frames']
        self.assertEqual(sorted(c.name for c in frames.primary_key), ['boot', 'device', 'seq'])
        users = Base.metadata.tables['users']
        self.assertIn('created_at', users.c)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**（`petsense.db.models` 不存在）

- [ ] **Step 3: `base.py` 顶部加 `Base`**

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

- [ ] **Step 4: `models/auth.py`**

```python
"""账号域模型。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text

from petsense.db.base import Base, UTCDatetime


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(40), nullable=False)
    display_name = Column(String(40), nullable=False)
    password_hash = Column(String(128), nullable=False)
    salt = Column(String(32), nullable=False)
    avatar_emoji = Column(String(16), nullable=False, default='🙂')
    bio = Column(Text, nullable=False, default='')
    created_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (
        Index('uq_users_username_lower', text('(lower(username))'), unique=True),
    )


class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    expires_at = Column(UTCDatetime, nullable=False)
```

- [ ] **Step 5: `models/pets.py`**

```python
"""宠物域模型。"""
from datetime import date, datetime

from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint

from petsense.db.base import Base, UTCDatetime


class Pet(Base):
    __tablename__ = 'pets'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    name = Column(String(40), nullable=False)
    species = Column(String(16), nullable=False, default='dog')
    breed = Column(String(80), nullable=False, default='')
    avatar_emoji = Column(String(16), nullable=False, default='🐾')
    device_id = Column(String(64), nullable=False, default='', index=True)
    petgif_id = Column(String(64), nullable=True)
    avatar_image = Column(String(128), nullable=True)
    created_at = Column(UTCDatetime, nullable=False)


class DailyStat(Base):
    __tablename__ = 'daily_stats'
    pet_id = Column(Integer, ForeignKey('pets.id'), primary_key=True)
    date = Column(Date, primary_key=True)
    sleep_seconds = Column(Integer, nullable=False, default=0)
    active_seconds = Column(Integer, nullable=False, default=0)
    hr_sum = Column(Float, nullable=False, default=0)
    hr_count = Column(Integer, nullable=False, default=0)
    samples = Column(Integer, nullable=False, default=0)


class HomeLayout(Base):
    __tablename__ = 'home_layout'
    pet_id = Column(Integer, ForeignKey('pets.id'), primary_key=True)
    rooms_json = Column(Text, nullable=False)
    safe_zone_json = Column(Text, nullable=False)
    anchors_json = Column(Text, nullable=False, default='[]')
    updated_at = Column(UTCDatetime, nullable=False)
```

- [ ] **Step 6: `models/social.py`**

```python
"""社区域模型。"""
from datetime import datetime

from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint

from petsense.db.base import Base, UTCDatetime


class Post(Base):
    __tablename__ = 'posts'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    pet_id = Column(Integer, nullable=True)
    image_path = Column(String(255), nullable=False, default='')
    caption = Column(Text, nullable=False, default='')
    location_text = Column(String(255), nullable=False, default='')
    media_url = Column(String(500), nullable=True)
    media_kind = Column(String(16), nullable=True)
    created_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (
        Index('ix_posts_user_id_id', 'user_id', text('id DESC')),
    )


class PostLike(Base):
    __tablename__ = 'post_likes'
    post_id = Column(Integer, ForeignKey('posts.id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    created_at = Column(UTCDatetime, nullable=False)


class Comment(Base):
    __tablename__ = 'comments'
    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(UTCDatetime, nullable=False)


class Follow(Base):
    __tablename__ = 'follows'
    follower_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    followee_id = Column(Integer, ForeignKey('users.id'), primary_key=True, index=True)
    created_at = Column(UTCDatetime, nullable=False)
```
（`from sqlalchemy import ... Index, text` 补进导入。）

- [ ] **Step 7: `models/telemetry.py`**

```python
"""遥测域模型：环冲原始帧 + 动作识别预测。"""
from sqlalchemy import BigInteger, Column, Float, Index, Integer, String, Text, text

from petsense.db.base import Base, UTCDatetime


class Reading(Base):
    __tablename__ = 'readings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    topic = Column(String(255), nullable=False)
    payload = Column(Text, nullable=False)
    received_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (
        Index('ix_readings_topic_received', 'topic', 'received_at'),
    )


class ActionPrediction(Base):
    __tablename__ = 'action_predictions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(64), nullable=False)
    event_id = Column(String(80), nullable=False)
    label = Column(String(64), nullable=False)
    action_key = Column(String(64), nullable=False)
    confidence = Column(Float, nullable=False)
    scores_json = Column(Text, nullable=False)
    sample_count = Column(Integer, nullable=False)
    window_start_ts_ms = Column(BigInteger, nullable=False)
    window_end_ts_ms = Column(BigInteger, nullable=False)
    model_version = Column(String(64), nullable=False)
    predicted_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (
        Index('action_predictions_device_time', 'device_id', text('id DESC')),
    )
```

- [ ] **Step 8: `models/collection.py`**

```python
"""采集域模型（文档型存储，结构与旧 collection.sqlite3 一致）。"""
from sqlalchemy import BigInteger, Column, Integer, String, Text, Index

from petsense.db.base import Base


class CollectionSession(Base):
    __tablename__ = 'collection_sessions'
    id = Column(String(80), primary_key=True)
    document = Column(Text, nullable=False)


class CollectionFrame(Base):
    __tablename__ = 'collection_frames'
    device = Column(String(80), primary_key=True)
    boot = Column(String(80), primary_key=True)
    seq = Column(Integer, primary_key=True)
    session = Column(String(80), nullable=False)
    mono_us = Column(BigInteger, nullable=False)
    utc_us = Column(BigInteger, nullable=True)
    received_us = Column(BigInteger, nullable=False)
    document = Column(Text, nullable=False)
    __table_args__ = (
        Index('collection_frames_session', 'session', 'boot', 'seq'),
    )


class CollectionVideo(Base):
    __tablename__ = 'collection_videos'
    id = Column(String(80), primary_key=True)
    session = Column(String(80), nullable=False)
    document = Column(Text, nullable=False)
    complete = Column(Integer, nullable=False, default=0)


class JournalCrc(Base):
    __tablename__ = 'journal_crc'
    device = Column(String(80), primary_key=True)
    boot = Column(String(80), primary_key=True)
    seq = Column(Integer, primary_key=True)
    crc = Column(Integer, nullable=False)
```

- [ ] **Step 9: `models/__init__.py` 与 db 包导入注册**

`models/__init__.py`：
```python
"""全部 ORM 模型（导入即注册到 Base.metadata）。"""
from petsense.db.models.auth import AuthSession, User
from petsense.db.models.collection import CollectionFrame, CollectionSession, CollectionVideo, JournalCrc
from petsense.db.models.pets import DailyStat, HomeLayout, Pet
from petsense.db.models.social import Comment, Follow, Post, PostLike
from petsense.db.models.telemetry import ActionPrediction, Reading

__all__ = ['User', 'AuthSession', 'Pet', 'DailyStat', 'HomeLayout', 'Post', 'PostLike',
           'Comment', 'Follow', 'Reading', 'ActionPrediction',
           'CollectionSession', 'CollectionFrame', 'CollectionVideo', 'JournalCrc']
```
`db/__init__.py` 在垫片导入之前加：
```python
from petsense.db.base import Base  # noqa: F401
import petsense.db.models  # noqa: F401
```

- [ ] **Step 10: 测试 + 提交**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v
git add server/petsense/db
git commit -m "feat: 15 张表 ORM 模型（时间 UTCDatetime、补外键与索引）"
```

---

### Task 3: Alembic 脚手架 + 0001 迁移 + init_db

**Files:**
- Create: `server/alembic.ini`、`server/alembic/env.py`、`server/alembic/script.py.mako`、`server/alembic/versions/0001_initial.py`
- Test: `server/tests/test_migrations.py`

**Interfaces:**
- Consumes: `db.Base.metadata`、`db.engine()`、`db.configure()`
- Produces: `init_db()` 可用（幂等 upgrade head）；`reset_schema()` 可用；`alembic upgrade head` / `downgrade base`

- [ ] **Step 1: 写失败测试**

```python
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect

from petsense import db

EXPECTED_TABLES = {'users', 'auth_sessions', 'pets', 'daily_stats', 'home_layout', 'posts',
                   'post_likes', 'comments', 'follows', 'readings', 'action_predictions',
                   'collection_sessions', 'collection_frames', 'collection_videos', 'journal_crc'}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        db.configure('sqlite:///' + holder.name + '/petsense.db')

    def test_upgrade_creates_all_tables(self):
        db.init_db()
        self.assertTrue(EXPECTED_TABLES <= set(inspect(db.engine()).get_table_names()))

    def test_downgrade_base_is_clean(self):
        db.init_db()
        db.reset_schema()
        tables = set(inspect(db.engine()).get_table_names())
        self.assertNotIn('users', tables)
        self.assertNotIn('alembic_version', tables)

    def test_init_db_is_idempotent(self):
        db.init_db()
        db.init_db()
        self.assertIn('users', inspect(db.engine()).get_table_names())


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**（无 alembic.ini）

- [ ] **Step 3: `server/alembic.ini`**

```ini
[alembic]
script_location = %(here)s/alembic
# URL 实际取自 petsense.db.engine()（见 env.py），这里仅为占位
sqlalchemy.url = sqlite:///petsense.db

[loggers]
keys = root

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 4: `server/alembic/env.py`**

```python
"""Alembic 环境：URL 一律来自 petsense.db.engine()，模型来自 Base.metadata。"""
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from petsense import db  # noqa: E402

target_metadata = db.Base.metadata


def run_migrations_offline():
    context.configure(url=str(db.engine().url), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        {'sqlalchemy.url': str(db.engine().url)},
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 5: `server/alembic/script.py.mako`**（Alembic 标准模板，原样使用）

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 6: 生成并校对 0001 迁移**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m alembic -c alembic.ini revision --autogenerate -m "initial schema"
```
生成后**人工校对**（对照 spec §5）：
- 15 张表全部出现；`uq_users_username_lower`、`ix_posts_user_id_id`、`ix_readings_topic_received`、`action_predictions_device_time`、`collection_frames_session` 等 Index 全部在
- 复合主键（post_likes/follows/daily_stats/collection 三张/journal_crc）正确
- 手动把 `revision` 改为 `'0001'`（文件名改 `0001_initial.py`）
- `downgrade()` 里 15 张 `op.drop_table` 按"被引用表后删"排序（先 posts 相关再 users 等）

- [ ] **Step 7: 测试 + 提交**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v
git add server/alembic server/alembic.ini server/tests/test_migrations.py
git commit -m "feat: Alembic 脚手架与 0001 全量建表迁移，init_db 幂等升级"
```

---

### Task 4: 模型行为测试（roundtrip / 外键 / upsert）

**Files:**
- Test: `server/tests/test_models.py`

**Interfaces:**
- Consumes: Task 2 模型、Task 3 `init_db`、Task 1 `session_scope`/`dialect_insert`
- Produces: `server/tests/helpers.py` 的 `fresh_db(testcase)`（后续域测试统一用它建库）

- [ ] **Step 1: 写 `server/tests/helpers.py`**

```python
"""测试公共：全新数据库（CI 的 PG job 用 PETSENSE_TEST_DATABASE_URL 覆盖）。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from petsense import db


def fresh_db(testcase):
    """为单个测试准备全新 schema；SQLite 用临时文件，PG 复位重建。"""
    url = os.environ.get('PETSENSE_TEST_DATABASE_URL')
    if url:
        db.configure(url)
        db.reset_schema()
        return
    holder = tempfile.TemporaryDirectory()
    testcase.addCleanup(holder.cleanup)
    db.configure('sqlite:///' + os.path.join(holder.name, 'petsense.db'))
    db.init_db()
```

- [ ] **Step 2: 写 `test_models.py`**

```python
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from petsense import db
from petsense.db.models import AuthSession, DailyStat, Follow, Post, PostLike, Reading, User
from tests.helpers import fresh_db


def make_user(session, username='mike', device_id=''):
    from petsense.db.models import Pet
    user = User(username=username, display_name=username, password_hash='x', salt='s',
                avatar_emoji='🙂', bio='', created_at=db.utc_now())
    session.add(user)
    session.flush()
    session.add(Pet(user_id=user.id, name='Max', species='dog', breed='', avatar_emoji='🐾',
                    device_id=device_id, created_at=db.utc_now()))
    return user


class ModelBehaviorTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)

    def test_duplicate_username_any_case_rejected(self):
        with db.session_scope() as s:
            make_user(s, 'mike')
        with self.assertRaises(IntegrityError):
            with db.session_scope() as s:
                make_user(s, 'MIKE')
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(User)).scalars().all().__len__(), 1)

    def test_like_requires_existing_post_and_user(self):
        with db.session_scope() as s:
            user = make_user(s)
            post = Post(user_id=user.id, image_path='', caption='hi', location_text='',
                        created_at=db.utc_now())
            s.add(post)
            s.flush()
            s.add(PostLike(post_id=post.id, user_id=user.id, created_at=db.utc_now()))
        with self.assertRaises(IntegrityError):
            with db.session_scope() as s:
                s.add(PostLike(post_id=99999, user_id=1, created_at=db.utc_now()))

    def test_daily_stats_upsert_accumulates(self):
        from petsense.db.models import Pet
        with db.session_scope() as s:
            user = make_user(s)
            pet_id = s.execute(select(Pet.id)).scalar_one()
        def upsert(sleep, active, hr_sum, hr_count):
            with db.session_scope() as s:
                stmt = db.dialect_insert(s, DailyStat).values(
                    pet_id=pet_id, date=date(2026, 10, 24), sleep_seconds=sleep,
                    active_seconds=active, hr_sum=hr_sum, hr_count=hr_count, samples=1)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['pet_id', 'date'],
                    set_={'sleep_seconds': DailyStat.__table__.c.sleep_seconds + stmt.excluded.sleep_seconds,
                          'active_seconds': DailyStat.__table__.c.active_seconds + stmt.excluded.active_seconds,
                          'hr_sum': DailyStat.__table__.c.hr_sum + stmt.excluded.hr_sum,
                          'hr_count': DailyStat.__table__.c.hr_count + stmt.excluded.hr_count,
                          'samples': DailyStat.__table__.c.samples + 1})
                s.execute(stmt)
        upsert(60, 30, 90.0, 1)
        upsert(60, 30, 110.0, 1)
        with db.session_scope() as s:
            row = s.get(DailyStat, (pet_id, date(2026, 10, 24)))
            self.assertEqual((row.sleep_seconds, row.samples, row.hr_count), (120, 2, 2))

    def test_reading_roundtrip_aware(self):
        moment = datetime(2026, 10, 24, 1, 2, 3, 400000, tzinfo=timezone.utc)
        with db.session_scope() as s:
            s.add(Reading(topic='petsense/sensor/x', payload='{}', received_at=moment))
        with db.session_scope() as s:
            loaded = s.execute(select(Reading.received_at)).scalar_one()
        self.assertEqual(loaded, moment)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 3: 运行至全绿后提交**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v
git add server/tests/test_models.py server/tests/helpers.py
git commit -m "test: 模型行为（小写唯一/外键/upsert 累加/时间往返）+ fresh_db 测试基建"
```

---

### Task 5: auth 域迁移

**Files:**
- Modify: `server/petsense/auth/routes.py`（全文件重写数据访问）
- Test: `server/tests/test_auth.py`

**Interfaces:**
- Consumes: `db.session_scope`、`db.utc_now`、`models.User/AuthSession`
- Produces（不变）: `app_user_from_request() -> dict|None`、`require_app_user() -> (user, error|None)`（pets/social/petgif 依赖这两个签名，禁止改动返回结构）

- [ ] **Step 1: 写失败测试 `test_auth.py`**

```python
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import db
from petsense.auth import routes as auth_routes
from petsense.db.models import AuthSession, User
from tests.helpers import fresh_db


class AuthTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(auth_routes.bp)
        self.client = app.test_client()

    def test_register_login_me_logout_flow(self):
        reply = self.client.post('/api/app/auth/register', json={
            'username': 'petsense', 'display_name': 'Max 爸爸', 'password': '123456', 'avatarEmoji': '🧑‍🌾'})
        self.assertEqual(reply.status_code, 201)
        token = reply.get_json()['token']
        headers = {'X-Petsense-Session': token}
        self.assertEqual(self.client.get('/api/app/auth/me', headers=headers)
                         .get_json()['user']['displayName'], 'Max 爸爸')
        reply = self.client.post('/api/app/auth/login', json={'username': 'PETSENSE', 'password': '123456'})
        self.assertEqual(reply.status_code, 200)
        self.client.post('/api/app/auth/logout', headers=headers)
        self.assertIsNone(self.client.get('/api/app/auth/me', headers=headers).get_json()['user'])

    def test_duplicate_register_409(self):
        body = {'username': 'a_user', 'display_name': 'a', 'password': '123456'}
        self.assertEqual(self.client.post('/api/app/auth/register', json=body).status_code, 201)
        self.assertEqual(self.client.post('/api/app/auth/register', json=body).status_code, 409)

    def test_wrong_password_and_expired_session(self):
        self.client.post('/api/app/auth/register',
                         json={'username': 'a_user', 'display_name': 'a', 'password': '123456'})
        self.assertEqual(self.client.post('/api/app/auth/login',
                         json={'username': 'a_user', 'password': 'wrong!'}).status_code, 401)
        with db.session_scope() as s:
            user = s.execute(User.__table__.select()).mappings().first()
            s.add(AuthSession(token_hash='e' * 64, user_id=user['id'],
                             expires_at=db.utc_now() - timedelta(days=1)))
        reply = self.client.get('/api/app/auth/me', headers={'X-Petsense-Session': 'expired-token'})
        self.assertIsNone(reply.get_json()['user'])


if __name__ == '__main__':
    unittest.main()
```
（`'expired-token'` 的 sha256 需与 token_hash 一致——实现时在测试里直接 `import hashlib; hashlib.sha256(b'expired-token').hexdigest()` 计算。）

- [ ] **Step 2: 运行确认失败**（新库没有 users 表，接口 500）

- [ ] **Step 3: 重写 `auth/routes.py` 数据访问（路由/校验/文案全部不动）**

新导入与核心函数：
```python
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from flask import Blueprint, request
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from petsense import config
from petsense.db import Session, iso_z, session_scope, utc_now
from petsense.db.models import AuthSession, User

bp = Blueprint('auth', __name__, url_prefix='/api/app/auth')


def password_digest(password, salt_hex):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), 180000).hex()


def app_user_from_request():
    token = request.headers.get(config.APP_TOKEN_HEADER, '')
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with session_scope() as s:
        row = s.execute(
            select(User.id, User.username, User.display_name, User.avatar_emoji)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(AuthSession.token_hash == token_hash, AuthSession.expires_at > utc_now())
        ).fetchone()
        return {'id': row.id, 'username': row.username,
                'display_name': row.display_name, 'avatar_emoji': row.avatar_emoji} if row else None


def require_app_user():
    user = app_user_from_request()
    if not user:
        return None, ({'success': False, 'message': '请先登录'}, 401)
    return user, None


def issue_app_session(user_id):
    token = secrets.token_urlsafe(32)
    expires = utc_now() + timedelta(days=config.APP_SESSION_DAYS)
    with session_scope() as s:
        s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                          user_id=user_id, expires_at=expires))
    return token
```
`app_register`：建 `User(...created_at=utc_now())` → `s.add` → `s.flush()` 拿 `user.id`；整段 `try: with session_scope() ... except IntegrityError: return 409`。其余文案/校验/响应结构逐行保留。
`app_login`：`select(User...).where(func.lower(User.username) == username.strip().lower())` 替代 `COLLATE NOCASE`；`hmac.compare_digest` 保留。
`app_logout`：`delete(AuthSession).where(AuthSession.token_hash == ...)`。
`app_me`：不变。

- [ ] **Step 4: 测试 + 提交**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v
git add server/petsense/auth server/tests/test_auth.py
git commit -m "refactor: auth 域迁统一数据层（lower 唯一索引、DateTime 过期比较）"
```

---

### Task 6: telemetry 域迁移（store 水位裁剪 / stats / actions / realtime）

**Files:**
- Modify: `server/petsense/telemetry/mqtt_ingest.py`（`store()` + `on_message` 两处调用点）
- Modify: `server/petsense/stats.py`
- Modify: `server/petsense/actions/service.py`（`persist_action_prediction`、`action_rows`，删 `import sqlite3`）
- Modify: `server/petsense/telemetry/realtime.py`（SSE 首帧、`/api/latest-full`、`/api/stats`）
- Test: `server/tests/test_telemetry.py`

**Interfaces:**
- Consumes: `db.session_scope/utc_now/iso_z/dialect_insert`、`models.Reading/ActionPrediction/DailyStat/Pet`
- Produces: `store(topic: str, payload: str) -> datetime`（**返回值从 str 变 aware datetime**，本文件内 `on_message` 同步适配：packet 用 `iso_z(ts)`、`recorder.append_cloud_experiment_frame(topic, frame, iso_z(ts))`——recorder 的 CSV 格式因此不变）
- Produces: `stats.update_daily_stats(device_id, frame, received_at: datetime)`（内部签名变 datetime；mqtt_ingest 调用点同步改）

- [ ] **Step 1: 写失败测试 `test_telemetry.py`**

```python
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from sqlalchemy import func, select

from petsense import config, db
from petsense.db.models import Reading
from petsense.telemetry import mqtt_ingest, realtime
from tests.helpers import fresh_db


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)

    def test_store_returns_aware_and_trims_by_watermark(self):
        moment = mqtt_ingest.store('petsense/sensor/esp32-001', '{"a":1}')
        self.assertIsNotNone(moment.tzinfo)
        with patch.object(config, 'MAX_HISTORY', 10):
            mqtt_ingest._trim_counter = 499  # 下一次插入触发裁剪
            for i in range(1):
                mqtt_ingest.store('t', f'{{"i":{i}}}')
        with db.session_scope() as s:
            count = s.execute(select(func.count()).select_from(Reading)).scalar_one()
        self.assertLessEqual(count, 10 + 500)

    def test_latest_full_shape_unchanged(self):
        moment = mqtt_ingest.store('petsense/sensor/x', '{"heartRate": 88}')
        app = Flask(__name__)
        realtime.sock.init_app(app)
        app.register_blueprint(realtime.bp)
        client = app.test_client()
        payload = client.get('/api/latest-full').get_json()
        self.assertEqual(payload['count'], 1)
        row = payload['data'][0]
        self.assertEqual(row['topic'], 'petsense/sensor/x')
        self.assertEqual(row['payload'], {'heartRate': 88})
        self.assertRegex(row['received_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')

    def test_stats_endpoint_counts(self):
        mqtt_ingest.store('t', '{}')
        app = Flask(__name__)
        realtime.sock.init_app(app)
        app.register_blueprint(realtime.bp)
        stats = app.test_client().get('/api/stats').get_json()
        self.assertEqual(stats['total'], 1)
        self.assertRegex(stats['latest'], r'Z$')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**（store 还在写旧库/返回 str）

- [ ] **Step 3: 重写 `mqtt_ingest.store` + 裁剪 + 调用点**

```python
_trim_counter = 0


def store(topic, payload):
    """落库一条原始帧，返回 aware UTC 时间（调用方用 iso_z() 序列化）。"""
    received = utc_now()
    with session_scope() as s:
        s.execute(insert(Reading).values(topic=topic, payload=payload, received_at=received))
    global _trim_counter
    _trim_counter += 1
    if _trim_counter >= 500:
        _trim_counter = 0
        with session_scope() as s:
            max_id = s.execute(select(func.max(Reading.id))).scalar_one()
            if max_id is not None:
                s.execute(delete(Reading).where(Reading.id <= max_id - config.MAX_HISTORY))
    return received
```
导入区：`from sqlalchemy import delete, func, insert, select`、`from petsense.db import iso_z, session_scope, utc_now`、`from petsense.db.models import Reading`；删除 `from petsense import config, db, stats` 中的 `db`。
`on_message` 中四处使用点：`ts = store(...)` 后，`packet = json.dumps({'topic': ..., 'payload': ..., 'received_at': iso_z(ts)})`；`recorder.append_cloud_experiment_frame(topic, frame, iso_z(ts))`；`stats.update_daily_stats(device, frame, ts)`（直接传 datetime）。

- [ ] **Step 4: 重写 `stats.py` 数据访问**

```python
def update_daily_stats(device_id, frame, received_at):
    '''按帧累加今日睡眠/活跃秒数与平均心率（received_at 为 aware UTC）。'''
    try:
        if not device_id:
            return
        utc_dt = received_at if received_at.tzinfo else received_at.replace(tzinfo=timezone.utc)
        imu = frame.get('imu') or {}
        accel = imu.get('accel') if imu.get('ok') else None
        if not (isinstance(accel, list) and len(accel) == 3 and all(isinstance(v, (int, float)) for v in accel)):
            return
        delta = abs(math.sqrt(sum(float(v) ** 2 for v in accel)) - 1.0)
        ts_ms = frame.get('ts_ms')
        if not isinstance(ts_ms, (int, float)):
            return
        previous = stats_last_ts.get(device_id)
        stats_last_ts[device_id] = ts_ms
        if previous is None or ts_ms <= previous:
            return
        interval = min(max(ts_ms - previous, 500), 10_000)
        sleep_inc = interval if utc_dt.hour in config.SLEEP_UTC_HOURS and delta < config.STILL_DELTA_G else 0
        active_inc = interval if delta > config.ACTIVE_DELTA_G else 0
        health = frame.get('health') or {}
        hr = health.get('heartRate') if health.get('valid') else 0
        hr_sum = float(hr) if isinstance(hr, (int, float)) and hr > 0 else 0.0
        hr_count = 1 if hr_sum > 0 else 0
        with session_scope() as s:
            pet_id = s.execute(select(Pet.id).where(Pet.device_id == device_id)
                               .order_by(Pet.id).limit(1)).scalar_one_or_none()
            if pet_id is None:
                return
            stmt = db_mod.dialect_insert(s, DailyStat).values(
                pet_id=pet_id, date=shanghai_now(utc_dt).date(),
                sleep_seconds=sleep_inc, active_seconds=active_inc,
                hr_sum=hr_sum, hr_count=hr_count, samples=1)
            stmt = stmt.on_conflict_do_update(
                index_elements=['pet_id', 'date'],
                set_={'sleep_seconds': DailyStat.__table__.c.sleep_seconds + stmt.excluded.sleep_seconds,
                      'active_seconds': DailyStat.__table__.c.active_seconds + stmt.excluded.active_seconds,
                      'hr_sum': DailyStat.__table__.c.hr_sum + stmt.excluded.hr_sum,
                      'hr_count': DailyStat.__table__.c.hr_count + stmt.excluded.hr_count,
                      'samples': DailyStat.__table__.c.samples + 1})
            s.execute(stmt)
    except Exception as exc:
        print(f'[APP-STATS] {exc}')
```
（`from petsense import db as db_mod`、`from petsense.db import session_scope`、`from petsense.db.models import DailyStat, Pet`、`from sqlalchemy import select`；`remember_uwb` 不动。）

- [ ] **Step 5: 重写 `actions/service.py` 两函数**

```python
def persist_action_prediction(prediction):
    with session_scope() as s:
        s.execute(insert(ActionPrediction).values(
            device_id=prediction['deviceId'], event_id=prediction['eventId'], label=prediction['label'],
            action_key=prediction['actionKey'], confidence=prediction['confidence'],
            scores_json=json.dumps(prediction['scores'], ensure_ascii=False),
            sample_count=prediction['sampleCount'],
            window_start_ts_ms=prediction['windowStartTsMs'], window_end_ts_ms=prediction['windowEndTsMs'],
            model_version=prediction['modelVersion'], predicted_at=prediction['predictedAt'],
        ))


def action_rows(limit=100, device_id=None):
    stmt = select(ActionPrediction).order_by(ActionPrediction.id.desc()).limit(limit)
    if device_id:
        stmt = stmt.where(ActionPrediction.device_id == device_id)
    with session_scope() as s:
        rows = s.execute(stmt).scalars().all()
    return [{
        'deviceId': row.device_id, 'eventId': row.event_id, 'label': row.label, 'actionKey': row.action_key,
        'confidence': row.confidence, 'scores': json.loads(row.scores_json), 'sampleCount': row.sample_count,
        'windowStartTsMs': row.window_start_ts_ms, 'windowEndTsMs': row.window_end_ts_ms,
        'modelVersion': row.model_version,
        'predictedAt': iso_z(row.predicted_at) if row.predicted_at.tzinfo else row.predicted_at,
        'source': 'random_forest_1s',
    } for row in rows]
```
注意：旧库 `predicted_at` 由分类器传 ISO 字符串（`classifier` 生成 `datetime.now(timezone.utc).isoformat()`? 实现时确认 `prediction['predictedAt']` 的类型——若是字符串，模型列收字符串会崩，需在 persist 处 `datetime.fromisoformat(...)` 转 aware）。实现时打开 `classifier.py` 核对并转换。

- [ ] **Step 6: 重写 `realtime.py` 三处读**

```python
@bp.route('/api/stream')
def sse_stream():
    q = fwd.add_sse()
    with session_scope() as s:
        row = s.execute(select(Reading.topic, Reading.payload, Reading.received_at)
                        .order_by(Reading.id.desc()).limit(1)).fetchone()
    def generate():
        try:
            if row:
                yield f'data: {json.dumps({"topic": row.topic, "payload": row.payload, "received_at": iso_z(row.received_at)})}\n\n'
            else:
                yield ': waiting\n\n'
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f'data: {msg}\n\n'
                except queue.Empty:
                    yield ': heartbeat\n\n'
        except GeneratorExit:
            pass
        finally:
            fwd.remove_sse(q)
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Access-Control-Allow-Origin': '*'})


@bp.route('/api/latest-full')
def api_latest_full():
    with session_scope() as s:
        rows = s.execute(select(Reading.topic, Reading.payload, Reading.received_at)
                         .order_by(Reading.id.desc()).limit(200)).fetchall()
    data = []
    for topic, payload_text, received_at in rows:
        try:
            payload = json.loads(payload_text)
        except Exception:
            payload = {'_raw': payload_text}
        data.append({'topic': topic, 'payload': payload, 'received_at': iso_z(received_at)})
    return {'count': len(data), 'data': data}


@bp.route('/api/stats')
def api_stats():
    with session_scope() as s:
        total, latest = s.execute(select(func.count(), func.max(Reading.received_at))).fetchone()
    return {'total': total, 'latest': iso_z(latest) if latest else None,
            'sse_clients': len(fwd.sse_queues), 'ws_clients': len(ws_clients)}
```
导入：`from sqlalchemy import func, select`、`from petsense.db import Session, iso_z, session_scope`、`from petsense.db.models import Reading`。

- [ ] **Step 7: 测试 + 提交**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v
git add server/petsense/telemetry server/petsense/stats.py server/petsense/actions server/tests/test_telemetry.py
git commit -m "refactor: 遥测域迁统一数据层，环冲改水位裁剪，stats 复用线程会话"
```

---

### Task 7: pets 域迁移

**Files:**
- Modify: `server/petsense/pets/routes.py`
- Test: `server/tests/test_pets.py`

**Interfaces:**
- Consumes: Task 5 `require_app_user`、Task 6 新库 `Reading`
- Produces（不变，social/user 页依赖）: `pet_latest_telemetry(device_id) -> dict|None`（键与值形状不变，`receivedAt` 为 Z 格式字符串）

- [ ] **Step 1: 写失败测试**

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import db
from petsense.db.models import Pet, Reading, User
from petsense.pets import routes as pets_routes
from tests.helpers import fresh_db


def seed_user_with_pet(device_id='esp32-001'):
    with db.session_scope() as s:
        user = User(username='u1', display_name='u1', password_hash='x', salt='s',
                    avatar_emoji='🙂', bio='', created_at=db.utc_now())
        s.add(user)
        s.flush()
        s.add(Pet(user_id=user.id, name='Max', species='dog', breed='', avatar_emoji='🐾',
                  device_id=device_id, created_at=db.utc_now()))
        return user.id


class PetsTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(pets_routes.bp)
        self.client = app.test_client()

    def register_and_header(self):
        from petsense.db.models import AuthSession
        import hashlib, secrets
        token = secrets.token_urlsafe(16)
        uid = seed_user_with_pet()
        with db.session_scope() as s:
            s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                              user_id=uid, expires_at=db.utc_now()))
        return {'X-Petsense-Session': token}

    def test_pets_list_shape(self):
        headers = self.register_and_header()
        reply = self.client.get('/api/app/pets', headers=headers).get_json()
        pet = reply['pets'][0]
        self.assertEqual(pet['name'], 'Max')
        self.assertIn('created_at', pet)
        self.assertIn('avatarImageUrl', pet)

    def test_dashboard_merges_telemetry(self):
        headers = self.register_and_header()
        with db.session_scope() as s:
            s.add(Reading(topic='petsense/sensor/esp32-001',
                          payload='{"health":{"valid":true,"heartRate":120,"spo2":98,"bodyTemp":38.5},"battery":{"percent":77}}',
                          received_at=db.utc_now()))
        reply = self.client.get('/api/app/dashboard', headers=headers).get_json()
        self.assertEqual(reply['latest']['heartRate'], 120)
        self.assertTrue(reply['latest']['online'])
        self.assertRegex(reply['latest']['receivedAt'], r'Z$')

    def test_uwb_latest_without_frame(self):
        from petsense import stats as stats_module
        stats_module.last_uwb_frame = None
        self.assertIsNone(self.client.get('/api/app/uwb/latest').get_json()['frame'])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 重写数据访问（路由与文案不动）**

`pet_latest_telemetry`：
```python
def pet_latest_telemetry(device_id):
    if not device_id:
        return None
    with session_scope() as s:
        rows = s.execute(select(Reading.payload, Reading.received_at)
                         .where(Reading.topic == f'petsense/sensor/{device_id}')
                         .order_by(Reading.id.desc()).limit(5)).fetchall()
    for payload_text, received_at in rows:
        try:
            payload = json.loads(payload_text)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        health = payload.get('health') or {}
        battery = payload.get('battery') or {}
        age = time.time() - received_at.timestamp()
        return {
            'heartRate': health.get('heartRate') or 0,
            'spo2': health.get('spo2') or 0,
            'bodyTemp': health.get('bodyTemp') or 0,
            'envTemp': health.get('envTemp') or 0,
            'respiration': health.get('respiration') or 0,
            'hrvSdnn': health.get('hrvSdnn') or 0,
            'microCir': health.get('microCir') or 0,
            'fatigue': health.get('fatigue') or 0,
            'systolic': health.get('systolic') or 0,
            'diastolic': health.get('diastolic') or 0,
            'valid': bool(health.get('valid')),
            'batteryPercent': round(battery.get('percent') or 0),
            'online': age < config.DEVICE_ONLINE_TIMEOUT_S,
            'receivedAt': iso_z(received_at),
        }
    return None
```
`pet_today_stats`：
```python
def pet_today_stats(pet_id):
    with session_scope() as s:
        row = s.execute(select(DailyStat.sleep_seconds, DailyStat.active_seconds,
                               DailyStat.hr_sum, DailyStat.hr_count)
                        .where(DailyStat.pet_id == pet_id,
                               DailyStat.date == shanghai_now().date())).fetchone()
    ...  # 与原函数完全相同的 None/round 逻辑
```
`app_pets` GET：`select(Pet).where(Pet.user_id == user['id']).order_by(Pet.id)` → 序列化：
```python
def pet_dict(pet):
    item = {'id': pet.id, 'user_id': pet.user_id, 'name': pet.name, 'species': pet.species,
            'breed': pet.breed, 'avatar_emoji': pet.avatar_emoji, 'device_id': pet.device_id,
            'petgif_id': pet.petgif_id, 'avatar_image': pet.avatar_image,
            'created_at': iso_z(pet.created_at)}
    telemetry = pet_latest_telemetry(pet.device_id)
    item['batteryPercent'] = telemetry.get('batteryPercent') if telemetry else None
    item['online'] = telemetry.get('online') if telemetry else False
    item['avatarImageUrl'] = '/uploads/' + pet.avatar_image if pet.avatar_image else None
    return item
```
POST：`s.add(Pet(...created_at=utc_now())); s.flush()` 拿 `pet.id` 返回 201（校验文案不变）。
avatar-image / petgif 绑定：`select(Pet.id).where(Pet.id == pet_id, Pet.user_id == user['id'])` 判空后 `update(Pet).where(...).values(avatar_image=filename / petgif_id=petgif_id)`。
dashboard：同样的两条 select（pet_id 精确 / `order_by(Pet.id).limit(1)`），`pet = pet_dict(pet)`，`avatarImageUrl` 覆盖逻辑保留。
`app_uwb_latest` 不动（纯内存）。
导入：`from sqlalchemy import select, update`、`from petsense.db import iso_z, session_scope, utc_now`、`from petsense.db.models import DailyStat, Pet, Reading`。

- [ ] **Step 4: 测试 + 提交**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v
git add server/petsense/pets server/tests/test_pets.py
git commit -m "refactor: pets 域迁统一数据层（telemetry/dashboard/档案）"
```

---

### Task 8: social 域迁移 + 线上时间格式测试

**Files:**
- Modify: `server/petsense/social/routes.py`
- Test: `server/tests/test_social.py`

**Interfaces:**
- Consumes: Task 5 `app_user_from_request/require_app_user`、Task 7 `pet_latest_telemetry`
- Produces（不变）: `/feed`、`/posts`、`/posts/<id>/like|comments`、`/users/<id>[/followers|/following|/follow]` 全部响应形状

- [ ] **Step 1: 写失败测试（覆盖全闭环 + wire 格式断言）**

```python
import sys
import unittest
import hashlib
import secrets
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import db
from petsense.db.models import AuthSession, Pet, Post, User
from petsense.social import routes as social_routes
from tests.helpers import fresh_db


def make_user(session, username):
    user = User(username=username, display_name=username, password_hash='x', salt='s',
                avatar_emoji='🙂', bio='', created_at=db.utc_now())
    session.add(user)
    session.flush()
    return user


class SocialTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(social_routes.bp)
        self.client = app.test_client()
        self.tokens = {}
        with db.session_scope() as s:
            for name in ('alice', 'bob'):
                user = make_user(s, name)
                token = secrets.token_urlsafe(16)
                s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                  user_id=user.id, expires_at=db.utc_now()))
                self.tokens[name] = (user.id, token)

    def header(self, name):
        return {'X-Petsense-Session': self.tokens[name][1]}

    def test_post_like_comment_follow_wire_format(self):
        reply = self.client.post('/api/app/posts', headers=self.header('alice'),
                                 data={'caption': '你好 #标签', 'location': '滨江公园'})
        self.assertEqual(reply.status_code, 201)
        post_id = reply.get_json()['postId']
        # 点赞 toggle
        liked = self.client.post(f'/api/app/posts/{post_id}/like', headers=self.header('bob')).get_json()
        self.assertTrue(liked['liked'] and liked['likes'] == 1)
        # 评论
        self.assertEqual(self.client.post(f'/api/app/posts/{post_id}/comments',
                         headers=self.header('bob'), json={'body': '可爱'}).status_code, 201)
        feed = self.client.get('/api/app/feed').get_json()['posts']
        item = feed[0]
        self.assertEqual(item['caption'], '你好 #标签')
        self.assertEqual(item['comments_count'], 1)
        self.assertTrue(item['likes'] == 1 and not item['isMine'])
        self.assertRegex(item['created_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')
        # 关注 toggle + 主页
        self.client.post(f'/api/app/users/{self.tokens["alice"][0]}/follow', headers=self.header('bob'))
        profile = self.client.get(f'/api/app/users/{self.tokens["alice"][0]}').get_json()['profile']
        self.assertEqual(profile['followers'], 1)
        self.assertEqual(profile['displayName'], 'alice')
        # 删除自己的帖子
        self.assertEqual(self.client.delete(f'/api/app/posts/{post_id}',
                         headers=self.header('bob')).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/app/posts/{post_id}',
                         headers=self.header('alice')).status_code, 200)
        self.assertEqual(self.client.get('/api/app/feed').get_json()['posts'], [])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 重写 `post_rows` 与全部路由数据访问（文案/校验/LIMIT 不变）**

```python
def post_rows(viewer_id, user_id=None):
    likes_count = select(func.count()).select_from(PostLike).where(PostLike.post_id == Post.id).scalar_subquery()
    comments_count = select(func.count()).select_from(Comment).where(Comment.post_id == Post.id).scalar_subquery()
    liked_by_me = select(literal(1)).where(PostLike.post_id == Post.id,
                                           PostLike.user_id == viewer_id).exists()
    stmt = (
        select(Post.id, Post.caption, Post.location_text, Post.image_path, Post.created_at,
               Post.media_url, Post.media_kind,
               User.id.label('user_id'), User.display_name, User.avatar_emoji,
               Pet.id.label('pet_id'), Pet.name.label('pet_name'), Pet.avatar_emoji.label('pet_emoji'),
               likes_count.label('likes'), comments_count.label('comments_count'),
               liked_by_me.label('liked_by_me'))
        .join(User, User.id == Post.user_id)
        .outerjoin(Pet, Pet.id == Post.pet_id)
        .order_by(Post.id.desc()).limit(60))
    if user_id is not None:
        stmt = stmt.where(Post.user_id == user_id)
    with session_scope() as s:
        rows = s.execute(stmt).mappings().fetchall()
    items = []
    for row in rows:
        item = dict(row)
        image_path = item.pop('image_path') or ''
        item['imageUrl'] = f'/uploads/{image_path}' if image_path else ''
        item['mediaUrl'] = item.pop('media_url')
        item['mediaKind'] = item.pop('media_kind')
        item['created_at'] = iso_z(item['created_at'])
        item['isMine'] = item['user_id'] == viewer_id
        items.append(item)
    return items


@bp.route('/feed')
def app_feed():
    viewer = app_user_from_request()
    user_id = request.args.get('user_id', type=int)
    items = post_rows(viewer['id'] if viewer else -1, user_id)
    return {'success': True, 'posts': items}
```
其余路由按同模式改（全部保持原文案/状态码/LIMIT）：
- `app_create_post`：宠物归属 `select(Pet.id).where(Pet.id == pet_id, Pet.user_id == user['id'])`；插入 `Post(..., created_at=utc_now())` + flush。
- `app_delete_post`：一个 `session_scope` 内完成查作者 + 三条 `delete()`（comments、post_likes、posts）。
- `app_like_post`：`select(PostLike).where(...)` 判存在 → delete / `db.dialect_insert(s, PostLike).values(...).on_conflict_do_nothing(index_elements=['post_id', 'user_id'])`；计数 `select(func.count()).select_from(PostLike).where(...)`。
- `app_post_comments` GET：`select(Comment.id, Comment.body, Comment.created_at, User.id.label('user_id'), User.display_name, User.avatar_emoji).join(User, ...).where(Comment.post_id == post_id).order_by(Comment.id).limit(200)` → mappings，`created_at` 过 `iso_z`；POST：帖子存在检查 + `Comment(..., created_at=utc_now())`。
- `app_user_profile`：一个 session 内 user 行 + 三个 count + is_following exists + pets 列表；`created_at`/`displayName`/`avatarEmoji` 键转换保持。
- `app_user_followers/following`：exists 子查询 + `order_by(Follow.created_at.desc()).limit(200)`，join 方向按原 SQL。
- `app_follow_user`：存在检查 + delete / `dialect_insert(...).on_conflict_do_nothing(index_elements=['follower_id', 'followee_id'])`。
导入：`from sqlalchemy import delete, func, literal, select`、`from petsense.db import dialect_insert, iso_z, session_scope, utc_now`、`from petsense.db.models import Comment, Follow, Pet, Post, PostLike, User`。

- [ ] **Step 4: 测试 + 提交**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v
git add server/petsense/social server/tests/test_social.py
git commit -m "refactor: social 域迁统一数据层，feed 子查询计数与 Z 格式线上时间"
```

---

### Task 9: collection 域迁移（CollectionStore）

**Files:**
- Modify: `server/petsense/collection/api.py`（`__init__`、`db()` 移除、`ingest`、sessions/videos 各路由）
- Test: `server/tests/test_collection.py`（更新到新 API，用例与断言不放松）

**Interfaces:**
- Consumes: `db.session_scope/dialect_insert`、`models.Collection*`
- Produces: `CollectionStore(root, token, publish)`——**签名不变**（mqtt_ingest.py:160 的装配零改动），内部存储改统一库；`self.db()` 上下文管理器删除（测试改用 `session_scope`）

- [ ] **Step 1: 更新既有测试到新 API（先改测试再改实现——迁移期测试先红）**

setUp 增加：
```python
from tests.helpers import fresh_db
fresh_db(self)
```
`with self.store.db() as db:` 的三处改为：
```python
from petsense.db import session_scope
from sqlalchemy import func, select
from petsense.db.models import CollectionFrame
with session_scope() as s:
    s.execute(select(func.count()).select_from(CollectionFrame)).scalar_one()
```
（`publish` 回调里的 count 检查、`test_commit_before_ack...`、`test_delta_batch...` 同模式；delta 用例的 `SELECT document FROM frames WHERE seq=2` 改 `select(CollectionFrame.document).where(CollectionFrame.seq == 2)`。）

- [ ] **Step 2: 运行确认失败**（CollectionStore 还在写 collection.sqlite3，新库无表）

- [ ] **Step 3: 重写 CollectionStore 存储层（协议语义逐行保留）**

- 删除 `db()` 上下文管理器与 `import sqlite3`；构造器去掉建表 `executescript`（schema 由 init_db 建），保留 `root.mkdir`、锁、`latest`、`media_locks`。
- `ingest` 的 collection_v2 分支：`with self.lock:` 保留；每个 frame：
```python
existing = s.execute(select(CollectionFrame.document)
                     .where(CollectionFrame.device == device, CollectionFrame.boot == boot,
                            CollectionFrame.seq == seq)).scalar_one_or_none()
if existing is not None and json.loads(existing) != frame:
    raise ValueError('sequence reused with different payload')
s.execute(db.dialect_insert(s, CollectionFrame).values(
    device=device, boot=boot, seq=seq, session=frame.get('session_id', ''),
    mono_us=frame['mono_us'], utc_us=frame.get('utc_us'), received_us=received,
    document=doc).on_conflict_do_nothing(index_elements=['device', 'boot', 'seq']))
if crcs is not None:
    stmt = db.dialect_insert(s, JournalCrc).values(device=device, boot=boot, seq=seq, crc=crcs[index])
    s.execute(stmt.on_conflict_do_update(index_elements=['device', 'boot', 'seq'],
                                         set_={'crc': stmt.excluded.crc}))
```
整批在一个 `session_scope`（= 一个事务，ACK 前已提交——保住持久化契约）。
- `create_session`：`select(CollectionSession.document).where(id==sid)` 幂等检查 + `dialect_insert(...).on_conflict_do_nothing`；文件写入逻辑不动。
- `control`：`select(CollectionSession.document)...`。
- `quality`：frames 查询 `select(CollectionFrame.boot, CollectionFrame.seq, CollectionFrame.mono_us, CollectionFrame.document).where(session==sid).order_by(CollectionFrame.boot, CollectionFrame.seq)`；journal_crc 对账 `select(func.count(), func.sum(JournalCrc.crc)).where(device==, boot==, seq.between(first, last))`。
- `create_video`：会话存在检查 + videos 幂等 + `dialect_insert(...).on_conflict_do_nothing`。
- `video_folder`：`select(CollectionVideo.document, CollectionVideo.complete).where(id==vid, session==sid)`。
- `finish`/`align`：`update(CollectionVideo).where(CollectionVideo.id == vid).values(complete=..., document=...)`。
- 导入：`from petsense import db as db_module`、`from petsense.db import session_scope`、`from petsense.db.models import ...`、`from sqlalchemy import func, select, update`。

- [ ] **Step 4: 测试 + 提交**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v
git add server/petsense/collection server/tests/test_collection.py
git commit -m "refactor: CollectionStore 迁统一库（复合主键幂等与 ACK 前提交语义保留）"
```

---

### Task 10: petgif + trends 域迁移

**Files:**
- Modify: `server/petsense/petgif/proxy.py`（仅 `app_petgif_diary_post` 两处 `app_connect`）
- Modify: `server/petsense/trends/routes.py`（仅 `_hourly_trend_buckets` 的 readings 查询）
- Test: `server/tests/test_trends.py`

**Interfaces:**
- Consumes: Task 6 新库 `Reading`、Task 5 `require_app_user`
- Produces: 响应形状不变（trends 桶结构、diary-post 的 `postId`）

- [ ] **Step 1: 写失败测试**

```python
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import db
from petsense.db.models import Reading
from petsense.trends import routes as trends_routes
from tests.helpers import fresh_db


class TrendsTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(trends_routes.bp)
        self.client = app.test_client()

    def test_day_period_buckets_include_recent_valid_frames(self):
        payload = '{"health":{"valid":true,"heartRate":100,"spo2":99,"bodyTemp":38.2}}'
        with db.session_scope() as s:
            for i in range(3):
                s.add(Reading(topic='petsense/sensor/esp32-001', payload=payload,
                              received_at=db.utc_now() - timedelta(minutes=i)))
        buckets = self.client.get('/api/app/trends?period=day').get_json()['buckets']
        self.assertTrue(buckets)
        self.assertEqual(buckets[-1]['samples'], 9)  # 3 帧 × hr/spo2/temp

    def test_day_period_empty(self):
        self.assertEqual(self.client.get('/api/app/trends?period=day').get_json()['buckets'], [])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 改 `trends/routes.py` 的 `_hourly_trend_buckets` 查询**

```python
cutoff = utc_now() - timedelta(hours=24)
with session_scope() as s:
    rows = s.execute(select(Reading.payload, Reading.received_at)
                     .where(Reading.topic == 'petsense/sensor/esp32-001',
                            Reading.received_at >= cutoff)
                     .order_by(Reading.id.asc())).fetchall()
```
循环内 `received_at` 已是 aware datetime：`dt = received_at + timedelta(hours=8)`（与原逻辑一致的北京换算），其余聚合不动。导入相应调整（去掉 `from petsense import ... db`，加 `from petsense.db import session_scope, utc_now` 等）。

- [ ] **Step 4: 改 `petgif/proxy.py` 的 `app_petgif_diary_post`**

宠物归属检查与插帖改为：
```python
with session_scope() as s:
    if pet_id:
        owned = s.execute(select(Pet.id).where(Pet.id == pet_id, Pet.user_id == user['id'])).scalar_one_or_none()
        if not owned:
            pet_id = None
    post = Post(user_id=user['id'], pet_id=pet_id, image_path=filename,
                caption=caption or '今天的运动日记 🏃', location_text=location, created_at=utc_now())
    s.add(post)
    s.flush()
return {'success': True, 'postId': post.id}, 201
```

- [ ] **Step 5: 测试 + 提交**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v
git add server/petsense/trends server/petsense/petgif server/tests/test_trends.py
git commit -m "refactor: trends/petgif 迁统一数据层，小时桶用 aware 时间比较"
```

---

### Task 11: 收尾——app 工厂接线、删除垫片与旧配置

**Files:**
- Modify: `server/petsense/app.py`（`init_db` + teardown）
- Delete: `server/petsense/db/_legacy.py`
- Modify: `server/petsense/db/__init__.py`（删垫片导入）
- Modify: `server/petsense/config.py`（删 `DB_PATH`、`APP_DB`）

**Interfaces:**
- Consumes: 前面全部任务
- Produces: 全部数据访问走新库；`grep -rn 'db\.cur\|db\.conn\|app_connect\|init_app_db' server/petsense` 零命中

- [ ] **Step 1: `app.py` 改造**

`db.init_app_db()` → `db.init_db()`；在 `create_app` 内注册完 blueprint 后加：
```python
    @app.teardown_appcontext
    def _close_db_session(_exception):
        db.Session.remove()
```
导入 `from petsense import config, db` 保持。

- [ ] **Step 2: 删垫片**

删除 `db/_legacy.py`，删 `db/__init__.py` 最后一行垫片导入。

- [ ] **Step 3: config 清理**

删除 `DB_PATH = ...` 与 `APP_DB = ...` 两行（先全局 grep 确认无其他引用：`grep -rn 'DB_PATH\|APP_DB' server/petsense server/deploy` 应只剩 config 自身）。

- [ ] **Step 4: 验收（三条硬检查）**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v   # 全绿
grep -rn 'db\.cur\|db\.conn\|app_connect\|init_app_db' server/petsense | wc -l  # 0
PETSENSE_MQTT_DISABLED=1 PETSENSE_ROOT=/tmp/petsense-smoke python3 wsgi.py &   # 启动后 curl 验证
curl -s localhost:8080/api/stats; curl -s localhost:8080/api/latest-full; curl -s localhost:8080/api/app/auth/me
kill %1
```
预期：`/api/stats` 返回 `{"total": 0, "latest": null, ...}`；`/api/app/auth/me` 返回 `{"success": true, "user": null}`；`/tmp/petsense-smoke/petsense.db` 存在且旧三库文件不在该目录创建。
另跑一次 `python3 seed-demo.py http://localhost:8080`（本地服务）确认种子流程可用（走 HTTP，无需改代码）。

- [ ] **Step 5: 提交**

```bash
git add -A server/petsense
git commit -m "refactor: app 工厂接 init_db 与会话 teardown，删除旧数据层垫片"
```

---

### Task 12: CI postgres 验证 job

**Files:**
- Modify: `.github/workflows/ci.yml`（新增 `server-postgres` job）

**Interfaces:**
- Consumes: `tests/helpers.py` 的 `PETSENSE_TEST_DATABASE_URL` 约定
- Produces: 每次 PR 双方言验证

- [ ] **Step 1: 在 `server` job 后新增 job**

```yaml
  server-postgres:
    name: Server tests (PostgreSQL dialect)
    runs-on: ubuntu-latest
    timeout-minutes: 15
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: petsense_test
        ports: ['5432:5432']
        options: >-
          --health-cmd pg_isready --health-interval 10s --health-timeout 5s --health-retries 5
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: '3.12'
          cache: pip
          cache-dependency-path: server/requirements.lock
      - name: 安装依赖（含 PG 驱动）
        run: python -m pip install -r server/requirements.lock psycopg2-binary
      - name: 双方言单测
        env:
          PETSENSE_MQTT_DISABLED: '1'
          PETSENSE_TEST_DATABASE_URL: postgresql://postgres:postgres@localhost:5432/petsense_test
        run: python -m unittest discover -s server/tests -v
```

- [ ] **Step 2: 本地预演（有 docker 时）**

```bash
docker run -d --name pg-test -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=petsense_test -p 5432:5432 postgres:16
pip install psycopg2-binary
cd server && PETSENSE_MQTT_DISABLED=1 PETSENSE_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/petsense_test python3 -m unittest discover -s tests -v
docker rm -f pg-test
```
预期全绿。若无 docker，注明依赖 CI 首跑验证（push 后观察）。

- [ ] **Step 3: 提交**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: 服务端单测增加 PostgreSQL 方言 job，双方言漂移持续验证"
```

---

### Task 13: 部署集成与文档

**Files:**
- Modify: `server/deploy/deploy_server.py`（`MANAGED_FILES` 加两项）
- Modify: `server/README.md`（数据层说明 + 回滚语义）

**Interfaces:**
- Produces: 部署包含 alembic；`python -m unittest discover -s server/tests -v` 里的 `test_deployment` 自动覆盖新条目（它按 MANAGED_FILES 循环造桩文件）

- [ ] **Step 1: `MANAGED_FILES` 加条目**

```python
MANAGED_FILES = (
    "petsense", "wsgi.py",
    "alembic", "alembic.ini",
    "models/random_forest_1s.joblib",
    "action-dashboard/index.html",
    "requirements.txt", "requirements.lock",
)
```

- [ ] **Step 2: `server/README.md` 增补两段**

- 「数据层」：单一 `petsense.db`（Alembic 管理，`PETSENSE_DATABASE_URL` 可切 PG）；旧三库文件已停用但保留在安装根目录，稳定一个发布周期后可手动删除。
- 「回滚语义」：回滚只还原代码，不回滚数据库——新代码回滚到旧版本后，旧代码继续读写旧库文件（`data.db`/`petsense_app.db`），新库 `petsense.db` 留待重升时续用；首次部署新版本前旧库数据不再进入新库（演示数据，已决策放弃）。

- [ ] **Step 3: 全量验收**

```bash
cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v
cd ../app && npm run lint && npm run typecheck && npm run format:check   # 确认客户端零改动未被波及
git grep -n 'db\.cur\|app_connect\|init_app_db' -- server/petsense | wc -l   # 0
```

- [ ] **Step 4: 提交**

```bash
git add server/deploy/deploy_server.py server/README.md
git commit -m "deploy: 发布包含 alembic 迁移环境，文档补充数据层与回滚语义"
```

---

## 任务依赖图

```
T1 → T2 → T3 → T4
              ├→ T5(auth) ─→ T7(pets) ─→ T8(social)
              ├→ T6(telemetry) ─↗
              ├→ T9(collection)
              └→ T10(petgif/trends)
T5..T10 全部完成 → T11(收尾) → T12(CI PG) → T13(deploy/文档)
```

## 完成定义（对照 spec §12）

1. 本地 `PETSENSE_MQTT_DISABLED=1 PETSENSE_ROOT=<tmp> python3 wsgi.py` 全功能正常，dashboard/feed/trends/monitor 行为与现在一致
2. SQLite 与 PG（CI job）单测双绿
3. `grep -rn 'db\.cur\|app_connect' server/petsense` 零命中
4. 空库 `alembic upgrade head` 建全 15 表、`downgrade base` 干净回退
5. 旧三库文件未被触碰（`git status` 干净 + 服务器上原样）
