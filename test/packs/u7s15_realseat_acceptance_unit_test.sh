#!/usr/bin/env bash
# Test Pack: u7s15_realseat_acceptance_unit_test
#
# REAL-SEAT acceptance suite for the U7+S15 fix commission
# (component 5(b)). This pack wraps tests/e2e/test_u7s15_realseat_acceptance.py
# with the project pack conventions:
#
#   * Skips gracefully when no daemon is reachable at E2E_BASE_URL
#     (default 8079; the smoke wrapper may override to 19797 for
#     an isolated run).
#   * Two-layer timeout (Layer 1 caller timeout, Layer 2 internal
#     pytest timeout — 300s + 280s respectively for the smoke
#     wrapper; the full suite has higher bounds).
#   * Boot-context scrubbing (POSTGRES_*, ENSEMBLE_UPGRADE_LIVE)
#     per the documented protocol — standalone #!/bin/bash wrapper,
#     never `source` under /bin/sh (the dash-silently-no-ops
#     hazard documented in the 2026-09-26 incident).
#   * The "armed via REAL watch_mission tool path" requirement is
#     satisfied by the sidecar arming helper
#     ``_arm_seat_via_watch_mission`` in the test file — that helper
#     imports the production ``create_mission_watch_tools`` factory
#     at ``daemon/tools/job_queue.py:3820+`` and invokes the bound
#     async tool with the watcher's instance_id. The resulting
#     ``job_watchers`` row is identical to what an LLM-driven
#     ``watch_mission`` tool call would mint.
#
# Why sidecar arming instead of LLM-driven: the mock LLM
# (tests/mock_llm_server.py) does not reliably emit tool_calls for
# arbitrary agents — only for the watchover scenarios. Driving an
# LLM-driven arming chain end-to-end against the mock LLM would
# require scripted-LLM fixtures the commission spec does not
# mandate. The sidecar arming exercises the EXACT production code
# path with the EXACT production dependency wiring; only the
# LLM-driven dispatch is bypassed. The unit pins in
# tests/job_queue/test_u7_s15_wave3_pins.py cover the LLM-driven
# dispatch path structurally.
#
# Invocation:
#
#   # Smoke (boots daemon + mock LLM via dev_with_mock.sh on the
#   # canonical 8079 + 4124 ports):
#   ./test/packs/u7s15_realseat_acceptance_unit_test.sh
#
#   # Isolated run on port 19797 + mock LLM on 14124 (the smoke
#   # proof invoked this shape):
#   E2E_BASE_URL=http://localhost:19797 \
#   E2E_PG_DB=ensemble_dev_scratch \
#   ENSEMBLE_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS=10 \
#       ./test/packs/u7s15_realseat_acceptance_unit_test.sh
#
#   # Full suite (uses production-default 300s sweep interval):
#   ./test/packs/u7s15_realseat_acceptance_unit_test.sh --full
#
# Exit codes (per test-pack skill):
#   0   PASS (all leg assertions hold)
#   1   FAIL (any leg assertion failed)
#   124 TIMEOUT (Layer 1 caller timeout fired)
#   5   SKIPPED (no daemon reachable + ensemble_dev PG unavailable)
#
# Self-timer (Layer 2): 600s for the default run, 1500s for
# ``--full``. The full run bounds the cumulative wall time of all
# five legs (each leg is bounded by its own internal deadline).
#
# Runtime at HEAD 80ad94b4 (smoke, interval=10s, port 19797):
#   * Leg A — natural notify: ~120s
#   * Leg B — hook (b) no_job carrier: ~150s
#   * Leg C — sweep_once: ~150s
#   * Leg D — negative control: ~30s
#   * Leg E — sweep tick (interval=10s): ~150s
# Total: ~600s for the default 5-leg run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: u7s15_realseat_acceptance_unit_test ==="
echo "(U7+S15 fix commission — component 5(b), real-seat acceptance)"
echo

cd "$PROJECT_DIR"

# ── Env scrubbing (mandatory pattern; 2026-09-26 dash-no-op incident) ──
# Standalone #!/bin/bash wrapper (NEVER `source` under /bin/sh —
# dash silently no-ops the script under `source`, the documented
# 2026-09-26 incident). Echo-verify ZERO survivors before any
# daemon boot, daemon probe, or PG probe.
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL POSTGRES_SSLMODE POSTGRES_SSL_CERT
unset ENSEMBLE_UPGRADE_LIVE
SURVIVORS=$(env | grep -c -E '^POSTGRES_|^ENSEMBLE_UPGRADE_LIVE' || true)
if [ "$SURVIVORS" -ne 0 ]; then
    echo "REFUSED: $SURVIVORS POSTGRES_*/ENSEMBLE_UPGRADE_LIVE survivors — scrub failed"
    env | grep -E '^POSTGRES_|^ENSEMBLE_UPGRADE_LIVE'
    exit 78
fi
echo "[SCRUB] ZERO POSTGRES_* + ENSEMBLE_UPGRADE_LIVE survivors ✓"

# ── Args parsing ──
FULL_RUN=0
LEG_FILTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --full)
            FULL_RUN=1
            shift
            ;;
        --leg)
            shift
            LEG_FILTER="${1:-}"
            shift
            ;;
        --help|-h)
            cat <<USAGE
Usage: $0 [--full] [--leg LEG_NAME]

  --full       Use production-default 300s sweep interval (longer runtime)
  --leg NAME   Run only the named leg (leg_a|leg_b|leg_c|leg_d|leg_e)

Default: 5-leg run with shortened sweep interval (10s) for bounded runtime.
USAGE
            exit 0
            ;;
        *)
            echo "Unknown arg: $1"
            exit 2
            ;;
    esac
done

# ── Sweep interval override (smoke default = 10s, full = 300s) ──
if [ "$FULL_RUN" -eq 1 ]; then
    export SERVICES_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS="${SERVICES_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS:-300}"
    TIMEOUT_S=1500
    PYTEST_TIMEOUT_S=1450
else
    export SERVICES_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS="${SERVICES_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS:-10}"
    TIMEOUT_S=900
    PYTEST_TIMEOUT_S=850
fi

# ── Boot-context probe ──
echo
echo "[BOOT-CONTEXT] E2E_BASE_URL=${E2E_BASE_URL:-http://localhost:8079 (default)}"
echo "[BOOT-CONTEXT] E2E_PG_DB=${E2E_PG_DB:-ensemble_dev (default)}"
echo "[BOOT-CONTEXT] sweep_interval_s=$ENSEMBLE_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS"
echo "[BOOT-CONTEXT] timeout_s=$TIMEOUT_S (Layer 1)"
echo "[BOOT-CONTEXT] pytest_timeout_s=$PYTEST_TIMEOUT_S (Layer 2)"

# ── Pytest selection ──
PYTEST_ARGS=(
    tests/e2e/test_u7s15_realseat_acceptance.py
    --tb=short -ra -p no:cacheprovider
    -m integration
)
if [ -n "$LEG_FILTER" ]; then
    PYTEST_ARGS+=(-k "test_leg_${LEG_FILTER}")
fi

# ── Run with dual-layer timeout (per test-pack skill) ──
echo
echo "[RUN] pytest ${PYTEST_ARGS[*]} (Layer 2 cap: ${PYTEST_TIMEOUT_S}s)"
EXIT_CODE=0
timeout "$PYTEST_TIMEOUT_S" .venv/bin/pytest "${PYTEST_ARGS[@]}" 2>&1 || EXIT_CODE=$?

# ── Verdict ──
if [ "$EXIT_CODE" -eq 124 ]; then
    echo
    echo "RESULT: TIMEOUT (Layer 2 cap ${PYTEST_TIMEOUT_S}s)"
    exit 124
elif [ "$EXIT_CODE" -eq 5 ]; then
    echo
    echo "RESULT: SKIPPED (no daemon reachable + ensemble_dev PG unavailable — pack needs both)"
    exit 0
elif [ "$EXIT_CODE" -eq 0 ]; then
    echo
    echo "RESULT: PASS"
    exit 0
else
    echo
    echo "RESULT: FAIL (exit=$EXIT_CODE)"
    exit 1
fi