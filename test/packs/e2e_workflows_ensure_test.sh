#!/usr/bin/env bash
# Test Pack: e2e_workflows_ensure_test — E2E workflow ensure.md Release Gate (4 tests)
# Timeout: 5 minutes (300s hard cap)
#
# Runs the 4 E2E workflow tests listed in .agents/tester/rules/ensure.md
# Release Gate against the live daemon (localhost:8079, started via ./dev.sh).
#
# Dual-layer timeout (per test-pack skill, agents/_prompt_system/innate-skills/test-pack/skill.md):
#   - Layer 1 (command-level): bash `timeout 300` outer guard — caps damage even if
#     the script hangs outside pytest (e.g., setup shell hangs).
#   - Layer 2 (script-internal): `PYTEST_TIMEOUT=280` — pytest-timeout inner guard
#     that interrupts an individual hung test. 20s margin under the outer cap so the
#     two timers never fire simultaneously and produce a confused exit code.
#
# Prerequisites: daemon must be running on localhost:8079 (start with `./dev.sh`).
# These are integration tests marked with @pytest.mark.integration; the daemon
# exposes the live HTTP API that the tests exercise.
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────────
# Prior sessions can leave SSL_CERT_FILE/SSL_CERT_DIR pointing at stale PyInstaller
# temp paths or deleted venv certs, causing httpx SSL errors when the daemon calls
# the LLM API. Unset both before invoking pytest so the test process inherits a
# clean TLS environment. See LESSONS:
#   .agents/tester/LESSONS/e2e-architecture-migration-2026-06-27.md
#   .agents/tester/LESSONS/e2e-defer-seam-validation-2026-07-01.md
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: e2e_workflows_ensure_test ==="

cd "$PROJECT_DIR"

# ─── Run the 4 ensure.md Release Gate tests ─────────────────────────────────────
# Tests (from .agents/tester/rules/ensure.md, Release Gate section):
#   1. test_parent_child_workflow_happy_path
#   2. test_pause_after_spawn_then_resume
#   3. test_terminate_after_spawn_then_revive
#   4. test_wave_spawn_with_defer_queue
#
# Flag rationale:
#   .venv/bin/pytest                       — project venv (Python 3.13, pytest 9.0.2).
#                                            System pytest at /opt/homebrew/bin is
#                                            broken on this host (see core_unit_test.sh
#                                            header for the gory details).
#   --override-ini="addopts="              — strip project-level pytest addopts so
#                                            this script's flags aren't shadowed
#                                            by config-level options like -x.
#   -m integration                          — only run @pytest.mark.integration tests.
#   -k "<4 names or'd>"                     — select the 4 Release Gate tests by name.
#   --tb=short -q                           — short tracebacks, quiet summary; do NOT
#                                            pass -x because we want to see the result
#                                            of all 4 tests even if one fails (per
#                                            test-pack skill: "Any fail → FAIL", with
#                                            the full picture surfaced).
#
# Env: PYTEST_TIMEOUT=280 is the inner guard (pytest-timeout plugin reads this and
# enforces a per-test deadline of 280s). Any single hung test is killed there; the
# outer `timeout 300` catches anything that escapes pytest-timeout (e.g., pytest
# itself hung outside a test, plugin import loop, etc.).
PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest \
    tests/e2e/test_e2e_workflows.py \
    --override-ini="addopts=" \
    -m integration \
    -k "test_parent_child_workflow_happy_path or test_pause_after_spawn_then_resume or test_terminate_after_spawn_then_revive or test_wave_spawn_with_defer_queue" \
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
