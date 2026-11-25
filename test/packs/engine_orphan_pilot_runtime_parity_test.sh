#!/usr/bin/env bash
# =============================================================================
# Test Pack: engine_orphan_pilot_runtime_parity_test
#
# Phase: engine-arc Phase-2 pilot EVIDENCE lane (2026-11-23) — the RUNTIME
#        proof the landed unit pack could not give (the landed pack drives a
#        SHAPE-MOCKED bus; the production hazard is LOOP-AFFINITY).
#
# WHAT IT PINS
#   LEG 1 — tests/unit/services/test_orphan_watcher_runtime_parity.py
#       the RUNTIME parity proof against a REAL DependencyBus + REAL SQL:
#       (a) the two paths run on DIFFERENT event loops (measured, not
#           assumed); (b) the engine path survives a bus whose asyncio.Lock
#           is BOUND TO AND HELD BY the main loop (the adversarial setup);
#       (c) a MUTATION SELF-PROOF — a lock-touching sweep MUST fail the
#           harness (a harness that cannot fail is not evidence);
#       (d) DIRECTION A: engine-executed sweep ≡ standalone sweep — the SAME
#           orphans, the SAME actions, the SAME no-ops;
#       (e) DIRECTION B (byte): the pilot commits did not touch the bus
#           implementation and added to the sweep module without REMOVING a
#           line — the flag-OFF fallback is byte-preserved;
#       (f) the born-dead immunity (the bus wired AFTER the service is
#           constructed is picked up on a later tick).
#   LEG 2 — tests/unit/services/test_orphan_watcher_truth_table.py
#       the OWNERSHIP TRUTH TABLE: all EIGHT combinations of
#       (per-job flag × shadow × engine-enabled), each row resolved by the
#       REAL production predicates (the ownership conjunct + the engine's
#       real kill-switch ladder + the real shadow gate), plus the
#       flag-UNSET vs explicit-"0" distinction, plus the source pin that
#       BOTH api.py sites consult the SAME predicate.
#   LEG 3 — tests/unit/services/test_orphan_watcher_spec_truthfulness.py
#       the HONESTY fixes: the migrated spec's description/metadata are
#       truthful (and the other 11 specs are untouched), the dead-detection
#       carve-out still keys off the EXECUTORS dict (not the metadata), and
#       the boot posture line carries the executable-specs count.
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
#
# The suite is DB-FREE by construction: the runtime parity legs use a TEMP
# SQLite engine (the sweep's SQL is dialect-portable by design), so no
# ambient POSTGRES_* is read and no live database is touched.
# =============================================================================
set -u

PACK_NAME="engine_orphan_pilot_runtime_parity_test"

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

PYTEST_BIN="$PROJECT_DIR/.venv/bin/pytest"
if [ ! -x "$PYTEST_BIN" ]; then
  echo "RESULT: FAIL (no pytest at ${PYTEST_BIN})"
  exit 1
fi

SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u PGHOST -u PGUSER -u PGPASSWORD -u PGPORT -u PGDATABASE
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
  -u ENSEMBLE_ENGINE_SHADOW_MODE -u ENSEMBLE_ENGINE_ENABLED
  -u SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE
)

UNIT_FILES=(
  tests/unit/services/test_orphan_watcher_runtime_parity.py
  tests/unit/services/test_orphan_watcher_truth_table.py
  tests/unit/services/test_orphan_watcher_spec_truthfulness.py
)

for f in "${UNIT_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "RESULT: FAIL (missing ${f})"
    exit 1
  fi
done

OUT="$(mktemp)"
timeout 280 env "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PYTEST_BIN" -p no:xdist -p no:randomly -v --tb=short --color=no \
  --override-ini="addopts=" "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"
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
