#!/usr/bin/env bash
# Schedule Fire Notify Acceptance Pack (merge gate — NO xfail/skip parking).
# Runs: scheduler adapter seam gating + scheduler API creator capture + e2e
#       + the deferred-site identity-gate legs (W1 Green #4, -k selection).
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
.venv/bin/python -m pytest \
  tests/test_scheduler_adapter.py \
  tests/test_scheduler_api.py \
  tests/integration/test_scheduled_tasks_e2e.py -m "integration or not integration" \
  -q -rA
echo "--- deferred-pin identity-gate legs (W1 Green #4, -k selection) ---"
.venv/bin/python -m pytest \
  tests/unit/test_scheduler_deferred_delivery_pin.py -k "TestScheduleFireNotifyDeferred" \
  -q -rA
