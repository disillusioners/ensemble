#!/usr/bin/env bash
# Test Pack: memoir_v2_keepsake_proposals_test
#
# Memoir Store v2 — phase 6, the KEEPSAKE PROPOSAL pipeline (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase6-plan.md §3.1 /
# T6.1 / T6.2; AC1 + AC2; module
# tests/unit/test_memoir_keepsake_proposals.py).
#
# Scope (AC1 + AC2):
#   * QUALIFICATION — ``COALESCE(poignancy, importance) >= 8`` AND
#     not-a-keepsake AND not-superseded AND the enduring-significance
#     heuristic (a below-threshold episode is NEVER proposed);
#   * ONCE-EVER-PER-EPISODE (V2-36 / W2) — a second attempt is a SKIP for
#     ANY prior outcome, and the durable uq_memoir_proposal_agent_episode
#     constraint is the racing guard (IntegrityError ⇒ skip, no leak);
#   * the [PROPOSALS] ZONE — the plan's header + line shape, the EXPLICIT
#     JOIN onto memoir_episodes, the pending/enqueued filter, the plan's
#     ordering, the ≤1,000-char measured bound (header wrapper included)
#     and the 3-line cap;
#   * the §A6 ADMISSION LADDER (verbatim rung order ``fit dynamic → trim
#     dynamic → trim proposals (drop weakest first) → rung-2 withhold``)
#     and the V2-D 6.5 reachability analysis.
#
# The BRIDGE (AC3) lives in the sibling pack
# ``memoir_v2_proposal_notification_test.sh``.
#
# Interpreter resolution (house convention): the repo's OWN .venv first;
# MEMOIR_PACK_PYTHON overrides it. NEVER a bare ``python3`` — that would
# import ``daemon`` from the MAIN checkout and report GREEN about a tree
# that is not this worktree.
#
# FENCES: per-variable env scrub (the ``env -u POSTGRES_*`` glob form is a
# SILENT NO-OP — bash does not glob env names), ``-p no:randomly``, the
# dual-layer timeout, and the collected-count guard (GREEN-AT-ZERO IS A
# FAILURE).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACK_NAME="memoir_v2_keepsake_proposals_test"
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
  -u ENSEMBLE_MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER
)

TARGET="tests/unit/test_memoir_keepsake_proposals.py"
if [ ! -f "$TARGET" ]; then
  echo "RESULT: FAIL (missing test module '$TARGET' — the leg would be vacuous)"
  exit 1
fi

# The MEASURED node count at the pin (2026-11-10, this worktree,
# ``--collect-only``): TestQualification (6) + TestOnceEver (6)
# + TestCandidateWindow (3) + TestProposalsZoneSelection (5)
# + TestProposalsZoneRender (6) + TestAdmissionLadder (6)
# + TestReflectionPassIntegration (3) + TestFetchCapMultiplier (4) = 39.
#
# TestAdmissionLadder grew 5 -> 6 in the phase-6 DELTA: the canonical
# §A6 quote's PRESENCE pin (the source copy soft-wraps, so that leg is
# whitespace-normalized).
#
# TestCandidateWindow (phase-6 closure delta): W-1 probe (ledgered
# head-of-line) + same-class superseded head-of-line — both pin the
# query-side exclusion so a ledgered/superseded row can no longer eat
# a window slot and starve eligible rows below the LIMIT.
#
# TestCandidateWindow grew 2 -> 3 in the phase-6 CLOSURE-2 delta: the
# W-2 probe (a TERSE head-of-line) pins the bounded over-fetch — the
# substance floor is enforced read-side, so without the over-fetch a
# terse head holds the window slots on EVERY pass (a permanent wedge,
# not the self-healing ledgered case). RED at the parent commit
# 78bde0c0a (executed — see docs/memoir-v2/implementation-notes.md
# §16.4 receipt (h)).
#
# TestFetchCapMultiplier grew 0 -> 4 in the phase-6 CLOSURE-3 delta: the
# fetch-cap multiplier knob (the SANCTIONED ESCAPE for a pathological
# corpus). The legs are (a) the DEFAULT keeps the MEASURED boundary
# (29 terse ⇒ the tail still stages; 30 terse ⇒ the permanent
# deferral), (b) a RAISED multiplier reaches the eligible tail, (c) a
# GARBAGE multiplier fails CLOSED to the default (never a wide scan),
# (d) an UNSET multiplier resolves to the default with the LOUD
# budget-class warning. RED at the parent commit 019741440 (executed —
# the scratch `git archive` parent tree + THIS commit's test file: 4
# failed / 48 deselected — see docs/memoir-v2/implementation-notes.md
# §16.8).
#
# LANE RULE (binding): this delta's own legs land selector-registered and
# re-pinned IN THE SAME COMMIT — every class below is named EXPLICITLY, so
# a leg no pack selects can never hide from the gate.
EXPECTED_PASSED_N=39

SELECTOR=(
  "TestQualification or TestOnceEver or TestCandidateWindow"
  "or TestProposalsZoneSelection or TestProposalsZoneRender"
  "or TestAdmissionLadder or TestReflectionPassIntegration"
  "or TestFetchCapMultiplier"
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
