#!/usr/bin/env bash
# =============================================================================
# Test Pack: b4c_services_unit_core_pins_unit_test
#
# Lane: feature/pack-coverage-registration (base `40301a5e3`) (re-verified at this base) — PACKS.md +
#       pack-scripts coverage registration wave.
#
# WHAT IT PINS (13 pytest files + 1 standalone Python script leg)
#   tests/unit/services/test_pok_librarian_ra_d1_unit.py                  3 nodes
#   tests/unit/services/test_readiness_boot_window.py                     16 nodes
#   tests/unit/services/test_work_resolver_badge_pushdown.py               7 nodes
#   tests/unit/test_blueprint_lease_reaper.py                             40 nodes
#   tests/unit/test_graph_debug_log_guards.py                              8 nodes
#   tests/unit/test_gzip_middleware.py                                    25 nodes
#   tests/unit/test_husk_e7_checkpoint_leg.py                              3 nodes
#   tests/unit/test_instance_read_husk_spawn_stamp.py                     21 nodes
#   tests/unit/test_orjson_parity.py                                      19 nodes
#   tests/unit/test_port_holder_identity.py                               11 nodes
#   tests/unit/test_prod_dsn_fence.py                                     26 nodes
#   tests/unit/test_project_future_registration.py                         8 nodes
#   tests/unit/test_turn_end_injection_drain.py                           31 nodes
#   Total: 218 pytest nodes (mechanical `--collect-only` census at this freeze
#   `40301a5e3` (re-verified at this base): 218 collected, RC=0, 0 collection errors; per-file counts
#   3/16/7/40/8/25/3/21/19/11/26/8/31; slicing proof OK).
#
#   `tests/unit/test_persona_neutrality.py` (4 nodes) is DELIBERATELY NOT in
#   this slice: it carries a KNOWN PRE-EXISTING RED at this freeze (1 failed,
#   3 passed) and is registered as its OWN row — see
#   test/packs/persona_neutrality_pins_unit_test.sh. Nothing is dropped: the
#   file is still pinned, by its own honest-red pack.
#
#   tests/unit/tools/test_promote_launchd_survivorship_python.py — a
#   standalone Python script (not pytest-collectable); it runs as its own leg
#   below.
# =============================================================================
set -u

PACK_NAME="b4c_services_unit_core_pins_unit_test"
EXPECTED_NODES=218

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
  tests/unit/services/test_pok_librarian_ra_d1_unit.py
  tests/unit/services/test_readiness_boot_window.py
  tests/unit/services/test_work_resolver_badge_pushdown.py
  tests/unit/test_blueprint_lease_reaper.py
  tests/unit/test_graph_debug_log_guards.py
  tests/unit/test_gzip_middleware.py
  tests/unit/test_husk_e7_checkpoint_leg.py
  tests/unit/test_instance_read_husk_spawn_stamp.py
  tests/unit/test_orjson_parity.py
  tests/unit/test_port_holder_identity.py
  tests/unit/test_prod_dsn_fence.py
  tests/unit/test_project_future_registration.py
  tests/unit/test_turn_end_injection_drain.py
  tests/unit/tools/test_promote_launchd_survivorship_python.py
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

# pytest-collectable subset (the standalone script is not pytest-collectable)
PYTEST_FILES=(
  tests/unit/services/test_pok_librarian_ra_d1_unit.py
  tests/unit/services/test_readiness_boot_window.py
  tests/unit/services/test_work_resolver_badge_pushdown.py
  tests/unit/test_blueprint_lease_reaper.py
  tests/unit/test_graph_debug_log_guards.py
  tests/unit/test_gzip_middleware.py
  tests/unit/test_husk_e7_checkpoint_leg.py
  tests/unit/test_instance_read_husk_spawn_stamp.py
  tests/unit/test_orjson_parity.py
  tests/unit/test_port_holder_identity.py
  tests/unit/test_prod_dsn_fence.py
  tests/unit/test_project_future_registration.py
  tests/unit/test_turn_end_injection_drain.py
)

echo
echo "--- slicing proof: --collect-only over the declared pytest file set ---"
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
    "${PYTEST_FILES[@]}" > "$COLLECT_OUT" 2>&1
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
echo "--- B4c: services + unit-core singletons (pytest leg) ---"
OUT="$(mktemp)"
timeout 540 env \
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
    "${PYTEST_FILES[@]}" 2>&1 | tee "$OUT"
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

echo
echo "--- B4c: standalone script leg (test_promote_launchd_survivorship_python.py) ---"
SCRIPT_OUT="$(mktemp)"
env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  timeout 300 "$PYTHON_BIN" tests/unit/tools/test_promote_launchd_survivorship_python.py > "$SCRIPT_OUT" 2>&1
SCRIPT_EXIT="$?"
# VACUOUS guard: exit 0 with NO output is not a pass — an empty run proves nothing.
SCRIPT_EMPTY=0; [ -s "$SCRIPT_OUT" ] || SCRIPT_EMPTY=1
tail -2 "$SCRIPT_OUT"
rm -f "$SCRIPT_OUT"
if [ "$SCRIPT_EXIT" -ne 0 ]; then
  echo "STANDALONE SCRIPT: FAIL (exit=$SCRIPT_EXIT)"
elif [ "$SCRIPT_EMPTY" -ne 0 ]; then
  echo "STANDALONE SCRIPT: FAIL (VACUOUS — exit 0 but produced NO output)"
  SCRIPT_EXIT=1
else
  echo "STANDALONE SCRIPT: PASS (exit 0)"
fi

if [ "$PYTEST_EXIT" -ne 0 ] || [ "$SCRIPT_EXIT" -ne 0 ]; then
  echo
  echo "RESULT: FAIL (pytest leg rc=$PYTEST_EXIT, standalone leg rc=$SCRIPT_EXIT)"
  exit 1
fi
echo
echo "RESULT: PASS"
exit 0
