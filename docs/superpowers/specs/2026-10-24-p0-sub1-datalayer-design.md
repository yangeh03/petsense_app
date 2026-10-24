# P0-1 数据层重建设计（SQLAlchemy + Alembic + 三库收敛）

- 日期：2026-10-24
- 状态：已评审通过（brainstorming 八节逐节确认）
- 范围：P0 架构级阻断 · 子项目 1/3

## 0. 背景：P0 分解

P0（多租户架构阻断）分解为三个顺序子项目，各自独立走 spec → 计划 → 实现：

1. **数据层重建**（本设计）——迁移框架 + 单库收敛 + 消灭共享 cursor
2. **设备归属与租户隔离**——devices 表、配对 API、遥测/统计/趋势带归属、`/ws` 按用户过滤、MQTT 去单设备硬编码
3. **进程架构**——gunicorn + MQTT ingest 独立进程 + WS/SSE 广播改 Redis pub/sub

顺序理由：子项目 2 的 schema 变更需要迁移框架就位；子项目 3 的广播频道设计依赖子项目 2 的归属模型；共享 cursor 在子项目 1 消灭。

## 1. 现状问题（证据）

| 问题 | 证据 |
|------|------|
| 三个互独立 SQLite 库，各自建表 | `server/petsense/config.py:25,29`、`collection/api.py:51` |
| 无迁移框架，靠 `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` 手写补列 | `db.py:15-33,47-133` |
| 模块级单连接单 cursor 跨线程共享（MQTT 线程 + HTTP 线程），无锁 | `db.py:13,36`；使用者 `mqtt_ingest.py:23-29`、`realtime.py:80,115,128`、`stats.py:18` |
| 环冲裁剪每帧 `COUNT(*)` 全表 | `mqtt_ingest.py:25-29` |
| 时间列 TEXT，`Z` 与 `+00:00` 两种后缀混存，靠字典序比较 | `mqtt_ingest.py:22` vs `auth/routes.py:44`；`auth/routes.py:28` |
| 社交表零二级索引、外键缺失 | `db.py:86-104` |
| stats 每帧新开连接 | `stats.py:18` |

## 2. 决策记录（brainstorm 结论）

| 决策点 | 结论 |
|--------|------|
| 存量数据 | **全部可弃**，全新 schema，不写数据迁移；磁盘实验文件（cloud_data、采集视频、音频）不动 |
| 生产数据库 | 未来 TencentDB for **PostgreSQL**；本次生产暂继续 SQLite（数据量小），设计按 **SQLite + PostgreSQL 双方言兼容** |
| 三库收敛 | 全并入**单一逻辑库**（新文件 `petsense.db`），一套 Alembic 迁移 |
| 实现路线 | 方案 C 分层混合：ORM 建模 + 业务域 ORM 查询 + 遥测热路径 Core 表达式 |
| 切云时机 | 推后；切云 = 改 `DATABASE_URL` + 跑迁移，作为独立小任务 |

双方言可行性的语法依据：SQLite 与 PostgreSQL 同族的 `INSERT ... ON CONFLICT DO UPDATE/DO NOTHING`（现状 `stats.py:46-53`、collection 的 OR IGNORE/REPLACE 迁移后语义不变）。

## 3. 目标与非目标

**目标**

- 单一逻辑库；SQLAlchemy 2.0 模型为 schema 唯一事实源；Alembic 管版本，不再手写 DDL/补列
- 消灭模块级共享 `conn/cur` 与一切自建连接，统一 engine + scoped_session
- **API 行为完全等价**：响应 JSON 形状、时间字段格式逐字节不变，客户端零改动
- 修复：环冲 O(N) 裁剪、时间字符串混存、社交表零索引、外键缺失

**非目标**

- 不加 devices 表/租户列（子项目 2 以此作为迁移框架首次实战）
- 不动进程模型（仍 werkzeug 单进程 + 进程内 MQTT/WS 内存态，`ws_clients`/`latest`/`audio_buffers` 等原样）
- 不切腾讯云、不加 psycopg2（CI 加 PG 验证 job 防方言漂移）
- 不动文件上传存储（COS 另议）、不做时区参数化（北京日界行为保持，P3）

## 4. 代码结构

```
server/
  alembic.ini
  alembic/
    env.py            # 读 config.DATABASE_URL；target_metadata = Base.metadata
    versions/         # 0001_initial.py（全部 15 张表）
  petsense/db/
    __init__.py       # engine、Session(scoped_session)、session_scope()、init_db()、iso_z()
    base.py           # DeclarativeBase + UTC 时间戳 mixin
    models/
      auth.py         # User, AuthSession
      pets.py         # Pet, DailyStat, HomeLayout
      social.py       # Post, PostLike, Comment, Follow
      telemetry.py    # Reading, ActionPrediction
      collection.py   # CollectionSession, CollectionFrame, CollectionVideo, JournalCrc
```

- `db.py` 删除；`db.conn`、`db.cur`、`app_connect()`、`init_app_db()` 全部移除
- `config.py` 新增 `DATABASE_URL`（env `PETSENSE_DATABASE_URL`，默认 `sqlite:///<RUNTIME_ROOT>/petsense.db`）；删除 `DB_PATH`、`APP_DB`
- `CollectionStore.__init__(root, token, publish, session_factory)`——存储走统一 engine，`root` 仍用于视频/标注文件；`latest`、`media_locks`、`atomic_json` 等内存与文件逻辑不变
- `create_app()` 调 `db.init_db()`：程序化幂等执行 `alembic upgrade head`（保留"直接 `python wsgi.py` 就能跑"；多 worker 显式迁移的调整属子项目 3）

## 5. Schema 设计（15 张表）

### 5.1 表清单与命名

统一命名空间暴露一处撞名：App 的 `sessions`（登录令牌）与 collection 的 `sessions`（采集会话）。处置：前者改名 **`auth_sessions`**（仅 auth 域内部使用，无 API 影响），后者 **`collection_sessions`**；`frames`→`collection_frames`、`videos`→`collection_videos`、`journal_crc` 保留。

### 5.2 类型与约束变化

| 变化 | 说明 |
|------|------|
| 时间列 `TEXT` → `DateTime(timezone=True)` | SQLite 上由 SQLAlchemy 存 ISO 文本、PG 上 timestamptz；列名不变（`created_at`、`expires_at`、`received_at`、`predicted_at` 等） |
| `daily_stats.date` → `Date` | 保持北京日界字符串生成逻辑（`shanghai_now().date()`） |
| 补外键 | `post_likes(post_id→posts, user_id→users)`、`comments(同)`、`follows(follower_id/followee_id→users)`、`daily_stats(pet_id→pets)`、`auth_sessions(user_id→users)` |
| `users.username` 唯一性 | SQLite-only 的 `COLLATE NOCASE` 改为 **`lower(username)` 表达式唯一索引**（双方言等价） |
| collection 域结构原样 | 文档型 `document TEXT` + 复合主键（`collection_frames(device,boot,seq)` 等）不变；`INSERT OR IGNORE/REPLACE` → Core `on_conflict_do_nothing/do_update` |

### 5.3 索引（新增部分）

- `posts(user_id, id)`、`comments(post_id)`、`post_likes(post_id)`（UNIQUE(post_id,user_id) 已有）、`follows(followee_id)`
- `auth_sessions(user_id)`
- `pets(user_id)`、`pets(device_id)`（服务现有 `stats.py:19` 按 device_id 查询，兼顾子项目 2）
- `readings(topic, received_at)`（服务 `trends` 与 dashboard 的现有查询；现有代码无裸 `received_at` 条件查询，不另建单列索引）
- 保留：`action_predictions(device_id, id DESC)`、`collection_frames(session,boot,seq)`、各 UNIQUE 约束

### 5.4 持久化契约（SQLite）

统一库全部连接注入 PRAGMA：`journal_mode=WAL`、`synchronous=FULL`、`foreign_keys=ON`、busy `timeout=20s`（engine `connect_args`）。比现状更严（readings 库现在无 FULL），以一套策略保住 collection "ACK 前必落盘" 的契约；当前写入频率（单设备每秒数帧）下性能无虞。PG 连接上述为 no-op。

## 6. 连接与并发模型

- `engine = create_engine(DATABASE_URL, pool_pre_ping=True)`；SQLite 加 `connect_args={'check_same_thread': False, 'timeout': 20}`
- `Session = scoped_session(sessionmaker(bind=engine))`，生命周期规则：
  - **HTTP**：路由内用 `Session()`；`app.teardown_appcontext` 中 `Session.remove()`
  - **MQTT/分类器线程**：复用线程绑定的 session，每批 commit、异常 rollback、线程退出 remove
- 消灭清单：`db.conn/cur`、27 处 `app_connect()`、`CollectionStore` 自建 sqlite3 连接、`stats.py` 每帧开连接
- 任何两个线程不再共用 cursor（消灭跨线程结果串行污染）

## 7. 遥测热路径

- `store(topic, payload)` 改 Core `insert()`；`received_at` 在 Python 侧生成 aware UTC datetime
- 广播 packet 的 `received_at` 经 **`iso_z()`** 序列化——输出与今天逐字节相同（`%Y-%m-%dT%H:%M:%S.%f` 截毫秒 + `Z`）；`recorder` CSV 拿到相同字符串，文件格式不变；`stats` 钩子内部签名可改传 datetime
- **环冲水位裁剪**：模块内计数器每 500 次插入执行一次 `DELETE FROM readings WHERE id <= (SELECT MAX(id) FROM readings) - MAX_HISTORY`；语义等价（始终保留最近约 1 万条，容差 ≤500），均摊 O(1)
- `realtime.py` 三处读（SSE 首帧、`/api/latest-full`、`/api/stats`）与 `actions/service.py` 预测写入改 Core
- `broadcast_packet` 及 WS/SSE 内存结构不动

## 8. 时间处理规范

- Python 层一律 aware UTC datetime；DB 层 `DateTime(timezone=True)`
- 对外唯一出口 `iso_z(dt)`：Z 后缀、毫秒精度，所有 API 时间字段走它（客户端零改动）
- session 过期比较从字符串字典序改为 DateTime 比较
- `SLEEP_UTC_HOURS`、`shanghai_now` 北京日界行为不变

## 9. 迁移、部署与旧数据

- `0001_initial` 一次建全部表：`alembic revision --autogenerate` 生成 + 人工对照本 spec §5 校对
- 旧库文件（`data.db`、`petsense_app.db`、`collection_data/collection.sqlite3`）**原地保留不动**（天然备份），稳定一个发布周期后手动清理
- 部署管道零变化：`create_app` 启动幂等升级；deploy 脚本不改
- `seed-demo.py` 经 HTTP API 写入（不直接访问 DB），无需改动（dev 工具，生产不执行）
- `requirements.txt`：+`SQLAlchemy>=2.0`、`alembic`
- CI：现有 server 测试 job 不变；**新增 postgres service job**——同一套 unittest 以 `PETSENSE_DATABASE_URL=postgresql://...` 再跑一遍

## 10. 错误处理

- SQLite 锁等待由 `timeout=20` 吸收；不加应用层重试（仍单写者）
- PG 瞬断靠 `pool_pre_ping`；collection 的 ValueError→400、MQTT 拒帧不 ACK 等既有语义原样

## 11. 测试计划

| 模块 | 覆盖 |
|------|------|
| 既有 `test_collection.py` | 回归 CollectionStore 幂等重放/持久化语义（改用统一 engine 的 tmp 库） |
| 既有 `test_deployment.py` | 按需适配，断言不放松 |
| 新增 `test_migrations.py` | 空库 → `upgrade head` 全表存在；`downgrade base` 干净回退 |
| 新增 `test_models.py` | 核心模型 roundtrip；collection_frames 复合主键幂等（同 payload 跳过、异 payload 拒绝）；daily_stats upsert 累加；外键约束生效 |
| 新增 `test_wire_format.py` | readings `received_at` 与 feed `created_at` 线上格式逐字节不变 |
| CI PG job | 同套测试对 PG 全绿 |

## 12. 验收标准

1. 本地 `PETSENSE_MQTT_DISABLED=1 python3 wsgi.py` 全功能正常；App 端 dashboard / feed / trends / monitor 行为与现在一致
2. `python -m unittest discover -s server/tests` 在 SQLite 与 PG（CI）双双全绿
3. `grep -rn 'db\.cur\|app_connect' server/petsense` 零命中
4. `alembic upgrade head` 从空库可建全部 15 张表，`downgrade base` 可干净回退
5. 旧三库文件未被触碰

## 13. 风险与缓解

| 风险 | 缓解 |
|------|------|
| ORM 重写引入行为差异 | wire_format 逐字节测试 + 既有测试 + 手动冒烟清单（dashboard/feed/trends/monitor/采集） |
| 双方言漂移 | CI postgres job 每次 PR 验证 |
| autogenerate 漏索引/约束 | 0001_initial 人工对照 §5 清单校对（验收项） |
| collection 持久化语义弱化 | 统一 `synchronous=FULL` + `test_collection` 回归 |
| 启动升级与并发测试冲突 | 幂等 upgrade + SQLite busy timeout |

## 14. 对子项目 2/3 的接口约定

- 子项目 2 加 devices 表/租户列 = Alembic 第二个迁移（框架首次实战）
- 子项目 3 拆进程时，`CollectionStore` 注入 `session_factory` 的模式即"存储与进程解耦"的接缝；engine 每进程独立天然成立
