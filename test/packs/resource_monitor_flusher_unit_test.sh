#!/usr/bin/env bash
# Test Pack: resource_monitor_flusher_unit_test — the resource-monitor
# flusher repair's UNIT legs (T1 row identity, the shared PG scratch-DB
# fence, the ladder semantics).
#
# Scope:
#   * tests/test_resource_monitor_t1_row_identity.py — the sampler's
#     sample-time bucket_ts stamp (a multi-tick window lands MULTIPLE
#     rows), the degraded sentinel's per-drop count + the
#     MAX(missed_samples) recovery path, the raw retention sweep's
#     effective-interval range, and the column-audit removal check;
#   * tests/test_resource_monitor_pg_fence.py — the differential proof
#     that the SHARED PostgreSQL scratch-DB fence refuses the live
#     names (ensemble_prod / ensemble_dev / ensemble_prod_test) and
#     treats OWNERSHIP as a rejection-side signal only;
#   * tests/test_resource_monitor_ladder_semantics.py — the Linux-facing
#     ladder semantics, INCLUDING the slow-failing-read leg that is
#     RED at the base commit.
#
# NO database, NO network, NO /proc: these legs are hermetic (the
# platform read is mocked; the PG fence's ownership lookup is stubbed).
#
# Dual-layer timeout: Layer 1 = the dispatcher's outer `timeout 300`;
# Layer 2 = the internal watchdog below (300 s).
#
# The RESULT label is emitted by a TRAP, so it is printed on EVERY exit
# path (a leg that dies mid-run still reports).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -euo pipefail

PACK_NAME="resource_monitor_flusher_unit_test"
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

if [ ! -x ".venv/bin/pytest" ]; then
  RESULT="FAIL (no .venv/bin/pytest in ${REPO_ROOT})"
  exit 1
fi

TARGETS=(
  tests/test_resource_monitor_t1_row_identity.py
  tests/test_resource_monitor_pg_fence.py
  tests/test_resource_monitor_ladder_semantics.py
)

MISSING=0
for t in "${TARGETS[@]}"; do
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

LOG="$(mktemp -t rm_flusher_unit.XXXXXX)"

set +e
timeout 300s .venv/bin/pytest "${TARGETS[@]}" \
  -q --tb=short -p no:randomly >"${LOG}" 2>&1
RC="$?"
set -e

cat "${LOG}"

if [ "${RC}" -eq 124 ]; then
  RESULT="TIMEOUT (internal 300s cap exceeded)"
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
