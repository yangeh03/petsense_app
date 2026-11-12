# PetSense App

宠物健康管理 App：`app/`（React Native + Expo 客户端）+ `server/`（Python 服务端），单仓库双端。

## 仓库速览

- `app/`：Expo SDK 58、React Native 0.88、TypeScript（strict）、expo-router、Reanimated、react-native-svg。所有 npm 命令在 `app/` 下执行
- `server/`：Python 3.12 Flask。代码在 `server/petsense/` 包（入口 `wsgi.py`），按域分包：`telemetry/`（MQTT ingest、WS/SSE 分发、音频重组、设备控制）、`actions/`（动作识别）、`collection/`（数据采集 API）、`experiments/`（实验录制）、`auth/`、`pets/`、`devices/`（项圈认领/解绑）、`social/`、`petgif/`、`trends/`、`web/`，另含 `db/`（数据库会话与 ORM 模型）
- 运行时数据（DB、uploads、cloud_data 等）在服务端安装根目录（包上一级），与代码分离；本地跑 `PETSENSE_MQTT_DISABLED=1 python3 wsgi.py`
- 部署：push main 触发 CI，按变更路径执行——`app/` 有变才跑客户端链（Lint/Typecheck/Web 构建/客户端状态回归测试），`server/`（含 ci.yml）有变才跑服务端测试+模型校验并自动部署腾讯云；无关变更两侧检查秒级跳过。`app/` 有变的 main 直推另发 EAS Update OTA；手动重部署用 Actions 的 Run workflow

## 客户端要点

- 页面在 `app/src/app/`（`(tabs)` 含今日、位置、记录、社区、我的，以及 login/create/user/monitor/setup 等页面；setup 为首次引导与设备配对页），实时监测组件在 `app/src/components/live/`，WebSocket 遥测客户端在 `app/src/services/telemetry-service.ts`，实时数据 Hook 在 `app/src/hooks/use-telemetry.ts`，帧类型在 `app/src/types/telemetry.ts`，主题在 `app/src/constants/theme.ts`，回放数据集在 `app/src/data/replay-dataset.ts`
- 实时链路：设备 → MQTT（腾讯云）→ server（按设备归属过滤）→ WebSocket `/ws?token=`（需登录，服务端只推当前用户名下设备的帧；未登录返回 `unauthenticated` 状态）→ `telemetry-service.ts`（自动重连）→ `useTelemetry()`；连接地址在 `app/src/config.ts`，可用 `EXPO_PUBLIC_TELEMETRY_URL` 覆盖；设备归属在 devices 表（一人一设备，认领即绑定宠物，`/api/app/device/*`）
- 遥测双模式：`EXPO_PUBLIC_TELEMETRY_MODE=replay npm run start:replay` 离线循环回放真实实验数据集（由 `app/tools/gen-replay-dataset.cjs` 从云端 cloud_data 生成）
- 路径别名：`@/*` → `app/src/*`，`@/assets/*` → `app/assets/*`
- 品牌色：珊瑚橙（light `#E8835C` / dark `#F0987A`），绿色仅作在线/正常语义色（light `#31A66D` / dark `#4CC38A`）；全部颜色定义在 `app/src/constants/theme.ts`，不要在页面里写死颜色

## 开发约定

- 提交前必须通过：`npm run lint`、`npm run typecheck`、`npm run format:check`、`npm test`（在 `app/` 下；CI 会拦截）；服务端改动跑 `python -m unittest discover -s server/tests`
- 图标：用 `expo-symbols` 的 `SymbolView`，`name` 传 `{ ios, android, web }` 三端映射；图标名必须是合法的 SF Symbol / Material 名称，类型检查会拦截非法名字
- 不做纯前端假数据页面：所有展示必须有真实数据源（云端遥测或 `app/src/data/` 的真实实验数据集）
- 动画：优先用 Reanimated（React Compiler 已开启，RN Animated 的 ref 写法会被 lint 拦截）
- 遥测帧解析只改 `app/src/services/telemetry-service.ts`，`health.valid === false` 的帧不进时间序列
- 服务端按域加模块：新路由放进对应域的包，新域先建子包再在 `petsense/app.py` 的 `create_app()` 里注册 blueprint

## 近期项目状态（2026-10-29 核对远端 main）

- 项目还包括 `firmware/` 和 `tools/offline-map/`。UWB 定位代码集中在 `server/petsense/uwb/`；设备测距经 MQTT 入站，服务端按设备归属、Tag 和标定地图解算并通过鉴权接口及 WebSocket 提供位置，App 位置页消费服务端结果。地图版本变化会触发刷新；失联时保留标识明确的最后位置。
- 远端 main 已合入 edge 固件 BU03 测距上传、三基站触碰标定，以及绕边采集、点云对齐预览与确认发布。操作和限制分别见 `docs/uwb-guided-setup.md`、`docs/uwb-walk-alignment.md`；自动对齐候选仍需人工预览确认，地面参数需人工确认，当前触碰标定假定同一测距平面。采集和任务锁基于单进程，扩展多进程前需改为共享协调。
- 离线建图工具及其依赖与在线定位分工见 `tools/offline-map/README.md` 和 `docs/UWB_INTEGRATION.md`。真实设备、实地定位误差及原生端表现需按实际验收结果判断；历史文档中的模拟数据或浏览器测试不能证明硬件闭环已经完成。
- 截至本次核对远端为 `c53966b`；本地工作区可能落后或含未提交改动，后续工作先检查 `git status` 和远端，不覆盖用户工作。

## 模拟器 UI 端到端验证（computer use 实战经验）

- 启动：`EXPO_PUBLIC_API_BASE_URL=http://<目标服务> nohup npx expo start --ios --port 8082`（8081 常被用户自己的 Metro 占用，勿动）。`EXPO_PUBLIC_*` 是 Metro 打包时内联的，改了必须让 App 重新拉包
- **Expo Go/dev client 对同一 URL 去重**：`simctl openurl` 只会把已运行的 App 带回前台，内存里还是旧 bundle——表现为"请求全失败/改动不生效"。必须先 `xcrun simctl terminate booted host.exp.Exponent`（或 `com.yangeh03.petsense`）再 openurl，**Metro 日志出现 `iOS Bundled` 才算新包生效**
- 驱动 App 优先用 computer use 的无障碍树：Simulator 窗口里能看到 App 内元素（中文标题按索引点击）；文本框用 `setValue` 直接设值——`typeText`/键盘事件进不了 iOS 输入框
- 系统相册（PHPicker）和裁剪页是远端视图：AX 树看不到，且 CUA 截图帧持续失效（点击报 frame no longer maps）。用 `cliclick`（需 `brew install cliclick`）发真实鼠标事件；多显示器时先把模拟器窗口挪到主屏（`osascript` 设 position，副屏的 AX 坐标空间不可靠），点击点 = 窗口原点 + CUA 截图像素坐标（1:1）。System Events 的 `click at` 对模拟器内容无效
- 判定界面状态用 `xcrun simctl io booted screenshot`（直接截设备屏，不受窗口位置/遮挡影响）；定位按钮可用 PIL 按品牌色扫描像素再除以 3 换算逻辑 pt
- 判定请求是否真正到达服务端：`cloud-ssh tencent 'sudo journalctl -u petsense.service --since "2 min ago"'` 看每条请求的状态码，这是"客户端没发出去"还是"服务端报错"的铁证
- 测试账号 `petsense` / `123456` 是生产真实账号（宠物 Marni）：不要改名字等真实资料；纯接口测试用 `PETSENSE_ROOT=/tmp/xxx PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python wsgi.py` 起隔离实例（临时 root 会自动 alembic 建库），测完清理

# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v58.0.0/ before writing any code.
