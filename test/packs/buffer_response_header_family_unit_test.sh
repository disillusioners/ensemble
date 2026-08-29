#!/usr/bin/env bash
# Test Pack: buffer_response_header_family_unit_test — Quick-Wins #2 item 3
# (5 mock-fix files / 53F→161P fixture-drift family)
# Timeout: 2 minutes (120s) — target < 2 min runtime
#
# Runs ALL 5 test files modified by feature/stability-quick-wins-2 commit
# 0eaf21be (test(llm): add buffer_response_header to config mocks — the
# 53-failure fixture-drift family). Production added `buffer_response_header`
# to LLMConfig (daemon/config.py:231, default True) and reads it from
# instance_lifecycle.py:916, title_generation.py:104, child_reports.py:766
# and :1400 — but `MagicMock(spec=LLMConfig)` does NOT auto-expose pydantic
# fields, so the 5 mocks below went AttributeError-quiet until each mock
# site was patched to set `buffer_response_header = True` explicitly.
#
#   1. tests/unit/test_llm_config_override.py           (1 attr site)
#      - create_mock_config() mock_llm spec=LLMConfig fixture
#   2. tests/unit/test_llm_failover_v2.py               (4 attr sites)
#      - _FakeManager, TestKeywordExtractionIsFailoverWired,
#        TestChildReportsIsFailoverWired, TestPreCleanRebindRegressionPin
#   3. tests/unit/test_llm_failover_v2_adversarial.py   (3 attr sites)
#      - _manager_stub, TestZeroBehaviorChangeAllSitesBackupUnset,
#        TestMockTransportFailoverBothFamilies
#   4. tests/unit/test_llm_failover_v2_resilience.py    (6 attr sites)
#      - TestLatencyCaps (×2), TestFallbackComposition (×4 incl.
#        keyword_extraction keyword_extraction.py:377 call site)
#   5. tests/test_llm_load_balance_integration.py       (1 attr site)
#      - create_mock_config() mock_llm spec=LLMConfig fixture
#
# Per-file overlap with existing individual packs:
#   - llm_config_override_unit_test.sh        covers #1
#   - llm_failover_v2_unit_test.sh            covers #2
#   - llm_failover_v2_adversarial_unit_test.sh covers #3
#   - llm_failover_v2_resilience_unit_test.sh covers #4
#   - NO existing pack covers #5 — this pack is its first dedicated
#     coverage. Overlap on #1–#4 is intentional (single-shot gate view
#     over the full family, mirroring wedge_fix_suites_unit_test.sh).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 120s` pytest guard
#
# No deselection: no QUARANTINE.md entries for these files (the family
# was previously quarantined as a fixture-drift failure; commit 0eaf21be
# is the fix that makes the mocks representative of real config).
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: buffer_response_header_family_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/unit/test_llm_config_override.py \
  tests/unit/test_llm_failover_v2.py \
  tests/unit/test_llm_failover_v2_adversarial.py \
  tests/unit/test_llm_failover_v2_resilience.py \
  tests/test_llm_load_balance_integration.py \
  --tb=short -q 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
