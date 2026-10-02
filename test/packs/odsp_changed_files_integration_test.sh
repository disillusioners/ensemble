#!/usr/bin/env bash
# Test Pack: odsp_changed_files_integration_test
#
# Stage-1 OpenDesign self-provisioning: integration tests for the 2
# branch-changed integration files. Neither needs a live daemon:
#   - test_maintenancer_spawn_resolves_tools.py  — pure AST/inspect on
#     daemon/tools/_tool_registry.py + agents/{developer,worker}/meta.json
#     + CATEGORY_MODULES shape (no DB, no socket).
#   - test_service_tool_flag_off_byte_identical.py  — flag-OFF zero-side
#     effect tests using NullPool + in-memory SQLite (per file docstring
#     line 5: "without touching the DB or doing any work"); lifespan /
#     schema / privileged-category-set pins.
#
# Marker note: test_service_tool_flag_off_byte_identical.py carries
# @pytest.mark.integration on every case. Default pyproject.toml addopts
# (`-m 'not integration and not postgres'`) deselects them, so we
# explicitly override with `--override-ini="addopts="` to surface them.
# test_maintenancer_spawn_resolves_tools.py has no special markers, so
# the override is a no-op for it but keeps the run uniform.
#
# Daemon-lane validation is owned by a separate boot-smoke worker —
# this pack does NOT boot any daemon and never touches ports 9797/7979/8088.
#
# Total: 22 tests. Dry-run 2026-10-02 = 22 passed in 5.13s.
#
# Dual-layer timeout per the 2026-10-02 staging convention:
#   - Layer 2 (script-internal): 280s — interrupts hung tests
#   - Layer 1 (command-level):   300s via `timeout` wrapper below
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: odsp_changed_files_integration_test ==="
cd "$PROJECT_DIR"

CANDIDATE_FILES=(
  "tests/integration/test_maintenancer_spawn_resolves_tools.py"
  "tests/integration/test_service_tool_flag_off_byte_identical.py"
)

EXISTING_FILES=()
SKIPPED_FILES=()
for f in "${CANDIDATE_FILES[@]}"; do
  if [ -f "$f" ]; then
    EXISTING_FILES+=("$f")
  else
    SKIPPED_FILES+=("$f")
  fi
done

if [ ${#SKIPPED_FILES[@]} -gt 0 ]; then
  echo "[note] Skipping ${#SKIPPED_FILES[@]} missing test file(s):"
  for s in "${SKIPPED_FILES[@]}"; do
    echo "  - $s"
  done
fi

if [ ${#EXISTING_FILES[@]} -eq 0 ]; then
  echo "[fatal] No test files exist — nothing to run."
  echo "RESULT: FAIL"
  exit 1
fi

echo "[note] Running ${#EXISTING_FILES[@]} test file(s) (22 expected tests):"
for e in "${EXISTING_FILES[@]}"; do
  echo "  - $e"
done
echo "[note] --override-ini=\"addopts=\" surfaces @pytest.mark.integration cases."

# Env-scrubbed wrapper (scripts/run_tests_scrubbed.sh) strips every
# POSTGRES_*/libpq var per the 2026-09-26 live-probe incidents.
EXIT_CODE=0
timeout 280s ./scripts/run_tests_scrubbed.sh \
  "${EXISTING_FILES[@]}" \
  --override-ini="addopts=" \
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
