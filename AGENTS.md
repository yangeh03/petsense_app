# PetSense App

宠物健康管理 App（React Native + Expo）。

## 项目速览

- 技术栈：Expo SDK 57、React Native 0.86、TypeScript（strict）、expo-router（文件式路由）、Reanimated、react-native-svg
- 目录结构：页面在 `src/app/`（`(tabs)/` 为 Tab 组、`monitor.tsx` 为监测详情页），通用组件在 `src/components/`（`live/` 为实时监测组件），WebSocket 遥测客户端在 `src/services/`，实时数据 Hook 在 `src/hooks/use-telemetry.ts`，帧类型在 `src/types/telemetry.ts`，主题在 `src/constants/theme.ts`，Mock 数据在 `src/data/`，全局状态在 `src/context/`
- 实时链路：设备 → MQTT（阿里云）→ server.py → WebSocket `/ws` → `telemetry-service.ts`（自动重连）→ `useTelemetry()`；连接地址在 `src/config.ts`，可用 `EXPO_PUBLIC_TELEMETRY_URL` 覆盖；本地联调用 `npm run mock:server`
- 路径别名：`@/*` → `src/*`，`@/assets/*` → `assets/*`
- 品牌色：健康绿（light `#1FA35C` / dark `#3DD68C`），全部颜色定义在 `src/constants/theme.ts`，不要在页面里写死颜色

## 开发约定

- 提交前必须通过：`npm run lint`、`npm run typecheck`、`npm run format:check`（CI 会拦截）
- 图标：用 `expo-symbols` 的 `SymbolView`，`name` 传 `{ ios, android, web }` 三端映射；图标名必须是合法的 SF Symbol / Material 名称，类型检查会拦截非法名字
- Mock 数据统一放在 `src/data/`，类型随数据一起导出，后续替换为 API 层
- 动画：优先用 Reanimated（React Compiler 已开启，RN Animated 的 ref 写法会被 lint 拦截）
- 遥测帧解析只改 `src/services/telemetry-service.ts`，`health.valid === false` 的帧不进时间序列

# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v57.0.0/ before writing any code.
