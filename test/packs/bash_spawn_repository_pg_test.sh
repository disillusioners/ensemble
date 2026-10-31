#!/usr/bin/env bash
# Test Pack: bash_spawn_repository_pg_test — the PostgreSQL lane for the
# bash-tool spawn repository (the persistence contract against a real
# database).
#
# Scope:
#   * tests/postgres/test_bash_spawn_repository_pg.py — 5 nodes,
#     pytestmark = postgres.
#
# Runner + invocation (verbatim per the gate brief):
#   ./scripts/run_tests_scrubbed.sh \
#     --override-ini="addopts=" -m postgres \
#     tests/postgres/test_bash_spawn_repository_pg.py -q --tb=short -rs
# The `--override-ini="addopts="` + `-m postgres` pair is REQUIRED: the
# default addopts deselect postgres-marked nodes.
#
# Runner: ./scripts/run_tests_scrubbed.sh — the env-scrub wrapper (it
# hardcodes `exec .venv/bin/pytest "$@"`, cds to the repo root, scrubs
# POSTGRES_*/libpq env, exits 78 on leak). NO PG_TEST_DB export is
# needed: the target file uses the shared lane default scratch DB
# (`ensemble_test`), already a member of tests/postgres/conftest.py's
# ALLOWED_SCRATCH_DBS fence — which the shared pg_engine fixture runs
# BEFORE any SQL.
#
# Requires a reachable PostgreSQL (the shared pg_engine fixture wires
# it) — this is the PG lane; execute it only where that lane runs.
#
# Dual-layer timeout: Layer 1 = the caller's outer `timeout 300`;
# Layer 2 = the internal watchdog below (280 s — under the outer cap so
# this pack's own RESULT verdict always lands first).
#
# The RESULT label is emitted by a TRAP, so it is printed on EVERY exit
# path (a leg that dies mid-run still reports).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -euo pipefail

PACK_NAME="bash_spawn_repository_pg_test"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RESULT="FAIL (pack did not run)"
LOG=""
on_exit() {
  echo "RESULT: ${RESULT}"
  if [ -n "${LOG}" ] && [ -f "${LOG}" ]; then
    rm -f "${LOG}"
  fi
}
trap on_exit EXIT

cd "${REPO_ROOT}" || {
  RESULT="FAIL (cannot cd to ${REPO_ROOT})"
  exit 1
}

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    ${REPO_ROOT}"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ ! -x "scripts/run_tests_scrubbed.sh" ]; then
  RESULT="FAIL (no scripts/run_tests_scrubbed.sh in ${REPO_ROOT})"
  exit 1
fi

TARGET=(
  tests/postgres/test_bash_spawn_repository_pg.py
)

MISSING=0
for t in "${TARGET[@]}"; do
  if [ ! -f "$t" ]; then
    echo "FAIL: missing test module '$t'"
    MISSING=1
  else
    echo "  - $t"
  fi
done
if [ "${MISSING}" -ne 0 ]; then
  RESULT="FAIL (a declared test module is missing — the leg would be vacuous)"
  exit 1
fi

LOG="$(mktemp -t bash_spawn_repo_pg.XXXXXX)"

set +e
timeout 280s ./scripts/run_tests_scrubbed.sh \
  --override-ini="addopts=" -m postgres \
  "${TARGET[@]}" \
  -q --tb=short -rs >"${LOG}" 2>&1
RC="$?"
set -e

cat "${LOG}"

if [ "${RC}" -eq 124 ]; then
  RESULT="TIMEOUT (internal 280s cap exceeded)"
  exit 124
fi

# Explicit collected-count guard — GREEN-AT-ZERO IS A FAILURE: a
# collection error, an import error or a fully-skipped module would
# otherwise report a vacuous green.
SUMMARY="$(grep -Eo '[0-9]+ passed' "${LOG}" | tail -1 || true)"
case "${SUMMARY}" in
  [0-9]*) PASSED_N="${SUMMARY%% *}" ;;
  *)      PASSED_N="" ;;
esac
if [ -z "${PASSED_N}" ] || [ "${PASSED_N}" -lt 1 ]; then
  RESULT="FAIL (rc=${RC}, collected-count check: no \"N passed\" N>=1 in summary — collection/selection failure or a fully-skipped module; GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "${RC}" -ne 0 ]; then
  RESULT="FAIL (rc=${RC}, ${PASSED_N} passed before failure)"
  exit 1
fi

RESULT="PASS (${PASSED_N} passed, rc=${RC})"
exit 0
