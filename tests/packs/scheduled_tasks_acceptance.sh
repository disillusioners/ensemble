#!/usr/bin/env bash
# Scheduled Tasks Acceptance Pack (merge gate — NO xfail/skip parking).
# Runs: (1) adapter unit (TZ/DST/catch-up/idempotency), (2) REST API
# (phase-3 contract + delete-guard placement), (3) scheduled-tasks e2e,
# (4) LEG 4 — scheduler Phase-1 + fix-round PIN FILES: listing-truth,
# namespace, maintenance-seed canonical/guard/selfheal, phase-1 guards,
# label-ambiguous 409, namespace fail-closed 503 and the tool_full_doc
# scheduling tool-help contract. LEG 4 closes the POK
# sched-02 gap where these pins passed standalone but were selected by NO
# pack, so the merge gate never ran them. No hash-comment lines anywhere in
# the continued exec below: a hash comment inside a backslash continuation
# splits the command (house incident — a bare env dumped the environment).
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
  tests/unit/services/test_schedule_listing_truth.py \
  tests/unit/services/test_schedule_namespace.py \
  tests/unit/test_maintenance_seed_canonical.py \
  tests/unit/test_maintenance_seed_guard.py \
  tests/unit/services/test_schedule_phase1_guards.py \
  tests/unit/test_maintenance_seed_selfheal.py \
  tests/unit/services/test_schedule_label_ambiguous.py \
  tests/unit/services/test_schedule_namespace_fail_closed.py \
  tests/unit/tools/test_tool_full_doc_scheduling.py \
  -m "integration or not integration" \
  -q -rA
