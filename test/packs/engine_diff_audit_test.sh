#!/usr/bin/env bash
# =============================================================================
# Test Pack: engine_diff_audit_test
#
# Phase: engine-arc-phase1 P7 — §4.2 Leg E (T7.5, D7.4, AC4) — RELEASE-BLOCKING.
# Owner-of-record: P7 authors this pack; P7 REGISTERS it in
# `.agents/tester/PACKS.md` (deliberately NOT done here).
#
# WHAT IT PINS
#   tests/unit/services/test_engine_diff_audit.py
#       — the branch's committed `daemon/` diff (BASE_SHA..HEAD) is a SUBSET
#         of the phase1 §0.3 ownership-map literal (D7.4: the expected set is
#         DATA, never derived from the diff). RED names the offending path and
#         its owning phase. Includes the can-it-fail self-proof (three in-memory
#         mutations that must each drive the containment assertion RED) and a
#         non-vacuity guard (an empty diff = a stale base, not a green gate).
#
# CONTRACT (F-21 preamble — identical shape to engine_substrate_unit_test.sh)
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a silent no-op in bash —
#     see the house critical notes). Covers the LIVE credential class, the
#     LIVE PG coords, the full 7-var ENSEMBLE_AUTH_* family, and pins
#     ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 EXPLICITLY (never `env -u` on
#     it: stripping the ambient guard makes the sweep kill live procs).
#   - Repo addopts cleared (`--override-ini="addopts="`).
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line = FAIL.
#   - Output: header, pytest body, `RESULT: PASS|FAIL`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="engine_diff_audit_test"

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

TEST_FILE="tests/unit/services/test_engine_diff_audit.py"
if [ ! -f "$TEST_FILE" ]; then
  echo "FATAL: required test file missing: $TEST_FILE" >&2
  echo "RESULT: FAIL"
  exit 1
fi

# ── the per-variable env scrub ──────────────────────────────────────────
# Each variable named individually — `env -u POSTGRES_*` matches nothing in
# bash and leaves an ambient POSTGRES_DB=ensemble_prod visible (silent NO-OP).
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
  # The full 7-var ENSEMBLE_AUTH_* family (an ambient AUTH_ENABLED=true would
  # make the daemon require auth and change collection behavior).
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
)

echo
echo "--- Leg E diff-audit gate ---"
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
    "$TEST_FILE" 2>&1 | tee "$OUT"
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
