#!/usr/bin/env bash
# =============================================================================
# Test Pack: engine_dormant_posture_test
#
# Phase: engine-arc-phase1 P7 (Leg D) — release-blocking dormant-posture gate.
#
# WHAT IT PINS
#   tests/unit/services/engine/test_dormant_posture.py
#       — Leg D / CS-1: N observer specs registered (N derived from the
#         real wiring, >= 1), zero executables (execute_fn withheld), shadow
#         rows present (engine_runs carries ONLY outcome=SKIPPED_SHADOW
#         across >= 3 tick intervals), all SERVICES_*_USE_ENGINE unset, and
#         the single posture INFO line (enabled=True shadow=True
#         registrations=N). In-process via the REAL boot wiring; spy on the
#         dispatch/execute path asserts execute_fn call count == 0.
#
# CONTRACT
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic.
#   - PER-VARIABLE env scrub (no glob).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned explicitly.
#   - VACUOUS-VERDICT GUARD.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="engine_dormant_posture_test"

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
  tests/unit/services/engine/test_dormant_posture.py
)

for f in "${UNIT_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# ── the per-variable env scrub (F-21) ───────────────────────────────────
env \
  -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST \
  -u POSTGRES_PORT -u DATABASE_URL -u ENSEMBLE_DB_DSN \
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID \
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL \
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  timeout 280 \
  "$PYTEST_BIN" \
    -p no:randomly \
    -p no:xdist \
    -v \
    --tb=short \
    --color=no \
    --override-ini="addopts=" \
    --override-ini="timeout=280" \
    "${UNIT_FILES[@]}" 2>&1 | tee /tmp/pack_out_$$.txt
PYTEST_EXIT="${PIPESTATUS[0]}"

OUT_FILE="/tmp/pack_out_$$.txt"
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT_FILE" | tail -1)"
PASSED="$(sed -n 's/.*[^0-9]\([0-9]\+\) passed.*/\1/p' "$OUT_FILE" | tail -1)"
rm -f "$OUT_FILE"

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
PYEOF
chmod +x /home/nea/ensemble-src-wt-engine-arc-p1/test/packs/engine_dormant_posture_test.sh
echo "written"