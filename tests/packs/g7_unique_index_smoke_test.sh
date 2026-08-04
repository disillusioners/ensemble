#!/usr/bin/env bash
# G7 Unique Index Startup Smoke Test Pack
#
# Verifies that daemon/manager.py InstanceManager._ensure_blueprint_g7_unique_index
# does NOT raise AttributeError when iterating Project objects. This is the
# regression test for the bug where the function accessed project.id (which
# does not exist on the Project SQLModel — primary key is project_id).
#
# Layer 1: outer `timeout 300` (caller responsibility, not enforced here)
# Layer 2: inner signal.alarm(120) in the Python script
set -euo pipefail
cd "$(dirname "$0")/../.."
exec .venv/bin/python tests/packs/g7_unique_index_smoke_test.py