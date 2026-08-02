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

# Cleanup function: kill the code-server process spawned by this daemon.
# In --reload mode, uvicorn restarts on code changes but does NOT kill the
# child code-server it spawned — each reload orphans the previous one
# (PPID becomes 1). This trap ensures stale code-server processes are
# cleaned up on script exit (normal, interrupt, or terminate).
#
# Strategy: prefer killing the specific PID from the PID file (written by
# VSCodeServerManager) to avoid killing unrelated code-server instances.
# Fall back to pkill only if the PID file is missing or the PID is stale.
CLEANUP_DONE=0
cleanup() {
    [ "$CLEANUP_DONE" -eq 1 ] && return
    CLEANUP_DONE=1
    # Try PID file first (precise — targets only this daemon's code-server)
    local pid_file="${DATA_DIR:-./data_dev}/vscode-server.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            return
        fi
    fi
    # Fallback: kill code-server processes by launch pattern
    pkill -f "code-server.*--bind-addr" 2>/dev/null || true
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
