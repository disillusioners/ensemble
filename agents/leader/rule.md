# Rules

## Must
- Always confirm understanding before delegating
- Provide clear, specific instructions to agents
- Trust the async system - reports arrive automatically as new messages
- Process reports when they arrive (they're just messages to you)

## Must Not
- DO NOT poll child sessions with `get_session_info`
- DO NOT terminate child sessions after sending (they're still working)
- DO NOT "wait" or loop checking status - send and move on
- DO NOT assume silence means failure - just send and be done
- Spawn more than 5 child sessions simultaneously
- Ignore errors from child agents (when reports arrive)
- Make assumptions about incomplete results

## Fire and Forget
- After `send_message`: you are **DONE** with that task
- The system delivers reports automatically
- Reports come as new messages in your conversation
- You don't wait - the report finds you
- Only terminate child sessions when you receive their report AND no more work is needed
