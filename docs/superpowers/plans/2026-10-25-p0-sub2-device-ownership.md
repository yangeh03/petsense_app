# P0-2 设备归属与租户隔离实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** devices 归属模型 + 全链路租户隔离：认领即绑定、`/ws` 按用户设备过滤、MQTT 管道多设备化、命令端点鉴权、App 配对交互。

**Architecture:** Alembic 0002 加 `devices` 表与 `readings.device_id` 列（库已知为空，无回填）；归属链 `帧.device_id → devices.owner → 用户`；WS 作用域连接时缓存、认领/解绑即时刷新；服务端与客户端**最后一起推送**（避免 /ws 鉴权先上而 App 未带 token 的断档窗口）。

**Tech Stack:** 现有栈（Flask/SQLAlchemy/Alembic/React Native Expo）

**Spec:** `docs/superpowers/specs/2026-10-25-p0-sub2-device-ownership-design.md`

## Global Constraints

- 生产库为空：迁移不含存量回填；历史 readings 的 `device_id` 允许为 NULL
- `/api/stats`、`/api/action/status`（仅 `enabled` 字段语义）保持匿名——部署健康检查依赖
- App 读接口响应形状不变；唯一行为变化 = 未登录/未绑设备拿不到实时数据
- 每任务：`cd server && PETSENSE_MQTT_DISABLED=1 python3 -m unittest discover -s tests -v` 全绿；客户端改动后 `npm run lint && npm run typecheck && npm run format:check`
- **本地逐任务 commit，全部完成后一次性 push**（部署随 server/ 变更自动触发，避免断档）
- `server/petsense/` 内 `esp32-001` 字面量最终清零（firmware/ 不计）

---

### Task 1: 迁移 0002 + Device 模型

**Files:**
- Create: `server/petsense/db/models/devices.py`、`server/alembic/versions/0002_devices.py`
- Modify: `server/petsense/db/models/__init__.py`
- Test: `server/tests/test_devices.py`（结构部分）

**Interfaces:**
- Produces: 模型 `Device`（表 `devices`：device_id PK / owner_id FK unique / name / claimed_at / last_seen_at nullable）；`readings.device_id` 列 + `ix_readings_device_received` 索引；`pets` 部分唯一索引 `uq_pets_device_id`（`WHERE device_id != ''`）

**Steps:**
- [ ] 写失败测试：`EXPECTED_TABLES` 加 `devices`；0001→0002 后 inspect 确认列/索引存在；空库 `upgrade head` 直接到 0002
- [ ] `models/devices.py` 定义 Device（UTCDatetime 时间列）；`models/__init__.py` 注册
- [ ] 手写 `0002_devices.py`（不 autogenerate，DDL 小）：
```python
def upgrade() -> None:
    op.create_table('devices',
        sa.Column('device_id', sa.String(length=64), primary_key=True),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=40), nullable=False, server_default=''),
        sa.Column('claimed_at', petsense.db.base.UTCDatetime(), nullable=False),
        sa.Column('last_seen_at', petsense.db.base.UTCDatetime(), nullable=True),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']))
    op.create_index('ix_devices_owner_id', 'devices', ['owner_id'], unique=True)
    with op.batch_alter_table('readings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_readings_device_received', ['device_id', 'received_at'])
    op.create_index('uq_pets_device_id', 'pets', ['device_id'], unique=True,
                    sqlite_where=sa.text("device_id != ''"),
                    postgresql_where=sa.text("device_id != ''"))

def downgrade() -> None:
    op.drop_index('uq_pets_device_id', table_name='pets')
    with op.batch_alter_table('readings', schema=None) as batch_op:
        batch_op.drop_index('ix_readings_device_received')
        batch_op.drop_column('device_id')
    op.drop_index('ix_devices_owner_id', table_name='devices')
    op.drop_table('devices')
```
revision='0002'，down_revision='0001'。
- [ ] 测试全绿 + commit `feat: 迁移 0002——devices 表与 readings.device_id`

### Task 2: 设备域 API + 一户一宠

**Files:**
- Create: `server/petsense/devices/__init__.py`、`server/petsense/devices/routes.py`
- Modify: `server/petsense/app.py`（注册蓝图）、`server/petsense/pets/routes.py`（POST /pets 约束与自动认领）
- Test: `server/tests/test_devices.py`（补全行为用例）

**Interfaces:**
- Consumes: Task 1 模型；`require_app_user`
- Produces: `GET /api/app/device`、`POST /api/app/device/claim`、`POST /api/app/device/release`；`devices.routes.my_device_row(user_id) -> Device|None`（内部复用）；`realtime.refresh_scopes()`（Task 4 实装，本任务调用点用 `try/except AttributeError` 或先在 realtime 加空实现）

**核心行为：**
```python
@bp.route('/device')
def app_device():  # 我的设备 + 在线/电量（复用最新 readings 帧）
@bp.route('/device/claim', methods=['POST'])
def app_device_claim():
    # deviceId 正则 [A-Za-z0-9_-]{1,40}；已有属主且非我 -> 409 '设备已被其他账号绑定'
    # 属我 -> 幂等返回；无人认领 -> devices 写行 + 若我的宠物存在且未绑 -> pet.device_id 同步
    # 成功后 realtime.refresh_scopes()
@bp.route('/device/release', methods=['POST'])
def app_device_release():
    # 属主校验 404/403；清 pet.device_id；删 devices 行（或保留行清 owner？——删行，回到未认领池）
    # realtime.refresh_scopes()
```
- `POST /pets`：已有宠物 → `409 每个账号支持一只宠物档案`；带 deviceId → 自动认领（同 claim 规则，冲突 409）
- 测试：认领/幂等/409/释放清绑定/一户一宠 409/带 deviceId 建宠自动认领/未登录 401
- [ ] 全绿 + commit `feat: 设备域——认领/解绑 API 与一户一宠约束`

### Task 3: ingest 多设备管道

**Files:**
- Modify: `server/petsense/telemetry/mqtt_ingest.py`、`server/petsense/stats.py`、`server/petsense/telemetry/realtime.py`（broadcast 签名）
- Test: `server/tests/test_telemetry.py` 扩展

**Interfaces:**
- Produces:
  - `store(topic, payload, device_id=None) -> datetime`（readings 带 device_id）
  - `stats.update_daily_stats(device_id, frame, received_at)`：device → devices 行 → 属主宠物（`join`）；未认领跳过
  - `stats.remember_uwb(frame, received_at, device_id)`：`last_uwb_frames: dict[device_id]`
  - `broadcast_packet(packet_str, device_id=None)`（None=全体，如 ping；SSE 始终全量）
  - `devices.last_seen_at` 节流更新（内存 dict，60s/设备）
- ingest：`sensor_device(topic)` 提取；`petsense/sensor/esp32-001` 三处字面量判断删除，任何传感器设备帧走完整管道；recorder 调用传 device（recorder 门槛 `topic != esp32-001` 改为 `device_id` 非空判断——recorder 内部 per-device 化留 Task 5，此处仅放行多设备帧且无活跃实验时自然 no-op）
- `config.CONTROL_TOPIC` 与 `publish_control_command` 本任务**不动**（Task 5 一起改）
- 测试：双设备帧入库 device_id 各自正确；未认领设备 stats 跳过、认领后归属正确宠物；broadcast 带 device（过滤行为 Task 4 测）
- [ ] 全绿 + commit `refactor: ingest 管道多设备化，统计/UWB 按设备归属`

### Task 4: /ws 鉴权过滤 + 数据面鉴权

**Files:**
- Modify: `server/petsense/auth/routes.py`（抽 `user_for_token(token)`，`app_user_from_request` 复用）
- Modify: `server/petsense/telemetry/realtime.py`（ws 作用域/4401/refresh_scopes/latest-full 鉴权过滤）
- Modify: `server/petsense/actions/service.py`（latest/history 鉴权过滤；status 去 latest）
- Test: `server/tests/test_ws_scope.py`

**Interfaces:**
- Produces:
  - `auth.routes.user_for_token(token) -> dict|None`
  - `realtime.device_ids_for_user(user_id) -> set[str]`（devices.owner 查询）
  - `realtime.ws_clients: dict[ws -> frozenset]`；`realtime.refresh_scopes()`
  - `realtime.packet_visible(scope, device_id) -> bool`（纯函数：device_id None → True）
- `/ws?token=`：无效 → 发 `{"type":"unauthorized"}` 后 close(4401)；有效 → 注册作用域
- `/api/latest-full`、`/api/action/latest|history`：`require_app_user` + 我的设备过滤；`/api/action/status` 保留匿名、去 `latest` 键
- 测试：packet_visible 矩阵；user_for_token；latest-full 双用户互不可见；status 仍有 enabled
- [ ] 全绿 + commit `feat: /ws 与实时数据面鉴权——按用户设备过滤`

### Task 5: 设备命令 per-device 化 + 鉴权

**Files:**
- Modify: `server/petsense/telemetry/commands.py`、`server/petsense/experiments/recorder.py`、`server/petsense/telemetry/mqtt_ingest.py`（publish 签名）、`server/petsense/config.py`（删 CONTROL_TOPIC）
- Test: `server/tests/test_commands.py`

**Interfaces:**
- `publish_control_command(device_id, command) -> (payload, ok)`：topic `petsense/device/<device_id>/command`
- `recorder.experiment_states / event_states: dict[device_id -> state]`；`recorder.active_experiment(device_id)`；manifest 增加 `device_id`
- 四个端点（audio-recording / experiment / experiment-event / experiment-files）：`require_app_user`；目标设备 = 我的设备（未绑定 → 400 请先绑定项圈）；audio_recording_state per-device
- 测试：401/400/命令发到 `petsense/device/<my>/command`/双用户实验状态隔离/experiment-files 只见自己
- [ ] 全绿 + commit `feat: 设备命令鉴权与 per-device 实验状态，删除 CONTROL_TOPIC 硬编码`

### Task 6: trends / UWB 数据面归属

**Files:**
- Modify: `server/petsense/trends/routes.py`、`server/petsense/pets/routes.py`（uwb/latest）
- Test: `server/tests/test_trends.py` 扩展 + uwb 用例

**行为：** trends hourly 按 `device_id IN (我的设备)`（复用 `realtime.device_ids_for_user`）；实验 CSV 聚合读各实验 manifest（新字段 device_id，旧的从 device_topic 尾段解析）→ 属主过滤；`/api/app/uwb/latest` 返回我的设备最新帧（`stats.last_uwb_frames` dict），未登录/无设备返回 `frame: null`
- [ ] 全绿 + commit `feat: trends 与 UWB 按设备归属`

### Task 7: 客户端——token / 配对交互 / 空态

**Files:**
- Modify: `app/src/services/api.ts`（导出 `getToken()`；+`fetchMyDevice/claimDevice/releaseDevice`）
- Modify: `app/src/services/telemetry-service.ts`（URL 拼 token、`unauthenticated` 状态、4401/未授权帧停止重连、`reconnectTelemetry()`）
- Modify: `app/src/hooks/use-auth.tsx`（登录/登出成功后 `reconnectTelemetry()`）
- Create: `app/src/components/device-card.tsx`（含 PairDeviceModal）
- Modify: `app/src/app/(tabs)/profile.tsx`（挂 DeviceCard）、`tracking.tsx` / `monitor.tsx`（未登录/无设备空态引导）、`create.tsx`（设备 ID 文案）、`app/src/types/`（Device 类型）
- 验证：`npm run lint && npm run typecheck && npm run format:check`
- [ ] 全绿 + commit `feat: App 配对交互与 WS token 联动`

### Task 8: 验收与发布

- [ ] 服务端全测 + App 三门禁 + `grep -rn 'esp32-001' server/petsense/` 零命中
- [ ] 本地端到端冒烟：起本地服务 → 注册用户 → 建宠（带 deviceId=esp32-001）→ 模拟 ingest 帧（直接调 store/on_message 或 mock publish）→ WS（python 客户端带 token）收到帧；无 token 被拒；另一用户收不到
- [ ] 更新 `AGENTS.md`（遥测链路描述：/ws 需 token、设备归属）与 `server/README.md`
- [ ] **一次性 push**（服务端+客户端+文档）→ 观察 CI（新流水线路径感知：app+server 都变了全跑）→ 部署
- [ ] 生产验证：`/api/stats` 匿名可用；`/api/action/status` enabled=true；`/ws` 无 token 握手被拒（本地已验，生产抽查 SSH journalctl 或由用户 App 实测认领 esp32-001）

## 任务依赖

T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8（线性；T4 依赖 T2 的 devices 数据与 T3 的 broadcast 签名）
