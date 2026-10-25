# PetSense Data Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build a trustworthy, progressive daily pet dashboard backed by durable hardware observations.
**Architecture:** Atomic MQTT ingestion maintains latest checkpoints, minute rollups and sound episodes. Owner-scoped observations API drives current state, daily summaries, events and gap-aware trends; engineering data is secondary.
**Tech Stack:** Expo 57, React Native 0.86, TypeScript, Flask, SQLAlchemy, Alembic, SQLite/PostgreSQL.
**Spec:** docs/superpowers/specs/2026-10-25-pet-data-experience-design.md

## Global Constraints
- No fabricated telemetry, room position, emotions or sleep; no firmware/model changes.
- Parse telemetry only in app/src/services/telemetry-service.ts; health.valid=false never enters series.
- quiet confidence=0 is valid; RMS is not dB; whine is experimental; episodes are not individual barks.
- Existing theme tokens, SymbolView three-platform icon mappings; no new UI library.
- 90-day minute/event retention; raw pruning must not erase summaries; ownership enforced on every API.
- No production deployment or modifications to real pet records during verification.

## Review Focus
- Retransmitted or out-of-order frames after reconnect must not double count.
- Reboot or long offline gap must not create fictitious continuous activity.
- Sparse/invalid metrics need honest counts, extrema, missing buckets and single-point rendering.
- Logged out or reassigned devices must not reveal old user's data in API or stale client state.
- Failed refresh must preserve labeled old data and expose retry, rather than show successful zero activity.

### Task 1: Durable observations and API
**Files:** server/petsense/telemetry/observations.py; server/petsense/db/models/telemetry.py; server/alembic/versions/0003_observations.py; server/petsense/telemetry/mqtt_ingest.py; server/petsense/app.py; server/tests/test_observations.py.
**Interfaces:** ingest_observation(session, payload, received_at) within the raw transaction; GET /api/app/observations returns {latest,summary,events,buckets,range,retentionDays}; boot/seq checkpoint and minute/event models.
- [ ] Write tests for ingestion roundtrip, repeated seq, late frames/old boot, restart, gap and midnight, weighted extrema, invalid health, zero battery, ownership and retention/raw deletion.
- [ ] Run tests RED, implement migration, reducer, authenticated route and atomic ingestion/backfill; run tests GREEN.
- [ ] Run server suite and commit backend deliverable.

### Task 2: Edge contract and progressive daily UI
**Files:** app/src/types/telemetry.ts; app/src/services/telemetry-service.ts; app/src/services/observations.ts; app/src/types/observations.ts; app/src/app/(tabs)/index.tsx; app/src/components/observations/*; app/tools/observations.test.cjs; app/tools/telemetry-check.cjs.
**Interfaces:** consumes Task1 observations response; exports normalized edge predictions and gap-aware chart geometry; latest state requires matching device and freshness.
- [ ] Write failing behavioral tests for real edge action/sound parsing, quiet zero, stale/unknown/replay state and discontinuous chart points.
- [ ] Implement backward-compatible parser, types/client, dashboard with compact status, current behavior/sound, vitals, coverage, events and expandable trends.
- [ ] Verify node tests, lint/typecheck/format and commit.

### Task 3: Supporting flows, visual verification and review
**Files:** app/src/app/monitor.tsx; app/src/app/(tabs)/tracking.tsx; app/src/app/(tabs)/profile.tsx; app/src/app/create.tsx; app/src/app/pet-profile.tsx; server/petsense/pets/routes.py; server/tests/test_pets.py; docs/reviews/2026-10-25-data-experience.md.
**Interfaces:** PATCH pets/:id owner-scoped profile fields; profile focus reload; monitor reads normalized predictions, tracking shows actual capability.
- [ ] Add failing ownership/validation/persistence tests for profile patch; implement profile editor and one-pet navigation copy.
- [ ] Replace misleading map simulation with honest capability state; add focused edge diagnostics without raw payload dump.
- [ ] Run full server and client gates, Web build, exercise UI using computer use with isolated local data.
- [ ] Fresh whole-branch review, fix substantive defects with regression tests, commit and report worktree plus remaining hardware validation limits.
