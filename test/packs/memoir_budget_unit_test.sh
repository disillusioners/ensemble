#!/usr/bin/env bash
# Test Pack: memoir_budget_unit_test
#
# Memoir Store v1 — phase 2 (plan:
# .agents/shared/planning/ari-memoir-store/phase2-plan.md D2.8 / D2.10 / D2.11 (the recall budget)).
#
# The pack runs the phase-2 legs:
#   tests/unit/test_memoir_budget_unit.py
#
# The per-episode clamp, the 6,000-token envelope with its
# RANKED-DROP (the head stays whole), the footer bytes, the FINAL
# RE-CHECK (the footer is part of the shipped bytes), and the MARKED-ID
# EQUALITY — a dropped episode is neither returned nor marked.
#
# INTERPRETER RESOLUTION (the house rule, and the wave-1 FIX):
#   1) <repo>/.venv/bin/python  — the worktree's OWN interpreter;
#   2) MEMOIR_PACK_PYTHON      — an EXPLICIT operator override;
#   3) otherwise FAIL LOUD.
#   There is NO silent fallback to a bare `python3`: a pack that cannot
#   find its interpreter must fail, not run against whatever is on PATH
#   (the wave-1 packs did that and it was flagged as a hygiene item).
#
# FENCES: the per-variable env scrub (the `env -u POSTGRES_*` glob form
# is a SILENT NO-OP — bash does not glob env names), `-p no:randomly`,
# the dual-layer timeout, and the collected-count guard (GREEN-AT-ZERO
# IS A FAILURE).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

PACK_NAME="memoir_budget_unit_test"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "${REPO_ROOT}" || exit 1

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo: ${REPO_ROOT}"

# ── the interpreter ───────────────────────────────────────────────────────
PY=""
if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PY="${REPO_ROOT}/.venv/bin/python"
elif [ -n "${MEMOIR_PACK_PYTHON:-}" ] && [ -x "${MEMOIR_PACK_PYTHON}" ]; then
  PY="${MEMOIR_PACK_PYTHON}"
fi
if [ -z "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — neither ${REPO_ROOT}/.venv/bin/python nor MEMOIR_PACK_PYTHON is executable)"
  exit 1
fi
echo "Interpreter: ${PY}"

# ── the per-variable env scrub (never the glob form) ──────────────────────
SCRUB=(
  -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_HOST -u POSTGRES_PORT
  -u POSTGRES_PASSWORD -u DATABASE_URL -u DATABASE_URL_POSTGRES
  -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD
  -u PGPASSFILE -u PGSSLMODE -u PGSERVICE -u PGSERVICEFILE
  -u PORT -u DAEMON_PORT
)

TARGET=(tests/unit/test_memoir_budget_unit.py)

MISSING=0
for t in "${TARGET[@]}"; do
  if [ ! -f "$t" ]; then
    echo "FAIL: missing test module '$t'"
    MISSING=1
  fi
done
if [ "${MISSING}" -ne 0 ]; then
  echo "RESULT: FAIL (a declared test module is missing — the leg would be vacuous)"
  exit 1
fi

LOG="$(mktemp -t memoir_budget_unit_test.XXXXXX)"
trap 'rm -f "${LOG}"' EXIT

timeout 300 env "${SCRUB[@]}" "${PY}" -m pytest "${TARGET[@]}" \
  -q --tb=short -rfE -p no:randomly --override-ini="addopts=" >"${LOG}" 2>&1
RC=$?
cat "${LOG}"

if [ "${RC}" -eq 124 ]; then
  echo "RESULT: TIMEOUT (300s cap exceeded)"
  exit 124
fi

SUMMARY="$(grep -Eo '[0-9]+ passed' "${LOG}" | tail -1 || true)"
case "${SUMMARY}" in
  [0-9]*) PASSED_N="${SUMMARY%% *}" ;;
  *)      PASSED_N="" ;;
esac
if [ -z "${PASSED_N}" ] || [ "${PASSED_N}" -lt 1 ]; then
  echo "RESULT: FAIL (rc=${RC}, collected-count check: no \"N passed\" N>=1 in summary — GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "${RC}" -ne 0 ]; then
  echo "RESULT: FAIL (rc=${RC}, ${PASSED_N} passed before failure)"
  exit 1
fi

echo "RESULT: PASS (${PASSED_N} passed, rc=${RC})"
exit 0
