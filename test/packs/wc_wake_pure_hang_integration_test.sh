#!/usr/bin/env bash
# Test Pack: wc_wake_pure_hang_integration_test — S6 flake-resistance.
#
# Background. The pure-hang integration test
# (tests/integration/test_wc_wake_pure_hang.py) is the S6 acceptance
# surface for the wc-wake phase-1 gate — three tests, one per routing
# site (HTTP POST /messages, agent-tool send_message, job_inject). Each
# test boots a real manager + real worker pool + scripted-LLM graph +
# hung child, parks a WC parent, and asserts the wake message
# (a) flips WC→RUNNING, (b) is consumed by a real engine turn, and
# (c) a later child report delivers to the parent.
#
# S6 flake-resistance: the integration test exercises real worker
# threads, a real JobProcessor, a real dispatch bus, and real cross-
# thread DB writes. The harness includes a 5.0s WC-flip window, a
# 20.0s quiescence poll, and a 0.15s scripted-LLM sleep per turn —
# race-condition surface is real. The pre-flip batch's W1 commit
# closed module-identity flag-cache pollution so the resolver's cache
# no longer leaks across tests; that fix needs a 3× run to attest
# determinism. A single 1× green run is necessary but not sufficient.
#
# Each invocation sets ENSEMBLE_WC_WAKE_ENQUEUE=1 (the tests themselves
# use per-test monkeypatch.setenv; the ambient value is a boot-log
# safety net — kill-switch is restart-required semantics, so the
# resolver reads it once at first call). All three tests must pass on
# every one of the three runs.
#
# Cap math: the integration test runs in ~10s (measured at HEAD, see
# keep-the-cap rule below). 3 runs = ~33s with margin → comfortably
# inside the 280s internal cap. The pack self-caps at 280s internal /
# 300s command — if a single run risks exceeding the cap, the script
# times ONE run first, reports, and keeps the cap (it will FAIL with
# TIMEOUT rather than exceed).
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): SECONDS-based outer cap of 280s (fires
#     between runs; per-run `timeout 150s` is the inner guard against a
#     hung pytest). 280s = (3 × ~30s healthy) + ~190s margin for cold
#     start / GC pauses. Hard floor — must never exceed.
#
# Exit codes (per test-pack skill):
#   0   PASS  (3/3 runs green)
#   1   FAIL  (any run failed)
#   124 TIMEOUT (per-run `timeout 150s` tripped OR total-script cap)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Outer script-wide cap (Layer 2). Per-run `timeout 150s` is the inner
# guard; this SECONDS counter bails between runs if the cumulative
# wall-clock crosses 280s.
SCRIPT_CAP_S=280
SECONDS=0

echo "=== Test Pack: wc_wake_pure_hang_integration_test ==="
echo "(S6 acceptance — 3× flake resistance for tests/integration/test_wc_wake_pure_hang.py)"
echo "(flag: ENSEMBLE_WC_WAKE_ENQUEUE=1 ambient; tests set per-test via monkeypatch)"

cd "$PROJECT_DIR"

# Ambient flag for boot-log safety net. Per-test monkeypatch.setenv
# is what actually drives the routing; the ambient value is read
# only if a test forgets to call setenv (defensive).
export ENSEMBLE_WC_WAKE_ENQUEUE=1

# Three sequential runs. Capture each run's last pytest line (the
# PASS/FAIL summary) for the report.

declare -a RUN_TAILS=()
OVERALL_RC=0

for run in 1 2 3; do
  # Outer-cap check between runs: keep the cap regardless.
  if [ "$SECONDS" -ge "$SCRIPT_CAP_S" ]; then
    echo ""
    echo "── outer cap reached (${SECONDS}s >= ${SCRIPT_CAP_S}s) before run ${run}/3 ──"
    OVERALL_RC=124
    break
  fi

  echo ""
  echo "── run ${run}/3 (elapsed=${SECONDS}s / cap=${SCRIPT_CAP_S}s) ──"
  # Per-run inner guard: 150s. A healthy run is ~10s; 150s is a
  # margin-rich safety net that catches a hung pytest without
  # affecting the outer cap.
  timeout 150s .venv/bin/pytest \
    tests/integration/test_wc_wake_pure_hang.py \
    --tb=short -q -ra -p no:cacheprovider 2>&1 \
    | tee "/tmp/wc_wake_pure_hang_run${run}.log"
  rc=${PIPESTATUS[0]}
  # Capture last pytest line for the report.
  tail_line=$(tail -n 1 "/tmp/wc_wake_pure_hang_run${run}.log" || true)
  RUN_TAILS+=("run ${run}: rc=${rc} :: ${tail_line}")
  if [ "$rc" -ne 0 ]; then
    OVERALL_RC="$rc"
    echo "── run ${run} FAILED (rc=${rc}); stopping early — remaining runs would only re-confirm ──"
    break
  fi
done

echo ""
echo "── per-run tail lines ──"
for line in "${RUN_TAILS[@]}"; do
  echo "  ${line}"
done
echo ""
echo "(cumulative wall: ${SECONDS}s / cap ${SCRIPT_CAP_S}s)"

# Treat 124 (timeout) as TIMEOUT for the whole pack.
if [ "$OVERALL_RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$OVERALL_RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
