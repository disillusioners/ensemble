# agents/

## Responsibility
The `agents/` directory contains the agent configuration system that defines how autonomous AI agents behave, what tools they have access to, and how they interact. Each agent is a self-contained directory with markdown-based configuration files that define identity, capabilities, workflows, and learning mechanisms.

## Design Patterns

### Agent Configuration Architecture
Each agent is a directory containing markdown files that define its behavior:

| File | Purpose | Mutability |
|------|---------|------------|
| `meta.json` | Agent metadata (id, name, icon, color) | Static |
| `soul.md` | Identity & personality ("Who I am") | Rate-limited |
| `tools.md` | Tool permissions | Static |
| `workflow.md` | Task processing flow | Append-only |
| `rule.md` | Behavioral constraints | Static |
| `memory.md` | Persistent knowledge (~500 words) | Append-only |
| `user.md` | User preferences | Append-only |
| `growth.md` | Self-evolution rules | Static |
| `skills/` | Specialized capability modules | Per skill |
| `memories/` | Timestamped event logs | Append-only |

### Prompt Composition (loader.py)
The daemon composes agent prompts in this order:
1. **soul.md** → Identity section
2. **rule.md** → Rules (highest priority constraints)
3. **skill.md** / **skills/** → Capabilities
4. **tools_common.md** + **tools.md** → Available tools
5. **workflow.md** → Methodology
6. **memory.md** → Knowledge

### Fire-and-Forget Communication
Agents communicate asynchronously:
- `spawn_session` → Creates child agent session
- `send_message` → Queues task → **DONE** (no polling)
- Reports arrive as new messages automatically

## Agent Types

| Agent | Role | Key Files |
|-------|------|-----------|
| **leader** | Task orchestration, delegates to specialists | `leader/soul.md`, `leader/tools.md`, `leader/workflow.md` |
| **coder** | Code generation via opencode_skill | `coder/soul.md`, `coder/skills/opencode/skill.md` |
| **_mother** | Agent factory (create/modify/delete agents) | `_mother/tools.md`, `_mother/workflow.md` |
| **_inner_soul** | Self-evolution engine, semantic classification | `_inner_soul/soul.md`, `_inner_soul/workflow.md` |
| **_baby_template** | Spawnable blank agent template | `_baby_template/soul.md`, `_baby_template/tools.md` |

### Agent Hierarchy
```
leader (👑)
  ├── spawns → coder (💻)
  ├── spawns → [custom agents]
  │
_mother (system agent)
  ├── agent_create → new agents
  ├── agent_modify → existing agents
  └── agent_delete → removes agents

_inner_soul (system agent)
  └── inner_soul tool → self-modification

_baby_template (spawnable)
  └── First conversation → becomes custom agent
```

## Tool Permissions

### Common Tools (All Agents)
From `tools_common.md`:
- `bash` - Execute shell commands
- `time` - Get current date/time
- `read_file` - Read file contents with line numbers
- `list_directory` - List directory contents
- `glob_files` - Find files by pattern
- `inner_soul` - Remember/learn/change (self-evolution)

### Leader-Specific Tools
- `spawn_session` - Create child agent sessions
- `send_message` - Fire-and-forget task delegation
- `list_sessions` - List active sessions
- `get_session_info` - Query session details
- `terminate_session` - End sessions

### Mother-Specific Tools
- `agent_list` - List all agents
- `agent_create` - Create new agents
- `agent_modify` - Modify agent files
- `agent_delete` - Delete agents (to `_trash`)
- `agent_read` - Read agent files

### Coder Tools
- Uses common tools only
- Plus `opencode_skill` for orchestrator interaction

## Data & Control Flow

### Agent Loading Flow
```
daemon/loader.py
├── load_common_tools() → tools_common.md
├── load_agent_prompts(agent_dir) → soul.md, tools.md, workflow.md, rule.md, memory.md
├── load_agent_skills(agent_dir) → skills/*/skill.md
├── compose_system_prompt() → Combined markdown
├── estimate_tokens() → Token counting
└── PromptCache → In-memory caching with mtime invalidation
```

### Session Lifecycle
```
daemon/tools/session.py
├── spawn_session() → Creates SessionManager session
├── send_message() → Enqueues to InputMessageQueue
├── Queue Watchdog → Monitors for stuck sessions
├── Session Graph → Executes with LangGraph
└── Completion Report → Auto-returns to parent
```

### Self-Evolution Flow (_inner_soul)
```
Agent calls inner_soul(intent="remember|learn|change", content="...")
    ↓
_inner_soul classifies semantically:
    - identity → soul.md (requires approval)
    - personality → soul.md + user.md
    - user_preference → user.md
    - workflow → workflow.md
    - pattern/event → memories/ (timestamped files)
    ↓
Validates against growth.md rules:
    - soul.md: 1 per 10 tasks, min 24h apart
    - workflow.md: 1 per 5 tasks
    - memory.md: max 500 words
    ↓
Executes update (append-only for memories)
```

## Integration Points

### Daemon Loading
- `daemon/loader.py`: Agent prompt loading and composition with caching
- `daemon/tools/session.py`: Session tools (spawn_session, send_message) and base tool injection
- `daemon/manager.py`: Session lifecycle and orchestration
- `daemon/tools/agent_mother.py`: Agent CRUD operations
- `daemon/tools/inner_soul.py`: Self-evolution implementation

### External Systems
- **Leader** → spawns child sessions for parallel task execution
- **Coder** → uses `opencode_skill` CLI to interact with Orchestrator
- **_mother** → has direct filesystem access for agent CRUD

### Model Routing
No explicit model routing exists - all agents use the same LLM with different system prompts. Model selection could be added at the session spawning level.

## Key Files

- `tools_common.md`: Shared tools documentation (bash, time, read_file, list_directory, glob_files, inner_soul)
- `daemon/loader.py`: Agent prompt loading and composition with caching
- `daemon/tools/session.py`: Session tools (spawn_session, send_message) and base tool injection
- `daemon/manager.py`: Session lifecycle and orchestration
- `daemon/tools/agent_mother.py`: Agent CRUD operations
- `daemon/tools/inner_soul.py`: Self-evolution implementation

## System Agent Constraints

| Agent | Constraint |
|-------|------------|
| `_mother` | Immutable - cannot modify itself, uses agent management tools |
| `_inner_soul` | Immutable - handles other agents' evolution, enforces growth.md rules |
| `_baby_template` | Spawnable template - becomes custom agent after first conversation |

System agents (prefixed with `_`) cannot be deleted or modified by other agents - only by direct filesystem access.
