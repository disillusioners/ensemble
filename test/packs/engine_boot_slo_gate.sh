#!/usr/bin/env bash
# =============================================================================
# Test Pack: engine_boot_slo_gate  (P4 T4.7)
# =============================================================================
set -u

PACK_NAME="engine_boot_slo_gate"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL"; exit 1; }
PYTEST_BIN="$PROJECT_DIR/.venv/bin/pytest"
[ -x "$PYTEST_BIN" ] || { echo "FATAL: no pytest"; exit 1; }

UNIT_FILES=(
  tests/unit/services/engine/test_wiring.py
  tests/unit/services/engine/test_boot.py
)
for f in "${UNIT_FILES[@]}"; do
  [ -f "$f" ] || { echo "FATAL: missing $f"; exit 1; }
done

SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR -u OPENAI_API_KEY
)
OUT="$(mktemp)"
timeout 280 env "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=ensemble_dev \
  "$PYTEST_BIN" -p no:randomly -p no:xdist -v --tb=short --color=no \
  --override-ini="addopts=" "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(sed -n 's/.*[^0-9]\([0-9]\+\) passed.*/\1/p' "$OUT" | tail -1)"
rm -f "$OUT"
[ -z "$SUMMARY" ] && { echo "RESULT: FAIL (vacuous)"; exit 1; }
[ -z "$PASSED" ] || [ "$PASSED" -eq 0 ] && { echo "RESULT: FAIL (zero passed)"; exit 1; }
echo "SUMMARY: ${SUMMARY}"
case "$PYTEST_EXIT" in
  0) echo "RESULT: PASS"; exit 0 ;;
  124) echo "RESULT: TIMEOUT"; exit 124 ;;
  *) echo "RESULT: FAIL (exit=$PYTEST_EXIT)"; exit 1 ;;
esac