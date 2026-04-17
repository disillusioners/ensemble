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

# Override persistence paths for dev mode
export PERSISTENCE_DB_PATH="$DATA_DIR/instances.db"
export PERSISTENCE_CHECKPOINTER_DB_PATH="$DATA_DIR/checkpoints.db"

# Dev mode always uses port 8079 to avoid conflicting with production on 8088
export PORT=8079
export HOST="${HOST:-0.0.0.0}"
export LOG_LEVEL="${LOG_LEVEL:-info}"

echo -e "${GREEN}Starting server with auto-reload...${NC}"
echo -e "${GREEN}API Documentation: http://localhost:$PORT/docs${NC}"
if [ "$LOG_LEVEL" = "debug" ]; then
    echo -e "${YELLOW}Log level: $LOG_LEVEL (verbose)${NC}"
fi
echo ""

$PYTHON -m uvicorn daemon.api:app --host "$HOST" --port "$PORT" --reload --log-level "$LOG_LEVEL" --no-access-log
