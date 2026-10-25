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
- [ ] Configure isolated Python 3.12 dependencies and run baseline pets tests.
- [ ] Add failing tests for Chinese rename, Unicode boundaries, strict types/unknown fields, login/ownership, unchanged device, photo keep/replace/remove and atomic failure, public/dashboard consistency.
- [ ] Implement common field validation, decoded image validation and transactional persistence; reuse serializer for public profile without adding private fields.
- [ ] Run targeted tests then complete server suite; commit verified service change.

### Task 2: Profile domain and invalidation
**Files:** new app/src/services/pet-profile.ts and pet-profile-events.ts; app/src/services/api.ts; app/src/types/social.ts; new app/tools/pet-profile.test.cjs; app/src/hooks/use-auth.tsx.
**Interfaces:** PetDraft from SocialPet; validatePetDraft(draft), isPetDraftDirty(pet,draft), buildPetUpdate(draft), speciesLabel(species); subscribePetChanges(userId,listener), notifyPetChanged(userId), use hook wrapper in hooks/use-pet-changes.ts.
- [ ] Add failing Node tests for Unicode, preserved photos, default/photo modes, trimmed dirty state and account-isolated notifications.
- [ ] Implement pure draft helpers and account-scoped notification subscriptions; API fetchPet/updatePet preserve multipart support and notify on success only.
- [ ] Run npm tests and typecheck; commit tested domain/API change.

### Task 3: Complete profile interface and entry points
**Files:** new app/src/app/pet.tsx and components/pets/pet-profile-form.tsx; app/src/app/_layout.tsx; (tabs)/profile.tsx, index.tsx, community.tsx; app/src/app/create.tsx, setup.tsx, user.tsx; app/src/components/device-card.tsx.
**Interfaces:** /pet owns load/save/draft and unsaved navigation guard; form receives draft/change/errors/busy callbacks. Existing consumers reload on focus and account-scoped changes with cancellation/sequence protection.
- [ ] Implement read/edit/loading/error/empty states, local image preview, field errors, save retry and success, cancel and all navigation guards.
- [ ] Connect profile card, home avatar and create entry; preserve setup for first creation/device pairing; notify after setup/device mutations.
- [ ] Fix shared avatar/name rendering and refresh consumers including feed and item selection; prevent old account responses.
- [ ] Run lint/typecheck/format:check/npm tests/Web export; resolve actual diagnostics; commit.

### Task 4: End-to-end verification and review
**Files:** docs/superpowers/specs/2026-10-25-pet-profile-management-design.md and verification report.
- [ ] Launch isolated local Flask+Expo with disposable account; computer use exercises create/view/Chinese rename/cancel/invalid input/photo cancel/save/refresh/navigation guards and persistence.
- [ ] Verify narrow viewport and dark mode, record environment limitations honestly.
- [ ] Run full required checks and fresh whole-branch review; fix important findings with regression tests, document results, commit. Leave branch/worktree available for review; no merge or deployment requested.
