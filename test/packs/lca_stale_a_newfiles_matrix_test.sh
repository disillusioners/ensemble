#!/usr/bin/env bash
# LCA stale-A (B1) merge gate — NEW-FILES MATRIX cohort (7 files, two legs)
# Pack: lca_stale_a_newfiles_matrix_test
# Worktree: feature/lca-stale-a-fix @ e0d15e93 (base a6442bff lineage).
# Cohort: the 7 test files NEW on this branch (dual-autopsy B1 stale-A fix):
#   unit (3):         test_lcau_anchor_security, test_lcau_caps_redaction,
#                     test_lcan_nonote_census
#   integration (4):  test_lcau_incident_e2e, test_lcan_childlie_e2e,
#                     test_lcan_legacy_checkpoint, test_lcan_sameturn_window
# Census at HEAD (collect-only): 71 nodes total — leg 1 collects 64 + deselects
# 7 (integration) under default addopts; leg 2 runs exactly those 7.
# Two legs:
#   (i)  default addopts (-m 'not integration and not postgres') → unit subset
#   (ii) --override-ini="addopts=" -m integration                → integration subset
# NOTE: none of the 4 cohort integration files carries a live-LLM-gated test —
# forcing -m integration here selects no real-LLM work (sibling lcau_matrix_
# intf_m/s1/s2/s3 packs own the live-LLM files).
# Dual-layer timeout: outer 'timeout 300' at invocation + inner shared 280s
# budget (leg1 ≤150s; leg2 gets the remainder, capped at 130s) so the TOTAL
# script stays < 5 min.
# Invocation contract: timeout 300 bash test/packs/lca_stale_a_newfiles_matrix_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lca_stale_a_newfiles_matrix_test"
ALL_FILES=(
  tests/unit/test_lcau_anchor_security.py
  tests/unit/test_lcau_caps_redaction.py
  tests/unit/test_lcan_nonote_census.py
  tests/integration/test_lcau_incident_e2e.py
  tests/integration/test_lcan_childlie_e2e.py
  tests/integration/test_lcan_legacy_checkpoint.py
  tests/integration/test_lcan_sameturn_window.py
)
INTF_FILES=(
  tests/integration/test_lcau_incident_e2e.py
  tests/integration/test_lcan_childlie_e2e.py
  tests/integration/test_lcan_legacy_checkpoint.py
  tests/integration/test_lcan_sameturn_window.py
)
echo "=== Test Pack: ${PACK} ==="
echo "File count: ${#ALL_FILES[@]} (expected 7 = 3 unit + 4 integration)"
START=$(date +%s)
TOTAL_BUDGET=280
LEG1_CAP=150
RC1=0
RC2=0
TIMED_OUT=0

echo "--- leg 1/2: default addopts (unit subset; integration auto-deselected) ---"
timeout "$LEG1_CAP" uv run python -m pytest "${ALL_FILES[@]}" --tb=short -q
RC1=$?
echo "leg 1 exit: $RC1"

ELAPSED=$(( $(date +%s) - START ))
LEG2_CAP=$(( TOTAL_BUDGET - ELAPSED ))
if [ "$LEG2_CAP" -gt 130 ]; then LEG2_CAP=130; fi
if [ "$RC1" -eq 124 ]; then
  TIMED_OUT=1
elif [ "$LEG2_CAP" -lt 30 ]; then
  echo "leg 2 skipped: budget exhausted (${ELAPSED}s elapsed, ${LEG2_CAP}s left)"
  TIMED_OUT=1
else
  echo "--- leg 2/2: -m integration (integration subset), cap ${LEG2_CAP}s ---"
  timeout "$LEG2_CAP" uv run python -m pytest "${INTF_FILES[@]}" --tb=short -q \
    --override-ini="addopts=" -m integration
  RC2=$?
  echo "leg 2 exit: $RC2"
  if [ "$RC2" -eq 124 ]; then TIMED_OUT=1; fi
fi

END=$(date +%s)
echo "Pack inner runtime: $((END-START))s"
if [ "$TIMED_OUT" -eq 1 ]; then
  echo "RESULT: TIMEOUT"; exit 124
elif [ "$RC1" -eq 0 ] && [ "$RC2" -eq 0 ]; then
  echo "RESULT: PASS"; exit 0
else
  echo "RESULT: FAIL (leg1=$RC1 leg2=$RC2)"; exit 1
fi
