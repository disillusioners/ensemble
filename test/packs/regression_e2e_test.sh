#!/usr/bin/env bash
# Test Pack: regression_e2e_test — the e2e half of the retired P-12 partition
# Scope: tests/e2e/ (30 collected / 51 deselected at 2026-10-20, after the
# F5 --ignore and the one quarantine deselect below)
# Timeout: 5 minutes (300 s) — xdist parallelized
#
# === ORIGIN ===
# This pack replaces the retired P-12 partition
# `test/packs/regression_integration_opencode_e2e_test.sh`, DELETED upstream
# in `2c8d4b190` (2026-09-19) because the combined scope breached the 300 s
# cap. The PACKS.md row for P-12 (row ~1492) is a STALE POINTER to that
# deleted file — the tester lane owns PACKS.md and must re-point it at these
# three packs.
#
# The retired pack's recovered invocation was (verbatim, from
# `git show 2c8d4b190^:test/packs/regression_integration_opencode_e2e_test.sh`):
#
#   timeout 280s uv run pytest \
#     tests/integration/ tests/opencode/ tests/e2e/ \
#     -n auto --tb=short -q -rf \
#     --override-ini="timeout=240" \
#     --deselect "tests/e2e/test_e2e_workflows.py::test_pause_after_spawn_then_resume"
#
# This pack MIRRORS that invocation's marker selection, deselects and per-test
# timeout override. It deliberately does NOT clear the default addopts
# (`-m 'not integration and not postgres and not slow'`), so the slice stays
# comparable to the registered pack's collected baseline.
#
# `tests/opencode/` is EXCLUDED BY DESIGN: `daemon/opencode/` and
# `tests/opencode/` were archived to `legacy/` by `300ac0ba4`
# (`daemon.opencode` is unimportable). Resurrecting that half is out of scope.
#
# === F5 EXCLUSION (known blocker) ===
#   `--ignore=tests/e2e/test_context_injection_hybrid.py`
#   That module performs a MODULE-LEVEL `requests.get("http://localhost:8079")`
#   at IMPORT time. When port 8079 is free (the normal test-host state) the
#   import raises ConnectionError and pytest ABORTS COLLECTION for the whole
#   file — a collection-time abort, not a test failure, so the node never
#   reports a verdict. This is the F5 blocker: PRE-EXISTING and NOT introduced
#   by any branch under test (the file is absent from every branch diff).
#   Keep the --ignore until the module's import-time probe is made lazy.
#
# === QUARANTINE DESELECT ===
#   tests/e2e/test_e2e_workflows.py::test_pause_after_spawn_then_resume
#   QUARANTINE.md 2026-08-21 — post-resume terminal-status stall (Task↔JobItem
#   reconciliation-gap family). The retired P-12 pack deselected it too; the
#   deselect travels with this slice.
#
# === DUAL-LAYER TIMEOUT ===
#   * Layer 2 (script-internal): 280 s — interrupts hung tests
#   * Layer 1 (command-level): 300 s via `timeout` wrapper
#   * Per-test timeout: 240 s via `--override-ini="timeout=240"`
#
# Invocation contract:
#   timeout 300 bash test/packs/regression_e2e_test.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_e2e_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Runner resolution — `uv run pytest` mirrors the retired P-12 pack.
# `--no-sync` keeps the shared worktree venv untouched (no network sync,
# no re-resolve) while still running the project interpreter + pytest.
if [ -x "${HOME}/.local/bin/uv" ]; then
  PYTEST=( "${HOME}/.local/bin/uv" run --no-sync pytest )
elif command -v uv >/dev/null 2>&1; then
  PYTEST=( uv run --no-sync pytest )
else
  PYTEST=( "${PROJECT_DIR}/.venv/bin/python" -m pytest )
fi

# RESULT-echo: `|| EXIT_CODE=$?` list-context capture — under `set -e`, a bare
# `EXIT_CODE=$?` after a failing command never executes (silent exit, no RESULT).
EXIT_CODE=0
timeout 280s "${PYTEST[@]}" \
  tests/e2e/ \
  -n auto --tb=short -q -rf \
  --override-ini="timeout=240" \
  --ignore=tests/e2e/test_context_injection_hybrid.py \
  --deselect "tests/e2e/test_e2e_workflows.py::test_pause_after_spawn_then_resume" \
  2>&1 || EXIT_CODE=$?
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
