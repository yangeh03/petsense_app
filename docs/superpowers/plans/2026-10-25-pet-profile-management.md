# Pet Profile Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver persistent single-pet profile viewing/editing with consistent names and avatars across the app.
**Architecture:** Owner-scoped Flask GET/PATCH endpoints persist existing Pet fields and optional validated photo in one save. An Expo Router profile screen owns its draft; account-scoped invalidation refreshes existing consumers after successful mutations.
**Tech Stack:** Python 3.12, Flask/SQLAlchemy, Pillow, Expo SDK 57, React Native, TypeScript.
**Spec:** docs/superpowers/specs/2026-10-25-pet-profile-management-design.md

## Global Constraints
- One account, one pet, one device; no deletion or AI regeneration.
- Name 1–20 Unicode code points after trimming; optional breed up to 40; species dog/cat/rabbit.
- Photos JPEG/PNG/WebP, maximum 10MB, validate actual decoding; no file writes before ownership/field validation.
- Existing theme and SymbolView mappings; exact Expo SDK 57 docs read before product code.
- Photo save and basic fields atomic; failure keeps draft, cancellation does not upload.
- User approved design and explicitly requested implementation; execute inline without additional handoff approval.

## Review Focus
- Lost save response: retry is safe and draft remains available.
- Stale fetch and account changes: old requests cannot overwrite new account/profile data.
- Modal swipe, Android Back and Web navigation: unsaved changes receive the same guard.
- Uploaded photo versus existing GIF: saved photo takes precedence, default avatar behavior is explicit.
- Invalid/corrupt/oversized image: failure neither changes fields nor leaves a newly written orphan.

### Task 1: Owner-scoped persistence
**Files:** server/petsense/pets/routes.py; new server/petsense/pets/profile.py; server/petsense/social/routes.py; server/tests/test_pets.py; server/requirements.txt and requirements.lock.
**Interfaces:** GET/PATCH /pets/<id> -> {pet: SocialPet}; PATCH accepts JSON or multipart fields name/species/breed/avatarEmoji/avatarMode with optional photo. Modes keep/photo/emoji. Public serializer excludes user_id and created_at as before.
- [x] Configure isolated Python 3.12 dependencies and run baseline pets tests.
- [x] Add failing tests for Chinese rename, Unicode boundaries, strict types/unknown fields, login/ownership, unchanged device, photo keep/replace/remove and atomic failure, public/dashboard consistency.
- [x] Implement common field validation, decoded image validation and transactional persistence; reuse serializer for public profile without adding private fields.
- [x] Run targeted tests then complete server suite; commit verified service change.

### Task 2: Profile domain and invalidation
**Files:** new app/src/services/pet-profile.ts and pet-profile-events.ts; app/src/services/api.ts; app/src/types/social.ts; new app/tools/pet-profile.test.cjs; app/src/hooks/use-auth.tsx.
**Interfaces:** PetDraft from SocialPet; validatePetDraft(draft), isPetDraftDirty(pet,draft), buildPetUpdate(draft), speciesLabel(species); subscribePetChanges(userId,listener), notifyPetChanged(userId), use hook wrapper in hooks/use-pet-changes.ts.
- [x] Add failing Node tests for Unicode, preserved photos, default/photo modes, trimmed dirty state and account-isolated notifications.
- [x] Implement pure draft helpers and account-scoped notification subscriptions; API fetchPet/updatePet preserve multipart support and notify on success only.
- [x] Run npm tests and typecheck; commit tested domain/API change.

### Task 3: Complete profile interface and entry points
**Files:** new app/src/app/pet.tsx and components/pets/pet-profile-form.tsx; app/src/app/_layout.tsx; (tabs)/profile.tsx, index.tsx, community.tsx; app/src/app/create.tsx, setup.tsx, user.tsx; app/src/components/device-card.tsx.
**Interfaces:** /pet owns load/save/draft and unsaved navigation guard; form receives draft/change/errors/busy callbacks. Existing consumers reload on focus and account-scoped changes with cancellation/sequence protection.
- [x] Implement read/edit/loading/error/empty states, local image preview, field errors, save retry and success, cancel and all navigation guards.
- [x] Connect profile card, home avatar and create entry; preserve setup for first creation/device pairing; notify after setup/device mutations.
- [x] Fix shared avatar/name rendering and refresh consumers including feed and item selection; prevent old account responses.
- [x] Run lint/typecheck/format:check/npm tests/Web export; resolve actual diagnostics; commit.

### Task 4: End-to-end verification and review
**Files:** docs/superpowers/specs/2026-10-25-pet-profile-management-design.md and verification report.
- [ ] Launch isolated local Flask+Expo with disposable account; computer use exercises create/view/Chinese rename/cancel/invalid input/photo cancel/save/refresh/navigation guards and persistence.（接口层已过，见下方验证记录；UI 层走查被中止）
- [ ] Verify narrow viewport and dark mode, record environment limitations honestly.（未执行）
- [x] Run full required checks and fresh whole-branch review; fix important findings with regression tests, document results, commit. Leave branch/worktree available for review; no merge or deployment requested.

## 验证记录（2026-10-25 收尾会话）

- 质量门禁（rebase 到 main dea82cc 后全部通过）：server unittest 83/83（Python 3.12 venv）、app `npm run typecheck` / `lint` / `format:check`、node --test 23/23、`expo export --platform web` 成功。
- 整分支 review 发现一处问题并修复：`fetch` 网络错误（`TypeError: Failed to fetch`）会把英文原文展示给用户，已本地化为中文提示并补测试（`fix: localize network failures when saving pet profile`）。
- 接口层端到端（隔离 `PETSENSE_ROOT` 起真实 Flask + curl，一次性账号）：注册/建档、GET/PATCH、中文改名+改物种、21 字名字 400、禁止字段 400、未登录 401、他人档案 404、multipart 照片上传（3000×2000 JPEG → 服务端解码压缩为 512 PNG，`/uploads/` 可访问）、伪造图片 400 且无孤儿文件、切回 Emoji 清除照片引用——全部符合预期。
- UI 层端到端（浏览器 Web 与 iOS 模拟器两条路）均被用户中止，未执行；相册选图的原生弹窗在浏览器自动化下本就不可用（接口层已覆盖照片链路）。
- 结论：服务端与领域逻辑可信，UI 层行为依据为组件测试 + 本地门禁 + 类型检查，建议合并后真机/模拟器人工过一遍编辑页交互。
