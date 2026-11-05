#!/usr/bin/env bash
# Test Pack: memoir_schema_unit_test
#
# Memoir Store v1 — phase 1 (plan:
# .agents/shared/planning/ari-memoir-store/phase1-plan.md AC1/AC2/AC6).
#
# The model registry census, the .sql applied on a throwaway SQLite DB
# with zero failing statements, the string-column-TEXT pin, the
# model-bound CHECK constraints, the StarsListType round-trip, and the
# migration census DELTA (one new file, unique prefix, distinct-prefix
# count == file count — re-derived, never a hardcoded absolute).
#
# Interpreter resolution (house convention): the repo's own .venv first.
# MEMOIR_PACK_PYTHON overrides it for worktree runs.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: memoir_schema_unit_test ==="
cd "$PROJECT_DIR"

PY=""
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PY="$PROJECT_DIR/.venv/bin/python"
elif [ -n "${MEMOIR_PACK_PYTHON:-}" ] && [ -x "${MEMOIR_PACK_PYTHON}" ]; then
  PY="${MEMOIR_PACK_PYTHON}"
fi
if [ -z "$PY" ]; then
  # FAIL LOUD (the false-green class): a bare `python3` fallback can
  # import `daemon` from the MAIN checkout, so the pack would test a
  # tree that is not this worktree and report GREEN. Never fall back.
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
echo "Interpreter: $PY"

TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-300}"

timeout "${TIMEOUT_S}" "${PY}" -m pytest tests/unit/test_memoir_schema_contract.py \
  -q --tb=short -p no:randomly --override-ini="addopts=" 2>&1
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
