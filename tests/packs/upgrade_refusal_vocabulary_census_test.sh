#!/usr/bin/env bash
# ============================================================================
# tests/packs/upgrade_refusal_vocabulary_census_test.sh — AC-D5-13 census pin
# (tester adjudication). Runs the spec-line-provenance refusal/halt vocabulary
# census (tests/test_upgrade_refusal_vocabulary_census.py) plus the
# substitution-shape scanner (tests/upgrade_refusal_scan.py).
# ============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [ "${POSTGRES_DB:-}" = "ensemble_prod" ]; then
    echo "FENCE ABORT: ambient POSTGRES_DB=ensemble_prod" >&2
    exit 78
fi

PY="${REPO_ROOT}/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

PYTEST_LOG="/tmp/census_pytest_$$.txt"
SCAN_LOG="/tmp/census_scan_$$.txt"

cd "$REPO_ROOT"

timeout 240 "$PY" -m pytest -q tests/test_upgrade_refusal_vocabulary_census.py >"$PYTEST_LOG" 2>&1
PYTEST_RC=$?
tail -8 "$PYTEST_LOG"

timeout 60 "$PY" tests/upgrade_refusal_scan.py "$REPO_ROOT" >"$SCAN_LOG" 2>&1
SCAN_RC=$?
cat "$SCAN_LOG"

rm -f "$PYTEST_LOG" "$SCAN_LOG"

if [ "$PYTEST_RC" -eq 124 ] || [ "$SCAN_RC" -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
fi
if [ "$PYTEST_RC" -ne 0 ] || [ "$SCAN_RC" -ne 0 ]; then
    echo "RESULT: FAIL"
    exit 1
fi
echo "RESULT: PASS"
exit 0
