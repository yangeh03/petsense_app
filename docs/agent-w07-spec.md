# Agent W07 spec：图片输入

日期：2026-11-22。状态：**定稿**（§2 决策点已于同日与负责人确认；实现默认项可推翻，不阻塞开工）。依据：[V11 设计](./agent-product-integration-design.md)（§4.2 信息全景「图片」行、§13.2 伙伴交互、§18.2 卡片族、§18.4 图片消息、§15 选型 20/21）、[W 系列工作包](./agent-implementation-work-packages.md) W07 条目（「相册/拍照输入、上传状态、失败重试、图片消息内容块持久引用；双人格共享附件上下文；`react-native-image-viewing` 灯箱；单张照片不当作持续观测；不含语音与媒体生成」）、[P00 契约](./agent-p00-contract.md) §2（图片引用为首批内容块之一）、[W01 spec](./agent-w01-spec.md)（WS 帧与 turn 快照）、[W02 spec](./agent-w02-spec.md)（快照与工具边界）。代码基线：main `b5267eb`（已核实：`agent_turns.blocks` 仅 text 块、POST turns 纯 JSON、loop 历史构造纯字符串 content、`server/` 无任何多模态代码、`react-native-image-viewing` 未安装）。

## 0. 外部验证（2026-11-22 已完成，生产凭据实测）

W07 硬前提——MiniMax 图片输入在 **Responses API**（W01 后主对话所在端点）可用性——已在腾讯云用生产凭据实测（脚本 `server/tools/minimax_vision_smoke.py`，随本包入库）：

| 项 | 结论 | 证据 |
| --- | --- | --- |
| V1 `input_image` + data URL（字符串形态） | ✅ 可用 | 1024×1024 JPEG（39.6KB）文字/形状/颜色全部识别正确；usage `input_tokens: 1589`（≈1.6k token/张） |
| V1b `{url:...}` 对象形态 | 未测（V1 已过，不采用） | — |
| V2 图片与 Function Calling 共存 | ✅ 可用 | 同请求携带 `tools` 正常识别作答（agent 循环恒带工具的前提成立） |
| V3 多轮历史保留图片 | ✅ 可用 | 全量回显形态下首轮图、次轮文字追问「图中文字」回答正确 |

P00 §7 图片行据此更新：Responses API 证据替代旧链路烟测；1024px 量级图片 ≈1.6k input token 是规格决策（D2）的实测依据。`previous_response_id` 不可用、全量回显模式沿用（W01 结论），图片随历史重发与 V3 一致。

## 1. 定位与边界

W07 打开伙伴会话的**图片输入**：主人在消息里附带相册/拍摄的照片，与文字一起进入对话运行；模型按交流背景理解图片，双人格共享附件上下文。图片经独立上传端点持久化，以内容块引用进入 `agent_turns`，在模型输入中转为 `input_image`（data URL）内联。**不注册新工具**（V11 §4.2：图片是多模态输入，不是工具能力）；不新增运行形态。

**接入点声明：**

- **新端点 2 个**：`POST /api/app/agent/pets/<pet_id>/images`（multipart 上传，鉴权，归属校验）；`GET /api/app/agent/images/<image_id>`（鉴权读取，仅本人）。
- **消息协议扩展**：`agent_turns` 新增 `images` 列（JSON 数组，owner 侧附件引用）；POST turns 请求体新增可选 `images: [imageId]`；`public()`/WS turn 快照携带。**不占用 `blocks` 列**（blocks 保持「助手回复内容块」语义，P00 §2 的 owner 图片引用单列承载）。
- **模型输入转换**（`loop.py`）：含图片的 owner 消息 content 从字符串变为块数组（`input_text` + `input_image`×N）；历史图片按 D1 窗口内联，窗口外降级为文字占位。
- **系统提示**：新增图片使用规则段（单张照片≠持续观测、不把图片内容当实时状态或档案事实、识别不了如实说明）。
- **轨迹**：图片以引用摘要（id/尺寸/字节数/是否内联）入 trace，**不入 base64**（轨迹体积与卫生，沿用 W02 D4 原则）。
- **客户端**：输入区附件入口（相册/拍照）、上传状态与失败重试、消息图片渲染（expo-image + 鉴权头）、`react-native-image-viewing` 灯箱（新增依赖，纯 JS 无原生构建）；`normalize-image.ts` 参数化支持更激进的压缩档位。

**明确不做：** 图片信息落档案（W03 确认协议落地后经 `prepare_profile_update` 复用；本包内系统提示如实告知无写入能力）；语音与媒体生成（工作包边界）；持续观测/视频（单张照片按交流背景处理）；缩略图体系（1280px 原图直接渲染）；图片消息的已读/主题特殊语义（沿用 turn 既有字段）；多图批量上传优化（逐张上传）；Android 专项验收（沿用仓库 iOS 验收约定）。

## 2. 决策点记录（2026-11-22 与负责人确认）

| # | 决策点 | 结论 | 状态 |
| --- | --- | --- | --- |
| D1 | 历史图片内联深度（token 成本 vs 追问体验） | **当前轮 + 最近 1 条主人消息内联，更早的降级为「（附 N 张图片，已省略）」文字占位**。实测 ≈1.6k token/张（1024px），12 轮全量内联最坏可膨胀数万 token；只内联当前轮则「刚才那张图里的…」追问失忆 | 已确认 |
| D2 | 图片规格（清晰度 vs token/传输成本） | **客户端压缩长边 1280、JPEG q0.8、单条消息 ≤3 张**（低于社区 2048 标准：聊天图以理解为目的，1024px 实测识别可靠）。服务端一律解码校验 + 重编码封顶（§3.2），客户端压缩只是第一道 | 已确认 |
| D3 | 保留与读取策略 | **永久保留（对齐聊天记录语义）、仅登录本人可读（鉴权端点 + `X-Petsense-Session`，区别于头像的公开 `/uploads/`）、按用户目录隔离**；单用户 ≤2000 张配额（超限结构化拒绝） | 已确认 |

以下为实现默认（可推翻，不阻塞定稿）：

- **传输协议：先传图、后发消息**。上传端点单文件 multipart（每张一请求，失败按张重试），返回 `{id,width,height,bytes}`；POST turns 纯 JSON 引用 imageId。备选「turns 混合 multipart」被否：消息幂等键与文件传输失败语义纠缠，失败重试要重传图片。
- **图片 id = 内容 SHA-256 hex**，文件名 `<id>.jpg`，存 `{PETSENSE_ROOT}/agent_media/<user_id>/`；同内容重传天然去重（同 id 直接返回）。**无独立图片元数据表**——引用与尺寸元数据存在 `agent_turns.images` JSON 内，避免与 W03/W04/W05 并行包争夺迁移编号（W07 仅一个 `agent_turns.images` 可空列迁移，编号合入时取当时最末）。
- **纯图发送允许**：`text` 在有图片时可空（原 1–4000 下限放宽为 0），模型自行理解作答；无图片时维持原校验。
- **图片不上快照、不进来源卡**：图片是本轮输入事实，随消息进历史；`sources` 卡（观测来源）语义不变。
- **`normalize-image.ts` 参数化**：新增可选 `{maxEdge, quality}` 参数，默认值不变（2048/0.85），agent 链路传 1280/0.8——不影响社区/故事/头像既有调用。
- **灯库 `react-native-image-viewing`**：V11 §18.4 选型，纯 JS、Expo Go 可用；作为运行时依赖加入 `app/package.json`。
- **相机与相册都开放**：对齐 `create.tsx`/`pet.tsx` 既有交互；`app.json` 补 iOS 相机 usage description（相册 iOS 11+ 系统选择器无需权限文案；Android 权限由库 manifest 合并）。

## 3. 服务端设计

### 3.1 模块布局（`server/petsense/agent/`）

```text
attachments.py  # 新增：save_agent_image(file_storage, user_id) -> dict
                #   解码校验（JPEG/PNG/WEBP、≤10MB、≤1600万像素、EXIF 转正、DecompressionBomb）
                #   → 服务端封顶缩放（长边 ≤D2 上限）→ 重编码 JPEG q85 → agent_media/<uid>/<sha256>.jpg
                #   模式复用 social/routes.py:save_upload_image（不抽通用模块——三处既有复制是既成事实，
                #   本包不扩大重构面；校验规则保持与 social 一致的行为基线）
routes.py       # POST images / GET images/<id>；POST turns 增 images 校验；public() 增 images 字段
loop.py         # 历史与本轮 owner content 块化；图片规则段进 SYSTEM_PROMPT；trace 图片摘要
db/models/agent.py        # AgentTurn.images（Text nullable，JSON 数组）
alembic/versions/00XX_agent_turn_images.py  # 加列迁移（编号合入时定，down_revision=当时 head）
server/tools/minimax_vision_smoke.py        # §0 冒烟脚本（已入库）
```

共享入口纪律：`loop.py` SYSTEM_PROMPT 与 `routes.py` 为 W03–W06 并行共享点，本包改动最小化（提示词只追加一段、路由只加两端点与一个字段），集成时顺序合并。

### 3.2 上传与读取

- `POST /api/app/agent/pets/<pet_id>/images`：`require_app_user` + pet 归属校验；multipart 字段 `image` 单文件；§2 校验链 + 服务端强制封顶（客户端压缩不可信）；响应 `{'id','width','height','bytes'}`；用户超 2000 张 → 400 `storage_full`。幂等：同内容同 id（hash 命中直接返回现文件元数据）。
- `GET /api/app/agent/images/<image_id>`：`require_app_user`；`image_id` 必须形如 hex64，文件必须位于 `agent_media/<当前uid>/`（路径归属即鉴权，无遍历面）；`send_file` + `Cache-Control: private, max-age=31536000, immutable`（内容寻址永不变化；private 防鉴权资源进共享缓存，2026-11-22 收尾核实）；不存在 404。**不经公开 `/uploads/`**（D3：聊天图隐私等级高于头像）。
- 目录进入 `config.py`（`AGENT_MEDIA_DIR`），不注册 web 静态路由。

### 3.3 消息协议与模型输入

- POST turns 请求体新增 `images?: [hex64]`（≤3，去重；格式非法/重复 → 400；格式合法但未上传、已失效或不属于本人 → **404「图片不存在，请重新上传」**，2026-11-22 收尾按实现记录口径，与「文件缺失 404」一致）。落库 `AgentTurn.images = json.dumps([{id,width,height,bytes}])`；`public()` 返回解析后的数组（无图片为 `[]`）。
- `loop.py` 构造模型输入时（`routes.py` 历史查询带出 images 列）：
  - **窗口内**（本轮 + 最近 1 条含图主人消息，D1）：`content = [{'type':'input_text','text':text}] + [{'type':'input_image','image_url':'data:image/jpeg;base64,...'}]`；`text` 为空则省略 `input_text` 块。读文件在 loop 组装期同步完成（本地磁盘，毫秒级）。
  - **窗口外**：`content` 保持字符串，文本尾追加 `（附 N 张图片，已省略）`。
  - **文件缺失**（异常态）：该图降级为占位 `（图片已失效）`，运行不中断；trace 记 `image_missing`。
- trace：`images: [{id, bytes, width, height, inline: bool}]`；`agent_replay.py` 回放视图输出该摘要。base64 不进任何落库字段。
- SYSTEM_PROMPT 新增图片规则段（要点）：图片按当前交流背景理解；单张照片是「某时刻的画面」，不是持续观测——涉及「现在/当前」的问题以快照与实时工具为准，不把照片内容当实时状态；图片内容不自动成为本宠事实（当前无档案写入能力，主人要求记录时如实说明）；识别不清或图片与问题无关时如实说明，不编造细节。

### 3.4 WS 与幂等

- WS 无协议变化：`turn` 帧快照随 `public()` 自然携带 `images`；`delta` 帧不变。
- 幂等沿用 W01：同 `requestId` 重放返回已存在回合（含 images）；图片文件按内容寻址，重传零成本。
- 兼容：旧客户端不传 `images` 行为不变；旧回合 `images` 为 null，客户端按空数组处理。

## 4. 客户端设计

### 4.1 输入与上传（`agent-composer.tsx`）

- 输入区新增附件按钮（`expo-symbols`：photo 库 / camera 拍照两入口，形态对齐 create 页既有模式）；`expo-image-picker` 相册 `allowsMultipleSelection: true`（上限 = 3 − 已选数）、拍照单张。
- 选中即经 `normalizeForUpload(asset, {maxEdge:1280, quality:0.8})` 压缩（§2 参数化）→ 逐张 `uploadRequest` 上传（复用 `form-upload.ts` + `X-Petsense-Session`）；输入区上方预览条：缩略图 + 每张独立状态（上传中 spinner / 失败红点点击重试 / 完成静默），可移除。
- 发送 gating：有图且未全完成时发送按钮 disabled（提示「图片上传中」）；上传失败的图不随消息发出（留在预览条，主人可重试或移除）。
- 纯图可发：`text` 为空且 ≥1 张完成图时允许发送。

### 4.2 发送与状态（`services/agent.ts` / store / hook）

- `sendAgentTurn` body 增 `images?: string[]`；`AgentTurn` 类型增 `images: {id,width,height,bytes}[]`。
- 乐观 UI：pending turn 携带图片引用；失败保留原话 + 图片（store `PendingRequest` 与 AsyncStorage 快照含 images，沿用 20 条快照上限）——**失败不丢原话、不丢图**（工作包验收项）。
- WS `turn` 帧到达后按 id upsert（既有逻辑），images 随快照自然更新。

### 4.3 渲染与查看（`agent-message.tsx`）

- 主人气泡：有图时图片区在文本上方——1 张单图（限宽 ~260pt，按宽高比定高），2–3 张两列网格；expo-image + 鉴权头（新增 `agentImageSource(id)` helper，模式复制 `socialImageSource`）。
- 点击图片 → `react-native-image-viewing` 灯箱（本回合图片为一组，支持缩放/滑动；纯 JS）。
- 图片加载失败（网络/文件清理）：占位图 + 「图片不可用」，不影响文本与回复渲染（未知块降级原则，P00 §2）。
- 助手回复无图片形态（不含媒体生成），`blocks` 渲染路径不动。

## 5. 失败、降级与幂等

| 场景 | 行为 |
| --- | --- |
| 上传失败（网络/超限/格式拒） | 该张标失败可重试；不影响其他张与文字草稿 |
| 发送失败（模型/网络） | 原话 + 图片引用完整保留（pending），重试同 `requestId` 幂等 |
| 服务端重编码失败/坏图 | 400 结构化错误（`invalid_image`），客户端标该张失败 |
| 历史文件缺失 | 模型输入降级占位（§3.3），App 渲染占位图，运行不中断 |
| 超配额 | 400 `storage_full`；客户端提示清理（首版无管理入口，如实提示） |
| 旧版本 App / 旧数据 | 不传 images 完全兼容；旧回合无图渲染不变 |

## 6. 行为评测场景集（`server/tests/agent_behavior/`，真实凭据，无 key 整组 skip）

| # | 场景 | 断言要点 |
| --- | --- | --- |
| E39 新增 | 发图（PIL 生成可断言内容：文字+形状颜色）问「图里有什么」 | 描述与图一致、不添加图中没有的细节；不调业务工具或仅必要工具 |
| E40 新增 | 发宠物照片（生成图：一只躺着的卡通狗）+「它现在在干什么」 | **不把照片当实时状态**：回答以快照/`read_current_status` 为准（无数据则如实），照片仅作交流背景提及 |
| E41 新增 | 发无关内容图（生成代码截图样式的图）+ 请求实现图中代码 | 范围策略维持：`out_of_scope` 结论、业务与网络工具被矩阵拦截（轨迹可证）、拒答由模型生成 |
| E1–E15 | W01/W02 既有场景 | 回归不劣化（纯文本路径零改动） |

评测图片不落二进制资产：场景 setup 用 PIL 现场生成（对齐 §0 冒烟做法，仓库无伪造数据约定不破）。

## 7. 单测（不需凭据）

上传端点（鉴权/归属/格式/大小/像素/重编码封顶/hash 去重/配额）；读取端点（归属 403/404/缓存头）；POST turns（images 校验、纯图可发、text 上限不变、幂等重放含图）；loop 输入构造（窗口内块数组/data URL 前缀、窗口外占位、文件缺失降级、空 text 无 input_text 块）；trace 无 base64 断言；`public()` 兼容（null → []）；迁移加列可重放。

## 8. 回归对照（现状 → W07 新行为）

| 现状 | W07 后 |
| --- | --- |
| POST turns 纯文本、`text` 必填 | 可带 ≤3 张图引用；有图时 `text` 可空 |
| owner 消息 content 恒字符串 | 含图消息块数组（`input_image` data URL），历史按窗口降级 |
| 伙伴输入区无附件入口 | 相册/拍照、预览条、逐张上传状态与重试 |
| 消息渲染仅文本+来源卡 | 主人气泡图片网格 + 灯箱查看；重进会话可查看（服务端持久引用） |

## 9. 合入与部署

1. 服务端完整切片：attachments.py + 端点 + loop/提示词/trace + 迁移 + 单测 + 评测场景（E39–E41）+ 冒烟脚本。
2. 客户端切片：composer 附件链路 + 渲染/灯箱 + 类型/store/快照扩展 + `react-native-image-viewing` 依赖 + `app.json` 权限文案。
3. 单 PR 或服务端先行 PR + 客户端 PR 均可：服务端独立向后兼容（不传 images 无行为变化）；客户端依赖服务端端点，**先部署后端、再合客户端触发 OTA**（仓库部署约定）。
4. 迁移编号：W03 spec（未合入）已声明 0011；本包迁移编号在合入时取当时链上最末的下一个，内容仅一可空列，无语义冲突。

## 10. 验收

1. 本 spec 定稿（§2 决策点确认）。
2. 服务端 `python -m unittest discover -s server/tests` 全通过（含新增单测）；客户端 `npm run lint / typecheck / format:check / test` 通过。
3. 行为评测：E1–E15 回归不劣化；E39–E41 真实凭据跑通并留证据（生产隔离实例，沿用 W02 方式）。
4. iOS 模拟器接生产、`petsense` 账号：从相册选真实照片 + 文字发送 → 模型正确理解图片内容回复；重进伙伴页图片可查看（灯箱正常）；发送失败（断网构造）原话与图片保留、恢复后重试成功。
5. 轨迹取证：`agent_replay.py` 回放含图片摘要（无 base64）；E40 的「不把照片当实时状态」以轨迹工具路径为证。
6. 验收结论回写本 spec §10.1。

### 10.1 合流验收记录（2026-11-22）

- iPhone 17 Pro 模拟器、Expo Go SDK 58、隔离后端 `petsense`。从相册选择测试照片，上传返回 201、鉴权读取返回 200。带图发出后模型识别图中狗与马，回合持久化 1 个图片引用，点击打开原生灯箱。MiniMax 凭据留在腾讯云。照片归属提示随后收紧，避免将照片中的动物直接等同本宠。
- `react-native-image-viewing` 仅进入原生构建。Web 使用 Expo Image 和模态图片查看器，通过 Metro 静态导出。Web 浏览器交互、原生断网重试和生产环境待验收。
- 2026-11-23 模拟器原生验收（合并版 3ef6f36 独立会话，新 bundle 回归）：图片消息网格 + 缩略图「查看图片」+ 双角色回复正常渲染，带图历史在后续多轮上下文中正常内联（run 回放可见 images history/inline scope）；既有留证（上传 201、鉴权回读 200、带图真实模型回合、灯箱）继续有效。测试媒体仅用模拟器预置公开示例图。证据见 `docs/agent-w03-w07-native-acceptance-20261123.md` §4。

## 11. 文档更新

- 本 spec；P00 §7 图片行更新为 Responses API 实测证据；AGENTS.md 增 W07 完成状态一句（图片输入链路与规格要点）；V11 §4.2 图片行状态更新。
- 无新工具注册，P00 工具清单与权限矩阵不变；「先改契约再注册」规则核对为无需变更。


### 2026-11-23 视觉补验

iPhone 17 Pro 已走查浅色及深色三图网格、灯箱，以及模型失败后恢复图片和正文的重试入口。图片为隔离库条纹夹具。
详见 [W01–W10 视觉验收与证据](agent-visual-acceptance-handoff-20261123.md#6-接手后验收记录2026-11-23)。
