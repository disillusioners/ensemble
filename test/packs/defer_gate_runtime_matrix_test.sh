#!/usr/bin/env bash
# Test Pack: defer_gate_runtime_matrix_test — defer-gate runtime
# behavioral evidence (5 scenarios S1–S5).
#
# Spec: .agents/tester/MOCK_TESTS.md → "Mock Test:
# defer_gate_runtime_matrix (W-round, fix/defer-gate-post-settle-window)"
#
# Exercises the widened defer-gate admission semantics at RUNTIME on the
# production code path:
#   * S1 defer BLOCKED (settled mirror + non-terminal instance)
#   * S2 defer ADMITTED (settled mirror + TERMINAL instance)
#   * S3 PAUSED blocked by-design (7ecf09e2 invariant)
#   * S4 folding layering proof (gate vs claim t2 guard — two legs)
#   * S5 self-deadlock exclusion (defer queue excluded from busy-set)
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 200s` around the python process
#     (probe target <150s per spec; the python script additionally self-
#     guards with signal.alarm(150) and exits 124 on its own timer).
#
# No pytest; the python probe is self-contained and exits 0/1/124 with the
# RESULT: PASS|FAIL|TIMEOUT line printed at the end.
set -u
cd "$(dirname "$0")/../.."
PROJECT_DIR="$(pwd)"

echo "=== Test Pack: defer_gate_runtime_matrix_test ==="
echo "(Defer-Gate Runtime Matrix — 5 scenarios S1–S5)"
echo "Branch: fix/defer-gate-post-settle-window @ b46c9f8b"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  PY=python3
fi

# Pre-flight: ensure PYTHONPATH and venv are usable
if [ ! -x "$PY" ]; then
  echo "FATAL: no python interpreter found (.venv/bin/python or python3)"
  echo "RESULT: FAIL"
  exit 1
fi

# Layer 2 (script-internal): 200s hard cap on the python process. The
# python script self-times out at 150s; the 200s gives the script time
# to print a final RESULT line on its own.
timeout 200s "$PY" test/packs/defer_gate_runtime_matrix_test.py 2>&1
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
