# Agent W00 spec：契约刷新与选型基线（收尾）

日期：2026-11-22。状态：定稿（关键决策点已于同日与负责人确认，见 §2）。依据：[V11 设计](./agent-product-integration-design.md)、[W 系列工作包](./agent-implementation-work-packages.md) W00 条目、[P00 契约](./agent-p00-contract.md) 第 7 节。

## 1. 定位与边界

W00 是契约与基线包。**接入点声明：无**——不注册工具、不新增运行形态、不新增交付卡片与快照项。

W00 的文档交付三项已随 `98a7096`、`7e43914` 入库，不在本 spec 重复：

1. P00 契约按 V11 语义刷新（运行/轨迹/工具注册表/权限矩阵/常驻快照/分级记忆/WS 传输/SQLite 唯一/前端栈）；
2. 21 项选型记录（V11 §15、§18）；
3. 旧 P→W 映射表（工作包文档 §0）。

本 spec 覆盖剩余交付：

- 现状处置清单逐条对照实际代码核实，冻结为开发基线（§3）；
- 外部依赖实测启动——本轮完成 **MiniMax 搜索/网页阅读能力实测**（§4）；
- Expo 推送凭据、区域语义的状态登记（§5）；
- 旧路由层冻结约定（§6）。

**明确不做**：不启动 W01 内核改造；不修改 agent 域任何运行代码；不动客户端代码；不创建 worktree。

## 2. 决策点记录

| # | 决策点 | 结论 | 状态 |
| --- | --- | --- | --- |
| 1 | 开发流程写入项目记忆 | 后续 W 包一律：spec 先行 → 决策点与负责人讨论 → 实现 → iOS 模拟器接生产验收（账号 `petsense`，密码随仓库记录） | 负责人已确认（2026-11-22） |
| 2 | W00 代码实现范围 | 含 MiniMax 搜索/网页阅读只读冒烟实测；用生产已配置凭据，经 cloud-ssh 在云端执行 | 负责人已确认（同日） |
| 3 | 实测脚本位置 | `server/tools/minimax_search_smoke.py`（新建 `server/tools/`，与 `server/deploy/` 部署脚本区隔） | 实现默认，可推翻 |
| 4 | 实测执行方式 | 云端 `python3 -` stdin 管道执行不落盘；脚本仅依赖 requests + 标准库；凭据读取约定与 `model.py` 完全一致（`MINIMAX_API_KEY(_FILE)`/`MINIMAX_BASE_URL`/`MINIMAX_MODEL`） | 实现默认 |
| 5 | 脚本退出码语义 | 退出码只反映 T0 凭据/端点健康；搜索能力本身的结论是输出数据，不算脚本失败（P00 契约原则：文档不等于连通证据，反之探明不可用也是有效交付） | 实现默认 |

## 3. 现状处置基线（2026-11-22 对照 main `7e43914` 逐文件核实）

本节取代 V11 §14.1 的表格式描述，作为 W01 起各线的开发基线；处置动作在 W01/W02 执行，W00 不动代码。

### 3.1 服务端 `server/petsense/agent/`

| 现状（文件:行） | 内容 | 处置 |
| --- | --- | --- |
| `model.py:8-77` `complete()` | MiniMax 适配：凭据加载（:9-13）、单工具 `query_observations` 硬编码 schema（:15-18）、3 轮循环（:20）、流式增量解析（:26-57）、工具执行回调与结构化错误（:58-72） | **保留并演进**：W01 扩为通用运行循环执行器（多轮、预算、轨迹采集），工具 schema 移入注册表 |
| `model.py:80-89` `decide()` | 前置三分类（scope/speaker/intent 三元组 JSON） | **W01 删除** |
| `routes.py:27-43` `/pets/<id>/observations` | 鉴权观测查询端点 | 保留（App 侧使用），`observations.py` 本体 W01 注册为工具 |
| `routes.py:46-65` turns GET | 历史分页（40 条）、pending 超 5 分钟标 failed 保留原话（:62-64） | 保留；W01 增加 WS 直连通道后保留为降级轮询路径 |
| `routes.py:66-104` turns POST 前半 | 校验、请求键幂等去重（:80-92）、主题/回复引用校验、12 回合历史组装、三字段共享上下文（:99-100：name/species/breed + 占位 observations） | 幂等与校验保留；**共享上下文 W02 替换**为常驻快照服务 |
| `routes.py:105-118` 前置路由 | `decide()` 调用（:108）+ 五个 intent 固定文案分支：`out_of_scope`（:109-110）、`ambiguous`（:111-112）、`observe`（:113-114）、`write`（:115-116）、`search`（:117-118） | **W01 整段删除**：范围/意图/角色判断并入主循环结构化输出，拒答由模型生成 |
| `routes.py:119-147` 主对话 | 指向优先、时间规则注入（:121）、`execute` 观测工具回调（:122-137）、0.5s 节流流式进度（:138-146）、`complete()` 调用 | 保留并演进：executor 换注册表执行器，进度写库换 WS 推送 |
| `routes.py:148-156` 失败与落库 | 失败保留原话「暂时没能接上…」（:149-151）、结果写回 | 语义保留（P00 契约） |
| `routes.py:159-177` `/read` | 已读上报（幂等） | 保留 |
| `observations.py` | 统一观测查询（分钟/声音/体征、覆盖与缺口） | 保留并演进：W01 注册为 `query_observations`，补粒度参数 |
| `learning.py` | 版本化学习累计（有效日口径） | 保留；W05 作基线证据层，重构期间累计不中断 |
| `db/models/agent.py`（`AgentTurn`） | turns 持久化 | 保留并扩展：W01 加内容块与 `AgentRun` 轨迹表 |

### 3.2 客户端

| 现状 | 处置 |
| --- | --- |
| `app/src/app/(tabs)/agent.tsx`（713 行，纯 hooks + 轮询） | W01 迁移到 expo-ai-elements + zustand 栈（先 spike 作 go/no-go） |
| `app/src/services/agent.ts`（58 行 HTTP） | 保留 HTTP 层；W01 增加 `agent-channel.ts` WS 客户端 |
| 无 `stores/`、无 `components/agent/` | W01 新建（V11 §18.1 分层） |

## 4. MiniMax 搜索/网页阅读能力实测（W06 硬前提）

### 4.1 待验证问题（承接 P00 契约第 7 节未决项）

1. M3.1-Flash 的 ChatCompletion 是否接受内建搜索参数（旧版文档 `web_search` 标注 MiniMax-Text-01 限定）；
2. Token Plan 页面列出的 MCP 搜索对当前账户/端点是否可用、以何种请求字段挂载；
3. 官方 [server-tools 文档](https://platform.minimax.cn/docs/guides/server-tools.md)所述 **Responses API / Anthropic Messages API 的 `web_search` 服务端工具**在生产凭据下是否可用（2026-11-22 补入）；
4. 搜索结果是否携带可引用的来源链接（W06 来源卡依赖）；
5. 模型能否直接读取给定 URL 正文（决定 `read_web_page` 需自建 fetcher 还是模型自带）。

### 4.2 脚本设计：`server/tools/minimax_search_smoke.py`

- **只读**：不触数据库、不写文件、不发 MQTT；不改服务配置。
- 凭据与端点：与 `model.py:9-14` 完全一致；输出永不包含凭据值。
- 依赖：`requests` + 标准库；支持 `python3 -` stdin 执行（云端不落盘）。
- 每请求超时 30s，总请求数 ≤ 8，单次运行有界。

| 测试项 | 方法 | 判定 |
| --- | --- | --- |
| T0 基线连通 | 普通 chat completion（无工具） | 200 且有正文 → 通过；失败则脚本退出码 1，后续项标 skip |
| T1 内建搜索参数 | 同一问题时序敏感问题分别带 `web_search: {enable: true}`、`web_search: {enabled: true}` 两种形状 | 按 HTTP 状态与响应体记录：accepted / rejected（留原始错误）/ ignored（200 但无搜索痕迹） |
| T2 MCP 挂载探测 | 请求体分别加 `mcp`、`mcp_servers` 字段变体 | 同上，原始错误入库为证据；探不出合法挂载方式记 unknown |
| T3 来源链接 | 递归扫描 T1/T2 响应 JSON 中任意层级的 URL / citations 字段 | 有可引用 URL → sources yes；否则 no |
| T4 网页阅读 | 随机 token 经 base64 编入 `httpbin.org/base64/<b64>` 与 `postman-echo.com/get?token=` 两级探针，要求模型复述页面内容；token 每次运行随机生成 | 模型返回本次 token → 真实抓取（fetched）；明确说明无法联网 → honest-no-fetch；返回其他内容 → hallucination |
| T5 计费痕迹 | 记录 usage 字段是否出现搜索相关计数 | 尽力而为，只记录 |
| T6 Responses API | `POST /v1/responses` + `tools:[{type:"web_search"}]`，依次探生产域 `api.minimaxi.com`、文档域 `api.minimax.cn`、国际域 `api.minimax.io` | 200 且 output 含 `web_search_call`/`url_citation` 或真实来源 URL → available(真实搜索)；401/404/无痕迹分别记录 |
| T7 Anthropic API | `POST api.minimax.cn/anthropic/v1/messages` + `tools:[{type:"web_search_20250305",name:"web_search"}]`（`x-api-key`） | 200 且 content 含 `server_tool_use`/`web_search_tool_result` 或真实来源 URL → available |

结论输出为矩阵（每项 `available / unavailable / unknown` + 证据摘要），供回填 P00 契约第 7 节与 W06 spec。

### 4.3 执行位置

生产腾讯云（凭据已由 systemd drop-in `40-minimax.conf` 以 `MINIMAX_API_KEY_FILE` 配置，见 `docs/agent-phase-one.md`）。执行方式：`cloud-ssh tencent 'sudo env MINIMAX_API_KEY_FILE=<服务实际路径> python3 -' < server/tools/minimax_search_smoke.py`，凭据路径从 `systemctl show petsense.service` 读取且不回显内容。本地如需复跑，设 `MINIMAX_API_KEY` 即可，脚本行为一致。

### 4.4 结果（2026-11-22 生产实测，共 6 轮运行，脚本 `server/tools/minimax_search_smoke.py`）

环境：腾讯云生产凭据（systemd `40-minimax.conf`）、`https://api.minimaxi.com/v1`、`MiniMax-M3.1-Flash`。

> **结论演变记录**：前 5 轮只探测了 Chat Completions 端点及其参数形状，初判「MiniMax 无内建搜索」；负责人质疑后核对[官方文档](https://platform.minimax.io/docs/guides/server-tools.md)（[国内镜像](https://platform.minimax.cn/docs/guides/server-tools.md)）发现 `web_search` 服务端工具挂在 **OpenAI Responses API 与 Anthropic Messages API** 上，第 6 轮补测证实可用。教训已吸收：探测面必须覆盖官方文档给出的全部接口形态，不能只测既有适配层在用的端点。

| 项 | 结论 | 证据摘要 |
| --- | --- | --- |
| T0 端点与凭据 | **available** | 每轮首请求 HTTP 200 正常回复 |
| T1 web_search 参数 @ Chat Completions | **unavailable（该端点）** | `web_search: {enable\|enabled: true}` 均 HTTP 200 但被静默忽略：无搜索痕迹、无来源链接，模型自述「我目前无法联网核验」（6 轮一致）；官方文档确认该端点 tools 仅支持 `function` 类型 |
| T1b MiniMax-Text-01 @ `/v1/chat/completions` | **unavailable（本端点）** | HTTP 404；旧文档组合在当前账户端点不可达 |
| T2 MCP 字段 @ Chat Completions | **unavailable** | `mcp`/`mcp_servers` 被忽略；`tools: [{type: web_search}]` 400「function is empty (2013)」 |
| T3 来源链接 @ Chat Completions | **no** | 全部响应递归扫描无引用 URL |
| T4 URL 阅读 @ Chat Completions | **不可依赖** | 多次明确声明无法打开网页；3 次幻觉错误 token（含换位错误）；2 次返回可合成内容（`User-Agent: curl/7.64.1`、header 字母序指纹）。无一次可证明真实抓取 |
| **T6 Responses API `web_search`** | **available ✅** | `POST /v1/responses` + `tools:[{type:"web_search"}]`：生产在用域 `api.minimaxi.com` 与文档域 `api.minimax.cn` 均 HTTP 200 真实搜索，返回当天真实来源（weather.com.cn、weather.cma.cn、当日新闻链）与 `url_citation` annotations（title/url/content）；国际域 `api.minimax.io` 401（key 不通用） |
| **T7 Anthropic API `web_search`** | **available ✅** | `POST api.minimax.cn/anthropic/v1/messages` + `tools:[{type:"web_search_20250305",name:"web_search"}]`（`x-api-key` 认证）：200，content 含 `server_tool_use`/`web_search_tool_result` 块与真实来源 |

**对 W06 的结论（2026-11-22 更正版）**：

1. **MiniMax 自带搜索可用，维持原选型方向**，但接口形态是 Responses API（`/v1/responses`）或 Anthropic Messages API，**不是现有适配层在用的 Chat Completions**。W06 需新增一个 Responses API 适配（生产域名 `api.minimaxi.com` 已实测可用）；是否把主对话整体迁到 Responses API 由 W01/W06 spec 权衡。注意事项：官方标注 **Beta**（参数可能变）、启用搜索请求耗时显著增加（官方提示调大超时）、计费页面未注明搜索费用（W06 定稿前核对账户实际扣费）。
2. 来源引用：Responses API 的 `url_citation` annotations 自带 title/url/content 片段，可支撑 W06 来源卡；Anthropic 形态对应 `web_search_tool_result` 块。
3. `read_web_page` 仍建议自建 fetcher：web_search 返回的 content 是摘要片段非整页正文；且 Chat Completions 模型直接读 URL 不可依赖（T4）。W06 可评估浅层引用直接复用 `url_citation.content`、深度阅读走自建 fetcher 的组合。
4. 复测方式：脚本已入库，`MINIMAX_API_KEY(_FILE)` 环境下随时重跑。

## 5. 其他外部依赖状态登记

| 能力 | 本轮动作 | 负责人 | 后续包 |
| --- | --- | --- | --- |
| Expo 推送凭据（iOS APNs key / Android FCM 服务账号） | 仅登记：需准备平台凭据与原生构建，Android 远程推送不能依赖 Expo Go（P00 第 7 节） | 负责人 | W13（平台准备自 W00 起持续） |
| UWB 区域语义（文本标签、时间有效性、版本） | 仅登记：接口未交付，区域工具届时返回明确 unavailable，不阻塞动作/声音线 | 定位同学 | 区域相关工具 |
| MiniMax 搜索/网页阅读 | 本 spec §4 实测 | 本轮完成 | W06 |

## 6. 旧路由冻结约定

W01 合入前，`server/petsense/agent/` 的 `model.decide()`、`routes.py` intent 分支与三字段共享上下文**只修缺陷，不做行为改动**：不新增 intent 分支、不大改提示词语义、不扩展共享上下文字段。缺陷指线上错误（500、去重失效、凭据加载失败一类），修完在阶段 1 实施记录追加一行即可。

## 7. 错误与边界行为

- 实测脚本任一探测请求失败：记录 HTTP 状态与响应体前 500 字符为证据，继续后续项，不中断整体运行。
- 云端执行失败（ssh/凭据路径变化）：不臆测结论；向负责人报告后改本地凭据复跑或登记 blocked。
- 本包不改运行代码，无数据迁移、无兼容开关需求。

## 8. 行为评测场景

W00 不触碰行为路径，无新增场景。W01 spec 将从 §3.1 的「W01 删除」行导出首批回归场景对照清单（既有对话场景行为不劣化）。

## 9. 验收

1. 本 spec 定稿（决策点已确认）。
2. `python -m unittest discover -s server/tests` 全通过（脚本入库不破坏既有套件）。
3. 云端实测执行成功，§4.4 回填结论矩阵；P00 契约第 7 节搜索行按证据更新。
4. **iOS 模拟器基线回归（生产 + `petsense` 账号）**：~~登录 → 伙伴页发送一条消息 → 旧管线正常回复（含身份标注）→ 退出重进会话恢复~~ **负责人免除（2026-11-22）**：W00 无客户端与运行代码变更，本轮不执行；W01 spec 编写时如需「行为不劣化」对照基线，由 W01 验收自行覆盖。已执行部分：生产 API 连通性核对（`/api/app/pets` 未登录返回 401 JSON，符合预期）、Metro 新 bundle 在模拟器加载成功（App 正常进入未登录首页）。
5. AGENTS.md 已记入开发流程（本次变更内完成）。

## 10. 合入与文档更新

- 变更内容：本 spec、`server/tools/minimax_search_smoke.py`、AGENTS.md（开发流程）、P00 契约第 7 节（实测证据）、本 spec §4.4 回填。
- 服务端变更触发 CI 服务端测试与部署；脚本为独立工具，不影响服务运行时。
- 交接：W01 spec 编写时以 §3.1 为现状基线，从 §3.1「W01 删除」行导出回归场景清单。
