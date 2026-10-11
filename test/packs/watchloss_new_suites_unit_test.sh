#!/usr/bin/env bash
# =============================================================================
# Test Pack: watchloss_new_suites_unit_test
#
# Purpose: Independent verification of the 3 NEW branch test suites
# (feature/fix-retry-supersede-double-silence). Developer claimed
# 5/5, 11/11, 6/6 green; this pack re-runs them in ONE pytest invocation.
#
# Scope — EXACTLY these three files, nothing else, no discovery:
#   tests/job_queue/test_pattern_f_mission_live_gate.py        (expected 5 tests)
#   tests/job_queue/test_retry_supersede_watch_recovery.py     (expected ~11-12 tests)
#   tests/migration/test_stored_watches_index_parity.py        (expected 6 tests)
#
# Contract:
#   - Dual-layer timeout: inner `timeout 280` around pytest (self-timer <=280s);
#     caller wraps with `timeout 300 bash <this pack>`.
#   - Serial: no -n passed AND xdist plugin disabled outright (-p no:xdist).
#   - Deterministic order: -p no:randomly.
#   - MANDATORY env scrub — ambient shell may carry LIVE-PROD PG coords:
#       env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER
#           -u POSTGRES_PASSWORD -u POSTGRES_DB -u POSTGRES_URL
#           -u DATABASE_URL -u ENSEMBLE_DB_DSN -u SSL_CERT_FILE -u SSL_CERT_DIR
#   - Repo addopts cleared (--override-ini="addopts=") — house convention;
#     per-test timeout raised to 280s (--override-ini="timeout=280",
#     PYTEST_TIMEOUT=280; repo default ini timeout=30).
#   - Optional `-k <expr>` passthrough as $1 (later RED-sim node filtering).
#   - Output: header line, pytest -v --tb=short body, RESULT: PASS|FAIL|TIMEOUT.
#   - Exit: 0 (PASS) / 1 (FAIL) / 124 (TIMEOUT).
#
# Expected runtime: < 1 min (unit class).
# =============================================================================
set -u

echo "=== Test Pack: watchloss_new_suites_unit_test ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL"; exit 1; }

PYTEST_BIN="$PROJECT_DIR/.venv/bin/pytest"
if [ ! -x "$PYTEST_BIN" ]; then
  echo "FATAL: .venv/bin/pytest not found at $PYTEST_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

FILES=(
  "tests/job_queue/test_pattern_f_mission_live_gate.py"
  "tests/job_queue/test_retry_supersede_watch_recovery.py"
  "tests/migration/test_stored_watches_index_parity.py"
)

for f in "${FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

KARGS=()
if [ -n "${1:-}" ]; then
  echo "( -k filter active: $1 )"
  KARGS=(-k "$1")
fi

# NOTE: set -e is deliberately NOT used — a non-zero pytest exit must fall
# through to the RESULT mapping below (contract: RESULT line always printed).
timeout 280 env \
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD \
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN \
  -u SSL_CERT_FILE -u SSL_CERT_DIR \
  PYTEST_TIMEOUT=280 \
  "$PYTEST_BIN" \
    -p no:randomly \
    -p no:xdist \
    -v \
    --tb=short \
    --color=no \
    --override-ini="addopts=" \
    --override-ini="timeout=280" \
    "${KARGS[@]}" \
    "${FILES[@]}"
PYTEST_EXIT=$?

echo ""
case "$PYTEST_EXIT" in
  0)
    echo "RESULT: PASS"
    exit 0
    ;;
  124)
    echo "RESULT: TIMEOUT"
    exit 124
    ;;
  *)
    echo "RESULT: FAIL (pytest exit=$PYTEST_EXIT)"
    exit 1
    ;;
esac
