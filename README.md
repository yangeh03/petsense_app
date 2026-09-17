# PetSense 🐾

宠物健康管理 App（Demo），使用 **React Native + Expo** 构建，一套代码同时支持 **iOS / Android / Web**。

## 功能演示

| 模块        | 说明                                                                 |
| ----------- | -------------------------------------------------------------------- |
| 💚 实时监测 | 硬件设备数据经云端实时推送：心率 / 血氧 / 体温 / 电量 + 实时趋势曲线 |
| 🏠 今日     | 实时监测卡片 + 当前宠物档案 + 可勾选的护理待办                       |
| 🐶 宠物     | 宠物档案列表，点击卡片切换「当前宠物」，首页数据联动更新             |
| 👤 我的     | 用户信息与设置入口（健康报告、疫苗提醒、家人共享等）                 |

> 实时数据来自云端真实链路；宠物档案与待办为 `src/data/pets.ts` 演示数据。

## 实时监测架构（跨网）

APP 与硬件设备**不要求同一局域网**，数据全程经云端中转：

```
ESP32 硬件 ──MQTT──▶ 阿里云 Mosquitto ──▶ petsense-server(Flask) ──WebSocket──▶ APP
 (任意网络)            8.148.188.68:1883     :8080                /ws            (任意网络)
```

- 服务端（`server.py`）部署在阿里云 `/root/petsense-server`，订阅 `petsense/sensor/#` 并向 `/ws` 广播
- APP 端实时层在 `src/services/telemetry-service.ts`（自动重连），页面通过 `useTelemetry()` 订阅
- 连接地址在 `src/config.ts` 配置，默认线上地址，可用环境变量 `EXPO_PUBLIC_TELEMETRY_URL` 覆盖
- 设备离线时云端有 `tools/device-simulator.py` 模拟器补数据流（部署在阿里云常驻运行）
- 服务端 HTTP 接口：`/api/latest-full`（最近 200 帧）、`/api/stats`（统计）、`/ws`（实时推送）

### 本地联调（不依赖云端）

```bash
# 终端 1：启动本地模拟服务（每秒一帧模拟数据）
npm run mock:server

# 终端 2：APP 指向本地 mock 启动（手机调试请把 localhost 换成电脑局域网 IP）
EXPO_PUBLIC_TELEMETRY_URL=ws://localhost:8090/ws npm run web
```

## 技术栈

- [Expo SDK 57](https://docs.expo.dev/versions/v57.0.0/) + [expo-router](https://docs.expo.dev/router/introduction/)（文件式路由 + 原生底部 Tab）
- React Native 0.86 + React 19（React Compiler 已开启）
- TypeScript（strict 模式）
- ESLint（eslint-config-expo）+ Prettier + GitHub Actions CI

## 环境准备（macOS）

团队默认使用 Mac 开发，请按顺序安装以下内容。

### 1. Node.js（必需）

推荐用 [nvm](https://github.com/nvm-sh/nvm) 管理版本，仓库已提供 `.nvmrc`：

```bash
# 安装 nvm（已安装可跳过）
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
# 重启终端后安装并使用 Node 22
nvm install
nvm use
node -v   # 应输出 v22.x
```

### 2. Watchman（推荐）

React Native 开发体验更好，避免 Metro 文件监听问题：

```bash
brew install watchman
```

### 3. iOS 开发环境（需要真机 / 模拟器时）

1. 从 App Store 安装 **Xcode**（建议最新稳定版），首次打开后完成组件安装
2. 安装命令行工具并接受协议：

```bash
xcode-select --install
sudo xcodebuild -license accept
```

3. 安装 CocoaPods（构建原生 iOS 代码时需要）：

```bash
brew install cocoapods
```

4. 打开 Xcode → Settings → Platforms，确认已下载 **iOS** 平台与模拟器

### 4. Android 开发环境（需要真机 / 模拟器时）

1. 安装 [Android Studio](https://developer.android.com/studio)，按向导安装最新 SDK
2. 配置环境变量（`~/.zshrc`）：

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
export PATH=$PATH:$ANDROID_HOME/platform-tools
```

3. 生效并验证：

```bash
source ~/.zshrc
adb devices   # 能执行即配置成功
```

4. 在 Android Studio → Device Manager 中创建一个模拟器（API 34+）

### 5. 最简方案：Expo Go（不想装上面任何东西时）

在手机上安装 [Expo Go](https://docs.expo.dev/get-started/expo-go/)（App Store / Google Play），
电脑只需 Node.js 即可开发调试，日常 UI 开发推荐这种方式。

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/yangeh03/petsense_app.git
cd petsense_app

# 2. 切换 Node 版本并安装依赖
nvm use
npm install

# 3. 启动开发服务器
npm start
```

启动后按提示操作：

| 按键 / 命令      | 作用                                                |
| ---------------- | --------------------------------------------------- |
| `i`              | 打开 iOS 模拟器（需已装 Xcode）                     |
| `a`              | 打开 Android 模拟器 / 真机（需已装 Android Studio） |
| `w`              | 在浏览器中打开 Web 版（零依赖，最快预览方式）       |
| 手机扫终端二维码 | 用 Expo Go 真机调试                                 |

也可以直接运行：`npm run ios` / `npm run android` / `npm run web`。

## 常用命令

| 命令                              | 说明                                         |
| --------------------------------- | -------------------------------------------- |
| `npm start`                       | 启动 Metro 开发服务器                        |
| `npm run ios` / `android` / `web` | 直接在对应平台启动                           |
| `npm run lint`                    | ESLint 检查                                  |
| `npm run typecheck`               | TypeScript 类型检查                          |
| `npm run format`                  | Prettier 格式化全部文件                      |
| `npm run format:check`            | Prettier 格式检查（CI 使用）                 |
| `npm run mock:server`             | 启动本地遥测模拟服务（:8090，1s/帧）         |
| `npm run generate-icons`          | 从 `scripts/icons/` 的 SVG 重新生成 App 图标 |
| `npm run reset-project`           | 清空模板重置（危险，会移动代码到 example/）  |

## 项目结构

```
petsense_app/
├── .github/
│   ├── workflows/ci.yml          # CI：lint + typecheck + web 构建
│   ├── PULL_REQUEST_TEMPLATE.md  # PR 模板
│   └── ISSUE_TEMPLATE/           # Issue 模板（Bug / 功能建议）
├── assets/                       # 图标、启动图等静态资源
├── scripts/
│   ├── icons/                    # 品牌图标 SVG 源文件
│   └── generate-icons.sh         # 图标生成脚本（macOS 自带工具，无需装依赖）
├── tools/
│   ├── mock-server.js            # 本地遥测模拟服务（零依赖 WebSocket）
│   └── device-simulator.py       # 设备模拟器（部署在阿里云，MQTT 上报）
└── src/
    ├── app/                      # 页面（expo-router 文件式路由）
    │   ├── _layout.tsx           # 根布局：Stack + 主题
    │   ├── (tabs)/               # Tab 页面组（今日 / 宠物 / 我的）
    │   └── monitor.tsx           # 实时监测详情页
    ├── components/               # 通用组件（live/ 为实时监测组件）
    ├── config.ts                 # 运行时配置（遥测地址）
    ├── constants/theme.ts        # 颜色 / 字体 / 间距（明暗双主题）
    ├── context/                  # 全局状态（当前宠物）
    ├── data/                     # Mock 数据与类型定义
    ├── hooks/                    # 自定义 Hooks（use-telemetry 实时数据流）
    ├── services/                 # WebSocket 遥测客户端（自动重连）
    └── types/                    # 遥测数据帧类型定义
```

## 团队协作

- 分支命名、Commit 规范、PR 流程见 [CONTRIBUTING.md](./CONTRIBUTING.md)
- 代码提交前请确保本地通过：`npm run lint && npm run typecheck && npm run format:check`
- CI（GitHub Actions）会在每个 PR 上自动执行同样检查，未通过无法合并
- 修改品牌色 / 主题请统一改 `src/constants/theme.ts`，不要在页面写死颜色
- App 图标源文件在 `scripts/icons/`，修改后运行 `npm run generate-icons` 重新生成

## 常见问题

**Q：`npm install` 或启动时报 Node 版本不兼容？**
A：确认 `nvm use` 后 Node 为 v22（`.nvmrc` 指定版本），必要时 `nvm install`。

**Q：iOS 模拟器起不来 / 报 xcode 相关错误？**
A：打开 Xcode 确认模拟器已下载；执行 `sudo xcode-select -s /Applications/Xcode.app`；仍有问题可 `npx expo start -c` 清缓存重启。

**Q：8081 端口被占用？**
A：`lsof -ti:8081 | xargs kill -9`，或让 Metro 换端口 `npx expo start --port 8082`。

**Q：Metro 缓存异常、热更新不生效？**
A：`npx expo start -c` 清缓存启动；偶发问题可 `watchman watch-del-all`。

**Q：Android 模拟器检测不到（adb devices 为空）？**
A：确认 `ANDROID_HOME` 配置正确（见上文），先手动打开 Android Studio 再启动模拟器。

**Q：真机 Expo Go 扫码后卡在 opening project？**
A：三个高频原因，按序排查：① Metro 广播的局域网 IP 过期（Mac 换网后未重启）→ 重启 `npm start` 再扫新码；② 手机开了 VPN / 代理 → 关掉再试；③ 路由器开了 AP 隔离 → 改用 `npx expo start --tunnel` 隧道模式。

**Q：真机提示需要登录 Expo Go？**
A：2025 年起 Expo Go 强制登录。手机 Expo Go 与电脑 CLI 需登录**同一免费账号**：电脑执行 `npx expo login -b`（浏览器授权），手机 App 内 Sign in。

**Q：真机提示 Expo Go 版本不兼容？**
A：项目使用 SDK 57，手机端 Expo Go 太旧会报 "requires a newer version" → App Store 更新 Expo Go 后重连。

**Q：实时监测显示「未连接」？**
A：① 确认手机能访问 `http://8.148.188.68:8080/api/stats`；② 本地联调时检查 `EXPO_PUBLIC_TELEMETRY_URL` 是否指向电脑局域网 IP（手机上 `localhost` 不是电脑）；③ 服务重启后 APP 会自动指数退避重连，稍等即可。

## License

MIT
