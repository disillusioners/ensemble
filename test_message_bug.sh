#!/bin/bash
# Test script to reproduce the message duplication bug
# Usage: ./test_message_bug.sh

BASE_URL="${BASE_URL:-http://localhost:8079}"
API_URL="$BASE_URL/api"

echo "=== Testing Message Duplication Bug ==="
echo "API URL: $API_URL"
echo ""

# Check server is running
echo "0. Checking server health..."
HEALTH=$(curl -s "$API_URL/health" 2>&1)
if [ $? -ne 0 ] || [ -z "$HEALTH" ]; then
    echo "ERROR: Server is not running. Please start with ./dev.sh first"
    exit 1
fi
echo "Server is healthy: $HEALTH"
echo ""

# 1. List available agents
echo "1. Listing agents..."
AGENTS=$(curl -s "$API_URL/agents")
echo "Agents: $AGENTS"

# Find mother agent
MOTHER_AGENT=$(echo "$AGENTS" | python3 -c "import sys,json; data=json.load(sys.stdin); print(next((a['id'] for a in data.get('agents',[]) if 'mother' in a['id'].lower()), data['agents'][0]['id'] if data.get('agents') else ''))" 2>/dev/null || echo "")

if [ -z "$MOTHER_AGENT" ]; then
    echo "ERROR: No agents found"
    exit 1
fi

echo "Using agent: $MOTHER_AGENT"
echo ""

# 2. Create a new session
echo "2. Creating new session..."
SESSION_RESPONSE=$(curl -s -X POST "$API_URL/sessions" \
    -H "Content-Type: application/json" \
    -d "{\"agent_dir\": \"./agents/$MOTHER_AGENT\"}")
echo "Session response: $SESSION_RESPONSE"

SESSION_ID=$(echo "$SESSION_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")

if [ -z "$SESSION_ID" ]; then
    echo "ERROR: Failed to create session"
    exit 1
fi

echo "Session ID: $SESSION_ID"
echo ""

# 3. Send a message and track the response
echo "3. Sending first message 'hi'..."
SEND_RESPONSE=$(curl -s -X POST "$API_URL/sessions/$SESSION_ID/messages" \
    -H "Content-Type: application/json" \
    -d '{"content": "hi"}')
echo "Send response: $SEND_RESPONSE"

MESSAGE_ID=$(echo "$SEND_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message_id',''))" 2>/dev/null || echo "")
echo "Message ID: $MESSAGE_ID"
echo ""

# 4. Wait for processing
echo "4. Waiting 10 seconds for processing..."
sleep 10

# 5. Check queue status before checking messages
echo "5a. Checking queue status..."
QUEUE_STATS=$(curl -s "$API_URL/sessions/$SESSION_ID/messages/$MESSAGE_ID")
echo "Queue stats: $QUEUE_STATS"
echo ""

# 6. Check message history - count messages
echo "5b. Checking message history..."
MESSAGES=$(curl -s "$API_URL/sessions/$SESSION_ID/messages")
echo "Messages:"
echo "$MESSAGES" | python3 -m json.tool 2>/dev/null || echo "$MESSAGES"

# Count messages by role
USER_COUNT=$(echo "$MESSAGES" | python3 -c "import sys,json; msgs=json.load(sys.stdin); print(sum(1 for m in msgs if m.get('role')=='user'))" 2>/dev/null || echo "0")
ASSISTANT_COUNT=$(echo "$MESSAGES" | python3 -c "import sys,json; msgs=json.load(sys.stdin); print(sum(1 for m in msgs if m.get('role')=='assistant'))" 2>/dev/null || echo "0")

echo ""
echo "=== Results ==="
echo "User messages: $USER_COUNT"
echo "Assistant messages: $ASSISTANT_COUNT"

if [ "$USER_COUNT" -gt 1 ]; then
    echo ""
    echo "BUG DETECTED: More than 1 user message found!"
    echo "Expected: 1 user message"
    echo "Actual: $USER_COUNT user messages"
else
    echo ""
    echo "No duplication detected (only 1 user message)"
fi

# 6. Check queue for any remaining messages
echo ""
echo "6. Checking queue stats..."
QUEUE_STATS=$(curl -s "$API_URL/sessions/$SESSION_ID/messages/$MESSAGE_ID")
echo "Queue stats: $QUEUE_STATS"

# 7. Check database for enqueued messages
echo ""
echo "7. Checking queue database for this session..."
if [ -f "data/ensemble.db" ]; then
    echo "Queue table entries:"
    sqlite3 data/ensemble.db "SELECT message_id, session_id, status, source, substr(content, 1, 50) as content_preview, enqueued_at FROM message_queue WHERE session_id LIKE '${SESSION_ID:0:8}%' ORDER BY enqueued_at;" 2>/dev/null || echo "Could not query database"
else
    echo "Database file not found at data/ensemble.db"
fi

echo ""
echo "=== Test Complete ==="
