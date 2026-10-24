# PetSense App

宠物健康管理 App：`app/`（React Native + Expo 客户端）+ `server/`（Python 服务端），单仓库双端。

## 仓库速览

- `app/`：Expo SDK 57、React Native 0.86、TypeScript（strict）、expo-router、Reanimated、react-native-svg。所有 npm 命令在 `app/` 下执行
- `server/`：Python 3.12 Flask。代码在 `server/petsense/` 包（入口 `wsgi.py`），按域分包：`telemetry/`（MQTT ingest、WS/SSE 分发、音频重组、设备控制）、`actions/`（动作识别）、`collection/`（数据采集 API）、`experiments/`（实验录制）、`auth/`、`pets/`、`social/`、`petgif/`、`trends/`、`web/`
- 运行时数据（DB、uploads、cloud_data 等）在服务端安装根目录（包上一级），与代码分离；本地跑 `PETSENSE_MQTT_DISABLED=1 python3 wsgi.py`
- 部署：push main 触发 CI，按变更路径执行——`app/` 有变才跑客户端链（Lint/Typecheck/Web 构建），`server/`（含 ci.yml）有变才跑服务端测试+模型校验并自动部署腾讯云；无关变更两侧检查秒级跳过。手动重部署用 Actions 的 Run workflow

## 客户端要点

- 页面在 `app/src/app/`（(tabs) 四 tab + login/create/user/monitor 弹层），实时监测组件在 `app/src/components/live/`，WebSocket 遥测客户端在 `app/src/services/telemetry-service.ts`，实时数据 Hook 在 `app/src/hooks/use-telemetry.ts`，帧类型在 `app/src/types/telemetry.ts`，主题在 `app/src/constants/theme.ts`，回放数据集在 `app/src/data/replay-dataset.ts`
- 实时链路：设备 → MQTT（腾讯云）→ server → WebSocket `/ws` → `telemetry-service.ts`（自动重连）→ `useTelemetry()`；连接地址在 `app/src/config.ts`，可用 `EXPO_PUBLIC_TELEMETRY_URL` 覆盖；本地联调用 `npm run mock:server`
- 遥测双模式：`EXPO_PUBLIC_TELEMETRY_MODE=replay npm run start:replay` 离线循环回放真实实验数据集（由 `app/tools/gen-replay-dataset.cjs` 从云端 cloud_data 生成）
- 路径别名：`@/*` → `app/src/*`，`@/assets/*` → `app/assets/*`
- 品牌色：健康绿（light `#1FA35C` / dark `#3DD68C`），全部颜色定义在 `app/src/constants/theme.ts`，不要在页面里写死颜色

## 开发约定

- 提交前必须通过：`npm run lint`、`npm run typecheck`、`npm run format:check`（在 `app/` 下；CI 会拦截）；服务端改动跑 `python -m unittest discover -s server/tests`
- 图标：用 `expo-symbols` 的 `SymbolView`，`name` 传 `{ ios, android, web }` 三端映射；图标名必须是合法的 SF Symbol / Material 名称，类型检查会拦截非法名字
- 不做纯前端假数据页面：所有展示必须有真实数据源（云端遥测或 `app/src/data/` 的真实实验数据集）
- 动画：优先用 Reanimated（React Compiler 已开启，RN Animated 的 ref 写法会被 lint 拦截）
- 遥测帧解析只改 `app/src/services/telemetry-service.ts`，`health.valid === false` 的帧不进时间序列
- 服务端按域加模块：新路由放进对应域的包，新域先建子包再在 `petsense/__init__.py` 注册 blueprint

# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v57.0.0/ before writing any code.
