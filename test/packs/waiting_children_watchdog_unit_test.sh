#!/usr/bin/env bash
# Test Pack: waiting_children_watchdog_unit_test — #8 WAITING_CHILDREN hang-watchdog suite
# Timeout: 2 minutes (120s) — unit-type limit per test-pack skill
#
# Wraps exactly one suite:
#   - tests/unit/services/test_waiting_children_watchdog.py (47 tests)
#     The #8 WAITING_CHILDREN hang-watchdog suite (hourly scan + waking
#     hang-guide notices; landed pre-branch via 22d03844, doc fix 606b1bed).
#     Covers scan scheduling, hang-notice enqueue, cooldown bounds, and
#     hung-children SQL dialect parity (SQLite/PG branches + fallback).
#
# Scope note (2026-08-29, reconciler-wedge-fix gate): the BRANCH's wedge
# tests (TestWedgeBackstop — wedge notice/silence/idempotence/children
# gate) live in tests/unit/test_reconciler_wedge_fix.py and are served by
# the wedge_fix_suites_unit_test pack. Per the council record there is NO
# separate branch-added watchdog test file, so this pack intentionally
# runs ONLY the pre-existing #8 watchdog suite — no double-inclusion.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 120s` pytest guard
#
# No deselection: no QUARANTINE.md entries for this file (2026-08-29).
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: waiting_children_watchdog_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/unit/services/test_waiting_children_watchdog.py \
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
