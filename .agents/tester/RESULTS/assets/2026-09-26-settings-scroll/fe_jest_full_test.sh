#!/usr/bin/env bash
# fe_jest_full_test.sh — Settings-scroll acceptance gate: FE Jest full suite
# Scope: exactly ONE full, non-watch, non-interactive Jest run in frontend/.
# Baseline expectation: 3388 pass / 4 fail — the 4 failures are pre-existing
# jobs-model enum drift, confined to jobs-filter-state.model.spec.ts and
# jobs-grouping.model.spec.ts.
# Dual-layer timeout: inner 270s watchdog (process-tree kill) + outer `timeout 300`.
set -u

LOG="/home/nea/ensemble-src/.agents/tester/RESULTS/assets/2026-09-26-settings-scroll/fe_jest_full.log"
FRONTEND="/home/nea/ensemble-src/frontend"
WATCHDOG_SECS=270

echo "=== Test Pack: fe_jest_full_test ==="
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Command: CI=true npm test -- --watch=false"

cd "$FRONTEND" || { echo "RESULT: FAIL (cannot cd into $FRONTEND)"; exit 1; }

rm -f "$LOG"

# Launch Jest in its own process group (setsid) so the watchdog can kill the tree.
setsid env CI=true npm test -- --watch=false >"$LOG" 2>&1 &
TPID=$!
sleep 1
PGID=$(ps -o pgid= -p "$TPID" 2>/dev/null | tr -d ' ')
[ -z "$PGID" ] && PGID="$TPID"

elapsed=0
while kill -0 "$TPID" 2>/dev/null && [ "$elapsed" -lt "$WATCHDOG_SECS" ]; do
  sleep 5
  elapsed=$((elapsed + 5))
done

if kill -0 "$TPID" 2>/dev/null; then
  echo "Watchdog fired at ~${elapsed}s (limit ${WATCHDOG_SECS}s) — killing process tree pgid=$PGID"
  kill -TERM -- -"$PGID" 2>/dev/null
  sleep 3
  kill -KILL -- -"$PGID" 2>/dev/null
  echo "--- last 20 lines of log ---"
  tail -20 "$LOG"
  echo "RESULT: TIMEOUT"
  exit 124
fi

wait "$TPID"; rc=$?
echo "Jest exit code: $rc"
echo "Finished: $(date -u +%Y-%m-%dT%H:%M:%SZ) (approx ${elapsed}s poll)"
echo ""

# ---- Summary extraction ----
tail -25 "$LOG"
echo ""

SUITES_LINE=$(grep -E "^Test Suites:" "$LOG" | tail -1)
TESTS_LINE=$(grep -E "^Tests:" "$LOG" | tail -1)
SNAP_LINE=$(grep -E "^Snapshots:" "$LOG" | tail -1)
TIME_LINE=$(grep -E "^Time:" "$LOG" | tail -1)
echo "SUMMARY: ${SUITES_LINE:-<none>} | ${TESTS_LINE:-<none>}"
echo "SUMMARY2: ${SNAP_LINE:-} | ${TIME_LINE:-}"

# ---- Which suites failed? ----
FAIL_SPECS=$(grep -E "^FAIL" "$LOG" | sed -E 's/^FAIL[[:space:]]+//' | sort -u)
echo "FAILED SUITES:"
if [ -z "$FAIL_SPECS" ]; then
  echo "  <none>"
  N_FAIL_SUITES=0
else
  echo "$FAIL_SPECS" | sed 's/^/  /'
  N_FAIL_SUITES=$(echo "$FAIL_SPECS" | grep -c .)
fi

# ---- PASS criteria: exactly 4 failed tests, all inside the two known suites ----
KNOWN_SPECS="jobs-filter-state.model.spec.ts
jobs-grouping.model.spec.ts"

FAILED_TESTS_N=$(echo "$TESTS_LINE" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' | head -1)
[ -z "$FAILED_TESTS_N" ] && FAILED_TESTS_N=0

OUTSIDE=0
SETTINGS_FAILS=0
if [ -n "$FAIL_SPECS" ]; then
  while IFS= read -r spec; do
    base=$(basename "$spec")
    known=0
    while IFS= read -r k; do
      [ "$base" = "$k" ] && known=1
    done <<< "$KNOWN_SPECS"
    if [ "$known" -eq 0 ]; then
      OUTSIDE=$((OUTSIDE + 1))
      case "$base" in
        settings*) SETTINGS_FAILS=$((SETTINGS_FAILS + 1)) ;;
      esac
    fi
  done <<< "$FAIL_SPECS"
fi

echo "ANALYSIS: failed_tests=$FAILED_TESTS_N failed_suites=$N_FAIL_SUITES suites_outside_known_two=$OUTSIDE settings_suite_failures=$SETTINGS_FAILS"

if [ "$FAILED_TESTS_N" -eq 4 ] && [ "$OUTSIDE" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
