# P0-2 设备归属与租户隔离开设计（devices 表 + 实时链路过滤 + 多设备管道）

- 日期：2026-10-25
- 状态：已评审通过（brainstorm 两批设计逐节确认）
- 范围：P0 架构级阻断 · 子项目 2/3
- 前置：P0-1 数据层重建已交付（`docs/superpowers/specs/2026-10-24-p0-sub1-datalayer-design.md`）

## 0. 背景

P0-1 完成数据层统一后，多租户的核心缺口是**设备没有归属模型**：

- 无 device 表，`pets.device_id` 自由文本可冒领；统计按"同 device_id 取 id 最小宠物"归属（`stats.py`），跨用户串数据
- `esp32-001` 字面量 6 处（`config.py:21`、`mqtt_ingest.py:218/238/244`、`recorder.py:73`、`trends/routes.py:98`）——第二台设备的实验/统计/趋势静默丢弃
- `/ws`、`/api/latest-full` 等无鉴权全局广播；命令端点（录音/实验）任何人可调

## 1. 决策记录（brainstorm 结论）

| 决策点 | 结论 |
|--------|------|
| 配对方式 | **device_id 认领**：用户输入机身/包装上的设备 ID；固件零改动。信任模型 = 知道设备 ID 即拥有设备；彻底防冒领（配对码/设备级凭据）留后续 |
| 本期范围 | 核心隔离 + 命令鉴权；音频文件下载、SSE 内部面板的归属化留后续 |
| 客户端联动 | WS 带 token（query 参数，RN 限制）+ 未登录空态；完整路由守卫/401 统一拦截留 P1 |
| 归属模型 | 方案 A：独立 `devices` 表，归属链 `帧.device_id → devices.owner → 用户` |
| 实体约束 | **一个用户 = 一只宠物 = 一台设备**（宠物数在 API 层强制，设备属主在 DB 层强制） |
| 性能结论 | 作用域在连接时缓存、不在帧路径查库；每帧新增 <1ms；瓶颈仍是单进程+SQLite（P0-3 域，不因本期恶化） |

## 2. 目标与非目标

**目标**

- `devices` 表 + 认领/解绑 API；认领即自动绑定到用户的宠物
- MQTT 管道全设备化：`server/petsense/` 内 `esp32-001` 字面量清零；任何设备的帧走完整管道
- `/ws` token 鉴权 + 按用户设备过滤；认领/解绑即时生效（无需重连）
- `readings` 加 `device_id` 列（存量回填）；stats / trends / UWB / 动作历史 / `latest-full` 按归属
- 录音/实验命令端点加登录 + 属主校验；录音/实验/事件状态 per-device 化
- App 客户端：WS 带 token、设备卡 + 配对弹层、实时页空态引导

**非目标**

- 音频文件列表/下载归属化、SSE 鉴权（后续任务）
- MQTT 每设备凭据、设备端配对码（后续；本期保持 broker 共享账号）
- 完整路由守卫、401 统一拦截、改密/找回（P1）
- `/api/stats` 保持匿名（部署健康检查依赖 `enabled`/stats 字段）
- 内部 dashboard 网页工具（dashboard.html / action-dashboard）本期不适配——其依赖的命令/动作接口鉴权后会显示异常，已知代价
- 多 worker/多实例（P0-3）

## 3. 配对机制（端到端）

```
项圈(固件写死 device_id) ──MQTT──> topic: petsense/sensor/<device_id>
服务器 ingest：从 topic 提取 device_id，readings 带列入库
用户在 App 输入 device_id 认领 → devices 表一行 (device_id, owner_id)
App 连 /ws?token → 服务器：token→用户→其设备集合 → 只推该集合的帧
```

- 设备不感知认领状态，只管上报自己的 topic；新增设备仅需烧录时配置唯一 device_id，即插即用，无需服务器注册
- 认领冲突：已被他人认领 → 409"设备已被其他账号绑定"；转绑路径 = 原账号解绑 → 新账号认领（解绑有确认弹窗）
- 未认领设备的帧照常入库（认领后历史可见），但无人能订阅

## 4. 数据模型与迁移 0002（Alembic 第二个迁移）

- 新表 `devices`：
  - `device_id String(64) PK`
  - `owner_id Integer FK users.id` **UNIQUE**（DB 级强制一人一设备）
  - `name String(40) NOT NULL DEFAULT ''`
  - `claimed_at UTCDatetime NOT NULL`
  - `last_seen_at UTCDatetime NULL`（ingest 更新，每设备 60s 节流）
- `readings`：`+ device_id String(64) NULL` + 复合索引 `(device_id, received_at)`；存量回填 `substr(topic, 17)`（双方言通用）；`topic` 列保留（广播原样转发）
- `pets`：部分唯一索引 `uq_pets_device_id ON pets(device_id) WHERE device_id != ''`（DB 级兜底一设备一宠物）
- **一人一宠物**：API 层强制（`POST /pets` 已有宠物 → 409"每个账号支持一只宠物档案"），不加 DB 约束（避免存量多宠物卡死迁移）
- **存量回填：不做**（生产库已知为空）。如存在少量历史 readings，其 `device_id` 为 NULL，归属查询自然排除；设备归属由用户在 App 内认领建立
- 新实验 manifest 记录 `device_id` 字段（旧 manifest 从 `device_topic` 尾段解析）

## 5. 设备 API（`/api/app`，全部 `require_app_user`）

| 路由 | 行为 |
|------|------|
| `GET /api/app/device` | 我的设备（单个或 `null`）：deviceId/name/petId/petName/online/batteryPercent/lastSeenAt/claimedAt（在线/电量复用 readings 最新帧解析） |
| `POST /api/app/device/claim` `{deviceId, name?}` | deviceId 校验 `[A-Za-z0-9_-]{1,40}`；无人认领 → 认领并绑定我的宠物（`pet.device_id` 同步；**尚无宠物时仅认领不绑定**，建宠时自动接上）；他人已认领 → 409；自己已认领 → 幂等返回 |
| `POST /api/app/device/release` | 属主校验；设备回未认领池，`pet.device_id` 清空 |
| `POST /pets`（现有） | 首只宠物（已有 → 409）；带 deviceId 时自动"认领或校验"（无人认领→自动认领；他人认领→409） |

认领/解绑成功后调用 `realtime.refresh_scopes()` 刷新在线连接的订阅范围。

## 6. APP 配对交互

**三入口一套流程**：

1. **创建宠物顺带绑定**（现状流程保留）：create.tsx 设备 ID 输入框文案改"项圈设备 ID（选填，机身标签可查，填写后自动绑定）"，创建 + 认领一步完成
2. **"我的"页设备卡（主入口）**：新组件 `DeviceCard` 挂 profile 页宠物卡下方
   - 无设备：虚线占位卡"绑定你的项圈 🐾"→ 打开配对弹层
   - 有设备：deviceId、名称、在线状态点、电量、绑定宠物名、"解绑"按钮（Alert 二次确认："解绑后设备数据不再归属本账号，其他账号可认领"）
   - `PairDeviceModal`：输入框（正则校验）+ 占位符 `esp32-xxxx` + "如何查看设备 ID"帮助文案 → claim → 成功关弹层刷新；409 红字提示"该设备已被其他账号绑定，请先在原账号解绑"
3. **实时页空态引导**（tracking/monitor）：未登录或无设备时显示三步引导卡"① 登录 → ② 创建宠物档案 → ③ 绑定项圈"，各步可点击跳转

客户端改动面：`services/api.ts`（+getDevice/claimDevice/releaseDevice）、新 `DeviceCard`/`PairDeviceModal` 组件、`profile.tsx`、`tracking.tsx`、`monitor.tsx`、`create.tsx` 文案、`telemetry-service.ts`（§8）。

## 7. MQTT 管道多设备化

- `on_message`：统一从 `petsense/sensor/<device_id>` 提取设备；**任何**传感器设备的帧走完整管道（批量展开 → 动作识别 → 入库（带 device_id）→ 实验录制 → 统计 → UWB → 广播（带 device_id））；删除三处 esp32-001 字面量判断；collection/audio 专用 topic 分支保留
- `config.CONTROL_TOPIC` 删除；`publish_control_command(device_id, command)` 发 `petsense/device/<device_id>/command`（与 collection 命令 topic 规则对齐）
- `recorder`：`experiment_state`/`event_state` 全局单例改 per-device 字典；`append_cloud_experiment_frame` 按帧的 deviceId 写入对应活跃实验
- `stats.update_daily_stats`：device → devices 行 → 属主的宠物；未认领设备跳过
- UWB：`last_uwb_frame` 单例改 per-device 字典；`/api/app/uwb/latest` 返回我的设备最新帧（带 deviceId）
- `devices.last_seen_at`：ingest 侧每设备 60s 节流更新

## 8. /ws 鉴权过滤 + 命令鉴权 + 数据面归属化

**WS**：

- 握手 `/ws?token=xxx`；服务端 `user_for_token(token)`（从 auth 域抽公共函数）；无效/过期 → 建连后 close(code=4401)，不注册进广播集
- `ws_clients` 从 `set` 改 `{连接: frozenset(device_ids)}`；`broadcast_packet(packet, device_id=None)`：WS 按 `device_id ∈ 集合` 投递；SSE 队列投递全量（内部工具，非目标）；ping 发全体
- `refresh_scopes()`：认领/解绑后遍历在线连接重查设备集（个位数连接，开销可忽略）——已打开页面即时开始/停止收到数据

**命令端点**（`/api/audio-recording`、`/api/experiment`、`/api/experiment-event`、`/api/experiment-files`）：

- 全部 `require_app_user`；目标设备 = 我的设备（一人一设备，无参数）；未绑定 → 400"请先绑定项圈"
- `audio_recording_state`/`experiment_state`/`event_state` per-device 化；GET 返回我的设备状态

**数据面**：

- `/api/action/status`：保留匿名（部署健康检查依赖 `enabled`），**去掉 `latest` 数据字段**
- `/api/action/latest|history`：`require_app_user` + 按我的设备过滤
- `/api/latest-full`：`require_app_user` + 只返回我的设备帧
- trends：实时小时桶 `WHERE device_id IN (我的设备)`（走新复合索引，快于现状 topic 匹配）；实验 CSV 聚合按 manifest 设备 → 属主过滤，无法归属的排除
- dashboard/pets/feed 等现有接口：响应形状不变（归属已由 pet.user_id 保证）

**客户端 token 联动**：

- `telemetry-service.ts`：连接 URL 拼 `?token=`（api 层导出 token getter）；无 token 不连接；收到 4401 → 停止重连；状态机新增 `unauthenticated`
- 登录/登出成功后触发重连（`use-auth` 一处调用 telemetry 重连接口，不动页面层）
- replay 模式不连 WS，零影响

## 9. 性能结论

- 帧路径新增：topic 截取（µs）+ insert 多一列（0）+ devices 主键点查（~0.1ms）+ 每客户端集合判断（ns）——**每帧 <1ms**
- 避开的坑：作用域连接时缓存（不逐帧查库）、复合索引（不全表扫）、last_seen 节流（不逐帧 update）
- WS 过滤降低无关发送量（网络发送才是广播真实成本）
- 容量：链路每帧 2~5ms，单进程可承载数百帧/秒；瓶颈仍是单进程+SQLite（P0-3），本期不恶化

## 10. 测试与验收

**新增测试**：

- 设备域：认领/幂等/409/释放级联清绑定/一人一宠物 409/带 deviceId 建宠自动认领
- 迁移：0002 空库升降级；0001→0002 升级链路正确
- WS：`user_for_token`、作用域计算、帧可见性（抽纯函数）；握手 4401
- 管道：双设备帧各自入库 device_id 正确、stats 各归各的宠物、控制命令发到各自 topic
- 命令端点：未登录 401、未绑设备 400、per-device 状态隔离
- 数据面：trends/UWB/action/latest-full 只见自己设备

**验收清单**：

1. 无 token 连 `/ws` 被拒（4401）；用户 A/B 各绑设备，互收不到对方帧
2. 认领/解绑后已连接页面订阅范围即时变化
3. 命令接口未登录 401；App 端配对/解绑/空态交互可用
4. `grep -rn 'esp32-001' server/petsense/` 零命中（firmware/ 不计）
5. 生产部署后用户在 App 内认领 esp32-001 即恢复实时数据
6. 服务端单测全绿 + App 三门禁（lint/typecheck/format）全绿
7. 部署健康检查不受影响（`/api/stats`、`/api/action/status` 保持匿名可用）

## 11. 风险与已知代价

| 风险/代价 | 处置 |
|-----------|------|
| 内部 dashboard 网页工具部分接口失效 | 已接受，后续单独适配（可加 admin 通道） |
| `/api/action/status` 去 `latest` 影响内部面板 | 已接受（check_health 只依赖 enabled） |
| device_id 可猜测导致理论冒领 | 本期接受（409 + 解绑确认缓解）；配对码升级在后续 |
| 存量多宠物用户 | API 层限制新增；迁移不动存量数据 |
| 多 worker 重复 MQTT | 不变（P0-3 解决） |

## 12. 对 P0-3 的接口约定

- `broadcast_packet(packet, device_id)` 的"按设备发布"语义是拆进程后 Redis pub/sub 按设备 channel 广播的直接前置
- `refresh_scopes()` 单进程实现；拆进程后改为发布 scope-invalidated 消息
- devices 表的 `last_seen_at` 为 P0-3 独立 ingest 进程提供在线判定数据源
