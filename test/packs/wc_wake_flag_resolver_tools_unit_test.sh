#!/usr/bin/env bash
# Test Pack: wc_wake_flag_resolver_tools_unit_test — kill-switch resolver +
# instance-tools unit tests for the wc-wake phase-1 gate.
#
# Background. The kill-switch's truthy/falsy spelling contract is pinned
# by test_wc_wake_flag_resolver.py (W2 in the 2026-08-30 pre-flip batch).
# The instance-tools suite (test_instance_tools.py) is the largest
# consumer of the resolved boolean and the W1 council's reference
# regression: the suite installs an autouse _reset_wc_wake_enqueue_flag_cache
# fixture so the cache leak vector (which causes the
# ``assert 200 == 202`` legacy-routing regression in cross-file pytest
# processes) cannot fire.
#
# Combining the two surfaces in one pytest invocation proves that the
# resolver's truthy contract and the instance-tools routing are stable
# together — the resolver file is the smaller surface, the instance
# tools file is the consumer; both share sys.modules for
# daemon.services.instance_messaging.
#
# The two files manage their own flag env via fixtures; the pack
# DELIBERATELY does NOT export ENSEMBLE_WC_WAKE_ENQUEUE so the
# unset-default-OFF contract is exercised as production sees it.
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 150s` on the pytest process
#     (unit-pack cap is 2 min; we cap at 150s for margin).
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: wc_wake_flag_resolver_tools_unit_test ==="
echo "(resolver truthy contract + instance-tools routing — flag-implicit)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 150s hard cap. The pair is ~210 tests
# and runs in ~10s; 150s is margin-rich. -p no:cacheprovider avoids
# writing .pytest_cache; -p no:asyncio would break the suite because
# instance_tools uses asyncio mode implicitly via @pytest.mark.asyncio,
# so we leave the default asyncio plugin on.
timeout 150s .venv/bin/pytest \
  tests/unit/services/test_wc_wake_flag_resolver.py \
  tests/unit/tools/test_instance_tools.py \
  --tb=short -q -ra -p no:cacheprovider 2>&1
RC=$?

if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
