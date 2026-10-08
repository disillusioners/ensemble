#!/usr/bin/env bash
# Test Pack: compaction_unit_test — Compaction and idle timeout unit tests
#
# Authoritative compaction-scoped test list for the NEVER-BLOCKED
# commission (fix/compaction-never-blocked @ c600af60d, see
# ``docs/agent-prompt-writing-guide.md`` for pathspec discipline).
# The pack is the single source of truth the tester runs against
# — its 22-file breadth is the implementer's verified scope; any
# narrower run is a SUBSET and must not be reported as the
# commission's compaction-suite count.
#
# Iteration-3 (REVIEWER MINOR-3) added the 16 files beyond the
# original 6 — each one is either directly compaction-related
# (e.g. test_compact_executor, test_proactive_compaction_fix_*,
# test_compaction_never_blocked) or the supporting graph /
# classifier / response-validation / find-near-instance harness
# that the compaction tests depend on. The iteration-3
# implementer's FULL-suite run reported 781 passed in 43.22s;
# this pack reproduces that count.
#
# Timeout: 3 minutes (180s) — leaves headroom for the 22-file
# breadth (the 43.22s run is the median; CI variance is ~2x).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: compaction_unit_test (22-file authoritative breadth) ==="

cd "$PROJECT_DIR"

timeout 180s .venv/bin/pytest \
  tests/unit/test_compaction.py \
  tests/unit/test_compaction_never_blocked.py \
  tests/unit/test_compaction_empty_guard_fallback.py \
  tests/unit/test_compaction_multimodal.py \
  tests/unit/test_compaction_model_config.py \
  tests/unit/tools/test_inner_soul_compaction.py \
  tests/unit/services/test_proactive_compaction_fix_p1.py \
  tests/unit/services/test_proactive_compaction_fix_p1b.py \
  tests/unit/services/test_proactive_compaction_fix_p2.py \
  tests/unit/services/test_proactive_compaction_symptom_acceptance.py \
  tests/unit/services/test_compact_executor.py \
  tests/unit/services/test_compact_fired_watchers_deliver_before_compact.py \
  tests/unit/services/test_compact_executor_revive_brick_e2e.py \
  tests/unit/services/test_compact_executor_defect1_pause_resume_lifecycle.py \
  tests/services/test_instance_messaging_compaction_guard.py \
  tests/unit/services/test_injected_notes_hoisting.py \
  tests/unit/services/test_injected_notes_hoisting_sweep_gaps.py \
  tests/test_injection_compaction.py \
  tests/unit/test_find_near_instance.py \
  tests/unit/test_graph_retry_integration.py \
  tests/unit/test_llm_error_classifier.py \
  tests/unit/test_response_validation.py \
  --tb=line -q 2>&1

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
