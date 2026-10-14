# PetSense App

宠物健康管理 App（React Native + Expo）。

## 项目速览

- 技术栈：Expo SDK 57、React Native 0.86、TypeScript（strict）、expo-router（文件式路由）、Reanimated、react-native-svg
- 目录结构：单屏应用，页面在 `src/app/index.tsx`（实时监测主屏，`_layout.tsx` 为根布局），实时监测组件在 `src/components/live/`，WebSocket 遥测客户端在 `src/services/`，实时数据 Hook 在 `src/hooks/use-telemetry.ts`，帧类型在 `src/types/telemetry.ts`，主题在 `src/constants/theme.ts`，回放数据集在 `src/data/replay-dataset.ts`
- 实时链路：设备 → MQTT（腾讯云）→ server.py → WebSocket `/ws` → `telemetry-service.ts`（自动重连）→ `useTelemetry()`；连接地址在 `src/config.ts`，可用 `EXPO_PUBLIC_TELEMETRY_URL` 覆盖；本地联调用 `npm run mock:server`
- 遥测双模式：`src/config.ts` 的 `TELEMETRY_MODE`（`EXPO_PUBLIC_TELEMETRY_MODE=replay npm run start:replay`）——live 连云端实时硬件数据（默认），replay 离线循环回放 `src/data/replay-dataset.ts`（真实实验数据集片段，由 `tools/gen-replay-dataset.cjs` 从云端 cloud_data 生成）
- 路径别名：`@/*` → `src/*`，`@/assets/*` → `assets/*`
- 品牌色：健康绿（light `#1FA35C` / dark `#3DD68C`），全部颜色定义在 `src/constants/theme.ts`，不要在页面里写死颜色

## 开发约定

- 提交前必须通过：`npm run lint`、`npm run typecheck`、`npm run format:check`（CI 会拦截）
- 图标：用 `expo-symbols` 的 `SymbolView`，`name` 传 `{ ios, android, web }` 三端映射；图标名必须是合法的 SF Symbol / Material 名称，类型检查会拦截非法名字
- 不做纯前端假数据页面：所有展示必须有真实数据源（云端遥测或 `src/data/` 的真实实验数据集）
- 动画：优先用 Reanimated（React Compiler 已开启，RN Animated 的 ref 写法会被 lint 拦截）
- 遥测帧解析只改 `src/services/telemetry-service.ts`，`health.valid === false` 的帧不进时间序列

# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v57.0.0/ before writing any code.
