#!/usr/bin/env bash
# Test Pack: f1_behavioral_real_engine_test — f1-misfire gate, behavioral proof
# on REAL machinery (branch feature/f1-misfire-fix @ e6cd5fc8).
# Created: 2026-08-31
# Timeout: 200 seconds (3min 20s) — designed for outer `timeout 300`
#
# Gate scopes 2 + 3 + 8 (THE core of this gate):
#   - Scope 2: Pattern-f1 subtree-alive guard (incident-replay SKIP).
#   - Scope 3: Pattern-f1 zombie preservation (FIRE branch on 802095d8 class).
#   - Scope 8: the 11:38:18 WARN-class line that KILLED the live subtree
#     now reads as a SKIP — all 5 WARN text-class substrings verified.
#   Scope-5 tie-in: tz read-back spot (NAIVE + AWARE both fire zombie).
#
# Proves the misfire class is DEAD AND the zombie mission SURVIVED, on real
# machinery: real JobRecoveryService (real repos wired: instance + task +
# job queue + lock) driven via its REAL entry ``reconcile_drift_states``
# (the sweep itself is the engine). File-backed SQLite in tmp dirs —
# deliberately NOT StaticPool (per the f1-batch convention at
# tests/job_queue/test_orphan_active_job_recovery.py:3024-3038). No daemon
# boot. Kill-switch default ON. NEVER port 8088.
#
# Three scenarios:
#   S1 (×2 tests) — incident-replay SKIP + subtree-completion-follow-up
#   S2 (×1 test)  — zombie FIRE (DEAD + lock released + durable reason)
#   S3 (×2 tests) — tz NAIVE + tz AWARE both fire zombie
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 200s` pytest guard
#
# Quick Fix Authorization: YES — test-code only, <20 lines, obvious root
# causes. daemon/ read-only.
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: f1_behavioral_real_engine_test ==="

cd "$PROJECT_DIR"

timeout 200s .venv/bin/pytest \
  test/packs/f1_behavioral_real_engine_test.py \
  --tb=short -q 2>&1

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
