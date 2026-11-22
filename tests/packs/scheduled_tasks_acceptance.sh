#!/usr/bin/env bash
# Scheduled Tasks Acceptance Pack (merge gate — NO xfail/skip parking).
# Runs: (1) adapter unit (TZ/DST/catch-up/idempotency), (2) REST API
# (phase-3 contract + delete-guard placement), (3) scheduled-tasks e2e.
# Layer 1: outer `timeout 600` (caller wraps: `timeout 600 bash tests/packs/scheduled_tasks_acceptance.sh`).
# Layer 2: pytest has no per-test timeout configured; the suites self-bound (<2 min observed).
set -euo pipefail
export ENSEMBLE_SELF_ENV=dev
# W1 AUTH FENCE: daemon-child shells can carry ENSEMBLE_AUTH_ENABLED=true,
# which 302-fails every client-fixture test in tests/test_scheduler_api.py
# (including the N6/A6 creator pins). The scheduler API surface under test
# assumes the OFF-state middleware; force the fence INSIDE the pack so the
# gate is environment-independent.
export ENSEMBLE_AUTH_ENABLED=false
echo "[pack-fence] ENSEMBLE_AUTH_ENABLED=$ENSEMBLE_AUTH_ENABLED ENSEMBLE_SELF_ENV=$ENSEMBLE_SELF_ENV"
cd "$(dirname "$0")/../.."
exec .venv/bin/python -m pytest \
  tests/test_scheduler_adapter.py \
  tests/test_scheduler_api.py \
  tests/integration/test_scheduled_tasks_e2e.py \
  -m "integration or not integration" \
  -q -rA
