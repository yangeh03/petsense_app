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
- 共享记忆须纳入 Git 并推送，未提交内容不会随仓库共享；不得写入密码、令牌或私人数据。不要把聊天记录当作其他协作者自动拥有的项目记忆。

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
- 模拟器验收统一使用指定账号 `petsense`，密码由用户提供，不写进源码、文档或提交。可直接在生产 `petsense` 的伙伴会话发送 Agent 验收消息，无需逐条或逐轮批准。需要创建宠物、改动真实资料、绑定设备或验证其他写入时，使用隔离后端的同名账号。
- 涉及历史记录、观测问答和学习累计的验收，优先用指定账号从云端读取真实观测。先检查日期范围及有效覆盖，再挑选有数据的时段。新接口尚未部署时，可用授权的云端只读数据副本在隔离实例验收；本地空库用于缺数据、写入和故障场景，空库通过只算相应场景通过。验收记录须注明数据来源、时段和实际经过的接口。
- Metro 已被占用时使用空闲端口，先检查 `/status`。`--localhost` 可能只监听 IPv6 `::1`，此时 Expo Go 用 `exp://localhost:<端口>`，不要使用 IPv4 `127.0.0.1` 打开 Metro。App 的 API 地址按隔离后端实际监听地址配置。
- `idb` 若找不到默认的 `idb_companion`，连接时通过 `--companion-path` 指向可用二进制。默认 HID 报 `Mach port invalid` 时，使用 `idb ui tap/set-value --api ax-coresimulator`，从无障碍树取元素坐标。每次导航后重新读树，避开过场中同时出现的两页元素。
- iOS 原生标签栏和透明 Stack 标题栏会覆盖内容。固定输入区在键盘收起时要留出标签栏空间，`KeyboardAvoidingView` 的 `padding` 模式会覆盖自身的底部 padding，留白应设在输入区外层。记录来源区放入页面安全布局，并在模拟器截图检查遮挡。
- Expo Router 58 的 iOS NativeTabs 为伙伴页提供的底部安全区已经反映标签栏位置。固定输入区使用该值一次，避免叠加 `BottomTabInset`。iPhone 的标签栏在底部，iPad 的标签栏可在顶部，验收时分别检查。

# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v58.0.0/ before writing any code.

## Agent 开发入口

- 第一阶段开发说明与当前验证边界见 `docs/agent-phase-one.md`；产品、路线和最小契约分别见 `docs/agent-product-integration-design.md`、`docs/agent-implementation-work-packages.md`、`docs/agent-p00-contract.md`。
- 新 Agent 服务端域在 `server/petsense/agent/`，App 使用独立业务服务，不混入遥测解析。模型凭据仅由服务端环境或外部文件读取，不纳入 Git。

## 运动日记

- 「今日」的「今日记录」下方可一键生成。PetSense 服务端按上海时间冻结当天已识别的走动秒数，当前按试点步频 150 步/分钟估算。步频未经实机校准，海报显示「估算步数」，无动作观测时省略数值。按宠物、日期和样式复用任务，用户可在详情页手动重新生成。
- 模板使用档案照片并在服务端渲染。海报通过鉴权接口读取并保存在运行目录的 `diary-posters/`，沿用日记任务 90 天保留期。社区发布统一走动态发布页并独立保存草稿。原生保存相册使用 Expo MediaLibrary，发布需包含该模块的新原生构建。
- 私有 PetGIF 海报流默认关闭。仅在确认上游照片和海报均为私有且测试通过后，才为已有归属验证的个人形象启用 `PETSENSE_PETGIF_PRIVATE_DIARIES=1`。
