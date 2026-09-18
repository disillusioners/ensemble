#!/usr/bin/env bash
# LCA user-intent incident E2E pack (incident 4dfded83 class) - independent construction at 47b56df8
# Pack: lcau_incident_e2e_integration_test
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 240s guard below.
# Invocation contract: timeout 300 bash test/packs/lcau_incident_e2e_integration_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcau_incident_e2e_integration_test"
FILES=(
  tests/integration/test_lcau_incident_e2e.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 240 uv run python -m pytest "${FILES[@]}" --tb=short -q
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
