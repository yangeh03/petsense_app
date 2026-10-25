# Pet/device onboarding implementation plan

> Execute inline using executing-plans; user requested direct implementation and computer use validation.

**Goal:** Connect pet creation, manual device pairing and honest home readings in one guided flow.
**Architecture:** A dedicated setup route resumes from server state, shares existing authenticated APIs, and returns home after success. A pure readings selector protects live values from missing, invalid, stale or unrelated telemetry.
**Tech Stack:** Expo SDK 57, React Native, TypeScript, Flask/SQLAlchemy.
**Spec:** docs/superpowers/specs/2026-10-25-pet-device-onboarding-design.md

## Constraints and review focus
- One account, one pet, one device. Input example petsense-esp32-001; never claim automatically.
- API failures must retain successful prior steps. Avoid duplicate pets on retry.
- Existing device before pet, repeated claim and second-device conflicts must behave correctly.
- Missing/invalid/stale/other-device health cannot be labeled real-time. Battery zero is valid.
- Image generation is excluded after observed upstream HTTP 404; no new dependency or fake data.

## Tasks
- [x] Add failing device API tests for existing-device-before-pet, idempotent binding repair and second-device conflicts. Fix these transaction paths, run device tests.
- [x] Add setup route with name/species/emoji/breed, resumable manual claim, skip, confirmation and home return. Route pet CTAs to setup, keep create for posts, remove unusable AI entry from main flow.
- [x] Add failing Node tests for readings freshness, ownership, invalid samples and zero battery. Implement selector; show three main vitals, connection guidance, last sync and refresh errors on home. Refresh snapshots while focused.
- [x] Run lint/typecheck/format checks, full server tests, web export. Exercise iOS Expo Go via computer use, including error retry and home display. Review complete diff and report external service/device limitations.

## Execution notes
- Initial computer use confirmed the wrong create mode.
- External image API probe: HTTP 502 / upstream HTTP 404. Deliberately omitted from setup.

## Verification evidence
- iPhone Expo Go computer use: initial add-pet CTA enters pet setup directly; created 小派, skipped device, returned and resumed at pairing; invalid input stayed on pairing; petsense-esp32-001 claim succeeded and home displayed live heart rate/SpO2/temperature and battery.
- Reviewer found an async dashboard lifecycle race. Fixed with focus/account generation invalidation and an in-flight guard; follow-up review found no further important issue.
- Full Python suite: 69 tests passed; Node readings suite: 8 tests passed. Lint/typecheck/format and web export passed again after dependency alignment.
- Cold restart exposed AsyncStorage 3.1.1 native module unavailable in SDK57 Expo Go. Runtime warnings confirmed read/write failures; pinned SDK57-compatible 2.2.0 with expo install and updated lockfile. Official versioned documentation and installed bundledNativeModules agree on 2.2.0.
- expo install --check also reports pre-existing SDK57 patch upgrade advisories; only incompatible storage major changed in this task.
- Final cold restart computer use passed: user restored automatically, bound 小派 and live measurements displayed without login; 24-hour history showed real aggregates.
