#!/usr/bin/env bash
# Test Pack: pattern_f_failsafe_flips_probe_test — Pattern (f) fail-safe
# flips real-DB verification (feature/security-boundary-hygiene @
# a77647bf/ac2c3091)
# Created: 2026-08-30
# Timeout: internal 150s self-guard + outer `timeout 300` (dual-layer)
#
# Verifies that the Pattern (f) lineage-helper fail-safe flips
# (False → True) in daemon/services/job_recovery_service.py make
# lookup errors SKIP (JobItem stays ACTIVE, re-checked next drift
# cycle) instead of finalizing:
#   FS1 exception→skip (has_instance_busy raises → ACTIVE + WARNING)
#   FS2 unwired-repo→skip (task_repository=None → helper True + ACTIVE)
#   FS3 no-instance_id→skip (None → helper True + row survives ACTIVE)
#   FS4 re-check-next-cycle → boundary finalizes (deferral, not wedge)
#   FS5 healthy-shape negative (young ACTIVE+PENDING → normal-path skip)
#
# Real JobRecoveryService + real repositories on file-backed SQLite
# (/tmp); only the dependency-bus singleton is stubbed. Fixture
# patterns reused from pattern_f_killpath_matrix_test.py.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `signal.alarm(150)` inside the
#     Python script
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: pattern_f_failsafe_flips_probe_test ==="
echo "(Pattern (f) Fail-Safe Flips Probe — lookup errors SKIP, not finalize)"

cd "$PROJECT_DIR"

# Use the .venv python (matches project convention used by sibling packs).
PY="${PROJECT_DIR}/.venv/bin/python"
if [ ! -x "${PY}" ]; then
  echo "ERROR: ${PY} not executable; cannot run probe"
  exit 1
fi

# Wrap in `timeout 300` outer guard (Layer 1). The Python script's
# internal `signal.alarm(150)` is Layer 2.
timeout 300 "${PY}" test/packs/pattern_f_failsafe_flips_probe_test.py 2>&1
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
