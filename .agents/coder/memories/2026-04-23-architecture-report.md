# Agents-Ensemble Architecture Report (2026-04-23)

## Project Overview

**agents-ensemble** is a persistent multi-agent daemon built with LangGraph. Agents are defined by markdown files, communicate via HTTP API, use OpenAI-compatible LLMs, have a session hierarchy for spawning/communication, and use SQLite checkpoints for crash recovery.

**Main source:** `daemon/` directory  
**Agent definitions:** `agents/<agent_id>/` directory  
**Databases:** `data/ensemble.db` (main), `data/checkpoints.db` (LangGraph state)

---

## 1. TOOL SYSTEM

### 1.1 Tool Definition Pattern

Tools use **LangChain's `@tool` decorator** with a two-level doc system:

```python
# daemon/tools/bash.py
@register_tool_category("bash")
@tool
async def bash(command: str | List[str], timeout: int | None = 1800, workdir: str | None = None, input: str | None = None) -> str:
    """Execute a bash command and return the output. Use tool_help("bash") for details."""
    # ... implementation

# Full doc stored as attribute (shown via tool_help())
bash._full_doc_ = """Execute a bash command and return the output.
[detailed documentation...]
"""
```

- **Short doc**: In `@tool` decorator → shown in LLM context
- **Full doc**: `_full_doc_` attribute → shown via `tool_help("tool_name")`

### 1.2 Tool Registry (`daemon/tools/_tool_registry.py`)

**Global registries:**
- `_full_docs: dict[str, str]` — tool_name → full documentation
- `_tool_metadata: dict[str, dict[str, Any]]` — tool_name → {category, short_doc, full_doc}

**Decorators:**
- `@register_tool_category(category)` — Tags tool with a category string
- `@register_tool(tool_name, category, short_doc, full_doc)` — Registers metadata directly
- `scan_tools_for_full_docs(tools)` — Scans tool functions for `_full_doc_` attributes

**CATEGORY_MODULES mapping:**
```python
CATEGORY_MODULES = {
    "bash": "daemon.tools.bash",
    "filesystem": "daemon.tools.filesystem",
    "time": "daemon.tools.time",
    "instance": "daemon.tools.instance",
    "self": ["daemon.tools.inner_soul", "daemon.tools.access_memory"],
    "project": "daemon.tools.project",
    "job": "daemon.tools.job_queue",
    "help": "daemon.tools.help",
    "mother": "daemon.tools.agent_mother",
}
```

### 1.3 All 9 Tool Categories

| Category Key | CATEGORY_NAME | Module(s) | Tools |
|-------------|---------------|-----------|-------|
| `bash` | Shell | `bash.py` | `bash` |
| `filesystem` | File Operations | `filesystem.py` | `list_directory`, `read_file`, `glob_files`, `write_file`, `grep_files`, `edit_file` |
| `time` | Time | `time.py` | `time` |
| `instance` | Instance Management | `instance.py` | `spawn_instance`, `send_message`, `terminate_instance`, `list_instances`, `get_instance_info` |
| `self` | Self-Modification | `inner_soul.py`, `access_memory.py` | `inner_soul`, `access_memory` |
| `project` | Project Management | `project.py` | 21 tools: `project_create`, `project_get`, `project_list`, `project_search`, `project_update`, `project_set_status`, `project_add_tag`, etc. |
| `job` | Job Queue | `job_queue.py` | 12 tools: `job_create`, `job_get`, `job_list`, `job_cancel`, `job_retry`, `job_delete`, `job_restore`, `queue_list`, `queue_create`, `queue_update`, `dlq_list`, `dlq_replay` |
| `help` | Help | `help.py` | `tool_help` |
| `mother` | Agent Management | `agent_mother.py` | `agent_list`, `agent_create`, `agent_read`, `agent_modify`, `agent_delete` |

### 1.4 Tool Filtering Per Agent

Agents configure tool access via `meta.json`:

```json
{
  "id": "coder",
  "name": "Coder",
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help"]
  }
}
```

**ToolFilter Model** (`daemon/registry.py`):
```python
class ToolFilter(BaseModel):
    allow: list[str] | None = None   # Allowed categories or individual tool names
    deny: list[str] | None = None    # Denied (wins conflicts)
```

**Filter Resolution** (`daemon/tools/instance.py:resolve_tool_filter`):
- Both empty → None (all tools allowed)
- allow expands categories to individual tool names
- deny removes tools (deny wins conflicts)
- Returns set of allowed tool names

### 1.5 Tool Factory (`daemon/tools/instance.py:create_instance_tools`)

Factory function that assembles all tools for an instance:
1. Creates instance-specific tools (spawn_instance, send_message, etc.)
2. Creates self-modification tools (inner_soul, access_memory)
3. Creates project tools
4. Creates workdir-aware wrappers for filesystem tools
5. Adds job tools (if service available)
6. Adds mother tools (if `_mother` agent only)
7. Adds help tool (last — needs reference to all other tools)
8. Scans metadata via `scan_tools_for_full_docs()`
9. Applies tool filtering via `_apply_tool_filter()`

### 1.6 Tool Documentation Generation (`daemon/loader.py`)

```python
def load_tools_doc_for_agent(agent_id: str) -> str:
    # Gets agent's ToolFilter from registry
    # Resolves allow/deny to tool names
    # Groups tools by category
    # Builds formatted documentation sections
```

### 1.7 LLM Binding (`daemon/graph.py`)

Tools are bound to the LLM via LangChain's `bind_tools()`:
```python
llm_with_tools = ThinkingChatOpenAI(**config).bind_tools(tools)
```

---

## 2. JOB QUEUE SYSTEM

### 2.1 Job Models (`daemon/repositories/job_queue/models.py`)

**JobStatus enum** — 7 states:
```
PENDING → PROCESSING → COMPLETED
                      → FAILED → (retry) → PENDING
                      → TERMINATED
                      → CANCELLED
                      → DEAD_LETTER (after max retries)
```

**JobQueue table** (`job_queues`):
- `queue_id` (PK, UUID), `project_id`, `queue_name`, `queue_name_lower`
- `queue_type` (FIFO | PARALLEL), `concurrency_limit` (1-20)
- `is_system`, `is_paused`, `description`, `default_max_retries`
- UniqueConstraint: (project_id, queue_name_lower)

**JobItem table** (`job_queue_items`):
- `job_id` (PK, UUID), `agent_id`, `agent_dir`, `message`, `source`
- `project_id`, `queue_id` (FK), `priority` (1-10, default=5), `status`
- `instance_id`, `error_message`, `result_summary`
- `job_metadata` (JSON), `idempotency_key`
- `retry_count`, `max_retries`, `failed_at`, `next_retry_at`
- Timestamps: `created_at`, `started_at`, `completed_at`, `cancelled_at`, `deleted_at`

**JobLock table** (`job_locks`): Per-queue concurrency control
- `lock_id` (PK), `project_id`, `queue_id`, `job_id`, `instance_id`, `acquired_at`

**DeadLetterItem table** (`dead_letter_items`):
- `dlq_id` (PK), `job_id`, `agent_id`, `agent_dir`, `message`, `source`
- `project_id`, `queue_id`, `priority`, `error_message`
- `retry_count`, `failed_at`, `moved_to_dlq_at`, `reason`, `metadata_json`

### 2.2 Job Queue Tools (12 tools in `daemon/tools/job_queue.py`)

All decorated with `@register_tool_category("job")`:

| Tool | Signature | Purpose |
|------|-----------|---------|
| `job_create` | `(agent_id, message, project_id?, priority?, queue_id?, idempotency_key?, metadata?, source?)` | Enqueue a job |
| `job_get` | `(job_id)` | Get job details |
| `job_list` | `(project_id?, statuses?, queue_id?, offset?, limit?, include_deleted?)` | List jobs with filters |
| `job_cancel` | `(job_id)` | Cancel a pending job |
| `job_retry` | `(job_id)` | Retry a failed job |
| `job_delete` | `(job_id)` | Soft-delete a job |
| `job_restore` | `(job_id)` | Restore a deleted job |
| `queue_list` | `(project_id)` | List queues for a project |
| `queue_create` | `(project_id, queue_name, queue_type?, concurrency_limit?, description?)` | Create a queue |
| `queue_update` | `(queue_id, project_id, queue_name?, concurrency_limit?, is_paused?)` | Update queue settings |
| `dlq_list` | `(project_id, queue_id?, limit?)` | List dead-letter items |
| `dlq_replay` | `(dlq_id)` | Replay a dead-letter item |

### 2.3 Job Queue Service (`daemon/services/job_queue_service.py`)

**Class: `JobQueueService`**

Key methods:
- `enqueue(agent_id, message, source, project_id, priority, metadata, queue_id, idempotency_key)` → `JobItem`
- `get_job(job_id)` → `JobItem | None`
- `cancel_job(job_id)` → `bool`
- `retry_job(job_id)` → `JobItem | None`
- `start_job(job_id)` → `JobItem | None` (acquires lock BEFORE state transition)
- `complete_job(job_id, demand_state, error, result_summary)` → `JobItem | None`
- `trigger_next_job(project_id, queue_id)` → `JobItem | None`
- `get_next_pending_job()` → `JobItem | None`

**Key architectural patterns:**
1. **Lock-First Pattern**: `start_job()` acquires the lock BEFORE transitioning job to PROCESSING (prevents race conditions)
2. **Atomic Transitions**: All state changes use `atomic_transition()` validating against state machine
3. **Zero-Delay Handoff**: `JobFeedbackObserver` triggers next job immediately after completion (no polling delay)

### 2.4 Job Execution (`daemon/services/job_processor.py`)

- **Background polling worker** that picks up PENDING jobs
- Checks for orphaned PROCESSING jobs on startup (crash recovery)
- Respects concurrency limits per queue via `JobLockManager`

### 2.5 Job Completion Observer (`daemon/services/job_feedback_observer.py`)

- **PRIMARY completion mechanism** — event-driven, not polling
- Observes LangGraph instance completions
- Calls `complete_job()` → triggers next job immediately

### 2.6 Retry System

- `RetryScheduler` — Polls every 60s for failed jobs ready for retry
- `JobRetryEngine` — Exponential backoff: base=60s, max=3600s, multiplier=2.0

### 2.7 Pause Control (Two-Level)

1. **Project-level** (`job_queue_paused` on Project model) — master override
2. **Queue-level** (`is_paused` on JobQueue model) — individual queue control

---

## 3. AGENT SYSTEM

### 3.1 Agent Definition Files

Located in `agents/<agent_id>/`:

| File | Required | Purpose |
|------|----------|---------|
| `meta.json` | ✅ | Agent metadata: id, name, description, icon, color, tool permissions |
| `soul.md` | ✅ | Identity, personality, core behavior |
| `rule.md` | ✅ | Hard constraints (highest priority) |
| `skill.md` | Optional | Single capability definition |
| `skills/<name>/skill.md` | Optional | Multiple named skills |
| `workflow.md` | Optional | Methodology/process/workflow |
| `memory.md` | Optional | Long-term knowledge |
| `tools_note.md` | Optional | Tool-specific usage notes |

### 3.2 meta.json Structure

```json
{
  "id": "coder",
  "name": "Coder",
  "description": "...",
  "icon": "💻",
  "color": "accent-cyan",
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help"],
    "deny": []
  }
}
```

### 3.3 Agent Loader (`daemon/loader.py`)

**Key functions:**
- `load_agent_prompts(agent_dir)` → `dict[str, str]` — Loads soul.md, rule.md, etc.
- `load_agent_skills(agent_dir)` → `dict[str, str]` — Loads skill.md files
- `compose_system_prompt(prompts, skills, dynamicTools, ...)` → `str` — Assembles final prompt
- `load_and_cache_prompt(agent_id, agent_dir, cache)` → `tuple[str, int]` — Cached loading

**Prompt composition order:**
1. `soul.md` → Identity
2. `rule.md` → Constraints
3. `skill.md` → Single capability
4. `skills/*/skill.md` → All skills
5. Dynamic tools documentation (generated from registry)
6. `tools_note.md` → Tool notes
7. `workflow.md` → Methodology
8. `memory.md` → Knowledge
9. Recent memory filenames
10. `project-experience.md` → Shared knowledge

### 3.4 Agent Registry (`daemon/registry.py`)

```python
class AgentMetadata(BaseModel):
    id: str
    name: str
    path: Path
    tools: ToolFilter | None = None

class AgentRegistry:
    def discover(self) -> None        # Scans agents/ directory
    def get(self, agent_id) -> AgentMetadata | None
    def list_all(self) -> list[AgentMetadata]
```

---

## 4. SESSION/INSTANCE SYSTEM

### 4.1 Instance Model (`daemon/models/instance.py`)

**InstanceStatus enum:**
```
idle → running → waiting (waiting_children) → completed
             → error
             → terminated
```

**Instance table** (`daemon/repositories/instance/models.py`):
- `instance_id` (PK), `agent_id`, `agent_dir`
- `parent_id` (FK to parent instance — establishes hierarchy)
- `status`, `children` (denormalized JSON list), `waiting_for` (pending child count)
- `InstanceHierarchy` junction table for parent-child relationships

### 4.2 Spawning Flow (`daemon/services/instance_lifecycle.py`)

```
spawn_instance(agent_id, parent_id, project_id):
  1. Resolve agent via registry
  2. Generate UUID for instance_id
  3. Load & cache system prompt
  4. Create tools via create_instance_tools()
  5. Build LLM config
  6. Build graph config
  7. Compile LangGraph
  8. Persist to DB
  9. Store in memory dict
```

### 4.3 Message Passing (`daemon/services/instance_messaging.py`)

```python
async def enqueue_message(instance_id, message, source="api", priority=1):
    # Creates BOTH MessageQueue + Task atomically
    # Source types: "api", "internal_agent:*", "internal_report:*"
```

- `send_message` tool → calls `enqueue_message()` with `source=f"internal_agent:{current_instance_id}"`
- Message types: AGENT, HUMAN, COMPLETION_REPORT

### 4.4 Child Completion Flow (`daemon/services/child_reports.py`)

```
1. Child instance completes → creates COMPLETION_REPORT
2. Parent receives report as special message
3. Parent's waiting_for decremented
4. When waiting_for=0 → parent marked COMPLETED
```

### 4.5 LangGraph Structure (`daemon/graph.py`)

```python
class SessionState(MessagesState):
    compacted_at: str | None = None

# Graph: START → agent → [tools | agent | nudge | END]
# Routing:
#   "tools"  — LLM made tool_calls
#   "agent"  — Ghost promise (response ends with ':' but no tool_call)
#   "nudge"  — Empty response after tools
#   END      — Done
```

### 4.6 State Persistence

- **Main DB** (`data/ensemble.db`): Instance, MessageQueue, Task, JobQueue, JobItem, etc.
- **Checkpoint DB** (`data/checkpoints.db`): LangGraph state via `AsyncSqliteSaver`
- Thread ID = instance_id for state recovery

---

## 5. EVENT SYSTEM

### 5.1 Three Event Buses

| Bus | File | Purpose |
|-----|------|---------|
| `EventBus` | `daemon/services/event_bus.py` | DB persistence + SSE streaming (checkpoint/error events) |
| `LiveEventHub` | `daemon/services/live_event_hub.py` | Live-only SSE (fire-and-forget, no buffering) |
| `DispatchEventBus` | `daemon/services/dispatch_event_bus.py` | Job dispatch wakeup signaling |

### 5.2 SSE Endpoints

- `GET /instances/{id}/events` — Instance checkpoint streaming (`daemon/routers/messages.py`)
- `GET /jobs/{id}/events` — Job status polling (`daemon/routers/jobs_streaming.py`)

### 5.3 Event Flow

```
LangGraph execution → EventBus/LiveEventHub → JobFeedbackObserver → Job COMPLETED → trigger_next_job
```

### 5.4 Agent Watching Limitation

**There are NO agent tools to subscribe to events.** Agents cannot directly "watch" jobs. The event system is purely internal infrastructure (HTTP SSE endpoints). An agent would need to:
- Poll via `job_get()` tool
- Use the HTTP SSE endpoint externally (not available as a tool)
- Be notified via `send_message()` from another agent that's monitoring

---

## 6. KEY FILE INDEX

| Component | File Path |
|-----------|-----------|
| Tool Registry | `daemon/tools/_tool_registry.py` |
| Tool Init | `daemon/tools/__init__.py` |
| Tool Factory | `daemon/tools/instance.py` (`create_instance_tools()`) |
| Bash Tool | `daemon/tools/bash.py` |
| Filesystem Tools | `daemon/tools/filesystem.py` |
| Instance Tools | `daemon/tools/instance.py` |
| Job Queue Tools | `daemon/tools/job_queue.py` |
| Project Tools | `daemon/tools/project.py` |
| Agent Mother Tools | `daemon/tools/agent_mother.py` |
| Help Tool | `daemon/tools/help.py` |
| Inner Soul | `daemon/tools/inner_soul.py` |
| Access Memory | `daemon/tools/access_memory.py` |
| Agent Registry | `daemon/registry.py` |
| Agent Loader | `daemon/loader.py` |
| LangGraph | `daemon/graph.py` |
| Instance Lifecycle | `daemon/services/instance_lifecycle.py` |
| Instance Messaging | `daemon/services/instance_messaging.py` |
| Child Reports | `daemon/services/child_reports.py` |
| Instance Manager | `daemon/manager.py` |
| Job Queue Service | `daemon/services/job_queue_service.py` |
| Job Processor | `daemon/services/job_processor.py` |
| Job Feedback Observer | `daemon/services/job_feedback_observer.py` |
| Job Retry Engine | `daemon/services/job_retry_engine.py` |
| Job Lock Manager | `daemon/services/job_lock_manager.py` |
| Retry Scheduler | `daemon/sources/adapters/scheduler.py` |
| Job Models | `daemon/repositories/job_queue/models.py` |
| Instance Models | `daemon/repositories/instance/models.py` |
| Task Models | `daemon/repositories/task/models.py` |
| API Schemas | `daemon/routers/schemas.py` |
| Jobs Router | `daemon/routers/jobs.py` |
| Jobs Streaming | `daemon/routers/jobs_streaming.py` |
| Messages Router | `daemon/routers/messages.py` |
| Event Bus | `daemon/services/event_bus.py` |
| Live Event Hub | `daemon/services/live_event_hub.py` |
| Dispatch Event Bus | `daemon/services/dispatch_event_bus.py` |
| Persistence | `daemon/persistence.py` |
