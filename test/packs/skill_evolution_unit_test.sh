#!/usr/bin/env bash
# Test Pack: skill_evolution_unit_test — Skill evolution unit tests
# Timeout: 2 minutes (120s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: skill_evolution_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/unit/test_skill_seeding.py \
  tests/unit/test_skill_clone_service.py \
  tests/unit/test_auto_load_skills.py \
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
