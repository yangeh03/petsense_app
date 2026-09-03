# PetSense App

宠物健康管理 App（React Native + Expo）。

## 项目速览

- 技术栈：Expo SDK 57、React Native 0.86、TypeScript（strict）、expo-router（文件式路由）、Reanimated
- 目录结构：页面在 `src/app/`，通用组件在 `src/components/`，主题在 `src/constants/theme.ts`，Mock 数据在 `src/data/`，全局状态在 `src/context/`
- 路径别名：`@/*` → `src/*`，`@/assets/*` → `assets/*`
- 品牌色：健康绿（light `#1FA35C` / dark `#3DD68C`），全部颜色定义在 `src/constants/theme.ts`，不要在页面里写死颜色

## 开发约定

- 提交前必须通过：`npm run lint`、`npm run typecheck`、`npm run format:check`（CI 会拦截）
- 图标：用 `expo-symbols` 的 `SymbolView`，`name` 传 `{ ios, android, web }` 三端映射；图标名必须是合法的 SF Symbol / Material 名称，类型检查会拦截非法名字
- Mock 数据统一放在 `src/data/`，类型随数据一起导出，后续替换为 API 层

# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v57.0.0/ before writing any code.
