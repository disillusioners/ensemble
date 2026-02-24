#!/bin/bash
# Start the Auto-Code Daemon

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

echo -e "${GREEN}Starting Auto-Code Daemon...${NC}"

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Warning: .env file not found. Creating from .env.example...${NC}"
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${YELLOW}Created .env file. Please edit it with your API keys.${NC}"
    fi
fi

# Load environment variables from .env if it exists
if [ -f ".env" ]; then
    echo -e "${GREEN}Loading environment from .env...${NC}"
    export $(cat .env | grep -v '^#' | xargs)
fi

# Check required environment variables
if [ -z "$OPENAI_API_KEY" ]; then
    echo -e "${RED}Error: OPENAI_API_KEY is not set${NC}"
    echo "Please set it in .env file or export it:"
    echo "  export OPENAI_API_KEY=your-api-key"
    exit 1
fi

# Set defaults
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com/v1}"
export OPENAI_MODEL="${OPENAI_MODEL:-gpt-4}"

# Create data directory if it doesn't exist
mkdir -p data

# Get port from config or use default
PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"

echo -e "${GREEN}Configuration:${NC}"
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  Model: $OPENAI_MODEL"
echo "  API URL: $OPENAI_BASE_URL"
echo ""

# Start the server
echo -e "${GREEN}Starting server...${NC}"
echo -e "${GREEN}API Documentation: http://localhost:$PORT/docs${NC}"
echo -e "${GREEN}Health Check:     http://localhost:$PORT/health${NC}"
echo -e "${GREEN}UI:                http://localhost:$PORT/ui${NC}"
echo ""

$PYTHON -m uvicorn daemon.api:app --host "$HOST" --port "$PORT"
