#!/usr/bin/env bash
# Test Pack: ensure_deferred_unit_test — obligation-semantics regression
# for the INSERT-ON-MISSING fix in ReportInjectionRepository.ensure_deferred
# (Debug Phase 4, commit e9aac370, branch feature/fix-report-delivery-ensure-deferred).
#
# Fix part 1 under test: ensure_deferred (daemon/repositories/report_injection/
# repository.py) — absence of rows NEVER means "already delivered":
#   * terminal row pre-check (INJECTED / TASK_DELIVERED / FAILED) =
#     positive evidence → no-op without minting a marker;
#   * non-terminal duplicate → guarded in-place reason UPDATE;
#   * ZERO rows → INSERT fresh DEFERRED row (insert-on-missing);
#   * IntegrityError + re-read converges to exactly one row;
#     persistent conflict with still-zero rows re-raises (never a
#     silent "racing delivery won" no-op — the b7ead8a4 bug class).
#
# NOTE ON PATHS: the dispatch paraphrased this file as
# tests/unit/repositories/test_report_injection_ensure_deferred.py — that
# path does NOT exist. The ACTUAL file in commit e9aac370 is:
#   tests/unit/test_ensure_deferred_insert_on_missing.py
# (verified via `git show e9aac370 --name-only`).
#
# Files covered:
#   1. tests/unit/test_ensure_deferred_insert_on_missing.py — 10 tests
#      (collect-verified 2026-09-07: empty-state insert, legitimate
#      no-ops, in-place reason updates, terminal evidence ×2 states,
#      phantom IntegrityError → insert-on-missing, persistent conflict
#      re-raise, concurrent double-call convergence).
#
# Branch-under-test: feature/fix-report-delivery-ensure-deferred
# (fix commit e9aac370, parent bb052fce).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 120s` on the pytest process
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ensure_deferred_unit_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(ensure_deferred INSERT-ON-MISSING: zero rows insert / terminal pre-check / convergence)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 120s hard cap on the pytest process.
# 10 unit tests, <5s typical; 120s leaves wide margin for CI cold-start.
# set +e around the timed command so the RESULT: FAIL branch below is
# reachable under `set -e` (the timed command's exit code is captured,
# not fatal, until the RESULT block re-raises it).
set +e
timeout 120s .venv/bin/pytest \
  tests/unit/test_ensure_deferred_insert_on_missing.py \
  --tb=short -q 2>&1
EXIT_CODE=$?
set -e

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
