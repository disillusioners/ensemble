#!/usr/bin/env bash
# =============================================================================
# Test Pack: engine_orphan_pilot_parity_test
#
# Phase: engine-arc Phase-2 PILOT (Emperor GO 2026-11-22) — the first real
#        execution migration (OrphanWatcherSweepService onto the engine).
#
# WHAT IT PINS
#   tests/unit/services/test_orphan_watcher_engine_parity.py
#       — (1) dual-path identity: one canonical flag shared by the legacy
#         construct site and the engine spec's kill_switch_key; (2) parity on
#         identical fixture shapes (swept rows / skip reasons / state stamps /
#         timing); (3) dormant default for EXECUTABLES + one-flag rollback;
#         (4) greenlight evidence derivability (SKIPPED_SHADOW cadence,
#         posture-line stability, immediate first tick).
#   Adjacent reviewed pins that this lane's wiring must NOT redden:
#       tests/unit/services/engine/test_dormant_posture.py (CS-1 dormant)
#       tests/unit/services/engine/test_wiring.py (posture line / source order)
#       tests/unit/services/test_engine_kill_switch_dualpath.py (flag polarity)
#       tests/unit/services/test_engine_diff_audit.py (Leg E, re-anchored to
#         this lane's base with the sanctioned service-module row)
#
# CONTRACT (F-21 / D7.2 preamble — identical shape to the P7 engine packs)
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a silent no-op in bash).
#     Covers the live credential class, live PG coords, the full 7-var
#     ENSEMBLE_AUTH_* family, and pins
#     ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 EXPLICITLY (never `env -u` on
#     it: stripping the ambient guard makes the sweep kill live processes).
#   - Repo addopts cleared (`--override-ini="addopts="`).
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line = FAIL.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="engine_orphan_pilot_parity_test"

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
  tests/unit/services/test_orphan_watcher_engine_parity.py
  tests/unit/services/engine/test_dormant_posture.py
  tests/unit/services/engine/test_wiring.py
  tests/unit/services/test_engine_kill_switch_dualpath.py
  tests/unit/services/test_engine_diff_audit.py
)

for f in "${UNIT_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# ── the per-variable env scrub (F-21) ───────────────────────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
)

echo
echo "--- Phase-2 pilot parity + adjacent dormant pins ---"
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
    -s \
    --tb=short \
    --color=no \
    --override-ini="addopts=" \
    --override-ini="timeout=280" \
    "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

# ── the VACUOUS-VERDICT GUARD ───────────────────────────────────────────
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(sed -n 's/.*[^0-9]\([0-9]\+\) passed.*/\1/p' "$OUT" | tail -1)"
rm -f "$OUT"

if [ -z "$SUMMARY" ]; then
  echo
  echo "RESULT: FAIL (VACUOUS — pytest produced NO summary: the suite did not run)"
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
    echo "RESULT: FAIL (timeout 280)"
    exit 124
    ;;
  *)
    echo "RESULT: FAIL (pytest exit=$PYTEST_EXIT)"
    exit 1
    ;;
esac
