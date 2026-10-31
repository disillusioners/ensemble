#!/usr/bin/env bash
# Test Pack: bash_spawn_reaper_feature_test — the bash-tool orphan-reaper
# FEATURE legs (spawn repository contract, the reaper service, boot
# wiring, and the spawn-stamp plumbing the reaper's attribution rides on).
#
# Scope:
#   * tests/repositories/test_bash_spawn_repository.py — the spawn
#     repository contract (19 nodes);
#   * tests/unit/services/test_bash_spawn_reaper.py — the reaper's
#     orphan classification and reap decisions (133 nodes);
#   * tests/unit/services/test_bash_spawn_reaper_boot.py — boot-time
#     reaper wiring (14 nodes);
#   * tests/tools/test_bash_spawn_stamp.py — the spawn-stamp plumbing
#     (9 nodes).
#
# Census: 19+133+14+9 = 175 nodes. Includes REAL-PROCESS legs (the
# reaper exercises live child processes), so the budget is the full
# internal watchdog.
#
# Runner: ./scripts/run_tests_scrubbed.sh — the env-scrub wrapper (it
# hardcodes `exec .venv/bin/pytest "$@"`, cds to the repo root, scrubs
# POSTGRES_*/libpq env, exits 78 on leak).
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

PACK_NAME="bash_spawn_reaper_feature_test"
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

TARGETS=(
  tests/repositories/test_bash_spawn_repository.py
  tests/unit/services/test_bash_spawn_reaper.py
  tests/unit/services/test_bash_spawn_reaper_boot.py
  tests/tools/test_bash_spawn_stamp.py
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

LOG="$(mktemp -t bash_spawn_reaper.XXXXXX)"

set +e
timeout 280s ./scripts/run_tests_scrubbed.sh \
  "${TARGETS[@]}" \
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
