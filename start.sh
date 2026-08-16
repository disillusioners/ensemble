#!/bin/bash
# Start the Ensemble Daemon (local production test)
# For full production install, use: make install

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

# Use venv if available, otherwise use system python
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
    echo -e "${GREEN}Using virtual environment: .venv${NC}"
elif [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
    echo -e "${GREEN}Using virtual environment: venv${NC}"
else
    PYTHON="python3"
    echo -e "${YELLOW}Using system Python (no venv found)${NC}"
fi

echo -e "${GREEN}Starting Ensemble Daemon (Local Production Test)...${NC}"

# Check if frontend is built
if [ ! -f "frontend/dist/index.html" ]; then
    echo -e "${YELLOW}Warning: Frontend not built. Run 'make build' first.${NC}"
    echo -e "${YELLOW}Or use 'make install' for full production setup.${NC}"
fi

# Load environment from .env.prod (or .env as fallback)
if [ -f ".env.prod" ]; then
    echo -e "${GREEN}Loading environment from .env.prod...${NC}"
    export $(cat .env.prod | grep -v '^#' | xargs)
elif [ -f ".env" ]; then
    echo -e "${GREEN}Loading environment from .env...${NC}"
    export $(cat .env | grep -v '^#' | xargs)
fi

# Check required environment variables
if [ -z "$OPENAI_API_KEY" ]; then
    echo -e "${RED}Error: OPENAI_API_KEY is not set${NC}"
    echo "Please set it in .env.prod or .env file"
    exit 1
fi

# Set defaults
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com/v1}"
export OPENAI_MODEL="${OPENAI_MODEL:-gpt-4}"
export PORT="${PORT:-8079}"
export HOST="${HOST:-0.0.0.0}"

# Create data directory if it doesn't exist
mkdir -p data

# Kill existing process on port — OWNERSHIP-SCOPED (incident fix 2026-08-16).
# This dev script previously lsof-killed whatever held $PORT; when .env.prod
# supplies PORT=9797 that could terminate the REAL prod daemon on a
# dev+prod coexistence host. Now: only stop processes owned by THIS repo
# checkout (cwd-based ownership via scripts/stop-ensemble.sh, which also
# handles the launcher-first ordering); anything foreign on the port is
# reported with an operator hint instead of killed.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if pid=$(lsof -ti :"$PORT" 2>/dev/null); then
    echo -e "${YELLOW}Port $PORT is held by: $pid${NC}"
    echo -e "${YELLOW}Stopping only processes owned by this checkout ($REPO_DIR)...${NC}"
    if [ -x "$REPO_DIR/scripts/stop-ensemble.sh" ]; then
        bash "$REPO_DIR/scripts/stop-ensemble.sh" "$REPO_DIR" "$PORT"
    else
        echo -e "${RED}scripts/stop-ensemble.sh missing — refusing to port-kill (unsafe on coexistence hosts).${NC}"
        echo -e "${RED}Free port $PORT manually if it belongs to this checkout.${NC}"
        exit 1
    fi
fi

echo ""
echo -e "${GREEN}Configuration:${NC}"
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  Model: $OPENAI_MODEL"
echo ""
echo -e "${GREEN}Starting server...${NC}"
echo -e "${GREEN}API Docs: http://localhost:$PORT/docs${NC}"
echo -e "${GREEN}UI:       http://localhost:$PORT${NC}"
echo ""

# exec -a ensemble-prod $PYTHON -m uvicorn daemon.api:app --host "$HOST" --port "$PORT"

# exec -a ensemble-prod $PYTHON -c '
# import uvicorn
# uvicorn.run("daemon.api:app", host="$HOST", port=$PORT)
# '

exec -a ensemble-prod $PYTHON -c "
import uvicorn
import os
uvicorn.run('daemon.api:app', host=os.environ['HOST'], port=int(os.environ['PORT']), access_log=False)
"