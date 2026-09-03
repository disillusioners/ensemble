#!/usr/bin/env bash
# Test Pack: mission_pins_final_test — Mission program FINAL gate (ad-hoc)
# Tests (5 pin files, exact scope — nothing else):
#   tests/job_queue/test_n8_hot_path_pin.py             (N8 per-kind dispatch hot-path pins)
#   tests/job_queue/test_work_notifier_n1_pin.py        (N1 claim-first ordering pins — expect 5)
#   tests/unit/tools/test_watch_job_mission_terminal.py (N1 companion: watch tool terminal token)
#   tests/integration/test_n3_per_kind_filter_pin.py    (N3 per-kind filter SQL pins — expect 4)
#   tests/integration/test_m3_per_kind_dispatch_pin.py  (M3 dispatch pin)
# Timeout: 5 minutes (300s)
#
# Mission FINAL gate pack on `feature/mission-class` @ 3f9fca81 (base e676ddea).
# Covers the 5 pin files added by the post-M2-gate fix rounds; no prior pack
# covers them. Integration files carry their own file-backed SQLite recipe
# (tmp_path + NullPool + WAL per BLUEPRINT §3) — no env setup needed here.
#
# Runner conventions modeled on the sibling mission packs
# (work_resolver_dead_letter_integration_test.sh, mission_resolver_unit_test.sh):
# .venv/bin/pytest, --tb=short -q -rf, no --override-ini.
# Guard note: `timeout … || EXIT_CODE=$?` keeps `set -e` active for the rest of
# the script while still capturing the real exit code — a bare command would
# errexit-abort BEFORE the RESULT echo-guard could run on FAIL/TIMEOUT.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: mission_pins_final_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Mixed unit+integration pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout 300 bash …` at invocation
EXIT_CODE=0
timeout 280s .venv/bin/pytest \
  tests/job_queue/test_n8_hot_path_pin.py \
  tests/job_queue/test_work_notifier_n1_pin.py \
  tests/unit/tools/test_watch_job_mission_terminal.py \
  tests/integration/test_n3_per_kind_filter_pin.py \
  tests/integration/test_m3_per_kind_dispatch_pin.py \
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
