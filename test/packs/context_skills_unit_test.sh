#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: context_skills_unit_test ==="
cd "$PROJECT_DIR"

# Unit pack — 2 min hard limit. Dual-layer timeout.
# NOTE: tests/unit/test_auto_load_skills.py and tests/unit/test_shared_context_injection.py
# were deleted in eeef8845 (legacy context injection removal).
# Repointed to test_context_injection which covers the same surface.
#
# EXTENDED by workstream F (skill-system-revival Phase 1) — the injection
# gate:
#   * tests/services/test_skill_identity_dedupe.py — the identity dedupe at
#     the injection boundary: at most ONE variant per (name, project_id)
#     reaches an agent, picked by the deterministic rule (highest LINEAGE
#     GENERATION, ties by id — never by timestamp).
#   * tests/services/test_skill_roster_manifest.py — the roster manifest
#     (the per-axis capture/injection roster) and the staged payload's
#     capture-lane gate.
# The budget arm and the F mutation matrix live in the NEW registered pack
# test/packs/skill_injection_budget_unit_test.sh.
timeout 110s .venv/bin/pytest \
  tests/unit/services/test_context_injection.py \
  tests/unit/test_skill_seeding.py \
  tests/unit/test_skill_clone_service.py \
  tests/services/test_skill_identity_dedupe.py \
  tests/services/test_skill_roster_manifest.py \
  -v --override-ini="addopts=" --tb=short -q 2>&1
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
