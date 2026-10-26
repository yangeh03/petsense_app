# PetSense Community Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Track each checkbox as work is verified.

**Goal:** Deliver the public pet community described in the companion spec, including complete post, relationship, interaction, search, story, nearby, and notification flows. Exclude governance, private messages, and group chats.

**Architecture:** Evolve the Flask social domain with additive Alembic migrations and stable v1 compatibility; expose v2 endpoints with explicit, idempotent write semantics. Build Expo screens on real server data and keep shared entities synchronized after mutations. Use foreground location only and store deliberately coarse discovery coordinates.

**Tech Stack:** Flask, SQLAlchemy, Alembic, SQLite/PostgreSQL; Expo SDK 57, React Native 0.86, TypeScript strict, expo-router.

**Spec:** `docs/superpowers/specs/2026-10-26-community-upgrade-design.md`

## 进度状态（2026-10-27 更新）

Codex 完成了 Task 1–9 的绝大部分代码后因额度中断，未提交、未验证。之后的接手工作：

- 全部代码已提交到 `codex/community-upgrade`；服务端 155 个单测、客户端 lint / typecheck / format / 44 个测试、Web 构建全部通过。
- 用隔离服务端 + Expo Go 模拟器做了完整的端到端验收（Feed、点赞、收藏、两层评论、关注、通知与评论锚点、故事查看与发布、多图发帖、详情、编辑、删除、搜索三分类、地点筛选、收藏列表、公开资料编辑、附近宠友/动态、被发现开关）。验收中发现并修复了约 20 个问题，详见 git log 中 `fix:` 开头的提交。
- Task 1–4、6–9 视为完成；Task 5 的服务端搜索与地点聚合完成，但 **Place/Topic 正规实体未建**（地点仍是 `location_text` 聚合）；Task 10 的独立代码审查未做。

**仍未完成（设计明确要求）：**

1. PetGIF 日记发布路径（`petgif/proxy.py`）仍直接创建 Post，未统一到带受众/幂等键/PostMedia/通知事件的社区写入路径。
2. Place / Topic 正规实体与话题页。
3. 逐张上传进度与单张重试（当前一次 multipart 全部提交）。
4. 点赞通知按帖子/时间窗跨用户聚合（当前只做同人同帖去重）。
5. 搜索输入法 composing 态处理；定位拒绝时的"同城模式"兜底。
6. 附近查询频控；旧帖 `image_path` 仍走公开 `/uploads/`（有"legacy 媒体禁止改为仅粉丝"守卫补偿）。
7. 正式附近功能的 PostgreSQL + PostGIS 路径（当前纯 haversine，符合首期设计）。

**环境注意：** `expo-location` 与 `expo-image-manipulator` 是本分支新增的原生模块，Expo Go 自带可直接跑；现有 `com.yangeh03.petsense` 开发包是旧构建，需要重新 `expo run:ios` 才包含它们。

## Global Constraints

- Do not alter the dirty original checkout or push to main. All code goes on `codex/community-upgrade` in this worktree.
- No community governance, reports, bans, moderation, blocking, private messaging, or group chat.
- Preserve v1 callers and PetGIF publishing; all visible content uses real data.
- Read exact Expo 57 documentation before adding native dependencies or using their APIs.
- For each behavior, write an integration/regression test that fails first, implement, and run it green. Run the complete mandated suites at completion.

## Review Focus

- Data access and audience rules through every list/detail/search/story/notification path.
- Stable pagination amid insertion/deletion, idempotent retry and count consistency.
- Upload validity, ownership, partial failure, and data lifecycle.
- Permission refusal and stale location; no precise coordinate disclosure.
- Cross-account stale responses and native/Web behavior.

---

## Task 1 — Compatibility baseline and immediate UX repairs

- [ ] Add failing tests for missing-target like, delete confirmation, comment failure/success-refresh-failure, and follow-list entry.
- [ ] Fix v1 social errors; add `PUT/DELETE` CORS support, profile follow-list rendering, delete confirmation, comment error states, and remove false success actions.
- [ ] Run focused tests and commit.

## Task 2 — Schema and shared public policy

- [ ] Add migration/model tests for post state/audience/version, comments reply/tombstone/idempotency, bookmarks, stories/views, notifications/outbox, topics/places, and discovery locations.
- [ ] Implement additive migration and shared public DTO/visibility service; strip device/telemetry fields from public pets.
- [ ] Verify old rows and legacy endpoints, then commit.

## Task 3 — Posts, media, and feed contract

- [ ] Add API tests for text-only, multiple images, corrupt media, upload retry, post edit/delete, audience, detail, discover/following feeds, and 0/65/1000 row cursor cases.
- [ ] Implement shared post service, media validation, mutation keys, detail/edit/delete, and keyset feed, retaining v1 adapters.
- [ ] Verify PetGIF publishing, migration, and feed queries; commit.

## Task 4 — Comments, reactions, follows, and bookmarks

- [ ] Add tests for two-level reply rules, pagination, author deletion, tombstones, repeated mutations, missing/deleted posts, and follower permissions.
- [ ] Implement idempotent PUT/DELETE reactions/relations/bookmarks and paged comment/reply APIs; preserve v1 where needed.
- [ ] Verify counts and concurrent/repeated operations; commit.

## Task 5 — Search, places, and topics

- [ ] Add tests for Chinese two-character matches, user/post/place/topic categories, query escaping, audience filtering, pagination, and stale index behavior.
- [ ] Implement server search and canonical place/topic associations; connect search UI with clear/cancel/category states.
- [ ] Verify search against rows beyond the old 60-post feed cap; commit.

## Task 6 — Persistent notifications

- [ ] Add tests for event deduplication, recipient rules, retries, unread counts, read-through high water mark, and unavailable targets.
- [ ] Implement transaction outbox, worker retry, notification inbox APIs, and an actual client inbox.
- [ ] Verify no duplicate/self notifications and commit.

## Task 7 — Stories

- [ ] Add tests for create/view/delete, 24-hour server expiry, audience, viewer idempotency, and read status.
- [ ] Implement story media lifecycle, tray, viewer, progress/pause, and explicit story creation entry; remove post-derived decorative rings.
- [ ] Verify foreground/background and expired content states; commit.

## Task 8 — Nearby discovery

- [ ] Add tests for location permission contract, coordinate/accuracy checks, coarse storage, TTL, opt-in/off, radius and stable paging.
- [ ] Implement foreground location flow, privacy controls, nearby users/posts service and UI with honest fallback states.
- [ ] Verify no precise coordinate leak and deletion/expiry mid-pagination; commit.

## Task 9 — Client post flows and unified state

- [ ] Add tests for API pagination and mutation state transitions, stale account response, comment retry, and draft persistence.
- [ ] Implement feed lists, detail/image viewer, composer camera/album/multi-image/text/draft, reliable interaction states, follow lists and profile entry points.
- [ ] Run client checks and commit.

## Task 10 — Integration and release verification

- [ ] Run all app and server checks, migration upgrade from existing schema, isolated API E2E, Web build, and available iOS/Android smoke checks.
- [ ] Fix concrete failures; review privacy and compatibility; verify clean worktree and produce a final commit.
- [ ] Request independent code review as required by executing-plans, resolve findings, and report verified limitations accurately.
