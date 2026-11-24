# W01–W10 视觉验收交接（2026-11-23 晚）

> **已接手归档。** §1–§5 保留交接时状态。补验结论、环境和缺陷修复见 [§6](#6-接手后验收记录2026-11-23)。
>
> W11/W12 的双主题视觉补验不在此文：见 `docs/agent-w11-spec.md` §7、`docs/agent-w12-spec.md` §5，截图索引 `docs/reviews/2026-11-24-agent-w11-w12/`（2026-11-24）。

> 背景：委托簿 UI 事故后，AGENTS.md 新增规则「功能与视觉双通道验收」。本文档交接
> W00–W10 已交付界面的**模拟器视觉重验收**的当前状态、剩余工作和可用工具链。
> 工作目录：`/tmp/w01w10-visual/`（shots/ 全部截图、bin/ 全部脚本、ax*.json AX 树转储）。

## 1. 环境（当前仍在运行）

| 组件 | 状态 | 说明 |
|---|---|---|
| Metro | http://localhost:8096 运行中 | 从 worktree `~/.codex/worktrees/petsense-w08-review/petsense_app` 打包，代码与 main 一致（含 PR #26）；Web 端同代码可用 |
| 隔离后端 | http://127.0.0.1:8080（pid 98837） | `PETSENSE_ROOT=/tmp/w08w10-acc-20261123/root`，**无 MiniMax 凭据**（发消息会失败——正好用来验失败态） |
| 测试账号 | petsense / 123456 | 模拟器已重新登录（2026-11-23 21:5x） |
| idb_companion | `~/.local/share/idb-companion-1.7.8/idb_companion` | 官方 v1.7.8 预编译包，sha256 已校验；客户端 `~/.local/bin/idb`（fb-idb 1.7.7，可用） |
| 目标设备 | iPhone 17 Pro，UDID `67C41F02-93EF-4B22-AA0F-D45B34140965` | **注意：还有第二台模拟器「W06-验收」（EFD0DD5F…）可能 booted，所有命令必须用 UDID，禁用 `booted` 关键字** |

### 常用命令（`/tmp/w01w10-visual/bin/`）

```bash
# 截图（全分辨率 + 603px 半图）
bin/shot.sh <name>                      # -> shots/<name>.png + .half.png

# 重载 bundle 并深链（等 Metro 出现 iOS Bundled 才算新包）
bin/reload.sh agent                     # 或 agent-tasks / assistant-settings / pet / login

# idb 触摸注入（已封装）
bin/idbx.sh tap <x> <y>                 # 设备逻辑坐标（402×874）
bin/idbx.sh swipe up|down [reps]

# ★ 最高效路径：AX 树按 label 找元素 → frame → tap / set-value（未封装，直接用）
CP=~/.local/share/idb-companion-1.7.8/idb_companion
~/.local/bin/idb --companion-path $CP ui describe-all --udid <UDID> > ax.json
# 在 ax.json 里按 AXLabel 查 frame（注意：frame 是内容坐标，负值=已滚过；屏内元素 0<y<874）
~/.local/bin/idb --companion-path $CP ui set-value --udid <UDID> --api ax-coresimulator \
  --match-key AXValue --value "petsense" "账号（2–20 位中文 / 字母 / 数字 / 下划线）"   # 已验证可登录
```

## 2. 已完成（有证据）

| 项 | 结论 | 证据 |
|---|---|---|
| 伙伴会话页浅色全量走查（13 回合） | 通过（视觉模型的 2 个对齐指控经像素实测为幻觉，已剔除） | shots/s01–s22、t3、t5 |
| 卡片对齐（聊天内） | 通过：全部卡片 left=62pt/right=383pt，与文本列(61pt)一致 | shots 像素测量 |
| 话题视图（横幅/回信卡/日期来源按钮渲染） | 通过 | shots/topic3 |
| 键盘弹起态 | 通过（内容上移 + iOS 26 半透明键盘；注：键盘不是纯白，勿用白色检测） | shots/k3 |
| 发送→pending→失败态 | Web DOM 验证通过（气泡出现→输入框清空→「再说一次」）；原生渲染同组件同数据，未单独截屏 | 浏览器会话 |
| W07 带图回合 | 数据已就绪：API 上传 3 张真实图并随消息发送成 turn 15（橙/绿/蓝 900×1200 JPEG）；AX 树可见 `查看第 1/2/3 张图片` 按钮（内容 y≈6553）和失败态 `再说一次` | ax3/ax4.json |
| 登录/未登录态 | 未登录态截图 loggedout.png；登录操作本身验证了 idb set-value 路径 | shots/loggedout, logedin |

### 随验随修（已全部合入 main 并 OTA）

| PR | 内容 |
|---|---|
| #23（此前） | W08–W10 全部 + 卡片对齐文本列修正 |
| #24 | 委托簿重设计（状态胶囊/层次/投影/标题栏穿模修复） |
| #26 | **P0：助手回复 Markdown 原样显示**（新自建解析器+渲染器，测试 115/115）+ 关怀卡证据行分钟级去重 |

### 遗留小缺陷（P3，未修）

1. 破折号「——」可悬行首（RN Text 无 CJK 标点规则）
2. 脚注「继续聊这件事」小字对比度偏低（设计取舍项）
3. Web 端点「发送」按钮：按钮在视口外时 Playwright click 挂起（用户实际手点无碍）

## 3. 剩余工作（按优先级）

1. **W07 图片网格 + 灯箱**（数据已就绪，就差操作）：
   - 滚到列表底部（turn 15），`describe-all` 找 `查看第 2 张图片` 的屏内 frame → tap → 截图灯箱 → 点关闭。
   - 顺路截「再说一次」失败回合的原生渲染。
2. **助手设定页**（`bin/reload.sh assistant-settings`）：主动程度三档选择器、保存。
3. **宠物档案页**（`bin/reload.sh pet`）：W03 生日精度展示、头像入口。
4. **社区发布页私有草稿**（`bin/reload.sh create`）：W10 草稿恢复横幅（隔离库已有草稿数据）。
5. **委托簿多状态**：当前只有「已交付」任务（id=1）。可通过 API `POST /api/app/agent/pets/1/tasks/<id>/ops`（pause→resume）产生 paused 态截图；waiting_baseline/空态需造数据或标注代码走查。
6. **深色模式**：`xcrun simctl ui <UDID> appearance dark` → 重载 → 各页截图；颜色断言：bg `#17120E`、卡 `#241D17`、文字 `#F3EAE0`。
7. **汇总回写**：把结论写进各 W 系列 spec 的验收节 + 本文档归档或删除。

## 4. 本轮踩坑（接手人必读）

- **两台模拟器并用**时 `booted` 关键字随机命中其一，事件/截图会打到错设备、登录态会被别的进程清掉。一律用 UDID。
- **idb describe-all 在列表滚动惯性中返回空**；等 2–3 秒停稳再查。
- **idb swipe 动量不可控**（duration 参数不等于慢速拖拽），快滑一次可能飞几千 pt；精确定位用「describe-all 查目标内容坐标 → 估算差值 → 小步 swipe(1.2s) → 再探测」。
- 聊天列表在 `onContentSizeChange` 时会恢复 store 里的 scrollY，操作中偶发跳回。
- **CUA（computer-use 插件）在这台机器上基本不可用**：窗口在别的桌面空间导致无栅格帧；element_scroll 未实现；AX 桥陈旧。**用 idb 的 AX 接口替代**（describe-all / set-value --api ax-coresimulator / tap），这才是项目记忆里记载的可用路径。
- 中文文本输入：键盘事件进不了 iOS 输入框，必须 `idb ui set-value`（AX 路径）。
- 视觉判断走 `analyze_image`（4.5v MCP），需要先 Read 截图拿 CDN URL 再原样传参——URL 必须逐字符复制，签名差一个字符就 400。

## 5. 缺陷分级汇总（至交接时）

- P0×1 Markdown 未渲染 → **已修**（PR #26）
- P2×1 证据行重复 → **已修**（PR #26）
- P3×3 见上「遗留小缺陷」
- 其余验收面：无缺陷

## 6. 接手后验收记录（2026-11-23）

### 环境和范围

- iPhone 17 Pro，iOS 26.5，Expo Go。以 `5d945b4` 的 `main` 为基线，在主检出上构建修复版。每次冷重载确认 Metro 出现 `iOS Bundled`。
- `petsense` 登录隔离实例 `127.0.0.1:8080`，伙伴回合、图片与草稿沿用交接夹具。补种月份精度生日与两项委托。另以 SQLite 副本启动 `127.0.0.1:8098`，清空委托表验证真实空列表，在模型调用前增加 8 秒等待以截取原生发送中状态。两套实例均无模型凭据。
- 测试经过真实的 `/turns`、`/assistant-settings`、`/api/app/pets`、`/social-draft`、`/tasks` 和 `/tasks/<id>/ops`。视觉判断基于设备截图，操作和回读另用 AX 和接口核对。截图右下蓝色齿轮为 Expo Go 开发浮标。
- 以今日页和既有档案页对照容器、间距与主题。委托簿沿用圆角、边框和浅色卡片投影，聊天卡以轻边框贴齐正文列，弹层与资料页用圆角色块分组。深色截图像素核对了 `#17120E` 页面背景、`#241D17` 卡面与 `#F3EAE0` 主文字。

### 验收结果

| 范围 | 截图走查 | 功能核对 |
|---|---|---|
| W01 伙伴对话 | 浅色沿用 §2 的 13 回合走查，修复版抽查 Markdown、深色会话、输入区和发送状态 | 原生发送等待指示可见，失败后保留消息及重试入口。窄屏 Web 发送后输入框清空，显示消息和重试按钮 |
| W03 助手设定与宠物档案 | 三档选中边框、提示文案、底部保存状态、档案信息层级。生日显示「约 2022 年 5 月 · 约 4 岁」 | 切换安静、标准、多提醒，保存返回 200，重新拉取保留所选档位。从聊天宠物头像进入档案 |
| W07 图片 | 两列方格、缩略图圆角、原图长宽比和灯箱关闭按钮。浅色打开第 2 张，深色滑动至第 3 张 | 带图失败回合恢复原话与三张附件，原生输入框完整显示两行。切换到纯文字回合重试时清理之前的图片 |
| W08 委托簿 | 浅色、深色检查观察中、已暂停、等待基线、已交付和空态。标题和状态胶囊同排，卡片与导航栏保有间距 | 用原生按钮暂停再恢复观察，均回读成功。空态从隔离副本读到 `tasks: []` |
| W09 关怀 | 深色低电卡的标题、持续状态、证据及追问入口可读，浅色沿用 §2 | 低电发生与追问行为沿用 W09 原验收，主动程度控件在 W03 实际操作 |
| W10 发布页 | 浅色、深色均复查私有草稿标识、正文层级和填入按钮。发布控件的前景色与品牌底色搭配核对 | 填入正文后关闭再打开，编辑内容仍在。操作只使用隔离实例的私有草稿 |

### 修复和边界

- 伙伴回复的加粗内联代码同时保留两种样式，修复合并引用行丢失样式和空白行过大，渲染层避免中文破折号拆开或悬在正文行首。重试与继续聊入口使用主文字色。
- 原生输入框根据内容高度展开，设置页保存回执显示于原按钮。委托空态分成标题与操作提示。浅色的已交付、等待基线和观察中胶囊文字与各自底色的对比度均高于 5.5:1，浅色发布动作文字与珊瑚橙底色的对比度为 4.9:1。
- 390×844 Chrome 以直接访问登录页复现了登录成功后仍停留在原页的问题。无返回历史时回到「我的」，以同一入口复测成功。伙伴发送按钮在该视口可见且可点击。交接中仅见于自动化的视口外点击挂起未作为发送按钮缺陷。
- 本轮集中补验 App 视觉和交互。生产模型、档案写入、真实设备与 Android 继续按各工作包的既有验收结论判断。

### 截图索引和静态验证

- [iOS 浅色](reviews/2026-11-23-agent-visual/ios-light.jpg) 从左到右为修复后的伙伴 Markdown、图片灯箱和发布页私有草稿。
- [iOS 深色](reviews/2026-11-23-agent-visual/ios-dark.jpg) 从左到右依次为伙伴关怀卡、助手设定保存、宠物档案、委托簿、私有草稿及两行正文的带图重试。
- [iOS 空态与发送状态](reviews/2026-11-23-agent-visual/ios-states.jpg) 从左到右为委托空态、伙伴接话中及纯文字发送失败。AX 实测输入框单行约 40 pt、带图恢复两行约 62 pt。切到纯文字重试时 AX 无附件控件，服务端该回合的 `images` 为空。
- `npm run lint`、`npm run typecheck`、`npm run format:check`、`npm test`（118/118）及 Expo Web 导出通过。Markdown 回归新增加粗内代码、引用行保留样式和空行分块三项。
