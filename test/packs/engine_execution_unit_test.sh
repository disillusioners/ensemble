#!/usr/bin/env bash
# =============================================================================
# Test Pack: engine_execution_unit_test
#
# Phase: engine-arc-phase1 P2 (Execution Core).
# Owner-of-record: P2 authors this pack; P7 REGISTERS it in
# `.agents/tester/PACKS.md` (deliberately NOT done here).
#
# WHAT IT PINS
#   tests/unit/services/test_engine_honest_state.py
#       — T2.1 state machine + stamp_terminal + AC1/AC7/AC4:
#         every terminal transition stamps last_run_at/last_outcome/
#         last_skip_reason and appends exactly one engine_runs row;
#         restart-clock hydration; retry arithmetic (no jitter).
#   tests/unit/services/test_engine_single_flight.py
#       — T2.2/T2.3/T2.5 single-flight + timeout + off-loop discipline
#         + EngineService lifecycle: AC2 (overlap), AC3 (cancel/extend),
#         AC6 (thread-ident assertions), idempotent start/stop.
#   tests/unit/services/test_engine_due_skip_backoff.py
#       — T2.4/T2.6/T2.7/T2.8/T2.9 due-ness + shadow zero-execution +
#         backoff + metrics snapshot key set + dead (CS-9 carve-out) +
#         project_merged purity + hydration + ingest_event seam + F-7
#         (no silent return) + consecutive_skips survival.
#
# CONTRACT
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - PER-VARIABLE env scrub.
#   - Repo addopts cleared.
#   - VACUOUS-VERDICT GUARD.
#   - Output: header, pytest body, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="engine_execution_unit_test"

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

PYTEST_BIN="$PROJECT_DIR/.venv/bin/pytest"
if [ ! -x "$PYTEST_BIN" ]; then
  echo "FATAL: .venv/bin/pytest not found at $PYTEST_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

UNIT_FILES=(
  tests/unit/services/test_engine_honest_state.py
  tests/unit/services/test_engine_single_flight.py
  tests/unit/services/test_engine_due_skip_backoff.py
)

for f in "${UNIT_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
)

echo
echo "--- unit legs ---"
OUT="$(mktemp)"
timeout 280 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTEST_TIMEOUT=280 \
  "$PYTEST_BIN" \
    -p no:randomly \
    -p no:xdist \
    -v \
    --tb=short \
    --color=no \
    --override-ini="addopts=" \
    --override-ini="timeout=280" \
    "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(sed -n 's/.*[^0-9]\([0-9]\+\) passed.*/\1/p' "$OUT" | tail -1)"
rm -f "$OUT"

if [ -z "$SUMMARY" ]; then
  echo
  echo "RESULT: FAIL (VACUOUS — pytest produced NO summary)"
  exit 1
fi
if [ -z "$PASSED" ] || [ "$PASSED" -eq 0 ]; then
  echo
  echo "RESULT: FAIL (VACUOUS — zero tests passed: '${SUMMARY}')"
  exit 1
fi
echo "SUMMARY: ${SUMMARY}"

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
