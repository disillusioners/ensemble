#!/usr/bin/env bash
# LCA anchor-security adversarial pack (SOURCE U injection-proofing, 4dfded83 class)
# Pack: lcau_anchor_security_unit_test
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 180s guard below (unit lane).
# Invocation contract: timeout 300 bash test/packs/lcau_anchor_security_unit_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcau_anchor_security_unit_test"
FILES=(
  tests/unit/test_lcau_anchor_security.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
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
