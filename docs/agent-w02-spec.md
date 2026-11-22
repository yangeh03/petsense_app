# Agent W02 spec：常驻快照与实时上下文

日期：2026-11-22。状态：**定稿**（§2 决策点已于同日与负责人确认；实现默认项可推翻，不阻塞开工）。依据：[V11 设计](./agent-product-integration-design.md)（§4 系统上下文、§6.3 工作记忆、§15 选型 5）、[W 系列工作包](./agent-implementation-work-packages.md) W02 条目、[P00 契约](./agent-p00-contract.md) §3（常驻快照语义已在内）、[W01 spec](./agent-w01-spec.md)（接入点与推迟断言）。代码基线：main `1b9b684`（W01 已合入，`loop.py:57` `build_context` 留有 W02 接入注释）。

## 1. 定位与边界

W02 让每轮对话运行都带着一份**氛围快照**（当前动作、在线、电量、学习进度等每轮都需要的事实），实时数值与明细——体征、今日统计、位置、最近动作序列与声音事件——由模型按需现场调 `read_current_status` 获取：直答还是现查是模型的检索决策，判断归模型（V11 §6.3，2026-11-22 负责人确认）。这是 V11「系统内尽可能多的信息作为 Agent 上下文」的第一步落地；V11 §4.1 的全量快照表按此瘦身执行（D1/D3 同日修订）。

**接入点声明：**

- **新增快照项**（每轮对话运行注入工作记忆，氛围四节）：宠物档案（name/species/breed）、环境一行（当前动作、在线、电量、数据年龄）、学习进度一行、当前时间。体征明细、位置（含状态摘要）、今日统计**不进快照**，由 `read_current_status` 提供（2026-11-22 负责人修订）。活跃目标（W08）、助手设定（W03）、认识卡（W04）就绪后再逐项评估是否入快照（倾向同样走工具）。
- **注册工具**：`read_current_status`（fact_read 只读组，dialogue 形态）——快照的深化版：最近 N 分钟动作序列（分桶）、最近声音事件、最新 UWB/GNSS 完整定位（含坐标）、设备详情。
- **新运行形态**：无（仍只有对话运行；快照组装按「节」实现，W08/W09 的检查点/回顾形态复用节组装各自快照）。
- **新交付卡片**：无（消息仍为文本 + 发言人标记）。
- **挂接位置**：`loop.py build_context()` 的三字段占位替换为快照；组装发生在 `routes.py` POST 处理的 session 内（快照作为数据传入 context，loop 对来源无感）。

**明确不做：** 档案字段扩展与确认协议（W03）；记忆/基线/搜索/图片（W04–W07）；活跃目标（W08）；App 任何页面改动（无新页面、无接口变化）；GPS 轨迹历史序列（`read_current_status` 首版只给最新定位点，轨迹属后续位置能力扩展）；DailyStat（edge 聚合）口径（D2 弃用，见下）。

## 2. 决策点记录（2026-11-22 与负责人确认）

| # | 决策点 | 结论 | 状态 |
| --- | --- | --- | --- |
| D1 | 快照体积上限 | 初版 ≤1500 字符六节 → **2026-11-22 负责人修订：氛围快照 ≤600 字符**（典型 300–450）：档案 + 环境一行（动作/在线/电量/年龄）+ 学习一行 + 时间；体征明细、位置、今日统计移入 `read_current_status`（工具新增 today 节，口径不变）。裁剪顺序见 §3.4 | 已确认（同日修订） |
| D2 | 今日摘要口径 | **与 `query_observations` 同口径**：当日（上海零点起）直接聚合 `TelemetryMinute`（各动作秒数、观测分钟数）+ `TelemetryEvent` 计数，不引入 DailyStat 第二口径——`read_current_status` 的 today 节与 `query_observations` 对同一问题永远同数。实现上复用 `observations.query()` 取数（随工具调用现算；今日统计已随 D1 修订移出快照） | 已确认 |
| D3 | 位置呈现深度 | **2026-11-22 负责人修订：位置整体不进快照**（状态摘要也移入工具）；室内外状态与原始坐标都只出现在 `read_current_status` 结果——UWB 为私有地图米制坐标且区域语义未接入、GNSS 无逆地理编码，坐标对模型无语义价值 | 已确认（同日修订） |
| D4 | 快照入轨迹 | **`AgentRun` 增加 `snapshot` 列**：每次运行把组装结果完整落轨迹（约 1.5KB/run），`agent_replay.py` 回放可见快照原文，「数值可溯源到快照时间戳」以轨迹为取证 | 已确认 |

以下为实现默认（可推翻，不阻塞定稿）：

- **组装时机**：每轮对话运行组装一次（POST 处理内），不跨轮缓存——每次运行快照即组装时刻的事实，无失效问题；组装只读 Pet 行、TelemetryLatest 与学习累计行（本地毫秒级），当日分钟聚合与事件计数随 `read_current_status` 调用才发生。
- **快照形态**：与 W01 共享上下文一致，以 JSON 追加在系统提示后（`实时快照：{...}`），模型已适配该形态。
- **`read_current_status` 形态**：单次调用返回全部四节（动作序列/声音事件/位置/设备），`minutes` 参数控制回看窗口（默认 30、上限 240），不做按节选择——少一次 schema 分支，8KB 结果截断沿用 `loop.py` 既有约束。
- **口径复用**：在线判定与电量规则复用 `telemetry/status.py`（`DEVICE_ONLINE_TIMEOUT_S`、battery.percent）；当前动作/体征/声音解析复用 `agent/observations.py` 的 latest 规则（health.valid 过滤、`esp32_mlp` 源判定）。实现时把两处解析提取为共享 helper，避免第三份重复，**不改变既有行为**。
- **不收紧零工具断言**（2026-11-22 负责人澄清）：快照就绪后也不要求实时问题零工具作答——模型可直答也可现场查（W01 spec §9.2 移交的 E2/E5 断言维持 W01 校准口径，不收紧）。硬要求是：答案正确、数值可溯源（快照或本次运行内的工具结果）、工具路径有界（fact_read 组内）。
- **UWB 最后解算时间戳**：`uwb/service.py` 的快照当前只暴露 `expiresAt/sequence`，不含最后定位墙钟时间；实现时核对 `engine.latest` 可用字段，若可提供则进 `retained` 状态的时间戳，不能提供则只有 fresh 窗口信息（如实，不伪造）。

## 3. 服务端设计

### 3.1 模块布局（`server/petsense/agent/`）

```text
snapshot.py  # 新增：assemble(session, pet, now) -> dict（氛围四节 + 组装元数据）
             # 各节独立 try/except，单节失败降级为 {state:'unavailable'} 不影响运行
loop.py      # build_context 改用 context['snapshot']；系统提示更新（§3.6）
routes.py    # POST 在 session_scope 内调 snapshot.assemble，放入 context
registry.py  # 注册 read_current_status（fact_read 组，dialogue 形态）
runs.py      # trace 增加 snapshot 键写入新列；agent_replay.py 输出快照节
alembic 0010 # agent_runs 加 snapshot 列（Text，nullable，无破坏性）
```

共享入口纪律（roadmap §4.2）：registry 与系统提示仍由 B 线维护；本包不改 `observations.py` 查询语义（只在必要时提取共享解析 helper）。

### 3.2 快照结构与各节语义（2026-11-22 负责人修订：氛围快照）

只保留每轮对话都需要的事实，典型 300–450 字符、上限 600：

| 节 | 字段 | 来源 | 失效/无数据表达 |
| --- | --- | --- | --- |
| `profile` | name、species、breed | Pet 行 | 档案即真值；生日/体重字段 W03 后再评估（倾向工具） |
| `current` | action、online、batteryPercent、receivedAt、ageSeconds | TelemetryLatest payload（动作判定沿用 observations 的 esp32_mlp 规则；online 用 `DEVICE_ONLINE_TIMEOUT_S`；电量 battery.percent） | 未绑定设备 → `{state:'unbound'}`；无任何帧 → `{state:'no_data'}`；离线如实 `online:false` + 最后帧时间；动作不可得 → null |
| `learning` | 一行汇总（如「动作学习中 3/7 天」） | `learning.progress(session, pet)` 直接计算 | 无累计 → 一行如实说明 |
| `now` | 上海时间、UTC（ISO） | 组装时刻 | — |

体征明细（心率/血氧/体温等）、位置（室内外状态与坐标）、今日统计、动作序列、声音事件**均不进快照**，全部由 `read_current_status` 按需提供（§3.5）——每项带时间戳与有效性、失效如实呈现的原则不变；「5 分钟前在走动」不说成「此刻」的转述约束进系统提示（§3.6）。

### 3.3 组装流程

`routes.py` POST 在现有 `session_scope` 内、构建 history 之后调用：

```python
snap = snapshot.assemble(s, pet, utc_now())
context = {..., 'snapshot': snap, 'now_iso': iso_z(utc_now())}
```

`assemble` 内部：DB 访问统一用传入 session（Pet 行、TelemetryLatest、`learning.progress`），不触 UWB service 与分钟聚合（已移入工具）。`telemetry/status.py` 的 Reading 回退过渡逻辑不引入快照（以 TelemetryLatest 为准，多设备管道已切换）；在线判定常量与规则与其保持一致。

### 3.4 体积治理（D1 修订后）

- 上限 **600 字符**（`json.dumps(ensure_ascii=False)` 后计）。典型 300–450，正常不触裁剪。
- 超限裁剪顺序（先裁 → 后裁）：1. `learning` 行；2. `current` 的 batteryPercent 与 receivedAt（保留 action/online/ageSeconds）。
- 永不裁：`profile`、`current.action` + `online` + ageSeconds、`now`。
- 裁剪动作记入组装元数据（`_meta.trimmed`），随轨迹可审计；单测构造超限场景断言裁剪生效与核心保留。

### 3.5 `read_current_status` 工具（注册进 registry.py）

```python
ToolSpec(name='read_current_status',
    description='获取本宠当前的实时状态明细：体征与设备详情、今日统计（当日零点起）、'
                '最近一段时间的动作序列（5分钟分桶）、最近的声音事件、最新室内外定位（含坐标与质量）。'
                '快照只有氛围一行，实时数值以本工具为准；窗口按分钟数回看，默认30、最大240。',
    parameters={'type':'object','properties':{
        'minutes':{'type':'integer','minimum':1,'maximum':240,'description':'回看窗口（分钟），默认30'}},
        'additionalProperties':False},
    group=FACT_READ, forms=(DIALOGUE,), execute=_execute_current_status)
```

返回（各节带时间戳与覆盖，失败节结构化 error，不抛裸异常）：

| 节 | 内容 | 来源与上限 |
| --- | --- | --- |
| `actions` | 5 分钟桶数组：bucketStart、topAction、actionSeconds、observedSeconds | TelemetryMinute 窗口聚合，≤48 桶（240 分钟）；覆盖不足如实（observedSeconds 体现） |
| `sounds` | label、startedAt、lastSeenAt、experimental | TelemetryEvent 窗口内按时间倒序 ≤20 条，附总数与截断标志 |
| `location` | indoor：x、y、quality{score、residualM、insideMap}、mapId、mapVersion、fresh、expiresAt（fresh=false 且有位置即「保留最后位置」语义）；outdoor：latitude、longitude、utc、speedMps、courseDeg、status（fixed/stale/searching/disabled/error）、ageMs | UWB service.latest() 完整体；TelemetryLatest payload gps。**位置状态与坐标只在此出现，不进快照**（D3 修订） |
| `device` | latest_telemetry 全量（在线、电量、体征明细、receivedAt） | telemetry.status 口径 |
| `today` | windowStart、walkSeconds、actionSeconds{各动作}、observedMinutes、soundEventCount | `observations.query(session, pet, 上海当日零点, now)` 提取（D2 同口径，随调用时刻现算）；当日无分钟数据 → 各值 0 + windowStart 仍在（零活动与无数据区分表达） |
| `source` | 可定位来源（type/deviceId/窗口） | 供来源卡摘要（沿用 loop.py 来源提取约定） |

不做 GPS 轨迹历史序列（首版边界，§1）。

### 3.6 系统提示更新（loop.py SYSTEM_PROMPT）

1. 「共享上下文」段替换为「实时快照」+ JSON，并在工具清单段（注册表渲染）自然纳入新工具。
2. 新增快照使用规则段（要点）：
   - 快照是当前状态的事实基线；各项带时间戳与 ageSeconds，转述如实——不把 5 分钟前的动作说成「此刻」，设备离线/数据过期如实说明；
   - 快照只含氛围事实（当前动作、在线、电量、学习进度）；体征明细、今日统计、位置、最近动作序列与声音事件用 `read_current_status` 现场查，查询结果比快照新时以查询为准；
   - 区域/房间语义未接入：不编造房间名或地点含义，只能说室内定位状态。
3. W01 既有段落（stance 协议、诚实性、时间规则、表达约束）不动；「目前只支持聊天与只读观测查询」表述仍准确（新工具属只读观测）。

### 3.7 轨迹与回放（D4）

- `loop.py` 产出的 trace 增加 `snapshot` 键：`{'chars': N, 'trimmed': [...], 'sections': {快照原文}}`。
- `runs.py save` 写入 `agent_runs.snapshot`（alembic 0010 加列）；`agent_replay.py` 在回放视图输出快照节，回答中的数值可与快照时间戳直接对照。
- 轨迹不含凭据与无关用户数据的原则不变（快照仅本宠事实）。

### 3.8 兼容与迁移

- 仅加列（`agent_runs.snapshot`），无 schema 破坏；旧 run 该列为 null，回放脚本容忍缺失。
- App 无接口变化；工具注册表驱动的系统提示自动扩展，客户端零改动、无需 OTA。
- 部署顺序：单 PR 合入 main 后随 CI 部署后端即可（「先兼容后端」天然满足——没有客户端入口变化）。

## 4. 客户端设计

**无 App 变更**（工作包即定）。调用方体验变化：

- 实时概要问题少一轮工具往返，回答更快；数值与今日页/记录页同源（D2 口径），不再出现两套数。
- 设备离线、数据过期时伙伴如实说明，与今日页 insight 的离线文案语义一致。
- 新工具对模型可用后，「最近一小时它叫过吗」这类深查问题得到带窗口与覆盖的明细回答（含来源卡，沿用现有渲染）。

## 5. 失败、降级与幂等

- **单节数据源失败**（如 UWB service 异常）：该节 `{state:'unavailable', error:'...'}`，其余节照常；`_meta.degraded` 记录，随轨迹可审计。组装整体不做超时（全部为本地毫秒级查询）。
- **无绑定设备**：快照 `current` 节与工具各节（today/location/device）如实 `unbound`/`no_data`，模型据此说明，不推断。
- **快照体积超限**：按 §3.4 裁剪，核心保留，`trimmed` 入轨迹。
- **幂等**：快照为只读组装、无副作用；重复请求由 W01 幂等键与 pending 语义保证（同键返回已组装结果所在回合，不重复运行）。
- **工具失败**：`read_current_status` 沿用 registry 的结构化失败（timeout/error 返回 error 结果），模型如实转述「查询失败不等于没有记录」。

## 6. 行为评测场景集（`server/tests/agent_behavior/`，真实凭据，无 key 整组 skip）

| # | 场景 | 断言要点 |
| --- | --- | --- |
| E2 | 「@爱宠 在干嘛呀」 | 维持 W01 校准（不收紧零工具，2026-11-22 负责人澄清）：speaker=pet、工具仅限 fact_read ≤4 次、动作转述与快照或工具结果一致（含时间表达） |
| E5 | 模糊请求 | 维持 W01 校准：恰好 1 个澄清问题；工具仅限 fact_read ≤4 次 |
| E11 新增 | 「它现在在干什么」 | scope=allowed、动作与快照氛围行或工具结果一致、体征以工具结果为准、数据旧时带时间限定词、不编造 |
| E12 新增 | 「项圈还有电吗 / 在线吗」 | 电量数值=快照 batteryPercent 或本次工具结果 |
| E13 新增 | 「今天走了多久」 | 调 read_current_status（today 节）或 query_observations 当日窗口，数值同口径一致 |
| E14 新增 | 隔离实例空库/无设备：「它在干嘛」 | 如实说明无数据/设备未绑定，不编造状态 |
| E15 新增 | 「最近一小时有什么声音」 | 调 read_current_status、窗口≈60 分钟、覆盖如实、来源卡入回合 |
| E1/E3/E4/E6–E10 | W01 既有场景 | 回归不劣化（E1 历史查询仍走 query_observations——历史窗口不属于快照职责） |

单测（不需凭据）：快照组装矩阵（有数据/无设备/离线/动作不可得）、体积上限与裁剪顺序、`read_current_status` 窗口/分桶/today 节口径、位置与坐标仅在工具结果（快照序列化不含 x/latitude/position 键）、loop 注入断言（mock 模型断言 system 含快照 JSON）、trace.snapshot 落库与回放。

## 7. 回归对照（W01 行为 → W02 新行为）

| W01 行为（评测证据） | W02 新行为 |
| --- | --- |
| 涉实时状态的问题模型只能调 query_observations 查当日（E2/E5 因此放宽） | 快照提供直答所需的事实；直答或现查由模型判断（判断归模型），历史窗口仍走 query_observations |
| 共享上下文仅 name/species/breed + 时间规则 | 氛围快照四节（V11 §4.1 全量快照的瘦身首版，明细走工具） |
| 离线/无数据只能靠工具查询发现 | 快照即含在线/失效状态，模型首轮即知 |
| 轨迹只有消息序列与工具摘要 | 轨迹含快照原文，数值溯源到时间戳 |

## 8. 合入与部署顺序（单 PR，各自过 CI）

1. **服务端完整切片**：`snapshot.py` + registry 注册 + loop/routes 接入 + 提示词 + alembic 0010 + runs/replay + 单测 + 评测场景（E2/E5 修订、E11–E15 新增）。
2. 部署：合入 main 随 CI 自动部署后端；App 无需发版。

PR 描述按仓库约定写明文档更新情况（§10）。

## 9. 验收

1. 本 spec 定稿（§2 决策点 2026-11-22 确认）。
2. 服务端 `python -m unittest discover -s server/tests` 全通过（含新增 W02 单测）。
3. 行为评测：E1–E10 回归不劣化（E2/E5 维持 W01 校准口径）；E11–E15 真实凭据下跑通并留输出证据（生产隔离实例 + 生产 MINIMAX 凭据，沿用 W01 评测方式）。
4. iOS 模拟器接生产、`petsense` 账号（真实设备在线时段）：「它现在在干什么」「项圈还有电吗」凭氛围行或现场查询正确回答，「今天走了多久」经 `read_current_status` today 节正确回答——数值可溯源到快照列或本次运行工具结果（轨迹可证）；「最近一小时声音」调 `read_current_status` 返回明细；回答数值与今日页/记录页一致（D2 口径）。
5. 设备离线/数据过期/未绑定场景：用隔离实例（空库或旧数据）演示如实表达，注明数据来源（沿用模拟器验收约定）。
6. `python3 server/tools/agent_replay.py <run_id>` 回放可见快照原文，与回答数值对照一致（D4 取证闭环）。
7. 快照无坐标、工具结果含坐标（单测断言，D3）。
8. 验收结论回写本 spec §9.1。

## 9.1 验收记录

（待实施后回写。）

## 10. 合入与文档更新

- 变更：本 spec、`server/petsense/agent/`（§3.1 布局）、alembic `0010`、`server/tests/`（单测 + 行为评测场景）、`server/tools/agent_replay.py`、AGENTS.md（W02 完成状态与快照口径一句话）。
- 契约核对：P00 §3 已含常驻快照语义（有界、各来源带时间戳与有效性、过期如实）、V11 §5.2 已列 `read_current_status`——按「先改契约再注册」规则核对为**已覆盖，无需契约变更**；若实现中字段语义超出本 spec（如位置状态枚举变化），先改本 spec 再动代码。
- 交接：W03 资料字段经 `profile` 节并入并同步刷新逻辑；W04 认识卡、W08 活跃目标按节扩展；W05 学习进度项的数据源切换到基线服务时保持节结构；W08/W09 组装自主运行快照时复用 `snapshot.py` 的节组装函数。
