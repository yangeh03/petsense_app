# PetSense server

Python 3.12. Production service: `petsense.service`, running as `ubuntu` in
`/home/ubuntu/petsense-server` on Tencent Cloud. The code lives in the
`petsense/` package (entry point `wsgi.py`); runtime data (databases,
uploads, audio, experiment folders) stays at the install root, unchanged from
the previous single-file layout. `requirements.lock` snapshots its virtualenv,
while `requirements.txt` lists direct dependencies. FFmpeg/FFprobe are
required system packages (cloud: FFmpeg 6.1.1).

## Package layout

- `petsense/telemetry/` — MQTT ingest, WS/SSE fanout, WAV reassembly, device control routes
- `petsense/actions/` — random-forest action classifier and its routes
- `petsense/collection/` — versioned collection storage (moved verbatim from `collection_api.py`)
- `petsense/experiments/` — cloud_data experiment recording
- `petsense/auth/`, `petsense/pets/`, `petsense/social/`, `petsense/petgif/`, `petsense/trends/` — App REST API by domain
- `petsense/web/` — internal dashboard static pages and `/uploads` serving

## Device ownership

`devices` table maps each collar to its owner (one user = one device = one pet;
enforced by a unique owner index plus API rules). Users claim a device by its
ID (`POST /api/app/device/claim`, printed on the collar/packaging) — claiming
auto-binds the device to the user's pet; releasing frees it for another
account. MQTT topics stay per-device (`petsense/sensor/<id>`); ingest stores
`readings.device_id` and `/ws` only forwards frames for devices owned by the
connecting user. Device control commands require login and target the caller's
own device (`petsense/device/<id>/command`). Note: `/api/stats` and
`/api/action/status` (enabled only) stay anonymous — the deploy health check
depends on them.

## Collection API

`petsense/collection/api.py` handles `clock_v2`, `collection_v2` and
`collection_status` MQTT topics. It provides `/api/collection/v1` endpoints
for sessions, device control, data quality, resumable video uploads and manual
video/IMU alignment. Sensor batches are committed before ACK. The MQTT client
module (`petsense/telemetry/mqtt_ingest.py`) owns the shared client and
registers this module as a Flask blueprint.

- Credentials remain outside Git: `~/.petsense-collection-token`,
  `~/.petsense-mqtt-password`, and the existing PetGIF key.
- Session/video files stay in `collection_data/` folders; their metadata now
  lives in the unified database (see Database below).
- `export_session.py --session ID --output NEW_DIRECTORY` exports samples and
  annotations without inventing missing values. Run it from the server directory.
- Do not treat quality checks as proof of sensor accuracy or visual alignment.

## Database

Single logical database `petsense.db` at the install root, created and
versioned by Alembic (`alembic/` + `alembic.ini` ship with the package;
`create_app` runs `upgrade head` idempotently on boot). Models are the schema
source of truth (`petsense/db/models/`); never hand-write DDL — add a migration
(`cd server && python3 -m alembic -c alembic.ini revision --autogenerate`).

SQLite and PostgreSQL are both supported dialects. The test suite takes a
`PETSENSE_TEST_DATABASE_URL` override (see `tests/helpers.py`) to run against a
PostgreSQL instance — verify once with it when cutting over the cloud database.
Switching is a config change: set `PETSENSE_DATABASE_URL=postgresql://...` and
restart — no code changes. The legacy pre-migration files (`data.db`,
`petsense_app.db`, `collection_data/collection.sqlite3`) are kept untouched at
the install root as a natural backup; delete them manually after a
stabilization period.

## Local verification

```sh
python3.12 -m venv /tmp/petsense-server-venv
/tmp/petsense-server-venv/bin/pip install -r server/requirements.lock
/tmp/petsense-server-venv/bin/python -m unittest discover -s server/tests -v
```

Tests use temporary databases and fake MQTT callbacks, never live device commands.

## Automatic deployment

Push to `main`: existing App CI and backend tests run first. If `server/` or the
CI workflow changed, Actions deploys the exact commit via SSH. App-only changes
do not restart the server. The CI workflow also supports **Run workflow** on
`main` to redeploy. Pull requests run checks without deploying.

The `production` environment stores two secrets:

- `TENCENT_DEPLOY_SSH_KEY`: dedicated Ed25519 private key.
- `TENCENT_DEPLOY_KNOWN_HOSTS`: pinned server host public keys.

The corresponding authorized key has `restrict` and a forced command pointing
to `/home/ubuntu/.local/lib/petsense-deploy/receive.py`. This key cannot request
an interactive shell, forwarding, or arbitrary SSH commands. Server bootstrap
must install that receiver and create `/home/ubuntu/petsense-releases` and the
ubuntu-writable `/etc/systemd/system/petsense.service.d` directory. Receiver
changes require explicitly updating the installed copy through an admin session.

The deployment key is still a production code-deployment credential: uploaded
Python code executes as ubuntu. The forced command limits the SSH interface;
it is not a sandbox for untrusted code. Only trusted main commits receive it.

Each deployment:

1. Validates and extracts a bounded archive into a unique release directory.
2. Runs under a transient systemd unit, with a server-side file lock.
3. Builds an independent virtualenv, runs tests and loads the model before activation.
4. Backs up only files listed in `deploy/deploy_server.py:MANAGED_FILES`.
5. Replaces those files in the existing directory and switches the Python
   interpreter via `90-petsense-deploy.conf`, preserving other unit settings.
6. Restarts `petsense` and checks stats, classifier availability and collection
   authentication, including a request using the existing collection token.
   Records the commit in `.deployed-sha` only on success.
7. Restores previous managed files and the interpreter if activation fails.

Database, upload, collection, audio, static/dashboard and credential files are
outside the deployment allowlist. No demo seeding occurs. Rollback restores code
and interpreter; it does not undo database migrations. The initial schema
migration (`alembic/versions/0001_initial.py`) is safe to roll back over: if the
service is reverted to a pre-migration revision, the old code resumes reading
its legacy SQLite files (`data.db`, `petsense_app.db`), which are never touched
by the new code path, while `petsense.db` stays in place for a later re-upgrade.
Review migrations separately before adding them.

Inspect with an admin SSH session:

```sh
systemctl status petsense
cat /home/ubuntu/petsense-server/.deployed-sha
journalctl -u petsense -n 50
```

Release directories contain the previous managed files under `previous/` and
the new virtualenv. Retain the active and prior interpreter directories; do not
delete them while referenced by the systemd drop-in or rollback backup. This
pipeline does not yet automatically prune releases or back up runtime databases.
