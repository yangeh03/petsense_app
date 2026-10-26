# Avatar Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline; checklist tracks progress.

**Goal:** Restore original central pet hero and drive exact preset animation from reported action.
**Architecture:** Pure avatar selection service consumes parsed frame, freshness and current pet/PetGIF. Hero component handles static/image-error fallback. Dashboard retains observations history below original-style header/hero/vitals.
**Tech Stack:** Expo57, React Native, expo-image, existing theme and PetGIF contract.
**Spec:** docs/superpowers/specs/2026-10-26-avatar-dashboard-design.md

## Global Constraints
- Existing isolated worktree, no production data edits/deploy.
- No guessed action, missing/stale animation or unrelated pet assets; existing telemetry parsing untouched.
- Existing theme tokens and SymbolView platform mappings.

## Review Focus
- New edge predictions never silently fall back to older cloud/IMU inference.
- Stale or unrelated frames stop animation; invalid health does not hide valid action.
- Missing or failed action image displays static profile rather than unrelated animation.
- Polling/rename does not restart an unchanged GIF; changed action does not retain old GIF.

### Task 1: Exact action-to-avatar selection
**Files:** app/src/services/pet-avatar.ts, app/tools/pet-avatar.test.cjs, app/src/hooks/use-pet-action.ts.
**Interfaces:** inferPetAction(frame) returns PetActionKey|null; selectPetAvatar({pet,petgif,frame,fresh}) returns actionKey,label,animationUrl,staticUrl.
- [x] Add behavioral tests, verify RED, implement pure selector and remove guessed inference from legacy hook, verify GREEN.

### Task 2: Restore dashboard composition
**Files:** app/src/components/dashboard/pet-hero.tsx, app/src/app/(tabs)/index.tsx.
- [x] Restore centered greeting, hero/avatar with action badge, compact connection and sound; preserve real observations below.
- [x] Handle image load failure and current/pending/offline states; no arbitrary animation fallback.
- [x] Run lint/typecheck/format/test and Web export; computer use local isolated mobile view.
- [x] Review diff, record actual checks and limits, commit.
