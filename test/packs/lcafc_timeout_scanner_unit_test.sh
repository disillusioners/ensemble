#!/usr/bin/env bash
# LCAFC SEMANTICS verification pack (Jobs 3 + 4).
# Pack: lcafc_timeout_scanner_unit_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (incident 7d4a3bd9 merge
# gate — correct-judge-override class: unverified surface + all-timeout
# no-terminal rule + directive nudge + scanner pin).
# Splitter-generated 2026-09-26. 1 file / 6 collected tests; est <1 min.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 110s guard
# below (unit-pack target 2 min; inner ≤110s per spec).
# Invocation contract: timeout 300 bash test/packs/lcafc_timeout_scanner_unit_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# CORRECTED drift pin (per coordination update 2026-09-26):
# * Ancestor-check, NOT equality (sibling commits are EXPECTED on
#   feature/lca-false-complete-fixes — the pg-lane + boot-smoke sibling
#   packs already shipped; HEAD may have advanced).
# * Real invariant: ZERO production-side diff vs d5c50994
#   (daemon/ frontend/ scripts/ migrations/ MUST stay byte-stable —
#   this is the LCAFC merge-gate scope).
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

PACK="lcafc_timeout_scanner_unit_test"
FILES=(
tests/unit/test_lcafc_timeout_scanner.py
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
