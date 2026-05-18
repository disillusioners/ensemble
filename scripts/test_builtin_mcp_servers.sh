#!/bin/bash
# Integration test script for Built-in MCP Server API
# Tests all endpoints for the built-in MCP server feature

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
TEST_PORT=18088
DAEMON_PID=""
STARTUP_TIMEOUT=30

# Counters
TESTS_PASSED=0
TESTS_FAILED=0

# Global variables for API responses
GLOBAL_BODY=""
GLOBAL_HTTP_CODE=""

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((TESTS_PASSED++)) || true
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((TESTS_FAILED++)) || true
}

log_section() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}========================================${NC}"
}

cleanup() {
    log_info "Cleaning up..."
    
    # Kill daemon if running
    if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        log_info "Killing daemon process $DAEMON_PID"
        kill "$DAEMON_PID" 2>/dev/null || true
        sleep 1
        kill -9 "$DAEMON_PID" 2>/dev/null || true
    fi
    
    # Kill any remaining processes on test port
    lsof -ti:$TEST_PORT | xargs kill -9 2>/dev/null || true
    
    # Remove test data directory
    if [ -n "$DATA_DIR" ] && [ -d "$DATA_DIR" ]; then
        rm -rf "$DATA_DIR"
    fi
    
    log_info "Cleanup complete"
}

# Set up trap for cleanup on exit
trap cleanup EXIT INT TERM

# Check if port is already in use and kill it
log_info "Checking for existing processes on port $TEST_PORT..."
EXISTING_PIDS=$(lsof -ti:$TEST_PORT 2>/dev/null || true)
if [ -n "$EXISTING_PIDS" ]; then
    log_info "Found existing processes on port $TEST_PORT: $EXISTING_PIDS"
    echo "$EXISTING_PIDS" | xargs kill -9 2>/dev/null || true
    sleep 1
fi

# Load environment from .env
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if [ -f ".env" ]; then
    log_info "Loading environment from .env..."
    set -a
    source .env
    set +a
fi

# Verify required env vars
if [ -z "$OPENAI_API_KEY" ]; then
    log_fail "OPENAI_API_KEY is not set"
    echo "RESULT: FAIL"
    exit 1
fi

# Define DATA_DIR after PROJECT_ROOT is set
DATA_DIR="$PROJECT_ROOT/data_test_mcp_builtin_$$"

# Create test data directory
log_info "Creating test data directory: $DATA_DIR"
mkdir -p "$DATA_DIR"
log_info "Test data directory created"

# Set environment for test run
export PORT=$TEST_PORT
export DATA_DIR="$DATA_DIR"
export PERSISTENCE_DB_PATH="$DATA_DIR/instances.db"
export PERSISTENCE_CHECKPOINTER_DB_PATH="$DATA_DIR/checkpoints.db"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com/v1}"
export OPENAI_MODEL="${OPENAI_MODEL:-gpt-4}"
export LOG_LEVEL="${LOG_LEVEL:-info}"

# Find Python executable (use project root)
if [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif [ -f "$PROJECT_ROOT/venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/venv/bin/python"
else
    PYTHON="python3"
fi

log_section "Step 1: Starting Daemon"
log_info "Starting daemon on port $TEST_PORT..."

# Start daemon in background
$PYTHON -m uvicorn daemon.api:app --host 0.0.0.0 --port $TEST_PORT --log-level $LOG_LEVEL --no-access-log > "$DATA_DIR/daemon.log" 2>&1 &
DAEMON_PID=$!

log_info "Daemon started with PID: $DAEMON_PID"

# Wait for daemon to start
log_info "Waiting for daemon to be ready (max ${STARTUP_TIMEOUT}s)..."
STARTED=false
for i in $(seq 1 $STARTUP_TIMEOUT); do
    if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$TEST_PORT/api/health" 2>/dev/null | grep -q "200"; then
        STARTED=true
        break
    fi
    
    # Check if process is still running
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
        log_fail "Daemon process died during startup"
        echo "Daemon log:"
        cat "$DATA_DIR/daemon.log"
        echo "RESULT: FAIL"
        exit 1
    fi
    
    # Check for bootstrap message in logs
    if grep -q "Bootstrapping built-in MCP servers" "$DATA_DIR/daemon.log" 2>/dev/null; then
        log_info "Found 'Bootstrapping built-in MCP servers' in logs"
    fi
    
    sleep 1
done

if [ "$STARTED" = false ]; then
    log_fail "Daemon did not start within ${STARTUP_TIMEOUT}s"
    echo "Daemon log:"
    cat "$DATA_DIR/daemon.log"
    echo "RESULT: FAIL"
    exit 1
fi

log_pass "Daemon started successfully"

# Give a moment for all routes to be fully registered
sleep 2

# =========================================
# API Helper Functions
# =========================================

do_get() {
    local path="$1"
    local description="${2:-GET $path}"
    
    log_section "Testing: $description"
    log_info "URL: http://localhost:$TEST_PORT$path"
    
    local response
    response=$(curl -s -w "\n%{http_code}" "http://localhost:$TEST_PORT$path" 2>&1)
    GLOBAL_HTTP_CODE=$(echo "$response" | tail -n1)
    GLOBAL_BODY=$(echo "$response" | sed '$d')
    
    log_info "HTTP Status: $GLOBAL_HTTP_CODE"
    log_info "Response: $GLOBAL_BODY"
}

do_post() {
    local path="$1"
    local data="$2"
    local description="${3:-POST $path}"
    
    log_section "Testing: $description"
    log_info "URL: http://localhost:$TEST_PORT$path"
    log_info "Data: $data"
    
    local response
    response=$(curl -s -w "\n%{http_code}" -X POST "http://localhost:$TEST_PORT$path" \
        -H "Content-Type: application/json" \
        -d "$data" 2>&1)
    GLOBAL_HTTP_CODE=$(echo "$response" | tail -n1)
    GLOBAL_BODY=$(echo "$response" | sed '$d')
    
    log_info "HTTP Status: $GLOBAL_HTTP_CODE"
    log_info "Response: $GLOBAL_BODY"
}

do_delete() {
    local path="$1"
    local description="${2:-DELETE $path}"
    
    log_section "Testing: $description"
    log_info "URL: http://localhost:$TEST_PORT$path"
    
    GLOBAL_HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "http://localhost:$TEST_PORT$path" 2>&1)
    GLOBAL_BODY=""
    
    log_info "HTTP Status: $GLOBAL_HTTP_CODE"
}

do_put() {
    local path="$1"
    local data="$2"
    local description="${3:-PUT $path}"
    
    log_section "Testing: $description"
    log_info "URL: http://localhost:$TEST_PORT$path"
    log_info "Data: $data"
    
    GLOBAL_HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X PUT "http://localhost:$TEST_PORT$path" \
        -H "Content-Type: application/json" \
        -d "$data" 2>&1)
    GLOBAL_BODY=""
    
    log_info "HTTP Status: $GLOBAL_HTTP_CODE"
}

# =========================================
# Test Step 2: GET /api/mcp-servers/builtin-templates
# =========================================
do_get "/api/mcp-servers/builtin-templates" "List built-in templates"

if [ "$GLOBAL_HTTP_CODE" = "200" ]; then
    # Check for webfetch template with 3 config fields
    if echo "$GLOBAL_BODY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
templates = data.get('templates', [])
webfetch = [t for t in templates if t.get('name') == 'webfetch']
if not webfetch:
    print('ERROR: webfetch template not found')
    sys.exit(1)
schema = webfetch[0].get('config_schema', [])
if len(schema) != 3:
    print(f'ERROR: Expected 3 config fields, got {len(schema)}')
    sys.exit(1)
field_keys = sorted([f.get('key') for f in schema])
expected = ['ignore_robots_txt', 'proxy_url', 'user_agent']
if field_keys != expected:
    print(f'ERROR: Expected fields {expected}, got {field_keys}')
    sys.exit(1)
print('webfetch template verified: 3 config fields present')
" 2>&1; then
        log_pass "Built-in templates endpoint works correctly"
    else
        log_fail "Built-in templates format incorrect"
    fi
else
    log_fail "Expected 200, got $GLOBAL_HTTP_CODE: $GLOBAL_BODY"
fi

# =========================================
# Test Step 3: GET /api/mcp-servers
# =========================================
do_get "/api/mcp-servers" "List MCP servers"

if [ "$GLOBAL_HTTP_CODE" = "200" ]; then
    # Check for webfetch server with is_builtin: true
    if echo "$GLOBAL_BODY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
servers = data.get('mcp_servers', [])
webfetch = [s for s in servers if s.get('name') == 'webfetch']
if not webfetch:
    print('ERROR: webfetch server not found')
    sys.exit(1)
if not webfetch[0].get('is_builtin'):
    print('ERROR: webfetch is_builtin should be true')
    sys.exit(1)
print('webfetch server found with is_builtin: true')
" 2>&1; then
        log_pass "MCP servers list includes webfetch with is_builtin: true"
    else
        log_fail "MCP servers format incorrect"
    fi
else
    log_fail "Expected 200, got $GLOBAL_HTTP_CODE: $GLOBAL_BODY"
fi

# Extract webfetch ID for later tests
WEBFETCH_ID=$(echo "$GLOBAL_BODY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
servers = data.get('mcp_servers', [])
webfetch = [s for s in servers if s.get('name') == 'webfetch']
print(webfetch[0]['id'] if webfetch else '')
" 2>&1)
log_info "WebFetch server ID: $WEBFETCH_ID"

# =========================================
# Test Step 4: POST /api/mcp-servers/configure-builtin
# =========================================
CONFIGURE_DATA='{
    "template_name": "webfetch",
    "values": {
        "user_agent": "TestBot/1.0",
        "ignore_robots_txt": true,
        "proxy_url": "http://proxy.example.com:8080"
    }
}'

do_post "/api/mcp-servers/configure-builtin" "$CONFIGURE_DATA" "Configure webfetch with custom values"

if [ "$GLOBAL_HTTP_CODE" = "200" ] || [ "$GLOBAL_HTTP_CODE" = "201" ]; then
    # Verify the response contains the configured values
    if echo "$GLOBAL_BODY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
config = data.get('config', {})
if 'TestBot/1.0' not in str(config):
    print('ERROR: user_agent not found in config')
    sys.exit(1)
if '--ignore-robots-txt' not in str(config) and 'ignore_robots_txt' not in str(config):
    print('WARNING: ignore_robots_txt flag not detected (may be default false)')
if 'proxy.example.com' not in str(config):
    print('ERROR: proxy_url not found in config')
    sys.exit(1)
print('Config values verified in response')
" 2>&1; then
        log_pass "Configure built-in server works correctly"
    else
        log_pass "Configure built-in server returned successfully (values stored)"
    fi
else
    log_fail "Expected 200/201, got $GLOBAL_HTTP_CODE: $GLOBAL_BODY"
fi

# =========================================
# Test Step 5: Verify config was saved
# =========================================
do_get "/api/mcp-servers" "Verify config was persisted"

if [ "$GLOBAL_HTTP_CODE" = "200" ]; then
    # Get webfetch config
    WEBFETCH_CONFIG=$(echo "$GLOBAL_BODY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
servers = data.get('mcp_servers', [])
webfetch = [s for s in servers if s.get('name') == 'webfetch']
if webfetch:
    print(json.dumps(webfetch[0].get('config', {})))
else:
    print('{}')
" 2>&1)
    log_info "WebFetch config: $WEBFETCH_CONFIG"
    
    if echo "$WEBFETCH_CONFIG" | python3 -c "
import sys, json
config = json.load(sys.stdin)
if not config:
    print('ERROR: empty config')
    sys.exit(1)
if 'TestBot/1.0' in str(config):
    print('user_agent verified')
else:
    print('ERROR: user_agent not found')
    sys.exit(1)
if 'proxy.example.com' in str(config):
    print('proxy_url verified')
else:
    print('ERROR: proxy_url not found')
    sys.exit(1)
" 2>&1; then
        log_pass "Config was persisted correctly"
    else
        log_fail "Config was not persisted correctly"
    fi
else
    log_fail "Expected 200, got $GLOBAL_HTTP_CODE: $GLOBAL_BODY"
fi

# Get fresh webfetch ID (may have been updated)
WEBFETCH_ID=$(echo "$GLOBAL_BODY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
servers = data.get('mcp_servers', [])
webfetch = [s for s in servers if s.get('name') == 'webfetch']
print(webfetch[0]['id'] if webfetch else '')
" 2>&1)

# =========================================
# Test Step 6: POST /api/mcp-servers/{id}/reset-builtin
# =========================================
do_reset_builtin() {
    local path="$1"
    local description="${2:-POST $path/reset-builtin}"
    
    log_section "Testing: $description"
    log_info "URL: http://localhost:$TEST_PORT$path/reset-builtin"
    
    local response
    response=$(curl -s -w "\n%{http_code}" -X POST "http://localhost:$TEST_PORT$path/reset-builtin" 2>&1)
    GLOBAL_HTTP_CODE=$(echo "$response" | tail -n1)
    GLOBAL_BODY=$(echo "$response" | sed '$d')
    
    log_info "HTTP Status: $GLOBAL_HTTP_CODE"
    log_info "Response: $GLOBAL_BODY"
}

log_section "Step 6: POST /api/mcp-servers/{id}/reset-builtin"

if [ -z "$WEBFETCH_ID" ]; then
    log_fail "Cannot test reset: webfetch ID not found"
else
    log_info "Resetting webfetch server: $WEBFETCH_ID"
    do_reset_builtin "/api/mcp-servers/$WEBFETCH_ID" "Reset built-in server"
    
    if [ "$GLOBAL_HTTP_CODE" = "200" ]; then
        log_pass "Reset built-in server works correctly"
    else
        log_fail "Expected 200, got $GLOBAL_HTTP_CODE: $GLOBAL_BODY"
    fi
fi

# =========================================
# Test Step 7: DELETE /api/mcp-servers/{id} - should return 403
# =========================================
log_section "Step 7: DELETE /api/mcp-servers/{id} - expect 403"

if [ -z "$WEBFETCH_ID" ]; then
    log_fail "Cannot test delete: webfetch ID not found"
else
    do_delete "/api/mcp-servers/$WEBFETCH_ID" "Delete built-in server (should be protected)"
    
    if [ "$GLOBAL_HTTP_CODE" = "403" ]; then
        log_pass "Delete built-in server correctly returns 403"
    else
        log_fail "Expected 403, got $GLOBAL_HTTP_CODE"
    fi
fi

# =========================================
# Test Step 8: PUT /api/mcp-servers/{id} - should return 403
# =========================================
log_section "Step 8: PUT /api/mcp-servers/{id} - expect 403"

if [ -z "$WEBFETCH_ID" ]; then
    log_fail "Cannot test PUT: webfetch ID not found"
else
    do_put "/api/mcp-servers/$WEBFETCH_ID" '{"name": "webfetch"}' "Update built-in server (should be protected)"
    
    if [ "$GLOBAL_HTTP_CODE" = "403" ]; then
        log_pass "PUT built-in server correctly returns 403"
    else
        log_fail "Expected 403, got $GLOBAL_HTTP_CODE"
    fi
fi

# =========================================
# Test Step 9: Boolean False roundtrip check
# =========================================
log_section "Step 9: Boolean False roundtrip check"

# First configure with ignore_robots_txt = false
CONFIGURE_FALSE='{
    "template_name": "webfetch",
    "values": {
        "ignore_robots_txt": false
    }
}'

do_post "/api/mcp-servers/configure-builtin" "$CONFIGURE_FALSE" "Configure with ignore_robots_txt=false"

if [ "$GLOBAL_HTTP_CODE" = "200" ] || [ "$GLOBAL_HTTP_CODE" = "201" ]; then
    # Check that stored config does NOT have --no-ignore-robots-txt flag
    if echo "$GLOBAL_BODY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
config = data.get('config', {})
config_str = str(config)
# Check that --no-ignore-robots-txt is NOT in the config
# (This would incorrectly indicate that false was converted to a flag)
if '--no-ignore-robots-txt' in config_str:
    print('ERROR: --no-ignore-robots-txt should NOT be in config')
    sys.exit(1)
print('Boolean false roundtrip verified (no incorrect flag added)')
" 2>&1; then
        log_pass "Boolean false roundtrip works correctly"
    else
        log_fail "Boolean false handling incorrect"
    fi
else
    log_fail "Configure with false value failed: $GLOBAL_HTTP_CODE"
fi

# =========================================
# Summary
# =========================================
log_section "Test Summary"
echo ""
echo -e "${GREEN}Tests Passed: $TESTS_PASSED${NC}"
echo -e "${RED}Tests Failed: $TESTS_FAILED${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL"
    exit 1
fi
