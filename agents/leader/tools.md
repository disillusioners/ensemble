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

**Best Practice:** Reuse agents when possible. If a coder already exists and is idle, send it a new task rather than spawning another.

---

## `send_message`

Send a task to an agent and receive its response.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `session_id` | string | Yes | - | Target session ID |
| `message` | string | Yes | - | Task description |

**Returns:** The agent's response

**Example:**
```
send_message(session_id="abc-123", message="Implement a hello world function")
```

---

## `list_sessions`

See all active sessions you've created.

**Parameters:** None

**Returns:** List of session info dictionaries with status, agent type, and creation time

---

## `get_session_info`

Get details about a specific session.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `session_id` | string | Yes | - | Session ID to query |

**Returns:** Session info including status, message count, and metadata

---

## `terminate_session`

End a session when the agent is no longer needed.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `session_id` | string | Yes | - | Session ID to terminate |

**Returns:** True if successful, False otherwise

**Warning:** Only terminate sessions when truly done. Terminated sessions cannot be resumed.
