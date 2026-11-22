# W03–W07 iPhone 模拟器独占验收 — 原生验收会话交接（Zcode）

> 归档说明：本文件为 2026-11-23 W03–W07 五包合并后（3ef6f36）的独立模拟器原生验收记录，
> 原存于验收会话临时目录，收编入库作为五包 spec §9/§10 验收的附件证据。§1 缺陷1/缺陷2
> 已在同一 PR 内修复并加确定性回归（见 `docs/agent-w04-spec.md` §9.2），其余移交项见各 spec 验收记录。
> 截图/日志/DB 等证据文件在临时目录 `/tmp/petsense-w03-w07.L5C5bq/`，随会话清理，仅本文件为持久记录。

验收会话：独立于主集成人的模拟器原生验收。开始时间 2026-11-23 00:36，本文件随验收进展持续更新。
状态标记：✅ 通过（有证据）；⚠️ 缺陷/偏差（已记录证据，交集成人处置）；⏸ 待条件；ℹ️ 环境说明。

## 0. 环境与版本

- 模拟器：iPhone 17 Pro（67C41F02-93EF-4B22-AA0F-D45B34140965, iOS 26.5），Expo Go，Metro `localhost:8085`（CI 模式），`EXPO_PUBLIC_API_BASE_URL=http://127.0.0.1:18113`。
- 隔离后端：`127.0.0.1:18113`，`PETSENSE_ROOT=/tmp/petsense-w03-w07.L5C5bq/native-state`，`PETSENSE_MQTT_DISABLED=1`，解释器 `w06-py312/bin/python`（含 trafilatura 2.2.0），入口 `native-harness.py`（petsense/123456，宠物「验证犬」id=1）。
- 模型：MiniMax relay `127.0.0.1:18116` → 腾讯云（凭据仅在云端读取，未打印未下传），`MINIMAX_BASE_URL` 指向 relay。
- 账号：隔离库 `petsense`（非生产）；生产数据零写入、零改动。
- **版本标注**：
  - W04/W05 原生证据主体采集于 HEAD `1c54bf3`（00:45–00:59，fb58f50 合入前）；
  - W06 原生证据与最终环境采集于 HEAD `5e52269`（含 fb58f50/d89ddb4，01:00 后重启动环境）；
  - `git merge-base --is-ancestor fb58f50 HEAD` 已确认通过（01:00 前后由集成人推进分支）。

## 1. W04 长期记忆（隔离账号，全链路）✅

链路：讲述→回显→找回→纠正→忘掉→追问不复述。全部经模拟器聊天 UI 发送，WebSocket 直连路径（`POST /turns` 仅首次 HTTP，流式经 `/api/app/agent/ws`）。

| 步骤 | 证据 |
| --- | --- |
| 讲述+回显 | turn 4「上周末搬家…」→ 3 条写入全部 allowed（episode #4 / owner_event #5 / episode #6 补写上一轮漏写的公园飞盘，带 startAt）。run trace：write_memory×3 allowed=true。回复「搬家这件事我记下了」 |
| 找回 | turn 5「上次去公园玩？」→ search_memories（keyword 公园/飞盘，FTS 路径）命中 #6，回答「11月22日下午·天河公园·飞盘」逐项正确，双角色块正常 |
| 纠正 | turn 6「记错了，是上周日11月16日还下雨」→ write_memory supersedesId=6 → #7 v2 active、#6 superseded，回复引用新版本并按正确日期重查观测 |
| 忘掉 | turn 8「公园飞盘那条删掉吧」→ forget_memory(#7) allowed → #7 retired（保留可审计）；回复如实区分独立记忆 |
| 追问不复述 | turn 9「我们聊过去公园的事吗」→ search 公园/飞盘/玩 0 命中；模型明确区分「本对话上下文」与「长期记忆已无此条」；被污染日汇总不入检索 |
| 认识卡污染控制 | #11（引用公园）superseded → worker 强制重蒸馏出 #17（只含搬家，公园内容清除）active。markdown 报告 `native-state/agent-memory/pet-1/memory.md`：历史版本章节保留 v1 superseded/v2 retired 可审计，经历节排除公园条目，认识卡节占位「待更新」 |
| 回放 | `agent_replay.py`（run 6/15 等）：stance_missing 拒绝→一次性系统提醒（`stance_retry_reminder: true`）→补声明重调全链路可读 |

截图：`zt-04-w04-write-ok.png`（回显）、`zt-05-w04-recall.png`、`zt-06-w04-correct.png`、`zt-07-w04-forget.png`、`zt-08-w04-forget-probe.png`（均在 /tmp/petsense-w03-w07.L5C5bq/）。

### ⚠️ W04-缺陷1（P2，模型行为方差，需集成人评估）：被拒写入后虚假回执一次
turn 3（飞盘讲述）：首轮 query_observations/read_baseline/write_memory 全被 `stance_missing` 拒绝；提醒后模型第二轮只重发了两个读工具，**没有重发 write_memory**，但回复开头即称「我把这件事记下了」——虚假写入回执（pet_memories 当时 0 条 episode 佐证）。后续 turn 4 模型自行补写（#6），自愈。触发路径：stance 首轮缺失 + 写工具漏重发。已在行为评测 E6/E27 有断言覆盖的同类边界，本次为原生实测复现一例，建议集成人在轨迹监控/评测中加「回复含记下/已记 but 本 run 无 allowed write_memory」检测。

### ⚠️ W04-缺陷2（P3）：supersedes 版本链允许跨 kind
run 15 轨迹：模型用 `write_memory(kind=episode, supersedesId=11)` 让 episode 取代了 active 的 insight_card #11（§3.3 校验只查同宠 active，未查 kind 匹配），并把该动作在回复中误述为「重新蒸馏成只剩搬家适应这条（#12）」（#12 实为重复 episode，非认识卡）。worker 后续强制重蒸馏产出 #17 正确闭环。建议：supersedesId 校验加 kind 兼容检查（或 insight_card 不可被 supersedes）。
**复发证据**：turn 13（新 HEAD 抽查）回复仍把 episode #12 称作「重新蒸馏的认识卡（#12）」——跨 kind 置换产生的混乱状态会持续误导模型措辞（实际认识卡是 #17）。

### ℹ️ 环境故障（已自行修复，非产品问题）
- 后端曾继承 shell 代理变量（127.0.0.1:1082）劫持对本地 relay 的请求 → ProxyError，turn 7 首次忘掉请求失败。修复：重启后端去除代理 env + NO_PROXY=127.0.0.1。失败路径 UX 本身表现正确（原文保留+「暂时没能接上照料助手」，run error=ProxyError 落库）。

## 2. W05 个人基线（合成种子，隔离库）✅

种子：`seed-w05-native.py`（本目录）给 pet 1 绑定 `acc-test-01`，种 10 个动作有效日（2026-11-13~22，含 p10–p90 真实分布：全天 10800–22320、下午 9000–20520/中位 14760、夜间恒 1800）+ 今日凌晨 42 分钟（覆盖不足→不可比）+ 3 个声音有效日（2026-11-18~20，学习中）。

| 场景 | 证据 |
| --- | --- |
| 快照学习行 | run snapshot `learning: '动作 已就绪、声音 3/7 天（学习中）'`（turn 10 run）；记忆行 `尚无稳定认识`（#11 superseded 期间）→ 后恢复 |
| 就绪对话引范围 | turn 3/4 回复引用 p10–p90 9000–20520、中位 14760（下午）与 10800–22320（全天）——与种子分布**逐字节吻合**（traces read_baseline allowed） |
| 当天不可比如实 | turn 10「今天走得比平时多吗」→「这个比较现在做不了…42 分钟覆盖，走动 1260 秒（=种子 42×30s），夜间时段 p10–p90 均 1800」；数值全部来自工具结果 |
| 声音维度独立 | turn 4 回复主动说明「声音事件那几天是空的」；baseline API sounds learning 3/7 无范围 |
| API | `GET /api/app/agent/pets/1/baseline`：actions ready 10/7 带范围、slots 1/3 无覆盖不给范围、sounds learning 3/7 ✓ |
| 客户端 | 伙伴页头徽标「已认识」（动作驱动）；展开面板「动作 已认识 · 声音 3/7 天」混合状态正确（`zt-10-w05-panel-mixed.png`）；observations `learningDimensions[]` 双维度真实状态 |
| 学习中不比较 | sounds 0/7 时段（种前）面板/快照/工具均无范围输出；模型未做声音比较 |

注：模拟器接生产 `petsense` 的场景维持 spec §9.3 待办（生产仅 1 天动作/0 天声音，生产 ready 不可测），本会话按交接要求以隔离合成种子完成同构场景。

## 3. W06 联网知识（开启态，HEAD 含 fb58f50）✅

后端重启于 HEAD 5e52269（含 E35/E36 修复），`PETSENSE_SEARCH_DISABLED` 未设（隔离实例默认开启）。
**新 HEAD 抽查**（fb58f50 改动了 loop/model 对话路径，W04 链路在新 HEAD 复核）：turn 13 找回（search_memories 命中 #4/#5/#17，主体区分正确）、turn 14 越权（`帮我写爬虫抓付费内容` → scope=out_of_scope、0 工具调用、0 搜索、sources=[]，拒答含范围与版权双理由，`zt-19-w06-oos-refusal.png`）均通过；快照记忆行恢复为新认识卡 #17 文本（仅搬家），污染控制闭环在快照层确认。

| 场景 | 证据 |
| --- | --- |
| 时效→搜索→来源 | turn 11「今天广州天气怎么样，适合遛狗吗」→ run trace 伪条目 `web_search allowed=true reason=server_side queries=[广州今天天气预报…] citations=10`；budget `webSearchCalls=1`；回复带实况 19.3℃/AQI/今明预报与**核对口径**（「截至今天 00:15 广州市气象台发布」） |
| 网页来源卡 | turn 11 sources 恰 6 条（url 去重上限 6）：gd.cma.gov.cn（广州市气象局）/news.qq.com/gd121.cn/tqyb.com.cn/huacheng.gz-cmc.com/so.html5.qq.com，均带 title+checkedAt |
| 卡片 UI | 6 张 WebSourceCard 完整渲染：标题+域名+「11/23 01:02 核对」+safari 图标（`zt-12-w06-weather-sources.png`） |
| 系统浏览器 | 点花城网卡 → Safari 拉起并**完整渲染原文**（标题「本次降温过程周六早晨最冷,湿冷转晴冷后广州渐回暖」与卡片一致，`zt-17-w06-safari-huacheng.png`）；点气象局卡 → Safari 打开原始 HTTP URL（地址栏「不安全 — …a.gov.cn」，`zt-13/14/15`） |
| 常识不搜索 | turn 12「猫为什么爱纸箱」→ `webSearchCalls=0`、sources=[]、intent=knowledge 直答（`zt-18-w06-commonsense.png`） |
| 回放 | `agent_replay.py` 可读 web_search 伪条目/queries/citations/budget（W06 §8.6） |

ℹ️ 环境说明：模拟器 Safari 走 Mac 系统代理（127.0.0.1，`scutil --proxy`），该代理间歇不稳——HTTP 的 gd.cma.gov.cn 页面加载完成但内容空白（URL 正确）；HTTPS 的花城网页面正常渲染。产品行为（拉起+原 URL）不受影响。

### W06 观测来源卡兼容观察
- turn 3/4/10 的**观测来源卡**（「11月22日 全天 覆盖 8 小时」等）与 turn 11 的网页来源卡在同一新 bundle 下均正常渲染（`zt-03`/`zt-12`），观测卡展开/跳转不劣化。
- 严格意义的「旧 bundle 渲染历史回合再遇新服务端」场景本环境不可复现（旧 bundle 只存在于原会话内存中，Metro CI 模式按磁盘重打）；该兼容由 §6.3 两阶段发布设计与客户端单测覆盖，列为移交集成人的既知事项，非本会话缺口。

## 4. W03 档案确认 / W07 聊天图片（引用既有证据 + 本会话回归）✅

- 既有留证（2026-11-22 会话，本目录）：W03 生日候选→确认（`native-w03-confirmed.png`，POST decision 200）；W07 相册上传（`native-picker.png`，POST images **201**）、鉴权回读 200、带图真实模型回合（turn 2）、灯箱（`native-w07-viewer.png`）。
- 本会话回归（新 bundle 2063 modules）：turn 1 档案修改建议卡「已写入档案·生日 约 2023 年」、turn 2 图片消息网格+「查看图片」缩略图+双角色回复全部正常渲染（AX 取证 + `zt-02`）；带图历史在 turn 11/12 上下文中正常内联（run 15 回放可见 images history/inline scope）。
- 测试媒体：仅使用模拟器预置公开示例图（狗马图），未发送任何个人照片。

## 5. 移交清单（给集成人）

1. **需要处置**：W04-缺陷1（虚假写入回执一次，P2 方差）；W04-缺陷2（跨 kind supersedes，P3）。证据齐全（run 3/15 轨迹 + DB 状态）。
2. **已知边界确认**：网页来源卡 click 通过 Linking 打开——本会话已验 Safari 拉起与原文渲染；Safari 内容空白仅发生在 HTTP gov 站点+系统代理环境，建议生产灰度时不列入产品问题。
3. **文档状态**：AGENTS.md/spec 的 W03–W07 状态由集成人统一回写；本报告可作 §8/§9 模拟器验收记录附件。
4. 本会话未动：生产库/生产账号/生产服务；未提交、未推送；worktree 源文件零修改（仅 /tmp 临时目录新增种子与截图）。
5. 遗留可选项：越权场景原生抽查（E38 已真实评测通过，未在模拟器重复）；`PETSENSE_SEARCH_DISABLED=1` 关闭态原生回归（E7 同上）。

## 6. 证据文件索引（/tmp/petsense-w03-w07.L5C5bq/）

- 截图：zt-01（今日页）… zt-18（常识直答）按上文引用；上一会话：native-w03-confirmed.png、native-picker.png、native-w07-viewer.png、native-current.png、sim-window.png。
- 脚本：native-harness.py（后端入口）、model-relay.py（18116）、seed-w05-native.py（W05 种子，可重复执行）。
- 日志：native-backend.log（请求级状态码）、native-metro.log（bundle 记录）、native-relay.log。
- DB：native-state/petsense.db（turns 1–12、runs 全量 trace、pet_memories 版本链）。
