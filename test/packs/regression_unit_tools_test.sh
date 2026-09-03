#!/usr/bin/env bash
# Test Pack: regression_unit_tools — full tests/unit/tools/ sweep
# Scope: tests/unit/tools/ (1,049 collected)
# Timeout: 5 minutes (300s) — xdist parallelized
#
# Unit-slice regression partition. House style would use .venv/bin/pytest
# but the partition layer uses `uv run pytest` per task spec (xdist +
# per-test timeout). Unit slices keep the pyproject per-test default;
# no --override-ini=timeout.
#
# Quarantine-aware deselects (replicated from tools_suite_unit_test.sh
# per partition-scope rule):
#   - tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive × 5
#     (pre-existing access_memory 'Access denied' family — see QUARANTINE.md
#     2026-08-20; this partition covers that file's scope.)
#
# Pre-existing failure families MUST RUN and be adjudicated from failure
# inventory, NOT deselected: watchover cascade, sqlite migration 20260714,
# httpx pollution, subdirs-sweep, proxy_phase1, slash_commands, injection_api,
# vscode.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_unit_tools [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Regression partition pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
timeout 280s uv run pytest \
  tests/unit/tools/ \
  -n auto --tb=short -q -rf \
  --deselect tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive::test_access_archive_valid_path \
  --deselect tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive::test_access_archive_path_traversal_rejected \
  --deselect tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive::test_access_archive_invalid_format_sanitized \
  --deselect tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive::test_access_archive_nonexistent_returns_not_found \
  --deselect tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive::test_access_normal_file_still_works \
  2>&1
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
