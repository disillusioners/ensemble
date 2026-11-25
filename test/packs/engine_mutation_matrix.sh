#!/usr/bin/env bash
# =============================================================================
# Test Pack: engine_mutation_matrix
#
# Phase: engine-arc-phase1 P7 (Leg F) — mutation-matrix harness.
# Owner-of-record: P7 authors this pack.
#
# WHAT IT PINS
#   Leg F — engine mutation matrix: disposable-clone plants of the C-1 recipe
#   (Leg A) + 10 P7-proven in-tree predicate mutations (B1..B10, incl.
#   control leg B2) + the M-01a..M-14c mapping docstring (Leg C).  Exit 0
#   ONLY when every selected leg is RED-with-defect AND GREEN-after-restore;
#   a leg GREEN with the defect planted is NON-DISCRIMINATING and FAILS.
#
# CONTRACT
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a silent no-op in
#     bash — see the house critical notes). Pins
#     ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 EXPLICITLY (never `env -u`
#     on it).
#   - Repo addopts cleared (`--override-ini="addopts="` is inside the .py).
#   - VACUOUS-VERDICT GUARD: zero passed legs / no RESULT line = FAIL.
#   - Output: header, per-leg lines, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="engine_mutation_matrix"

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

PY_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PY_BIN" ]; then
  echo "FATAL: .venv/bin/python not found at $PY_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

MATRIX_PY="tests/packs/engine_mutation_matrix.py"
if [ ! -f "$MATRIX_PY" ]; then
  echo "FATAL: required file missing: $MATRIX_PY" >&2
  echo "RESULT: FAIL"
  exit 1
fi

# ── the per-variable env scrub ──────────────────────────────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
)

OUT="$(mktemp)"
timeout 280 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PY_BIN" \
    "$MATRIX_PY" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

# ── the VACUOUS-VERDICT GUARD ───────────────────────────────────────────
if ! grep -q "RESULT: PASS\|RESULT: FAIL\|RESULT: TIMEOUT" "$OUT"; then
  echo
  echo "RESULT: FAIL (VACUOUS — harness produced no RESULT line)"
  rm -f "$OUT"
  exit 1
fi

SUMMARY="$(grep -E 'RESULT: (PASS|FAIL|TIMEOUT)' "$OUT" | tail -1)"
rm -f "$OUT"

echo
echo "SUMMARY: ${SUMMARY}"

case "$PYTEST_EXIT" in
  0)
    if echo "$SUMMARY" | grep -q "RESULT: PASS"; then
      echo "RESULT: PASS"
      exit 0
    fi
    echo "RESULT: FAIL (harness reported non-PASS)"
    exit 1
    ;;
  124)
    echo "RESULT: TIMEOUT"
    exit 124
    ;;
  *)
    echo "RESULT: FAIL (harness exit=$PYTEST_EXIT)"
    exit 1
    ;;
esac
