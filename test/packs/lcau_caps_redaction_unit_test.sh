#!/usr/bin/env bash
# LCA user-intent section U — cap + redaction witness pack.
# Pack: lcau_caps_redaction_unit_test
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 180s unit-lane deadline.
# Invocation contract: timeout 300 bash test/packs/lcau_caps_redaction_unit_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcau_caps_redaction_unit_test"
FILES=(
  tests/unit/test_lcau_caps_redaction.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
# Inner guard: 180s unit-lane deadline (matches spec's unit scope).
timeout 180 uv run python -m pytest "${FILES[@]}" --tb=short -q
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