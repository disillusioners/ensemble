#!/usr/bin/env bash
# Test Pack: shared_context_tool_filter_check — Static agent tool-filter audit
# Timeout: 30s
#
# Verifies that the ``shared_context`` tool category appears in
# ``tools.allow`` for EVERY agent definition (20 active + 2 templates
# = 22 total). A missing entry means the agent cannot use the
# ``shared_context_metadata`` tool — this is a config-level guard,
# not a runtime check.
#
# Uses ``python -c`` for JSON parsing (more portable than ``jq``).
# Exits 0 if all 22 agents have ``"shared_context"`` in ``tools.allow``,
# exits 1 otherwise (with the offending agent's id on stdout/stderr).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: shared_context_tool_filter_check ==="

cd "$PROJECT_DIR"

# Use the project venv python so the standard library json module
# is guaranteed to be on the path.
PYTHON_BIN=".venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

# The 22 agent directories whose meta.json we audit.
AGENTS=(
  "approver"
  "ari"
  "charter"
  "coder"
  "developer"
  "devops"
  "experiencer"
  "explorer"
  "gaia"
  "giter"
  "jober"
  "kb-importer"
  "leader"
  "planner"
  "reviewer"
  "skill-keeper"
  "tester"
  "tidier"
  "wanderer"
  "worker"
  "_baby_template"
  "_mother"
)

EXPECTED_COUNT=${#AGENTS[@]}
FAIL=0

"$PYTHON_BIN" - "$EXPECTED_COUNT" "${AGENTS[@]}" <<'PYEOF'
import json
import sys
from pathlib import Path

expected_count = int(sys.argv[1])
agent_ids = sys.argv[2:]

project_root = Path.cwd()
agents_dir = project_root / "agents"

missing = []
malformed = []

for agent_id in agent_ids:
    meta_path = agents_dir / agent_id / "meta.json"
    if not meta_path.is_file():
        missing.append((agent_id, f"meta.json not found at {meta_path}"))
        continue

    try:
        with meta_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as e:
        malformed.append((agent_id, f"invalid JSON: {e}"))
        continue

    tools = data.get("tools")
    if not isinstance(tools, dict):
        missing.append((agent_id, "missing or non-dict 'tools'"))
        continue

    allow = tools.get("allow")
    if not isinstance(allow, list):
        missing.append((agent_id, "'tools.allow' missing or not a list"))
        continue

    if "shared_context" not in allow:
        missing.append((agent_id, "'shared_context' not in tools.allow"))
        continue

# Report.
if missing:
    print(f"FAIL: {len(missing)}/{expected_count} agents missing 'shared_context':", file=sys.stderr)
    for agent_id, reason in missing:
        print(f"  - {agent_id}: {reason}", file=sys.stderr)
    sys.exit(1)

if malformed:
    print(f"FAIL: {len(malformed)}/{expected_count} agents have malformed meta.json:", file=sys.stderr)
    for agent_id, reason in malformed:
        print(f"  - {agent_id}: {reason}", file=sys.stderr)
    sys.exit(1)

print(f"OK: all {expected_count} agents have 'shared_context' in tools.allow")
sys.exit(0)
PYEOF

PY_EXIT=$?

if [ $PY_EXIT -ne 0 ]; then
  echo "RESULT: FAIL"
  exit 1
fi

echo "RESULT: PASS"
exit 0