#!/usr/bin/env bash
# Test Pack: job_queue_full_unit_test — Job queue tests (xdist -n auto).
#
# Scope: tests/job_queue/ (2046 tests collected, xdist -n auto). The job
# queue subsystem exercises the JobItem admission state machine,
# cancellation cascade, dependency bus, atomic transitions, eligibility
# sweep, F1/F2/F3 plug families, supervisor hooks, and resume-real-chain
# integration. Tests are independent (xdist-friendly) and run on 8 cores
# in <80s on the calibration host (test/packs/ab/).
#
# Branch pin: fix/tool-pairing-full-history-heal @ 86c1bc041.
# Base: latest @ 9be991d56.
#
# Heavy pack — dual-layer timeout per ensure.md:
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 270s` on the pytest process
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail
IFS=$'\n\t'
export PATH="/home/nea/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Test Pack: job_queue_full_unit_test (tests/job_queue/, 2046 tests, xdist -n auto) ==="

# Layer 2 (script-internal): 270s hard cap on the pytest process.
# Heavy pack: -n auto for 8-core parallel speedup (per test/packs/ab/
# calibration precedent). --timeout=270 layered under --override-ini
# so the repo-level pytest addopts (which already include a timeout
# flag) do not double-up.
timeout 270s .venv/bin/pytest \
  tests/job_queue/ \
  --tb=short -q \
  --override-ini="addopts=" \
  --timeout=270 \
  -p no:cacheprovider \
  -n auto 2>&1
RC=$?

if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  echo "Exit: $RC"
  exit 1
fi
