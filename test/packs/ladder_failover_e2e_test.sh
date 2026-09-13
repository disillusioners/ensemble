#!/usr/bin/env bash
# Test Pack: ladder_failover_e2e_test — P2 summarizer failover e2e coverage
#
# Closes the P2 gap on the REAL ``wrap_langchain_failover`` facade path
# through ``SymptomRepairEngine._summarize`` (ADR-0006). Pre-existing
# coverage in tests/unit/test_symptom_repair_engine.py
# (``TestFacadeRouting``) stubs the facade entirely — this pack exercises
# the engine end-to-end through the REAL facade with a FAILING primary
# + WORKING backup, and verifies the degenerate (BOTH primary and backup
# fail) abort path.
#
# Covering files (exactly 1):
#   1. tests/unit/test_symptom_repair_engine_failover_e2e.py  (10 tests)
#
# Branch: feature/hallucination-recovery-ladder
# Engine: daemon/services/symptom_repair_engine.py
#   - class SymptomRepairEngine(:202)
#   - SYMPTOM_REPAIR_BUDGET=3 (:84)
#   - DEFAULT_SUMMARIZATION_TIMEOUT_S=120 (:91)
# Facade: daemon/services/llm_failover.py
#   - wrap_langchain_failover(:617)
#   - ChatFailoverBinding(:490) — real tenacity Retrying loop
#   - FailoverController(:526, llm_error_classifier.py) — real swap
# F1 — REAL facade: primary fails, backup returns valid summary
#     → repair SUCCEEDS via backup (surgery happens, budget +1,
#       repair doc contains the backup summary).
# F2 — REAL facade: BOTH primary and backup fail after facade retry+failover
#     → repair ABORTS fail-open (success=False, surgery_prefix=None,
#       budget_consumed=False, abort_reason="summarizer-failed",
#       repaired_messages == original).
# F3 — Source-level pin: no legacy static fallback string remains
#     in the engine source (the shipped LoopRepairer keeps its fallback
#     for the kill-switch-OFF path; the engine deliberately does NOT
#     reproduce it, ADR-0006 decision).
# F4 — 120s summarizer timeout parameter inherited from
#     LoopBreakerConfig.summarization_timeout_seconds (config.py:1755).
#
# Mocking strategy: the inner chat client's ``invoke`` is mocked to
# inspect ``root_client.base_url`` and dispatch to primary vs backup
# outcome. Everything ABOVE that layer is REAL — the real
# ``wrap_langchain_failover``, the real ``ChatFailoverBinding`` with
# tenacity retries, the real ``FailoverController`` mutating
# ``base_url`` on swap, and the real engine catch/return-carrier
# logic. No network calls.
#
# Script-internal timeout (Layer 2): 120s
# Command-level timeout (Layer 1):  300s
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ladder_failover_e2e_test ==="
echo "Branch: $(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD) @ $(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
echo ""

cd "$PROJECT_DIR"

CANDIDATE_FILES=(
  "tests/unit/test_symptom_repair_engine_failover_e2e.py"
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

echo "[note] Running ${#EXISTING_FILES[@]} test file(s):"
for e in "${EXISTING_FILES[@]}"; do
  echo "  - $e"
done

if [ ${#EXISTING_FILES[@]} -eq 0 ]; then
  echo "[fatal] No test files exist — nothing to run."
  echo "RESULT: FAIL"
  exit 1
fi

echo ""
echo "=== Running tests (dual-layer: 120s internal + 300s outer) ==="
timeout 300s uv run python -m pytest \
  "${EXISTING_FILES[@]}" \
  --override-ini="addopts=" --tb=short -q \
  --timeout=120 \
  2>&1

EXIT_CODE=$?

echo ""
echo "=== Post-run drift check ==="
POST_BRANCH=$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD)
POST_COMMIT=$(git -C "$PROJECT_DIR" rev-parse --short HEAD)
echo "Post-run branch: $POST_BRANCH"
echo "Post-run commit: $POST_COMMIT"

if [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
elif [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
else
  echo "RESULT: FAIL"
  exit 1
fi
