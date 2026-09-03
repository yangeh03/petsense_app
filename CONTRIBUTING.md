# 贡献指南（CONTRIBUTING）

欢迎加入 PetSense 开发！开始之前请先阅读 [README.md](./README.md) 完成环境配置。

## 协作流程总览

```
认领 Issue → 从 main 拉取分支 → 开发 + 自测 → 提交 PR → Code Review → 合并
```

我们**不直接向 main 分支推送代码**，所有变更通过 Pull Request 合并。

## 分支命名规范

从 `main` 拉出新分支，命名格式：`类型/简短描述`，示例：

| 分支                    | 用途                  |
| ----------------------- | --------------------- |
| `feat/pet-detail-page`  | 新功能：宠物详情页    |
| `fix/task-toggle-bug`   | 修复：待办勾选失效    |
| `refactor/theme-colors` | 重构：主题色整理      |
| `docs/readme-env`       | 文档：README 环境说明 |
| `chore/ci-pipeline`     | 工程配置：CI 调整     |

```bash
git checkout main && git pull
git checkout -b feat/your-feature
```

## Commit 规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)，格式：

```
<类型>(<范围>): <描述>

[可选正文]
```

常用类型：

| 类型       | 说明               | 示例                                  |
| ---------- | ------------------ | ------------------------------------- |
| `feat`     | 新功能             | `feat(pets): 宠物详情页支持体重曲线`  |
| `fix`      | 修复缺陷           | `fix(home): 修复待办勾选后计数不更新` |
| `style`    | 样式（不影响逻辑） | `style(profile): 调整菜单行间距`      |
| `refactor` | 重构               | `refactor(data): 抽离宠物数据类型`    |
| `docs`     | 文档               | `docs: 补充 Windows 环境说明`         |
| `chore`    | 构建 / CI / 依赖   | `chore(ci): CI 增加 web 构建校验`     |
| `test`     | 测试               | `test(utils): 补充分页工具单测`       |

## 提交 PR 之前

确保以下命令全部通过（与 CI 完全一致）：

```bash
npm run lint          # ESLint
npm run typecheck     # TypeScript 类型检查
npm run format:check  # Prettier 格式检查
```

格式不符合时运行 `npm run format` 自动修复后再提交。

UI 变更请在模拟器 / Expo Go 中手动验证，并确认**深色模式**下显示正常。

## PR 要求

1. 使用仓库提供的 PR 模板填写变更说明与自测清单
2. CI 全绿（Lint / Typecheck / Web 构建）
3. 至少 **1 位成员** Review 通过后才能合并
4. 保持 PR 小而聚焦，大功能请拆分为多个 PR
5. 合并后删除远端分支

## 代码约定

- **语言**：TypeScript，禁止提交 `.js` 页面代码；注释与文档使用中文
- **颜色与主题**：统一使用 `src/constants/theme.ts` 中的定义，通过 `useTheme()` 获取，禁止页面内写死颜色（保证明暗模式一致）
- **组件**：可复用组件放 `src/components/`，一个文件一个组件，`PascalCase` 命名
- **页面**：放 `src/app/`，由 expo-router 文件式路由自动注册
- **图标**：使用 `expo-symbols` 的 `SymbolView`，`name` 必须提供 `{ ios, android, web }` 三端映射
- **Mock 数据**：统一放 `src/data/`，导出类型定义；接入真实 API 时只改这一层
- **路径别名**：使用 `@/` 前缀（`@/components/...`），禁止 `../../` 相对路径跨层级引用
- **格式化**：Prettier 已配置保存自动格式化（见 `.vscode/settings.json`），不要手动调整格式

## 报告问题

- 缺陷：使用 [Bug 模板](https://github.com/yangeh03/petsense_app/issues/new?template=bug_report.md)
- 功能建议：使用 [功能模板](https://github.com/yangeh03/petsense_app/issues/new?template=feature_request.md)

提 Issue 时请尽量提供复现步骤与环境信息。

## 有问题？

优先在团队群里讨论，或在对应 Issue 下留言。
