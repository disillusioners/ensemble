#!/usr/bin/env bash
# fe_jest_baseline_test.sh — baseline re-run at parent commit dd14d975^
#
# Goal: prove the 4 FE Jest failures claimed pre-existing reproduce at the
# fix's parent commit (the commit BEFORE dd14d975 = "fix(settings): make
# settings container the scroll container").
#
# Scope: run ONLY two spec files in an isolated worktree at dd14d975^:
#   - jobs-filter-state.model.spec.ts
#   - jobs-grouping.model.spec.ts
#
# Safety:
#   - Hard isolation: git worktree at dd14d975^; main checkout untouched
#   - Symlink (or fallback copy) of frontend/node_modules
#   - No DB / network / ports touched
#   - EXIT trap always removes the worktree, even on failure/timeout
#
# Dual-layer timeout (innate test-pack invariant):
#   - Outer (caller): `timeout 300 bash <this-script>`
#   - Inner: this script's WATCHDOG_S=240 hard-kill fallback
#
# Output: RESULT line {PASS|FAIL|TIMEOUT} + exit {0|1|124}

set -u
set -o pipefail

# -------- config --------
PARENT_SHA="dd14d975^"
WT_PATH="/tmp/ens-settings-baseline"
LOG_DIR="/home/nea/ensemble-src/.agents/tester/RESULTS/assets/2026-09-26-settings-scroll"
LOG_FILE="${LOG_DIR}/fe_jest_baseline.log"
WATCHDOG_S=240
REPO="/home/nea/ensemble-src"
NM_SRC="${REPO}/frontend/node_modules"
WT_NM="${WT_PATH}/frontend/node_modules"

mkdir -p "${LOG_DIR}"

# -------- helpers --------
log() { echo "[$(date -u +%H:%M:%SZ)] $*" | tee -a "${LOG_FILE}"; }

cleanup() {
  local rc=$?
  log "CLEANUP: removing worktree ${WT_PATH} (exit=${rc})"
  if [[ -d "${WT_PATH}" ]]; then
    git -C "${REPO}" worktree remove --force "${WT_PATH}" 2>>"${LOG_FILE}" || true
    # remove any leftover symlink/copy target even if worktree remove failed
    rm -f "${WT_NM}" 2>/dev/null || true
    # belt-and-braces: nuke if it still exists
    if [[ -d "${WT_PATH}" ]]; then
      rm -rf "${WT_PATH}" 2>/dev/null || true
    fi
  fi
  log "CLEANUP: post-state:"
  git -C "${REPO}" worktree list 2>>"${LOG_FILE}" | tee -a "${LOG_FILE}" >/dev/null
  exit "${rc}"
}
trap cleanup EXIT INT TERM

# -------- preflight --------
: > "${LOG_FILE}"
log "=== fe_jest_baseline_test.sh ==="
log "REPO=${REPO}"
log "PARENT_SHA=${PARENT_SHA}"
log "WT_PATH=${WT_PATH}"
log "WATCHDOG_S=${WATCHDOG_S}"
log "HEAD verification:"
git -C "${REPO}" log -1 --format='HEAD=%H %s' 2>>"${LOG_FILE}" | tee -a "${LOG_FILE}" >/dev/null
log "Resolved parent:"
RESOLVED_PARENT="$(git -C "${REPO}" rev-parse "${PARENT_SHA}" 2>>"${LOG_FILE}")"
log "  ${RESOLVED_PARENT}"
echo "RESOLVED_PARENT=${RESOLVED_PARENT}" >> "${LOG_FILE}"

# Hard safety: refuse if worktree path is occupied
if [[ -e "${WT_PATH}" ]]; then
  log "FAIL: ${WT_PATH} already exists; refusing to clobber"
  exit 1
fi

# -------- step 1: worktree --------
log "STEP 1: git worktree add --detach ${WT_PATH} ${PARENT_SHA}"
if ! git -C "${REPO}" worktree add --detach "${WT_PATH}" "${PARENT_SHA}" 2>>"${LOG_FILE}"; then
  log "FAIL: worktree add failed"
  exit 1
fi
log "  worktree created"

# -------- step 2: node_modules --------
log "STEP 2: share frontend/node_modules via symlink"
if ! ln -s "${NM_SRC}" "${WT_NM}" 2>>"${LOG_FILE}"; then
  log "  symlink failed; falling back to copy"
  if ! cp -a "${NM_SRC}" "${WT_NM}" 2>>"${LOG_FILE}"; then
    log "FAIL: could not provide node_modules to worktree"
    exit 1
  fi
  log "  node_modules copied"
else
  log "  node_modules symlinked"
fi

# -------- step 3: locate spec files --------
log "STEP 3: locate the two spec files"
SPEC1="$(find "${WT_PATH}/frontend/src" -name 'jobs-filter-state.model.spec.ts' 2>/dev/null | head -1)"
SPEC2="$(find "${WT_PATH}/frontend/src" -name 'jobs-grouping.model.spec.ts' 2>/dev/null | head -1)"
log "  SPEC1=${SPEC1:-<missing>}"
log "  SPEC2=${SPEC2:-<missing>}"
if [[ -z "${SPEC1}" || -z "${SPEC2}" ]]; then
  log "FAIL: one or both spec files not found under ${WT_PATH}/frontend/src"
  exit 1
fi

# -------- step 4: run jest with watchdog --------
log "STEP 4: run jest --runTestsByPath for the 2 spec files"
cd "${WT_PATH}/frontend" || { log "FAIL: cd failed"; exit 1; }

(
  # watchdog: kill the jest process group if it overruns WATCHDOG_S
  (
    sleep "${WATCHDOG_S}"
    log "WATCHDOG: ${WATCHDOG_S}s elapsed; sending SIGTERM to jest pgid"
    pkill -TERM -f 'jest' 2>/dev/null || true
    sleep 5
    pkill -KILL -f 'jest' 2>/dev/null || true
  ) &
  WATCHDOG_PID=$!

  CI=true npx jest --runTestsByPath "${SPEC1}" "${SPEC2}" --colors=false \
    >> "${LOG_FILE}" 2>&1
  JEST_RC=$?

  kill "${WATCHDOG_PID}" 2>/dev/null || true
  wait "${WATCHDOG_PID}" 2>/dev/null || true

  echo "JEST_RC=${JEST_RC}" >> "${LOG_FILE}"
  log "  jest exited rc=${JEST_RC}"
  exit "${JEST_RC}"
)
JEST_RC=$?

# Map jest exit codes:
#   0  -> all tests passed at parent (unexpected: claim refuted)
#   1  -> jest reported failures (expected shape)
#   124 -> our outer timeout (not reached here; inner watchdog handled)
#   anything else: pass through
log "STEP 5: classify"
echo "JEST_RC_FINAL=${JEST_RC}" >> "${LOG_FILE}"

# Summary parsing: grep the failure count + JOB_STATUS_VALUES enum signature
log "  --- failure summary (post-run grep) ---"
{
  echo "== Failed-test lines =="
  grep -E '^ *✕|^ *FAIL |^Tests:.*failed' "${LOG_FILE}" 2>/dev/null | head -80 || true
  echo ""
  echo "== JOB_STATUS_VALUES enum-drift signature =="
  grep -nE 'JOB_STATUS_VALUES|jobs-model|enum' "${LOG_FILE}" 2>/dev/null | head -40 || true
  echo ""
  echo "== Number of failed tests in summary =="
  grep -E '^Tests:' "${LOG_FILE}" 2>/dev/null | tail -5 || true
} >> "${LOG_FILE}"

if [[ "${JEST_RC}" -eq 124 ]]; then
  log "RESULT: TIMEOUT"
  echo "RESULT: TIMEOUT"
  exit 124
elif [[ "${JEST_RC}" -eq 0 ]]; then
  log "RESULT: PASS (zero jest failures at parent — claim refuted; document loudly)"
  echo "RESULT: FAIL"
  echo "NOTE: parent commit shows ZERO failures; pre-existing claim NOT supported"
  exit 1
elif [[ "${JEST_RC}" -eq 1 ]]; then
  log "RESULT: FAIL (jest reported failures; now compare against known set)"
  echo "RESULT: FAIL"
  exit 1
else
  log "RESULT: FAIL (unexpected jest rc=${JEST_RC})"
  echo "RESULT: FAIL"
  exit 1
fi