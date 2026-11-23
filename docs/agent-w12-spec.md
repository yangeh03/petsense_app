# W12 收件箱与通知定位

2026-11-23，负责人已确认 D1–D7。复用 W08 委托、W09 主题、W11 个人变化。W13 以本包的稳定 ID 和消息分类为输入。

## 决策

| 编号 | 已确认 |
| --- | --- |
| D1 | 安静档下已持久化的非紧急关怀主题可在收件箱查看，不上今日预览，不触发系统通知。保持 W09 的主动会话策略。 |
| D2 | 今日最多一条未读预览。优先仍开放的关键设备告警、到期回信、可预览的关怀。同一优先级取最新。学习完成仅进收件箱。 |
| D3 | 正常打开目标成功才自动标为已读，另支持单条及全部已读。已读和告警解除各自持久化。 |
| D4 | 收件箱存稳定来源引用、类型、时间和已读，正文及证据从原主题或回信读取。通过稳定收件箱 ID 解析原目标。 |
| D5 | 启用时只补当前仍开放的设备告警，其他类型从启用后开始入箱。未接管的本地规则继续展示。 |
| D6 | 跳转与返回保留来源会话的草稿、滚动位置及来源展开状态，提供回原会话或收件箱路径。 |
| D7 | 仍开放且严重度为 warning 或 critical 的设备告警可进入关键预览和推送。info 设备事实只进收件箱。 |

## 服务端和跨端契约

- 在 Agent 域持久化收件箱索引，以拥有者、宠物、来源类型和稳定事件键保证幂等。同一关怀主题的重复扫描重用条目，独立的硬件升级阶段可由既有事件键形成新条目。委托按持久交付引用去重，学习完成沿用维度加版本。来源明确区分设备事实、关怀、到期回信、学习完成，保留 W11 高相关比较标记供 W13 消费。
- 在创建来源的事务内创建条目，或使用可恢复的投影水位保证创建后最终可见。恢复、重启、重复交付及初次迁移不可复活已读。初次补入当下仍开放设备告警，后续恢复和回放不能把已关闭的历史告警重新补入；站内补入不触发历史系统通知。关闭或来源删除时仍可安全展示历史，来源不可访问时给出明确状态。
- 宠物归属需在列表、详情、已读和解析各端点检查。分页用稳定游标，服务端给未读总数、可预览条目以及接管能力。全部已读按请求生效时的明确可见水位操作，避免把新增但未显示的通知一并处理。终态与旧客户端保持兼容。
- 深链通过 `petsenseapp://` 和应用内稳定路由定位，入参只包含收件箱 ID。登录后服务端解析消息类型与目标。前台、直接链接、冷启动和登录续接均使用同一解析逻辑。已读请求只在目标成功显示后自动执行，404、他人 ID、离线及目标加载失败不触发自动已读。
- 今日铃铛以及预览共用服务端列表、计数和已读语义。保留未接管的本地告警，站内用来源区分并防止设备规则重复。社区保持已有流程。伙伴同一条回信支持回到原计划、展开时段依据及回到相应日或周回顾。旧版 App 的本地行为沿用 W09 兼容边界。所有持久通知语义同步写回 P00 契约。

## 验收标准

| 编号 | 功能与证据 |
| --- | --- |
| W12-A1 | 迁移后仅补开放设备告警，新建四类对象各产生一条。重启、重投、竞争写入不重复；旧客户端与未接管本地规则回归。 |
| W12-A2 | 安静档非紧急关怀只进站内，学习完成不预览；设备关闭后退出关键优先级。对 4 类未读混排逐项检查预览和计数。 |
| W12-A3 | 铃铛、今日预览、伙伴与冷启动链接打开同一持久来源。在不同会话或重新登录核对已读同步，单条和全部已读均幂等。打开失败和跨账号请求不改已读；已读不关闭告警。 |
| W12-A4 | 打开历史回信不生成新回合；主题、计划、回信、观测依据和日周回顾可往返。空态、断网、无权限、目标缺失及未登录续接均要有明确结果。 |
| W12-A5 | iOS 模拟器功能验收覆盖通知列表、今日预览、深链、未读变化、返回与草稿保留。写入和缺数场景走隔离实例，生产只读抽验来源和链接。 |
| W12-A6 | 浅色及深色截图覆盖铃铛、未读、已读、空态、失败态、关键告警和来源展开。对照今日与伙伴现有页面检查卡片、投影、间距、颜色语义和 theme token，核对安全区、字号与长文本。 |

上线前完成服务端全量 unittest、客户端全套检查和 Web 构建，记录截图、数据来源与 CI。交付给 W13 的契约包括稳定 ID、链接解析、通知类型、推送候选条件和历史补入标识。

## 复核整改记录（2026-11-23，Codex 复核意见）

### 1. 精确的来源→回合身份

W09 的真实 request key 是 `care:event:<topic>:<phase>`（phase ∈ open/escalate）与
`care:review:<daily|weekly>:<pet>:<窗口>`。旧实现用 `LIKE 'care:event:<topic>:'` 前缀
匹配，会把任意后缀（甚至别的阶段）误配。现改为**精确 id 匹配**（`inbox_eligibility
.event_request_ids` / `.review_request_ids`），命中的回合 id 持久化在条目 `turn_id` 上：
重开/升级阶段各自可区分；日/周口径按真实 key 分别尝试，不靠前缀猜测。

### 2. 发布晚于水位 → 富化补齐（不复活已读、不追溯推送）

水位推进可能早于 W09 发布。现在每次投影都跑一次**富化**：按精确 request id 重新解析
回合身份，刷新 `published`/`turn_id`/预览档位/推送候选，**保留稳定 id、创建时间与
`read_at`**（已读不被复活）。历史补入（`backfilled=1`）永不成为推送候选。

### 3. 中心策略（预览与 W13 候选同一处）

新增 `inbox_eligibility` 的中心策略（`PolicyInput.preview_tier` / `.push_candidate`），
今日预览与 W13 推送候选读同一份输入：
- 推送候选 = 已交付 ∧（设备告警 ∨ 到期回信 ∨ 个人高相关三条件齐备）；**安静档不影响**
  设备告警与到期回信（W13 D7）；
- 个人高相关要求：W11 **冻结的证据** + 模型**明确给出 'high'** + W09 **已批准交付**；
  缺失相关性不得默认按 high（缺失即不成立）；
- 学习完成与一般关怀**一律不是**推送候选；
- 「关键设备告警是否只算 critical」仍待负责人确认：口径集中在 `KEY_DEVICE_SEVERITIES`
  一处常量，**本轮未改产品含义**，确认后只改这一处。

### 4. 归属与过滤（收紧）

- 宠物过滤区分「未指定」（本人全部宠物）与「无效/他人」（400/404，**写操作前先拒绝**）；
  旧实现把他人宠物当成「未指定」，会让「全部已读」误伤本人其它宠物。
- 列表/详情/已读/解析各自校验：条目归属本人 ∧ **宠物仍是本人名下**。
- 来源必须在**同一账号 + 同一宠物**下解析：换绑关闭（`closed_reason='rebind'`）或来源
  归属变更时返回 `out_of_scope` + 明确原因，**不水合原绑定正文**（不泄露）。
- 深链解析是纯读（不改已读）；目标不可用时未读保持不变。

### 5. 索引只存引用

`agent_inbox_items` 删掉 `title`/`summary` 两列（正文不进索引表）：列表/详情/预览的
标题与摘要在读取时从来源水合（`inbox.hydrate`）；来源不可访问时只给显式安全历史状态。
交付扫描改为**按宠物定向 join + 有界 limit**（不再全量载入他人/本人全部交付），投影状态
行用 upsert 兜底并发；列表分页多取一条判定是否还有下一页。

### 6. D6 往返（不再停在占位路由）

服务端 `resolve` 返回**锚点**（anchor = 交付回合 id）与稳定路由 + 参数；客户端
`/inbox/<id>` 只负责解析与跳转（不在跳转时标已读），伙伴页按锚点（turnId →
deliveryId → topicId 依次回退）**定位并展示那条来源消息**（滚动到该回合），定位成功
后才标已读；找不到锚点（目标缺失/加载失败）保持未读。草稿、话题与滚动位置沿用既有
会话 store，返回时不丢。

### 测试证据（整改后）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| W12 契约测试 | `cd server && PETSENSE_MQTT_DISABLED=1 PETSENSE_SCHEDULER_DISABLED=1 .venv/bin/python -m unittest discover -s tests -p "test_agent_w12.py"` | **32/32 通过**（新增：精确回合身份 open/escalate、日/周口径、前缀误配不成立、发布晚于水位由富化补齐且已读不复活、历史补入不推送、设备/回信推送候选、安静档不影响这两类、一般关怀与学习完成永不推送、个人高相关三条件、换绑/跨宠物不泄露正文与来源、他人宠物过滤被拒、多宠物隔离、分页 nextCursor、投影作业） |
| 迁移与模型 | `-p "test_migrations.py"` / `-p "test_models_schema.py"` | 4/4、3/3 通过（0020 单 head 接 0019；字段调整后仍一致） |
| 客户端 | `cd app && npx expo lint` / `npx tsc --noEmit` / `npx prettier --check` / `npm test` | 0 错误 0 警告；类型检查通过；格式通过；**115/115 通过** |
| 服务端全量 | `python -m unittest discover -s tests` | 本工作树 `.venv` 仍缺 `trafilatura` 的传递依赖（`justext`/`dateparser`），经 `agent/routes.py → registry → fetch → trafilatura` 的既有用例在导入期失败（与本次改动无关）；`server/.venv/bin/python -m pip` 在该 venv 不可用（无 pip 模块）。需在装全量依赖的环境复跑全量。 |

### 仍待确认 / 未做

- 关键设备告警的严重度口径（只算 critical 还是含 warning/info）：已隔离为单一常量，
  等负责人确认后再改，本轮未改产品含义。
- 委托条件提醒（`reminder` 交付）不属于 W12 四类来源，不入箱。
- W13 为 iOS 端；Android/Web 只保留收件箱（客户端未加推送相关代码）。
- 模拟器验收（A5/A6）与真机往返走查仍待另行委派；本记录只含自动化证据。

### 实施范围索引（首轮与整改后一致）

- 服务端新增：`server/petsense/agent/inbox.py`（索引核心）、`inbox_eligibility.py`
  （中心策略 + 窄类型 seam）、`inbox_routes.py`（独立 blueprint）、`inbox_jobs.py`
  （W08 队列上的周期投影作业）；模型与迁移：`db/models/agent.py` 的 `AgentInboxItem` /
  `AgentInboxState` + `alembic/versions/0020_agent_w12_inbox.py`（单 head 接 0019）；
  `server/petsense/app.py` 注册 blueprint 与作业。
- 客户端新增：`services/agent-inbox.ts`、`hooks/use-agent-inbox.ts`、
  `app/agent-inbox.tsx`（收件箱页）、`app/inbox/[id].tsx`（深链解析页）、
  `components/alerts/inbox-preview-card.tsx`；改动：`notification-bell.tsx`（角标用服务端
  未读数，能力未确认时回落本地）、`notification-center.tsx`（新增收件箱分区，**未接管的
  本地告警全部保留**）、`app/(tabs)/index.tsx`（今日预览）、`app/(tabs)/agent.tsx`
  （深链锚点定位到来源消息）。
- 与 W11 的合并点：W11 判定「个人高相关」后调用
  `inbox.mark_personal_eligibility(session, user_id, item_id, relevance=..., frozen_evidence=...,
  delivery_approved=...)`；W12 只存三项输入并按中心策略重算，不需要 W11 改本包。
  W13 读取 `inbox.push_candidates(session, user_id)`（稳定 ID、来源类型、候选条件、历史
  补入标识）。W11/W12 都不改动 W09 的 care.py / care_jobs.py。

### 终轮整改（D7 确认 + 深链阻塞缺口）

**D7（已确认）**：`inbox_eligibility.KEY_DEVICE_SEVERITIES = ('critical', 'warning')` ——
只有**仍开放的 warning/critical** 设备告警是关键预览档与推送候选；**info 级设备告警
永远只在收件箱**（不预览、不推送、但计入收件箱未读数）。测试显式断言三件事：
info 告警 `previewable=0` 且 `push_candidates` 为空；warning/critical 开放告警进入预览
与推送候选；关闭后退出关键优先级。

**深链阻塞缺口（Codex 独立检查发现）已修**：

| 缺口 | 修法 |
| --- | --- |
| 锚点只在 `visible`（分页 + 当前话题过滤）里找 | 查找范围改为**全部已加载回合**；不在范围（早于第一页、被话题过滤掉）时按稳定回合 id 调 `GET /agent/inbox/turn/<id>` **精确取回**（新增端点，鉴权 + 归属校验，跨账号 404） |
| 偏移从 ref 读、effect 早于 onLayout 且不重跑 | 偏移改为 `useState`，`onLayout` 写 state，定位 effect 依赖 state → 布局变化即重跑 |
| `anchorApplied` 不随新链接重置 | 锚点状态带 `key`（turnId/topicId/deliveryId/inboxId 组合），新链接即新 key，旧状态失效 |
| 静默主题无交付回合 → 卡在伙伴页 | 无交付回合时按收件箱条目取**权威主题**（`source.topic`），在会话顶部渲染「来自收件箱」卡片，展示后才标已读 |
| 历史条目早于第一页 / 被过滤掉 | 同上：精确取回并以固定卡片展示，不依赖会话分页 |
| 草稿/附件/话题/滚动被破坏 | 锚点区只读渲染，不触碰会话 store 的草稿、附件、话题与滚动位置；提供「返回收件箱」显式路径 |
| 未登录深链无入口、登录后不续 | 深链页给出「登录 / 注册」入口（login 页 `router.back()` 回到本页），登录后**恢复同一路由**继续解析 |
| 账号切换后旧响应生效 | 请求发起时记录账号 id，响应回来账号已变则丢弃，不跳转、不标已读 |
| 占位路由 / 误发新消息 | 目标解析不可用时留在深链页并给出明确状态（不跳占位路由）；锚点区不产生任何新回合 |

**新增定点测试**：`app/tools/inbox-anchor.test.cjs`（6 例，node:test）覆盖参数归一化、
turnId 命中、早于第一页需远程取回、被话题过滤掉的回信按 deliveryId 命中、安静主题按
topicId 取权威主题、锚点 key 变化重置。服务端新增 `test_agent_w12.py` 三例（D7 三条
口径）+ 锚点回合端点（本人可取、跨账号 404、不存在 404）。

**测试证据（终轮）**：

| 检查 | 结果 |
| --- | --- |
| `test_agent_w12.py` | **36/36 通过**（含 D7 三条显式断言与锚点端点） |
| `app/tools/inbox-anchor.test.cjs` | **6/6 通过** |
| 客户端 lint / typecheck / prettier / `npm test` | 0 错误 0 警告 / 通过 / 通过 / **121/121 通过** |
| 服务端全量 `python -m unittest discover -s tests` | **612/612 通过**（skipped=2；需带 `PETSENSE_MQTT_DISABLED=1`，**不要**带 `PETSENSE_SCHEDULER_DISABLED=1`——该开关会让 W08 的 `test_scheduler_disabled_switch` 断言失败，属环境用法而非缺陷） |

**P00 契约同步**：`docs/agent-p00-contract.md` §6.1 新增「W12 持久通知语义（收件箱）」，
写明来源分类与稳定 ID、幂等与恢复、D5 启用、D2/D7 预览口径、D3 已读、D4/D6 深链与
锚点、账号与宠物隔离，以及给 W13 的推送候选接口。

**未做**：模拟器验收与真机往返走查仍待另行委派（本记录只含自动化证据）；Android/Web
未接推送（W13 仅 iOS）。

### W11 → W12 已确认发布资格集成（2026-11-23，合并后）

**判据（全部严格相等，缺一不成立）**：`agent/inbox_publication.py` 解析 W11 的**不可变运行轨迹**：
`conclusion.publication.provenance == 'model_high_accepted'`（`'model_normal_accepted'`、
`'legacy_default_high'`、`'legacy_default_normal'` 一律不成立）+ 同一宠物的 `form='review'` 运行 +
`conclusion.deliveredBy == 'w09_review'` + 窗口与主题依据一致 + **已交付的 W09 回顾回合**
（request id 精确匹配 `care:review:<kind>:<pet>:<窗口>`，且其 care 块 `runId` 等于该运行 id）。

**最小元数据补写**：W09 回顾主题此前没有 run id，现由 `care_jobs.handle_review` 写入
`topic.run_id = run_id`（稳定 run id + 精确链接到已交付回合）。

**恢复语义**：资格由 W12 投影的富化阶段按不可变轨迹重新解析，**重启后自动恢复**；解析不出来时
**保持原值**（既不抬升也不降级）。安静档（无交付回合）、正常/默认高相关、证据缺失一律不是推送候选。

**端到端测试**（新增 `server/tests/test_agent_w11_w12.py`，11 例，按真实「回顾→收件箱」生命周期构造）：
正例 1 例（已确认 high → 条目 relevance=high + frozen_evidence + delivery_approved + 推送候选）；
反例 7 例（normal、默认 high、安静档无交付、缺运行、跨宠物、回合未指向运行、窗口不一致
→ 全部不抬升）；恢复 1 例（条目先建、运行与回合后到，再次投影即恢复资格）；既有中心策略 1 例
（安静档下 warning/critical 设备告警与到期回信仍是候选）。

### 本轮联合检查（W11+W12 合并后）

| 检查 | 结果 |
| --- | --- |
| 服务端全量 `python -m unittest discover -s tests`（带 `PETSENSE_MQTT_DISABLED=1`） | **657/657 通过**（skipped=3；含新增 11 例） |
| 客户端 typecheck / lint / prettier / `npm test` | 通过 / 0 错误 0 警告 / 通过 / **126/126 通过**（`tools/inbox-anchor.test.cjs` 6 例计入） |

**状态**：W12 代码与自动化检查完成，**模拟器验收（A5/A6，含浅色/深色视觉走查）待办**；
本轮未改任何视觉文件，未接 W13 依赖，未提交/未推送/未部署。
