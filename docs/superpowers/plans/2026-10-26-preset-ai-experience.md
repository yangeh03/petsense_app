# Preset PetGIF Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate approved preset animations into dog profiles and the home screen, while providing account-scoped asynchronous exercise diary jobs that use only each user's personal PetGIF identity.

**Architecture:** The server owns the preset catalog and persists a separate nullable `avatar_template_id`; the app uses authenticated profile/dashboard responses and existing fresh-action selection logic. Diary requests create durable authenticated jobs keyed by idempotency key, and a bounded worker communicates with the upstream API while the client polls status and history.

**Tech Stack:** Expo SDK 57 / React Native / TypeScript, Flask, SQLAlchemy, Alembic, unittest, Expo Image, Reanimated, CUA simulator testing.

**Spec:** `docs/superpowers/specs/2026-10-26-preset-ai-experience-design.md`

## Global Constraints

- Keep `Pet.petgif_id` as the user's individual PetGIF identity; `avatar_template_id` is a separate nullable preset key.
- Accept only server-approved template IDs; never expose the upstream API key or accept arbitrary media hosts/IDs from the app.
- Diary creation must resolve the authenticated user's pet and personal `petgif_id`; never send a shared preset ID to diary endpoints.
- Use the current `selectPetAvatar` freshness, matching-device and confidence rules (confidence >= 0.6); do not infer behavior when data is absent or stale.
- Diary inputs: duration 1–600 minutes, steps 0–100,000, at most 3 supported photos, max 10 MB each, bounded notes/place lengths.
- Diary generation must be asynchronous, durable, idempotent, account-scoped, and must not blindly retry unknown upstream state.
- Keep history for 90 days and show at most 20 jobs; clean temporary forwarded photos after upload.
- Run E2E against an isolated local database and mock PetGIF API; never create paid external media or alter production account data.
- Before app code changes, check the exact Expo SDK 57 docs at `https://docs.expo.dev/versions/v57.0.0/` for any API used.

## Review Focus

- A forged, removed, or non-dog preset ID is rejected and the existing profile value remains intact; pin in Task 1 validation tests.
- A stale frame, unknown action, low confidence, mismatched device, or failed GIF keeps the static portrait; pin in Task 3 selector/component tests.
- A pet with only a preset cannot start a diary, while a personal `petgif_id` is resolved only from the authenticated pet row; pin in Tasks 2 and 4 API tests.
- Repeated idempotency keys and restart during upstream work do not create another diary; pin in Task 4 durable-job tests.
- Oversized/unsupported photos, duplicate taps, offline polling, and unknown job status preserve a recoverable UI without resubmitting; pin in Tasks 4–6 tests and simulator scenarios.

---

### Task 1: Persist and expose the approved preset catalog

**Files:**
- Create: `server/petsense/db/migrations/versions/0004_pet_avatar_template.py` (use the repository's Alembic naming pattern)
- Modify: `server/petsense/db/models/pets.py`
- Modify: `server/petsense/pets/routes.py`
- Modify: `server/petsense/petgif/proxy.py`
- Modify: `server/petsense/config.py`
- Test: `server/tests/test_pets.py`, `server/tests/test_petgif.py` (create if absent)

**Interfaces:**
- Produce `PETGIF_AVATAR_TEMPLATES` server constant: approved template key to upstream pet ID and display metadata.
- Produce authenticated `GET /api/app/petgif/avatar-templates` returning `{templates: [...]}` with template key, name, preview/card URL, and supported action GIF map only.
- Pet PATCH accepts nullable `avatarTemplateId`; response/dashboard pet serialization emits it.
- Configure upstream base/storage host with environment overrides for mock E2E while preserving current defaults.

- [ ] **Step 1: Write failing tests** for migration round-trip, authenticated catalog response, rejected unknown/non-dog template, nullable clear, and disallowed arbitrary URLs/IDs.
- [ ] **Step 2: Run focused tests** with `PETSENSE_ROOT=/tmp/petsense-preset-plan-red PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python -m unittest server.tests.test_pets -v` (and petgif module); expect the new assertions to fail.
- [ ] **Step 3: Implement model/migration, allowlisted catalog, authenticated route, response serialization, and mockable upstream configuration.** Preserve existing `petgif_id` behavior.
- [ ] **Step 4: Re-run focused tests** and verify catalog outage returns a controlled error without breaking profile reads.

### Task 2: Return separate preset and personal dashboard media

**Files:**
- Modify: `server/petsense/pets/routes.py`
- Modify: `server/petsense/petgif/proxy.py`
- Test: `server/tests/test_pets.py`, `server/tests/test_petgif.py`

**Interfaces:**
- Dashboard exposes `petgif` (personal identity) and `avatarTemplate` (selected allowlisted preset) separately.
- Both include normal character-card media and exact action GIF maps; no action substitution across behaviors.

- [ ] **Step 1: Add failing dashboard tests** for personal-only, preset-only, both, missing catalog/upstream data, and exact action-map preservation.
- [ ] **Step 2: Run the focused dashboard tests** and confirm failure.
- [ ] **Step 3: Implement separate dashboard serialization and preserve normal/interaction/barking media maps from upstream simplification.** Cache catalog metadata with bounded TTL.
- [ ] **Step 4: Run pet/profile/dashboard tests** and verify existing dashboard contract remains compatible.

### Task 3: Add profile template selection and action-aware home animation

**Files:**
- Modify: `app/src/types/social.ts`
- Modify: `app/src/services/api.ts`
- Modify: `app/src/services/pet-profile.ts`
- Modify: `app/src/services/pet-avatar.ts`
- Modify: `app/src/components/pets/pet-profile-form.tsx` or add focused `app/src/components/pets/avatar-template-picker.tsx`
- Modify: `app/src/app/pet.tsx`
- Modify: `app/src/components/dashboard/pet-hero.tsx`
- Modify: `app/src/app/(tabs)/index.tsx`
- Test: `app/tools/pet-avatar.test.cjs` and service tests as appropriate

**Interfaces:**
- App template type mirrors the server catalog. `DashboardData` keeps `petgif` and `avatarTemplate` distinct.
- Extend `selectPetAvatar` to accept a preset asset candidate while retaining current confidence/freshness/device checks and personal-first exact-action selection.

- [ ] **Step 1: Add failing tests** for exact personal-first/preset fallback selection and static fallback on stale, mismatched, unsupported, failed, or low-confidence action data.
- [ ] **Step 2: Run the focused Node test** and confirm expected failures.
- [ ] **Step 3: Implement typed catalog/profile save and clear controls** for dog profiles, showing preview and “animation only” explanation; preserve photo and personal PetGIF fields and unsaved-edit protection.
- [ ] **Step 4: Implement hero selection** with still-image order: uploaded photo, personal card, preset card, emoji; GIF order: personal exact action, preset exact action. Keep existing failure retry, stable image keys, cache and a short crossfade.
- [ ] **Step 5: Verify focused tests, lint, typecheck, and format**; inspect the profile/home behavior on iOS simulator during E2E in Task 6.

### Task 4: Build durable authenticated asynchronous diary jobs

**Files:**
- Create: `server/petsense/db/models/petgif_diary_job.py`
- Create: `server/petsense/db/migrations/versions/0005_petgif_diary_jobs.py`
- Create: `server/petsense/petgif/diary_jobs.py`
- Modify: `server/petsense/db/models/__init__.py`
- Modify: `server/petsense/petgif/proxy.py`
- Modify: `server/petsense/app.py` (bounded executor startup/recovery/shutdown)
- Test: `server/tests/test_petgif_diary_jobs.py`

**Interfaces:**
- Authenticated `POST /api/app/petgif/pets/<pet_id>/diaries` multipart + `Idempotency-Key` returns `{jobId,status}` promptly.
- Authenticated `GET /api/app/petgif/diary-jobs/<job_id>` returns state/progress/result; `GET /api/app/petgif/pets/<pet_id>/diary-jobs` returns newest max 20, newest first.
- Jobs store PetSense user/pet IDs, personal PetGIF ID snapshot, idempotency key, status, remote diary ID, result, timestamps/error class; retain 90 days.
- Worker pool is bounded; restart resumes only jobs with known remote diary ID and marks jobs without one `unknown`; no automatic upstream resubmit.

- [ ] **Step 1: Add failing API/worker tests** for ownership, missing personal identity, input limits, idempotency, queue promptness, completed result, upstream failure, unknown restart state, URL host allowlist, and photo cleanup.
- [ ] **Step 2: Run the new module** and confirm test failures before implementation.
- [ ] **Step 3: Implement durable model/migration and job service** with bounded executor, authenticated ownership checks, idempotent insert, and photo validation/cleanup.
- [ ] **Step 4: Implement worker lifecycle** for upstream create/poll; persist remote diary ID before polling; map ambiguous timeouts to unknown; allow safe retry only for confirmed pre-upstream failures with a new key.
- [ ] **Step 5: Add authenticated routes** for submit/status/history; restrict returned URLs to trusted PetGIF storage host and ensure sharing endpoints validate account pet ownership.
- [ ] **Step 6: Run focused unit tests** using patched HTTP calls and isolated database; confirm no test can call real upstream.

### Task 5: Implement responsive diary form, polling, history and sharing

**Files:**
- Modify: `app/src/services/petgif.ts`
- Modify: `app/src/components/dashboard/diary-modal.tsx`
- Modify: `app/src/app/(tabs)/index.tsx`
- Modify: `app/src/types/social.ts`
- Test: `app/tools/petgif-diary.test.cjs` (or existing service test location)

**Interfaces:**
- Service functions: `createDiaryJob(petId, input, idempotencyKey)`, `getDiaryJob(jobId)`, `listDiaryJobs(petId)`, all authenticated and abortable.
- UI retains active job IDs locally per account/pet, polls with bounded backoff, and offers explicit retry only for confirmed pre-upstream failure.

- [ ] **Step 1: Add failing service tests** for idempotency header, request timeout/cancellation, unknown state retention, bounded history, and multipart limits.
- [ ] **Step 2: Run focused tests** and confirm failure.
- [ ] **Step 3: Implement client service contracts** without long synchronous HTTP timeout.
- [ ] **Step 4: Update diary modal** with duration/steps/note/place/photo validation, duplicate-submit guard, wait explanation, queued/running/completed/failed/unknown states, bounded polling, retained job IDs and a max-20 recent-history view.
- [ ] **Step 5: Show preset-only locked explanation** and keep entry available only for personal PetGIF pets; add explicit share action that can be retried without losing generated result.
- [ ] **Step 6: Run app unit tests, lint, typecheck, and format check.**

### Task 6: Isolated iOS simulator end-to-end verification

**Files:**
- Create: `server/tests/fixtures/petgif_mock_server.py` or an equivalent test-only mock server helper
- Modify only if needed: E2E setup documentation under `docs/superpowers/`

**Interfaces:**
- Disposable backend root and test account; mock PetGIF API returns ready catalog/GIF assets and queued-then-completed diary status/poster.
- Simulator gets a fresh install/session and uses only the isolated backend. No request to real PetGIF hosts.

- [ ] **Step 1: Start mock upstream and isolated backend**, seed/register a disposable test user with a dog and personal test PetGIF ID, and confirm service logs show only local mock requests.
- [ ] **Step 2: Launch app against isolated backend** on a free Metro port; terminate/reopen dev client so Metro reports a fresh bundle.
- [ ] **Step 3: Use computer use through the iOS Simulator UI** to choose a preset, verify it persists after navigation/reload, verify home exact-action animation and static fallback, submit a diary, observe queued/running/completed, reopen history, and share the completed poster.
- [ ] **Step 4: Exercise edge states**: preset-only diary lock, offline polling/reconnect, missing/stale prediction static fallback, invalid input/oversized photo, and duplicate submit; confirm no duplicate mock upstream diary.
- [ ] **Step 5: Run final project checks**: `cd app && npm run lint && npm run typecheck && npm run format:check && npm test`; `PETSENSE_ROOT=/tmp/petsense-preset-final PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python -m unittest discover -s server/tests`.
- [ ] **Step 6: Review `git diff`, secret scan, and branch status**; report the worktree branch and tests. Do not merge or deploy without a separate request.

