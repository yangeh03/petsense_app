# SDD ledger — plan: docs/superpowers/plans/2026-10-26-preset-ai-experience.md

Pre-flight: Task 1 produces the catalog/profile `avatarTemplateId` contract consumed by Task 2 dashboard serialization and Task 3 app types; inspected routes/model and found the client currently uses snake_case server fields, so retain existing wire conventions while adding the plan's explicit camelCase PATCH key.
Pre-flight: Task 2 produces distinct `petgif` and `avatarTemplate` dashboard fields consumed by Task 3; current dashboard returns only personal `petgif`, so add the new field without changing the existing one.
Pre-flight: Task 4 produces job submit/status/history contracts consumed by Task 5; preserve the spec's authenticated ownership and idempotency semantics.
Ruling: Repository migration directory is `server/migrations/versions/`, not the plan's `server/petsense/db/migrations/versions/`; use the repository's actual Alembic layout because the project migrations are rooted outside the package.
Task 1: complete (commits 043348f..043348f, tests: env PETSENSE_ROOT=/tmp/petsense-preset-task1 PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python -m unittest discover -s server/tests → [EDGE] rejected: Invalid edge source)
Task 2: complete (commits 043348f..043348f, tests: env PETSENSE_ROOT=/tmp/petsense-preset-task2 PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python -m unittest discover -s server/tests → [EDGE] rejected: Invalid edge source)
Task 3: complete (commits 043348f..043348f, tests: sh -c 'cd app && npm run lint && npm run typecheck && npm run format:check && npm test' → # duration_ms 325.608833)
Task 4: complete (commits 043348f..043348f, tests: env PETSENSE_ROOT=/tmp/petsense-preset-task4-final2 PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python -m unittest discover -s server/tests → [EDGE] rejected: Invalid edge source)
Task 5: complete (commits 043348f..043348f, tests: sh -c 'cd app && npm run lint && npm run typecheck && npm run format:check && npm test' → # duration_ms 316.334625)
Task 6: complete (commits 043348f..043348f, tests: env PETSENSE_ROOT=/tmp/petsense-preset-final PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python -m unittest discover -s server/tests → [EDGE] rejected: Invalid edge source)
Task 6: complete (commits 043348f..043348f, tests: env PETSENSE_ROOT=/tmp/petsense-preset-final PETSENSE_MQTT_DISABLED=1 server/.venv/bin/python -m unittest discover -s server/tests → [EDGE] rejected: Invalid edge source)
