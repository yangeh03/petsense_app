# W19 spec：模型供应商切换到 Kimi（Moonshot）——便宜多模态模型 + 关闭推理 + 联网搜索

状态：已实现并本地验收（2026-11-25）。生产配置待部署后终验。

## 1. 背景与目标

伙伴页（Agent）原用 MiniMax 直连（`api.minimaxi.com` + `MiniMax-M3.1-Flash`）。负责人要求更换凭据并降本，过程中三次候选：

| 候选 | 实测结论 |
| --- | --- |
| 新 MiniMax key | 认证通过但账户余额不足（HTTP 402 `insufficient_balance_error`，码 1008）→ 不可用 |
| OpenRouter key | 协议完全兼容，但**对中国大陆出口整体关闭推理通道**：所有模型 POST 一律 400 `Request path cannot be relayed`（元数据 GET 正常）。本项目两台服务器（腾讯云 `81.71.71.31`、阿里云 `8.148.188.68`）出口均为 CN → 不可用 |
| Kimi（Moonshot）key | 国内节点 `api.moonshot.cn` 可用，余额 ¥320，Responses 与 Chat Completions 两种形态均可用 → **选定** |

W19 目标（三条硬要求）：

1. **更便宜且支持多模态的模型**；
2. **不要开推理模式**；
3. **打开联网搜索**。

## 2. 选型与实测依据

### 2.1 模型

| 模型 | 输入/输出（每百万 token） | 多模态 | 关推理 | 结论 |
| --- | --- | --- | --- | --- |
| `kimi-k3.1-pro` | $2.00 / $10.00 | 支持 | **不可**（K3 始终推理） | 原选型，降本不足 |
| `kimi-k3.1-pro-highspeed` | $4.00 / $20.00 | 支持 | 不可 | 两次实测 429，排除 |
| `kimi-k3` | $3.00 / $15.00 | 支持 | 不可 | 降本不足 |
| **`kimi-k2.6`** | **$0.95 / $4.00** | **支持** | **支持** | **选定** |

`kimi-k2.6` 是唯一同时满足「比 K3.1 Pro 便宜」「多模态」「可关推理」的模型。已知边界：K2 系列按官方计划 2026 年底退役（新用户 2026-10-31 起不可注册），现有账号不受影响，但届时需再评估。

### 2.2 关推理：只有 Chat Completions 形态支持

Kimi 官方文档（[模型参数参考](https://platform.kimi.com/docs/api/models-overview)、[推理强度](https://platform.kimi.com/docs/guide/use-reasoning-effort)）：

- `thinking.type = "disabled"` 可关闭 `kimi-k2.6` 的思考——**仅 Chat Completions 端点**；
- K3 系列「始终进行推理思考」，`reasoning_effort` 只支持 low/high/max；
- 从 K2.x 迁到 K3 必须移除 `thinking` 字段（否则报错）。

实测（腾讯云出网，2026-11-25）：

| 端点 | 参数 | 输出 token | 推理 token |
| --- | --- | --- | --- |
| Chat Completions | 默认 | 73（`reasoning_content` 有内容） | — |
| Chat Completions | `thinking.type=disabled` | **4**（`reasoning_content` 为空） | 0 |
| Responses | `thinking.type=disabled` | 573 | 570（**被忽略**） |
| Responses | `reasoning_effort=none` | 811 | 808（**被忽略**） |
| Responses | `reasoning.effort=none` | HTTP 400 | 不支持 |
| Responses | `reasoning.effort=low` | 29 | 26（可用，但不是关闭） |

**结论**：关思考必须切到 Chat Completions 形态。

### 2.3 联网搜索：内建工具不执行，改本地工具

Kimi Responses API 文档支持内建 `web_search`（`{"type":"web_search"}`，¥0.03/次），但本账号实测**从不执行**：`kimi-k2.6` / `kimi-k3.1-pro`、多种工具形状（含 `search_content_types`、`max_uses`、`user_location`、`allowed_domains`）与 `include=web_search_call.action.sources`，均无 `web_search_call` 项、无 `url_citation`，模型自述「无法联网」。

官方推荐的 Agent/RAG 路径是**独立搜索 REST 接口**（[联网搜索最佳实践](https://platform.kimi.com/docs/guide/best-practices-for-web-search)），实测可用：

- `POST /v1/tools/search_pro`（¥0.015/次）：返回 `search_results[]`，含 title/url/site_name/date/snippet/chunks；
- `POST /v1/tools/search`（¥0.01/次）：标题/链接/摘要。

因此 W19 把联网搜索改为**本地工具**：模型调用 `search_web` → 服务端调供应商搜索接口 → 结果回灌模型并进网页来源卡。供应商内建 `web_search` 路径保留不动（MiniMax 仍走它）。

## 3. 设计

### 3.1 传输层：两种请求形态（`server/petsense/agent/model.py`）

`respond()` 的对外契约不变（`{'text','function_calls','web_search_calls','citations','usage'}`），内部按 `MINIMAX_API_MODE` 分派：

- `responses`（默认）：原实现，一行未改；
- `chat`：`POST {base}/chat/completions`，转换两处——

| Responses 形态 | Chat 形态 |
| --- | --- |
| `{'role','content'}` | 同（content 为块数组时 `input_text`→`text`、`input_image`→`image_url`） |
| `{'type':'function_call','call_id','name','arguments'}` | `{'role':'assistant','tool_calls':[{'id','type','function':{'name','arguments'}}]}` |
| `{'type':'function_call_output','call_id','output'}` | `{'role':'tool','tool_call_id','content'}` |
| 工具 schema `{'type':'function','name',...}` | `{'type':'function','function':{'name',...}}` |

流式解析按标准 Chat 形态：`choices[].delta.content` 累计正文并回调 `on_delta`；`choices[].delta.tool_calls[]` 按 `index` 合并 `id`/`name`/`arguments`；`usage` 从流帧收取。`thinking` 仅在 `MINIMAX_THINKING_DISABLED=1` 时发送（K3 系列会因该字段报错，故必须显式开关）。

形态选择：显式 `MINIMAX_API_MODE` 优先；未设置时按 base 含 `moonshot` 自动走 `chat`。

### 3.2 本地搜索工具（`server/petsense/agent/search.py` + `registry.py`）

- 新增工具 `search_web`（`EXTERNAL_READ` 组，`DIALOGUE` 形态），参数 `query`（必填）、`limit`（1—8）；
- 执行器走 `agent/search.py`：供应商接口路径、查询词字段名、默认条数由 `MINIMAX_SEARCH_PATH` / `MINIMAX_SEARCH_QUERY_FIELD` / `MINIMAX_SEARCH_LIMIT` 表达（默认 Moonshot `/tools/search_pro` + `text_query` + 5）；结果归一化为 `title/url/site/date/snippet/content`；
- 失败一律返回结构化 error（与 `read_web_page` 同口径），模型据此换关键词或如实说明查不到，不得编造来源；
- **按供应商能力灰度**：`MINIMAX_LOCAL_SEARCH=1` 时才进 schemas，否则模型看不到这个工具（避免调用一个必然失败的工具）；
- 每运行限次 `SEARCH_TOOL_CALL_LIMIT = 2`（与供应商内建搜索同口径，按次计费）；超限按结构化拒绝返回并入轨迹，不真的调用供应商。

### 3.3 loop 集成（`server/petsense/agent/loop.py`）

- 搜索结果的 url/title 进 `citations`，与供应商内建搜索的引用、`read_web_page` 的深读页面合并为同一组**网页来源**（上限 6 条，回合 `sources` 里 `type='web'`）；
- Chat 形态下供应商内建 `web_search` 不存在，`mount_search` 不再挂载该工具；联网只走本地 `search_web`。

## 4. 配置项（全部为服务端环境变量，凭据仍只进 systemd credential）

| 变量 | 取值 | 作用 |
| --- | --- | --- |
| `MINIMAX_API_KEY_FILE` | 凭据文件路径 | 供应商 key（systemd `LoadCredential`） |
| `MINIMAX_BASE_URL` | `https://api.moonshot.cn/v1` | 供应商 base |
| `MINIMAX_MODEL` | `kimi-k2.6` | 模型 |
| `MINIMAX_API_MODE` | `chat` | 请求形态（不设则按 base 自动推断） |
| `MINIMAX_THINKING_DISABLED` | `1` | 发送 `thinking.type=disabled`（仅 K2 系列支持） |
| `MINIMAX_LOCAL_SEARCH` | `1` | 启用本地 `search_web` 工具 |
| `MINIMAX_SEARCH_PATH` | `/tools/search_pro` | 搜索接口路径 |
| `PETSENSE_SEARCH_DISABLED` | **不设** | 联网搜索总开关（设置则 EXTERNAL_READ 工具全部不进 schemas） |

## 5. 验收

### 5.1 单元测试（`server/tests/test_agent_w19.py`，18 项全过）

形态选择、会话/工具转换、图片块转换、流式解析（正文增量 + tool_calls 分片合并 + usage + `on_delta`）、thinking 开关、chat 形态忽略供应商内建搜索、搜索结果归一化、失败结构化、空结果提示、工具按能力灰度、loop 里搜索结果进网页来源卡、搜索限次。

### 5.2 真实模型冒烟（`server/tests/test_agent_w19_real.py`，无凭据整组 skip）

隔离临时库 + 真实 Kimi 凭据（`kimi-k2.6` / chat / 关思考 / 本地搜索），2026-11-25 实测：

| 回合 | 耗时 | 结果 |
| --- | --- | --- |
| 闲聊 | 20.0s | 正文正常，stance 解析正确 |
| 观测问答 | 17.2s | 调用 `query_observations`，回合带观测来源卡 |
| 联网问答 | 50.2s | `search_web`×2 + `read_web_page`×1，网页来源卡带真实链接 |

### 5.3 待办（部署后终验）

- 腾讯云生产配置切换后跑真实伙伴会话（闲聊 / 观测 / 联网三回合），核对来源卡与来源链接；
- 确认生产搜索时延与计费（搜索按次计费，观察实际用量）；
- K2 系列退役前（2026 年底）重新评估模型。

## 6. 已知边界

1. **K2 系列退役**：官方计划 2026 年底下线 K2 系列，届时 `kimi-k2.6` 不可用，需重新选型（K3 系列无法关推理，届时要么接受推理、要么回到其他供应商）；
2. **搜索质量依赖供应商**：本地工具把「搜什么」交给模型、把「搜」交给供应商接口，检索质量与召回由供应商决定；模型可能搜到过期内容（实测出现过用错年份查询导致结果陈旧），提示词要求以检索到的来源为准、不得编造；
3. **日期依赖快照**：模型从常驻快照取当前时间，未带时间信息时可能给出错误日期（实测空快照时模型编了 2025 年的日期）；生产快照始终带时间，属测试夹具差异；
4. **搜索轮时延较长**：含 2 次搜索 + 1 次深读的网络问答回合约 50s，高于纯闲聊回合；
5. `read_web_page` 仍为自建 fetcher（SSRF 防护不变），与供应商搜索接口互补：一个按 URL 深读，一个按关键词检索。
