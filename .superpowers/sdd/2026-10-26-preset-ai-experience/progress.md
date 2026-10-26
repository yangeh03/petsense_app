# SDD ledger — plan: docs/superpowers/plans/2026-10-26-preset-ai-experience.md

Pre-flight: Task 1 produces the catalog/profile `avatarTemplateId` contract consumed by Task 2 dashboard serialization and Task 3 app types; inspected routes/model and found the client currently uses snake_case server fields, so retain existing wire conventions while adding the plan's explicit camelCase PATCH key.
Pre-flight: Task 2 produces distinct `petgif` and `avatarTemplate` dashboard fields consumed by Task 3; current dashboard returns only personal `petgif`, so add the new field without changing the existing one.
Pre-flight: Task 4 produces job submit/status/history contracts consumed by Task 5; preserve the spec's authenticated ownership and idempotency semantics.
Ruling: Repository migration directory is `server/alembic/versions/`, not the plan's `server/petsense/db/migrations/versions/`; use the repository's actual Alembic layout because the project migrations are rooted outside the package.
Task 1: complete (commits 043348f..043348f, tests: env PETSENSE_ROOT=/tmp/petsense-preset-task1 PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python -m unittest discover -s server/tests → [EDGE] rejected: Invalid edge source)
Task 2: complete (commits 043348f..043348f, tests: env PETSENSE_ROOT=/tmp/petsense-preset-task2 PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python -m unittest discover -s server/tests → [EDGE] rejected: Invalid edge source)
Task 3: complete (commits 043348f..043348f, tests: sh -c 'cd app && npm run lint && npm run typecheck && npm run format:check && npm test' → # duration_ms 325.608833)
Task 4: complete (commits 043348f..043348f, tests: env PETSENSE_ROOT=/tmp/petsense-preset-task4-final2 PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python -m unittest discover -s server/tests → [EDGE] rejected: Invalid edge source)
Task 5: complete (commits 043348f..043348f, tests: sh -c 'cd app && npm run lint && npm run typecheck && npm run format:check && npm test' → # duration_ms 316.334625)

Ruling: Do not backfill legacy `pets.petgif_id` values into the new ownership registry. Their origin cannot be proven because the previous bind endpoint accepted arbitrary upstream IDs; exposing them as verified would preserve cross-user identity access. Existing unverified IDs stay hidden and cannot create diaries until generated under the authenticated account. Cost if wrong: affected users may need to generate a personal identity again.
Final: fixed Critical 1 — authenticated generation provenance and bind/diary ownership regression tests RED→GREEN; targeted server tests 20/20, full suite 128/128.
Final: fixed Critical 2 — public PetGIF summaries omit diary fields; privacy regression test RED→GREEN; full server suite 128/128.
Final: fixed Important 3 — known upstream jobs resume polling on status query and startup recovery without re-creation; recovery/status regressions RED→GREEN; full server suite 128/128.
Final: fixed Important 4 — local idempotency key restores independently and submission waits for history reconciliation; app lint, typecheck and tests green.
Final: fixed Important 5 — post-creation uncertainty stays non-retryable and recoverable against the known remote ID; regression test RED→GREEN; full server suite 128/128.
Final: fixed Important 6 — blank steps rejected, explicit zero remains valid; client validation test RED→GREEN; app tests 41/41.
Final: fixed Important 7 — storage downloads validate origin, redirects, size, dimensions, and image format; regression tests RED→GREEN; full server suite 128/128.
Final: fixed Minor 8 — sharing captions use the selected diary's saved input instead of the current draft.
Final: Ruling: Keep the diary worker's active-job coordination process-local — the approved architecture targets the current single-process service; cost if wrong: a future multi-worker deployment needs a shared queue/lease before scaling or duplicate polls may run.
Final: Ruling: Leave the pre-existing floor-plan proxy outside this change — it does not participate in avatar binding or diary submission; cost if wrong: that separate endpoint may still accept unauthenticated upstream IDs and should be secured in its own change.
Final: Ruling: Simulator E2E was executed directly through Computer Use on the isolated iPhone 17e with the local mock and isolated DB — reviewer package omitted execution logs, but the UI showed profile save, action animation, preset-only diary lock, queued/running/completed diary, poster, and persisted history; cost if wrong: an unrecorded device-specific regression may be missed.
Task 6: complete (commits 043348f..043348f, tests: env PETSENSE_ROOT=/tmp/petsense-preset-final PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python -m unittest discover -s server/tests → [EDGE] rejected: Invalid edge source)
