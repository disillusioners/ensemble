# API Reference

Complete HTTP API reference for agents-ensemble daemon.

## Base URL

```
http://localhost:8079
```

## Authentication

Most endpoints require no authentication. Webhook endpoints require the `X-Webhook-Secret` header when configured.

## Documentation

Interactive API documentation is available at:
- Swagger UI: `http://localhost:8079/docs`
- ReDoc: `http://localhost:8079/redoc`

---

## Common Patterns

### Error Responses

All endpoints return errors in a consistent format:

```json
{
  "code": "ERROR_CODE",
  "message": "Human-readable error message",
  "details": {}
}
```

Common error codes:
- `INVALID_REQUEST` - 400 Bad Request
- `INSTANCE_NOT_FOUND` - Instance not found
- `SOURCE_NOT_FOUND` - Source not found
- `JOB_NOT_FOUND` - Job not found
- `PROJECT_NOT_FOUND` - Project not found
- `QUEUE_NOT_FOUND` - Queue not found
- `MAX_INSTANCES_EXCEEDED` - Instance limit reached (429)
- `INTERNAL_ERROR` - Server error (500)

### Pagination

List endpoints support pagination via `limit` and `offset` query parameters.

**Request:**
```
GET /api/endpoint?limit=20&offset=0
```

**Response:**
```json
{
  "items": [...],
  "total": 100,
  "limit": 20,
  "offset": 0
}
```

### SSE Event Format

Server-Sent Events use this format:

```
event: {event_type}
data: {json_payload}

```

Standard event types:
- `connected` - Initial connection confirmation
- `keepalive` - Periodic ping to maintain connection
- `status_update` - Status change notification
- `completed` - Terminal state reached
- `error` - Error occurred
- `notification` - General notification (notifications endpoint)

---

## Global Endpoints

### Health Check

Check daemon health status.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/health` | Get health status | None |

**Response:**
```json
{
  "status": "healthy",
  "uptime_seconds": 3600.5,
  "version": "0.1.0"
}
```

### Server Info

Get basic server information.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/info` | Get server info | None |

**Response:**
```json
{
  "name": "agents-ensemble",
  "version": "0.1.0",
  "description": "Multi-Agent AI Daemon with LangGraph"
}
```

---

## Agents

Agent management endpoints for listing, creating, and deleting agents.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/agents` | List all agents | None |
| POST | `/api/agents` | Create new agent | None |
| DELETE | `/api/agents/{agent_id}` | Delete agent (soft delete to trash) | None |

### List Agents

**Response:**
```json
{
  "agents": [
    {
      "id": "developer",
      "name": "Developer Agent",
      "description": "Software development agent",
      "icon": "💻",
      "color": "accent-blue",
      "version": "1.0.0",
      "agent_dir": "./agents/developer",
      "system": false
    }
  ]
}
```

### Create Agent

**Request:**
```json
{
  "id": "my-agent",
  "name": "My Agent",
  "description": "Custom agent description",
  "icon": "🤖",
  "color": "accent-green"
}
```

### Delete Agent

Returns deleted status with trash path.

```json
{
  "deleted": true,
  "agent_id": "my-agent",
  "trashed_as": "my-agent_20260315_103045"
}
```

---

## Instances

Instance management for agent instances (sessions).

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/instances` | Spawn new instance | None |
| GET | `/api/instances` | List instances | None |
| GET | `/api/instances/{instance_id}` | Get instance info | None |
| DELETE | `/api/instances/{instance_id}` | Terminate instance | None |
| POST | `/api/instances/{instance_id}/pause` | Pause instance | None |
| POST | `/api/instances/{instance_id}/resume` | Resume instance | None |
| POST | `/api/instances/{instance_id}/stop` | Stop instance (deprecated, use pause) | None |
| GET | `/api/instances/{instance_id}/messages` | Get message history | None |

### Spawn Instance

**Request:**
```json
{
  "agent_id": "developer",
  "instance_id": null,
  "project_id": null
}
```

**Response:**
```json
{
  "instance_id": "uuid",
  "agent_id": "developer",
  "agent_dir": "./agents/developer",
  "status": "RUNNING",
  "title": "Help with login bug",
  "parent_id": null,
  "children": [],
  "mcp_tool_names": ["file_read", "file_write"],
  "created_at": "2025-03-15T10:00:00Z",
  "updated_at": "2025-03-15T10:00:00Z",
  "project_id": "system"
}
```

### List Instances

**Query Parameters:**
- `limit` (int, default: 20) - Maximum number to return
- `offset` (int, default: 0) - Number to skip
- `project_id` (string, optional) - Filter by project
- `exclude_kb` (bool, default: true) - Exclude KB-related instances

**Response:**
```json
{
  "instances": [...],
  "total": 50,
  "limit": 20,
  "offset": 0,
  "has_more": true
}
```

### Instance Status Values

- `RUNNING` - Active and processing
- `PAUSED` - Paused, can be resumed
- `IDLE` - Idle, waiting for messages
- `WAITING_CHILDREN` - Waiting for child instances

### Pause Instance

Pauses instance and cascades to children.

```json
{
  "paused": true,
  "paused_ids": ["instance-uuid"],
  "skipped_ids": []
}
```

### Resume Instance

**Request Body (optional):**
```json
{
  "message": "Resume with this message"
}
```

---

## Messages & SSE

Send messages to instances and receive streaming events.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/instances/{instance_id}/messages` | Send message | None |
| GET | `/api/instances/{instance_id}/messages/{message_id}` | Get message status | None |
| GET | `/api/instances/{instance_id}/events` | SSE event stream | None |

### Send Message

**Request:**
```json
{
  "content": "Hello, agent!",
  "images": null
}
```

**Response (normal path):**
```json
{
  "message_id": "job-uuid",
  "role": "assistant",
  "content": "",
  "thinking": null,
  "thinking_extracted": null,
  "tool_calls": null,
  "images": null,
  "auto_resumed": false,
  "resume_info": null
}
```

**Response (auto-resumed PAUSED instance):**
```json
{
  "message_id": null,
  "role": "user",
  "content": "Hello, agent!",
  "auto_resumed": true,
  "resume_info": {
    "resumed": true,
    "resumed_ids": ["instance-uuid"],
    "skipped_ids": [],
    "target_id": "instance-uuid",
    "resume_results": {}
  }
}
```

### Get Message Status

**Response:**
```json
{
  "message_id": "job-uuid",
  "instance_id": "instance-uuid",
  "status": "processing",
  "result_summary": null,
  "error": null
}
```

### SSE Event Stream

Connects to real-time instance events.

**Event Types:**
- `connected` - Initial connection with instance_id
- `checkpoint` - Checkpoint saved
- `user_message` - User message received
- `assistant_message` - Assistant message generated
- `thinking` - Thinking/reasoning content
- `tool_call` - Tool execution
- `error` - Error occurred
- `status_change` - Instance status changed
- `keepalive` - Connection ping

---

## Sources

Message source management (Telegram, Webhook, WhatsApp, Discord, Scheduler).

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/sources` | List all sources | None |
| POST | `/api/sources` | Create source | None |
| POST | `/api/sources/test` | Test source configuration | None |
| GET | `/api/sources/{source_id}` | Get source details | None |
| PUT | `/api/sources/{source_id}` | Update source | None |
| DELETE | `/api/sources/{source_id}` | Delete source | None |
| POST | `/api/sources/{source_id}/start` | Start source adapter | None |
| POST | `/api/sources/{source_id}/stop` | Stop source adapter | None |

### Source Types

- `telegram` - Telegram bot integration
- `webhook` - Generic webhook receiver
- `whatsapp` - WhatsApp integration
- `discord` - Discord bot integration
- `scheduler` - Cron-based scheduling

### Source Status

- `running` - Adapter is active
- `stopped` - Adapter is stopped
- `error` - Error occurred

### Create Source

**Request:**
```json
{
  "source_id": "my-telegram",
  "source_type": "telegram",
  "name": "My Telegram Bot",
  "config": {
    "bot_token": "xxx"
  },
  "credentials": {
    "api_key": "xxx"
  },
  "enabled": true
}
```

### Test Source

Tests connection without saving.

```json
{
  "source_type": "telegram",
  "config": {
    "bot_token": "xxx"
  },
  "credentials": {}
}
```

**Response:**
```json
{
  "success": true,
  "message": "Connection successful"
}
```

### Start/Stop Source

```json
{
  "source_id": "my-telegram",
  "status": "running",
  "message": "Source started successfully"
}
```

---

## Source Mappings

Map external users to agent instances.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/sources/{source_id}/mappings` | List mappings | None |
| POST | `/api/sources/{source_id}/mappings` | Create mapping | None |
| DELETE | `/api/sources/{source_id}/mappings/{mapping_id}` | Delete mapping | None |

### Create Mapping

Creates instance and maps external user.

**Request:**
```json
{
  "external_user_id": "telegram:123456",
  "agent_id": "developer",
  "metadata": {}
}
```

**Response:**
```json
{
  "mapping_id": "my-telegram:telegram:123456",
  "source_id": "my-telegram",
  "external_user_id": "telegram:123456",
  "agent_instance_id": "instance-uuid",
  "agent_id": "developer",
  "agent_dir": "./agents/developer",
  "metadata": {},
  "last_message_at": "2025-03-15T10:00:00Z",
  "created_at": "2025-03-15T10:00:00Z"
}
```

---

## Schedules

Scheduler source management (special type of source).

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/schedules` | List all schedules | None |
| PUT | `/api/schedules/{schedule_id}` | Update schedule | None |
| POST | `/api/schedules/{schedule_id}/trigger` | Trigger manually | None |
| POST | `/api/schedules/{schedule_id}/start` | Start scheduler | None |
| POST | `/api/schedules/{schedule_id}/stop` | Stop scheduler | None |
| GET | `/api/schedules/{schedule_id}/executions` | Get execution history | None |

### List Schedules

**Response:**
```json
{
  "schedules": [
    {
      "id": "daily-report",
      "name": "Daily Report",
      "config": {
        "cron": "0 9 * * *",
        "agent_id": "reporter"
      },
      "status": "running",
      "created_at": "2025-03-15T10:00:00Z",
      "updated_at": "2025-03-15T10:00:00Z",
      "last_run_at": "2025-03-14T09:00:00Z",
      "next_run_at": "2025-03-15T09:00:00Z"
    }
  ]
}
```

### Trigger Schedule

Manually triggers a scheduled job.

**Response:**
```json
{
  "execution_id": "exec-uuid",
  "schedule_id": "daily-report",
  "message": "Schedule triggered successfully"
}
```

### Get Executions

**Query Parameters:**
- `limit` (int, default: 100) - Maximum to return
- `offset` (int, default: 0) - Number to skip

**Response:**
```json
{
  "executions": [
    {
      "execution_id": "exec-uuid",
      "schedule_id": "daily-report",
      "triggered_at": "2025-03-15T09:00:00Z",
      "instance_id": "instance-uuid",
      "status": "completed",
      "error_message": null,
      "completed_at": "2025-03-15T09:05:00Z"
    }
  ],
  "total": 30
}
```

---

## Webhooks

Receive incoming webhooks from external services.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/webhooks/{source_id}` | Receive webhook | X-Webhook-Secret |

### Webhook Request

**Headers:**
- `X-Webhook-Secret` - Required if source has webhook_secret configured

**Body:** JSON payload from external service

**Response:**
```json
{
  "received": true,
  "source_id": "my-webhook"
}
```

---

## Jobs

Job queue management for async task processing.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/jobs` | Create job | None |
| GET | `/api/jobs` | List jobs | None |
| GET | `/api/jobs/{job_id}` | Get job details | None |
| DELETE | `/api/jobs/{job_id}` | Cancel/delete job | None |
| POST | `/api/jobs/{job_id}/cancel` | Cancel job | None |
| POST | `/api/jobs/{job_id}/restore` | Restore deleted job | None |
| POST | `/api/jobs/{job_id}/retry` | Retry failed job | None |
| GET | `/api/jobs/{job_id}/events` | SSE job stream | None |

### Job Status Values

- `pending` - Waiting in queue
- `processing` - Currently being processed
- `completed` - Successfully completed
- `failed` - Failed (can retry)
- `cancelled` - Cancelled by user
- `dead_letter` - Moved to DLQ after max retries

### Create Job

**Request:**
```json
{
  "agent_id": "developer",
  "message": "Fix the login bug",
  "project_id": "project-uuid",
  "queue_id": null,
  "priority": 7,
  "source": "api",
  "metadata": {
    "user_id": "user-123"
  },
  "idempotency_key": "unique-key-123"
}
```

**Response (201 Created):**
```json
{
  "job_id": "job-uuid",
  "status": "pending",
  "priority": 7,
  "agent_id": "developer",
  "agent_dir": "./agents/developer",
  "project_id": "project-uuid",
  "queue_id": "queue-uuid",
  "instance_id": null,
  "created_at": "2025-03-15T10:00:00Z",
  "started_at": null,
  "completed_at": null,
  "result_summary": null,
  "error_message": null,
  "position": 5,
  "message": "Job queued for processing",
  "source": "api",
  "job_metadata": {"user_id": "user-123"},
  "cancelled_at": null,
  "idempotency_key": "unique-key-123",
  "dlq_reason": null,
  "retry_count": null,
  "moved_to_dlq_at": null,
  "deleted_at": null
}
```

### List Jobs

**Query Parameters:**
- `status` (string) - Filter by status, comma-separated
- `project_id` (string) - Filter by project
- `queue_id` (string) - Filter by queue
- `limit` (int, default: 50) - Maximum to return
- `include_deleted` (bool, default: false) - Include soft-deleted

**Example:**
```
GET /api/jobs?status=pending,processing&project_id=xxx&limit=20
```

### Cancel Job

Cancels a pending or processing job.

### Restore Job

Restores a soft-deleted job (must not be in terminal state).

### Retry Job

- `FAILED` jobs: Creates new job with same parameters
- `DEAD_LETTER` jobs: Resets existing job via DLQ replay

### SSE Job Events

**Event Types:**
- `connected` - Initial connection with job state
- `status_update` - Job status changed
- `completed` - Job reached terminal state
- `error` - Error occurred
- `keepalive` - Connection ping

**Initial Event:**
```json
{
  "event": "connected",
  "data": {
    "job_id": "job-uuid",
    "status": "processing",
    "instance_id": "instance-uuid",
    "queue_id": "queue-uuid"
  }
}
```

---

## Projects

Project management for organizing work and jobs.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/projects` | Create project | None |
| GET | `/api/projects` | List projects | None |
| GET | `/api/projects/{project_id}` | Get project | None |
| DELETE | `/api/projects/{project_id}` | Delete project | None |
| PATCH | `/api/projects/{project_id}/queue-status` | Update queue status | None |
| POST | `/api/projects/{project_id}/pause-queue` | Pause job queue | None |
| POST | `/api/projects/{project_id}/resume-queue` | Resume job queue | None |
| GET | `/api/projects/{project_id}/history` | List history | None |
| POST | `/api/projects/{project_id}/history` | Add history entry | None |
| GET | `/api/projects/{project_id}/history/search` | Search history | None |
| DELETE | `/api/projects/{project_id}/history/{entry_id}` | Delete history entry | None |

### Create Project

**Request:**
```json
{
  "name": "My Project",
  "project_type": "software",
  "main_directory": "/path/to/project",
  "description": "A sample project",
  "tags": ["python", "web"]
}
```

**Response:**
```json
{
  "project_id": "project-uuid",
  "name": "My Project",
  "project_type": "software",
  "status": "active",
  "main_directory": "/path/to/project",
  "related_directories": [],
  "description": "A sample project",
  "job_queue_paused": false,
  "tags": ["python", "web"],
  "shortnames": [],
  "metadata": {},
  "relationships": {},
  "critical_notes": [],
  "recent_history": [],
  "creator_instance_id": null,
  "creator_agent_id": null,
  "created_at": "2025-03-15T10:00:00Z",
  "updated_at": "2025-03-15T10:00:00Z",
  "is_system": false
}
```

**Example with a different project type:**

```json
{
  "name": "AWS Terraform Stack",
  "project_type": "infrastructure",
  "main_directory": "/path/to/tf-repo",
  "description": "Terraform-managed cloud infrastructure",
  "tags": ["terraform", "aws"]
}
```

Other types like `gitops`, `devops`, `library`, `data`, and `mobile` are also supported — see [Allowed Project Types](#allowed-project-types) below.

### Allowed Project Types

The `project_type` field accepts the following 11 values:

- `software` — Application code (web, API, services)
- `documentation` — Docs sites, guides, written material
- `research` — Research projects, experiments, papers
- `task` — One-off tasks, scripts, ad-hoc work
- `general` — Catch-all for projects that don't fit other categories
- `infrastructure` — Infrastructure-as-Code (Terraform, Ansible, Pulumi)
- `gitops` — GitOps pipelines (ArgoCD, Flux, Git-based deployment)
- `devops` — CI/CD, deployment automation, pipeline tooling
- `library` — Reusable libraries, packages, SDKs
- `data` — Data engineering, pipelines, analytics, ML
- `mobile` — Mobile applications (iOS/Android)

Values are validated against the `ProjectType` enum in `daemon/repositories/project/models.py`. Unknown values are rejected with a validation error.

### Delete Project

**Query Parameters:**
- `force` (bool, default: false) - Bypass safety checks

Cascades deletion of instances, jobs, queues, and related data.

### Update Queue Status

**Request:**
```json
{
  "paused": true
}
```

### History Entry Types

- `milestone` - Project milestone
- `commit` - Code commit
- `phase` - Project phase
- `bugfix` - Bug fix
- `deployment` - Deployment
- `note` - General note
- `config_change` - Configuration change
- `feature` - New feature
- `other` - Other

### Add History Entry

**Request:**
```json
{
  "entry_type": "milestone",
  "summary": "Completed Phase 1",
  "details": "Implemented core functionality",
  "entry_metadata": {"phase": 1}
}
```

### Search History

**Query Parameters:**
- `q` (string, required) - Search query
- `limit` (int, default: 20)
- `offset` (int, default: 0)

---

## Queues

Job queue management within projects.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/projects/{project_id}/queues` | List queues | None |
| POST | `/api/projects/{project_id}/queues/ensure-system` | Ensure system queues | None |
| POST | `/api/projects/{project_id}/queues` | Create queue | None |
| GET | `/api/projects/{project_id}/queues/{queue_id}` | Get queue | None |
| PATCH | `/api/projects/{project_id}/queues/{queue_id}` | Update queue | None |
| DELETE | `/api/projects/{project_id}/queues/{queue_id}` | Delete queue | None |
| POST | `/api/projects/{project_id}/queues/{queue_id}/start` | Resume queue | None |
| POST | `/api/projects/{project_id}/queues/{queue_id}/stop` | Pause queue | None |

### Queue Types

- `fifo` - First-in-first-out (concurrency_limit must be 1)
- `parallel` - Parallel processing (concurrency_limit > 1)
- `defer` - Deferred execution (concurrency_limit must be 1); only runs when the owning project is idle
- `background` - System-wide background execution (concurrency_limit must be 1); only runs when ALL projects are idle

### System Queues

Reserved queue names (cannot be created/deleted):
- `system_fifo_queue`
- `system_parallel_queue`
- `system_kb_fifo_queue`
- `system_defer_queue`
- `system_background_queue`

### Create Queue

**Request:**
```json
{
  "queue_name": "my-queue",
  "queue_type": "parallel",
  "concurrency_limit": 3,
  "description": "Custom parallel queue"
}
```

**Response:**
```json
{
  "queue_id": "queue-uuid",
  "project_id": "project-uuid",
  "queue_name": "my-queue",
  "queue_type": "parallel",
  "concurrency_limit": 3,
  "is_system": false,
  "is_paused": false,
  "description": "Custom parallel queue",
  "created_at": "2025-03-15T10:00:00Z",
  "updated_at": "2025-03-15T10:00:00Z",
  "active_jobs": 0,
  "pending_jobs": 0
}
```

### Ensure System Queues

Creates missing system queues idempotently.

**Response:**
```json
{
  "project_id": "project-uuid",
  "existing_queues": ["system_fifo_queue"],
  "created_queues": ["system_parallel_queue"],
  "total_system_queues": 5
}
```

### Update Queue

**Request:**
```json
{
  "queue_name": "updated-queue",
  "concurrency_limit": 5,
  "is_paused": false,
  "description": "Updated description"
}
```

### Delete Queue

- System queues cannot be deleted
- PENDING jobs are reassigned to system FIFO queue
- Cannot delete queue with PROCESSING jobs

---

## Dead Letter Queue (DLQ)

Failed job management within projects.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/projects/{project_id}/dlq` | List DLQ items | None |
| POST | `/api/projects/{project_id}/dlq/replay-all` | Replay all items | None |
| GET | `/api/projects/{project_id}/dlq/{dlq_id}` | Get DLQ item | None |
| POST | `/api/projects/{project_id}/dlq/{dlq_id}/replay` | Replay item | None |
| DELETE | `/api/projects/{project_id}/dlq/{dlq_id}` | Delete item (204 No Content) | None |
| DELETE | `/api/projects/{project_id}/dlq` | Bulk cleanup | None |

### DLQ Reasons

- `MAX_RETRIES` - Exceeded retry limit
- `MANUAL` - Manually moved to DLQ

### List DLQ Items

**Query Parameters:**
- `queue_id` (string, optional) - Filter by queue
- `reason` (string, optional) - Filter by reason
- `limit` (int, default: 50, max: 100)
- `offset` (int, default: 0)

**Response:**
```json
{
  "items": [
    {
      "dlq_id": "dlq-uuid",
      "job_id": "job-uuid",
      "agent_id": "developer",
      "agent_dir": "./agents/developer",
      "message": "Fix the login bug",
      "source": "api",
      "project_id": "project-uuid",
      "queue_id": "queue-uuid" | null,
      "priority": 5,
      "error_message": "Connection timeout after 3 retries",
      "retry_count": 3,
      "failed_at": "2025-03-15T10:00:00Z",
      "moved_to_dlq_at": "2025-03-15T10:05:00Z",
      "reason": "MAX_RETRIES",
      "metadata": {"user_id": "user-123"}
    }
  ],
  "total": 10
}
```

### Replay DLQ Item

Atomic operation that:
1. Updates job status from DEAD_LETTER to PENDING
2. Resets retry_count to 0
3. Clears error fields
4. Deletes DLQ entry

**Response:**
```json
{
  "job_id": "job-uuid",
  "status": "pending",
  "message": "Job queued for replay"
}
```

### Replay All

**Query Parameters:**
- `queue_id` (string, optional)
- `reason` (string, optional)
- `limit` (int, default: 100, max: 1000)

**Response:**
```json
{
  "total": 150,
  "limit": 100,
  "replayed": 95,
  "failed": 3,
  "skipped": 52,
  "errors": [
    {"dlq_id": "dlq-uuid", "error": "Job not in dead_letter state"}
  ]
}
```

### Bulk Cleanup

**Query Parameters:**
- `max_age_days` (int, default: 30) - Delete items older than N days
- `reason` (string, optional)

**Response:**
```json
{
  "deleted_count": 5,
  "message": "Deleted 5 DLQ items"
}
```

---

## MCP Servers

Model Context Protocol server management for tool integrations.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/mcp-servers` | List MCP servers | None |
| POST | `/api/mcp-servers` | Create server | None |
| POST | `/api/mcp-servers/test-connection` | Test connection | None |
| GET | `/api/mcp-servers/builtin-templates` | List templates | None |
| POST | `/api/mcp-servers/configure-builtin` | Configure builtin | None |
| GET | `/api/mcp-servers/{server_id}` | Get server | None |
| PUT | `/api/mcp-servers/{server_id}` | Update server | None |
| DELETE | `/api/mcp-servers/{server_id}` | Delete server | None |
| POST | `/api/mcp-servers/{server_id}/reset-builtin` | Reset builtin | None |

### Create MCP Server

**Request:**
```json
{
  "name": "filesystem",
  "description": "File system operations",
  "config": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
  },
  "is_active": true
}
```

**Response:**
```json
{
  "id": "server-uuid",
  "name": "filesystem",
  "description": "File system operations",
  "config": {...},
  "is_active": true,
  "is_builtin": false,
  "config_schema": null,
  "config_schema_version": "0",
  "initial_values": null,
  "created_at": "2025-03-15T10:00:00Z",
  "updated_at": "2025-03-15T10:00:00Z"
}
```

### Test Connection

**Request:**
```json
{
  "config": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
  }
}
```

**Response:**
```json
{
  "success": true,
  "message": "Connection successful - server responded with 5 tools",
  "tools_count": 5
}
```

### Built-in Templates

Returns available built-in MCP server templates.

**Response:**
```json
{
  "templates": [
    {
      "name": "webfetch",
      "display_name": "WebFetch",
      "description": "Fetch and read web page content",
      "config_schema": [
        {
          "key": "user_agent",
          "label": "User Agent",
          "type": "text",
          "description": "Custom User-Agent string",
          "default": "Mozilla/5.0...",
          "required": false
        },
        {
          "key": "ignore_robots_txt",
          "label": "Ignore robots.txt",
          "type": "boolean",
          "description": "Bypass robots.txt restrictions",
          "default": false,
          "required": false
        }
      ]
    },
    {
      "name": "context7",
      "display_name": "Context7",
      "description": "Provides up-to-date library documentation",
      "config_schema": []
    }
  ]
}
```

### Configure Built-in

**Request:**
```json
{
  "template_name": "webfetch",
  "values": {
    "user_agent": "MyCustomAgent/1.0"
  }
}
```

### Update MCP Server

Built-in servers have restrictions:
- Cannot modify name or description
- Cannot modify config (use configure-builtin or reset-builtin)

### Reset Built-in

Resets a built-in server to its default configuration.

---

## MCP KB Endpoints

Knowledge base MCP server with StreamableHTTP and SSE transports.

### StreamableHTTP Transport

```
POST /api/mcp/kb
```

MCP protocol endpoint using StreamableHTTP transport. Accepts JSON-RPC requests.

**Headers:**
- `Content-Type: application/json`
- `Accept: application/json, text/event-stream`

### SSE Transport

```
GET /api/mcp/kb/sse
```

SSE-based MCP transport for knowledge base operations. Client connects via GET to establish SSE stream, then sends requests via POST.

### Available Tools

These 4 tools are exposed by the ensemble-kb MCP server:

| Tool Name | Description |
|-----------|-------------|
| `ensemble_kb_explore` | Query the project knowledge base |
| `ensemble_kb_experience` | Query agent experience and memory |
| `ensemble_kb_list_projects` | List all projects |
| `ensemble_kb_search_projects` | Search projects by name or criteria |

---

## Notifications

Global SSE stream for notification events.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/notifications/stream` | SSE notification stream | None |

### Event Types

- `connected` - Connection established
- `notification` - Root instance completion notification
- `keepalive` - Periodic ping

### Notification Payload

```json
{
  "instance_id": "uuid",
  "agent_id": "developer",
  "name": "Developer Agent",
  "status": "completed",
  "timestamp": "2025-03-15T10:00:00Z"
}
```

