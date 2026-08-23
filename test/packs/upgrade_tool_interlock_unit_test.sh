#!/usr/bin/env bash
# Test Pack: upgrade_tool_interlock_unit_test
# Tests: tests/unit/tools/test_upgrade_journal.py + tests/unit/tools/test_upgrade_tools.py
# Timeout: 2 minutes (120s)
#
# P2.2 (self-restart-upgrade phase 2, Dispatch C): the system_upgrade tool
# surface + its journal/lock/nonce interlocks — the review-minor-#3 closure
# making the phase's session-smoke evidence reproducible from the tree.
# Covers phase2-plan T4/T5/T6/T7/T8 acceptance in pytest form:
#   - release_info field parity 1:1 vs scripts/upgrade/status.sh on a
#     /tmp fixture written by the REAL lib.sh (T6)
#   - upgrade_status run_id round-trip: armed pending_op → in-flight read →
#     terminal read keyed by the SAME run_id (the cross-death join)
#   - the full refusal-token matrix (every distinct reason=<token>, each its
#     own test) for both actor tools + read-pair env gates + fail-open reads
#   - the LIVE 3-factor gate under a FAKE live marker + /tmp fixture ONLY:
#     each factor missing alone refuses; spoofed origins stamp no window;
#     fabricated user_confirmed alone refuses; the full PASS case consumes
#     the nonce and arms; replay refused; TTL expiry
#   - sequencing (D2/D3): armed tools return with ZERO spawns
#     inside the call (restart banner RESTART SCHEDULED, upgrade banner
#     UPGRADE ARMED — N5 wording, P2.3); marker + journal set instead;
#     second arm → pipeline-busy naming the active run_id
#   - dry_run default TRUE: zero journal mutation, no lock, no marker
#   - journal primitives: kill -9 atomic-write safety (real SIGKILL, bounded
#     <2s), torn detection, BOTH lock stale-break branches, lib.sh interop
#     both directions, ADR-034 splice tolerance (tested, not violated)
#   - executor spawn: env allowlist (API-key-class + ENSEMBLE_UPGRADE_LIVE
#     absent), process-group independence, no-BashProcessRegistry static pin
# All fixtures /tmp-only; live/production NEVER touched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: upgrade_tool_interlock_unit_test ==="

cd "$PROJECT_DIR"

# Unit pack — 2 min hard limit. Dual-layer timeout.
# Internal watchdog (Layer 2): 110s `timeout` wrap below — interrupts hung tests.
# Layer 1 (outer) is the dispatcher's `timeout 120s` wrap.
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
  tests/unit/tools/test_upgrade_journal.py \
  tests/unit/tools/test_upgrade_tools.py \
  --tb=short -q 2>&1 || EXIT_CODE=$?

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
