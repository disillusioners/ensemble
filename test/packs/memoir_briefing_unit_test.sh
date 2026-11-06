#!/usr/bin/env bash
# Test Pack: memoir_briefing_unit_test
#
# Memoir Store v1 — phase 5 (plan:
# .agents/shared/planning/ari-memoir-store/phase5-plan.md T5.2/T5.5, AC2/AC5;
# N-3 — the char/token measurement lives here).
#
# The pack runs the phase-5 renderer legs:
#   tests/unit/test_memoir_briefing_unit.py
#
# Pinned here: the two-zone render (stable never truncated / dynamic
# oldest-dropped-first), the byte-pinned surfaces (keepsake-line
# template, affordance line, the 24,000-char overage disclosure, the
# 48,000-char withhold WARNING, the persona-missing prefix), the
# render-time backstop (stable-in-full + dynamic-dropped; withhold
# returns None), the knob gate, the presence predicate — and the N-3
# MEASUREMENT: the house-estimator char/token ratio for DR-1's
# admitted stable worst case (persona <=2,000 + keepsakes <=4,000
# tokens) against the 24,000-char ceiling, with the resulting
# expected dynamic-zone survival REPORTED in the log.
#
# INTERPRETER RESOLUTION (the house rule, and the wave-1 FIX):
#   1) <repo>/.venv/bin/python  — the worktree's OWN interpreter;
#   2) MEMOIR_PACK_PYTHON      — an EXPLICIT operator override;
#   3) otherwise FAIL LOUD.
#   There is NO silent fallback to a bare `python3`.
#
# FENCES: the per-variable env scrub (the `env -u POSTGRES_*` glob form
# is a SILENT NO-OP — bash does not glob env names), `-p no:randomly`,
# the dual-layer timeout, and the collected-count guard (GREEN-AT-ZERO
# IS A FAILURE). No InstanceManager is constructed anywhere in the
# suite; every engine is a throwaway in-memory SQLite StaticPool.
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

PACK_NAME="memoir_briefing_unit_test"
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
  -u ENSEMBLE_MEMOIR_ENABLED
)

TARGET=(tests/unit/test_memoir_briefing_unit.py)

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

#: The MEASURED node count at the pin (2026-11-05, this worktree):
#  `-lt 1` let a silently-shortened selection pass; an exact count
#  turns "a test disappeared" into a RED instead of a quieter green
#  (tidier M12). Re-measure and move this constant deliberately.
EXPECTED_PASSED_N=17

LOG="$(mktemp -t memoir_briefing_unit_test.XXXXXX)"
trap 'rm -f "${LOG}"' EXIT

timeout 300 env "${SCRUB[@]}" "${PY}" -m pytest "${TARGET[@]}" \
  -q --tb=short -rfE -p no:randomly --override-ini="addopts=" -s >"${LOG}" 2>&1
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
if [ -z "${PASSED_N}" ]; then
  echo "RESULT: FAIL (rc=${RC}, collected-count check: no \"N passed\" in summary — GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "${PASSED_N}" -ne "${EXPECTED_PASSED_N}" ]; then
  echo "RESULT: FAIL (rc=${RC}, passed=${PASSED_N} != EXPECTED_PASSED_N=${EXPECTED_PASSED_N} — the node count moved without the pin moving)"
  exit 1
fi
if [ "${RC}" -ne 0 ]; then
  echo "RESULT: FAIL (rc=${RC}, ${PASSED_N} passed before failure)"
  exit 1
fi

echo "RESULT: PASS (${PASSED_N} passed, rc=${RC})"
exit 0
