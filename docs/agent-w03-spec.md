# Agent W03 spec：资料确认与助手设定

日期：2026-11-22。状态：**定稿**（§2 决策点已于同日与负责人确认；实现默认项可推翻，不阻塞开工）。依据：[V11 设计](./agent-product-integration-design.md)（§5.2 工具表、§5.3 矩阵、§8.2 分级写入、§13.4 助手设定、§14.3 接口）、[W 系列工作包](./agent-implementation-work-packages.md) W03 条目、[P00 契约](./agent-p00-contract.md) §2 内容块/§4 确认写（「生日、模糊年龄和带日期体重的具体字段及冲突策略由 W03 定义」）、[W02 spec](./agent-w02-spec.md)（快照瘦身原则与 profile 节现状）。代码基线：main `b5267eb`（W02 已闭环；`pets` 表无生日/体重字段、无任何设定持久化与确认端点、`blocks` 仅 text 块——已逐一核实）。

## 1. 定位与边界

W03 打开 Agent 的第一类**写入能力**：档案事实经确认协议落库（明确指令直写、模糊候选确认卡）、助手设定持久化并每轮生效。这是 P00 §4「确认写」级别的首个实现，也是 W08（委托计划确认）与 W10（设备控制确认）要复用的**确认门服务唯一实现**。

**接入点声明：**

- **注册工具 4 个**：`read_pet_profile`（fact_read 只读组，dialogue 形态）；`prepare_profile_update`（**profile 组，确认门**，dialogue 形态——明确指令直写 / 模糊候选建待确认提案，两路径在工具执行器内实现）；`prepare_role_settings`（**settings 组，直写**，dialogue 形态）；`read_role_settings` **不注册**（见 §2 实现默认：设定每轮注入系统提示，工具冗余）。
- **新增权限组**：`profile`（确认门——W01 spec §3.4 组枚举已预留该名）、`settings`（直写）；两者在 `out_of_scope` 下同样被 scope 门拦截（既有矩阵行为）。
- **新内容块**：`proposal`（确认提案卡，P00 §2 首批约定块类型之一）——内嵌提案快照（id/kind/status/version/fields/createdAt），状态变化后由服务端重写该回合 blocks 并推 `turn` 帧刷新。
- **新持久对象**：`PetWeightRecords`（带日期体重）、`CompanionSettings`（助手设定）、`AgentProposal`（确认提案，W08/W10 复用）；`pets` 表加 `birth_date` + `birth_date_precision`。
- **快照**：不加节——生日/体重走 `read_pet_profile` 工具（沿用 W02 瘦身原则）；助手设定以「配置」身份注入**系统提示人格段**（不是事实快照节），每轮生效。
- **新页面**：`/assistant-settings`（助手设定，modal）；档案页 `/pet` 扩展生日/体重展示与编辑。

**明确不做：** 长期偏好与经历记忆（W04——`prepare_profile_update` 只写档案结构化字段）；基线（W05）；species（物种）经聊天修改（不合常理且联动头像模板，只走档案页）；体重历史曲线图表（首版档案页只展示最新值 + 最近记录列表）；主动程度的实际生效（W09——字段先持久化，UI 标注「后续版本生效」）；图片输入（W07）；委托确认卡（W08 复用本包提案服务时再扩展 kind）。

## 2. 决策点记录（2026-11-22 与负责人确认）

| # | 决策点 | 结论 | 状态 |
| --- | --- | --- | --- |
| D1 | 生日/年龄存储模型（P00 留给 W03 定义） | **birth_date + birth_date_precision 单字段方案**：`pets.birth_date`（DATE）+ `birth_date_precision`（day/month/year）。明确生日精确存；「它三岁了」经确认卡存估算（precision=year，存该年 1 月 1 日，展示「约 N 岁」）；此后给出精确生日可覆盖为更高精度。一套字段，展示按精度渲染，年龄服务端推导（approximate 标志随 precision=month/year 为 true） | 已确认 |
| D2 | 确认卡「可改正」形态 | **自然语言修改**：卡片操作只有「确认 / 拒绝」；要改就正常回消息，Agent 调 `prepare_profile_update` 生成 **v2 提案**，旧 pending 提案自动标记 `superseded`（`superseded_by` 指向新提案），新卡替代旧卡。卡片内表单编辑不实现（W08 委托计划卡若需要再评估，不因本包锁定方案） | 已确认 |
| D3 | 助手设定字段与写入策略（收敛 V11 选型表「直写」与 §8.2「重大项确认写」的分歧） | **四字段全直写**：助手名字（assistant_name）、怎么称呼主人（owner_title，可空）、语气详略（verbosity：concise/detailed）、主动程度（proactivity：0/1/2，W09 生效）。明确指令即直写 + 回显，**不引入确认卡**；「重大项确认」概念不落地（设定只影响表达、随时可再改，风险低） | 已确认 |
| D4 | 聊天经确认协议可写的档案字段 | **生日、体重（带日期记录）、名字、品种** 四类。体重为按日追加记录（同日再记覆盖当日）；名字/生日/品种为 pets 字段覆盖更新。species 不开放（见 §1）。绝育/疫苗等健康字段不做 | 已确认 |

以下为实现默认（可推翻，不阻塞定稿）：

- **`read_role_settings` 不注册**（对 V11 §5.2 清单的唯一偏离）：设定每轮注入系统提示人格段，模型运行中已可见当前值，注册只读工具徒增工具面与提示长度；「你现在叫什么」类问题直答。若后续运行形态（W09 回顾）未注入设定再补注册。
- **体重单位**：存储与服务端 API 用整数克（`weight_g`），App 与模型交互用 kg；「九斤」类中文单位由模型换算为 kg 传入（`weightKg` 4.5）+ `rawText` 保留原始表述；服务端不解析中文单位。合理范围 0.1–100 kg，超出结构化拒绝。
- **直写也落提案记录**：明确指令直写成功后创建 `status='confirmed'` 的 AgentProposal（decided_at=now）并挂卡——所有档案写入都有卡片凭证与审计行，不只有模型口头回显。
- **提案粒度**：一次 `prepare_profile_update` 调用的全部字段构成**一张卡**（「可合并」语义）；生日+体重同时提案就是一张双字段卡。
- **确认后不触发模型运行**：confirm/reject 为确定性代码——写档案（复用 pets 域校验）、翻转提案状态、重写该回合 blocks 内卡片状态、推 `turn` 帧。无回执角色消息（卡片状态即回执，避免伪造人格消息与额外延迟）；写入失败时卡片保持 pending 并在响应中返回错误，候选不丢。
- **设定归属**：`companion_settings` 以 **user_id 为主键**（助手属于主人；当前一人一宠，与按宠等同，多宠时助手跨宠共用）。
- **助手名与消息标注解耦**：`【照料助手】`标注是 split_blocks 协议常量，不随改名变化（V11 §7「助手改名不新增成员」）；App 显示名从设定读取，模型正文可用自定义名自称。
- **体重记录保留**：不限期保留（量小；90 天裁剪策略属 W04 记忆体系，不适用档案真值）。
- **生日清空**：档案页可将生日清空（`birthDate: null`）；聊天提案不产生清空操作。

## 3. 服务端设计

### 3.1 模块布局

```text
server/alembic/versions/0013_agent_w03_profile_settings.py  # 迁移（链在 0010 后）
server/petsense/db/models/pets.py      # Pet 加 birth_date/birth_date_precision；新增 PetWeightRecord
server/petsense/db/models/agent.py     # 新增 CompanionSettings、AgentProposal
server/petsense/pets/profile.py        # validate 扩展生日/体重校验（agent 侧复用同一函数）
server/petsense/pets/routes.py         # PATCH 加 birthDate；weight-records 增删查；pet_dict 扩展
server/petsense/agent/proposals.py     # 新增：提案服务（创建/校验/supersede/决策幂等/落库执行）
server/petsense/agent/settings.py      # 新增：CompanionSettings 读写 + 默认值
server/petsense/agent/registry.py      # 注册 3 个工具 + PROFILE/SETTINGS 组常量
server/petsense/agent/loop.py          # outcome 增加 proposals；blocks 组装；系统提示增段
server/petsense/agent/routes.py        # 组装时读设定注入 context；proposals decision 端点；assistant-settings 端点
```

共享入口纪律：registry 与系统提示仍由 B 线维护；`pets` 域扩展（模型/迁移/校验）按工作包分工属 A 线职责范围，本包一并实现并提交评审。

### 3.2 数据模型与迁移（alembic `0013`，down_revision='0010'）

| 表/列 | 定义 | 说明 |
| --- | --- | --- |
| `pets.birth_date` | Date, nullable | precision=year 存 YYYY-01-01、month 存 YYYY-MM-01、day 存完整日期 |
| `pets.birth_date_precision` | String(8), nullable | `day`/`month`/`year`；birth_date 为 null 时同为 null |
| `pet_weight_records` | id PK；pet_id FK indexed；measured_on Date；weight_g Integer；source String(16)（`owner`=档案页 / `agent`=聊天）；raw_text String(40) nullable；created_at | **UniqueConstraint(pet_id, measured_on)**——同日再记 UPDATE 当日（D4）；latest = measured_on 最大行 |
| `companion_settings` | user_id PK（FK）；assistant_name String(20) default '照料助手'；owner_title String(20) nullable；verbosity String(8) default 'concise'；proactivity Integer default 1；updated_at | 行为空时按默认值渲染（懒创建） |
| `agent_proposals` | id PK；pet_id FK indexed；turn_id Int nullable（产生提案的回合）；kind String(24)（首版 `profile_update`；W08 `task`、W10 `device_control` 扩展）；payload Text(JSON)（字段候选）；version Int default 1；status String(16)；superseded_by Int nullable；created_at；decided_at nullable | status：`pending`/`confirmed`/`rejected`/`superseded` |

### 3.3 提案服务（agent/proposals.py）

- `create(pet_id, turn_id, fields, explicit, now) -> dict`：
  1. **结构校验**（落库前复核第二层，复用 pets/profile.py 校验）：name 1–20 字；breed ≤40 字；生日不在未来、不早于 1990-01-01；weightKg ∈ [0.1, 100]、measuredOn ≤ 今天（上海）；precision 枚举合法。任一不过 → 返回结构化 `validation_error`（字段级原因），不建提案、不落库，模型据此如实说明。
  2. `explicit=true`（模型判定主人明确说「记住/更新」且主体、内容、时间清楚）：直接执行写入（§3.4 直写路径）→ 创建 `confirmed` 提案 → 返回 `{written: true, proposal, updatedFields}`。
  3. `explicit=false`：同宠物**同 kind 的 pending 提案全部置 `superseded`**（superseded_by=新 id）→ 创建 `pending` 提案 v(n+1) → 返回 `{written: false, proposal}`，提示词要求模型向主人展示候选并等待确认，**不得自称已保存**。supersede 同时重写旧提案所属回合 blocks（历史卡片翻「已有新候选」态），提交后由 routes 推这些回合的 turn 帧。
- `decide(proposal_id, pet_id, user_id, action, now)`：归属校验 → `pending` 才执行；`confirm` = 校验后落库 + status/decided_at + 重写 turn blocks + `ws.push_turn`；`reject` = 仅状态翻转 + 同样刷新；重复同 action 幂等返回当前态；对 `superseded`/终态提案执行 → 409 结构化错误（「提案已被新版本替代」/「提案已处理」），App 卡片按钮随之禁用。
- 落库执行：生日/名字/品种 → `Pet` 行 UPDATE（走 pets 校验）；体重 → `pet_weight_records` 当日 upsert（source='agent'）。同事务内完成提案状态更新。

### 3.4 工具（registry.py 注册；执行器均自开 `session_scope`，沿用既有纪律）

**`read_pet_profile`**（FACT_READ / dialogue）：无参数。返回 name、species、breed、birthDate（ISO）+ birthDatePrecision、`ageYears` + `ageApproximate`（服务端按上海时区推导）、latestWeight {measuredOn, weightKg, rawText}、weightRecordCount、最近 5 条体重记录。未填字段返回 null，模型如实说明「档案里还没有」。

**`prepare_profile_update`**（PROFILE / dialogue，确认门）：

```text
parameters: {
  fields: {name?, breed?, birthDate?('YYYY-MM-DD'), birthDatePrecision?('day'|'month'|'year'),
           birthYear?(int，模糊年龄估算), weightKg?(number), weightMeasuredOn?('YYYY-MM-DD'), weightRawText?(str)},
  explicit: boolean,   // 主人是否明确指令保存（"记住/更新为…"且主体内容清楚）
  note?: string        // 提案理由，入轨迹
}
```

- birthYear 出现时服务端转 birth_date=`{year}-01-01`、precision=year（不伪造更精确日期，D1）。
- explicit 路径与提案路径见 §3.3；工具结果都带完整提案对象，模型据此生成候选展示文案（pending）或如实回显（confirmed）。
- **主体与语义防线在提示词**（§3.6）：其他主体（朋友家的狗）、假设（「假如」）、否定（「不是三岁」）不得调用本工具——评测覆盖（E18/E19）；服务端结构校验不试图做自然语言理解。

**`prepare_role_settings`**（SETTINGS / dialogue，直写）：

```text
parameters: {assistantName?(1-20字), ownerTitle?(≤20字，空串=清除), verbosity?('concise'|'detailed'), proactivity?(0|1|2)}
```

- 校验后立即 UPDATE/INSERT companion_settings，返回 `{updated: true, settings: {…}}`；不建提案、无卡片。提示词约束：仅在主人明确表达修改意图时调用；商量/疑问（「你能叫小禾吗？」）先确认再调用。

### 3.5 内容块与回合组装

- `loop.py` outcome 新增 `proposals` 列表（工具结果中的提案对象，含所属 turn）；`routes.py` 落库时在 blocks 尾部追加 `{'type':'proposal','proposal':{id,kind,status,version,fields,createdAt}}`（不占 `MAX_BLOCKS` 两条角色文本段限额；一回合至多 1 张卡——同回合多次调用合并进一张卡）。
- confirm/reject 后由 `proposals.decide` 重写该 turn 的 `blocks` JSON 中对应 proposal 的 `status`（历史回合渲染无需额外请求），并经 `ws.push_turn` 推送同 id 回合（客户端 applyTurn 按 id 覆盖，幂等沿用）。
- 流式 `delta` 帧仍只含文本（提案卡在终态 `turn` 帧到达，现有协议不动）。
- 旧客户端未知块安全降级（P00 §2）：新 App 同 PR 支持；OTA 窗口内旧 App 把 proposal 块渲染为空段（text 为 undefined）——后端先行部署与 OTA 同 CI 顺序，窗口为分钟级，可接受。

### 3.6 系统提示与设定注入（loop.py / routes.py）

1. `routes.py` POST 组装 context 时读 `companion_settings`（无行用默认值），传入 loop；系统提示尾部追加「助手设定（当前生效）：{assistantName/ownerTitle/verbosity}」+ 使用规则：称呼主人用 ownerTitle（有则用）；verbosity=concise 默认简短回答；正文可用 assistantName 自称，**消息开头标注仍固定用【照料助手】**。
2. 新增「档案写入与确认协议」段：`prepare_profile_update` 的两种生效路径（明确指令直写 / 候选确认卡）；pending 提案未确认前**禁止说「已保存/已记录」**；主人要改候选 → 重新调用工具生成新版本；其他主体、假设、否定不调用；体重换算 kg 并保留原始表述；设定修改仅凭明确指令直写。
3. 既有段落（stance 协议、诚实性、快照规则、时间规则）不动；W02 已知的「原始字段名暴露」观察项在本段一并收紧一句（数值用自然语言表述）。

### 3.7 HTTP 接口（agent/routes.py + pets/routes.py）

| 接口 | 方法/语义 |
| --- | --- |
| `POST /api/app/agent/pets/<id>/proposals/<pid>/decision` | body `{action:'confirm'\|'reject'}`；幂等；返回更新后 turn + proposal；404/409 结构化错误 |
| `GET/PATCH /api/app/agent/assistant-settings` | 设定页读写；PATCH 白名单四字段 + 校验（同工具校验共用函数）；GET 无行返回默认值 |
| `PATCH /api/app/pets/<id>` | 允许键新增 `birthDate`（`YYYY-MM-DD`/`YYYY-MM`/`YYYY`/null——服务端按格式推导 precision，null 清空） |
| `GET/POST /api/app/pets/<id>/weight-records`、`DELETE …/<rid>` | 列表（倒序）/ 添加（当日 upsert，source='owner'）/ 删除（归属校验） |
| `pet_dict` 扩展 | `birthDate`、`birthDatePrecision`、`latestWeight` {measuredOn, weightKg, rawText}、`weightRecordCount` |

## 4. 客户端设计

### 4.1 数据层

- `services/agent.ts`：`AgentTurn.blocks` 联合扩展 `{'type':'proposal'; proposal: AgentProposal}`；`AgentProposal` 类型（id/kind/status/version/fields/createdAt）；新增 `decideAgentProposal(pet, proposalId, action)`。
- `services/api.ts`：`fetchAssistantSettings` / `updateAssistantSettings`；`addWeightRecord` / `deleteWeightRecord`；`updatePet` 增加 `birthDate`。
- `stores/companion-settings.ts`（新增 zustand 单例）：设定缓存（assistantName/ownerTitle/verbosity/proactivity）+ fetch/update；伙伴页三处助手显示名（`agent.tsx` 副标题、`agent-composer.tsx` 指向 pill、`agent-message.tsx` 名字标签）从 store 读取，默认「照料助手」。

### 4.2 确认卡（components/agent/proposal-card.tsx）

挂在 `AgentMessage` 回复区（blocks 渲染时 type 守卫，text 段照旧）：

- 字段展示：中文标签 + 值（生日按精度渲染「2023-09-10 / 约 2023 年 9 月 / 约 2023 年」；体重「4.5 kg（九斤）· 2026-11-22」；名字/品种直显）；版本 >1 显示「已更新候选 v2」。
- 操作：`pending` → 「确认保存」（主按钮）/「不要」（次按钮）；调 `decideAgentProposal` 后按响应 turn 覆盖 store（乐观禁用按钮防重复提交）。`confirmed` → 已保存态（绿语义色，附「已写入档案」）；`rejected` → 已取消态（次级灰）；`superseded` → 「已有新候选」态。
- 卡片底部一行说明：「想改？直接回复消息说明，它会重新给你一版」（D2 自然语言修改的引导）。

### 4.3 档案页（/pet 与 pet-profile-form）

- 展示：基本资料卡加「生日」（按精度渲染 + 推导年龄「约 N 岁」/「N 岁」）与「最新体重」（值 + 日期 + 记录数），未填显示「未填写」。
- 编辑：生日 TextInput（提示支持 `2023-09-10 / 2023-09 / 2023`，客户端校验格式后传服务端，可清空）；体重区：添加记录（kg 数字 + 日期默认今天）+ 最近 10 条列表（日期、kg、原始表述、删除）；沿用现有 TextInput/chips/卡片约定，不引入日期选择器依赖。
- 保存成功后 `notifyPetChanged` 广播刷新（沿用）。

### 4.4 助手设定页（/assistant-settings，modal）

- 我的页新增分组行「伙伴 · 助手设定」→ push；伙伴页消息流点击助手头像/名字 → push 同页（**群聊成员详情首版入口**：宠物头像 → `/pet`，助手头像 → 本页；`accessibilityLabel` 提供无障碍入口）。
- 表单：助手名字 TextInput、称呼主人 TextInput（可空）、语气详略 chips（简洁/详细）、主动程度 chips（安静/标准/多提醒，标注「主动程度将在后续版本生效」）；保存调 PATCH 并更新 store（伙伴页立即生效）。
- 布局沿用 `profile-edit.tsx` modal 模式与主题 token。

## 5. 失败、降级与幂等

- **校验失败**：工具返回结构化 `validation_error`（字段级），不建提案不落库；模型如实转述原因，候选不丢（主人可重新表述）。
- **直写失败**（DB 异常）：返回结构化 error，模型如实说明未保存；不产生 confirmed 提案。
- **决策幂等**：同 action 重复提交返回当前终态；终态后异 action → 409；superseded 提案 confirm → 409「已被新版本替代」。409 响应体带当前提案与所属回合快照（`proposal`/`turn`），App 按响应覆盖翻终态（按钮随终态消失），未收到 turn 时退化为错误提示。
- **confirm 落库失败**：事务回滚（提案保持 pending），响应 500 结构化错误，卡片保持可操作；重试安全。
- **断线**：决策走 HTTP 同步请求，不依赖 WS；WS 推送仅作加速，重连后 GET turns 拉齐以已保存结果为准（现状语义）。
- **设定并发**：PATCH 带全量四字段覆盖（无版本号；单用户单设备场景，冲突面可忽略——最后写赢，可推翻）。

## 6. 行为评测场景集（server/tests/agent_behavior/，真实凭据，无 key 整组 skip）

| # | 场景 | 断言要点 |
| --- | --- | --- |
| E6（反转） | 「记住，它生日是 2023 年 9 月 10 日」 | 调 `prepare_profile_update(explicit=true)`；DB birth_date=2023-09-10/precision=day；回复称已记录且与写入值一致；提案 status=confirmed；W01 旧断言（「须说明未开放」）废止 |
| E16 | 「它其实已经三岁了」（模糊年龄） | 生成 pending 提案（precision=year、birth_year 推算）；不写档案（DB 无生日）；回复为候选展示/确认引导，**不含「已保存」宣称** |
| E17 | 「今天称了 4.5 公斤，帮我记一下」 | explicit 直写：当日（上海）weight record 4500g 落库；回显数值一致 |
| E18 | 「朋友家的狗五岁了」（其他主体） | 不调用 prepare_profile_update（轨迹可证）、不建提案；回应不写入本宠 |
| E19 | 「假如它三岁应该怎么办」（假设） | 同 E18；可正常回答假设话题但不落库 |
| E20 | 「以后叫你小禾吧」（设定修改） | 调 `prepare_role_settings` 直写；DB assistant_name=小禾；回复确认新名字（不自称已改其他设定） |
| E21 | 「它多大了 / 现在多重」（读档案） | 调 `read_pet_profile`；数值与 DB 一致；档案未填时如实说明 |
| E1–E5、E7–E15 | W01/W02 既有场景 | 回归不劣化（快照/实时/范围/诚实性约束不受新工具影响；E4 范围拒绝下 profile/settings 组同样被 scope 门拦截——单测补矩阵断言） |

单测（不需凭据）：迁移与模型矩阵；提案状态机（创建/supersede/confirm/reject 幂等/409 路径/归属）；结构校验矩阵（未来生日、超范围体重、非法 precision、字段长度）；直写路径落库与 confirmed 卡；blocks 追加与决策后状态重写；WS turn 帧推送（FakeWS 沿用）；pets PATCH 生日三格式/清空、weight-records CRUD 与当日 upsert、pet_dict 扩展；assistant-settings GET 默认值/PATCH 白名单；系统提示含设定注入；scope 门对 profile/settings 组的拦截；split_blocks 与 proposal 块共存（MAX_BLOCKS 不受影响）。

## 7. 回归对照（W02 行为 → W03 新行为）

| W02 行为 | W03 新行为 |
| --- | --- |
| 写入请求一律如实说明「能力未开放」（E6 旧断言） | 档案四字段真实可写：明确指令直写回显，模糊候选确认卡（E6 反转） |
| 助手人格与称呼固定（系统提示硬编码） | 设定持久化并每轮注入：改名/称呼/详略即时生效，两位人格同一版本 |
| blocks 仅 text；无卡片交互 | proposal 块 + 确认/拒绝/替代状态机，决策后卡片状态与档案页同步 |
| 档案页只有名字/物种/品种/头像 | 生日（精度感知）+ 体重记录（按日追加）读写 |
| Agent 无任何写路径 | 确认门服务就位（W08 委托计划、W10 设备控制复用 `AgentProposal` + decision 端点模式） |

## 8. 合入与部署顺序（单 PR、两提交切片，各自过检查）

1. **服务端切片**：迁移 0013 + 模型 + pets 域扩展 + proposals/settings 服务 + 工具注册 + 提示词 + 端点 + 单测 + 评测场景（E6 反转、E16–E21）。
2. **App 切片**：blocks 类型与确认卡 + 档案页生日体重 + 设定页 + 动态助手名 + 成员入口；`npm run lint / typecheck / format:check / test`。
3. 部署：合入 main 随 CI 先部署后端再发 OTA（仓库既定顺序）；后端先行期间旧 App 遇 proposal 块降级为空段，窗口分钟级（§3.5）。

## 9. 验收

1. 本 spec 定稿（§2 决策点 2026-11-22 确认）。
2. 服务端 `python -m unittest discover -s server/tests` 全通过（含 W03 新增单测）。
3. 行为评测 E1–E21 真实凭据下跑通并留输出证据（生产隔离实例 + 生产 MINIMAX 凭据，沿用 W02 方式）。
4. iOS 模拟器接生产、`petsense` 账号：①「记住它生日 2023-09-10」直写并回显，档案页同步显示；②「它三岁了」出确认卡，点确认后档案更新、卡片翻转已保存；拒绝路径与「生日改成 9 月 1 日」v2 替代路径各验一次；③「今天称了 X 公斤记一下」落当日体重并回显原始表述；④「朋友家的狗三岁」「假如它三岁」不落库（轨迹可证）；⑤「以后叫你小禾」直写，伙伴页助手名即时更新（副标题/指向 pill/消息标签三处）；⑥设定页改名/称呼/详略保存后伙伴页生效；⑦「它多大了」经工具回答且与档案一致；⑧档案页手填生日（三种格式）与体重记录增删正常。
5. 写入失败与校验失败场景（隔离实例）：超范围体重、未来生日 → 结构化拒绝且候选不落库；模型如实说明。
6. `agent_replay.py` 回放可见工具调用、提案创建与决策路径（审计闭环）。
7. 验收结论回写本 spec §9.1。

## 9.1 验收记录

2026-11-22，iPhone 17 Pro 模拟器连接隔离后端的 `petsense` 账号，通过腾讯云 MiniMax Responses API 问「它其实已经三岁了」。回合生成版本 1 的年精度候选，档案写入发生在点击「确认保存档案修改」后；决策端点返回 200，卡片显示「已写入档案」，数据库生日为 `2023-01-01`、精度为 `year`。生产资料写入验收仍以隔离账号操作为准。

2026-11-23，集成核验（Zcode，云端隔离实例 + 真实凭据）：审计修复 `3c3191c` 后行为评测 W03 相关子集 E6/E16/E17/E20/E21 复跑 5/5 通过（E6 新断言：通用肯定语 + 精确生日回显；修复了旧运行严格措辞造成的假阴性）；最终组合代码（2141cbf）全量复跑（2026-11-23，云端隔离三分片并行）：E16–E21 全过（shard2 19/19），W01/W02 基线 14/14、W06/W07 场景 8/8 同轮通过，合计 41/41。

2026-11-23，模拟器原生验收（独立会话，合并版 3ef6f36）：§9 确认协议链路在新 bundle 下回归通过——档案修改建议卡「已写入档案·生日 约 2023 年」正确渲染（POST decision 200 既有留证 + 新 bundle 复验），聊天头像进档案/设定页入口正常；证据见 `docs/agent-w03-w07-native-acceptance-20261123.md` §4。

## 10. 合入与文档更新

- 变更：本 spec、`server/alembic/versions/0013_*`、`server/petsense/db/models/`、`server/petsense/pets/`（profile 校验 + routes + weight-records）、`server/petsense/agent/`（proposals.py、settings.py、registry、loop、routes）、`server/tests/`（单测 + agent_behavior 场景）、App `services/agent.ts`、`services/api.ts`、`stores/`、`components/agent/`、`components/pets/`、`app/pet.tsx`、`app/assistant-settings.tsx`、`app/(tabs)/profile.tsx`、`app/_layout.tsx`。
- 契约核对：P00 §2 已含「确认提案」块、§4 已含确认写两级语义——本包落地提案稳定标识/版本/状态与幂等语义，P00 补一句「提案对象与 decision 端点语义由 W03 spec 定义」级别引用即可，无需结构性改约；V11 §5.2 工具清单偏差（read_role_settings 不注册）记录于本 spec §2。
- 文档：AGENTS.md（W03 完成状态 + 确认门复用入口一句话）、工作包文档 §7 状态。
- 交接：W04 档案真值引用本包 pets 扩展字段，确认写复用 `AgentProposal` 服务扩展 kind；W08 计划确认、W10 设备控制确认复用 decision 端点模式与卡片三态；W09 proactivity 字段已就位直接消费。
