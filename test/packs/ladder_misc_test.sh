#!/usr/bin/env bash
# Test Pack: ladder_misc_test — P2 misc verification suite
#
# Covers the dispatcher's M1-M4 round:
#   * M1 — W2 L2-precall-skip BEHAVIORAL (precall hook is consulted on
#     every superstep EXCEPT the durable-repair superstep). Covered by
#     existing tests in tests/unit/test_symptom_repair_ladder.py.
#   * M2 — Telemetry axis suffix AS-RUN (graph.py:1771-1823 emit fn):
#     (a) source-level enumeration of axis= sites (the shipped path has
#     4 ram-per-turn + 4 durable-task = 8 non-terminal sites + 1
#     terminal axis-free site); (b) behavioral pin via TestSymptomTelemetry
#     in tests/unit/test_symptom_repair_ladder.py.
#   * M3 — Perf sanity probe (INFORMATIONAL): build a 300+ message
#     history with a 4x trailing identical loop, run LoopDetector.scan,
#     assert wall time < 2s. Authored in tests/unit/test_ladder_misc_p2.py.
#   * M4 — FE-adjacent static checks (no SSE error-lane, no empty
#     bubble). Static asserts in tests/unit/test_ladder_misc_p2.py
#     + existing TestExhaustionEscalation tests.
#
# Branch: feature/hallucination-recovery-ladder @ 2668e56300
# Engine: daemon/graph.py (LoopDetector, _emit_symptom_telemetry,
#                         _maybe_durable_loop_repair, _maybe_repair_loop)
#
# Dual-layer timeout: inner 150s watchdog + outer 300s command-level cap.
# Run ONLY this pack; do not run any other pack or suite.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ladder_misc_test ==="
echo "Branch: $(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD) @ $(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
echo ""

cd "$PROJECT_DIR"

# ── Pre-flight: M4a — git diff must show ZERO frontend/ files between
# the planner commit (57b1e0c2) and HEAD. Frontend was untouched by the
# ladder work; any diff is a contract violation.
echo "=== M4a: frontend/ untouched between 57b1e0c2..HEAD ==="
FRONTEND_DIFF=$(git diff --name-only 57b1e0c2..HEAD -- frontend/ 2>&1 || true)
if [ -z "$FRONTEND_DIFF" ]; then
  echo "[OK] frontend/ untouched (empty diff)."
else
  echo "[FAIL] frontend/ files modified by ladder work:"
  echo "$FRONTEND_DIFF"
  echo "RESULT: FAIL"
  exit 1
fi
echo ""

# Pack-internal watchdog (Layer 2). SIGTERM the pytest tree at 150s so
# the outer 300s cap is a true backstop, not the only line of defense.
( sleep 150; kill -TERM -$$ 2>/dev/null ) &
INTERNAL_WATCHDOG_PID=$!

cleanup_internal() {
  if kill -0 "$INTERNAL_WATCHDOG_PID" 2>/dev/null; then
    kill "$INTERNAL_WATCHDOG_PID" 2>/dev/null || true
  fi
}
trap cleanup_internal EXIT

CANDIDATE_FILES=(
  # Existing ladder coverage (M1, M2 behavioral, M4b terminal content):
  "tests/unit/test_symptom_repair_ladder.py"
  # New P2 misc coverage (M2a axis vocabulary, M3 perf, M4c routing):
  "tests/unit/test_ladder_misc_p2.py"
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
echo "=== Running tests (dual-layer: 150s internal + 300s outer) ==="
timeout 300s uv run python -m pytest \
  "${EXISTING_FILES[@]}" \
  --override-ini="addopts=" --tb=short -q -rs -v \
  2>&1

EXIT_CODE=$?

echo ""
echo "=== Post-run drift check ==="
POST_BRANCH=$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD)
POST_COMMIT=$(git -C "$PROJECT_DIR" rev-parse --short HEAD)
echo "Post-run branch: $POST_BRANCH"
echo "Post-run commit: $POST_COMMIT"

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