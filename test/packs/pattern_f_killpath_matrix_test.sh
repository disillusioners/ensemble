#!/usr/bin/env bash
# Test Pack: pattern_f_killpath_matrix_test — Pattern (f) Kill-Path
# Matrix behavioral, real-DB gate
# (feature/orphan-active-job-recovery @ ba39a40e)
# Created: 2026-08-29
# Timeout: internal 240s self-guard + outer `timeout 300` (dual-layer)
#
# Independent gate probe for the council-critical Pattern (f) recovery
# path. Spec: .agents/tester/MOCK_TESTS.md → "Pattern (f) Kill-Path
# Matrix (council criticals, real scenarios)".
#
#   Scenarios (a)-(e) drive the REAL
#   ``JobRecoveryService._pattern_f_orphan_active_job_recovery`` sweep
#   on file-backed SQLite, asserting REAL DB rows:
#     (a) PAUSED-past-grace → JobItem stays active + Task resumable
#     (b) FAILED/CANCELLED + live retry child → boundary after retry
#     (c) f2 per-leg mutation check (load-bearing proven)
#     (d) genuine restart-orphan → DEAD + W1 mid-mint negative
#     (e) f2 lock release on c=1 queue → new admit (no wedge)
#
# Only the dependency-bus singleton is stubbed (the service requires
# ``get_dependency_bus()``); everything else — repositories, lock
# manager, JobQueueService, the boundary finalize — runs against the
# production code path with REAL DB writes.
#
# This pack is COMPLEMENTARY to (and independent of)
# test/packs/orphan_active_job_recovery_suites_unit_test.sh — that
# pack re-runs the in-tree unit tests; this pack exercises the same
# services on REAL-DB seed data + REAL sweep + REAL assertions.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `signal.alarm(240)` inside the
#     Python script
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: pattern_f_killpath_matrix_test ==="
echo "(Pattern (f) Kill-Path Matrix — behavioral, real-DB gate)"

cd "$PROJECT_DIR"

# Use the .venv python (matches project convention used by sibling packs).
PY="${PROJECT_DIR}/.venv/bin/python"
if [ ! -x "${PY}" ]; then
  echo "ERROR: ${PY} not executable; cannot run probe"
  exit 1
fi

# Wrap in `timeout 240` outer guard (Layer 1). The Python script's
# internal `signal.alarm(240)` is Layer 2.
timeout 240 "${PY}" test/packs/pattern_f_killpath_matrix_test.py 2>&1
EXIT_CODE=$?

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
