#!/bin/bash
# Start the Ensemble Daemon in development mode with auto-reload

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Change to script directory
cd "$SCRIPT_DIR"

# Cleanup function: kill the code-server recorded for THIS boot, by exact
# PID only. In --reload mode, uvicorn restarts on code changes but does NOT
# kill the child code-server the daemon's VSCodeServerManager spawned — each
# reload can orphan the previous one (PPID becomes 1). The manager records
# the code-server it started in $DATA_DIR/vscode-server.pid (JSON:
# {"pid": N, "pgid": N, "port": N, "started_at": "..."}), and this trap
# kills THAT pid (if alive) on script exit (normal, interrupt, terminate).
#
# Scope invariant (2026-10-20 live-collateral incident): this trap must
# NEVER match processes this boot did not create. dev.sh itself never
# spawns a code-server — the daemon does, and the pid file is the only
# artifact identifying what this boot created. If the pid file is absent,
# unparseable, or its pid is stale, this trap does NOTHING: no pattern
# kill, no sweep. The previous `pkill -f "code-server.*--bind-addr"`
# fallback fired on every boot whose pid-file branch missed and SIGTERMed
# the LIVE install's code-server (pid 2021281, ~2s outage, 2026-10-20).
# Why the fallback fired so often: the old trap cat-ed the JSON pid file
# and kill -0 -ed the raw JSON string, so the precise branch never matched
# a real manager-written pid file. Live-pattern safety is absolute — no
# pkill pattern that can match a live-install process, ever.
# A bare-integer pid file (legacy format / test sentinel) is also accepted.
# Regression guard: tests/test_dev_sh_exit_trap_scope.sh
CLEANUP_DONE=0
cleanup() {
    [ "$CLEANUP_DONE" -eq 1 ] && return
    CLEANUP_DONE=1
    local pid_file="${DATA_DIR:-./data_dev}/vscode-server.pid"
    [ -f "$pid_file" ] || return 0
    local pid
    # Manager format: JSON — extract the "pid" field's integer value.
    pid=$(sed -n 's/.*"pid"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$pid_file" 2>/dev/null | head -n 1)
    if [ -z "$pid" ]; then
        # Legacy / test-sentinel format: a bare integer pid line.
        pid=$(sed -n 's/^\([0-9][0-9]*\)[[:space:]]*$/\1/p' "$pid_file" 2>/dev/null | head -n 1)
    fi
    [ -n "$pid" ] || return 0
    kill -0 "$pid" 2>/dev/null || return 0
    kill "$pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Use venv if available, otherwise use system python
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
else
    PYTHON="python3"
fi

echo -e "${GREEN}Starting Ensemble Daemon (Development Mode)...${NC}"

# Load environment variables from .env if it exists
if [ -f ".env" ]; then
    echo -e "${GREEN}Loading environment from .env...${NC}"
    # Safe .env loading that handles spaces and special characters
    set -a
    source .env
    set +a
fi

# --- Worktree env-fence guard (2026-10-05) -----------------------------------
# WHY: six env-poison incidents trace to daemon boots inside linked git
# worktrees inheriting AMBIENT environment (or sourcing no local .env at
# all) and silently targeting LIVE. ENSEMBLE_SELF_ENV resolution order is:
# explicit assignment > explicit opt-out > auto-derive (install-dir + DB
# heuristic -> live). An explicit assignment in the worktree's own .env
# pins identity and kills the ambient live-default inherit path.
# RULE: booting inside a linked worktree requires that worktree to carry a
# local .env with an explicit non-empty ENSEMBLE_SELF_ENV assignment
# (giter writes ENSEMBLE_SELF_ENV=dev into every worktree fence — see
# agents/giter "Worktree Mode"). Main checkout / non-worktree: pass
# through unconditionally (no false positives).
# NOTE: this script pins CWD to SCRIPT_DIR above, so the check inspects
# the script's own tree, not the caller's cwd. If git is absent or errors
# we warn loudly and proceed — refusing to boot the MAIN checkout on a
# git hiccup would be a false positive (fail-open for detection only; the
# fence itself remains the load-bearing guard). Resolution captures each
# path BEFORE cd-ing into it: bash `cd ""` is a successful no-op, so a
# failed rev-parse must be caught on emptiness, not on cd failure.
GIT_DIR_RES="$(git rev-parse --git-dir 2>/dev/null)" || GIT_DIR_RES=""
GIT_COMMON_RES="$(git rev-parse --git-common-dir 2>/dev/null)" || GIT_COMMON_RES=""
if [ -n "$GIT_DIR_RES" ] && [ -n "$GIT_COMMON_RES" ] \
   && GIT_DIR_ABS="$(cd "$GIT_DIR_RES" 2>/dev/null && pwd)" \
   && GIT_COMMON_ABS="$(cd "$GIT_COMMON_RES" 2>/dev/null && pwd)" \
   && [ -n "$GIT_DIR_ABS" ] && [ -n "$GIT_COMMON_ABS" ]; then
    if [ "$GIT_DIR_ABS" != "$GIT_COMMON_ABS" ]; then
        # Linked worktree: --git-dir points at <main>/.git/worktrees/<name>
        # while --git-common-dir points at the shared <main>/.git.
        if ! grep -Eq '^[[:space:]]*([[:space:]]*export[[:space:]]+)?ENSEMBLE_SELF_ENV[[:space:]]*=[[:space:]]*[^[:space:]#]+' .env 2>/dev/null; then
            echo -e "${RED}Error: linked git worktree detected without a fenced .env${NC}"
            echo -e "${RED}Refusing to boot: ambient environment would be inherited and may target LIVE.${NC}"
            echo -e "${YELLOW}Fix: create .env in this worktree root with an explicit identity pin, e.g.:${NC}"
            echo -e "${YELLOW}  ENSEMBLE_SELF_ENV=dev   (dev DB; ask giter to write the full fence)${NC}"
            exit 1
        fi
    fi
else
    echo -e "${YELLOW}Warning: could not determine worktree status (git unavailable or not a repo) — proceeding WITHOUT the worktree env-fence check.${NC}"
fi

# Check required environment variables
if [ -z "$OPENAI_API_KEY" ]; then
    echo -e "${RED}Error: OPENAI_API_KEY is not set${NC}"
    exit 1
fi

# Set defaults
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com/v1}"
export OPENAI_MODEL="${OPENAI_MODEL:-gpt-4}"

# Create dev data directory if it doesn't exist (separate from production ./data)
export DATA_DIR="${DATA_DIR:-./data_dev}"
mkdir -p "$DATA_DIR"

# Mirror to ENSEMBLE_DATA_DIR so the lifespan (api.py) loads ensemble.json
# from the dev dir. The checkpointer DB path lives in ensemble.json.
export ENSEMBLE_DATA_DIR="${ENSEMBLE_DATA_DIR:-$DATA_DIR}"

# Override persistence paths for dev mode
export PERSISTENCE_DB_PATH="$DATA_DIR/instances.db"

# Dev mode always uses port 8079 to avoid conflicting with production
export PORT=8079
export HOST="${HOST:-0.0.0.0}"
export LOG_LEVEL="${LOG_LEVEL:-info}"

echo -e "${GREEN}Starting server with auto-reload...${NC}"
echo -e "${GREEN}API Documentation: http://localhost:$PORT/docs${NC}"
if [ "$LOG_LEVEL" = "debug" ]; then
    echo -e "${YELLOW}Log level: $LOG_LEVEL (verbose)${NC}"
fi
echo ""

# --timeout-graceful-shutdown 10 ensures uvicorn forces exit after 10s even
# if shutdown hangs (e.g., on a sync DB write deadlock). Safety net for the
# sync-DB-write deadlock chain documented in the experience docs.
$PYTHON -m uvicorn daemon.api:app --host "$HOST" --port "$PORT" --reload --log-level "$LOG_LEVEL" --no-access-log --timeout-graceful-shutdown 10
