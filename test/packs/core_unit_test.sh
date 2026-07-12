#!/usr/bin/env bash
# Test Pack: core_unit_test — Core daemon unit tests
# Timeout: 2 minutes (120s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: core_unit_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs
# Use .venv/bin/pytest explicitly because the system pytest in /opt/homebrew/bin
# is broken on this host (ImportError: cannot import name '_console_main' from
# '_pytest.config'). The project venv (Python 3.13.3, pytest 9.0.2) works.
timeout 120s .venv/bin/pytest \
  tests/test_agents_api.py \
  tests/test_cancellation.py \
  tests/test_config.py \
  tests/test_help_tool.py \
  tests/test_instance_title.py \
  tests/test_loader.py \
  tests/test_manager.py \
  tests/test_memory_system.py \
  tests/test_migration_api_comprehensive.py \
  tests/test_migration_system_comprehensive.py \
  tests/test_models.py \
  tests/test_persistence.py \
  tests/test_project_store.py \
  tests/test_project_store_sqlmodel.py \
  tests/test_project_tools.py \
  tests/test_queue.py \
  tests/test_registry.py \
  tests/test_telegram_adapter.py \
  tests/test_tools.py \
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
