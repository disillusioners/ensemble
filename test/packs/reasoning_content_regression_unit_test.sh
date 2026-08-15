#!/usr/bin/env bash
# Test Pack: reasoning_content_regression_unit_test
# Scope: the 3 reasoning-content regression files (adjacent to the graph.py
# _create_chat_result override touched by the malformed-response guard):
#   - tests/unit/test_reasoning_content_roundtrip.py
#   - tests/unit/test_reasoning_content_edge_cases.py
#   - tests/unit/test_reasoning_content_fallback.py
# Layer 1 (outer, caller-side): `timeout 300 ./test/packs/reasoning_content_regression_unit_test.sh`
# Layer 2 (inner, this script): `timeout 120s` around pytest — unit hard limit 2 min.
set -u
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

echo "=== Test Pack: reasoning_content_regression_unit_test ==="

OUT="$(mktemp)"
set -o pipefail
timeout 120s "$PY" -m pytest \
  tests/unit/test_reasoning_content_roundtrip.py \
  tests/unit/test_reasoning_content_edge_cases.py \
  tests/unit/test_reasoning_content_fallback.py \
  --tb=short -q --override-ini="addopts=" 2>&1 | tee "$OUT"
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
