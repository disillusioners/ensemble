#!/usr/bin/env bash
# Test Pack: ladder_exhaust_oq5_test
# Phase-1 P1c+d verification — recovery ladder, hallucination-recovery worktree.
#
# Scope:
#   * symptom_repair_engine unit tests (full file)
#   * TestBudgetResetOQ5   — E4 (real HumanMessage resets) + E5 ×4
#                              negative arms (empty-nudge, language-reminder,
#                              attestation-nudge, [SYSTEM CONTEXT]/context_kind)
#   * TestExhaustionEscalation — E1 (loud terminal at cap) + E3 (OFF-mode
#                                  WARN+continue preserved byte-identically)
#   * TestSymptomTelemetry — E2 ([SYMPTOM] shape: terminal escalate carries
#                              repair-budget-exhausted; non-terminal lines
#                              carry axis=ram-per-turn|durable-task; terminal
#                              lines have no axis=)
#   * TestBudgetIncrement  — B-2 sanity (success → +1, abort → 0, accumulate)
#   * TestP9NoCounterTheft (partition) — E6: durable budget does NOT cross
#                                       with S5/S1/RAM/language/failover
#                                       counters
#   * TestRealHumanPredicate (partition) — E6 supporting: predicate
#                                          positive/negative shapes
#
# Dual-layer timeout: inner 120s (pack-internal hard ceiling) + outer
# 300s (command-level cap). Run ONLY this pack; do not run any other
# pack or suite.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ladder_exhaust_oq5_test ==="

cd "$PROJECT_DIR"

# Layer 2: pack-internal timer (SIGTERM the pytest tree at 120s so the
# outer 300s cap is a true backstop, not the only line of defense).
( sleep 120; kill -TERM -$$ 2>/dev/null ) &
INTERNAL_WATCHDOG_PID=$!

cleanup_internal() {
  if kill -0 "$INTERNAL_WATCHDOG_PID" 2>/dev/null; then
    kill "$INTERNAL_WATCHDOG_PID" 2>/dev/null || true
  fi
}
trap cleanup_internal EXIT

# Layer 1: command-level cap.
timeout 300s .venv/bin/pytest \
  tests/unit/test_symptom_repair_engine.py \
  "tests/unit/test_symptom_repair_ladder.py::TestBudgetResetOQ5" \
  "tests/unit/test_symptom_repair_ladder.py::TestExhaustionEscalation" \
  "tests/unit/test_symptom_repair_ladder.py::TestSymptomTelemetry" \
  "tests/unit/test_symptom_repair_ladder.py::TestBudgetIncrement" \
  "tests/unit/test_symptom_repair_partition.py::TestP9NoCounterTheft" \
  "tests/unit/test_symptom_repair_partition.py::TestRealHumanPredicate" \
  --tb=short -q \
  --override-ini="addopts=" \
  2>&1

EXIT_CODE=$?

cleanup_internal

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
