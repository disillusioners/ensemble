#!/usr/bin/env bash
# Test Pack: memoir_v2_wake_refresh_test
#
# Memoir Store v2 — phase 6, the WAKE-REFRESH context seam (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase6-plan.md §3.2 /
# T6.4 / T6.5; AC4 + AC5 + AC6(c); module
# tests/unit/test_memoir_wake_refresh.py).
#
# Scope (AC4 + AC5):
#   * the ``memoir_refresh`` REGISTRATION in the ``_stable_id_for`` CLOSED
#     LIST (``memoir_refresh:{instance_id}``, missing instance id refused)
#     AND the UPDATED closed-list ValueError message (the message is a
#     live contract — a stale enumeration is a defect);
#   * the seam: ari-only → the lane's OWN knob → the THREADED
#     ``wake_source == "scheduler"`` gate, emitting a
#     ``context_kind="memoir_refresh"`` block with the FRESH payload
#     (persona + keepsakes + the [PROPOSALS] zone + dynamic catch-up);
#   * ``wake_source`` THREADING from the dispatch site (the signal does not
#     exist at the assembly seam natively) and the explicit refusal to gate
#     on ``instance_metadata.original_source``;
#   * SCHEDULER STAMP PURITY (L4) — the scheduler's own wake message stays
#     an unstamped bare ``MessageType.HUMAN``; the refresh composes
#     ALONGSIDE it and the alarm template carries no memoir content;
#   * NO ONCE-FLAG — successive wakes supersede to exactly ONE row;
#   * the KILL-SWITCH (``ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED``) per the
#     DR-3 Enabled-flags truth table (unset ⇒ ON + WARNING; garbage ⇒ OFF
#     + WARNING; never a boot abort; call-time reads).
#
# Interpreter resolution (house convention): the repo's OWN .venv first;
# MEMOIR_PACK_PYTHON overrides it. NEVER a bare ``python3`` — that would
# import ``daemon`` from the MAIN checkout and report GREEN about a tree
# that is not this worktree.
#
# FENCES: per-variable env scrub (``env -u POSTGRES_*`` is a silent NO-OP
# glob), ``-p no:randomly``, the dual-layer timeout, and the
# collected-count guard (GREEN-AT-ZERO IS A FAILURE).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACK_NAME="memoir_v2_wake_refresh_test"
echo "=== Test Pack: ${PACK_NAME} ==="
cd "$PROJECT_DIR" || {
  echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"
  exit 1
}

PY=""
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PY="$PROJECT_DIR/.venv/bin/python"
elif [ -n "${MEMOIR_PACK_PYTHON:-}" ] && [ -x "${MEMOIR_PACK_PYTHON}" ]; then
  PY="${MEMOIR_PACK_PYTHON}"
fi
if [ -z "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
echo "Interpreter: $PY"

SCRUB=(
  -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_HOST -u POSTGRES_PORT
  -u POSTGRES_PASSWORD -u DATABASE_URL -u DATABASE_URL_POSTGRES
  -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD
  -u PGPASSFILE -u PGSSLMODE -u PGSERVICE -u PGSERVICEFILE
  -u PORT -u DAEMON_PORT
  -u ENSEMBLE_SELF_ENV
  -u ENSEMBLE_MEMOIR_ENABLED
  -u ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED
)

TARGET="tests/unit/test_memoir_wake_refresh.py"
if [ ! -f "$TARGET" ]; then
  echo "RESULT: FAIL (missing test module '$TARGET' — the leg would be vacuous)"
  exit 1
fi

# The MEASURED node count at the pin (2026-11-10, this worktree):
# TestStableIdRegistration (4) + TestRefreshSeam (8) + TestWakeRefreshKnob (19)
# + TestStampPurity (4) + TestWakeSourceThreading (3)
# + TestStructuralSupersede (2) = 40.
EXPECTED_PASSED_N=40

SELECTOR=(
  "TestStableIdRegistration or TestRefreshSeam or TestWakeRefreshKnob"
  "or TestStampPurity or TestWakeSourceThreading or TestStructuralSupersede"
)

TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-300}"
LOG="${TMPDIR:-/tmp}/${PACK_NAME}.log"

timeout "${TIMEOUT_S}" env "${SCRUB[@]}" "${PY}" -m pytest "${TARGET}" \
  -k "${SELECTOR[*]}" \
  -q --tb=short -rfE -p no:randomly --override-ini="addopts=" 2>&1 | tee "${LOG}"
EXIT_CODE=${PIPESTATUS[0]}
if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT (internal ${TIMEOUT_S}s watchdog exceeded)"
  exit 124
fi

# Explicit collected-count guard — GREEN-AT-ZERO IS A FAILURE.
SUMMARY="$(grep -Eo '[0-9]+ passed' "${LOG}" | tail -1 || true)"
case "${SUMMARY}" in
  [0-9]*) PASSED_N="${SUMMARY%% *}" ;;
  *)      PASSED_N="" ;;
esac
if [ -z "${PASSED_N}" ]; then
  echo "RESULT: FAIL (rc=${EXIT_CODE}, no \"N passed\" in summary — collection/selection failure or a fully-skipped module; GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "${PASSED_N}" -ne "${EXPECTED_PASSED_N}" ]; then
  echo "RESULT: FAIL (rc=${EXIT_CODE}, passed=${PASSED_N} != EXPECTED_PASSED_N=${EXPECTED_PASSED_N} — the node count moved without the pin moving)"
  exit 1
fi
if [ "$EXIT_CODE" -ne 0 ]; then
  echo "RESULT: FAIL (rc=${EXIT_CODE}, ${PASSED_N} passed before failure)"
  exit 1
fi

echo "RESULT: PASS (${PASSED_N} passed, rc=${EXIT_CODE})"
exit 0
