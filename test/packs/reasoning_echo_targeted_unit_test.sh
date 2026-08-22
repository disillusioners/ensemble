#!/usr/bin/env bash
# Test Pack: reasoning_echo_targeted_unit_test
# Scope: targeted reasoning-echo suite for the allowlist→denylist flip
# (commits 28ea76a9 + 018800b8, ThinkingChatOpenAI in daemon/graph.py):
#   - tests/unit/test_reasoning_content_roundtrip.py
#   - tests/unit/test_reasoning_content_fallback.py
#   - tests/unit/test_reasoning_content_edge_cases.py
#   - tests/unit/test_llm_reasoning_echo_config.py
# Layer 1 (outer, caller-side): `timeout 300 ./test/packs/reasoning_echo_targeted_unit_test.sh`
# Layer 2 (inner, this script): `timeout 280s` around pytest — unit hard limit ~4.7 min.
set -u
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble

echo "=== Test Pack: reasoning_echo_targeted_unit_test ==="

OUT="$(mktemp)"
set -o pipefail
timeout 280s .venv/bin/pytest \
  tests/unit/test_reasoning_content_roundtrip.py \
  tests/unit/test_reasoning_content_fallback.py \
  tests/unit/test_reasoning_content_edge_cases.py \
  tests/unit/test_llm_reasoning_echo_config.py \
  --tb=short -q 2>&1 | tee "$OUT"
RC=$?

SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
rm -f "$OUT"
if [ -n "$SUMMARY" ]; then echo "SUMMARY: $SUMMARY"; fi

if [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
elif [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
else
  echo "RESULT: FAIL"
  exit 1
fi
