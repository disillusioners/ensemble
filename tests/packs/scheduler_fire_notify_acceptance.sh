#!/usr/bin/env bash
# Schedule Fire Notify Acceptance Pack (merge gate — NO xfail/skip parking).
# Runs: scheduler adapter seam gating + scheduler API creator capture + e2e.
set -euo pipefail
export ENSEMBLE_SELF_ENV=dev
cd "$(dirname "$0")/../.."
exec .venv/bin/python -m pytest \
  tests/test_scheduler_adapter.py \
  tests/test_scheduler_api.py \
  tests/integration/test_scheduled_tasks_e2e.py \
  tests/unit/services/test_schedule_fire_notify.py \
  -q -rA
