# Available Tools

This document lists all tools available to agents in the system. Each agent session has access to these tools through LangGraph.

---

## Static Tools

These tools are available in all sessions.

### `bash`

Execute a bash command and return the output.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `command` | string | Yes | - | The bash command to execute |
| `timeout` | int | No | 120 | Timeout in seconds |
| `workdir` | string | No | None | Working directory for command execution |

**Returns:** Command output including stdout, stderr, and exit code

**Example:**
```
bash(command="ls -la", workdir="/path/to/project")
```

---

### `list_directory`

List contents of a directory.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `path` | string | No | "." | Directory path to list |
| `show_hidden` | bool | No | False | Whether to show hidden files |

**Returns:** Directory listing with file type indicators:
- `/` suffix for directories
- `@` suffix for symlinks
- `*` suffix for executables

**Example:**
```
list_directory(path="src", show_hidden=True)
```

---

### `read_file`

Read contents of a file.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `path` | string | Yes | - | File path to read |
| `offset` | int | No | 1 | Line number to start from (1-indexed) |
| `limit` | int | No | 2000 | Maximum number of lines to read |

**Returns:** File contents with line numbers (format: "line_num: content")

**Example:**
```
read_file(path="src/main.py", offset=10, limit=50)
```

---

### `glob_files`

Find files matching a glob pattern.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `pattern` | string | Yes | - | Glob pattern (e.g., "**/*.py", "*.md") |
| `path` | string | No | "." | Base directory to search from |

**Returns:** List of matching file paths, sorted by modification time (newest first)

**Example:**
```
glob_files(pattern="**/*.py", path="src")
```

---

## Session Management Tools

These tools enable multi-agent orchestration and communication.

### `spawn_session`

Spawn a new agent session.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `agent_dir` | string | Yes | - | Path to agent directory (e.g., "agents/coder") |
| `session_id` | string | No | None | Optional session ID (auto-generated if omitted) |

**Returns:** The session_id of the newly created session

**Example:**
```
spawn_session(agent_dir="agents/coder")
```

---

### `send_message`

Send a message to another session and get the response.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `session_id` | string | Yes | - | ID of the target session |
| `message` | string | Yes | - | Message content to send |

**Returns:** The response content from the session

**Example:**
```
send_message(session_id="abc-123", message="Write a hello world function")
```

---

### `terminate_session`

Terminate a session. Use with caution.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `session_id` | string | Yes | - | ID of the session to terminate |

**Returns:** True if termination was successful, False otherwise

**Example:**
```
terminate_session(session_id="abc-123")
```

---

### `list_sessions`

List all active sessions.

**Parameters:** None

**Returns:** List of session info dictionaries

**Example:**
```
list_sessions()
```

---

### `get_session_info`

Get information about a specific session.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `session_id` | string | Yes | - | ID of the session to get info for |

**Returns:** Session info dictionary

**Example:**
```
get_session_info(session_id="abc-123")
```

---

## Tool Categories Summary

| Category | Tools | Purpose |
|----------|-------|---------|
| **Filesystem** | `list_directory`, `read_file`, `glob_files` | Navigate and read files |
| **System** | `bash` | Execute shell commands |
| **Session** | `spawn_session`, `send_message`, `terminate_session`, `list_sessions`, `get_session_info` | Multi-agent orchestration |

---

## Implementation Details

- **Total Tools:** 9
- **Registration:** All tools are registered via `create_session_tools()` in `daemon/tools/session.py`
- **LangGraph Integration:** Tools are bound to LLM via `llm.bind_tools(tools)` and executed via `ToolNode`
