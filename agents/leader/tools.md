# Leader Tools

Session management tools for orchestrating work across specialized agents.

---

## `spawn_session`

Create a new agent session to handle a subtask.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `agent_dir` | string | Yes | - | Path to agent (e.g., `agents/coder`) |
| `session_id` | string | No | None | Optional custom session ID |

**Returns:** The session_id of the newly created session

**Example:**
```
spawn_session(agent_dir="agents/coder")
```

---

## `send_message` — FIRE AND FORGET

Send a task to an agent. **After sending, you are DONE.**

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `session_id` | string | Yes | - | Target session ID |
| `message` | string | Yes | - | Task description |

**Returns:** message_id (for logging only - you don't need to track it)

**Example:**
```
send_message(session_id="abc-123", message="Implement hello world")
# That's it. You're done. Move on.
```

**🔥 FIRE AND FORGET:**
- Send the message → **DONE**
- Do NOT poll, check, or wait
- Do NOT call `get_session_info` after
- The system delivers the report to you automatically
- Report appears as a new message: `"{AgentName} has done: {summary}"`

---

## `list_sessions`

See all active sessions.

**Parameters:** None

**Returns:** List of session info dictionaries

---

## `get_session_info`

Get details about a session.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `session_id` | string | Yes | - | Session ID to query |

**Returns:** Session info dictionary

**⚠️ NOTE:** Do NOT use this to "check if done". Just send tasks and let reports come to you.

---

## `terminate_session`

End a session permanently.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `session_id` | string | Yes | - | Session ID to terminate |

**Returns:** True if successful, False otherwise

**⚠️ ONLY terminate when:**
- You've received the completion report from that agent
- AND you're certain no more work is needed

**Terminating early kills queued messages and breaks the async flow.**
