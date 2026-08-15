#!/usr/bin/env bash
# Test Pack: child_error_resilience_unit_test
# Scope: the 2 NEW developer test files for the child-error-resilience feature:
#   - tests/unit/test_error_report_recovery_hint.py  (RECOVERY_GUIDANCE_HINT in _send_error_report)
#   - tests/unit/test_malformed_llm_response_guard.py (MalformedLLMResponseError type-guard)
# Layer 1 (outer, caller-side): `timeout 300 ./test/packs/child_error_resilience_unit_test.sh`
# Layer 2 (inner, this script): `timeout 120s` around pytest — unit hard limit 2 min.
set -u
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

echo "=== Test Pack: child_error_resilience_unit_test ==="

OUT="$(mktemp)"
set -o pipefail
timeout 120s "$PY" -m pytest \
  tests/unit/test_error_report_recovery_hint.py \
  tests/unit/test_malformed_llm_response_guard.py \
  --tb=short -q --override-ini="addopts=" 2>&1 | tee "$OUT"
RC=$?

# Surface the pytest pass/fail summary line (e.g. "12 passed in 0.34s").
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
