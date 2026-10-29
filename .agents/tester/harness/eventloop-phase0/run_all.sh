#!/usr/bin/env bash
# eventloop-phase0 — the re-runnable entrypoint for the
# fix/eventloop-sync-db-hotpaths lane's Phase-0 measurement harness.
#
# REPAIR 2026-10-30 (HEAD-mode repair): the runner now REQUIRES an
# explicit --mode.  The harness asserts DIFFERENT things per mode
# (base = the defect IS present; head = the defect is ABSENT), so a
# default would let a base-mode run be mistaken for a HEAD gate run.
# The two modes are mutually exclusive by construction.
#
# Usage:
#   # BASE mode — the defect pins are POSITIVE (the BASE positive control)
#   bash .agents/tester/harness/eventloop-phase0/run_all.sh \
#        --mode=base --profile=full
#
#   # HEAD mode — the defect pins are NEGATIVE; REQUIRES the paired BASE
#   # reference (G4 is a paired comparison, architect §6.4)
#   bash .agents/tester/harness/eventloop-phase0/run_all.sh \
#        --mode=head --profile=full --reference=raw/BASELINE.full.base.json
#
#   # The ONLY way to exercise HEAD mode before the developer's fix lands:
#   # transform the measured evidence into the fixed state (SIMULATION).
#   bash .agents/tester/harness/eventloop-phase0/run_all.sh \
#        --mode=head --profile=smoke --simulate=head \
#        --reference=raw/BASELINE.smoke.base.json
#
#   # The full 2x2 truth table (instrument x tree), sequentially:
#   bash .agents/tester/harness/eventloop-phase0/run_truth_table.sh --profile=smoke
#
# FENCES owned here (the python side double-checks and refuses):
#   * every POSTGRES_* / PORT / DAEMON_PORT ambient variable is unset
#     BY NAME before the python process starts — ``env -u POSTGRES_*``
#     is a SILENT NO-OP (bash does not glob env names) and is never
#     used;
#   * no live :9797 / :8079 contact — the harness picks its own free
#     port and asserts it;
#   * the scratch role+DB are created and DROPPed by the harness.
#
# Timeouts (dual-layer): the runner wraps the python process in
# ``timeout`` (full 2400 s / smoke 900 s); each phase carries its own
# internal budget.
#
# Exit codes: 0 = PASS (all N5 fences green), 1 = FAIL, 124 = TIMEOUT.

set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HARNESS_DIR/../../../.." && pwd)"
cd "$REPO_ROOT"

PROFILE=""
MODE=""
SIMULATE="none"
REFERENCE=""
CAPTURE_BODIES=""

for arg in "$@"; do
    case "$arg" in
        --profile=full)    PROFILE="full" ;;
        --profile=smoke)   PROFILE="smoke" ;;
        --mode=base)       MODE="base" ;;
        --mode=head)       MODE="head" ;;
        --simulate=none)   SIMULATE="none" ;;
        --simulate=head)   SIMULATE="head" ;;
        --capture-bodies)  CAPTURE_BODIES="--capture-bodies" ;;
        --reference=*)     REFERENCE="${arg#--reference=}" ;;
        *)
            echo "unknown arg: $arg" >&2
            echo "usage: $0 --mode={base|head} --profile={full|smoke} " >&2
            echo "          [--simulate={none|head}] [--reference=PATH] " >&2
            echo "          [--capture-bodies]" >&2
            exit 2
            ;;
    esac
done

if [ -z "$MODE" ]; then
    echo "FAIL: --mode is REQUIRED (base|head)." >&2
    echo "      'base' asserts the defect IS present (the positive control);" >&2
    echo "      'head' asserts it is ABSENT.  There is no default on purpose:" >&2
    echo "      a silent default would let a base-mode run be read as a gate run." >&2
    exit 2
fi
if [ -z "$PROFILE" ]; then
    echo "FAIL: --profile is REQUIRED (full|smoke)." >&2
    exit 2
fi
if [ "$MODE" = "head" ] && [ -z "$REFERENCE" ]; then
    echo "FAIL: --mode=head REQUIRES --reference=<BASE BASELINE JSON>." >&2
    echo "      G4 is a PAIRED BASE<->HEAD loop-lag comparison (architect §6.4);" >&2
    echo "      without the reference the non-vacuity condition cannot be asserted." >&2
    exit 2
fi
if [ -n "$REFERENCE" ] && [ ! -f "$REFERENCE" ]; then
    echo "FAIL: --reference path does not exist: $REFERENCE" >&2
    exit 2
fi

# ── the per-variable scrub fence (NEVER the glob idiom) ─────────────────
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
unset POSTGRES_URL DAEMON_PORT PORT
unset ENSEMBLE_UPGRADE_LIVE ENSEMBLE_SELF_ENV

# The interpreter: the worktree venv if present, else the main checkout's
# certified venv, else uv (off-PATH on this host — full path given).
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PY="$REPO_ROOT/.venv/bin/python"
elif [ -x "/home/nea/ensemble-src/.venv/bin/python" ]; then
    PY="/home/nea/ensemble-src/.venv/bin/python"
elif [ -x "/home/nea/.local/bin/uv" ]; then
    PY="/home/nea/.local/bin/uv run python"
else
    echo "FAIL: no certified interpreter found" >&2
    exit 1
fi

echo "=== eventloop-phase0 harness (mode=$MODE profile=$PROFILE simulate=$SIMULATE) ==="
echo "repo:      $REPO_ROOT"
echo "python:    $PY"
echo "reference: ${REFERENCE:-<none>}"
echo "started:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "$SIMULATE" = "head" ]; then
    echo "!! SIMULATED HEAD-FIX EVIDENCE — the tree was NOT fixed.  This run is"
    echo "!! NOT a measurement and NOT gate evidence."
fi

TIMEOUT_S=2400
[ "$PROFILE" = "smoke" ] && TIMEOUT_S=900

OUT_STEM="run_${PROFILE}.${MODE}"
[ "$SIMULATE" = "head" ] && OUT_STEM="${OUT_STEM}.simulated"

set +e
timeout "$TIMEOUT_S" "$PY" "$HARNESS_DIR/main.py" \
    --profile="$PROFILE" --mode="$MODE" --simulate="$SIMULATE" \
    ${REFERENCE:+--reference="$REFERENCE"} $CAPTURE_BODIES \
    > "$HARNESS_DIR/raw/${OUT_STEM}.out" 2>&1
RC=$?
set -e

echo "──────────────────────────────────────────────────────────"
tail -n 80 "$HARNESS_DIR/raw/${OUT_STEM}.out" || true
echo "──────────────────────────────────────────────────────────"
echo "exit=$RC (0=PASS, 1=FAIL, 124=TIMEOUT) — full log: raw/${OUT_STEM}.out"

exit "$RC"
