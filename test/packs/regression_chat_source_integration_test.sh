#!/usr/bin/env bash
# Test Pack: regression_chat_source_integration_test
# Scope: tests/integration/test_chat_source_*.py (11 files at 2026-09-19,
# 50 tests). Self-maintaining glob — any new test_chat_source_*.py file
# joins the pack automatically.
# Timeout: 1 minute (60s) — 22s solo per measurement 2026-09-19, ~60s with xdist under load
#
# Split from regression_integration_opencode_e2e_test.sh (P-12 in
# PACKS.md) — chat-source-worker-lane integration family was
# SILENTLY-DESELECTED by the default ``pyproject.toml`` addopts
# (``addopts = "-m 'not integration and not postgres'"``); every
# test in this family carries ``pytestmark = pytest.mark.integration``
# and was therefore never executed under the default addopts. This
# pack makes the selection REAL by passing the explicit marker plus
# the addopts override:
#
#   --override-ini="addopts="   # clear the project's default addopts
#   -m integration              # select only integration-marked tests
#
# which selects ONLY the chat-source worker-lane tests (50 tests,
# 11 files — none of which carry ``pytest.mark.postgres`` per the
# census, so no PG-only test slippage).
#
# Invariants:
#   * No PG-marked tests confirmed by grep over the file list (no
#     ``pytest.mark.postgres``); pg-only gates stay where they are
#     under the ``postgres`` pytest marker, NOT this pack.
#   * Inner guard 50s (under the 60s outer); solo ~22s typical.
#
# Invocation contract:
#   timeout 60 bash test/packs/regression_chat_source_integration_test.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_chat_source_integration_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

EXIT_CODE=0
timeout 50s .venv/bin/python -m pytest \
  tests/integration/test_chat_source_*.py \
  --override-ini="addopts=" \
  -m integration \
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
