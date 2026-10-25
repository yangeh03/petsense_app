# Trend Statistics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Correct health trend aggregation and make its time, coverage and refresh behavior understandable.
**Architecture:** Server owns raw statistics and complete time windows. A standalone trend card consumes the versioned response, a focused hook owns request lifecycle, and pure chart helpers own time positioning and ticks.
**Tech Stack:** Flask / SQLAlchemy / Python 3.12; Expo 57 / TypeScript / react-native-svg.
**Spec:** `docs/superpowers/specs/2026-10-25-trend-statistics-design.md`

## Global Constraints

- Only the isolated worktree and `codex/fix-trend-statistics`; no merge or deployment.
- Real owned-device records only; valid finite positive metric samples; retain missing periods.
- UTC timestamps, Asia/Shanghai display, no new runtime dependencies or schema migration.
- Preserve compatible API fields, theme colors and existing telemetry parsing.

## Review Focus

- Old HTTP response after identity/range switch must not populate the new selection (Task 2).
- Overlapping archive and DB samples must not inflate counts (Task 1).
- Partial metrics and nonfinite input must not corrupt averages (Task 1).
- Isolated/constant data must remain visible with unique ticks (Task 2).
- Mobile vertical scrolling must coexist with tap selection, background polling must stop (Task 3).

### Task 1: Server aggregation and regression coverage

**Files:** `server/petsense/trends/routes.py`, new `server/petsense/trends/aggregation.py`, `server/tests/test_trends.py`.
**Interfaces:** GET `/api/app/trends?period=day|week|month|all`; produces schemaVersion=2, generatedAt, window, summary, coverage, warnings, buckets with independent metrics `{count, avg, min, max}` and compatibility fields.
- [x] Add endpoint regression fixtures for weighted statistics, duplicate archive/DB records, time boundaries, missing metrics, auth/isolation, bad archive and cache invalidation.
- [x] Run trend tests and observe failures against old behavior.
- [x] Implement normalized samples, bounded archive cache, UTC/Shanghai windows, complete buckets and server summary.
- [x] Run trend and complete server unittest suites; commit verified deliverable.

### Task 2: Client data lifecycle and chart semantics

**Files:** `app/src/types/social.ts`, new `app/src/services/trend-presentation.ts`, `app/src/services/trend-query.ts`, `app/src/hooks/use-trends.ts`, `app/tools/trends.test.cjs`.
**Interfaces:** typed v2 trend data; pure chart geometry/ticks/selection; query controller rejecting stale responses; hook exposes data/loading/error/refresh for identity+device+period.
- [x] Write and run failing tests for gaps, true-time positions, constant scales, single bucket selection and out-of-order HTTP responses.
- [x] Implement helpers and request lifecycle; hook refreshes on focus/foreground, every 60s when expanded, and manual refresh.
- [x] Run app tests and typecheck.

### Task 3: Trend card and dashboard integration

**Files:** new `app/src/components/dashboard/trend-card.tsx`, new `app/src/components/dashboard/trend-chart.tsx`, `app/src/app/(tabs)/index.tsx`.
**Interfaces:** TrendCard receives user/device identity and dashboard refresh key; defaults open, supports metric/range tabs, server statistics, tap detail, accessible previous/next, retry and coverage.
- [x] Replace inline legacy trend state/markup with standalone card and integrate pull refresh.
- [x] Implement exact-time plot, gap segments, single points, unique ticks, selection and theme-aware display.
- [x] Run lint/typecheck/format:check, app tests, Web export; use computer use against isolated local app to verify all ranges and metric selection.
- [x] Request whole-branch independent review, fix substantive findings, repeat affected checks, commit and report.

## Execution ledger

- User approved the design and instructed implementation. Native execution chosen to keep tightly coupled API/UI changes in one context, with a fresh whole-branch reviewer.
- Pre-flight: Tasks 1→2→3 share the v2 statistics contract described above; no conflicts.
- Baseline app tests: 8 passed. Server environment needs SQLAlchemy installed in isolated Python 3.12 environment before baseline can run.

- Task 1: complete — server suite 78 passed, including 12 trend regressions. Ruling: archive timestamps truncate to milliseconds in existing iso_z; source-aware pairing reconciles precision while preserving distinct microsecond DB frames.
- Task 2: complete — app 17 tests passed, including stale-response isolation and Web/native/keyboard click coordinate fallback.
- Task 3: complete — lint, typecheck, format:check and Web export passed. Computer use checked 402×874 layout, all metric/range selectors, empty and single-point history, missing-period selection, actual-point detail, collapse/expand, failure without stale-range data, and retry recovery. Independent whole-branch review and focused re-review have no remaining important findings.
- Ruling: use the existing theme tokens (coral current tint) without restyling unrelated pages; no new hard-coded page colors.
- Ruling: homepage flex container needed minWidth=0 and flex shrink so trend explanatory text cannot expand the mobile page beyond viewport.
- Screenshot: `docs/superpowers/trend-mobile-review.png`. Local preview health values come from existing real August 12 replay data; receive times are reconstructed from device uptime and confined to a disposable local test database. Product data sources are unchanged.
- Validation limitation: computer-use verification exercised Web at mobile width, not the native iOS/Android runtime. Existing Node ES-module configuration warning remains; lint exits successfully.
- History limitation remains explicit: deleted readings without experiment archives cannot be recovered by this repair.
