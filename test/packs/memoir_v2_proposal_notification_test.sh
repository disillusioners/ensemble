#!/usr/bin/env bash
# Test Pack: memoir_v2_proposal_notification_test
#
# Memoir Store v2 — phase 6, the 1/DAY NOTIFICATION BRIDGE (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase6-plan.md §3.1 /
# T6.3; AC3 + AC6(a); module
# tests/unit/test_memoir_keepsake_proposals.py, the bridge classes).
#
# Scope (AC3):
#   * the SOURCE STRING is the plan's (``source="system:memoir-proposal"``,
#     V2-D 6.2 verbatim);
#   * the ledger slot is consumed ONLY on an ACCEPTED enqueue
#     (``pending → enqueued`` + ``notified_at``);
#   * every FAILURE path (enqueue raised / no running ari instance) leaves
#     the row ``pending`` — the day's slot is NOT burned;
#   * the 1/day gate keys on ``'enqueued'`` ONLY (a ``pending`` row does
#     not consume the day);
#   * RESTART SURVIVAL — a brand-new service over the same engine is still
#     silent on the same day;
#   * THE DATE ROLLOVER — a row STAGED on D and ENQUEUED on D+1 is
#     announced exactly ONCE (never re-announced on D+2+), the still-
#     pending proposal is not starved, and no calendar day receives two
#     notifications;
#   * the PRODUCTION bridge shape (``asyncio.run_coroutine_threadsafe`` to
#     the manager's loop) driven from a worker thread;
#   * NO PIN AUTHORITY (V2-D 6.3 / R2) and NO LLM CALLS in the phase.
#
# Interpreter resolution (house convention): the repo's OWN .venv first;
# MEMOIR_PACK_PYTHON overrides it. NEVER a bare ``python3`` — that would
# import ``daemon`` from the MAIN checkout and report GREEN about a tree
# that is not this worktree.
#
# FENCES: per-variable env scrub (``env -u POSTGRES_*`` is a silent NO-OP
# glob — bash does not glob env names), ``-p no:randomly``, the dual-layer
# timeout, and the collected-count guard (GREEN-AT-ZERO IS A FAILURE).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACK_NAME="memoir_v2_proposal_notification_test"
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

TARGET="tests/unit/test_memoir_keepsake_proposals.py"
if [ ! -f "$TARGET" ]; then
  echo "RESULT: FAIL (missing test module '$TARGET' — the leg would be vacuous)"
  exit 1
fi

# The MEASURED node count at the pin (2026-11-10, this worktree,
# ``--collect-only``): TestEnqueueBridge (9) + TestNoPinAuthority (3) = 12.
#
# TestEnqueueBridge grew 6 -> 9 in the phase-6 DELTA: the three CROSS-DAY
# legs (staged on D, notified on D+1) pin the 1/day gate's CONSUMPTION-day
# keying and the pending-only notify target.
EXPECTED_PASSED_N=12

SELECTOR="TestEnqueueBridge or TestNoPinAuthority"

TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-300}"
LOG="${TMPDIR:-/tmp}/${PACK_NAME}.log"

timeout "${TIMEOUT_S}" env "${SCRUB[@]}" "${PY}" -m pytest "${TARGET}" \
  -k "${SELECTOR}" \
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
