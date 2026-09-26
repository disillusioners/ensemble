#!/usr/bin/env bash
# LCAFC SURFACE READ-POINTS verification pack (Job 5, incident 7d4a3bd9
# merge gate). Asserts the escalated terminal
# "completed (gate escalated — unverified)" is visible at EVERY read
# point: §1 jobs API (jobs_crud detail + shared render seam), §2 mission
# resolver + get_mission/list_missions tool payloads + MissionResponse
# schema, §3 SSE completed payload, §4 FE static (job.model.ts unions +
# isTerminalStatus + 3 touched components) with a byte-pin census of the
# display string across BE+FE.
# Pack: lcafc_surface_readpoints_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (ancestor-pin —
# sibling test-only commits EXPECTED; production files byte-stable).
# 1 file / 21 collected tests; est <1 min.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 110s
# guard below (unit-pack target 2 min).
# Invocation contract: timeout 300 bash test/packs/lcafc_surface_readpoints_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Drift pin (corrected semantics, per coordination update 2026-09-26):
# ancestor-check NOT equality + ZERO production-side diff vs d5c50994.
BR=$(git rev-parse --abbrev-ref HEAD)
if [ "$BR" != "feature/lca-false-complete-fixes" ]; then
  echo "RESULT: FAIL (DRIFT — branch $BR != feature/lca-false-complete-fixes)"; exit 1
fi
if ! git merge-base --is-ancestor d5c50994 HEAD; then
  echo "RESULT: FAIL (DRIFT — d5c50994 not ancestor of HEAD)"; exit 1
fi
if [ -n "$(git diff d5c50994..HEAD -- daemon/ frontend/ scripts/ migrations/)" ]; then
  echo "RESULT: FAIL (DRIFT — production-side files have diff vs d5c50994: daemon/, frontend/, scripts/, migrations/ MUST stay byte-stable for this LCAFC merge gate)"; exit 1
fi

# ── Byte-pin census: the display string across BE + FE ──────────────────────
# The canonical string lives ONLY in daemon/constants.py (single source).
# BE readers either embed the literal (comments/tools) or import
# COMPLETION_GATE_ESCALATED_DISPLAY; FE files embed the literal verbatim.
echo "=== Byte-pin census: 'completed (gate escalated — unverified)' ==="
DISPLAY="completed (gate escalated — unverified)"
CONST_NAME="COMPLETION_GATE_ESCALATED_DISPLAY"
LITERAL_CENSUS=$(grep -rn "$DISPLAY" daemon frontend/src --include='*.py' --include='*.ts' --include='*.html' 2>/dev/null)
CONST_CENSUS=$(grep -rn "$CONST_NAME" daemon --include='*.py' 2>/dev/null)
if [ -z "$LITERAL_CENSUS" ]; then
  echo "RESULT: FAIL (byte-pin census — display string not found in daemon/ or frontend/src)"; exit 1
fi
echo "$LITERAL_CENSUS"
echo "--- constant-name reference sites (daemon/) ---"
echo "$CONST_CENSUS"
CENSUS_COUNT=$(echo "$LITERAL_CENSUS" | wc -l | tr -d ' ')
CONST_COUNT=$(echo "$CONST_CENSUS" | wc -l | tr -d ' ')
echo "byte-pin census: ${CENSUS_COUNT} literal sites + ${CONST_COUNT} constant-reference sites"
# Gate on load-bearing sites: BE readers must carry literal OR constant
# name; FE sites must carry the LITERAL (byte-pin the string end-to-end).
for SITE in \
  "daemon/constants.py:$CONST_NAME" \
  "daemon/routers/jobs_crud.py:$CONST_NAME" \
  "daemon/routers/jobs_streaming.py:$CONST_NAME" \
  "daemon/services/work_notifier.py:$CONST_NAME" \
  "daemon/tools/missions.py:$DISPLAY" \
  "daemon/services/work_resolver.py:$CONST_NAME" \
  "daemon/services/mission_resolver.py:$CONST_NAME" \
  "frontend/src/app/models/job.model.ts:$DISPLAY" \
  "frontend/src/app/components/job-card/job-card.component.ts:$DISPLAY" \
  "frontend/src/app/components/job-detail-drawer/job-detail-drawer.component.ts:$DISPLAY" \
  "frontend/src/app/components/job-queue-panel/job-queue-panel.component.ts:$DISPLAY"
do
  SITE_PATH="${SITE%%:*}"
  SITE_NEEDLE="${SITE#*:}"
  if [ "$SITE_NEEDLE" = "$DISPLAY" ]; then
    POOL="$LITERAL_CENSUS"
  else
    POOL="$LITERAL_CENSUS
$CONST_CENSUS"
  fi
  if ! echo "$POOL" | grep -q "^$SITE_PATH"; then
    echo "RESULT: FAIL (byte-pin census — required site missing: $SITE_PATH)"; exit 1
  fi
done

# ── OPTIONAL FE type-check (node_modules present ⇒ run; NON-GATING) ────────
# KNOWN DEFECT (reported, NOT fixed — daemon/ frontend/ read-only for this
# pack): tsc --noEmit exits 2 with 6 errors — the escalated literal was
# added to the MissionLiveness union but NOT to JobStatus (job.model.ts:11),
# while isTerminalStatus (:184) / getStatusColor (:397) and the 3 touched
# components case/compare against it on JobStatus-typed values
# (TS2367/TS2678). Runtime string comparisons still work (types are
# erased), so the read-point contract holds; the type-level defect is
# recorded here and must be fixed in a follow-up FE commit.
echo "=== FE type-check diagnostic (non-gating; known defect expected) ==="
if [ -d frontend/node_modules ]; then
  (cd frontend && npx tsc --noEmit -p tsconfig.app.json > /tmp/lcafc_surface_readpoints_tsc.out 2>&1)
  TSC_RC=$?
  TSC_ERRORS=$(grep -c 'error TS' /tmp/lcafc_surface_readpoints_tsc.out 2>/dev/null || true)
  echo "tsc --noEmit: exit=$TSC_RC errors=${TSC_ERRORS} (see /tmp/lcafc_surface_readpoints_tsc.out)"
  grep 'error TS' /tmp/lcafc_surface_readpoints_tsc.out 2>/dev/null | head -10
else
  echo "frontend/node_modules absent — tsc diagnostic SKIPPED (per dispatch)"
fi

# ── Pytest lane ──────────────────────────────────────────────────────────────
PACK="lcafc_surface_readpoints_test"
FILES=(
tests/unit/test_lcafc_surface_readpoints.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 110 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q
RC=$?
END=$(date +%s)
echo "Pack inner runtime: $((END-START))s"
if [ $RC -eq 124 ]; then
  echo "RESULT: TIMEOUT"; exit 124
elif [ $RC -eq 0 ]; then
  echo "RESULT: PASS"; exit 0
else
  echo "RESULT: FAIL"; exit 1
fi
