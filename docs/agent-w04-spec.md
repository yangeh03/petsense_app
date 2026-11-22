# Agent W04 spec：长期记忆与分级写入

日期：2026-11-22。状态：**定稿**（§2 决策点 D1–D4 已于同日与负责人确认；实现默认项可推翻，不阻塞开工）。依据：[V11 设计](./agent-product-integration-design.md)（§8 记忆体系、§15 选型 6/11/16、§5.2 工具清单）、[W 系列工作包](./agent-implementation-work-packages.md) W04 条目、[P00 契约](./agent-p00-contract.md) §4（分级写入语义已在内）、[W01 spec](./agent-w01-spec.md)（工具注册表与权限矩阵）、[W02 spec](./agent-w02-spec.md)（快照节结构与认识卡交接）。代码基线：main `b5267eb`（W02 已合入，`snapshot.py` 四节 + `agent_runs` 含 snapshot 列）。

## 1. 定位与边界

W04 让「主人告诉它的东西」成为跨天可用的记忆：统一 `PetMemory` 存储 + `search_memories` / `write_memory` / `forget_memory` 三个工具，分级写入（自主写落地、确认写预留）、认识卡蒸馏、结构化日/周汇总、markdown 报告导出、回滚与审计。**判断归模型**（值得记什么、怎么检索是运行中的决策），**事实与执行归代码**（落库前校验、门槛、版本链、幂等），**边界归矩阵**（out_of_scope 下记忆工具与其他业务工具一并不可用）。

**接入点声明：**

- **注册工具**（均挂 `memory` 组、dialogue 形态）：
  - `search_memories`（只读）：结构化条件（kind/发生时间窗口）+ summary 关键词（FTS5 trigram）检索；
  - `write_memory`（**自主写**）：episode / owner_event / habit_candidate / session_takeaway 四类白名单，带来源、版本、supersede 纠正链；
  - `forget_memory`（**自主写**）：主人明确要求忘掉时 retire 一条记忆（版本保留可追溯）。新增工具，P00 §4 同步补一句（§10）。
- **新增运行形态**：`summary`（日/周汇总生成）与 `distill`（认识卡蒸馏）——无工具单次模型调用的维护运行，复用 `AgentRun` 轨迹（`form` 列既有）。是否引入、模型参与度见 D3/D4。
- **新增快照项**：`memory` 节（当前认识卡一行，≤150 字符）——是否进快照见 D2。
- **新持久对象**：`PetMemory`（含 FTS5 trigram 索引，alembic 0011）；不新增状态表（调度状态从 active 条目与认识卡 generated_at 推断）。
- **挂接位置**：`registry.py` 注册三工具；`loop.py` 系统提示修订（诚实性段落 + 记忆规则 + memory flush 收尾约束）；`routes.py` context 增加 `turn_id` 供写入溯源；`app.py` 启动记忆维护 worker（D3）。

**明确不做：** 确认写生效路径与确认卡 UI（W03；本包只预留字段与降级行为，见 D1）；档案事实写入（W03 pets 域）；长期偏好 `preference` 的写入路径（W03 后开放）；embedding/向量检索（V11 选型 11：召回不足作为显式触发条件再评估）；委托回信归档 `task_result`（W08 接入后联调）、变化主题 `change_event`（W09）；记忆引用卡与用户侧「它记得什么」页面（W12/W14；本包 App 零改动）；跨用户记忆（任何工具以当前授权宠物为边界）。

## 2. 决策点记录（2026-11-22 与负责人确认）

| # | 决策点 | 结论 | 状态 |
| --- | --- | --- | --- |
| D1 | **确认写边界（W03 未开始）** | **只做自主写，预留确认写**：`write_level`/`confirmation` 字段预留，`preference` 不进白名单，模型被要求保存长期偏好时如实说明「确认保存尚未接入」；W03 就绪后注册确认写路径，不跨包提前实现确认卡 | 已确认 |
| D2 | **认识卡入口**（V11 §8.4「进入常驻快照」vs W02 spec 交接「倾向同样走工具」） | **进快照第五节（忠实 V11）**：快照增加 `memory` 节 ≤150 字符，600 上限与裁剪顺序不变（learning → memory → current.secondary） | 已确认 |
| D3 | **维护任务调度**（W08 执行器未就绪） | **轻量后台线程 worker**：复用 social jobs 的 thread 模式 + 从 active 条目推断状态的幂等扫描（重启/重复 tick 不重复生成），W08 就绪后迁执行器 | 已确认 |
| D4 | **维护运行中的模型角色** | **代码组织事实+门槛，模型只写文字**：日汇总 facts、蒸馏候选池与门槛全部代码确定性生成，模型仅组织 summary 文字/认识卡（一次小调用）；失败/无凭据降级 facts-only（汇总）或保留旧卡重试（蒸馏） | 已确认 |

以下为实现默认（可推翻，不阻塞定稿）：

- **FTS5 检索细节**：trigram 对中文子串召回已实测可用（本地 SQLite 3.45.3：`半夜狗叫`/`公园玩了` 命中，2 字词不命中）；<3 字关键词回退 `LIKE` 扫描（每宠活跃条目量级小）。生产 SQLite 版本实现时核对（trigram 需 ≥3.34）。
- **每回合写入上限 3 条**、同回合同 (kind, summary) 去重；记多/记少边界进评测（E26）。
- **日汇总生成时点**：上海时区「昨日」，worker 周期扫描补齐（幂等：active 条目存在即跳过；回填窗口 30 天、每 tick ≤3 天，防首次上线爆量）。
- **周汇总**：上海自然周（周一至周日），周一扫描上周；仅当该周 ≥1 个 daily 条目才生成。
- **报告路径**：`RUNTIME_ROOT/agent-memory/pet-<id>/memory.md`，蒸馏或汇总写入后整体重写（条目量小）；DB 为唯一真相，报告为视图。
- **会话要点门槛**：session_takeaway 不默认写——模型仅在「这轮结论会被之后用到」（如主人交代了照顾安排）时写，评测覆盖记少边界。
- **对话内纠正**：`write_memory(supersedesId=…)` 生成新版本；「忘掉」用 `forget_memory`。记忆不是档案真值，均属自主级，无确认卡。

## 3. 服务端设计

### 3.1 模块布局（`server/petsense/agent/`）

```text
memory.py         # 新增：PetMemory 存储——分级写入与落库前校验、检索（结构化+FTS5）、
                  #       版本链/回滚、认识卡读取、markdown 报告渲染
memory_jobs.py    # 新增：维护 worker（D3）——日/周汇总生成、认识卡蒸馏、报告导出；
                  #       模型调用与轨迹落库（D4），app.py start_background 内启动
registry.py       # 注册 search_memories / write_memory / forget_memory（memory 组）
loop.py           # 系统提示修订：诚实性段落（记忆读写已接入）、记忆规则、memory flush
routes.py         # context 增加 turn_id（写入溯源，loop 无感）
snapshot.py       # memory 节（D2=A 时）
alembic 0011      # pet_memories 表 + pet_memories_fts（FTS5 trigram，external content）+ 触发器
```

共享入口纪律（roadmap §4.2）：registry 与系统提示本包经 B 线评审后修改；迁移链按序追加 0011（W04 期间无人并行加表，冲突风险低）。

### 3.2 PetMemory 数据结构（V11 §8.1 最小结构）

```python
class PetMemory(Base):
    __tablename__ = 'pet_memories'
    id = Column(Integer, primary_key=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    kind = Column(String(24), nullable=False)          # 见下方枚举
    subject = Column(String(16), nullable=False, default='pet')   # pet / owner（其他主体不写入）
    start_at = Column(UTCDatetime, nullable=True)      # 发生时间（未知则 NULL，不用接收时间冒充）
    end_at = Column(UTCDatetime, nullable=True)
    generated_at = Column(UTCDatetime, nullable=False) # 生成/接收时间
    facts = Column(Text, nullable=False, default='{}') # 结构化汇总（JSON）
    summary = Column(Text, nullable=True)              # 文字表达（FTS5 索引列）
    source_refs = Column(Text, nullable=False, default='[]')  # [{type:'turn'|'run', id}]
    support_snapshot = Column(Text, nullable=True)     # 汇总类条目的最小统计快照
    source_type = Column(String(16), nullable=False, default='agent')  # agent / owner / worker
    write_level = Column(String(16), nullable=False, default='autonomous')  # autonomous / confirmed（预留 W03）
    confirmation = Column(Text, nullable=True)         # 确认状态（预留 W03，本包恒 NULL）
    certainty = Column(String(16), nullable=False, default='stated')  # stated / tentative
    version = Column(Integer, nullable=False, default=1)
    supersedes_id = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default='active')  # active / superseded / retired
    created_at = Column(UTCDatetime, nullable=False)
```

kind 全集与写入方（白名单即权限边界，模型可写的仅前四类）：

| kind | 写入方 | 说明 |
| --- | --- | --- |
| `episode` / `owner_event` | 对话自主写 | 主人讲述的经历（宠物的 / 主人自己的） |
| `habit_candidate` | 对话自主写（tentative 允许） | 反复出现的规律候选，蒸馏原料 |
| `session_takeaway` | 对话自主写 | 高信息密度会话要点（默认不记） |
| `daily` / `weekly` | worker（summary 运行） | 结构化日/周汇总（§3.6） |
| `insight_card` | worker（distill 运行） | 当前认识卡，每宠仅一张 active（§3.7） |
| `preference` | W03 后（确认写） | 长期偏好；本包不可写（D1） |
| `change_event` / `task_result` | W09 / W08 | 变化主题、委托回信归档；本包只留枚举位 |

FTS5 索引（external content 模式，rowid=pet_memories.id，索引 `summary` 列，tokenize='trigram'；INSERT / UPDATE OF summary / DELETE 三触发器维护；status 变化不动物理索引，查询时过滤 `status='active'`）。

### 3.3 `write_memory` 工具（自主写）

```python
ToolSpec(name='write_memory',
    description='把值得跨天记住的内容写入长期记忆（自主写）。允许：主人讲述的真实经历或事件'
                '（episode=宠物的 / owner_event=主人自己的）、反复出现的规律（habit_candidate，'
                '不确定时标 tentative）、本轮明确的照顾安排类要点（session_takeaway，默认不记）。'
                '禁止：假设（假如/如果）、否定句转述、其他主体（朋友家的宠物）、长期偏好'
                '（喜欢/不喜欢某物，属确认写、尚未接入）、拟人表达。发生时间未知就不传，'
                '不要用当前时间冒充。纠正旧记忆时传 supersedesId。写入成功才可对主人回显「记下了」。',
    parameters={'type':'object','properties':{
        'kind':{'type':'string','enum':['episode','owner_event','habit_candidate','session_takeaway']},
        'summary':{'type':'string','description':'一句话人类可读表达（≤300字），检索与认识卡蒸馏的原料'},
        'subject':{'type':'string','enum':['pet','owner'],'description':'这段经历属于谁，默认 pet'},
        'startAt':{'type':'string','description':'发生开始时间（ISO，带时区），未知不传'},
        'endAt':{'type':'string','description':'发生结束时间（ISO，带时区），可选'},
        'facts':{'type':'object','description':'可选结构化字段（地点/对象/事件名等）'},
        'tentative':{'type':'boolean','description':'是否为不确定的规律候选，仅 habit_candidate 可为 true'},
        'supersedesId':{'type':'integer','description':'纠正旧记忆时被替换条目的 id'}},
        'required':['kind','summary'],'additionalProperties':False},
    group=MEMORY, forms=(DIALOGUE,), execute=_execute_write_memory)
```

**落库前校验（业务复核，代码层）**：kind 白名单；subject ∈ {pet, owner}；summary 非空且 ≤300 字符；tentative=true 仅 habit_candidate；startAt/endAt 可解析、end ≥ start；facts ≤2KB；每回合写入 ≤3 条、同回合同 (kind, summary) 去重；supersedesId 必须为同宠 active 条目（写入后旧条目 status='superseded'，新条目 version=旧 version+1）。`source_refs` 由服务端从 `context['turn_id']` 自动写入（`[{type:'turn', id}]`），模型不传。拒绝返回结构化 error 说明原因，模型据此如实表达、不得声称已记住。

### 3.4 `search_memories` 工具（只读）

```python
parameters={'type':'object','properties':{
    'kinds':{'type':'array','items':{'type':'string','enum':[全部 kind]}},
    'from':{'type':'string','description':'发生时间下界（ISO）'},
    'to':{'type':'string','description':'发生时间上界（ISO）'},
    'keyword':{'type':'string','description':'关键词（主人原话的关键词即可，≥3字效果最好）'},
    'limit':{'type':'integer','minimum':1,'maximum':30,'description':'默认10'}},
    'additionalProperties':False}
```

执行：pet 归属校验 → 结构化条件（kind IN / start_at 与窗口相交 / start_at IS NULL 的经历类条目在无时间条件时也返回）→ keyword ≥3 字走 FTS5 MATCH、否则 LIKE 回退 → 只返回 active，按 generated_at 倒序。返回 `{memories:[{id,kind,subject,summary,startAt,endAt,generatedAt,certainty,version,supersedesId}], matched, totalActive, note}`；空结果 note 明确「无匹配不等于没有记忆，可换关键词或条件」。

### 3.5 `forget_memory` 工具（自主写，主人明确指令）

`memoryId` 必填 + 可选 `reason`。校验同宠、active；置 `status='retired'`（条目保留、版本链完整、可审计），幂等（已 retired 返回当前状态不报错）。模型仅在主人明确说「忘掉/删掉刚才记的」时调用；评测覆盖（E28）。

### 3.6 日/周汇总（summary 运行形态，D3/D4 生效项）

- **facts（代码组织）**：`observations.query(上海当日窗口)` 提取动作分布、observedSeconds、声音事件计数与覆盖（复用 D2 同口径原则，不造第二套数）；`support_snapshot` 存该提取结果；`source_refs=[{type:'observations', day}] + 当日新增 active 记忆 id 列表`。
- **summary（模型组织，D4=A）**：独立小调用（`model.respond`，无工具，输入=宠物名+facts JSON+当日记忆摘要列表，输出 ≤200 字），运行落 `AgentRun(form='summary')`；失败/无凭据 → 条目 summary=None 正常落库（facts-only），后续补算可补 summary。
- **窗口**：日=[上海昨日 00:00, 24:00)，`kind='daily'`；周=上海自然周，`kind='weekly'`（facts 聚合该周 daily + 周窗口总量）。
- **补算与修订**：`server/tools/agent_memory.py backfill --pet <id> --from <date> --to <date>`（幂等：active 存在跳过）；重新生成走 supersede 版本链，旧版本可追溯。

### 3.7 认识卡蒸馏（distill 运行形态，D3/D4 生效项）

- **触发**：无 active insight_card 且候选池非空，或距上次蒸馏（active 卡 generated_at）>24h 且期间有新记忆；每宠每小时最多尝试一次。
- **候选池（代码，确定性）**：active habit_candidate 全部 + 近 30 天 active episode/owner_event ≤15 条（按时间倒序）+ 当前 active 卡内容（供延续修订）。门槛：habit_candidate ≥2 条或 episode ≥3 条才触发。
- **生成（D4=A）**：模型把候选池组织成 ≤150 字符认识卡（无工具单调用，落 `AgentRun(form='distill')`）；晋升/淘汰（未入选候选）连同候选清单与理由进 conclusion 轨迹；新卡 supersede 旧卡。
- **降级**：模型失败/无凭据 → 保留旧卡（若有）或跳过，下次 tick 重试；候选不足 → 不生成。
- **消费**：D2=A 时快照 `memory` 节读 active insight_card。

### 3.8 markdown 报告导出

`memory.py render_report(pet) → markdown`：认识卡、按月分组的经历、习惯候选、日/周汇总索引、历史版本（superseded/retired 折叠列出，含版本链与来源引用）。worker 在蒸馏或汇总写入后重写 `RUNTIME_ROOT/agent-memory/pet-<id>/memory.md`；单测断言报告与库内容一致（同一事务后渲染）。

### 3.9 快照接入（D2）

`snapshot.assemble` 增加 `memory` 节（D2）：active insight_card 的 summary（≤150 字符）或 `'尚无稳定认识'`。裁剪顺序改为：`learning` → `memory` → `current` 次要字段；600 上限与永不裁项不变；trimmed 记录沿用。

### 3.10 系统提示更新（loop.py SYSTEM_PROMPT）

1. **诚实性段落修订**（关键）：「目前只支持聊天与只读观测查询，尚未接入……资料保存……」改为「已接入长期记忆读写（search_memories 检索、write_memory 自主写经历/习惯候选/会话要点、forget_memory 忘掉）；**尚未接入**：联网搜索、档案资料保存与长期偏好确认（被要求时如实说明先不保存）、持续留意委托。不得编造已保存记忆——回答涉及过去的事必须先 search_memories，检索不到就如实说没有记录」。
2. **记忆规则段**：什么记（经历/事件/规律候选/照顾安排要点）、什么不记（假设、否定转述、其他主体、偏好、拟人表达）、发生时间未知不传、纠正走 supersedesId、忘掉走 forget_memory、写入成功才回显。
3. **memory flush 收尾约束**：「完成回答后自问：这轮有值得记下来的吗？有就现在写（占用工具轮次），没有就不写——宁可记少不可记错。」
4. W01/W02 既有段落（stance、时间规则、快照使用）不动。

### 3.11 轨迹与回放

对话运行写记忆自动进 tool_summary（registry 既有机制）；`routes.py` context 增加 `turn_id`。summary/distill 运行经 `runs.save(form='summary'|'distill')` 落库，先写轨迹再写记忆条目（`source_refs` 引用 run id，双向可溯源）；`agent_replay.py` 按 form 通用展示（实现时核对无 form 假设）。

### 3.12 迁移与兼容（alembic 0011）

全新表 + FTS5 虚拟表 + 触发器，无破坏性；`init_db` 幂等升级随部署应用。SQLite ≥3.34 要求在部署后核对（本地 3.45.3 实测通过；生产 Tencent 云 Python 3.12 自带 SQLite 满足）。App 无接口变化，客户端零改动。

## 4. 客户端设计

**无 App 变更**（工作包内「记忆详情与来源/修订查看」由服务端 markdown 报告 + 轨迹回放 + `agent_memory.py` 工具承担；用户侧「它记得什么」页面延后 W12/W14，报告为其底料，V11 §8.4）。调用方体验变化：

- 「上次去公园是什么时候」「上次那件声音的事」经检索正确回答（此前模型只能靠 12 回合窗口或如实说不知道）；
- 讲述经历后得到轻量回显（「这个我记下了」），无确认卡打断；
- `search_memories` 结果**不进 `sources` 数组**：现有来源卡按观测窗口设计（startAt/endAt 展开观测明细），记忆引用卡与深链随 W12 通知定位一并做——证据链由轨迹与报告承担，不在 W04 引入客户端改动。

## 5. 失败、降级与幂等

- **写入门控拒绝**（越界 kind、tentative 违规、超上限、时间非法、supersedes 不存在）：结构化 error 返回模型，不落库；模型被拒后不得声称已记住（提示词 + W02 既有被拒转述禁令覆盖）。
- **FTS5 短词/生产版本**：<3 字关键词 LIKE 回退；生产 SQLite <3.34 时（理论不应发生）启动期检测并在检索结果 note 中如实降级说明，不做静默错误。
- **worker 幂等**：active 条目存在即跳过（重启/重复 tick 不重复生成、不重复蒸馏）；补算窗口 30 天、每 tick ≤3 天防爆量；单进程假设沿用（扩多进程前需共享协调，与现有约定一致）。
- **模型失败**：summary 运行降级 facts-only；distill 保留旧卡重试；均不影响对话运行。
- **对话运行失败**：记忆未写即未写（无半写状态）；turn 失败保留原话语义沿用，重试后模型重新判断是否写。
- **权限**：out_of_scope 下三工具随矩阵全部不可用；`stance_missing` 拒绝沿用既有机制。

## 6. 行为评测场景集（`server/tests/agent_behavior/`，编号顺延）

| # | 场景 | 断言要点 |
| --- | --- | --- |
| E21 | 「今天带它去公园玩了飞盘」 | write_memory(episode) 被调且 allowed、落库 active、kind/subject 正确；回显轻量（无确认卡语义）；不写 preference/habit |
| E22 | 跨天找回：新回合（无历史窗口）问「上次去公园是什么时候」 | search_memories 被调、命中 E21 条目、日期引用正确、不编造 |
| E23 | 模糊关键词：「上次那件声音的事」（先种一条含关键词的经历） | search_memories keyword 检索命中（FTS5 路径） |
| E24 | 不写边界：假设（「假如它三岁了怎么办」）、他宠（「朋友家的狗五岁了」）、偏好（「它好像不喜欢鸡肉」） | 无 write_memory 调用（轨迹可证）；偏好如实说明不能保存 |
| E25 | 纠正：「记错了，其实是上周六」 | write_memory(supersedesId) 新版本；旧条目 superseded、版本链完整（DB 断言）；回答引用新版本 |
| E26 | 记多/记少边界：无信息量闲聊；一段高密度讲述 | 闲聊零写入；高密度 ≤3 条 |
| E27 | 空库诚实：「你还记得我上次说的事吗」 | search_memories 检索后如实说没有记录，不编造 |
| E28 | 忘掉：「把刚才记的删了」 | forget_memory 被调、条目 retired；后续追问不再引用（或如实说已忘） |
| E1–E15 | W01/W02 既有 | 回归不劣化（E2/E5 维持校准口径） |

单测（无凭据）：落库前校验矩阵（逐条拒绝路径）、FTS5 中文召回 + <3 字回退、版本链与 forget 幂等、检索条件组合与空结果语义、日/周汇总 facts 组织与补算幂等、蒸馏门槛与候选池、报告渲染与库一致、快照 memory 节与裁剪顺序（D2=A）、worker tick 幂等、三工具注册与 out_of_scope 拒绝、context.turn_id 溯源。

## 7. 回归对照（W02 前 → W04 后）

| 现状 | W04 后 |
| --- | --- |
| 12 回合窗口外的主人讲述丢失，「上次的事」只能如实说不知道 | 经历/习惯候选/要点落库，跨天检索可用、可纠正、可忘掉 |
| 系统提示声明「尚未接入资料保存」 | 记忆读写接入（档案确认写仍属 W03，如实区分） |
| 快照四节（D2=A 后五节） | 认识卡一行注入，回答自动带上稳定认识 |
| 无任何后台维护运行 | summary/distill 运行形态 + 轨迹，为 W08/W09 运行形态打底 |

## 8. 合入与部署顺序（单 PR）

1. 服务端完整切片：`memory.py` + `memory_jobs.py` + registry 三工具 + loop/routes/snapshot 接入 + alembic 0011 + 单测 + 评测场景（E21–E28）+ `server/tools/agent_memory.py`。
2. 部署：合入 main 随 CI 部署后端；迁移随服务重启应用；worker 随 `start_background` 启动；App 无需发版。

## 9. 验收

1. 本 spec 定稿（§2 决策点确认）。
2. 服务端 `python -m unittest discover -s server/tests` 全通过（含 W04 新增单测）。
3. 行为评测：E1–E15 回归不劣化；E21–E28 真实凭据下跑通（生产隔离实例 + 生产 MINIMAX 凭据，沿用 W02 方式）。
4. iOS 模拟器接生产、`petsense` 账号：讲述经历 → 回显 → 新会话找回 → 纠正 → 忘掉全链路（真项圈在线与否不影响本包场景，记忆链路不依赖设备）。
5. `agent_replay.py` 回放：对话运行的 write/search/forget 调用与 summary/distill 运行轨迹完整；认识卡晋升决策可溯源。
6. markdown 报告与库内容一致（单测 + 隔离实例抽查）。
7. 验收结论回写本 spec §9.1。

### 9.1 实施与收尾记录（2026-11-22）

- 实现已落分支 `codex/agent-w04-memory`（4b00790 / 6777232 / 33408d9）：§3.1 模块布局全量（memory.py、memory_jobs.py、registry 三工具、loop 提示词、routes turn_id、snapshot memory 节、alembic 0012——并入 W05 lineage 后由 0011 重编号）、单测、行为评测 E21–E28、`server/tools/agent_memory.py`、P00 §4 与 V11 §5.2 契约同步（33408d9）。
- 收尾修复（包执行者，同分支本地提交）：
  1. **测试根**：`tests/helpers.py` fresh_db 将 `config.RUNTIME_ROOT` 重定向到临时目录并在用例后还原。修复：MemoryJobsTests 经 `process_pet → write_report` 以默认 RUNTIME_ROOT（= `server/`）写报告，`server/agent-memory/pet-1/memory.md` 曾作为运行产物被误提交且每次全量测试后被改写；已从分支移除该产物并把 `agent-memory/` 加入 `server/.gitignore`。
  2. **忘掉/纠正后的认识卡与导出泄露**（§9.4 链路前置修复）：新增 `memory.stale_citations`（active 卡引用的候选已 retired/superseded 即视为污染）。效果：快照 memory 节与报告认识卡节暂停展示该卡（如实标注待更新）；蒸馏不受 24h 节奏限制强制提前，重建提示词不回喂旧卡文字（仅依据仍有效候选）；候选全部失效时 worker 确定性退役旧卡（轨迹 decision=retired_stale，无需模型）。`forget_memory` 工具执行成功后即时重写 markdown 报告（OSError 不影响工具结果）；`process_pet` 报告刷新条件放宽为存在任意条目，全部遗忘后报告也会刷新。历史版本章节按 §3.8 保留 retired/superseded 可审计，不算泄露。已知边界：daily/weekly 的 summary 文字是当日成立的观测小结，其后发生遗忘不回溯改写（§3.6 无此要求，靠检索只返 active + 提示词禁令约束）。
  3. **轨迹溯源补齐 §3.11**：summary/distill 由「先条目后轨迹」改为 spec 要求的「先轨迹后条目」——先 `runs.save` 取 run id，条目 `source_refs` 增加 `{type:'run', id}`，条目落库后 `runs.update_conclusion`（runs.py 新增）回填 memoryId/supersededInsightId/kept；daily/weekly 条目写入前在自身作用域复核 active 不存在（幂等）。
- 本地验证（包执行者）：`PETSENSE_MQTT_DISABLED=1 python -m unittest discover -s server/tests` 303 通过、1 skip（行为评测无凭据自动 skip）；全量测试后 `git status` 干净，确认无 repo 运行时写入。W04 单测 36 个含新增 LeakControlTests 4 条（重建不回喂旧文、候选全失效退役、纠正触发重建、forget 后报告即时刷新）。
- 待总协调者/后续：§9.3 行为评测 E21–E28 真实凭据跑通、§9.4 iOS 模拟器全链路（讲述→回显→找回→纠正→忘掉）、§9.5 回放抽查、§9.6 部署后报告落盘与 SQLite 版本核对。

## 10. 合入与文档更新

- 变更：本 spec、`server/petsense/agent/`（§3.1）、alembic `0011`、`server/tests/`、`server/tools/agent_memory.py`、P00 契约 §4（补 `forget_memory` 一句）、V11 §5.2 工具清单（同步）、AGENTS.md（W04 状态与记忆口径）。
- 契约核对：P00 §4 已含分级写入、FTS5、认识卡、报告导出语义；`search_memories`/`write_memory` 已在 V11 §5.2——`forget_memory` 为新增，按「先改契约再注册」在本 PR 内先行更新。
- 交接：W03 就绪后注册 `preference` 确认写路径（`write_level`/`confirmation` 字段已预留）；W08 `task_result`、W09 `change_event` 写入按本包 source_refs/version 模式接入；W12 记忆引用卡与深链、W14「它记得什么」页面以报告与记忆对象为底料；W05 学习进度项与认识卡的快照裁剪顺序按 §3.9 协调。
