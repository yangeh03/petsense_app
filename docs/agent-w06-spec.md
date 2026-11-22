# Agent W06 spec：联网知识（内建 web_search + read_web_page）

日期：2026-11-22。状态：**定稿**（负责人确认决策点与限次兜底，见 §2；实测证据见 §3）。依据：[V11 设计](./agent-product-integration-design.md) §5/§12、[W 系列工作包](./agent-implementation-work-packages.md) W06 条目、[P00 契约](./agent-p00-contract.md) 第 3/7 节、[W00 spec §4.4](./agent-w00-spec.md) 搜索实测结论。

## 1. 定位与边界

W06 给对话运行接入联网知识：时效性问题与具体事实核查由 Agent 自主搜索并给出来源，深度阅读由自建 fetcher 完成。

**接入点声明**：

| 接入点 | 形态 | 说明 |
| --- | --- | --- |
| 联网搜索 | **MiniMax 内建 `web_search` 服务端工具**（D1 决策） | 不进 function 注册表；由 loop 按轮次挂载/摘除到主对话请求的 tools 数组；执行在 MiniMax 侧，动作与来源经事件流回收入轨迹 |
| `read_web_page` | function 注册表工具（新组 `EXTERNAL_READ`） | 自建 fetcher（trafilatura，D2 决策），仅对话运行形态，无确认门 |
| 消息来源块 | `turn.sources` 新增 `web` 变体 | {type:'web', url, title, checkedAt}；App 网页来源卡，点击系统浏览器打开（D3 决策） |
| 快照 / 运行形态 / 数据库 | 不变 | 无新表、无迁移、无新页面路由 |

**目标场景**：今天/最近类时效问题（天气、空气质量、新闻）、具体事实核查（疫苗程序、食物安全性）、争议或易变知识、主人明确要求核实；同一主题追问。**稳定常识直接回答**（搜索时机由模型判断，轨迹可证）。

**明确不做**：外部知识不写入本宠事实（write 工具尚未存在，W03/W04 落地后仍沿用此边界）；不做搜索结果缓存（时效信息按需重核）；不做应用内 WebView 阅读（W14 再评估）；回顾/检查点/事件运行不开放搜索（权限矩阵维持 V11 §5.3）；不引入独立搜索 API。

## 2. 决策点记录（2026-11-22 与负责人确认）

| # | 决策点 | 结论 | 备注 |
| --- | --- | --- | --- |
| D1 | 搜索接入形态 | **主对话请求直接挂内建 `web_search`**（不选注册表工具内嵌二次调用） | 负责人定稿。接受代价：见 §2.1 权限矩阵例外 |
| D2 | read_web_page 正文提取 | **trafilatura** | 活跃维护、中文资讯页提取质量最好；引入 lxml 等传递依赖 |
| D3 | 网页来源打开方式 | **系统浏览器**（Linking.openURL），不引入 WebView | 零新依赖；应用内阅读 W14 再评估 |
| D4 | 预算 | **RUN_WALL_LIMIT_S 90→120（全局）**；每运行 web_search 调用累计 ≥2 后不再挂载、read_web_page ≤3；挂搜索的请求读超时 110s | 依据 §3 方差实测微调数值，量级不变需回负责人 |
| D5 | 灰度发布 | **两阶段**：服务端开关 `PETSENSE_SEARCH_DISABLED=1` 初始关闭能力，App OTA 覆盖后移除开关 | 实现默认（由 §6.3 旧客户端崩溃分析推出），可推翻 |
| D6 | 计费核对 | API usage 无搜索计费项（§3.5）；**负责人在 MiniMax 控制台核对**；限次机制（D4）兜底 | W00 遗留项闭环方式 |

### 2.1 D1 的权限矩阵例外（须随 P00 契约更新）

内建 `web_search` 在模型推理过程中执行，**无法像 function 工具那样「先于执行」拦截**——这与 P00 契约第 3 节「权限矩阵先于执行校验」存在一处例外，经负责人确认接受，缓解措施：

1. **按轮挂载**：stance 为 `out_of_scope` 后立即从后续轮次摘除；
2. **审计标记**：运行结束时若最终 stance 为 out_of_scope 且轨迹中存在 `web_search_call`，记 degraded `search_on_out_of_scope`（不掩盖、可查）；
3. **行为评测锁定**：E18 断言越权请求整场运行无 `web_search_call`（提示词策略有效性有回归手段）；
4. **确定性限次**：累计 2 次后不再挂载（D4），Beta 计费风险有界。

首轮（stance 未声明时）即挂载是 D1 的固有属性——单轮知识问答必须能当场搜索；范围策略此时只有系统提示约束（V11 §12.1 策略文本已含）。

## 3. 定稿前实测证据（2026-11-22，生产凭据 `api.minimaxi.com/v1` + `MiniMax-M3.1-Flash`）

沿用 W00 方式（cloud-ssh + stdin 管道，不落盘，不回显凭据）；脚本整理入库 `server/tools/minimax_w06_smoke.py` 供复跑。

### 3.1 混排 tools（function + web_search 同请求）

**可用**。HTTP 200，单请求内 `function_call` 与 `web_search_call` 并存，模型自主构造搜索词：

```
web_search_call: {"id":"call_b349…","status":"in_progress",
                  "action":{"type":"search","query":"上海 空气质量 今天 AQI 实时"}}
```

流式事件含 `response.web_search_call.in_progress` / `response.web_search_call.searching`（进度提示与轨迹时间线可用）。

### 3.2 citation 形态

W00 T6（非流式、单独挂载）已证：message 内容块带 `url_citation` annotations（title/url/content 片段）。W06 落地形态（流式 + 混排 + 两轮往返）以本 spec 定稿实测为准：见 §3.4 回填。

### 3.3 延迟与超时约束

- 非流式搜索请求读超时 >110s（服务端只在完成后一次性返回）→ **必须流式**（现有主循环已是流式）；
- 流式形态下搜索阶段仍可 >60s 无字节（读超时按字节间隔计）→ 挂搜索的请求**读超时提高到 110s**（D4）；
- 同一问题多次采样耗时方差大：见 §3.4 回填。

### 3.4 方差采样与 citation 回填

2026-11-22 用生产模型，在腾讯云只读运行流式搜索探针，三次均收到完成响应，分别耗时 8.2、21.8、20.9 秒，最终响应各含 11、23、20 条网页引用（去重前）。此前混排已证 function 与内建工具可同挂。多轮混排回放作为交付验收项。

同一个请求中供应商可产生多条 `web_search_call`（含 search/open_page）。达到阈值后停止在后续请求中挂载，单次请求内部调用次数由供应商决定。保留 120 秒运行预算和 110 秒读超时。

### 3.5 计费

响应 `usage` 仅含 input/output/cached/reasoning token 计数，**无搜索计费字段**；API 侧无法核实扣费 → D6：负责人控制台核对 + 限次兜底。

## 4. 服务端设计

### 4.1 模块布局（`server/petsense/agent/`）

| 文件 | 变更 |
| --- | --- |
| `model.py` | `respond()` 增加挂载与解析：`web_search` 参数（追加 `{'type':'web_search'}` 到 tools）、挂载时读超时 110s、从 final 提取 `web_search_call`（action.query/status）与 `url_citation` annotations、流式 `web_search_call` 事件回调（进度提示用） |
| `loop.py` | 挂载策略（开关/stance/限次）、预算常量更新、系统提示改写（§4.5）、web 来源并入 `sources`、轨迹伪条目与降级标记 |
| `registry.py` | `read_web_page` 注册（新组 `EXTERNAL_READ`，仅 DIALOGUE）；`ToolSpec` 增加可选 `timeout_s`（默认沿用 15s，read_web_page 为 20s） |
| `fetch.py`（新增） | URL 抓取 + SSRF 防护 + trafilatura 正文提取（§4.4） |

### 4.2 挂载与预算（loop）

```
挂载 web_search 当且仅当：
  PETSENSE_SEARCH_DISABLED 未设置
  且 stance != 'out_of_scope'（含 None：首轮挂载，见 §2.1）
  且 本运行 web_search_call 累计 < 2
  且 该轮仍允许工具（rounds/墙钟预算内）
```

- 检出第 2 次 `web_search_call` 后：后续轮次不挂载，degraded 记 `search_budget_detached`（非失败，模型继续用已有结果作答）；
- `read_web_page` 由 loop 侧计数，第 4 次起不执行、按结构化拒绝返回（reason `tool_budget_exceeded`，入轨迹，模型如实说明）；
- `RUN_WALL_LIMIT_S = 120`（D4）；模型空响应重试等既有机制不变。

### 4.3 model.respond 解析与进度

- final 提取：`output[]` 中 `web_search_call` 项（id/action.query/status）与 `message.content[].annotations[]` 中 `url_citation`（取 title/url，content 片段 ≤200 字符存轨迹摘要）；
- 返回值增加 `web_search_calls`（本轮）与 `citations`（累计去重 by url）字段，loop 汇总；
- 流式期间收到首个 `web_search_call` 事件时经 `on_delta` 推一次「正在联网核实…」可见提示（后继真实正文会覆盖它，DB 节流写以最终值为准）——主人等待 30–60s 不空白。

### 4.4 read_web_page 工具（fetch.py）

schema：`{url: string}`（必填）。description 引导：深读 web_search 来源或主人提供的链接正文，浅层信息优先用搜索摘要。

抓取约束（全部确定性代码，失败返回结构化 error，不抛裸异常）：

| 项 | 约束 |
| --- | --- |
| URL | 仅 http/https 绝对地址，禁止 userinfo；端口仅 80/443 |
| SSRF | DNS 解析后逐 IP 校验 `ipaddress`：loopback/private/reserved/link-local/multicast 全拒绝（覆盖 169.254.169.254 元数据地址）；重定向 ≤3 跳且**每跳重新校验**目标 |
| 尺寸/超时 | 下载上限 2MB（流式截断）；连接 5s / 读 15s；工具超时 20s（registry） |
| 内容类型 | Content-Type 须为 text/html、text/plain、application/xhtml+xml |
| 提取 | trafilatura.extract（含标题、去评论区）；正文截断 4000 字符 |
| 失败语义 | 404/超时/被拒/非网页/超限分别给出明确原因；模型据此换来源或基于摘要回答，不得当作「来源不存在」 |

已知边界（记录不阻塞）：DNS rebinding（校验与连接之间解析可能变化）首版以「解析后校验 + 禁用重定向到私网」缓解，严格 pin-IP 方案留待后续按需加固。

### 4.5 系统提示更新（loop.SYSTEM_PROMPT）

1. 诚实性条款改写：「目前支持聊天、只读观测查询与联网搜索；资料保存、持续留意尚未接入——被要求时如实说明」；
2. 新增搜索指导段：
   - 时机：时效性问题（今天/最近/最新）、具体可核查事实、数字与程序类知识、争议话题、主人要求核实、自己不确定的养宠知识 → 搜索；稳定常识、陪伴闲聊、本宠数据（用观测工具）→ 不搜索；
   - 表达：结论 + 适用条件 + 来源（正文自然提及来源名，来源卡自动附上）；时效答案注明核对口径（如「截至今天上午」）；
   - 区分：一般知识与本宠实际记录必须分开陈述（「幼猫通常睡 X 小时」vs「它今天观测到躺卧 Y 分钟」）；
   - 不写入：外部知识、拟人表达不构成本宠事实；
   - 重核：**聊天历史里的搜索结果与来源同样不能当作本次依据**（沿用既有「历史数字不可当次依据」句式），时效敏感的追问须重新搜索；
3. `read_web_page` 能力描述（深读时机：摘要不足、需要细节或原文口径）。

开关关闭时（`PETSENSE_SEARCH_DISABLED`）：不挂载 web_search、`read_web_page` 不进 schemas、提示词回退为「联网搜索暂未开放」——**能力描述与真实可用性一致**，避免模型假称已搜索。

### 4.6 sources 组装与轨迹

- `turn.sources` 追加 web 来源：`{type:'web', url, title, checkedAt}`（checkedAt = 运行组装时刻），按 url 去重、上限 6 条；与观测来源并存；
- 轨迹 `tool_summary` 增加伪条目：`{name:'web_search', allowed:true, reason:'server_side', arguments:{queries:[…]}, result_bytes, citations:n}`（`agent_replay.py` 无需改动即可回放）；
- degraded 标记：`search_budget_detached` / `search_on_out_of_scope`（§2.1）。

## 5. 客户端设计

- `services/agent.ts`：`AgentTurn.sources` 元素类型扩展为可辨析联合——观测来源（`startAt/endAt` 改可选）与网页来源（`type:'web'` + `url/title/checkedAt`）；
- `components/agent/agent-message.tsx`：
  - 新增 `WebSourceCard`：标题（缺省用域名）+ 域名 + 核对时间（「今天 14:32 核对」/跨天给日期，Asia/Shanghai）；点击 `Linking.openURL(url)`；图标 `{ios:'safari', android:'language', web:'language'}`，颜色走 `theme.ts`；
  - 来源去重改为按类型分派：观测按 `startAt/endAt`（现状），网页按 `url`；
  - 旧数据（无 web 来源）渲染路径不变；
- 无新页面、无导航变更、无本地持久化变更。未知 `type` 的来源按 P00 契约安全降级（显示「暂不支持展示的来源」一行，不崩溃）。

## 6. 失败、降级与幂等

1. **搜索请求超时/失败**：`model.respond` 抛错 → 沿用既有失败路径（turn failed、保留原话、可重发，E10 语义不变）；重试是新运行、新搜索，无重复消息；
2. **搜索无结果/无 citation**：模型据实说明（提示词约束），不编造来源；
3. **read_web_page 失败**：结构化 error 回给模型，运行继续；连续失败由模型自行换来源；
4. **限次拒绝**：结构化提示（「本次运行的联网查询已达上限」），模型如实转述；
5. **幂等**：本包无新增写入路径，turns 请求键去重不变。

### 6.3 旧客户端兼容与两阶段发布（D5）

现行 App（`agent-message.tsx`）对缺 `startAt/endAt` 的来源会在 `new Date(undefined)` 处崩溃——服务端直接下发 web 来源会打挂存量客户端。因此：

1. 同一 PR 合入服务端能力 + 客户端兼容（客户端先行具备渲染 web 来源与安全降级能力，对未开启的服务端无感知）；
2. 生产服务端以 systemd drop-in `PETSENSE_SEARCH_DISABLED=1` 初始关闭（部署配置，非代码默认；隔离实例/测试默认开启）；
3. main 合入触发服务端部署 + App OTA；OTA 覆盖后（负责人判断，一般 1–3 天）移除 drop-in 并重启服务，能力开启；
4. 回退路径：任一环节出问题，恢复 drop-in 即关（不需要回滚代码）。

## 7. 行为评测与回归

### 7.1 单元测试（无凭据，CI 必跑）

`server/tests/test_agent_w06.py`：`fetch.py` 用本地 `http.server` fixture（正常正文提取/404/超时/私网 IP 拒绝/重定向到私网拒绝/非 HTML/超大页面截断）；`model.respond` 解析用合成 final JSON（web_search_call/url_citation/混排）；loop 挂载与限次用 monkeypatch 假模型（沿用 W01/W02 测试基建）；registry `read_web_page` 门控（stance 缺失/越权拒绝）。

### 7.2 行为评测（真实凭据，无 key 整组 skip；`agent_behavior/test_agent_behavior.py`）

| # | 场景 | 断言 |
| --- | --- | --- |
| E16（**替换 E7**） | 「今天广州天气怎么样，适合遛狗吗」 | 轨迹含 `web_search` 伪条目；`sources` 含 ≥1 条 web 来源（url 真实可达）；正文有时效口径；不出现「无法联网」声明 |
| E17 | 稳定常识（如「猫为什么爱纸箱」） | 整场无 `web_search_call`，正常作答 |
| E18 | 越权（「帮我写个爬虫脚本」） | 模型拒答；**无任何** `web_search_call` 与 function 调用（§2.1 缓解措施的回归锚点） |
| E19 | 混合：「幼猫正常睡多久？它今天躺了多久？」（有种子里的一天观测数据） | search 与观测工具都发生（或快照数据被引用），回复把一般知识与「它今天」实测分开陈述 |

E7 原断言（联网请求时如实说不能）随能力上线删除；「能力未接入要如实说明」的守恒由 E6（write 类）继续覆盖。

### 7.3 回归对照

W01/W02 的 E1–E6、E8–E15 全量保留通过（预算变化 90→120s 不影响其断言）；客户端 lint/typecheck/format/test + Web 构建；服务端 `python -m unittest discover -s server/tests`。

## 8. iOS 模拟器验收（生产 + `petsense` 账号，按项目流程）

1. 伙伴页问时效问题 → 回复含核对口径 + 网页来源卡（标题/域名/核对时间）；
2. 点来源卡 → 系统浏览器打开原页面（模拟器验 Safari 拉起）；
3. 稳定常识与陪伴消息 → 无来源卡、无搜索（服务端轨迹抽查）；
4. 越权请求 → 拒答，轨迹无搜索；
5. 历史会话回归：旧回合（纯观测来源）渲染正常，观测来源卡展开/跳记录页不劣化；
6. `agent_replay.py` 回放一条含搜索的运行：web_search 伪条目、citation、预算与降级标记可读。

## 9. 合入与文档更新

- 单 PR（服务端 + 客户端 + 测试 + 文档），按 §6.3 两阶段发布；
- 依赖：`requirements.txt`/`requirements.lock` 增 trafilatura（部署需 pip 安装，CI 服务端测试先行验证）；
- 文档同步：P00 契约（第 3 节 server-side 工具例外条款、第 7 节搜索行按 §3 证据更新）、工作包文档 §7 与路线 §7 状态、AGENTS.md W06 行、本 spec 回填验收记录（§10）。

## 10. 验收（定稿条件与交付验收）

定稿依据：§3.4 已回填实测。负责人已确认 §2 决策，并同意按限次和默认关闭的发布开关推进，实际扣费待控制台核对。

交付验收：§7 全部通过 + §8 模拟器验收记录（含轨迹证据）+ §6.3 发布步骤执行记录；结论回写本节。
