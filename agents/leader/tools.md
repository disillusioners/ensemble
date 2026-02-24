# Leader Tools

You have access to these tools for orchestrating work across specialized agents.

---

## `inner_soul`

Remember, learn, or change yourself. Just say what you want — I handle the rest.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `intent` | string | Yes | What you want: `remember`, `learn`, `change` |
| `content` | string | Yes | What to remember/learn/change |

**What happens:**
| Intent | Action |
|--------|--------|
| `remember` | Stores as timestamped file in `memories/` |
| `learn` | Stores in `memories/` + checks if pattern should evolve workflow |
| `change` | Proposes change to `workflow.md` or `soul.md` (may need approval) |

**Examples:**
```
inner_soul(intent="remember", content="Coder works best with specific requirements")
inner_soul(intent="learn", content="Parallel spawning works well for independent tasks")
inner_soul(intent="change", content="Add 'confirm understanding' step before delegating")
```

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
# Returns: "abc-123-def"
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
send_message(session_id="abc-123", message="Implement a hello world function in Python")
```

**Tip:** Be specific in your instructions. Include context, requirements, and expected output format.

---

## `list_sessions`

See all active sessions you've created.

**Parameters:** None

**Returns:** List of session info dictionaries with status, agent type, and creation time

**Example:**
```
sessions = list_sessions()
# Check which agents are busy vs idle
```

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

---

## Orchestration Patterns

### Pattern 1: Sequential Tasks
```
1. spawn_session("agents/coder") → coder_1
2. send_message(coder_1, "implement feature A")
3. wait for response
4. send_message(coder_1, "now add tests for feature A")
5. terminate_session(coder_1)
```

### Pattern 2: Parallel Tasks
```
1. spawn_session("agents/coder") → coder_1
2. spawn_session("agents/coder") → coder_2
3. send_message(coder_1, "implement API endpoints")
4. send_message(coder_2, "implement database layer")
5. collect both responses
6. terminate both sessions
```

### Pattern 3: Agent Reuse
```
1. spawn_session("agents/coder") → coder_1
2. send_message(coder_1, "task 1") → response
3. send_message(coder_1, "task 2") → response  # Reuse!
4. send_message(coder_1, "task 3") → response  # Reuse!
5. terminate_session(coder_1)
```
