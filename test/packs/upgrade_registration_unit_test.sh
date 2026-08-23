#!/usr/bin/env bash
# Test Pack: upgrade_registration_unit_test
# Tests: tests/unit/tools/test_upgrade_registration.py
# Timeout: 2 minutes (120s)
#
# P2.2 (self-restart-upgrade phase 2, Dispatch C): system_upgrade category
# registration + default-deny — phase2-plan T2 acceptance + R-SR16.
#   - 4-step registration checklist asserted greppably AND functionally:
#     (1) AST source discovery finds all 4 tools; (2) CATEGORY_MODULES
#     entry; (3) DYNAMIC_TOOL_NAMES (+ KNOWN_TOOL_NAMES) carry the 4;
#     (4) the CRITICAL create_instance_tools list-append present in source
#   - functional default-deny via the REAL create_instance_tools path with
#     staged synthetic agents (tmp registry): tools.allow=["system_upgrade"]
#     resolves ALL 4 tool objects; without it NONE — including the
#     empty-allow (watcher-like) and no-tools-config paths (R-SR16)
#   - deny wins (category-named deny strips all 4; individual tool deny
#     strips only that tool; individual allow grants only that tool)
#   - docs paths mirror execution: help._get_allowed_tools +
#     loader.load_tools_doc_for_agent never advertise the category to
#     empty-allow agents (no system-prompt leak)
#   - real meta resolution: ari resolves all 4; worker/jober/watcher none
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: upgrade_registration_unit_test ==="

cd "$PROJECT_DIR"

# Unit pack — 2 min hard limit. Dual-layer timeout.
# Internal watchdog (Layer 2): 110s `timeout` wrap below — interrupts hung tests.
# Layer 1 (outer) is the dispatcher's `timeout 120s` wrap.
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
  tests/unit/tools/test_upgrade_registration.py \
  --tb=short -q 2>&1 || EXIT_CODE=$?

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
