#!/usr/bin/env bash
# =============================================================================
# Test Pack: b4b_routers_pins_unit_test
#
# Lane: feature/pack-coverage-registration (base 40301a5e3) — Wave B family B4b
#       ("recent-add singletons": router layer).
#
# WHAT IT PINS (3 pytest files)
#   tests/unit/routers/test_jobs_list_badge_batch.py                    7 nodes
#   tests/unit/routers/test_live_views_media.py                        46 nodes
#   tests/unit/routers/test_maintenance_reports_router.py              15 nodes
#   Total: 68 nodes (mechanical --collect-only census at this freeze
#   40301a5e3 — re-verified at this base: 68 collected, RC=0, 0 collection
#   errors; per-file counts 7/46/15; slicing proof OK).
#
# CENSUS NOTE: these three files had NO named PACKS.md row before this lane
#   (the jobs-list badge batch pins, the live-views media pins, and the
#   maintenance-reports router pins); together they close the B4b sub-family
#   of the "recent-add singletons" family (B4). This script is their named
#   admission record (PACKS.md block "B4b — Router Recent-Add Singletons").
#   The 68-node count is re-derived on every run by the SLICING PROOF below
#   and FAILS on drift.
#
# CONTRACT (house shape)
#   - Dual-layer timeout: inner timeout 280; caller wraps with 300.
#   - Serial + deterministic: -p no:xdist -p no:randomly.
#   - PER-VARIABLE env scrub (a POSTGRES_* glob is a SILENT NO-OP in bash).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY.
#   - Repo addopts KEPT.
#   - SLICING PROOF: re-derives node count with --collect-only, FAILS on drift.
#   - TIMEOUT (exit 124) is checked BEFORE the vacuous-verdict guard.
#   - VACUOUS-VERDICT GUARD: zero passed or no summary line = FAIL.
#   - Output: RESULT: PASS|FAIL|TIMEOUT. Exit: 0/1/124.
# =============================================================================
set -u

PACK_NAME="b4b_routers_pins_unit_test"
EXPECTED_NODES=68

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "FATAL: .venv/bin/python not found at $PYTHON_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

UNIT_FILES=(
  tests/unit/routers/test_jobs_list_badge_batch.py
  tests/unit/routers/test_live_views_media.py
  tests/unit/routers/test_maintenance_reports_router.py
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
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
  -u ENSEMBLE_BASH_TRACKING_ID
  -u ENSEMBLE_SELF_ENV
)

# NORMALISE, never refuse: the host's ambient ENSEMBLE_SELF_ENV is `live` (measured
# on this host). A pack that REFUSED on the parent's ambient value would refuse to
# start for every tester — every PACKS.md Invocation cell runs
# `cd $WT && timeout 300 bash test/packs/<script>.sh` with NO prefix. So the pack
# normalises to `dev` (the value its own legs require) and the assert below can only
# fire if a future edit drops THIS export — which is exactly the drift it exists to
# catch.
export ENSEMBLE_SELF_ENV=dev

# FENCE (self-verifying): the legs below force ENSEMBLE_SELF_ENV=dev, so the child can
# never inherit the host's ambient value (measured `live` on this host). This assert makes
# that guarantee LOUD — if a future edit drops the normalising export, the pack refuses
# rather than silently running against the wrong environment.
if [ "${ENSEMBLE_SELF_ENV:-}" != "dev" ]; then
  echo "FENCE FAILURE: ENSEMBLE_SELF_ENV='${ENSEMBLE_SELF_ENV:-<unset>}' — this pack requires 'dev'." >&2
  echo "RESULT: FAIL (env fence)"
  exit 1
fi

echo
echo "--- slicing proof: --collect-only over the declared file set ---"
COLLECT_OUT="$(mktemp)"
timeout 120 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PYTHON_BIN" -m pytest \
    -p no:randomly \
    -p no:xdist \
    --collect-only \
    -q \
    --color=no \
    "${UNIT_FILES[@]}" > "$COLLECT_OUT" 2>&1
COLLECT_EXIT="$?"
COLLECTED="$(grep -oE '^[0-9]+ tests? collected' "$COLLECT_OUT" | head -1 | grep -oE '^[0-9]+')"

if [ "$COLLECT_EXIT" -ne 0 ]; then
  echo "SLICING PROOF: FAIL (collection exit=$COLLECT_EXIT)"
  tail -25 "$COLLECT_OUT"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
if [ -z "$COLLECTED" ]; then
  echo "SLICING PROOF: FAIL (VACUOUS — no 'collected' summary line)"
  tail -25 "$COLLECT_OUT"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
if [ "$COLLECTED" -ne "$EXPECTED_NODES" ]; then
  echo "SLICING PROOF: FAIL (node-count drift: expected $EXPECTED_NODES, collected $COLLECTED)"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
echo "SLICING PROOF: OK ($COLLECTED nodes collected, expected $EXPECTED_NODES)"
rm -f "$COLLECT_OUT"

echo
echo "--- B4b: badge batch / live views media / maintenance reports router ---"
OUT="$(mktemp)"
timeout 280 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PYTHON_BIN" -m pytest \
    -q \
    -p no:randomly \
    -p no:xdist \
    --tb=short \
    --color=no \
    "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ passed' | head -1 | grep -oE '^[0-9]+')"
rm -f "$OUT"

# NOTE (ordering): the TIMEOUT verdict is checked FIRST, before the
# vacuous-verdict guard. A killed pytest produces NO summary line at all,
# so the vacuous guard would fire and report `RESULT: FAIL` (exit 1) for a
# genuine timeout — and the pack contract's `RESULT: TIMEOUT` / exit 124
# would be unreachable. Timeout is a distinct verdict and must win.
if [ "$PYTEST_EXIT" -eq 124 ]; then
  echo
  echo "RESULT: TIMEOUT"
  exit 124
fi

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
    echo "RESULT: TIMEOUT"
    exit 124
    ;;
  *)
    echo "RESULT: FAIL (pytest exit=$PYTEST_EXIT)"
    exit 1
    ;;
esac
