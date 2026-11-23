# PetSense App

宠物健康管理项目：`app/`（React Native + Expo 客户端）、`server/`（Python 服务端）、`firmware/` 和 `tools/offline-map/`。

## 仓库速览

- `app/`：Expo SDK 58、React Native 0.88、TypeScript（strict）、expo-router、Reanimated、react-native-svg。所有 npm 命令在 `app/` 下执行
- `server/`：Python 3.12 Flask。代码在 `server/petsense/` 包（入口 `wsgi.py`），按域分包：`telemetry/`（MQTT ingest、WS/SSE 分发、音频重组、设备控制）、`actions/`（动作识别）、`collection/`（数据采集 API）、`experiments/`（实验录制）、`auth/`、`pets/`、`devices/`（项圈认领/解绑）、`social/`、`petgif/`、`trends/`、`web/`，另含 `db/`（数据库会话与 ORM 模型）
- 运行时数据（DB、uploads、cloud_data 等）默认在服务端安装根目录（包上一级），可用 `PETSENSE_ROOT` 覆盖；本地在 `server/` 下运行 `PETSENSE_MQTT_DISABLED=1 python3 wsgi.py`
- 部署：push main 触发 CI；`app/` 变更运行格式、lint、类型、Web 构建和客户端测试，main 直推通过后发布 EAS Update OTA；`server/`、`tools/offline-map/` 或 `ci.yml` 变更运行服务端和离线地图测试、模型校验，通过后部署腾讯云。手动 Run workflow 可重部署；固件与离线地图另有独立 workflow。具体触发条件以 `.github/workflows/` 为准

## 客户端要点

- 页面在 `app/src/app/`（`(tabs)` 含今日、位置、伙伴、社区、我的，记录为根 Stack `/records`；另有 login/create/user/monitor/setup 等页面；setup 为首次引导与设备配对页），实时监测组件在 `app/src/components/live/`，WebSocket 遥测客户端在 `app/src/services/telemetry-service.ts`，实时数据 Hook 在 `app/src/hooks/use-telemetry.ts`，帧类型在 `app/src/types/telemetry.ts`，主题在 `app/src/constants/theme.ts`，回放数据集在 `app/src/data/replay-dataset.ts`
- 实时链路：设备 → MQTT（腾讯云）→ server（按设备归属过滤）→ WebSocket `/ws?token=`（需登录，服务端只推当前用户名下设备的帧；未登录返回 `unauthenticated` 状态）→ `telemetry-service.ts`（自动重连）→ `useTelemetry()`；连接地址在 `app/src/config.ts`，可用 `EXPO_PUBLIC_TELEMETRY_URL` 覆盖；设备归属在 devices 表（一人一设备，认领即绑定宠物，`/api/app/device/*`）
- 遥测双模式：`EXPO_PUBLIC_TELEMETRY_MODE=replay npm run start:replay` 离线循环回放真实实验数据集（由 `app/tools/gen-replay-dataset.cjs` 从云端 cloud_data 生成）
- 路径别名：`@/*` → `app/src/*`，`@/assets/*` → `app/assets/*`
- 品牌色：珊瑚橙（light `#E8835C` / dark `#F0987A`），绿色仅作在线/正常语义色（light `#31A66D` / dark `#4CC38A`）；全部颜色定义在 `app/src/constants/theme.ts`，不要在页面里写死颜色
- 位置页用显式的「室内 / 室外」分段控件切换场景，选择本地持久化（默认室内），绝不随遥测自动切换；两路坐标独立、不混用。室外为项圈 GNSS GPS（经鉴权遥测传输，失效后不展示为当前位置，原始坐标收进「坐标详情」）并提供「遛狗地图」入口（见 `docs/DOG_MAP.md`）；室内为已标定 UWB 地图，失联后保留标注时间的最后位置，不绘制实时轨迹。UWB 标定在专属路由 `/uwb-setup`（三步流程、服务端会话恢复不变），发布后位置页回到前台即刷新。地图、坐标系和原生构建要求见 `docs/GPS_LOCATION.md`，固件及实机验收见 `firmware/GNSS.md`
- 实时室内地图只绘制当前位置圆点，不显示历史轨迹；轨迹仅在标定/对齐预览中显示。调整地图朝向必须同时更新底图、轮廓、基站和定位变换，禁止只旋转图片。

## 遛狗地图

- 「位置」页室外视图的「遛狗地图」打开独立路由 `/dog-map`：本地打包的广州南沙路段评分快照（663 点，版本随数据走），与项圈和实时遥测无关，未绑定/无 GPS 也可用。地图/列表双视图，低缩放聚合、点选出详情，离开覆盖范围可一键复位；底图失败时列表仍可用。
- 数据由 `app/tools/gen-dog-map.cjs` 从上游公开页面（CityScene 南沙演示地图）可重复生成：源为 GCJ-02（高德瓦片 + `coordtype=gcj02ll`），导入时转 WGS-84 近似值；评分与等级原样保留不重算。快照抓取 2026-11-20，原始采集日期未知。来源、坐标验证、更新流程见 `docs/DOG_MAP.md`；更新后重跑 `npm test`（完整性断言固定抽样点）。

## 开发约定

- 提交前必须通过：`npm run lint`、`npm run typecheck`、`npm run format:check`、`npm test`（在 `app/` 下；CI 会拦截）；服务端改动跑 `python -m unittest discover -s server/tests`
- 图标：用 `expo-symbols` 的 `SymbolView`，`name` 传 `{ ios, android, web }` 三端映射；图标名必须是合法的 SF Symbol / Material 名称，类型检查会拦截非法名字
- 不做纯前端假数据页面：所有展示必须有真实数据源（云端遥测或 `app/src/data/` 的真实实验数据集）
- 动画：优先用 Reanimated（React Compiler 已开启，RN Animated 的 ref 写法会被 lint 拦截）
- 遥测帧解析只改 `app/src/services/telemetry-service.ts`，`health.valid === false` 的帧不进时间序列
- 服务端按域加模块：新路由放进对应域的包，新域先建子包再在 `petsense/app.py` 的 `create_app()` 里注册 blueprint

## 项目记忆维护

- 本文件是跨协作者共享的项目入口；开始工作先检查 Git 状态和适用文档，涉及远端时核对远端，不覆盖他人的未提交工作。
- 每次完成涉及架构、关键路径、接口与数据流、运行或测试命令、部署、设备限制或重要工作约定的变更时，检查并在同一变更中按需更新本文件及相关专题文档；无相关变化时无需为更新而更新。PR 描述或交付说明写明已更新的文档，或说明无需更新的原因。
- 本文件保留稳定规则、关键限制和专题文档入口；详细设计和操作步骤放对应文档。与代码、CI 或实际验证冲突时，以当前实现及验证证据为准，修正文档并注明未验证之处。动态状态注明核对日期和依据，过时内容及时删除或修订，不把推测和模拟结果写成硬件验收结论。
- 共享记忆须纳入 Git 并推送，未提交内容不会随仓库共享；不得写入密码、令牌或私人数据（唯一例外：模拟器验收测试账号 `petsense` 的密码，2026-11-22 负责人确认入库）。不要把聊天记录当作其他协作者自动拥有的项目记忆。

## 定位项目状态（2026-11-12 按仓库代码和文档核对）

- 项目还包括 `firmware/` 和 `tools/offline-map/`。UWB 定位代码集中在 `server/petsense/uwb/`；设备测距经 MQTT 入站，服务端按设备归属、Tag 和标定地图解算并通过鉴权接口及 WebSocket 提供位置，App 位置页消费服务端结果。地图版本变化会触发刷新；失联时保留标识明确的最后位置。
- 触碰标定（2026-11-17 调整）：每基站采集 20 秒、至少 8 个不同有效帧；中间 80% 测距跨度和双向中值差上限均为 80 cm，贴近距离上限 50 cm。保留逐路中值、帧新鲜度/去重和非共线检查；当前 edge 约 1 Hz 上传，通过延长采集增加样本，不改变固件频率。参数通过 setup 响应的 `touchConfig` 提供，App 以服务端倒计时为准，见 `docs/uwb-guided-setup.md`。
- 对齐默认栅格算法使用有效原始轨迹；多边形诊断单独清洗，清洗不足 10 点只跳过该诊断，不能阻断栅格预览。预览不达质量门槛仍禁止发布。App 在对齐按钮旁显示提交、处理中及结果/错误；详细日志保留在任务目录，不能将原始 traceback 直接暴露给客户端。
- 远端 main 已合入 edge 固件 BU03 测距上传、三基站触碰标定，以及绕边采集、点云对齐预览与确认发布。操作和限制分别见 `docs/uwb-guided-setup.md`、`docs/uwb-walk-alignment.md`；自动对齐候选仍需人工预览确认，地面参数需人工确认，当前触碰标定假定同一测距平面。采集和任务锁基于单进程，扩展多进程前需改为共享协调。
- 离线建图工具及其依赖与在线定位分工见 `tools/offline-map/README.md` 和 `docs/UWB_INTEGRATION.md`。真实设备、实地定位误差及原生端表现需按实际验收结果判断；历史文档中的模拟数据或浏览器测试不能证明硬件闭环已经完成。
- A7670E GNSS 通过现有 4G 遥测上传，位置页与室内 UWB 并列显示；真实项圈定位和原生端仍需实机验收，见 `firmware/GNSS.md` 和 `docs/GPS_LOCATION.md`。

## 模拟器 UI 端到端验证（computer use 实战经验）

- 启动：`EXPO_PUBLIC_API_BASE_URL=http://<目标服务> nohup npx expo start --ios --port 8082`（8081 常被用户自己的 Metro 占用，勿动）。`EXPO_PUBLIC_*` 是 Metro 打包时内联的，改了必须让 App 重新拉包
- **Expo Go/dev client 对同一 URL 去重**：`simctl openurl` 只会把已运行的 App 带回前台，内存里还是旧 bundle——表现为"请求全失败/改动不生效"。必须先 `xcrun simctl terminate booted host.exp.Exponent`（或 `com.yangeh03.petsense`）再 openurl，**Metro 日志出现 `iOS Bundled` 才算新包生效**
- 驱动 App 优先用 computer use 的无障碍树：Simulator 窗口里能看到 App 内元素（中文标题按索引点击）；文本框用 `setValue` 直接设值——`typeText`/键盘事件进不了 iOS 输入框
- 系统相册（PHPicker）和裁剪页是远端视图：AX 树看不到，且 CUA 截图帧持续失效（点击报 frame no longer maps）。用 `cliclick`（需 `brew install cliclick`）发真实鼠标事件；多显示器时先把模拟器窗口挪到主屏（`osascript` 设 position，副屏的 AX 坐标空间不可靠），点击点 = 窗口原点 + CUA 截图像素坐标（1:1）。System Events 的 `click at` 对模拟器内容无效
- 判定界面状态用 `xcrun simctl io booted screenshot`（直接截设备屏，不受窗口位置/遮挡影响）；定位按钮可用 PIL 按品牌色扫描像素再除以 3 换算逻辑 pt
- 判定请求是否真正到达服务端：`cloud-ssh tencent 'sudo journalctl -u petsense.service --since "2 min ago"'` 看每条请求的状态码，这是"客户端没发出去"还是"服务端报错"的铁证
- 生产测试账号关联真实宠物资料，不要修改；凭据通过安全渠道获取，不写入仓库。纯接口测试在 `server/` 下用 `PETSENSE_ROOT=/tmp/xxx PETSENSE_MQTT_DISABLED=1 .venv/bin/python wsgi.py` 起隔离实例（临时 root 会自动 alembic 建库），测完清理
- 模拟器验收统一使用指定账号 `petsense`，密码 `123456`（2026-11-22 负责人确认写入仓库，是「不得写入密码」约定的唯一例外，仅限此测试账号；模型 API key 等真实凭据仍不入库）。可直接在生产 `petsense` 的伙伴会话发送 Agent 验收消息，无需逐条或逐轮批准。需要创建宠物、改动真实资料、绑定设备或验证其他写入时，使用隔离后端的同名账号。
- 涉及历史记录、观测问答和学习累计的验收，优先用指定账号从云端读取真实观测。先检查日期范围及有效覆盖，再挑选有数据的时段。新接口尚未部署时，可用授权的云端只读数据副本在隔离实例验收；本地空库用于缺数据、写入和故障场景，空库通过只算相应场景通过。验收记录须注明数据来源、时段和实际经过的接口。
- Metro 已被占用时使用空闲端口，先检查 `/status`。`--localhost` 可能只监听 IPv6 `::1`，此时 Expo Go 用 `exp://localhost:<端口>`，不要使用 IPv4 `127.0.0.1` 打开 Metro。App 的 API 地址按隔离后端实际监听地址配置。
- `idb` 若找不到默认的 `idb_companion`，连接时通过 `--companion-path` 指向可用二进制。默认 HID 报 `Mach port invalid` 时，使用 `idb ui tap/set-value --api ax-coresimulator`，从无障碍树取元素坐标。每次导航后重新读树，避开过场中同时出现的两页元素。
- iOS 原生标签栏和透明 Stack 标题栏会覆盖内容。固定输入区在键盘收起时要留出标签栏空间，`KeyboardAvoidingView` 的 `padding` 模式会覆盖自身的底部 padding，留白应设在输入区外层。记录来源区放入页面安全布局，并在模拟器截图检查遮挡。
- Expo Router 58 的 iOS NativeTabs 为伙伴页提供的底部安全区已经反映标签栏位置。固定输入区使用该值一次，避免叠加 `BottomTabInset`。iPhone 的标签栏在底部，iPad 的标签栏可在顶部，验收时分别检查。

# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v58.0.0/ before writing any code.

## Agent 开发入口

- 产品设计 V11（2026-11-22 起，以 Agent 为设计对象：单一 Agent 统一运行循环、无前置路由、常驻实时快照、工具注册表 + 权限矩阵、记忆分级写入；前端架构与组件选型见其第 18 章：WS 直连 + zustand + 自建渲染层与容器（expo-ai-elements spike no-go，见 W01）+ keyboard-controller）见 `docs/agent-product-integration-design.md`；实施按 W 系列工作包（W00–W15）见 `docs/agent-implementation-work-packages.md`，路线与分工见 `docs/agent-implementation-roadmap.md`，跨端契约见 `docs/agent-p00-contract.md`。阶段 1 实施记录见 `docs/agent-phase-one.md`（历史记录，其 decide 路由架构已被 V11 替代，由 W01 删除）。
- 新 Agent 服务端域在 `server/petsense/agent/`，App 使用独立业务服务，不混入遥测解析。模型凭据仅由服务端环境或外部文件读取，不纳入 Git。
- 工作包开发流程（2026-11-22 确立，W 系列一律按此执行）：① 先写 spec 文档（`docs/agent-w<NN>-spec.md`），关键决策点与负责人讨论确认后定稿；② 按定稿 spec 实现代码；③ 验收统一用 iOS 模拟器接生产环境，登录测试账号 `petsense`（密码 `123456`，负责人确认随仓库记录）。spec 定稿前不动代码，验收结论回写 spec。
- W00 已完成（2026-11-22）：spec 与现状处置基线见 `docs/agent-w00-spec.md`。关键外部结论：MiniMax 内建 `web_search` **可用**，但只在 **Responses API（`/v1/responses`）与 Anthropic Messages API** 上（生产域 `api.minimaxi.com` 实测通过、Beta）；**Chat Completions 端点不支持**（`web_search` 参数被忽略）。URL 直接阅读不可依赖，`read_web_page` 自建 fetcher。实测脚本 `server/tools/minimax_search_smoke.py` 可复跑。
- W01 已完成（2026-11-22，spec 与验收记录见 `docs/agent-w01-spec.md`）：`model.decide()` 与 intent 固定文案分支已删除，全部对话跑统一运行循环（`agent/loop.py`）；主对话已迁 **MiniMax Responses API**（全量回显会话，工具结果必须放 `function_call_output.output` 字段；`previous_response_id` 400 不可用）；工具注册表 + 权限矩阵在 `agent/registry.py`（新增工具先改 P00 契约再注册，矩阵先于执行校验，`out_of_scope`/无 stance 声明时业务工具被拒并入轨迹）；运行轨迹落 `agent_runs` 表，回放用 `python3 server/tools/agent_replay.py <run_id>`；App 读取走 WS 直连 `/api/app/agent/ws?token=`（断线降级轮询，HAProxy TCP 透传无需代理配置）。客户端已迁移 zustand + `services/agent-channel.ts` + `hooks/use-agent-chat.ts`（AsyncStorage 快照恢复）；expo-ai-elements spike 判 no-go（npm 包为空壳且深度依赖与仓库工具链冲突），渲染层按回退方案自建，流式 markdown 待后续迭代。行为评测：`cd server && MINIMAX_API_KEY(_FILE)=… python3 -m unittest discover -s tests -p test_agent_behavior.py`（无凭据自动 skip）。已知边界：模型空响应（reasoning 耗尽输出预算）循环内放宽 token 上限重试；stance 拒绝后模型须重发工具调用（提示词已强化，轨迹可查）。
- W02 已完成（2026-11-22，spec 与验收记录见 `docs/agent-w02-spec.md` §9.1–§9.3，含独立复验与修复闭环）：**氛围快照**（`agent/snapshot.py`：档案 + 环境一行[动作/在线/电量/数据年龄] + 学习一行 + 时间，≤600 字符、固定裁剪顺序）在 routes POST 内组装并注入系统提示；明细走 `read_current_status` 工具（device/today/location/actions/sounds 五节；today 与 `query_observations` 同口径，均复用 `observations.query`；位置状态与坐标只在工具结果；`valid=false` 帧降级 `stale_invalid` 不给占位数值）。快照随 `AgentRun.snapshot` 落库（alembic 0010），`agent_replay.py` 回放可见——数值溯源以轨迹为证。设计要点（负责人 2026-11-22 澄清）：**不强制零工具作答**，直答还是现查由模型判断（判断归模型）；E2/E5 维持 W01 校准。iOS 端到端已补验（模拟器 AX 设值发送 + 收回复）；独立复验暴露的三项问题已修复闭环（`f5b61f9`/`8d44f11`）：被拒调用后禁止自称已查（loop 一次性系统提醒机制，防聊天历史数字转述）、模糊输入澄清问句稳定化；行为评测最终 **E1–E15 15/15**。待真项圈在线时段实机核对（电池耗尽离线中）。工具执行器注意：`session_scope` 为线程级共享会话，`latest_telemetry`/`uwb service` 等自开会话的能力必须在自身 `session_scope` 之外调用，否则嵌套关闭外层事务。
- W03 已完成（2026-11-23 集成核验，spec 与验收见 `docs/agent-w03-spec.md`）：**档案确认协议与助手设定**——`prepare_profile_update` 双路径（明确指令 explicit 直写回显 / 模糊信息生成待确认候选卡，未经确认不得自称已保存），确认/拒绝决策端点幂等（409 带当前提案与回合快照，App 翻终态禁按钮）；被替代旧卡所在回合重写 blocks 并推帧刷新（历史卡「已有新候选」）；`read_pet_profile`（生日带精度与推导年龄）与 `prepare_role_settings` 直写、`/api/app/agent/assistant-settings`、App 设定页 + `proposal-card` + 聊天头像进档案/设定页。alembic 0013。审计修复（3c3191c）：过期候选卡翻态、decision 409 状态、设定页安全；E6 旧运行严格措辞假阴性已修（通用肯定语 + 精确生日回显断言），2026-11-22 云端真实模型子集复跑通过。合并版模拟器原生回归通过（确认卡渲染与入口，见 `docs/agent-w03-w07-native-acceptance-20261123.md` §4）。
- W04 已完成（2026-11-23 集成核验，spec 与验收记录见 `docs/agent-w04-spec.md` §9.1）：**长期记忆**——`PetMemory` 存储 + `search_memories`/`write_memory`（episode/owner_event/habit_candidate/session_takeaway 白名单）/`forget_memory`（retire 保留可审计），FTS5 中文召回；认识卡蒸馏、日/周汇总、markdown 报告导出（`agent-memory/pet-<id>/memory.md`），alembic 0012。泄露控制：忘掉/纠正后认识卡暂停展示并强制重建（不回喂旧文）、候选全失效确定性退役；2026-11-23 集成核查发现日汇总文字可经检索/报告复述被遗忘内容，已推广污染控制（`memory.polluted_active_ids`：检索排除 + 报告占位，文字不回溯改写、历史章节可审计），见其 §9.1 第 3 条。原生验收两缺陷已修（2026-11-23，见其 §9.2）：**虚假写入回执兜底**（最终回复声称已记但本运行无任何 write_memory 保存成功且发起过写入调用时，一次性系统提醒强制补写或如实说明；覆盖无执行与结构化未写入两种形态，无写入调用的合规回显只审计不拦截）；**supersede 目标须与新条目同 kind**（认识卡等系统条目不可经写入替换，蒸馏 worker 同类重建不受影响）。
- W05 已实现（2026-11-22，spec 定稿见 `docs/agent-w05-spec.md`，D1–D4 均按推荐项：绝对值+覆盖门槛 / p10–p90+中位数 / 全证据+核验工具 / 惰性重算；实施记录见其 §9.1，合入待办）：**个人基线**（`agent/baseline.py`：维度×粗时段[夜间/上午/下午/晚间]把 `agent_learning_days` 有效日物化为 `pet_baselines` 参照范围，alembic 0011；窗口最近 28 个有效日、水位变化才重算、幂等可复算；时段样本需覆盖 ≥60 分钟、当天该时段覆盖不足不可比且不得当作活动减少；明细过 90 天保留期的有效日计进度不入样本；版本切换即新键重学、活跃版本=最新 lastObservedAt）。`read_baseline` 工具（FACT_READ/DIALOGUE，认识中不给范围）与 `GET /api/app/agent/pets/<id>/baseline`；快照学习行/observations/认识中卡共用 `baseline.dimension_states()` 真实就绪态（「动作 已就绪、声音 n/7 天」）。回填核验工具 `server/tools/agent_baseline_rebuild.py`（只读比对，分钟级记账近似、单列无版本标注数据；时间解析须显式按 UTC）。行为评测新增 E22–E26（编号随全仓场景集：E16–E21 为 W03 占用；认识中不得说「比平时」、就绪引用工具结果、覆盖不足如实、范围引用、分维度独立）；行为评测 E22–E26 已随集成终验全过（2026-11-23，见 `docs/agent-w05-spec.md` §9.3）；隔离合成种子的模拟器原生验收已过（快照学习行/就绪引用范围逐字吻合/覆盖不足不可比/分维度独立，同日），生产侧待真项圈在线累计后抽查（生产账号现仅 1 动作有效日）。
- W07 已完成（2026-11-23 集成核验，spec 与验收记录见 `docs/agent-w07-spec.md` §10.1）：**聊天图片**——`POST /api/app/agent/pets/<id>/images`（校验次序先格式后大小、按用户子目录隔离、鉴权读取 `GET /api/app/agent/images/<id>`），owner 消息本轮与最近 1 条带图历史内联 `input_image`（更早降级文字占位，base64 不落存储），提示词「照片不是实时状态」约束；App 压缩上传 + 图片消息网格 + 灯箱（原生/Web 分离）。iOS 相册上传 201/鉴权回读 200/带图真实模型回合已留证；合并版原生回归通过（图片网格/灯箱/带图历史内联）。
- W06 已实现并合入集成（2026-11-23 集成合入 3895ec1 + E35/E36 真修复 fb58f50，spec 与验收见 `docs/agent-w06-spec.md` §10.1–§10.2）：**联网知识**——内建 `web_search` 由 loop 按轮挂载到 Responses 请求（与 function 混排；执行在 MiniMax 侧，无法先于执行拦截，P00 §3 已记录例外与缓解），每运行累计 2 次后摘除（degraded `search_budget_detached`；越权残留 `search_on_out_of_scope` 审计），挂搜索的请求读超时 110s、RUN_WALL_LIMIT_S 90→120；`read_web_page`（`agent/fetch.py`，EXTERNAL_READ 组、工具超时 20s、每运行 3 次上限）为自建 fetcher：**pin-IP SSRF 防护**（解析后逐 IP `ipaddress.is_global` 校验、连接固定校验过的公网 IP、TLS 仍按原域名校验、重定向逐跳复检、拒绝代理隧道与 userinfo/非 80/443 端口、2MB 流式截断）+ trafilatura 正文提取（≤4000 字符）；回合 `sources` 增 web 来源（url 去重、上限 6、`checkedAt`），App `WebSourceCard` 点击系统浏览器打开、未知来源类型安全降级；依赖 `trafilatura==2.2.0`（requirements.lock 已同步）。灰度：`PETSENSE_SEARCH_DISABLED=1` 关闭（生产 drop-in 起步，测试/隔离实例默认开启），关闭时不挂搜索、`read_web_page` 不进 schemas、提示词回退「暂未开放」。复跑实测脚本 `server/tools/minimax_w06_smoke.py`。E35/E36 修复（d89ddb4）：流式 citations 捕获、深读页面入轨迹、搜索计时校准 + 搜索结果跨轮系统提醒回带（无状态全量回显下结果不随轮返回的归因修复）；隔离实例真实模型 E7/E35–E38 5/5、E35/E36 各三轮稳定通过（证据见其 §10.2）。集成终验 41/41（含本包五场景，E35/E36 三轮稳定，见 `docs/agent-w06-spec.md` §10.3）；生产 drop-in 起步关闭。§8 模拟器验收已过（2026-11-23 原生会话：来源卡/系统浏览器/常识不搜索/越权拒答，旧 bundle 兼容按 §6.3 设计移交；记录收编 `docs/agent-w03-w07-native-acceptance-20261123.md`），生产灰度核对确认默认关闭（50-search-rollout.conf drop-in 在服务有效环境中）。待办：OTA 后灰度放行评估与 D6 计费核对。
- W08 已实现（2026-11-23，spec 见 `docs/agent-w08-spec.md`）：**委托与后台执行**——`agent/tasks.py`（委托 `AgentTask` + 追加式计划版本 + 交付 `AgentTaskDelivery`，pet 内 `task_key` 唯一）、`agent/jobs.py`（通用持久作业队列：dedup_key 唯一、租约、有限重试、认领续期）、`agent/checkpoint.py`（检查点评估：窗口物化、跳过路径与降级回信）、`agent/deliver.py`（后台交付走同一 Agent 消息通道，稳定 request key 去重）；`read_task` 只读工具与 `propose_task_change` 候选工具（仅对话形态）、`GET /api/app/agent/pets/<id>/tasks|tasks/<id>|tasks/<id>/ops` 与 `POST .../task-proposals`；App 委托簿页 `agent-tasks.tsx`、任务提案卡与任务结果卡。检查点与回顾共用 `loop.run_structured`（V11 单一运行循环，W08/W09 不另起模型循环），alembic 0015（作业队列）与 0016（委托域）。2026-11-23 合并到 W10 分支后行为评测 E42–E50 真实模型复跑通过（见 `docs/agent-w10-spec.md` §7.4）。
- W09 已实现（2026-11-23，spec 与实施记录见 `docs/agent-w09-spec.md` §5）：**周期回顾与硬件事件**——`agent/care.py`（主题 `AgentCareTopic` 按实例持久化、硬件规则状态机挂 `ingest_observation` 接受帧后同事务推进、失联扫描需连接稳定 ≥30s、换绑 rebind 关闭、学习转换首扫只初始化水位、回顾窗口按宠物时区只取最新受影响窗口）+ `agent/care_jobs.py`（W08 队列上 `care_scan`/`care_review`/`care_event` 三 kind；交付走 W08 后台消息边界，稳定 request key 去重）+ `loop.run_background`（review/event 后台形态：事实工具子集、无搜索无写入、read_baseline 不开放）+ `read_care_topics` 工具与 `GET /api/app/agent/pets/<id>/care/rules|topics` 端点；客户端 `services/care-rules.ts` 抑制三类本地重复告警、聊天气泡 care 卡 +「为什么提醒我」追问。启动 backfill 重放历史帧只重建水位不补发历史事件。alembic 0017（接 W08 的 0016）。2026-11-23 合并到 W10 分支后行为评测 E51–E57 真实模型复跑通过；已知边界：安静档期间打开的设备事实不因调档补发（同一事件按去重规则只发一次，见 W09 spec §5.3）。
- W10 已实现（2026-11-23，spec 与本轮验证记录见 `docs/agent-w10-spec.md` §6）：**Agent 动作工具**——`server/petsense/agent/actions.py` 落地三个动作（远程录音 `control_device`、运动日记 `create_diary`、私有社区草稿 `draft_social_post`），共用 `agent_actions` 表的稳定动作 ID 与结构化回执；录音与日记走 W03 确认卡（确认后由 `routes.proposal_decision` 提交事务再交 `actions.execute_confirmed` 执行），草稿按 D6 明确指令直存、模糊意图出确认卡，直写与确认两条路径都按「授权意图键」唯一（重复调用只落一行），**社区发布无任何副作用，发布始终由用户手动完成**。录音协议 psrec01：命令走 `petsense/device/<id>/command`，回传走设备作用域 `petsense/sensor/<id>/agent_audio`，请求与回传同一高熵 requestId；服务端在读文件与落库前验证设备/宠物归属、凭据、时长与去重，音频存 `agent-recordings/` 独立目录并经专属鉴权路由读取（不经旧公共 `/audio/`）；固件两目标同源 `agent_audio_protocol.{h,cpp}`，遥测 envelope 上报 `agentAudio` 能力。**灰度：`PETSENSE_AGENT_RECORDING_ENABLED` 默认关闭**（生产未开启），关闭时录音工具不进 schemas、系统提示如实说明未开放；日记与草稿可独立启用。数据层 `db.session_scope` 增加同线程嵌套保护（自开事务的能力必须在自身作用域之外调用）。alembic 0018（接 W09 的 0017）。2026-11-23 合并到 W10 分支后行为评测 E58–E65 真实模型复跑通过；共享后台回合（`loop.run_structured`）补齐「模型空响应放宽 token 上限重试」，与对话形态同口径。

## 运动日记

- 「今日」的「今日记录」下方一键提交 AI 海报。服务端用宠物档案照片和上海时间当天的已识别走动时长，按试点步频 150 步/分钟估算。步频未经实机校准，海报标明为估算。新任务需要档案照及至少一分钟有效走动。同日复用任务，详情可手动重新生成。已生成的模板海报仍可查看。
- 后台默认用 PetGIF 的 Marni 演示 UUID 挂载日记任务，可用 `PETSENSE_PETGIF_DIARY_PET_ID` 替换。生成素材来自宠物档案，用户无需绑定个人 PetGIF 形象。PetGIF 托管素材可通过链接公开访问，App 在提交前提示。配置见 [运动日记接入说明](docs/EXERCISE_DIARY.md)。
- 海报保存在运行目录的 `diary-posters/`，通过鉴权接口读取，沿用 90 天任务保留期。社区发布统一走动态发布页并独立保存草稿。原生保存相册使用 Expo MediaLibrary，需要包含该模块的原生构建。
