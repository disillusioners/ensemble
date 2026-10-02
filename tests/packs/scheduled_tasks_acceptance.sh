#!/usr/bin/env bash
# Scheduled Tasks Acceptance Pack (merge gate — NO xfail/skip parking).
# Runs: (1) adapter unit (TZ/DST/catch-up/idempotency), (2) REST API
# (phase-3 contract + delete-guard placement), (3) scheduled-tasks e2e.
# Layer 1: outer `timeout 600` (caller wraps: `timeout 600 bash tests/packs/scheduled_tasks_acceptance.sh`).
# Layer 2: pytest has no per-test timeout configured; the suites self-bound (<2 min observed).
set -euo pipefail
export ENSEMBLE_SELF_ENV=dev
cd "$(dirname "$0")/../.."
exec .venv/bin/python -m pytest \
  tests/test_scheduler_adapter.py \
  tests/test_scheduler_api.py \
  tests/integration/test_scheduled_tasks_e2e.py \
  -m "integration or not integration" \
  -q -rA
