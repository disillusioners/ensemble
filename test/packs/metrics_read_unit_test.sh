#!/usr/bin/env bash
# Test Pack: metrics_read_unit_test
# The metrics-agent-tools Phase 5 gate — Tier 0 (the lane's own packs).
#
# The pack runs the lane's FOUR python packs + the Phase-1 seam pack:
#
#   metrics_read_registration  — the registration + wiring proof
#   metrics_read_tools         — the three tools' behaviour
#   metrics_read_personas      — the design's two persona examples
#   metrics_read_docs_surface  — the agent-visible docs chain
#   metrics_query_protocol_consumer — the Phase-1 seam pin
#
# EXIT-CODE CONTRACT (the house shape, `metrics_census_pg_test.sh:36-42`):
#   per leg  0 = ok · 1 = failure · 5 = nothing collected · 124 = timeout
#   pack     0 = PASS · 1 = FAIL · 124 = TIMEOUT
#
# GREEN-AT-ZERO IS A FAILURE: a leg whose summary does not carry
# "N passed" with N >= 1 is a VACUOUS green and FAILS the pack.
#
# FENCES: the per-variable env scrub (the `POSTGRES_*` glob form is a
# SILENT NO-OP), `-p no:randomly`, the dual-layer timeout (`timeout 300`
# outer + `--timeout=120` inner), and the WORKTREE-LOCAL `.venv/bin/python`
# (never the main checkout's interpreter).
set -uo pipefail

PACK_START=${SECONDS:-0}
PACK_BUDGET_S=900

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
LOG_DIR="${METRICS_READ_LOG_DIR:-/tmp/metrics-read-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT)

LEG_PACKS=(
  "metrics_read_registration:tests/unit/test_metrics_read_registration.py"
  "metrics_read_tools:tests/unit/metrics/test_metrics_read_tools.py"
  "metrics_read_personas:tests/unit/metrics/test_metrics_read_personas.py"
  "metrics_read_docs_surface:tests/unit/metrics/test_metrics_read_docs_surface.py"
  "metrics_query_protocol_consumer:tests/unit/metrics/test_metrics_query_protocol_consumer.py"
)

if [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi

echo "=== Test Pack: metrics_read_unit_test ==="
echo "REPO_ROOT=$REPO_ROOT"

FAILURES=0
for entry in "${LEG_PACKS[@]}"; do
  leg_name="${entry%%:*}"
  leg_path="${entry#*:}"
  log="$LOG_DIR/${leg_name}.log"
  echo ""
  echo "--- ${leg_name} ---"
  if [ ! -f "$leg_path" ]; then
    echo "${leg_name}: FAILED (the pack file is missing: ${leg_path})"
    FAILURES=$((FAILURES + 1))
    continue
  fi
  set +e
  timeout 300 "${SCRUB[@]}" "$PY" -m pytest "$leg_path" -q --tb=line -rfE \
    -p no:randomly --timeout=120 > "$log" 2>&1
  leg_rc=$?
  set +e   # the leg summary greps must never kill the pack
  echo "${leg_name}_EXIT=${leg_rc}"
  grep -E "^[0-9]+ (passed|failed)" "$log" | tail -1 | sed 's/^/  /' || true
  grep -E "^(FAILED|ERROR) " "$log" | sed 's/^/  /' || true
  # THE VACUOUS-GREEN GUARD: a leg that collected nothing is NOT a pass.
  # (The quiet pytest summary carries "N passed"; a leg with N < 1 is
  # a VACUOUS green and FAILS the pack.)
  if ! grep -qE "^[1-9][0-9]* passed" "$log"; then
    echo "${leg_name}: FAILED (VACUOUS — the summary does not carry 'N passed' with N >= 1)"
    FAILURES=$((FAILURES + 1))
    continue
  fi
  if [ "$leg_rc" -ne 0 ]; then
    echo "${leg_name}: FAILED (rc=${leg_rc})"
    FAILURES=$((FAILURES + 1))
  fi
  elapsed=$(( ${SECONDS:-0} - PACK_START ))
  if [ "$elapsed" -ge "$PACK_BUDGET_S" ]; then
    echo "RESULT: TIMEOUT (self-cap ${PACK_BUDGET_S}s breached at ${elapsed}s)"
    exit 124
  fi
done

if [ "$FAILURES" -ne 0 ]; then
  echo "RESULT: FAIL (${FAILURES} leg(s) failed)"
  exit 1
fi
echo "RESULT: PASS (${#LEG_PACKS[@]} legs)"
exit 0
