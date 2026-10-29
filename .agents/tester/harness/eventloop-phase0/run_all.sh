#!/usr/bin/env bash
# eventloop-phase0 — the re-runnable entrypoint for the
# fix/eventloop-sync-db-hotpaths lane's Phase-0 baseline harness.
#
# Usage:
#   bash .agents/tester/harness/eventloop-phase0/run_all.sh --profile=full
#   bash .agents/tester/harness/eventloop-phase0/run_all.sh --profile=smoke
#
# The tester's functional gate RE-RUNS this at BASE and HEAD.  The
# harness is ADDITIVE (this tree overlays cleanly at BASE via
# ``git checkout <branch> -- .agents/tester/harness/eventloop-phase0``).
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
# Timeouts (dual-layer, the dbpool-phase0 precedent): the runner wraps
# the python process in ``timeout`` (full 2400s / smoke 600s); each
# phase carries its own internal budget.
#
# Exit codes: 0=PASS (all N5 fences green), 1=FAIL, 124=TIMEOUT.

set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HARNESS_DIR/../../../.." && pwd)"
cd "$REPO_ROOT"

PROFILE="full"
for arg in "$@"; do
    case "$arg" in
        --profile=full) PROFILE="full" ;;
        --profile=smoke) PROFILE="smoke" ;;
        --profile) : ;;
        *) echo "unknown arg: $arg (use --profile={full|smoke})" >&2; exit 2 ;;
    esac
done

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

echo "=== eventloop-phase0 harness (profile=$PROFILE) ==="
echo "repo:    $REPO_ROOT"
echo "python:  $PY"
echo "started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

TIMEOUT_S=2400
[ "$PROFILE" = "smoke" ] && TIMEOUT_S=600

set +e
timeout "$TIMEOUT_S" "$PY" "$HARNESS_DIR/main.py" --profile="$PROFILE" \
    > "$HARNESS_DIR/raw/run_$PROFILE.out" 2>&1
RC=$?
set -e

echo "──────────────────────────────────────────────────────────"
tail -n 60 "$HARNESS_DIR/raw/run_$PROFILE.out" || true
echo "──────────────────────────────────────────────────────────"
echo "exit=$RC (0=PASS, 1=FAIL, 124=TIMEOUT) — full log: raw/run_$PROFILE.out"

exit "$RC"
