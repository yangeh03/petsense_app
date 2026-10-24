# Server Deployment Implementation Plan

**Goal:** Recover the deployed collection service into Git and deploy tested main
commits automatically to Tencent Cloud.

**Architecture:** GitHub Actions runs the existing App checks and Python tests.
When server files change on main, a dedicated restricted SSH key streams the
committed server archive to a receiver. Each release gets its own virtualenv.
Only an explicit list of code/model files is replaced in the existing service
directory. A systemd drop-in selects the release interpreter. Failed activation
restores previous files and interpreter; runtime data and credentials stay put.

**Tech Stack:** Python 3.12, Flask, unittest, OpenSSH, systemd, GitHub Actions.

**Spec:** User authorization in this chat: recover cloud collection code locally
and to GitHub, then configure the previously discussed SSH deployment pipeline.

## Constraints and review focus

- Preserve the cloud collection module byte for byte during recovery.
- Never commit tokens, private keys, databases, experimental data, or videos.
- Do not run collection commands against a real device during testing.
- Serialize production deployments and verify HTTP plus collection/model status.
- Code rollback does not reverse database migrations; this change adds none.
- Initial provisioning and first actual deployment are included in this task.

## Tasks

- [x] Recover server.py, collection_api.py, export_session.py and dependency versions.
- [x] Add isolated collection tests: authentication, durable ACK, deduplication,
      conflicting payload rejection, delta batches, clock and video validation.
- [x] Implement and test restricted archive receiver and rollback deployment.
- [x] Extend CI with backend checks, changed-path detection and deployment.
- [x] Install receiver and restricted deployment key; configure Actions secrets.
- [x] Run required local checks, review, commit and push main.
- [x] Watch first pipeline, resolve failures, verify remote hashes and deployed SHA.

## Execution record

- Existing production service runs as ubuntu with Python 3.12.3 and systemd.
- Existing App CI and cloud runtime remain in place.
- export_session.py is a reusable companion tool and is recovered; compare_batch.py
  is a one-off script tied to observed-batch.json and is excluded.

- Independent review found an authentication-health gap; fixed with authenticated
  collection checks and a regression test. Local checks and 11 Python tests pass.
- Production receiver, restricted key, pinned host key and GitHub production
  secrets are installed. A transient deployment unit was verified as ubuntu.

- First push deployed commit `491387057a968170225d8ecdaf7d29d396919142`.
  [CI run 40093407371](https://github.com/yangeh03/petsense_app/actions/runs/40093407371)
  passed all three jobs. All eight managed file hashes match local Git content.
- Deployment environment is restricted to main. The original runtime paths and
  original virtualenv remain available; the running unit selects the new release
  virtualenv through its deployment drop-in.
