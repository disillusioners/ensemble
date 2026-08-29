#!/usr/bin/env bash
# Test Pack: defer_bus_emit_probe_test — Bus-Emit Fix behavioral probe
# (incident 02fb2e01, fix ca9263c2)
# (feature/orphan-active-job-recovery @ ba39a40e)
#
# Behavioral, real-DB probe of the child_still_running_defer branch in
# ChildReportsService._dispatch_post_commit_side_effects. Three probes:
#   P1 — exactly-once called-twice (REAL dispatch path)
#   P2 — legitimate defer preserved (no premature finalization)
#   P3 — incident replay 02fb2e01 (multi-turn, parent gate released)
#
# Spec: .agents/tester/MOCK_TESTS.md → "child_still_running_defer Bus-Emit
# Fix (02fb2e01)".
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 180s` around the python process
#     (probe target <3 min per spec); the python script additionally self-
#     guards with signal.alarm(180) and exits 124 on its own timer.
#
# No pytest; the python probe is self-contained and exits 0/1/124 with the
# RESULT: PASS|FAIL|TIMEOUT line printed at the end.
set -u
cd "$(dirname "$0")/../.."
PROJECT_DIR="$(pwd)"

echo "=== Test Pack: defer_bus_emit_probe_test ==="
echo "(Bus-Emit Fix Probe — incident 02fb2e01, fix ca9263c2)"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  PY=python3
fi

# Layer 2 (script-internal): 180s hard cap on the python process.
timeout 180s "$PY" test/packs/defer_bus_emit_probe_test.py 2>&1
RC=$?

if [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
elif [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
else
  echo "RESULT: FAIL"
  exit 1
fi
