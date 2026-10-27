# User-Centered Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the mobile app into 今日、记录、位置、社区、我的 so a new owner can find present status, history, future location, sharing, and account/device management without developer knowledge.

**Architecture:** Keep existing backend data contracts and telemetry parsing unchanged. Add a records tab that composes existing observation and diary components, reduce today to current status and clear routes into records, retain location as a deliberately empty future-capability state, and move publishing into the community header. Navigation metadata is centralized so native and web tabs cannot diverge.

**Tech Stack:** Expo SDK 57, Expo Router, React Native, TypeScript strict, Reanimated, node:test.

**Spec:** `docs/reviews/2026-10-28-product-inventory-and-interaction-plan.md`

## Global Constraints

- Keep the location Tab visible, but do not imply that indoor or outdoor location is available.
- Use only actual dashboard, observation, diary, and social data; do not introduce mock health, activity, position, or location content.
- Keep telemetry parsing in `app/src/services/telemetry-service.ts`; invalid health frames remain out of time series.
- Use colours from `app/src/constants/theme.ts`; use `expo-symbols` icons with valid platform mappings.
- All npm commands run in `app/`; required final checks are lint, typecheck, format:check, and test.
- Do not change server APIs, user profiles, device binding, posts, or media while validating.

## Review Focus

- Guest: records and profile explain the next action without rendering account-scoped data from a previous user.
- Bound but offline device: today says data is not current and routes to a user-facing device state, never a fabricated vital.
- Existing historical data: records can reach 7- and 30-day health trends without returning to today.
- Personal digital identity absent: diary availability explains the requirement and offers only an existing reachable action.
- Location unavailable: the fifth Tab remains discoverable but does not claim a room, route, or phone GPS is the pet's position.

---

## File Structure

- Create `app/src/constants/tab-navigation.ts`: shared five-tab label, route and valid icon mapping.
- Create `app/src/app/(tabs)/records.tsx`: records screen, using current dashboard/observation data and the existing diary modal.
- Create `app/tools/tab-navigation.test.cjs`: regression tests for navigation labels, order, routes and no global compose entry.
- Modify `app/src/components/app-tabs.tsx`: consume shared metadata with native tab triggers.
- Modify `app/src/components/app-tabs.web.tsx`: consume shared metadata with web tab triggers.
- Modify `app/src/app/(tabs)/_layout.tsx`: remove the global floating creation button.
- Modify `app/src/app/(tabs)/index.tsx`: retain current-status information; replace embedded history/detail blocks with routes to records and device/setup.
- Modify `app/src/app/(tabs)/tracking.tsx`: retain the Tab as an honest, minimal future-location state without a diagnostic detour.
- Modify `app/src/app/(tabs)/community.tsx`: add a visible publish action in the community header.
- Modify `app/src/app/(tabs)/profile.tsx`: group pet/device, personal content, and developer tools in user-facing order.

### Task 1: Centralize tab navigation

**Files:**
- Create: `app/tools/tab-navigation.test.cjs`
- Create: `app/src/constants/tab-navigation.ts`
- Modify: `app/src/components/app-tabs.tsx`
- Modify: `app/src/components/app-tabs.web.tsx`

**Interfaces:**
- Produces: `APP_TABS`, a readonly ordered array with `name`, `href`, `label`, and `icon` fields.
- Consumes: platform-specific `NativeTabs` and `TabTrigger` APIs.

- [ ] **Step 1: Write failing navigation tests**

Assert `APP_TABS` has the exact sequence `index`, `records`, `tracking`, `community`, `profile`; labels are `今日`, `记录`, `位置`, `社区`, `我的`; hrefs include `/records` and `/tracking`; every icon has iOS, Android and web values.

- [ ] **Step 2: Run the navigation test to verify it fails**

Run: `node --test tools/tab-navigation.test.cjs`

Expected: FAIL because the shared navigation module does not exist.

- [ ] **Step 3: Implement `APP_TABS` in `src/constants/tab-navigation.ts`**

Use valid `expo-symbols` SF/Material names. Make records a timeline/chart-style mapping and retain location's existing mapping.

- [ ] **Step 4: Make native and web tab renderers consume `APP_TABS`**

Preserve native selected state and web active-state calculation. The only tab route addition is `/records`.

- [ ] **Step 5: Run the navigation test and typecheck**

Run: `node --test tools/tab-navigation.test.cjs && npm run typecheck`

Expected: PASS.

### Task 2: Move historical health, activity, sound, and diary into records

**Files:**
- Create: `app/src/app/(tabs)/records.tsx`
- Modify: `app/src/app/(tabs)/index.tsx`

**Interfaces:**
- Consumes: `useObservations(period)`, `TrendPanel`, `SoundTimeline`, and `DiaryModal`.
- Produces: route `/records`, and today CTAs whose routes are `/records`, `/monitor`, `/setup`, and `/pet` as appropriate.

- [ ] **Step 1: Write the failing screen-level route regression check**

Extend `app/tools/tab-navigation.test.cjs` to load the source text of `src/app/(tabs)/index.tsx` and assert it routes to `/records`, while `src/app/(tabs)/records.tsx` is required to exist. The check guards the user-visible boundary: historical components are owned by records, not duplicated on today.

- [ ] **Step 2: Run the check to verify it fails**

Run: `node --test tools/tab-navigation.test.cjs`

Expected: FAIL because `/records` and the records screen are absent.

- [ ] **Step 3: Implement `RecordsScreen`**

Give it a concise title and three real sections: health trends, today's activity/sound timeline, and diary. For guests, show an account-specific login CTA. For a signed-in owner without a pet, provide the existing setup route. Reuse `useObservations('day')` and retain refresh/error behavior. Open `DiaryModal` only when a pet exists.

- [ ] **Step 4: Simplify `DashboardScreen`**

Keep its welcome, pet hero, live/current vitals, and concise today summary. Replace the embedded `SoundTimeline`, `TrendPanel`, and diary modal with clearly named routes into records. Make the status line go to setup when unbound, and diagnostic detail only from an explicit device-details action. Do not remove source fields or change their data semantics.

- [ ] **Step 5: Run targeted test and typecheck**

Run: `node --test tools/tab-navigation.test.cjs && npm run typecheck`

Expected: PASS.

### Task 3: Keep location discoverable without false capability

**Files:**
- Modify: `app/src/app/(tabs)/tracking.tsx`

**Interfaces:**
- Produces: an accessible `位置` empty state that promises neither current position nor a diagnostic action.

- [ ] **Step 1: Write the failing source regression check**

Extend `app/tools/tab-navigation.test.cjs` to assert the location source has both `室内定位` and `室外定位` copy and does not route directly to `/monitor`.

- [ ] **Step 2: Run the check to verify it fails**

Run: `node --test tools/tab-navigation.test.cjs`

Expected: FAIL because the existing location screen still routes to the diagnostic page.

- [ ] **Step 3: Implement the minimal empty state**

Replace dynamic UWB checks and the diagnostic CTA with a stable title and two unavailable capability rows: indoor and outdoor. State that the Tab will show real collar data after those services are configured. Do not render maps, room names, coordinates, or demo routes.

- [ ] **Step 4: Run targeted test and typecheck**

Run: `node --test tools/tab-navigation.test.cjs && npm run typecheck`

Expected: PASS.

### Task 4: Put publishing and management in their natural homes

**Files:**
- Modify: `app/src/app/(tabs)/_layout.tsx`
- Modify: `app/src/app/(tabs)/community.tsx`
- Modify: `app/src/app/(tabs)/profile.tsx`

**Interfaces:**
- Consumes: existing `/create`, `/setup`, `/pet`, `/monitor`, `/collect`, `/bookmarks`, and social routes.
- Produces: no app-wide create FAB; the only primary route to `/create` is from community.

- [ ] **Step 1: Write the failing source regression check**

Extend `app/tools/tab-navigation.test.cjs`: assert the tab layout does not contain `/create`; assert community contains `router.push('/create')`; assert profile groups `我的宠物与设备`, `我的内容`, and `开发者工具` in that order.

- [ ] **Step 2: Run the check to verify it fails**

Run: `node --test tools/tab-navigation.test.cjs`

Expected: FAIL because the floating compose button remains and profile has no groups.

- [ ] **Step 3: Remove the floating compose button and add community publishing**

Leave `/create` route available for backward links, but do not present pet management in the community publishing entry. Add a clearly labeled header action that opens it. Do not alter post form submission logic.

- [ ] **Step 4: Group profile content**

Place pet cards and `DeviceCard` under `我的宠物与设备`; social homepage/notifications/bookmarks under `我的内容`; move collect and monitor under `开发者工具`. Keep setup available only if the current user has no pet. Do not change logout or device-unbind behavior.

- [ ] **Step 5: Run targeted test and full client checks**

Run: `node --test tools/tab-navigation.test.cjs && npm run lint && npm run typecheck && npm run format:check && npm test`

Expected: PASS.

### Task 5: Simulator acceptance

**Files:**
- Create: `docs/reviews/2026-10-28-interaction-acceptance.md`

- [ ] **Step 1: Start Expo on port 8082 with the target REST base URL and load a fresh bundle**

Terminate Expo Go before opening the URL. Confirm Metro prints `iOS Bundled`.

- [ ] **Step 2: Verify visitor onboarding**

Verify that today and records give a clear login/setup route; location is visible but does not show a false map; community remains browsable; profile explains its login action.

- [ ] **Step 3: Verify signed-in owner flow without mutating production data**

Using the existing test account, verify all five tabs, today-to-record route, 7-day trend, records sound/diary boundaries, location empty state, community publish entry, and profile grouping. Do not post, comment, upload, unbind, or edit real data.

- [ ] **Step 4: Record evidence and outcome**

Save simulator screenshots and the completed checklist under `docs/reviews/2026-10-28-interaction-acceptance.md`. Capture any unavailable hardware condition as a limitation, not a passing live-data test.

## Self-Review

- Spec coverage: Tasks 1–4 cover the five-tab information architecture, today/records boundary, future location placeholder, community publishing and profile grouping. Task 5 covers first-time comprehension and simulator acceptance.
- Step scan: every behavior-changing task starts with a failing regression test; the source-level tests protect the information architecture because this project has no React component test runtime.
- Type consistency: `APP_TABS` is the shared interface used by both tab renderers. Existing API and route interfaces are unchanged.
- Review Focus: guest behavior is exercised in Task 5; offline/current values remain owned by the existing readings service and dashboard; history is checked through Task 2 and simulator; missing identity is checked in Task 2; unavailable location is checked in Task 3 and simulator.
- Proportion: implementation reuses existing components and API shapes; no back-end, map, or mock-data implementation is planned.
