# 社区写入的乐观上屏（2026-11-24）

## 背景

社区评论/回复、发帖、发故事三处都是「等响应回来才反映」的写法：用户在社区页点发送或发布，界面要等服务端往返完才出现内容。发帖还要等整段图片上传，用户看到的是「点了没反应」。

伙伴页的同类缺陷已在 [`agent-w01-spec.md`](agent-w01-spec.md) 的「2026-11-24 修复：发送后主人的话没有立刻上屏」记录。本轮把同一套「先上屏、后确认」的写法补到社区三处。

服务端接口与契约不变：发帖/发故事仍是 multipart POST，评论仍是 JSON POST，**请求体里的 `clientMutationId` / `client_mutation_id` 幂等键照旧传**，服务端按 `(作者, 幂等键)` 去重并在重复提交时回放原记录。

## 改动

### 评论 / 回复

`app/src/components/social/comment-sheet.tsx`

- 点发送先插入一条本地评论（负 id、`PendingComment`），回复按服务端同口径归桶（`target.root_id ?? target.id`，即挂在根评论下）；父级评论数同做乐观 +1。
- 服务端返回后按 id 就地替换本地评论，不产生第二条。
- 失败则撤下本地评论，把原话与回复对象回填输入区，评论数回退（`onCommented(post.id, -1)`）。
- 待确认的评论显示「发送中…」，并隐藏回复/删除操作。

### 发帖 / 发故事

新增：

- `app/src/stores/social-pending.ts`：内存 zustand store（`PendingPost` / `PendingStory`，状态 `publishing | confirmed | failed`，负 id、`serverId`、`mutationId`，`resolvePosts` / `resolveStories`）。
- `app/src/services/social-publish.ts`：`buildLocalPost` / `buildLocalStory`、`stagePost` / `stageStory`、`publishPost` / `publishStory`（后台上传，成功记 `serverId` 并把 `post.id` 换成服务端 id，失败落 `failed` + 原因）、`retryPost` / `retryStory`（复用同一幂等键）。

改动：

- `app/src/app/create.tsx`：点「分享」即 `stagePost` + `router.back()`，上传在后台跑；草稿清理延后到确认成功时执行。
- `app/src/app/story-create.tsx`：同样改为 `stageStory` + 返回 + 后台 `publishStory`。
- `app/src/app/(tabs)/community.tsx`：本地占位帖子并入列表顶部，服务端确认后触发一次列表刷新；本地故事并入故事环「自己」分组。
- `app/src/app/story.tsx`：合并本地占位故事（本地图预览），本地故事不报已读、隐藏删除，显示「正在发布…」。
- `app/src/components/social/post-card.tsx`：新增 `pending?: 'publishing' | 'syncing' | 'failed'` 与 `onRetryPending`，显示「正在发布… / 发布成功，正在同步… / 发布失败，点此重试」，未确认时点赞/评论/收藏禁用。
- `app/src/services/api.ts`：`socialImageSource` 对 `file:` / `content:` / `ph:` / `assets-library:` / `data:` 这类本地 uri 直通（不再拼服务端域名），本机预览必需。

占位只存内存：进程重启后以服务端结果为准；发布页草稿另有 AsyncStorage 兜底。

## 验收（iOS 模拟器，隔离后端）

按 AGENTS.md 的约定，写入类验收走隔离后端（`PETSENSE_ROOT=/tmp/petsense_iso_w15`，账号 `petsense` / `123456`），生产宠物资料未改动。为了能看清「乐观」窗口，用一个临时转发代理给相关接口加延迟或制造 500。

| 场景 | 期望 | 实测 |
| --- | --- | --- |
| 发帖 | 点分享即上屏，「正在发布…」，未确认时点赞/评论/收藏禁用 | 点分享后立即回到社区页，帖子置顶显示「正在发布，稍后会自动出现在这里」+「正在发布…」，三个动作按钮为禁用态 |
| 发帖（服务端确认） | 就地替换，不重复 | 约 8 秒后服务端 201（`POST /api/app/posts`），卡片换成正式帖子（服务端 id 4），「正在发布…」消失、出现「更多操作」 |
| 发帖失败 | 显示「发布失败，点此重试」，重试成功 | 点重试后服务端 201，帖子落库并换成服务端 id |
| 发评论 | 点发送即上屏，「发送中…」 | 1.2 秒内气泡出现并显示「发送中…」，按钮置「发送中」 |
| 发评论（服务端确认） | 就地替换，不重复 | 约 6 秒后换成服务端记录（时间戳 14:26:32），恢复「回复 / 删除」，评论区只有一条 |
| 发回复 | 挂在根评论下并即时上屏 | 回复缩进在根评论下，带「发送中…」 |
| 发回复（服务端确认） | 就地替换 | 换成服务端记录（时间戳 14:27:15），动作区恢复 |
| 评论失败（500） | 撤下本地评论 + 回填草稿 + 计数回退 | 气泡撤下、错误提示「模拟的评论失败」、原话「这条会失败」回到输入区、发送按钮恢复；帖子评论数仍是 1（未多出 2） |
| 发故事 | 点发布即上屏，「正在发布…」 | 发布后立即回到社区页，故事环「自己」分组出现本地故事；打开故事页显示「正在发布…」（此时无「查看者 / 删除」） |
| 发故事（服务端确认） | 换成正式故事 | 约 15 秒后服务端 201（`POST /api/app/stories`），故事页恢复「查看者 / 删除」，服务端 `GET /api/app/users/1/stories` 两条记录与两次发布一一对应，无重复 |

单测：`app/tools/social-pending.test.cjs`（3 项：本地帖子构造 + 确认换 id、失败可重试且复用 mutationId、`resolvePosts` 只撤已收录的）。

校验：`npm run lint` / `npm run typecheck` / `npm run format:check` / `npm test`（197/197）全绿；本轮没有服务端改动，`python -m unittest discover -s server/tests` 不涉及。

### 复现隔离环境（供复验）

```bash
# 隔离后端（临时数据目录，不碰生产）
cd server && PETSENSE_ROOT=/tmp/petsense_iso_w15 PETSENSE_MQTT_DISABLED=1 \
  PETSENSE_SCHEDULER_DISABLED=1 python3 wsgi.py          # :8080
# 注册同名账号 petsense/123456，再 POST /api/app/pets 建一只宠物
# Metro 指向隔离后端
cd app && EXPO_PUBLIC_API_BASE_URL=http://localhost:8080 npx expo start --port 8202
```

需要看清「乐观」窗口时，在隔离后端前面挂一个转发代理给对应接口加延迟或制造 500 即可（验收时用过：帖子 +8 秒、评论 +6 秒/500、故事 +15 秒）。

## 约定

- 乐观占位用**负 id**，与服务端自增正 id 不撞号；列表排序统一走统一的排序入口，服务端回合/帖子按 id 正序，本地占位排最后。
- 重试必须**复用同一 `clientMutationId`**，否则会重复落库（服务端去重键是 `(user_id, client_mutation_id)`）。
- 新增写操作若要上屏，先想清楚三件事：占位长什么样、服务端确认后怎么就地替换、失败后怎么回填或回退。
