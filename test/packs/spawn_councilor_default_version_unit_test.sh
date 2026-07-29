#!/usr/bin/env bash
# Test Pack: spawn_councilor_default_version_unit_test
# Tests: tests/unit/tools/test_spawn_councilor_default_version.py
# Timeout: 2 minutes (120s)
#
# Covers W3 — spawn_councilor resolves version internally (like spawn_instance):
#   - spawn_councilor with no explicit version_tag uses the default version
#   - convene_council and convene_council_with_skill still work correctly
#   - version_tag is no longer accepted as a public parameter
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: spawn_councilor_default_version_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/unit/tools/test_spawn_councilor_default_version.py \
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
