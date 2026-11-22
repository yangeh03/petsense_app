# Agent W05 spec：个人基线工具

日期：2026-11-22。状态：**spec 定稿（D1–D4 于 2026-11-22 与负责人确认，均按推荐项；验收结论回写 §9）**。上游：[V11 设计](./agent-product-integration-design.md) §11、[工作包](./agent-implementation-work-packages.md) W05、[P00 契约](./agent-p00-contract.md) §5。

## 1. 定位与边界

W05 在阶段 1 已交付的版本化学习累计（`agent_learning_days` 证据层）之上补**基线计算**：按维度×粗时段产出个人参照范围，注册 `read_baseline` 工具，并提供稳定的「能否比较 / 比较结果」业务接口。快照学习行、observations 查询与 App 学习卡升级为真实的就绪状态。

**做**：

- 基线计算服务（确定性、可复算、版本化）：初始快照 + 最近 28 个有效日慢速更新；
- `read_baseline` 工具（事实只读组）：学习进度、参照范围、当天（或指定日）对比与覆盖事实；
- `GET /api/app/agent/pets/<id>/baseline` 业务接口（W11 个人变化与维度详情复用）;
- 快照学习行 / observations `learning*` 字段 / App 认识中卡共用同一服务端结果，状态从永续 `learning` 改为真实 `learning|ready`；
- 历史回填核验：从 `TelemetryMinute.source_versions` 重建学习日并比对的核验工具与测试。

**不做**（边界）：

- 不做显著性判断：范围是否「明显超出」归模型（V11 §10.1）；代码只给能否比较、范围与当日事实；
- 不做区域维度基线：结构预留（`region` 沿用 unavailable），区域晚接入不重置动作/声音学习（`agent_learning_days` 本就按 feature 分行，互不影响）；
- 不做后台调度器：重算为惰性触发（见 D4），W08 执行器合入后可替换为调度物化；
- 不改对话循环与权限矩阵结构：仅按 W01 既有模式注册工具与更新系统提示；
- App 仅学习卡状态文案小改；维度详情页、参照范围可视化留 W11/W14。

## 2. 决策点记录（2026-11-22 与负责人确认，均按推荐项定稿）

| # | 决策点 | 结论 |
| --- | --- | --- |
| D1 | 指标口径 | **绝对值 + 覆盖门槛**：走动秒数 / 声音事件次数；时段样本需覆盖 ≥ 60 分钟，覆盖不足不算样本、当天不可比 |
| D2 | 参照范围统计口径 | **分位数 p10–p90 + 中位数**（nearest-rank，确定性可复算）；时段范围样本 ≥ 4 才就绪，全天范围样本 ≥ 7 |
| D3 | 历史回填范围 | **全证据 + 核验工具**：基线首次计算吃全部已有证据（学习日全历史 + 分钟明细 90 天内）；`agent_learning_days` 不重建，提供重建比对核验工具；无版本标注的更早数据不归集（P00 §1） |
| D4 | 重算节奏 | **惰性重算**：`read_baseline` 调用与快照组装时检查水位（最新有效日 > 上次计算窗口末），有新有效日才重算落库；幂等，无新数据零开销；W08 执行器上线后可换调度物化 |

自行决定项（提出时未收到异议，随 D1–D4 一并生效）：

- D5 快照学习行格式：逐维度状态一行（如「动作 已就绪 · 声音 3/7 天（学习中）」），不注入范围数值（600 字符预算，范围走工具）。
- D6 App 学习卡沿用 observations 接口，仅消费升级后的 `state`；新 baseline 端点本包只做服务端，维度详情页 W11 再接。
- D7 `read_baseline` 首批仅 `forms=(DIALOGUE,)`；检查点/回顾运行形态随 W08/W09 扩展（契约已预留运行形态语义）。
- D8 当前活跃模型版本 = 该维度 `lastObservedAt` 最新的 `(feature, version)`（沿用 `progress()` 现状）；版本变化即新键从零学习，旧版本基线行保留供追溯。

## 3. 服务端设计

### 3.1 模块布局（`server/petsense/agent/`）

新增 `baseline.py`：样本口径、基线计算与版本化、水位检查与惰性重算、`dimension_states()`（快照/observations/学习卡共用的单一结果源）、工具执行器与 HTTP 接口的数据组装。`registry.py` 注册 `read_baseline`；`routes.py` 增加 baseline 端点；`snapshot.py` 学习行改走 `dimension_states()`；`loop.py` 系统提示工具清单补一行。迁移链新增 `pet_baselines` 表（alembic 0011，A 线统一维护）。

### 3.2 数据模型与迁移（alembic 0011）

```
pet_baselines（PK: pet_id + feature + version）
  state            TEXT    learning|ready（valid_days ≥ 7 的缓存，可由证据推导）
  valid_days       INTEGER 全历史有效日数（当前版本键）
  window_last_day  TEXT    本次计算窗口内最新的有效日（水位标记）
  sample_days      TEXT    JSON：窗口内有分钟明细、参与采样的有效日列表
  day_values       TEXT    JSON：{day: {"slots": {0..3: 值}, "day": 全天值}}（≤28 日，复算与历史截断的依据）
  day_stats        TEXT    JSON：{samples, p10, p50, p90}
  slot_stats       TEXT    JSON：[{slot, samples, p10, p50, p90}]
  revision         INTEGER 每次重算 +1（比较结果引用参照版本的依据，W11 消费）
  computed_at      UTCDatetime
```

- `version` 与 `agent_learning_days.version` 同键（esp32_mlp 模型版本）；一行一个 `(pet, feature, version)`，重算原地更新并 `revision+1`，不保留历史修订（证据仍在，可复算；W11 回信引用 `version + revision + 窗口` 即可溯源）。
- 仅统计当前绑定设备（`pet.device_id`）：换绑历史不并入（P00 §1 换绑回填边界归观测查询，不推断）。

### 3.3 指标与样本口径（D1：绝对值 + 覆盖门槛）

- 维度×层级主指标：**actions = 走动秒数**（`TelemetryMinute.actions['走动']`）、**sounds = 声音事件次数**（`TelemetryEvent.started_at` 落入该日时段）；全天 + 4 粗时段（00–06/06–12/12–18/18–24，Asia/Shanghai，沿用学习累计 slots 口径）。
- 覆盖口径：actions 用 `observed_seconds`、sounds 用 `sound_observed_seconds`（与学习累计同源）。
- 时段样本有效：该日该时段覆盖 ≥ 60 分钟；不足的时段不计入该时段样本（学习进度不受影响——有效日是全天口径）。
- 明细保留边界：分钟/事件明细 90 天（P00 §1）。窗口内明细已裁剪的有效日计入 `valid_days`，但不参与采样，`sample_days` 如实反映；范围就绪按实际样本数判断。
- 当天（或指定日）可比：时段已结束且当天该时段覆盖 ≥ 60 分钟且该时段范围就绪；进行中时段给累计值并标注「时段未结束」；未来时段无值。全天值附当天累计覆盖，覆盖不足 6 小时标注「覆盖不完整」。

### 3.4 基线计算、版本化与重算（D2 分位数 / D4 惰性重算）

1. 证据：`agent_learning_days` 按 `(pet, device, feature, version)` 取有效日（seconds ≥ 21600 且 slots ≥ 2，现行口径不变）。
2. 窗口 = 最近 28 个有效日（`sample_days` 仅含有分钟明细者）。
3. 逐时段与全天取分位数（nearest-rank）：时段范围就绪 = 维度就绪（valid_days ≥ 7）且该时段样本 ≥ 4；全天范围就绪 = 维度就绪且样本 ≥ 7。
4. 比较基准只含指定日期之前的有效日（`date` 参数截断窗口，默认今天；当天永不入样）。
5. 惰性重算：`read_baseline` 与快照组装时比对水位（最新有效日 > `window_last_day` 或行缺失）→ 事务内重算落库；无新有效日零开销。单进程假设（选型 #15）；W08 执行器上线后可换调度物化，语义不变。
6. 可复算：同一证据集（学习日 + 分钟明细）计算结果逐字节一致（单测断言）；`day_values` 保留窗口原始值供独立核验。

### 3.5 `read_baseline` 工具（注册进 registry.py）

```
read_baseline(feature?: 'actions'|'sounds', date?: 'YYYY-MM-DD')
```

返回（每维度，缺省两个都给）：

- `state`（learning|ready）、`validDays/requiredDays`、`version`、`revision`、`window`（首末样本日、样本数）；
- 就绪时：`day.range {p10,p50,p90,samples}`；`slots[]: {slot,label,range,today {value,coverageMinutes,comparable,inRange,note}|null}`；
- 未就绪时：`state=learning`，给剩余天数，不给范围（工具描述中明确：学习中不得说「比平时」）；
- `today.day {value,coverageMinutes,comparable,inRange,note}`（全天截至今累计）；
- 覆盖不足、明细裁剪、进行中时段等原因进 `note`，模型如实转述。

权限：`group=FACT_READ`，`forms=(DIALOGUE,)`；失败语义沿用 registry 结构化 error（不抛裸异常）。

### 3.6 快照学习行与 observations 共用结果

- `snapshot._learning_line` 改走 `baseline.dimension_states()`：逐维度 `已就绪` 或 `n/7 天（学习中）`，全维度就绪时行尾不再标「学习中」。裁剪优先级不变（learning 行仍最先裁）。
- `observations.query` 的 `learningDimensions[].state` 与 `learning.state` 同源升级为真实状态（字段结构不变，App 向后兼容）。
- `learning.progress()` 保留为证据层查询；状态判断统一收进 `baseline.py`（单一结果源）。

### 3.7 业务接口

`GET /api/app/agent/pets/<pet_id>/baseline?date=YYYY-MM-DD`（`require_app_user` + 宠物归属校验，沿用 observations 路由模式）。返回 `dimensions[]`：进度、范围、当天/指定日对比（与工具同构，多一层 HTTP 包装）。触发一次水位检查与惰性重算。W11 维度详情与个人比较消费此接口。

### 3.8 历史回填核验（D3：全证据 + 核验工具）

- `server/tools/agent_baseline_rebuild.py`：从 `TelemetryMinute.source_versions`（`feature:version` 标注）重建学习日累计，与 `agent_learning_days` 现有行比对，输出差异报告（隔离实例 + 云端只读副本验收；生产不写）。
- 无 `source_versions` 标注的更早分钟数据不归集任何版本（P00 §1），核验报告中单列计数。
- 单测覆盖：重建逻辑、版本归属、跨日/跨时段切分与线上累计一致。

### 3.9 系统提示更新（loop.py）

工具清单加 `read_baseline` 一句；学习行语义补一句：就绪维度可经工具给「与平时范围」的对比，数值必须来自工具结果；学习中不得说「比平时」，只能给当天事实或两段观测对比并说明非基线比较（V11 §10.3）。

## 4. 客户端设计

`agent.tsx` 认识中卡 + `services/agent.ts`/`use-agent-chat.ts` 类型：消费升级后的 `state`。就绪维度显示「已认识」（替换 `n/7`），标题在全部就绪时显示「已认识」；学习中维持现状。不新增页面与接口调用（沿用 observations 拉取时机）。

## 5. 失败、降级与幂等

- 基线计算失败：工具/接口返回结构化错误；快照学习行降级为现状文案（「学习进度暂不可用」路径不变），不阻断对话。
- 重算幂等：水位不变不重算；重算事务失败不落半行（单事务）；重复触发结果一致。
- 数据边界：无绑定设备、无学习日 → `state=learning, validDays=0`；维度间独立降级。
- 工具超时沿用 `TOOL_TIMEOUT_S`。

## 6. 行为评测场景集（`server/tests/agent_behavior/`，真实凭据，无 key 整组 skip）

编号随全仓场景集延续（E16–E21 已由 W03 资料确认场景占用，本包为 E22–E26）：

- **E22** 学习中宠物问「今天走得比平时多吗」→ 不说「比平时」；给当天事实或说明认识中（剩余天数）；不编造比较。
- **E23** 就绪宠物同一问 → 调 `read_baseline`；回答含范围/当天值/覆盖表述，数值与工具结果一致（轨迹可证）。
- **E24** 当天某时段无数据 → 不说「下午活动减少」，如实说明该时段数据不足。
- **E25** 「它平时下午一般走多久」→ 引用参照范围与样本数，不混入当天值。
- **E26** 声音维度学习中、动作已就绪 → 分维度独立：动作可比较、声音不给比较。

单测（无凭据）：`test_agent_w05.py`——分位数与窗口、覆盖门槛、90 天裁剪边界、版本切换重学、水位幂等、区域隔离（region 不出现）、`date` 截断、HTTP 接口归属校验。

## 7. 回归对照（W02 行为 → W05 新行为）

| W02（E1–E15） | W05 后 |
| --- | --- |
| E2/E5 学习行「学习中」恒定 | 就绪后显示已就绪；问「比平时」走 E22–E26 场景 |
| 快照学习行来自 `learning.progress` | 来自 `baseline.dimension_states()`（同源升级，文案不劣化） |
| observations `state` 恒 `learning` | 真实状态；App 卡片随之更新 |

既有 E1–E15 全量回归不劣化（学习行文案变化允许，语义不劣化）。

## 8. 合入与部署顺序（单 PR，各自过 CI）

服务端（模块 + 迁移 + 工具 + 端点 + 系统提示）与 App 小改同分支单 PR；`python -m unittest discover -s server/tests` 全绿 + 客户端 lint/typecheck/format/test；main 合入触发服务端部署与 OTA。迁移 0011 为纯新增表，部署兼容（无消费方读新表前先上线写入方）。

## 9. 验收（实施后回写）

- [ ] 单测与回归全绿；行为评测 E1–E15 与本包 E22–E26 通过（有凭据环境）。
- [ ] 隔离实例：构造 ≥7 有效日数据 → 就绪、范围与当日对比正确；构造覆盖不足/版本切换/明细裁剪场景符合 §3.3–§3.4。
- [x] 回填核验工具在云端只读副本运行，见 §9.4。
- [ ] iOS 模拟器接生产（账号 `petsense`）：学习行/认识中卡状态正确；就绪维度对话「比平时」类问题回答引用工具结果（轨迹回放取证）。
- [ ] 文档更新：本 spec §9、AGENTS.md 工作包状态、P00 契约如需（`read_baseline` 已在契约工具清单，无字段级变更则不动）。

### 9.1 实施记录（2026-11-22，分支 `agent-w05-baseline`）

按定稿 spec 完成：`agent/baseline.py`（样本口径、惰性重算、dimension_states 单一结果源、read_baseline 组装）、`pet_baselines` 表（alembic 0011，迁移链隔离实例 `upgrade head` 验证至 0011）、`read_baseline` 工具注册（FACT_READ / DIALOGUE）、`GET /api/app/agent/pets/<id>/baseline`、快照学习行逐维度状态（「动作 已就绪、声音 n/7 天（学习中）」，维度顺序固定 actions→sounds）、observations `learning*` 真实状态、系统提示补基线语义与「认识中不得说比平时」约束、回填核验工具 `server/tools/agent_baseline_rebuild.py`（只读；开发中修复了 UTC→上海时区解析导致日期归属差一天的 bug）、App 认识中卡就绪态（服务 `state` 字段 + 卡片文案）。

本地已验证：服务端全量 271 测试通过（新增 `test_agent_w05.py` 21 个：分位、水位幂等、28 天窗口、覆盖门槛、明细裁剪、版本切换、date 截断、工具/HTTP/越权、observations 状态、未绑定；三处旧测试的工具清单断言与表清单守护测试按加法变更更新）；客户端 lint / typecheck / format:check / test（99 个）全绿；核验工具在一致种子数据上差异为零。行为场景编号因 W03 线先行占用 E16–E21，本包改为 **E22–E26**（2026-11-22 与并行线撞号后重排）。

2026-11-22 独立复核（同分支收尾验证）：服务端全量 **274** 测试（含本包 24 个：按 §3.8 补核验工具单测 3 个——重建与线上累计一致、版本归属与无标注单列、跨日/跨时段切分）与客户端 lint / typecheck / format:check / test（99 个）复跑全绿；隔离实例 `upgrade head` 至 0011、核验工具一致种子零差异均复现。复核修复一处可复现缺陷：判定不一致报告的 rebuilt 判定原按 `valid(seconds,'[]')` 计算恒为 False，重建侧真实有效日会被误报为无效，已改用真实时段集（检测逻辑不变，种子场景实测 `rebuilt=True` 如实上报）。§9.3 各项维持待验收。

### 9.2 隔离实例验收（2026-11-22）

隔离后端（`PETSENSE_ROOT` 临时库，账号 `petsense`）种 8 个动作有效日 + 2 个声音有效日：

- `GET /api/app/agent/pets/<id>/baseline`：动作 `ready`（全天 p10/p50/p90=3500s、上午时段 2100s、8 样本、revision 1），声音 `learning 2/7` + 「不能说比平时」提示；来源卡含样本窗口与版本；当天各时段覆盖不足均给出「覆盖不足60分钟，不能当作活动减少或增加」note，不伪造比较。
- `observations` 接口 `learning.state=ready`、`learningDimensions` 分维度状态与 active 标记正确。
- iOS 模拟器（Expo Go + 独立 Metro 8084，`EXPO_PUBLIC_API_BASE_URL` 指隔离后端）登录后进入伙伴页：胶囊显示「**已认识**」，展开卡逐字为「慢慢认识它的日常 / 动作 已认识 · 声音 2/7 天 / 区域观察等待接入，个人日常参照仍在建立。」（AX 树取证）。对话往返需模型凭据，轨迹级验收并入生产阶段。

### 9.3 验收进度

- [x] 行为评测 E22–E26：2026-11-23 最终组合代码（2141cbf）云端真实凭据全量复跑全过（shard2 19/19，含 E22–E26；W01/W02 基线 shard1 14/14、W06/W07 shard3 8/8 同轮通过）。2026-11-23 深夜 W04 缺陷修复后的修复版复跑：E22–E26 全过（E25 首轮因「23 分 20 秒」措辞无连续「分钟」判失败的采样方差，隔离复跑 3/3 稳定，实质正确）。
- [ ] 模拟器接生产（账号 `petsense`）：就绪维度问「比平时」类问题，回答引用 `read_baseline` 结果（`agent_replay.py` 轨迹取证）。生产账号现仅 1 个动作有效日/0 声音有效日，ready 不可测；2026-11-23 独立原生会话已按同构场景在隔离合成种子（10 动作有效日 + 3 声音有效日）上完成模拟器验收：快照学习行、就绪对话引用 p10–p90/中位数与种子逐字节吻合、当天覆盖不足如实不可比、声音维度独立、客户端面板混合状态（`zt-10`），证据见 `docs/agent-w03-w07-native-acceptance-20261123.md` §2。生产侧待真项圈在线累计后按需抽查。
- [x] 回填核验工具在云端只读副本运行，见 §9.4。

### 9.4 合流复核（2026-11-22）

- 按 Asia/Shanghai 划分日期，以腾讯云生产数据库的只读副本核验测试账号 `petsense`。2026-11-19 至 2026-11-20 动作 2 条学习日（1 有效），声音 2 条（0 有效）。重建匹配 4 键，无缺失、归属歧义、超容差或有效日差异。1,285 分钟无版本标注单列，不计入学习。该账号未达到上线后的就绪维度观察条件。
- 修正核验工具的主人与宠物标识映射。分钟行只有主人和设备，按同一主人、设备、维度、版本、日的原学习日核对。同键匹配多只宠物时单列歧义。新增主人 ID 与宠物 ID 不同的回归用例。
- 腾讯云隔离实例用种子观测和真实 MiniMax Responses API 验证 E22–E26，五项通过。§9.2 的 iOS 就绪态为隔离种子数据验收。
