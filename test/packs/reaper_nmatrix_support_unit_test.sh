#!/usr/bin/env bash
# Test Pack: reaper_nmatrix_support_unit_test — the N-matrix support
# families plus the service-tool kill-site exemption contract.
#
# TWO sections — the second needs a marker override:
#   Section 1 — DEFAULT lane (repo addopts as-is):
#     * tests/test_tools.py — the N-matrix tool tests (38 nodes);
#     * tests/test_chart_tools.py — the chart-tool family (43 nodes).
#     Census: 38+43 = 81 nodes.
#   Section 2 — INTEGRATION lane (explicit override): the service-tool
#     kill-site exemption file carries `@pytest.mark.integration`, which
#     the default addopts DESELECT — collect-only census verified it
#     collects 0 under the default lane ("no tests collected (12
#     deselected)"). It therefore runs in its OWN invocation with
#     `--override-ini="addopts=" -m integration` so its 12 nodes
#     actually execute:
#     * tests/integration/test_service_tool_kill_site_exemption.py
#     Census: 12 nodes. Pack total: 81 + 12 = 93.
#
# Runner: ./scripts/run_tests_scrubbed.sh — the env-scrub wrapper (it
# hardcodes `exec .venv/bin/pytest "$@"`, cds to the repo root, scrubs
# POSTGRES_*/libpq env, exits 78 on leak).
#
# Dual-layer timeout: Layer 1 = the caller's outer `timeout 300`;
# Layer 2 = the internal watchdog below (280 s PER SECTION — under the
# outer cap so this pack's own RESULT verdict always lands first).
#
# The RESULT label is emitted by a TRAP, so it is printed on EVERY exit
# path (a leg that dies mid-run still reports).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -euo pipefail

PACK_NAME="reaper_nmatrix_support_unit_test"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RESULT="FAIL (pack did not run)"
LOG1=""
LOG2=""
on_exit() {
  echo "RESULT: ${RESULT}"
  if [ -n "${LOG1}" ] && [ -f "${LOG1}" ]; then
    rm -f "${LOG1}"
  fi
  if [ -n "${LOG2}" ] && [ -f "${LOG2}" ]; then
    rm -f "${LOG2}"
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

SECTION1_TARGETS=(
  tests/test_tools.py
  tests/test_chart_tools.py
)
SECTION2_TARGETS=(
  tests/integration/test_service_tool_kill_site_exemption.py
)

MISSING=0
for t in "${SECTION1_TARGETS[@]}" "${SECTION2_TARGETS[@]}"; do
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

# --- Section 1: default lane (repo addopts as-is). ----------------------
echo "--- section 1: default lane (N-matrix + chart-tool families) ---"
LOG1="$(mktemp -t reaper_nmatrix_s1.XXXXXX)"

set +e
timeout 280s ./scripts/run_tests_scrubbed.sh \
  "${SECTION1_TARGETS[@]}" \
  -q --tb=short -rs >"${LOG1}" 2>&1
RC1="$?"
set -e

cat "${LOG1}"

if [ "${RC1}" -eq 124 ]; then
  RESULT="TIMEOUT (section 1 exceeded the internal 280s cap)"
  exit 124
fi

# Explicit collected-count guard — GREEN-AT-ZERO IS A FAILURE: a
# collection error, an import error or a fully-skipped module would
# otherwise report a vacuous green.
SUMMARY1="$(grep -Eo '[0-9]+ passed' "${LOG1}" | tail -1 || true)"
case "${SUMMARY1}" in
  [0-9]*) PASSED1_N="${SUMMARY1%% *}" ;;
  *)      PASSED1_N="" ;;
esac
if [ -z "${PASSED1_N}" ] || [ "${PASSED1_N}" -lt 1 ]; then
  RESULT="FAIL (section 1 rc=${RC1}, collected-count check: no \"N passed\" N>=1 in summary — collection/selection failure or a fully-skipped module; GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "${RC1}" -ne 0 ]; then
  RESULT="FAIL (section 1 rc=${RC1}, ${PASSED1_N} passed before failure)"
  exit 1
fi

# --- Section 2: integration lane (marker override). ----------------------
echo "--- section 2: integration lane (--override-ini=\"addopts=\" -m integration) ---"
LOG2="$(mktemp -t reaper_nmatrix_s2.XXXXXX)"

set +e
timeout 280s ./scripts/run_tests_scrubbed.sh \
  --override-ini="addopts=" -m integration \
  "${SECTION2_TARGETS[@]}" \
  -q --tb=short -rs >"${LOG2}" 2>&1
RC2="$?"
set -e

cat "${LOG2}"

if [ "${RC2}" -eq 124 ]; then
  RESULT="TIMEOUT (section 2 exceeded the internal 280s cap)"
  exit 124
fi

SUMMARY2="$(grep -Eo '[0-9]+ passed' "${LOG2}" | tail -1 || true)"
case "${SUMMARY2}" in
  [0-9]*) PASSED2_N="${SUMMARY2%% *}" ;;
  *)      PASSED2_N="" ;;
esac
if [ -z "${PASSED2_N}" ] || [ "${PASSED2_N}" -lt 1 ]; then
  RESULT="FAIL (section 2 rc=${RC2}, collected-count check: no \"N passed\" N>=1 in summary — collection/selection failure or a fully-skipped module; GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "${RC2}" -ne 0 ]; then
  RESULT="FAIL (section 2 rc=${RC2}, ${PASSED2_N} passed before failure)"
  exit 1
fi

RESULT="PASS (section 1: ${PASSED1_N} passed, rc=${RC1}; section 2: ${PASSED2_N} passed, rc=${RC2})"
exit 0
