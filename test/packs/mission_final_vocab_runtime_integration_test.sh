#!/usr/bin/env bash
# Test Pack: mission_final_vocab_runtime_integration_test — Mission program FINAL gate (ad-hoc)
# Tests: tests/integration/test_mission_final_vocab_runtime.py
# Timeout: 5 minutes (300s)
#
# Mission FINAL merge-gate pack on `feature/mission-class` @ 3f9fca81
# (gate HEAD 6f12a5cd, base e676ddea). The vocabulary final-state
# RUNTIME matrix + N8 hot-path end-to-end + purity, scoped to the
# Mission program contract: mirror/receipt jobs render `settled`
# (per-kind token), task jobs keep `completed`. NO surface renders a
# mirror `status='completed'`.
#
# Integration tier — real engine + file-backed SQLite (NullPool + WAL
# + busy_timeout per BLUEPRINT §3, NOT StaticPool). Real FastAPI app
# with jobs + missions + streaming routers mounted. In-proc ASGI
# transport via httpx.ASGITransport — no external services, no LLM
# calls.
#
# Probe scope:
#   Row 1 — Jobs list (task → completed, mirror → settled, no mirror
#           `completed`).
#   Row 2 — Jobs detail (mirror → settled).
#   Row 3 — SSE payload (mirror → settled, task → completed).
#   Row 4 — Missions list + detail (no `completed` for mirror cohort;
#           settled/terminal vocab per doc §8).
#   Row 5 — work_notifier display (direct call: mirror → `settled ✓`).
#   Row 6 — N8 HOT PATH end-to-end (real settle through observer's
#           primary event path → notify_watchers → enqueue_message →
#           emitted `[JOB_EVENT]` text carries `settled ✓`).
#   Row 7 — Done-alias filter (`done` returns BOTH task + mirror
#           cohorts).
#   Row 8 — Mission tool reads (get_mission + list_missions; per-kind
#           vocab).
#   Row 9 — Purity (engine-counted DML = 0 across all read surfaces).
#
# Runner conventions modeled on the sibling mission packs
# (m2_missions_runtime_contract_integration_test.sh, mission_pins_final_test.sh):
# .venv/bin/pytest, --tb=short -q -rf, no --override-ini.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: mission_final_vocab_runtime_integration_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Integration pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout 300 bash …` at invocation
EXIT_CODE=0
timeout 280s .venv/bin/pytest \
  tests/integration/test_mission_final_vocab_runtime.py \
  --tb=short -q -rf 2>&1 || EXIT_CODE=$?
if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi