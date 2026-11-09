#!/usr/bin/env bash
# =============================================================================
# Test Pack: decision_module_p1_unit_test
#
# Purpose: register the Decision Module P1 lane's unit suite so the
# standard gate actually RUNS it. Before this registration the lane's
# 202+ nodes were executed by no named pack — a house-process gap, not
# cosmetics (council round 4, fold-now item 1).
#
# Scope — EXACTLY this directory, nothing else, no discovery:
#   tests/unit/decision/            (the Decision Module P1 unit suite)
#
# Contract:
#   - Dual-layer timeout: inner `timeout 280` around pytest; the caller
#     wraps with `timeout 300 bash <this pack>`.
#   - Serial: no -n passed AND xdist plugin disabled outright (-p no:xdist).
#   - Deterministic order: -p no:randomly.
#   - MANDATORY env scrub — the ambient shell may carry LIVE-PROD PG
#     coords AND the live OPENAI_* credentials; the scrub is
#     PER-VARIABLE (a `POSTGRES_*` glob is a silent no-op in bash).
#   - Repo addopts cleared (--override-ini="addopts=") — house
#     convention; per-test timeout raised to 280s.
#   - Optional `-k <expr>` passthrough as $1.
#   - Output: header line, pytest body, RESULT: PASS|FAIL|TIMEOUT.
#   - Exit: 0 (PASS) / 1 (FAIL) / 124 (TIMEOUT).
#
# Expected runtime: < 1 min (unit class).
# =============================================================================
set -u

echo "=== Test Pack: decision_module_p1_unit_test ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL"; exit 1; }

PYTEST_BIN="$PROJECT_DIR/.venv/bin/pytest"
if [ ! -x "$PYTEST_BIN" ]; then
  echo "FATAL: .venv/bin/pytest not found at $PYTEST_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

if [ ! -d "tests/unit/decision" ]; then
  echo "FATAL: required test directory missing: tests/unit/decision" >&2
  echo "RESULT: FAIL"
  exit 1
fi

KARGS=()
if [ -n "${1:-}" ]; then
  echo "( -k filter active: $1 )"
  KARGS=(-k "$1")
fi

# NOTE: set -e is deliberately NOT used — a non-zero pytest exit must
# fall through to the RESULT mapping below.
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
    tests/unit/decision

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
