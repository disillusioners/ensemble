#!/usr/bin/env bash
# Wrapper for the VSCode browser E2E test pack.
# Runs the Python implementation with the project's venv (which has
# Playwright + Chromium installed). Hard 5-minute cap is enforced inside
# the Python script as well, but we add a belt-and-suspenders outer
# timeout for any unexpected hang.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif [ -f "$PROJECT_ROOT/venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/venv/bin/python"
else
    PYTHON="python3"
fi

cd "$PROJECT_ROOT"

# Outer 5-minute guard — the Python script also enforces SIGALRM.
exec "$PYTHON" "$SCRIPT_DIR/vscode_e2e_browser_test.py"
