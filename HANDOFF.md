# PetSense App · Session Handoff 文档

> 更新时间：2026-10-15 ｜ 最后提交：`513ee3d` ｜ 分支：`main`（与远程同步）
> 上一阶段成果：五 Tab 全栈 App + 云端部署 + PetGIF AI 数字形象集成 + 全链路演示数据

---

## 一、项目是什么

宠物健康管理 App（港科大广州 Red Bird MPhil 群体项目 T20250652 的工程实现）。完整链路：

```
ESP32 项圈 ──MQTT──▶ 腾讯云 Mosquitto(81.71.71.31:1883)
                        │
                        ▼
              server.py（腾讯云 :80，Flask 单文件）
              ├─ 遥测：解析/入库/环形缓冲 + WebSocket /ws 广播
              ├─ 社区/账号/宠物/趋势统计 /api/app/*
              └─ PetGIF 代理 /api/app/petgif/*（key 只存服务端）
                        │
        ┌───────────────┼──────────────┐
        ▼               ▼              ▼
   Expo App 三端    网页控制台      外部 AI 服务
  (iOS/Android/   (81.71.71.31)  qioyo.site/petsenseIMG
   Web 五 Tab)                     （行为GIF/角色卡/日记）
```

**核心演示亮点**：上传宠物照片 → AI 生成角色卡 + 9 种行为动图 → 首页 Hero 实时播放宠物当前动作（行为推断：实验标注 > IMU 分档 > 时段兜底）。

## 二、仓库结构（petsense_app）

```
├── src/app/
│   ├── _layout.tsx            # 根布局：AuthProvider + Stack(modal: create/login)
│   ├── (tabs)/_layout.tsx     # NativeTabs 四页 + 右下角 ⊕ FAB
│   ├── (tabs)/index.tsx       # 今日：Hero(动作GIF/角色卡/emoji 三级) + 体征卡 + 趋势统计 + 运动日记
│   ├── (tabs)/tracking.tsx    # 位置：双模板户型图 + 演示移动引擎 + 拖拽/点击指挥/楼层
│   ├── (tabs)/community.tsx   # 社区：ins 风（搜索/⋯菜单/分享/1.2k 格式化/GIF 帖）
│   ├── (tabs)/profile.tsx     # 我的：宠物行(✨AI形象入口) + 粉丝/关注弹窗 + 诊断入口
│   ├── create.tsx / login.tsx # 弹层（布局已重写，见踩坑 #4）
│   ├── user.tsx / monitor.tsx # 他人主页 / 设备诊断（工程师视图）
├── src/components/
│   ├── alerts/                # 告警三层：铃铛+通知中心+头像状态环（引擎在 services）
│   ├── dashboard/             # diary-modal（运动日记）/ character-card-modal（补生成）
│   ├── live/                  # line-chart（大图表，支持 width/height 参数）
│   ├── social/                # post-card / comment-sheet / follow-list-modal
│   └── floor-plan.tsx         # SVG 户型图 + 移动引擎（自 Desktop/Petsense-demo 移植）
├── src/services/
│   ├── telemetry-service.ts   # 遥测单例（live/replay 双模式 + 帧解析含 uwb/imu/audio/experiment）
│   ├── alert-engine.ts        # 规则告警状态机（8 条规则，去抖+滞回，纯逻辑）
│   ├── api.ts                 # REST 客户端（token 内存+AsyncStorage 双层）
│   ├── petgif.ts              # AI 形象服务（同步生成/日记/绑定，走 buildUploadForm）
│   ├── form-upload.ts         # ★ 跨端上传组装（Web=Blob / 原生={uri}，全部上传必须走它）
│   └── uwb-position.ts        # 三边定位（高斯-牛顿，当前无真实数据源）
├── src/hooks/  use-telemetry / use-alerts(共享单例) / use-auth(含DEV自动登录) / use-pet-action(行为推断) / use-uwb
├── server/
│   ├── server.py              # ★ 后端全量（腾讯云部署版，2300+ 行）
│   ├── seed-demo.py           # 演示数据种子（幂等，主账号 petsense/123456）
│   └── .gitignore             # *.db/uploads/cloud_data 等运行时产物不入库
├── firmware/petsense_esp32/   # ESP32 固件源码 + secrets 模板
├── tools/telemetry-check.cjs  # ★ 自检：解析器+告警状态机+UWB定位+Form上传 四组用例
└── doc/                       # 论文/proposal（gitignore，本地文档）
```

## 三、账号 / 地址 / 凭据速查

| 项             | 值                                                                                           |
| -------------- | -------------------------------------------------------------------------------------------- |
| **演示主账号** | `petsense / 123456`（Max 爸爸，全套内容，App 自动登录用）                                    |
| 管理员（空）   | `petadmin / petsense`                                                                        |
| 配角（发帖用） | `seed_doudou` / `seed_niangao` / `seed_xueqiu`，均 `demo123`                                 |
| 腾讯云         | `ssh ubuntu@81.71.71.31`（密码在 ~/.zcode/skills/cloud-ssh；sudo 提权）                      |
| 后端目录       | `/home/ubuntu/petsense-server/`（server.py + 两个 sqlite 库 + uploads/）                     |
| MQTT           | 同机 Mosquitto:1883，账号 `petsense-device`，密码文件 `/home/ubuntu/.petsense-mqtt-password` |
| PetGIF key     | `/home/ubuntu/.petgif-api-key`（本地 `~/.petgif-api-key`，勿入库）                           |
| App 遥测 WS    | `ws://81.71.71.31/ws`（`src/config.ts`）                                                     |
| App REST       | `http://81.71.71.31/api/app`（EXPO_PUBLIC_API_BASE_URL 覆盖）                                |

## 四、本地开发环境

```bash
# 后端（8080，跳过 MQTT；需 PYTHONPATH 指向 flask_sock 桩，见下）
cd server && PYTHONPATH=/tmp/petsense-backend/stubs PETSENSE_MQTT_DISABLED=1 python3 server.py
# 桩文件 /tmp/petsense-backend/stubs/flask_sock.py 是空实现（REST 调试不需要真 WS）——tmp 会丢，必要时报错重建

# Metro（App 端 env 是打包期注入，改 env 必须重启 Metro）
EXPO_PUBLIC_API_BASE_URL=http://81.71.71.31 EXPO_PUBLIC_DEV_AUTOLOGIN=1 npx expo start
#   回放模式加 EXPO_PUBLIC_TELEMETRY_MODE=replay（离线演示真实实验数据集）
#   指向本地后端用 http://192.168.1.5:8080（查本机 IP：ipconfig getifaddr en0）

# 模拟器（原生 dev client，已构建安装；ATS 已放行公网 http）
xcrun simctl launch booted com.yangeh03.petsense
xcrun simctl openurl booted "petsenseapp://community"   # 深链接直达页面（截图验证用）

# 手机：Expo Go 扫 exp://<电脑IP>:8081（同一 WiFi）；改代码后摇一摇→Reload

# 提交门禁（CI 拦截）
npm run lint && npm run typecheck && npm run format:check
node tools/telemetry-check.cjs        # 四组自检（解析/告警/UWB/Form上传）
npx expo export --platform web        # 构建验证
```

## 五、后端端点速查（server.py）

- **遥测**：`/ws`（App 实时流）、`/api/latest-full`（近 200 帧）、`/api/stats`
- **账号**：`/api/app/auth/{register,login,logout,me}`（PBKDF2-18 万轮+盐；header `X-Petsense-Session`）
- **宠物**：`GET/POST /api/app/pets`、`POST /pets/:id/petgif`（绑 AI 形象）、`POST /pets/:id/avatar-image`
- **看板**：`GET /api/app/dashboard?pet_id=`（体征+今日统计+petgif+洞察）
- **趋势**：`GET /api/app/trends?period=day|week|month|all`（cloud_data 历史 CSV 聚合 + readings 小时聚合，缓存 15min）
- **社区**：`/api/app/feed`、`POST /posts`（multipart：image 或 media_url+media_kind）、`DELETE /posts/:id`（仅作者）、`POST /posts/:id/like`、`GET/POST /posts/:id/comments`、`POST /users/:id/follow`、`/users/:id(/followers|/following)`（带 is_following）
- **PetGIF 代理**：`POST /petgif/pets/sync`（★同步生成，阻塞 65-150s 到角色卡就绪）、`GET /petgif/pet/:id`（10min 缓存）、`POST /petgif/pet/:id/diary`（200s 轮询，无照片自动用形象照兜底）、`POST /petgif/diary-post`（海报→社区帖）、floor-plan
- **统计钩子**：MQTT 每帧 → `update_daily_stats`（IMU 幅值派生睡眠/活跃秒数，北京时间日切）

**数据库（全 SQLite，无独立 DB 服务）**：`petsense_app.db`（账号/宠物/帖子/评论/点赞/关注/每日统计）、`data.db`（遥测环形 1 万条）、`cloud_data/`（实验 CSV/JSONL 604M）、`uploads/`（图片）。**均无备份机制**（待办）。

## 六、部署流程（改了 server.py 后）

```bash
# 1. 备份 + 上传 + 重启 + 验证
sshpass -p '...' ssh ubuntu@81.71.71.31 'cp /home/ubuntu/petsense-server/server.py /home/ubuntu/petsense-server/server.py.backup-$(date +%m%d%H%M)'
sshpass -p '...' scp server/server.py ubuntu@81.71.71.31:/tmp/s.py && \
sshpass -p '...' ssh ubuntu@81.71.71.31 'mv /tmp/s.py /home/ubuntu/petsense-server/server.py && sudo systemctl restart petsense && systemctl is-active petsense'
curl -s http://81.71.71.31/api/stats   # 冒烟
# 2. 演示数据（幂等）：python3 server/seed-demo.py http://81.71.71.31
```

## 七、踩坑记录（下个 session 必读，勿重蹈覆辙）

1. **iOS ATS**：公网明文 http 的图片/请求会被 iOS 拦截（本地 IP 豁免）。已设 `NSAllowsArbitraryLoads`（app.json+ios/Info.plist，上 https 后移除）。手机 Expo Go 上**帖子媒体必须走外部 https**（已把云端种子帖全部换成 qioyo.site 的 GIF）。
2. **Web 端 FormData**：浏览器只接受 Blob/File，`{uri,name,type}` part 必抛 `Unsupported FormDataPart implementation`。**所有上传必须走 `services/form-upload.ts`**，telemetry-check 里有自动用例把守。
3. **外部 PetGIF API 的三个坑**：宠物上传文件字段名是 `image`（不是 photo）；响应 `{pet:{id}}` 嵌套；日记必须 multipart+至少 1 张照片且全流程约 2 分钟（后端已兜底：无照片用形象照、200s 轮询、`{diary:{}}` 嵌套解包）。
4. **弹层布局**：modal 容器禁用 `alignItems:'center'` 包裹无定宽 ScrollView（宽度收缩→贴边+可横向拖动）。正确模式见 create.tsx/login.tsx：`form: width '100%' + maxWidth + alignSelf center`，控件显式定宽。
5. **Metro 缓存**：改 env/删路由后必须杀掉 Metro（`lsof -ti:8081 | xargs kill -9`）重启；热更不生效先怀疑缓存。8081 被占会静默失败（"Port in another window"）。
6. **并行会话事故**：曾发生另一 session 把全 App 英文化覆盖本会话工作。**同一时间只跑一个会话；每完成一块立即 commit**。
7. **模拟器时区是 UTC**（与 Mac 不同）——时段相关逻辑（问候语/夜间睡眠兜底）在模拟器上"像凌晨"是正常的。
8. **curl 走系统代理**会 502，本地调试加 `--noproxy '*'`。
9. expo-image 原生支持 GIF（autoplay）；expo-symbols 图标名非法会被 typecheck 拦（name 传 `{ios,android,web}` 三端映射）。
10. RN 新版 hooks 规则严格：渲染期 `Date.now()`、effect 内同步 setState、渲染期读写 ref 都会被 lint 拦——写法参考现有代码（tick state / promise 链 / useSyncExternalStore / PanResponder 文件级豁免）。

## 八、已知问题与待办

- [ ] **HTTPS**：后端是明文 http（ATS 已豁免、手机 Expo Go 部分媒体走外部 https 规避）。上线前配域名+TLS，随后移除 arbitrary loads。
- [ ] **数据库无备份**：云端两库 + uploads + cloud_data（604M）。建议 cron 每日打包。
- [ ] 遥测：真设备离线时体征显示 `--`（诚实空态）；`src/data/replay-dataset.ts` 回放模式可离线演示。云端设备 topic=`petsense/sensor/esp32-001`。
- [ ] UWB 定位：解析/三边定位已就绪（含单测），但固件未接入、无真实数据——tracking 页跑的是 demo 移动引擎（已标注）。
- [ ] 日记海报帖存在云端 /uploads（http），手机 Expo Go 上看不到图（电脑/模拟器可见）；可改为直链外部 https。
- [ ] 每日活跃/睡眠统计从部署时刻起累积（daily_stats 当前 0 天）。
- [ ] `CharacterCardModal`（Profile ✨）与 create.tsx 流程共用同步生成；生成期 1 分钟不可取消（可接受，后续可改异步通知）。
- [ ] doc/ 目录（论文）在 gitignore，勿提交。

## 九、验证当前一切正常（新 session 开工自检）

```bash
node tools/telemetry-check.cjs && npm run typecheck && npm run lint
curl -s http://81.71.71.31/api/stats                    # 后端活着
curl -s "http://81.71.71.31/api/app/trends?period=all"  # 趋势有 4 个实验日
# 模拟器：dashboard Hero 播动作 GIF + 社区 5 篇 GIF 帖 + 生成运动日记（约 2 分钟出海报）
```

## 十、近期提交索引（语义速查）

| 提交                        | 内容                                                      |
| --------------------------- | --------------------------------------------------------- |
| 513ee3d                     | Web 端 FormData 上传统一修复（form-upload.ts + 自检用例） |
| 8035426                     | 演示账号更名 petsense/123456                              |
| a740f37 / e52551e / bb37643 | 登录报错提示 / login+create 弹层布局重写                  |
| 239427f                     | ATS 放行 + 我的页边距 + 云端重播种 https GIF 帖           |
| b25a9ce                     | 运动日记三层根因修复（格式/照片/嵌套/超时）               |
| f815b0c                     | AI 数字形象闭环（同步生成 + Hero 实时动作）               |
| c68d6e7                     | PetGIF 集成主体（趋势统计/运动日记/角色卡/GIF 帖）        |
| 58fcace / ddee7d9           | 社区功能闭环 + ins 风格化                                 |
| 29de6c4                     | 五 Tab 全栈改造（结构基线，更早细节见 git log）           |
