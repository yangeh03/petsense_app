# W10 动作工具

2026-11-23。设计已确认。依赖 W01 注册表与 W03 确认协议。录音、日记和草稿各沿用现有业务入口，由服务端验证主体和执行条件。

## 1. 决策记录

| 编号 | 负责人确认 |
| --- | --- |
| D1 | 日记首次生成或重生成都先展示档案照和 PetGIF 托管提示，并经确认卡。读取已存在日记可直接返回。 |
| D2 | Agent 草稿按账号和宠物在服务端私有保存。结果卡跳转现有编辑器的独立草稿槽，保留原有手写草稿。正式发布由用户手动操作。 |
| D3 | 没有实机条件时可部署代码，录音能力保持关闭，真项圈验收通过后才开启。 |
| D4 | 固件及服务端协议适配纳入 W10。支持 1–10 秒，音频必须绑定设备及请求，旧固件不开放 Agent 录音。 |
| D5 | 复用已有 WebView 和 Web 播放能力，保持 OTA 兼容。只在用户播放时鉴权读取 WAV。 |
| D6 | 明确要求写或保存社区草稿时可直接保存。模糊意图先确认，正式发布始终由用户操作。 |

## 2. 共用动作状态与权限

- `control_device` 只开放 `record_audio`，`create_diary` 仅处理现有当日 AI 运动日记，`draft_social_post` 仅写私有草稿。对话可发起工具；后台检查点、回顾和事件不得调用。所有执行以已验用户、宠物、已批准请求、业务参数和幂等键为输入，复用 W03 的提案决定入口、待确认版本和重复决定处理。
- 会话显示待确认、执行中、完成或失败。录音的指令发送和音频到达分开显示；日记只在任务真实完成后呈现图片结果；草稿只在私有保存成功后回显。结果块保存稳定引用，WS 补发或 HTTP 拉取以持久结果为真。App 重开或重复点确认不重复向外部系统提交。
- 轨迹记录稳定动作 ID、执行来源、结构化结果和错误。避开凭据、完整音频和无关资料。旧客户端以可读文本降级。可恢复状态有唯一业务键；外部提交结果不明确时显示未知并等待原结果，经新确认才能再次提交录音。

## 3. 远程录音

- 每次请求由服务器生成唯一录音凭据，卡片展示目标项圈和整数时长 1–10 秒。确认时重新检查宠物和设备绑定、在线状态、协议能力和全局可用开关。复用遥测命令的归属、防抖及单设备录音互斥。拒绝、过期、旧提案或未确认时无 MQTT 录音副作用。
- 现有固件的 `PSWAV01` 共享 topic 和自增文件号仅支持旧链路，无法关联已授权请求。新增明确版本的 Agent 录音命令与回传格式。两个可用固件目标均须处理真实时长、足够缓存和有界录制；使用设备作用域的 MQTT 回传 topic 及请求凭据。请求和回传都携带同一关联值，服务端在读文件、广播与落库前验证期望设备、所属宠物、请求有效性和去重，拒绝错设备、过期、重放或未完整音频。沿用既有非 Agent 录音入口和传统协议。
- 固件须明确上报协议能力，支持自动限时停止和已完成请求去重。重复 MQTT 投递不启动第二次录音。通过有效样本数与 WAV 时长核对捕获时长，缓存容量及长包回传的处理要覆盖 10 秒，旧固件能力或失败原因如实显示。未确定 MQTT 连接身份时，关联凭据须有高熵且不对其他设备暴露。不能用共享 topic 中最接近的时间戳猜测归属。
- 只有收到并持久化归属正确的完整音频才更新完成。单次动作有超时终态，后到包按相应状态处理，不会串到下一请求。音频资源走专属鉴权读取，验证账号、宠物、设备绑定和动作引用。禁止通过旧公共音频 URL 暴露 Agent 内容。客户端仅按需读取并播放，同时用已有 WebView/Web 能力，保持现有 runtime 原生模块约束。
- 按 D3 发布。支持独立开关，生产默认关闭直到真项圈门槛通过。其他两个动作可独立验收和启用。

## 4. 运动日记与私有草稿

- 提取「今日」现有上海日界、档案照、走动至少一分钟、PetGIF AI 能力和现有任务复用逻辑为共用服务。两处入口继续使用同一按宠物、日期、样式和替换任务标识的唯一规则。确认时核对本地日期、档案照和目标任务，旧候选不能隐式改成新的一天或覆盖更新后的任务。PetGIF 结果保持现有鉴权详情、手动重生成及保留期。
- 日记透明提示档案照发送至 PetGIF 与其托管链接可访问性。拒绝、缺照片、覆盖不足、上游失败和状态未知均有准确结果，用户可打开现有日记详情。模型不得根据聊天文字伪造步数或数据来源。
- 社区草稿用账号、宠物、稳定动作 ID 绑定私有记录。经现有发布页独立槽加载，原有文字、图片和普通草稿状态不被覆盖。明确邀请保存的判断受注册表和服务器侧确认上下文约束，模糊时提交候选。保存草稿只写草稿对象，没有发布工具或发布副作用。用户进入现有发布页可修改并使用现有发布流程。

## 5. 验收标准

| 场景 | 验收判据与证据 |
| --- | --- |
| W10-A1 确认与权限 | 录音和日记测试拒绝、旧卡、重复确认、越权主体和后台违规。所有执行均能按动作 ID 追到确认及业务回执。 |
| W10-A2 音频协议 | 固件两目标编译。自动化覆盖 1、5、10 秒和时长越界，错设备、错请求、旧固件、离线、重复投递、超时、归属失效和未完整数据。模拟走真实编码格式、接收路径和鉴权音频读取。 |
| W10-A3 录音模拟器与真机 | 隔离账户走完整确认、等待、播放、前后台回读及失败展示。真实项圈验收要有更新的固件版本、有效命令与回传 ID、实际时长及播放结果。有条件时真机实测；无条件时记录生产录音关停证据。 |
| W10-A4 PetGIF | 档案照和有效真实观测的隔离环境中，确认后发起任务一次，任务完成后打开详情。重生成产生一次新任务，App 重开不重复。覆盖未确认、数据缺口和上游失败；使用测试素材且按既有外部服务授权验证。 |
| W10-A5 草稿 | 明确指令直存和模糊意图确认分别验证。重开恢复私有稿，原手写草稿保持，异账号拿不到稿件，社区动态计数在手动发布前不变化。 |
| W10-A6 发布兼容 | App 完整检查、固件 CI、旧录音／今日海报／社区原流程和旧 bundle 兼容。核对生产录音开关、未升级固件降级及日记/草稿上线状态。 |

本包行为评测号段 E58–E65。验收记录须写明 MQTT 或协议数据来源、实机与模拟范围、PetGIF 返回状态、稳定引用和最终负责人选定的录音发布状态。

## 6. 实施与本轮验证记录（2026-11-23，分支 codex/agent-w10-implementation）

### 6.1 已实现范围

- 服务端 `server/petsense/agent/actions.py`：三个动作共用的 `AgentAction` 回执（稳定动作 ID + 结构化结果）、录音 psrec01 协议接入、日记共用提交入口、私有草稿槽。
- 注册表 `registry.py`：`control_device`（DEVICE_CONTROL 组，D3 开关关闭时**不**进 schemas）、`create_diary`、`draft_social_post`（ACTION 组）。三个工具 `forms=(DIALOGUE,)`，后台检查点／回顾／事件形态不可调用。
- 提案确认门复用 W03：`proposals.decide` 改为返回 `(row, turn_id, changed)`，W10 动作类只在确认时翻状态，实际执行由 `routes.proposal_decision` 在事务提交后交给 `actions.execute_confirmed`。
- 固件 `agent_audio_protocol.{h,cpp}`（两目标同源，host 可测）+ 两份 `.ino` 接入：命令解析、凭据校验、去重表、限时停止、设备作用域回传、容量不足如实拒绝；遥测 envelope 上报 `agentAudio` 能力。
- App：动作回执卡 `action-card.tsx`（录音按需播放、日记详情入口、草稿发布页入口）、录音播放 `action-audio.native.tsx`（WebView 内建 audio，无新增原生模块）、`proposal-card.tsx` 的 W10 候选卡形态、`create.tsx` 的独立私有草稿槽（不触碰本地手写草稿）。
- 迁移 `server/alembic/versions/0018_agent_w10_actions.py`：revision `0018`、`down_revision=0017`（接 W09），全链单 head（0014 → 0015 → 0016 → 0017 → 0018）。

### 6.2 复审意见的处置

| 复审条目 | 处置 |
| --- | --- |
| 嵌套 `session_scope` | `db.session_scope` 增加同线程嵌套保护（嵌套即 `RuntimeError`），并把「读状态」与「写回执/推帧」拆成两段事务：`_transition` 用条件 UPDATE 做原子迁移，`_publish_turn` 在事务外调用。全量服务端测试在保护开启下通过（431 项）。 |
| unknown 与到期口径不一致 | `unknown` 语义统一为「等待原结果」：仍接受归属正确的后续有效回传并可完成；同时为 unknown 排到期定时器，到期收敛为失败。任何路径都不自动重发指令，重发需新的确认。 |
| WAV 核对不足 | 收尾处强制 mono 16 bit 16 kHz、声明字节数与实际字节数一致、数据块长度与实际字节一致且为整帧、时长对齐请求时长；终态提交前复核宠物与设备归属；超时与音频交叉时以原子迁移为准，迁移失败即删除本次落盘文件，不留孤儿音频。 |
| 草稿幂等与副作用 | 直写与确认两条路径都按「授权意图键」（`proposal:<id>` 或账号+宠物+回合+正文+地点的哈希）唯一，重复调用只对应一条动作行；确认后候选卡被动作卡取代（同一位置只留一个可交互卡）；发布表无任何写入；社区发布计数在手动发布前不变。 |
| 录音读取鉴权 | 独立目录 `agent-recordings/` + 专属鉴权路由，读取前校验账号、宠物、设备绑定与动作引用；不经旧公共 `/audio/` 路由暴露。 |
| 固件与生产开关 | 两目标 ESP32 完整编译通过；`PETSENSE_AGENT_RECORDING_ENABLED` 默认关闭（生产未开启），工具不进 schemas、系统提示如实说明未开放。 |

### 6.3 本轮测试证据

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 服务端全量 | `cd server/tests && PETSENSE_MQTT_DISABLED=1 python -m unittest discover -s . -p "test_*.py"` | 431 项通过（1 项跳过） |
| W10 服务端 | `python -m unittest test_agent_w10` | 35/35 通过（原 24 项 + 新增 11 项：unknown 接受迟到音频、unknown 到期不自动重发、终态不被迟到帧覆盖、非声明格式拒绝、字节数不符拒绝、数据块不完整拒绝、请求时长不符拒绝、草稿按意图幂等、不同意图各自成行、确认后候选卡被动作卡取代、私有槽取最新且旧行保留） |
| 固件 host | `python -m unittest discover -s firmware/tests -v` | 25 项通过（含 W10 协议 2 项：两目标协议逻辑、两目标副本一致） |
| 固件编译 | `bash firmware/check-build.sh`（arduino-cli 1.6.0 + esp32:esp32@2.0.16） | 两目标 ESP32 sketch 编译成功，`BUILD_EXIT=0`（`/tmp/w10-firmware-build-rerun.log`） |
| App | `npm run lint` / `npx tsc --noEmit` / `npm run format:check` / `npm test` | 全部通过（无 error，格式一致，100/100 测试通过） |
| 行为评测 E58–E65 | `cd server/tests/agent_behavior && python -m unittest test_agent_behavior` | 场景已补入；本轮环境无 `MINIMAX_API_KEY`，真实模型回合未执行（无凭据自动 skip） |

MQTT／协议数据来源：录音链路未连生产 broker，回传走 `mqtt_ingest.on_message` 的真实 topic 路由与真实帧格式（`petsense/sensor/<id>/agent_audio`，start/chunk/end 三类帧、base64 分片、声明 16 kHz/16 bit/单声道），仅 `publish_control_command` 被替换为桩。

### 6.4 本轮未覆盖（需后续模拟器／真机）

- W10-A3 录音模拟器与真机：隔离账户走完整「确认 → 等待 → 播放 → 前后台回读 → 失败展示」；真实项圈验收需更新固件、有效命令与回传 ID、实际时长与播放结果。生产录音开关保持关闭，等真项圈门槛通过再评估开启。
- W10-A4 PetGIF：隔离环境用测试素材走「确认 → 发起一次任务 → 完成后打开详情」，重生成只产生一次新任务，App 重开不重复。
- W10-A5 草稿：模拟器验证发布页私有槽加载／填入／移除，原手写草稿保持，异账号拿不到稿件，社区动态计数在手动发布前不变。
- W10-A6 发布兼容：App 完整检查、固件 CI、旧录音／今日海报／社区原流程与旧 bundle 兼容由集成阶段统一核对。
- 行为评测 E58–E65 需在有模型凭据时复跑。

## 7. 集成记录（2026-11-23，W10 worktree 合并 W08/W09 快照 `0bb8ca8`）

### 7.1 合并范围与冲突处置

合并对象为 W08/W09 已提交快照 `0bb8ca8`（`codex/agent-w08-w10` 上的既有提交，**不含**该分支后续的确认阈值细节改动）。合并在本 worktree 分支 `codex/agent-w10-implementation` 上完成，基线 `94f0852`。

| 冲突文件 | 处置 |
| --- | --- |
| `agent/registry.py` | 两组工具并存：`DEVICE_CONTROL`/`ACTION`（W10）与 `TASK`（W08）四个组常量都保留；`control_device`/`create_diary`/`draft_social_post`/`read_task`/`propose_task_change` 全部注册，权限矩阵先于执行校验不变 |
| `agent/loop.py` | 单一运行循环：对话仍走 `run_dialogue`（stance 协议），后台检查点／回顾／事件三种形态共用 W08 引入的 `run_structured`（`run_background` 为其包装）。系统提示合并为「诚实性段 + 记忆规则 + 委托规则（W08）+ 动作规则（W10）+ 录音能力句（按 D3 开关）」，无第二套模型循环 |
| `agent/proposals.py` | `decide` 按 kind 分派：`profile_update` 写档案、`task_change` 走 `tasks.apply_proposal`、W10 动作类只翻状态并由 `routes` 在提交后交 `actions.execute_confirmed`。返回统一为 `(row, turn_id, changed)` |
| `agent/routes.py` | 导入并用 `AgentAction` + `AgentCareTopic`；决策端点在事务提交后执行 W10 动作，任务与 care 端点保持 W08/W09 形态 |
| `db/models/agent.py` / `__init__.py` | `AgentAction`（W10）与 `AgentTask`/`AgentTaskPlan`/`AgentTaskDelivery`/`AgentJob`/`AgentCareTopic`/`CareDeviceState`/`CarePetState`（W08/W09）并存，`__all__` 补齐 |
| `db/models/__init__.py` | 同上，导出全量 agent 模型 |
| `tests/test_agent.py` / `test_agent_w01.py` / `test_models_schema.py` | 期望集合改为两组工具与全部表的并集（录音工具默认不进 schemas，`control_device` 不计入） |
| `app/src/services/agent.ts` | `AgentAction`（W10）与 `AgentCareInfo`/`AgentCareTopic`/`AgentTask*`（W08/W09）类型并存；`AgentTurn.blocks` 联合类型补 `action`/`task_result`/`care` 三类块 |
| `app/src/components/agent/agent-message.tsx` | 未知块判定同时扣除文本／提案／动作／任务结果／care 五类；卡片分派：提案按 kind 分派 `TaskProposalCard`／`ProposalCard`，另有 `TaskResultCard`/`CareCard`/`ActionCard` |
| `AGENTS.md` | W08／W09／W10 三条合并为按包分列的现状条目，迁移号改 0015/0016/0017/0018 |

### 7.2 集成期发现并修复的问题

| 问题 | 处置 |
| --- | --- |
| W10 迁移与 W09 迁移同为 `0017` | W10 迁移改名为 `0018_agent_w10_actions.py`，`revision='0018'`、`down_revision='0017'`（接 W09）；全链 0014 → 0015 → 0016 → 0017 → 0018 单 head，`upgrade head` 与 `downgrade base` 均验证通过 |
| `session_scope` 嵌套保护会打断 PetGIF 日记重试路径 | **决定：保留全局嵌套保护**（它正是这次发现潜在嵌套的工具），并修掉被它暴露的真实嵌套点。W10 动作路径在重构后本就不嵌套（`_run_recording` 等已在自身 `session_scope` 之外调用 `publish_control_command`/`latest_telemetry`），保护不依赖它也能成立。`petgif/proxy.py` 的「同幂等键重提且上游结果不明」分支原先在本体 `session_scope` 内调用 `diary_jobs._update_job`/`_read_job`（各自开作用域，嵌套会关闭外层事务——W02 教训），已把该分支移出本体事务并补回归用例 `test_repeat_submit_resumes_unknown_job_without_nested_transaction`。全仓静态扫描确认 `petsense/**` 无其他真实嵌套点。若统筹者仍倾向收窄为作用域内强制，可单独改为基线行为——本轮保留全局保护并在此记录取舍 |
| 系统提示能力清单未覆盖 W10 | 诚实性段的「已接入」清单补入运动日记与社区草稿；录音能力仍由 D3 开关句单独控制（关闭即如实说明未开放） |
| W09 回顾事实缺「活跃目标」 | W09 原分支因 W08 未合入而未接（W09 spec §5.3 已记录）。合并后 `care_jobs.review_facts` 增 `activeTasks`（已确认且未结束的委托：标题、kind、状态、截止），回顾系统提示同步列入「活跃目标」，并补确定性用例 |
| 后台形态遇模型空响应即判失败（真实模型 E51 暴露） | 对话形态早已「空响应放宽 token 上限重试」（W01 已知边界），共享后台回合 `loop.run_structured`（W08 检查点／W09 回顾与事件）却没有重试，一次空响应即降级为 `model_unavailable`。已补同口径重试：最多 2 次、token 上限逐次翻倍，**重试成功不算降级**（只在轨迹与预算里留痕），重试耗尽才判失败并记 `empty_response`。补确定性用例 2 项（`test_agent_w08.py`） |
| 行为评测文件自身缺陷（继承自 W08/W09 分支，无凭据时整组 skip 从未暴露） | ① 模块级漏导入 `AgentTurn` → E53/E54/E55/E57 抛 `NameError`；② `CareBehaviorTests._at()` 缺 `second` 参数 → E51/E52 抛 `TypeError`；③ E58 场景未给宠物绑定项圈，而录音要求已绑定（spec §3）→ 无候选卡；④ E59 断言只认固定短语表，模型实际说「暂时做不了/没有接入/没法真的…」→ 措辞假阴性，改为「录音相关词附近出现不可用表述」判定；⑤ E61 用「已生成」子串判定「不得声称海报已生成」，而模型说的「确认卡已经生成」「海报生成确认卡已出」命中 → 改为显式的海报/日记完成态短语表；⑥ E55 第二段用电量 13%（不构成新事件阶段）却期待一条新消息，与 D2「同一事件按规则去重」冲突 → 改为 8%（<10% 升级阶段）；⑦ E57 断言「消息恰一条」，但事件形态按 W09 spec §3 由模型判断「是否值得此刻打扰」，学习里程碑可不交付 → 改为断言事件只触发一次、表达内容以运行轨迹为准、消息条数 0—1；⑧ E49 的「不得断言『一切正常』」用裸子串判定，模型写的「**不代表**它没有走动或一切正常」这类否定句被误判 → 改为否定感知的断言（否定标记前的出现不算违规）。以上均为测试侧修正，未改 W08/W09 产品语义 |
| 调档后不补发安静档期间打开的设备事实 | 现状：同一事件实例按去重规则只发一次，安静档期间打开的关键设备事实不会在用户调高主动程度后补发（主题仍可追溯）。W09 spec D2「同一事件按规则去重」支持现状，但「调档后补发」是否需要属产品决策，本轮不改实现，作为已知边界记录（见 W09 spec §5.3） |

### 7.3 集成后自动化证据（本轮，2026-11-23）

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 服务端全量 | `cd server && PETSENSE_MQTT_DISABLED=1 .venv/bin/python -m unittest discover -s tests -p "test_*.py"` | **545 项通过**（2 项跳过：需真实凭据/外部条件）。合并前同一命令 540 项通过（2 跳过）；新增 5 项：PetGIF 幂等重提不嵌套事务、W09 回顾事实含活跃目标、后台回合空响应重试成功、后台回合空响应重试耗尽降级、迁移链断言更新 |
| 行为评测 E42–E65 | `cd server && PYTHONPATH=tests MINIMAX_API_KEY=… MINIMAX_BASE_URL=http://127.0.0.1:18117/v1 .venv/bin/python -m unittest agent_behavior.test_agent_behavior -v -k …` | 见 §7.4 说明（真实 MiniMax，经既有安全中继：凭据只留在云端主机 `/etc/petsense/minimax-api-key`，本地不读取、不打印） |
| App | `cd app && npm run lint` / `npx tsc --noEmit` / `npm run format:check` / `npm test` | 全部通过：lint 0 error、typecheck 0 error、格式一致、**107/107** 测试通过（W08/W09 合并带入 `agent-tasks`/`care-rules` 用例） |
| 固件 host | `python3 -m unittest discover -s firmware/tests -v` | **25 项通过**（含 W10 协议 2 项） |
| 固件编译 | `PATH=/tmp/arduinocli:$PATH bash firmware/check-build.sh`（arduino-cli 1.6.0 + esp32:esp32@2.0.16） | 两目标 ESP32 sketch 编译成功，`BUILD_EXIT=0`；固件字节数与 W10 分支实现轮一致（427609 / 483421），合并未改固件 |
| 迁移链 | `alembic upgrade 0014 → head`，再 `downgrade base` | head = `0018`（单 head）；`agent_jobs`/`agent_tasks`/`agent_task_plans`/`agent_task_deliveries`/`agent_care_topics`/`care_device_states`/`care_pet_states`/`agent_actions` 全部就位；downgrade 到 base 后业务表清空 |
| 离线地图 | `server/.venv/bin/python -m unittest discover -s tools/offline-map/tests` | 22 项通过（CI 的服务端/离线地图门槛，与本包无关但同 CI 触发） |

### 7.4 行为评测（真实 MiniMax）

W08 E42–E50、W09 E51–E57、W10 E58–E65 共 24 个场景在合并后的分支上用真实模型复跑。模型请求经既有安全中继转发到云端主机上的 MiniMax Responses API（凭据由云端主机以 root 读取 `/etc/petsense/minimax-api-key`，不回传本地）；测试在本地隔离临时库执行，无生产写入。

结果与用例明细见本轮交付说明（`/tmp/w10-integration/behavior-shard{A,B,C}.log`）。

本轮**未做**模拟器验收（W10-A3/A4/A5/A6、W08-A6、W09-A4 仍待模拟器）；**未做**真项圈验收；生产录音开关保持关闭；无生产写入、无推送、无部署、无 main 变更。
