# daemon/tools/

## Responsibility
Tool implementations for LangGraph agents. Provides filesystem operations, command execution, time information, session orchestration, agent self-modification, and agent lifecycle management.

## Design Patterns

### Factory Pattern
Tools requiring runtime context (manager, session_id, agent_dir) are created via factory functions:
- `create_session_tools()` - Creates all tools for a session with injected SessionManager
- `create_inner_soul_tool()` - Creates self-modification tool bound to specific agent
- `create_mother_tools()` - Creates agent management tools (Mother-only)

### LangChain Tool Integration
All tools use `@tool` decorator from `langchain_core.tools`:
- Docstrings become tool descriptions (visible to LLM)
- Type hints define parameter schemas
- Return values are serialized to strings for LLM consumption

### Dependency Injection
- `SessionManager` is injected into tools needing cache invalidation or session operations
- `agent_dir` binds tools to specific agent context
- `session_id` tracks session hierarchy for spawning/messaging

## Data & Control Flow

### Tool Invocation Flow
```
Agent Request → LangGraph Agent Node → Tool Function → Result String → Agent Response
```

### Tool Categories

| Category | Tools | Description |
|----------|-------|-------------|
| **Filesystem** | `list_directory`, `read_file`, `glob_files` | File/directory operations |
| **Execution** | `bash` | Shell command execution |
| **Utility** | `time` | Current time information |
| **Session** | `spawn_session`, `send_message`, `terminate_session`, `list_sessions`, `get_session_info` | Multi-agent orchestration |
| **Self-Modification** | `inner_soul` | Agent growth and learning |
| **Agent Management** | `agent_list`, `agent_create`, `agent_read`, `agent_modify`, `agent_delete` | Lifecycle management (Mother-only) |

### Return Value Patterns
- **Simple tools** (`bash`, `time`, `list_directory`): Return formatted string
- **Complex tools** (`agent_create`, `agent_read`, `spawn_session`): Return dict with `success` field + data
- **Error handling**: Return `"ERROR: <message>"` strings

### Cache Invalidation
When agents modify their files (via `inner_soul` or `agent_modify`):
```
File Write → manager.prompt_cache.invalidate(agent_path) → Next prompt rebuilds context
```

## Integration Points

### Tool Availability by Agent

| Tool | All Agents | Mother Agent | Notes |
|------|------------|--------------|-------|
| `bash` | ✓ | ✓ | Shell execution |
| `list_directory` | ✓ | ✓ | Directory listing |
| `read_file` | ✓ | ✓ | File reading |
| `glob_files` | ✓ | ✓ | Pattern matching |
| `time` | ✓ | ✓ | Time info |
| `spawn_session` | ✓ | ✓ | Create child session |
| `send_message` | ✓ | ✓ | Inter-session messaging |
| `terminate_session` | ✓ | ✓ | Kill session |
| `list_sessions` | ✓ | ✓ | List active sessions |
| `get_session_info` | ✓ | ✓ | Session details |
| `inner_soul` | ✓ | ✓ | Self-modification |
| `agent_list` | - | ✓ | List agents |
| `agent_create` | - | ✓ | Create agent |
| `agent_read` | - | ✓ | Read agent files |
| `agent_modify` | - | ✓ | Modify agent files |
| `agent_delete` | - | ✓ | Delete agent |

### SessionManager Integration
- `manager.spawn_session()` - Create new agent session
- `manager.queue.enqueue()` - Queue messages between sessions
- `manager.terminate_session()` - Kill session
- `manager.list_sessions()` - List active sessions
- `manager.get_session_info()` - Get session metadata
- `manager.prompt_cache.invalidate()` - Refresh agent context after file changes

## Key Files

- **`__init__.py`**: Module exports - re-exports all tools for easy importing
- **`bash.py`**: Shell command execution with timeout (default 120s)
- **`filesystem.py`**: Three tools for file operations (list, read, glob)
- **`time.py`**: Time information in multiple formats (ISO, human, unix)
- **`session.py`**: Session orchestration + tool aggregation factory (`create_session_tools`)
- **`inner_soul.py`**: Agent self-modification with semantic classification (600 lines - most complex)
- **`agent_mother.py`**: Agent lifecycle management tools (Mother agent only)

## Tool Interface Patterns

### Basic Tool Pattern
```python
@tool
def tool_name(param: str = "default") -> str:
    """Description visible to LLM."""
    try:
        # Implementation
        return result
    except Exception as e:
        return f"ERROR: {str(e)}"
```

### Factory Tool Pattern
```python
def create_tool(manager, agent_dir, session_id):
    @tool
    def tool(param: str) -> str:
        # Uses captured manager, agent_dir, session_id
        manager.operation()
        return result
    return tool
```

### Parameter Documentation
All tools use detailed docstrings with:
- `Args:` - Parameter descriptions
- `Returns:` - Output format
- `Examples:` - Usage examples for LLM understanding
