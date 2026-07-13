# Usage Guide — agents-ensemble HTTP API

This guide covers the complete HTTP API for operating the agents-ensemble multi-agent daemon. All endpoints are prefixed with `/api`. The daemon runs on port **8079** in development and port **8088** in production.

> **Base URL**: `http://localhost:8079/api` (development)

---

## Table of Contents

1. [Starting a Conversation](#1-starting-a-conversation)
2. [Sending Messages](#2-sending-messages)
3. [Instance Lifecycle](#3-instance-lifecycle)
4. [Instance Hierarchy (Parent-Child)](#4-instance-hierarchy-parent-child)
5. [Memory System](#5-memory-system)
6. [Critical Notes](#6-critical-notes)
7. [Projects](#7-projects)
8. [Sources & Messaging Adapters](#8-sources--messaging-adapters)
9. [Job Queue](#9-job-queue)
10. [Queue Management](#queue-management)
11. [Dead Letter Queue](#dead-letter-queue)
12. [Schedules](#schedules)
13. [Agents](#11-agents)
14. [MCP Servers](#mcp-servers)
15. [Real-Time Events (SSE)](#real-time-events-sse)
16. [Error Codes](#error-codes)

---

## 1. Starting a Conversation

### Spawn an Agent Instance

**POST `/api/instances`**

Create a new agent instance. An instance is an isolated execution environment with its own message history and tools.

**Request Body**

```json
{
  "agent_id": "leader",
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "instance_id": "550e8400-e29b-41d4-a716-446655440001"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent_id` | string | Yes | Agent identifier (e.g., `leader`, `developer`, `reviewer`, `tester`) |
| `project_id` | string | No | UUID of an existing project to associate with this instance |
| `instance_id` | string | No | UUID to assign; auto-generated if omitted |

**Response** `201 Created`

```json
{
  "instance_id": "550e8400-e29b-41d4-a716-446655440001",
  "agent_id": "leader",
  "agent_dir": "./agents/leader",
  "status": "idle",
  "title": null,
  "parent_id": null,
  "children": [],
  "mcp_tool_names": ["webfetch", "context7_fetch_docs"],
  "created_at": "2026-05-29T10:00:00Z",
  "updated_at": "2026-05-29T10:00:00Z",
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "waiting_for": null,
  "pending_count": null
}
```

**cURL Example**

```bash
curl -X POST http://localhost:8079/api/instances \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "leader",
    "project_id": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

**What Happens Internally**

1. Agent prompt files (`soul.md`, `rule.md`, `memory.md`, `workflow.md`) are loaded and cached
2. MCP tools are preloaded for the agent
3. A LangGraph state machine is built with a checkpointer for crash recovery
4. Database record created with status `idle`
5. Instance is ready to receive messages

> See [docs/agents.md](agents.md) for how agents are defined.

---

## 2. Sending Messages

### Send a Message

**POST `/api/instances/{instance_id}/messages`

Send a message to an instance and get a response. Messages are queued and processed asynchronously — the HTTP response returns immediately with a `message_id`.

**Request Body**

```json
{
  "content": "Build a REST API for user management",
  "images": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | Yes | Message text to send |
| `images` | array | No | Base64-encoded images (max 3, max 10MB each, format: `data:image/png;base64,...`) |

**Response**

```json
{
  "message_id": "660e8400-e29b-41d4-a716-446655440002",
  "role": "assistant",
  "content": "",
  "thinking": null,
  "thinking_extracted": null,
  "tool_calls": null,
  "images": null,
  "created_at": "2026-05-29T10:01:00Z",
  "auto_resumed": false,
  "resume_info": null
}
```

> The `message_id` is used to poll for completion and to subscribe to SSE events.

**Auto-Resume Behavior**

If the instance is in `PAUSED` status, the request automatically resumes it:

```json
{
  "message_id": null,
  "auto_resumed": true,
  "resume_info": {
    "resumed": true,
    "resumed_ids": ["660e8400-..."],
    "skipped_ids": [],
    "target_id": "660e8400-...",
    "resume_results": { ... }
  }
}
```

**Message Flow**

```
Client → POST /messages → MessageQueue DB → WorkerPool picks up
       → LLM call → Tool execution (optional) → Response streaming via SSE
       → SSE event: assistant_message
       → SSE event: instance_completed
```

**cURL Example**

```bash
curl -X POST http://localhost:8079/api/instances/550e8400-e29b-41d4-a716-446655440001/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Build a REST API for user management"}'
```

### Get Message Status

**GET `/api/instances/{instance_id}/messages/{message_id}`

Poll for message completion status.

**Response**

```json
{
  "message_id": "660e8400-e29b-41d4-a716-446655440002",
  "instance_id": "550e8400-e29b-41d4-a716-446655440001",
  "status": "completed",
  "result_summary": "Built REST API with FastAPI endpoints for user CRUD operations"
}
```

---

## 3. Instance Lifecycle

### Instance Status States

| Status | Description |
|--------|-------------|
| `idle` | Instance created, waiting for messages |
| `running` | Actively processing a message (LLM call or tool execution) |
| `waiting` | Waiting for a resource (e.g., rate limit) |
| `waiting_children` | All work done, waiting for child agents to complete |
| `completed` | Successfully finished all tasks |
| `paused` | Suspended, can be resumed |
| `error` | Failed with an unrecoverable error |
| `terminated` | Manually terminated, cannot be resumed |

### List Instances

**GET `/api/instances`**

**Query Parameters**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 20 | Max results (1–100) |
| `offset` | int | 0 | Pagination offset |
| `project_id` | string | — | Filter by project |
| `exclude_kb` | bool | true | Exclude KB-related instances |

**Response**

```json
{
  "instances": [
    {
      "instance_id": "550e8400-...",
      "agent_id": "leader",
      "status": "running",
      "title": "Build REST API",
      "parent_id": null,
      "children": ["660e8400-..."],
      "created_at": "2026-05-29T10:00:00Z",
      "updated_at": "2026-05-29T10:05:00Z",
      "project_id": "...",
      "waiting_for": 1,
      "pending_count": 0
    }
  ],
  "total": 15,
  "limit": 20,
  "offset": 0,
  "has_more": false
}
```

### Get Instance Info

**GET `/api/instances/{instance_id}`**

**Response**

```json
{
  "instance_id": "550e8400-e29b-41d4-a716-446655440001",
  "agent_id": "leader",
  "agent_dir": "./agents/leader",
  "status": "running",
  "title": "Build REST API",
  "parent_id": null,
  "children": ["660e8400-e29b-41d4-a716-446655440003"],
  "mcp_tool_names": ["webfetch", "context7_fetch_docs"],
  "created_at": "2026-05-29T10:00:00Z",
  "updated_at": "2026-05-29T10:05:00Z",
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "waiting_for": 1,
  "pending_count": 0
}
```

### Pause Instance

**POST `/api/instances/{instance_id}/pause`**

Pause an instance and all its children (cascade pause). The instance can be resumed later.

**Response**

```json
{
  "paused": true,
  "paused_ids": ["550e8400-...", "660e8400-..."],
  "skipped_ids": []
}
```

### Resume Instance

**POST `/api/instances/{instance_id}/resume`**

Resume a paused instance. If `project_id` is set on the instance, this also resumes its children.

**Request Body** (optional)

```json
{
  "message": "Continue with the implementation"
}
```

**Response**

```json
{
  "resumed": true,
  "resumed_ids": ["550e8400-...", "660e8400-..."],
  "skipped_ids": [],
  "target_id": "550e8400-...",
  "resume_results": {
    "550e8400-...": { "instance_id": "550e8400-...", "job_id": "...", "message_id": "...", "status": "resuming" }
  }
}
```

**Auto-Resume via Send Message**

Sending a message to a paused instance automatically resumes it:

```bash
curl -X POST http://localhost:8079/api/instances/{paused_id}/messages \
  -d '{"content": "Resume work"}'
# Returns auto_resumed: true in response
```

### Terminate Instance

**DELETE `/api/instances/{instance_id}`**

Permanently terminate an instance and all its children. Cannot be resumed.

**Response**

```json
{
  "terminated": true
}
```

### Stop Instance (Deprecated)

> **⚠️ Deprecated**: `POST /instances/{instance_id}/stop` is deprecated. Use `POST /instances/{instance_id}/pause` instead.

The stop endpoint is an alias for pause that exists for backward compatibility.

### Get Message History

**GET `/api/instances/{instance_id}/messages`**

Retrieve all messages in the instance's conversation history.

**Response**: Array of message objects with `role`, `content`, `tool_calls`, `thinking`, etc.

---

## 4. Instance Hierarchy (Parent-Child)

Agents can spawn child instances using the `spawn_instance` tool. The parent-child relationship forms a tree structure.

### How It Works

1. **Spawning**: A parent agent calls `spawn_instance(agent_id="developer", ...)` to create a child
2. **Work**: The child processes its assigned task independently
3. **Completion**: When the child finishes, it sends a `COMPLETION_REPORT` to its parent
4. **Waiting**: The parent's `waiting_for` counter tracks pending children
5. **Cascade**: When `waiting_for` reaches 0, the parent resumes its own work

### waiting_for Counter

The `waiting_for` field on an instance indicates how many child completions the parent is waiting for:

| waiting_for | Meaning |
|-------------|---------|
| `null` or `0` | No children spawned |
| `> 0` | Waiting for N children to complete |
| `0` after being `> 0` | All children done, parent resuming |

### Cascade Operations

Both **pause** and **terminate** cascade to the entire descendant tree:

```bash
# Pausing parent pauses all children too
curl -X POST http://localhost:8079/api/instances/{parent_id}/pause

# Terminating parent terminates all children
curl -X DELETE http://localhost:8079/api/instances/{parent_id}
```

### Completion Report Flow

When a child completes, it sends a report to its parent containing:
- The child's final response
- Child's instance ID and agent type
- Summary of work done

The parent receives this as a `COMPLETION_REPORT` message in its conversation history.

---

## 5. Memory System

Agents maintain persistent memory across sessions using files in their agent directory.

### File Structure

Each agent has a `memory.md` file and a `memories/` directory:

```
agents/leader/
├── soul.md           # Identity and personality
├── memory.md         # Core persistent knowledge (max 2000 words)
├── memories/         # Timestamped event files
│   ├── 2026-05-01-team-retrospective.md
│   ├── 2026-05-15-sprint-planning.md
│   └── archive/      # Archived memories (older than TTL)
│       └── 2026/04/2026-04-15-project-kickoff.md
├── workflow.md       # Process and methodology
└── user.md          # User preferences and relationship
```

### Tools Available to Agents

**inner_soul tool** — Write memories, modify personality

```
inner_soul(request="User prefers TypeScript over Python")
# → Updates user.md (user preference)

inner_soul(request="I learned that early testing catches bugs")
# → Creates memory file in memories/ with timestamp

inner_soul(request="Always check for tests before committing")
# → Updates workflow.md (process)
```

**access_memory tool** — Read memory files

```
access_memory(filename="2026-05-01-team-retrospective.md")
# Returns: Full content of the memory file
```

### Memory Compaction

When `memory.md` exceeds 80% of its 2000-word limit, automatic deduplication runs to remove duplicate lines while preserving structure.

### Memory Archive Lifecycle

| Stage | Location | Trigger |
|-------|----------|---------|
| Active | `memories/` | Recent (< 90 days default) |
| Archived | `memories/archive/YYYY/MM/` | Older than TTL (configurable) |

Archival runs automatically at startup and periodically. Archived memories can still be accessed via `access_memory(filename="archive/2026/04/file.md")`.

### Limits (from growth.md)

| File | Limit |
|------|-------|
| `memory.md` | 2000 words |
| `soul.md` | 2000 characters |
| `memories/` | 2000 chars per request, auto-archived at 90 days |

---

## 6. Critical Notes

Critical notes capture important lessons, conventions, and constraints for a **project** that all agents working on that project should know.

### What Are Critical Notes?

Project-level manual notes injected into every agent's context when that project is active. They appear in the system prompt as:

```
### ⚡ Critical Notes
- 🔴 **[convention]** Use snake_case for Python identifiers *(ref: project-style-guide.md)*
- 🟡 **[risk]** Database migrations must be backward compatible
```

### Categories

| Category | Use Case |
|----------|----------|
| `convention` | Coding standards, naming, formatting |
| `pattern` | Common patterns, architectural decisions |
| `risk` | Known risks, fragile areas, gotchas |
| `decision` | Architectural decisions with rationale |
| `constraint` | Hard constraints, dependencies |

### Priorities

| Priority | Icon | Meaning |
|----------|------|---------|
| `critical` | 🔴 | Must follow, breaks functionality |
| `high` | 🟡 | Important, avoid deviations |
| `medium` | 🟢 | Recommended, not enforced |

### API Endpoints

Critical notes are managed via **agent tools** (not REST endpoints):

#### project_cn_add

```javascript
project_cn_add(
  project_id="550e8400-e29b-41d4-a716-446655440000",
  category="convention",
  priority="high",
  summary="Use snake_case for all Python identifiers",
  reference="project-style-guide.md"
)
```

**Merge Logic**: If a similar entry (same category, ≥2 keyword overlap) exists, they are merged automatically.

**Eviction**: At 30 entries, the oldest lowest-priority entry is evicted.

#### project_cn_list

```javascript
project_cn_list(project_id="550e8400-e29b-41d4-a716-446655440000")
```

#### project_cn_remove

```javascript
project_cn_remove(
  project_id="550e8400-e29b-41d4-a716-446655440000",
  entry_id="entry-uuid"
)
```

---

## 7. Projects

Projects are organizational units that group related work, agents, and context.

### Create Project

**POST `/api/projects`**

```json
{
  "name": "E-Commerce Platform",
  "project_type": "software",
  "main_directory": "/path/to/project",
  "description": "Multi-tenant e-commerce platform",
  "tags": ["python", "fastapi", "postgresql"]
}
```

**Response** `201 Created`

```json
{
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "E-Commerce Platform",
  "project_type": "software",
  "status": "active",
  "main_directory": "/path/to/project",
  "related_directories": [],
  "description": "Multi-tenant e-commerce platform",
  "job_queue_paused": false,
  "tags": ["python", "fastapi", "postgresql"],
  "shortnames": [],
  "metadata": {},
  "relationships": {},
  "critical_notes": [],
  "recent_history": [],
  "creator_instance_id": null,
  "creator_agent_id": null,
  "created_at": "2026-05-29T10:00:00Z",
  "updated_at": "2026-05-29T10:00:00Z",
  "is_system": false
}
```

### List Projects

**GET `/api/projects`**

**Query Parameters**
- `exclude_system`: Exclude system default project (default: false)

### Get Project

**GET `/api/projects/{project_id}`**

### Update Project Queue Status

**PATCH `/api/projects/{project_id}/queue-status`**

```json
{
  "paused": true
}
```

### Pause Project Queue

**POST `/api/projects/{project_id}/pause-queue`**

Pause the job queue for a project. All queued jobs will remain paused until resumed.

**Response** `200 OK`

```json
{
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "E-Commerce Platform",
  "job_queue_paused": true,
  ...
}
```

**cURL Example**

```bash
curl -X POST http://localhost:8079/api/projects/{project_id}/pause-queue
```

### Resume Project Queue

**POST `/api/projects/{project_id}/resume-queue`**

Resume the job queue for a project. Paused jobs will start processing.

**Response** `200 OK`

```json
{
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "E-Commerce Platform",
  "job_queue_paused": false,
  ...
}
```

**cURL Example**

```bash
curl -X POST http://localhost:8079/api/projects/{project_id}/resume-queue
```

### Project History

Projects maintain a history log of significant events.

#### List History

**GET `/api/projects/{project_id}/history`**

**Query Parameters**
- `limit`: Max entries (default: 20)
- `offset`: Pagination offset
- `entry_type`: Filter by type (milestone, commit, phase, bugfix, deployment, note, config_change, feature, other)

#### Add History Entry

**POST `/api/projects/{project_id}/history`**

```json
{
  "entry_type": "milestone",
  "summary": "Completed Phase 1 - Authentication",
  "details": "Implemented JWT-based auth with refresh tokens",
  "entry_metadata": { "phase": 1 }
}
```

#### Search History

**GET `/api/projects/{project_id}/history/search?q={query}`**

### Delete Project

**DELETE `/api/projects/{project_id}?force=false`**

Cascade deletes all associated data (instances, queues, jobs, history, critical notes). Fails if active instances or running jobs exist unless `force=true`.

---

## 8. Sources & Messaging Adapters

Sources are external message adapters that bridge external systems (Telegram, webhooks, Discord, etc.) into the agent system.

### Create Source

**POST `/api/sources`**

```json
{
  "source_id": "my-telegram-bot",
  "source_type": "telegram",
  "name": "Main Telegram Bot",
  "config": {
    "bot_token": "123456:ABC-DEF..."
  },
  "credentials": {
    "api_key": "secret"
  },
  "enabled": true
}
```

**Supported Source Types**

| Type | Description |
|------|-------------|
| `telegram` | Telegram Bot API integration |
| `webhook` | Generic webhook receiver |
| `discord` | Discord bot (not yet implemented) |
| `whatsapp` | WhatsApp (not yet implemented) |
| `scheduler` | Time-based scheduled jobs |

### List Sources

**GET `/api/sources`**

### Get Source

**GET `/api/sources/{source_id}`**

### Update Source

**PUT `/api/sources/{source_id}`**

### Delete Source

**DELETE `/api/sources/{source_id}`**

### Start/Stop Source

**POST `/api/sources/{source_id}/start`**

**POST `/api/sources/{source_id}/stop`**

### Test Source

**POST `/api/sources/test`**

Validates credentials by attempting to connect to the external service.

### Instance Mappings

Route external users to specific agent instances.

#### List Mappings

**GET `/api/sources/{source_id}/mappings`**

#### Create Mapping

**POST `/api/sources/{source_id}/mappings`**

Maps an external user ID to an agent instance:

```json
{
  "external_user_id": "telegram:12345678",
  "agent_id": "leader"
}
```

#### Delete Mapping

**DELETE `/api/sources/{source_id}/mappings/{mapping_id}`**

### Webhook Receiver

**POST `/api/webhooks/{source_id}`

Receive webhook payloads from external systems. Validates `X-Webhook-Secret` header if configured.

```bash
curl -X POST http://localhost:8079/api/webhooks/my-webhook-source \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: my-secret" \
  -d '{"message": "Hello from webhook"}'
```

---

## 9. Job Queue

The job queue manages long-running tasks with persistence, retries, and dead-letter handling.

### Submit Job

**POST `/api/jobs`**

```json
{
  "agent_id": "developer",
  "message": "Fix the login bug in auth.py",
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "priority": 7,
  "source": "api",
  "metadata": { "user_id": "user-123" },
  "idempotency_key": "fix-login-bug-001"
}
```

**Response** `201 Created`

```json
{
  "job_id": "job-uuid",
  "status": "pending",
  "priority": 7,
  "agent_id": "developer",
  "agent_dir": "./agents/developer",
  "project_id": "550e8400-...",
  "queue_id": null,
  "instance_id": null,
  "created_at": "2026-05-29T10:00:00Z",
  "started_at": null,
  "completed_at": null,
  "result_summary": null,
  "error_message": null,
  "position": 3,
  "message": "Job queued for processing",
  "source": "api",
  "job_metadata": { "user_id": "user-123" },
  "idempotency_key": "fix-login-bug-001"
}
```

**Idempotency**: If a non-terminal job with the same `idempotency_key` exists, returns `200 OK` with the existing job instead of creating a duplicate.

### List Jobs

**GET `/api/jobs`**

**Query Parameters**
- `status`: Filter by status (pending, processing, completed, failed, cancelled, dead_letter)
- `project_id`: Filter by project
- `agent_id`: Filter by agent
- `limit`, `offset`: Pagination

### Get Job

**GET `/api/jobs/{job_id}`**

### Cancel Job

**POST `/api/jobs/{job_id}/cancel`**

### Delete Job

**DELETE `/api/jobs/{job_id}`**

### Retry Failed Job

**POST `/api/jobs/{job_id}/retry`**

### Restore Job

**POST `/api/jobs/{job_id}/restore`**

Restore a soft-deleted job. The job must not be in a terminal state.

**Response** `200 OK`

```json
{
  "job_id": "job-uuid",
  "status": "pending",
  ...
  "deleted_at": null,
  "message": "Job restored successfully"
}
```

**Error Response** `400 Bad Request`

```json
{
  "error": "Job cannot be restored",
  "message": "Cannot restore a job in terminal state: completed. Retry the job instead."
}
```

---

## Queue Management

Queues organize and prioritize job processing. Each project has system queues, and you can create custom queues.

### List Queues

**GET `/api/projects/{project_id}/queues`**

**Response**

```json
{
  "queues": [
    {
      "queue_id": "queue-uuid",
      "project_id": "project-uuid",
      "queue_name": "system_fifo_queue",
      "queue_type": "fifo",
      "concurrency_limit": 1,
      "is_system": true,
      "is_paused": false,
      "description": null,
      "created_at": "2026-05-29T10:00:00",
      "updated_at": "2026-05-29T10:00:00",
      "active_jobs": 0,
      "pending_jobs": 5
    }
  ],
  "total": 4
}
```

### Create Queue

**POST `/api/projects/{project_id}/queues`**

Create a custom queue for specialized job processing.

**Request**

```json
{
  "queue_name": "high-priority",
  "queue_type": "parallel",
  "concurrency_limit": 3,
  "description": "High priority parallel processing"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `queue_name` | string | Yes | Unique name per project |
| `queue_type` | string | No | `fifo` (default), `parallel`, `defer`, or `background` |
| `concurrency_limit` | int | No | Max concurrent jobs (default: 1, max: 20) |
| `description` | string | No | Optional description |

**Constraints:**
- FIFO, DEFER, and BACKGROUND queues: `concurrency_limit` must be 1
- Reserved names: `system_fifo_queue`, `system_parallel_queue`, `system_kb_fifo_queue`, `system_defer_queue`, `system_background_queue`

### Get Queue

**GET `/api/projects/{project_id}/queues/{queue_id}`**

Get details of a specific queue including active/pending job counts.

### Update Queue

**PATCH `/api/projects/{project_id}/queues/{queue_id}`**

Update queue settings.

```json
{
  "queue_name": "renamed-queue",
  "is_paused": true,
  "description": "Updated description"
}
```

### Delete Queue

**DELETE `/api/projects/{project_id}/queues/{queue_id}`**

Delete a custom queue. PENDING jobs are reassigned to system FIFO queue. Cannot delete system queues.

### Pause/Resume Queue

**POST `/api/projects/{project_id}/queues/{queue_id}/stop`**

Pause queue processing (sets `is_paused=true`).

**POST `/api/projects/{project_id}/queues/{queue_id}/start`**

Resume paused queue (sets `is_paused=false`).

> For complete queue documentation including queue types, system queues, and behavior, see [docs/job-queue.md](job-queue.md).

---

## Dead Letter Queue

The DLQ stores jobs that have exhausted their retry attempts and require manual inspection.

### What is DLQ?

When a job fails repeatedly, it's moved to the DLQ with a reason:
- `MAX_RETRIES`: Job exceeded maximum retry attempts
- `MANUAL`: Manually moved to DLQ

### Key Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/projects/{project_id}/dlq` | List DLQ items |
| `GET /api/projects/{project_id}/dlq/{dlq_id}` | Get DLQ item details |
| `POST /api/projects/{project_id}/dlq/{dlq_id}/replay` | Replay single job |
| `POST /api/projects/{project_id}/dlq/replay-all` | Replay all DLQ jobs |
| `DELETE /api/projects/{project_id}/dlq` | Cleanup old DLQ items |

### Example: Replay DLQ Job

```bash
# Replay a failed job
curl -X POST http://localhost:8079/api/projects/{project_id}/dlq/{dlq_id}/replay
```

> For complete DLQ documentation, see [docs/job-queue.md](job-queue.md).

---

## Schedules

Schedules are scheduler sources that trigger agent invocations at configured times using cron-like expressions.

### List Schedules

**GET `/api/schedules`**

### Update Schedule

**PUT `/api/schedules/{schedule_id}`**

Update schedule configuration (name, cron expression, agent, etc.).

**Config Options**

```json
{
  "name": "Daily Standup",
  "cron": "0 9 * * 1-5",
  "agent_id": "leader",
  "message": "Run daily standup report",
  "instance_mode": "reuse_instance",
  "max_concurrent": 1
}
```

| Option | Description |
|--------|-------------|
| `instance_mode` | `new_instance` (default) or `reuse_instance` |
| `max_concurrent` | Max concurrent executions (default: 1) |
| `project_id` | Optional project to pass to instances |

### Trigger Manually

**POST `/api/schedules/{schedule_id}/trigger`**

Immediately trigger a scheduled job, bypassing the cron schedule.

### Start/Stop Schedule

**POST `/api/schedules/{schedule_id}/start`**

**POST `/api/schedules/{schedule_id}/stop`**

### Execution History

**GET `/api/schedules/{schedule_id}/executions`**

Returns recent executions with status, timestamps, and errors.

---

## 11. Agents

### List Available Agents

**GET `/api/agents`**

Returns all agents defined in the `agents/` directory.

**Response**

```json
{
  "agents": [
    {
      "id": "leader",
      "name": "Leader",
      "description": "Coordinates tasks and manages workflow delegation",
      "icon": "👑",
      "color": "accent-amber",
      "version": "1.1.0",
      "agent_dir": "./agents/leader",
      "system": false
    }
  ]
}
```

### Create Agent

**POST `/api/agents`**

Create a new agent from the `_baby_template` template.

```json
{
  "id": "my-agent",
  "name": "My Agent",
  "description": "Custom agent for XYZ tasks",
  "icon": "🤖",
  "color": "accent-blue"
}
```

### Delete Agent

**DELETE `/api/agents/{agent_id}`**

Soft-deletes by moving to `_trash`.

---

## MCP Servers

MCP (Model Context Protocol) servers extend agent capabilities with external tools and resources.

### List MCP Servers

**GET `/api/mcp-servers`**

**Response**

```json
{
  "mcp_servers": [
    {
      "id": "server-uuid",
      "name": "filesystem",
      "description": "File system operations",
      "config": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"] },
      "is_active": true,
      "is_builtin": false,
      "config_schema": null,
      "created_at": "2026-05-29T10:00:00Z",
      "updated_at": "2026-05-29T10:00:00Z"
    }
  ]
}
```

### Create MCP Server

**POST `/api/mcp-servers`**

Create a new MCP server configuration.

**Request**

```json
{
  "name": "my-server",
  "description": "My custom MCP server",
  "config": {
    "command": "npx",
    "args": ["-y", "@mcp/server", "--arg1", "value1"]
  },
  "is_active": true
}
```

**Response** `201 Created`

```json
{
  "id": "server-uuid",
  "name": "my-server",
  "description": "My custom MCP server",
  "config": { "command": "npx", "args": ["-y", "@mcp/server"] },
  "is_active": true,
  "is_builtin": false,
  "created_at": "2026-05-29T10:00:00Z",
  "updated_at": "2026-05-29T10:00:00Z"
}
```

**cURL Example**

```bash
curl -X POST http://localhost:8079/api/mcp-servers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-server",
    "description": "My custom MCP server",
    "config": { "command": "npx", "args": ["-y", "@mcp/server"] },
    "is_active": true
  }'
```

### Get MCP Server

**GET `/api/mcp-servers/{server_id}`**

Get details of a specific MCP server including config schema for built-in servers.

### Update MCP Server

**PUT `/api/mcp-servers/{server_id}`**

Update an MCP server configuration.

```json
{
  "name": "updated-name",
  "description": "Updated description",
  "config": { "command": "npx", "args": ["-y", "@mcp/server", "--new-arg"] },
  "is_active": false
}
```

> Built-in servers cannot have their name, description, or config modified directly. Use `/configure-builtin` or `/reset-builtin` instead.

### Delete MCP Server

**DELETE `/api/mcp-servers/{server_id}`**

Delete an MCP server. Built-in servers cannot be deleted.

### Test Connection

**POST `/api/mcp-servers/test-connection`**

Test MCP server connectivity before saving. Does not save anything to the database.

**Request**

```json
{
  "config": {
    "command": "npx",
    "args": ["-y", "@mcp/server"]
  }
}
```

**Response**

```json
{
  "success": true,
  "message": "Connection successful — server responded with 5 tools",
  "tools_count": 5
}
```

**Error Response**

```json
{
  "success": false,
  "message": "Connection failed: connection refused"
}
```

### List Built-in Templates

**GET `/api/mcp-servers/builtin-templates`**

List all available built-in server templates.

**Response**

```json
{
  "templates": [
    {
      "name": "filesystem",
      "display_name": "File System",
      "description": "File system operations for reading, writing, and navigating directories",
      "config_schema": [
        { "name": "allowedDirectories", "type": "string", "description": "Allowed directories for file operations", "required": true }
      ]
    }
  ]
}
```

### Configure Built-in Server

**POST `/api/mcp-servers/configure-builtin`**

Configure a built-in MCP server from a template.

**Request**

```json
{
  "template_name": "filesystem",
  "values": {
    "allowedDirectories": "/Users/me/projects"
  }
}
```

**Response** `201 Created`

```json
{
  "id": "server-uuid",
  "name": "filesystem",
  "description": "File system operations for reading, writing, and navigating directories",
  "config": { "allowedDirectories": "/Users/me/projects" },
  "is_active": true,
  "is_builtin": true,
  "created_at": "2026-05-29T10:00:00Z",
  "updated_at": "2026-05-29T10:00:00Z"
}
```

### Reset Built-in Server

**POST `/api/mcp-servers/{server_id}/reset-builtin`**

Reset a built-in MCP server to its default configuration.

**cURL Example**

```bash
curl -X POST http://localhost:8079/api/mcp-servers/{server_id}/reset-builtin
```

---

## Real-Time Events (SSE)

### Instance Events

**GET `/api/instances/{instance_id}/events`**

Subscribe to real-time events for an instance.

**Event Types**

| Event | Description |
|-------|-------------|
| `connected` | SSE connection established |
| `keepalive` | Periodic ping to keep connection alive |
| `user_message` | User message received |
| `assistant_message` | Agent response generated |
| `thinking` | Reasoning/thinking content |
| `tool_call` | Tool invocation |
| `tool_result` | Tool execution result |
| `instance_completed` | Instance finished |
| `child_completed` | Child instance reported completion |
| `error` | Error occurred |

**Event Format**

```json
{
  "event_type": "assistant_message",
  "instance_id": "550e8400-...",
  "message_id": "660e8400-...",
  "content": "I've analyzed the requirements and here's my plan...",
  "tool_calls": null,
  "timestamp": "2026-05-29T10:05:00Z"
}
```

**cURL Example**

```bash
curl -N http://localhost:8079/api/instances/{instance_id}/events \
  -H "Accept: text/event-stream"
```

### Global Notifications

**GET `/api/notifications/stream`**

Subscribe to global notification events (root instance completions, job status changes).

### Job Events

**GET `/api/jobs/{job_id}/events`**

Subscribe to real-time events for a job.

**Event Types**

| Event | Description |
|-------|-------------|
| `connected` | SSE connection established with initial job state |
| `status_update` | Job status changed |
| `completed` | Job reached terminal state |
| `keepalive` | Periodic ping to keep connection alive |
| `error` | Error occurred |

**Event Format**

```json
{
  "event": "status_update",
  "data": {
    "job_id": "job-abc123",
    "status": "processing",
    "previous_status": "pending",
    "instance_id": "inst-xyz",
    "queue_id": "queue-abc"
  }
}
```

**Completion Event**

```json
{
  "event": "completed",
  "data": {
    "job_id": "job-abc123",
    "status": "completed",
    "result_summary": "Task completed successfully",
    "error_message": null,
    "queue_id": "queue-abc"
  }
}
```

**cURL Example**

```bash
curl -N http://localhost:8079/api/jobs/{job_id}/events \
  -H "Accept: text/event-stream"
```

> The stream automatically closes when the job reaches a terminal state (completed, failed, cancelled, or dead_letter).

---

## 13. Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `INSTANCE_NOT_FOUND` | 404 | Instance does not exist |
| `INSTANCE_TERMINATED` | 400 | Instance was terminated and cannot be resumed |
| `MAX_INSTANCES_EXCEEDED` | 429 | Too many concurrent instances |
| `INVALID_REQUEST` | 400 | Malformed request |
| `INTERNAL_ERROR` | 500 | Server-side error |
| `SOURCE_NOT_FOUND` | 404 | Source adapter not found |
| `SOURCE_ALREADY_EXISTS` | 409 | Source ID already registered |
| `SOURCE_TYPE_NOT_SUPPORTED` | 400 | Unknown source type |
| `MAPPING_NOT_FOUND` | 404 | Instance mapping not found |
| `MAPPING_ALREADY_EXISTS` | 409 | Mapping already exists |
| `JOB_NOT_FOUND` | 404 | Job does not exist |
| `PROJECT_NOT_FOUND` | 404 | Project does not exist |
| `INVALID_PROJECT_ID` | 400 | Invalid project ID format |
| `RATE_LIMITED` | 429 | Rate limit exceeded |
| `LLM_ERROR` | 500 | LLM provider error |
| `MCP_SERVER_NOT_FOUND` | 404 | MCP server not found |
| `MCP_SERVER_ALREADY_EXISTS` | 409 | MCP server with name already exists |
| `SERVICE_UNAVAILABLE` | 503 | Service temporarily unavailable |
| `BUILTIN_SERVER_PROTECTED` | 403 | Cannot modify/delete built-in MCP server |

---

## Quick Reference

### Common Workflows

**Start a conversation with a project**

```bash
# 1. Create instance
INSTANCE=$(curl -s -X POST http://localhost:8079/api/instances \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "leader", "project_id": "PROJECT_ID"}')
INSTANCE_ID=$(echo $INSTANCE | jq -r '.instance_id')

# 2. Send message
curl -X POST http://localhost:8079/api/instances/$INSTANCE_ID/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello, let us start building the API"}'

# 3. Subscribe to events
curl -N http://localhost:8079/api/instances/$INSTANCE_ID/events
```

**Pause and resume**

```bash
# Pause
curl -X POST http://localhost:8079/api/instances/$INSTANCE_ID/pause

# Resume
curl -X POST http://localhost:8079/api/instances/$INSTANCE_ID/resume \
  -H "Content-Type: application/json" \
  -d '{"message": "Continue where we left off"}'
```

**Submit a background job**

```bash
curl -X POST http://localhost:8079/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "developer",
    "message": "Write tests for user service",
    "project_id": "PROJECT_ID",
    "priority": 5
  }'
```
