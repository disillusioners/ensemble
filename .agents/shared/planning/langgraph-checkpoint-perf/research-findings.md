# Checkpoint-Persistence Performance Research Findings

Research conducted for checkpoint-persistence performance plan. All citations are file:line format.

---

## 1. MIGRATION CONVENTIONS

### Migration Runner Mechanics

**File: daemon/migrations/runner.py**

The migration system uses ordered SQL files with checksum tracking:

- **Naming Convention** (runner.py:77-85): Filenames follow pattern `YYYYMMDD_HHMMSS_descriptive_name.sql`
  - Version extracted via regex: `^(\d{8}_\d{6})` (runner.py:78)
  - Human-readable name: `^\d{8}_\d{6}_(.+)$` with underscores → spaces (runner.py:84)
- **Checksum Calculation** (runner.py:56-60): SHA-256 of full file content, stored in `SchemaMigration.checksum` field
- **File Structure** (runner.py:88-95): Parsed with `-- UP` and `-- DOWN` sections using regex `re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)`
- **Discovery Order** (runner.py:232): Sorted by filename, which is chronological by timestamp prefix
- **Applied Tracking** (runner.py:200-218): Queries `schema_migrations` table for applied versions, compares against discovered files

**Three Newest Migration Files (naming templates):**
1. `20260819_000001_report_injections_deferred_marker.sql`
2. `20260811_000001_reconcile_stuck_tasks_with_terminal_jobitems.sql`
3. `20260810_000001_fix_idle_gate_stuck_task_flags.sql`

### PostgreSQL-Primary + SQLite Compatibility

**Single Dialect-Neutral SQL Per File**

Migrations use **portable ANSI SQL** that works on both databases. Examples from recent migrations:

- **WHERE EXISTS portable subqueries** (20260811_000001:33-39):
  ```sql
  WHERE status IN ('paused', 'pending')
    AND EXISTS (
        SELECT 1 FROM job_queue_items ji
        WHERE ji.job_id = task.work_id
          AND ji.admission_state IN ('done', 'dead')
          AND ji.deleted_at IS NULL
    )
  ```

- **No dialect-specific branching** within migration SQL files

**Dual-Driver Pattern for Backfills** (20260819_000001:26-36, 20260810_000001:24-30):
- Migration .sql files applied by `MigrationRunner` **ONLY when engine dialect is SQLite**
- Existing PostgreSQL databases receive equivalent statements from `EnsembleManager._ensure_postgres_columns()` in manager.py
- Statements kept **byte-identical** across both paths for convergence
- Comment header declares: `This .sql is applied by MigrationRunner ONLY when the engine dialect is SQLite`

**Concrete Example: CREATE TABLE** (20260819_000001:76-91):
```sql
-- Guarded by MigrationRunner's per-statement duplicate-column handler
ALTER TABLE report_injections ADD COLUMN deferred_reason TEXT;
ALTER TABLE report_injections ADD COLUMN recovery_attempted_at TEXT;
ALTER TABLE report_injections ADD COLUMN report_message_id_new VARCHAR(64);
UPDATE report_injections SET report_message_id_new = report_message_id;
ALTER TABLE report_injections DROP COLUMN report_message_id;
ALTER TABLE report_injections RENAME COLUMN report_message_id_new TO report_message_id;
```

Uses idempotent ALTER patterns (ADD COLUMN + NULL check → rename → DROP old) that work on both SQLite and PostgreSQL.

### SQLite Path: No setup() Call on AsyncSqliteSaver

**File: daemon/persistence.py:56-60**

```python
conn = await aiosqlite.connect(str(db_path))
await conn.execute("PRAGMA busy_timeout=5000")
await conn.execute("PRAGMA synchronous=NORMAL")
saver = AsyncSqliteSaver(conn)
return SqliteCheckpointerAdapter(saver)
```

**Findings:**
- **No setup() call** — the caller creates `AsyncSqliteSaver(conn)` directly without invoking `saver.setup()`
- LangGraph's `AsyncSqliteSaver.setup()` would normally create checkpoint tables if they don't exist
- **Implication**: SQLite checkpoint tables (`checkpoints`, `writes`) are either:
  1. Created externally (unlikely based on grep results)
  2. Created lazily by `AsyncSqliteSaver` on first `aput()`/`aget()` call (most likely)
  3. Handled by LangGraph's internal table creation on first use

**No CREATE TABLE statements found** in ensemble's migration files for checkpoint tables (`checkpoints`, `writes`, `checkpoint_blobs`, `checkpoint_writes`), confirming LangGraph manages its own schema.

---

## 2. MESSAGE-CREATION HOOK POINTS

### a. User Message Injection via aupdate_state

**Primary Site: daemon/services/instance_messaging.py:810-822**

Context compaction path writes compacted messages back to checkpoint:
```python
await graph.aupdate_state(
    config,
    {'messages': result.replacement_messages},
    as_node='agent'
)

if result.compacted_at:
    await graph.aupdate_state(
        config,
        {'compacted_at': result.compacted_at},
        as_node='agent'
    )
```

**Other aupdate_state Callers in daemon/** (from grep):
- `daemon/graph.py:3248` — Reactive compaction in agent_node, replacement_messages
- `daemon/graph.py:3250` — Reactive compaction, compacted_at timestamp
- `daemon/graph.py:3248-3250` — Both invoked after ContextLengthExceededError handling

**Message ID Availability:**
- User messages receive auto-generated `message_id` via LangGraph's `add_messages` reducer
- HumanMessage.id is populated automatically (LangChain convention)
- At compaction sites, messages are already objects with existing IDs (dedup relies on this)

**Thread ID / Instance ID Availability:**
- `config` parameter contains `config.configurable.thread_id` which equals `instance_id`
- Available at all aupdate_state call sites via `config['configurable']['thread_id']`

### b. AIMessage/ToolMessage Creation in Graph Flow

**Graph Structure: daemon/graph.py:2336-5668**

- **State Schema** (graph.py:2334-2365): `SessionState(MessagesState)` with `messages` channel
- **Graph Compilation** (graph.py:5668): `graph.compile(checkpointer=checkpointer)` with 10 nodes
- **Node List** (from graph.py ~5650): agent, tools, nudge, optional question_pause_node

**Message Creation Flow:**

1. **Agent Node** (graph.py:2710+): `create_agent_node()` closure
   - LLM invocation generates `AIMessage` with `tool_calls` if applicable
   - AIMessage auto-generates `id` via LangChain (UUID)
   - Returns to graph, persisted via checkpoint after node completion

2. **Tool Execution Node** (implicit in LangGraph):
   - Tools generate `ToolMessage` results
   - ToolMessage auto-generates `id` via LangChain (UUID)
   - ToolMessage.tool_call_id links to parent AIMessage
   - Persisted via checkpoint after tool node completion

**Choke Points for Side-Table INSERT:**

**Post-Agent-Node Hook:**
- Location: `daemon/graph.py:3248-3250` (after `result = await compactor.compact_state(ctx)`)
- Both `message.id` and `thread_id` available via:
  - `result.replacement_messages` contains message objects with `.id` attributes
  - `thread_id` available via `config['configurable']['thread_id']`
- Runs only on reactive compaction (not every turn)

**Post-LLM-Call (AIMessage):**
- No explicit post-LLM hook in current code
- AIMessage created inside `agent_node` closure (graph.py:2710+)
- Message ID available immediately after `response = await llm_with_tools.ainvoke(full_messages, ...)`
- Thread ID available via `config['configurable']['thread_id']`

**Post-Tool-Execution (ToolMessage):**
- No explicit post-tool hook in current code
- ToolMessages generated by LangGraph's tool execution middleware
- Message ID and tool_call_id available after tool returns
- Thread ID available via config

**Current LangGraph Persistence:**
- Messages persisted automatically via checkpoint mechanism at node boundaries
- No explicit hook point for side-table INSERT in current codebase
- Side-table insertion would require adding middleware or post-node hooks

### c. Report Framing

**Report Injection Site:**

From grep for `ReportInjectionSlot` (graph.py:2631, 2658-2669):
- `report_injection_slot` parameter in `create_agent_node()`
- Drains pending child completion reports via `report_injection_slot.drain(instance_id)`
- Each report threaded as a `HumanMessage` into the LLM conversation
- Occurs **before LLM call** in agent node (after user-message injection pull)

**No explicit "report framing middleware"** found in graph.py or instance_messaging.py

**Report as Stored Message:**
- Reports become `HumanMessage` objects with `message_id` (auto-generated)
- Persisted via normal checkpoint mechanism after agent node completes
- No separate "final report" storage mechanism — reports are just user messages

**Message ID / Thread ID Availability at Report Site:**
- Report messages have `id` after HumanMessage construction
- `thread_id` available via `config['configurable']['thread_id']`
- Injection happens inside `agent_node` closure (graph.py:2631, 2658-2669)

---

## 3. MAINTENANCE/PRUNE STRUCTURE

### Retention Prune Triggers/Scheduling

**File: daemon/services/maintenance.py:678-730**

**Trigger Mechanism:**
- **Service:** `MaintenanceService` class (maintenance.py:68-101)
- **Check Interval:** `MAINTENANCE_CHECK_INTERVAL_MINUTES = 15` (constants.py:70)
- **Idle Gate:** Jobs only run when system is idle (no active jobs AND no active LLM requests)
- **Background Loop:** `async def _run_loop()` (implied from service design)

**_prune_per_thread_checkpoints() Method** (maintenance.py:678-730):
- **Trigger:** Called from maintenance job registered in service
- **Method Name:** `_prune_per_thread_checkpoints()`
- **Max Per Thread Config:** `CHECKPOINT_MAX_PER_THREAD = 50` (constants.py:68)
- **Error Handling:** Wrapped in try/except, logs error on failure (maintenance.py:729-730)

**Error Handling Pattern** (maintenance.py:700, 729-730):
```python
try:
    # ... prune logic
except Exception as e:
    logger.error(f"Per-thread checkpoint pruning failed: {e}")
```

### Checkpoint Deletion Methods

**File: daemon/checkpoint_adapter.py:294-376**

#### delete_checkpoints_excluding (adapter.py:294-320)

**Signature:**
```python
async def delete_checkpoints_excluding(
    self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
) -> int:
```

**SQL Run** (adapter.py:305-314):
```sql
DELETE FROM checkpoints
WHERE thread_id = $1 AND checkpoint_ns = $2
AND NOT (checkpoint_id = ANY($3::text[]))
```

- Returns deleted count (int)
- Uses PostgreSQL `ANY($3::text[])` for list parameter

#### delete_writes_excluding (adapter.py:322-348)

**Signature:**
```python
async def delete_writes_excluding(
    self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
) -> int:
```

**SQL Run** (adapter.py:334-343):
```sql
DELETE FROM checkpoint_writes
WHERE thread_id = $1 AND checkpoint_ns = $2
AND NOT (checkpoint_id = ANY($3::text[]))
```

- Note: Targets `checkpoint_writes` table (PostgreSQL), not `writes` (SQLite)
- Same deletion pattern as checkpoints table

#### adelete_thread (adapter.py:350-376)

**Signature:**
```python
async def adelete_thread(self, thread_id: str) -> None:
```

**Deletes All Three Tables** (adapter.py:365-376):
```sql
-- Order: writes/blobs first (no FK to checkpoints), then checkpoints last
DELETE FROM checkpoint_writes WHERE thread_id = $1
DELETE FROM checkpoint_blobs WHERE thread_id = $1
DELETE FROM checkpoints WHERE thread_id = $1
```

**Transaction Wrapping** (adapter.py:363-376):
- All three DELETEs wrapped in `async with conn.transaction()`
- Ensures atomic deletion (no partial state on failure)

### checkpoint_blobs Table Schema

**Schema Inferred from Usage:**

From checkpoint_adapter.py:350-376 and checkpoint_migrator.py:9-10, 40, 266:

**Table: checkpoint_blobs** (PostgreSQL only)
- `thread_id` TEXT — thread identifier
- `channel` TEXT — channel name (non-primitive values stored separately)
- `version` TEXT — checkpoint version
- Additional columns (blob storage format unknown from code, likely `value` JSONB)

**Schema Notes:**
- **SQLite equivalent:** None — checkpoint_blobs has no SQLite counterpart (adapter.py:356: "no SQLite equivalent")
- **Purpose:** Stores non-primitive channel values (large binary, complex objects)
- **LangGraph responsibility:** Managed by `AsyncPostgresSaver`, not by ensemble migrations

**checkpoint_writes Schema** (inferred from adapter.py:322-348, maintenance.py:692):
- `thread_id` TEXT
- `checkpoint_ns` TEXT
- `checkpoint_id` TEXT
- `task_id` TEXT
- `idx` INTEGER
- `...` (other write data)

**checkpoints Schema** (inferred from adapter.py:305-314, maintenance.py:691):
- `thread_id` TEXT
- `checkpoint_ns` TEXT
- `checkpoint_id` TEXT — UUID string, lexicographic ordering = chronological
- `...` (checkpoint JSONB data, channel values, metadata)

**Reference-Aware Deletion Requirements:**
For reference-aware blob pruning, need: `(thread_id, checkpoint_ns, checkpoint_id)` to identify which blobs belong to which checkpoints.

### CHECKPOINT_MAX_PER_THREAD Value/Config

**File: daemon/constants.py:68**

```python
CHECKPOINT_MAX_PER_THREAD: int = 50  # Max checkpoints per thread (preserves parent chain)
```

**Config Mechanism:**
- Hard-coded constant in `daemon/constants.py`
- Used in maintenance.py:701 (`max_per_thread = CHECKPOINT_MAX_PER_THREAD`)
- Passed to `checkpointer.find_excess_checkpoint_groups(max_per_thread)` (maintenance.py:705)

**No runtime configuration** — value is compile-time constant.

---

## 4. LOGGING/INSTRUMENTATION CONVENTIONS

### Structured-Logging Pattern

**Logger Naming:**
- Module-level loggers: `logger = logging.getLogger(__name__)` (seen in all daemon modules)
- Example: maintenance.py:50, checkpoint_adapter.py:27, graph.py:21 (implied)

**Logging Format:**
- **Plain text logging** — no JSON structured logging detected
- Example patterns:
  - `logger.info(f"[Compaction] instance={instance_id[:8]}...")` (instance_messaging.py:826)
  - `logger.info(f"Found {len(excess_pairs)} thread/namespace pairs...")` (maintenance.py:713-715)
  - `logger.error(f"Per-thread checkpoint pruning failed: {e}")` (maintenance.py:730)

**Prefix Conventions:**
- Bracketed prefixes for context: `[Compaction]`, `[RESUME]`, `[LLM]`
- Instance short IDs: `instance_id[:8]` truncation for brevity

### Timing/Metrics Patterns

**time.perf_counter Usage** (grep results):
- `daemon/migrations/runner.py:302` — `start_time = time.perf_counter()`
- `daemon/migrations/runner.py:396` — `execution_time_ms = int((time.perf_counter() - start_time) * 1000)`
- `daemon/migrations/runner.py:436` — migration rollback timing
- `daemon/migrations/runner.py:454` — rollback execution time logging
- `daemon/services/blueprint_matcher.py:183` — `t0 = time.perf_counter()`
- `daemon/services/blueprint_matcher.py:240` — `latency_ms = (time.perf_counter() - t0) * 1000.0`

**Timing Decorators:**
- No generic timing decorator helpers found
- Timing is ad-hoc per use-case (migrations, blueprint matching)

### Current Instrumentation on GET /messages Path

**Confirmed: NO existing timing/metrics on GET /messages path**

- `daemon/persistence.py:254-346 get_instance_messages()` — No timing instrumentation
- `daemon/manager.py:9314 get_messages()` — No timing instrumentation
- `daemon/routers/instances.py:1421 get_messages endpoint` — No timing instrumentation

**No saver op timing anywhere** in checkpoint operations:
- `persistence.py:312` — `state = await saver.aget(config)` — untimed
- `persistence.py:326-333` — `async for checkpoint_tuple in saver.alist(config, limit=1000)` — untimed
- No performance counters or metrics collection

**Implication:** Phase 1 would be adding the first checkpoint performance instrumentation.

---

## 5. QUICK VERIFICATIONS

### persistence.py:254-346 get_instance_messages Structure

**File: daemon/persistence.py:254-346**

**Function Signature** (persistence.py:254-259):
```python
async def get_instance_messages(
    checkpointer: Any,
    instance_id: str,
    manager: Any | None = None,
) -> list[dict[str, Any]]:
```

**Structure Confirmed:**
1. **aget at :312** — `state = await saver.aget(config)`
2. **alist loop at :326-333** — `async for checkpoint_tuple in saver.alist(config, limit=1000)`
3. **msg_timestamps reconstruction at :339-346** — Loop through checkpoints_data, build `msg_timestamps[msg_id] = ts` map

**Final Response Shape:**
From routers/instances.py:1428-1476 (response model documentation), API returns:
- `list[dict]` of serialized messages in chronological order
- Each dict contains:
  - `role`: "human", "ai", "tool", "system", etc.
  - `content`: message content (text, images, structured)
  - `thinking`: AI thinking content (if applicable)
  - `tool_calls`: tool call list (for AIMessages)
  - `message_id`: message identifier (auto-generated or synthetic)
  - `is_synthetic`: boolean (for synthetic system/context messages)
  - `context_kind`: string (for context messages: "project", "source", etc.)
  - `created_at`: ISO-8601 timestamp (reconstructed from checkpoint ts)

### manager.py:9314 get_messages Delegation

**File: daemon/manager.py:9314-9326**

**Signature:**
```python
async def get_messages(self, instance_id: str) -> list[dict]:
    """Get message history for an instance.

    Args:
        instance_id: The ID of the instance.

    Returns:
        List of message dictionaries from LangGraph checkpoints.

    Raises:
        KeyError: If instance is not found.
    """
    return await self._messaging_service.get_messages(instance_id)
```

**Simple delegation** to `_messaging_service.get_messages(instance_id)`

### routers/instances.py:1421 Messages Endpoint Response Model

**File: daemon/routers/instances.py:1421-1476**

**Endpoint Definition:**
```python
@router.get("/{instance_id}/messages")
async def get_messages(
    instance_id: str,
    request: Request,
) -> list[dict]:
```

**Response Shape Documentation** (instances.py:1428-1476):
- Returns `list[dict]` of serialized messages in chronological order
- **Three segments** (optional synthetic messages first):
  1. **Synthetic system message** (`role="system"`, `is_synthetic=True`, `message_id="synthetic-system-<instance_id>"`)
  2. **Synthetic context messages** (per-turn context, `is_synthetic=True`, `context_kind`)
  3. **Persistent messages** (actual conversation history)

**Each message dict contains:**
- `role`: message type
- `content`: message content (may be text, list of content blocks)
- `thinking`: AI reasoning (for AIMessages with thinking)
- `tool_calls`: list of tool calls (for AIMessages)
- `tool_call_id`: linking ID (for ToolMessages)
- `message_id`: message identifier
- `is_synthetic`: boolean flag
- `context_kind`: string (for context messages)
- `created_at`: ISO-8601 timestamp string

**Phase 1 Compatibility:** Side-table must preserve this exact response shape (no changes needed, synthetic system/context messages are added by GET /messages endpoint logic, not checkpoint).

### checkpoint_migrator.py alist Usage (Offline-Only Confirmed)

**File: daemon/migrations/checkpoint_migrator.py:36-40**

**Usage Pattern:**
```python
- SQLite stores checkpoints as msgpack BLOBs in 2 tables
- PostgreSQL stores as JSONB in 4 tables (checkpoints, checkpoint_writes,
  checkpoint_blobs, checkpoint_migrations)
- The aput()/aput_writes() API handles the serialization conversion automatically
```

**alist() for Export** (checkpoint_migrator.py:36):
- Uses `AsyncSqliteSaver.alist()` to read checkpoints from SQLite
- Each checkpoint tuple passed to `AsyncPostgresSaver.aput()` for writing
- **Offline-only** — migrator is invoked via manual tool or script, not during runtime
- No alist() usage in production code paths (confirmed by grep: only persistence.py:326 uses alist in production)

---

## SUMMARY

This research provides the foundation for implementing a checkpoint-persistence performance plan:

1. **Migrations** use portable SQL with dual-driver pattern (SQLite vs PostgreSQL). New side-table migration should follow `YYYYMMDD_HHMMSS_name.sql` convention with ANSI SQL, declared as SQLite-only in header.

2. **Message creation hooks** are limited: aupdate_state calls at compaction sites (instance_messaging.py:810-822, graph.py:3248-3250), but no post-node hooks for AIMessage/ToolMessage creation. Side-table INSERT would require new middleware or post-node hooks in graph.py agent_node.

3. **Maintenance/prune** runs every 15 minutes via MaintenanceService idle gate, deletes via checkpoint_adapter methods using checkpoint_id exclusion pattern. checkpoint_blobs has no SQLite equivalent; pruning needs thread_id + checkpoint_id references.

4. **Instrumentation** uses plain text logging with bracketed prefixes, no JSON logging. No existing timing on GET /messages path or saver operations. First checkpoint instrumentation opportunity.

5. **GET /messages endpoint** returns `list[dict]` with role/content/thinking/tool_calls/message_id/is_synthetic/context_kind/created_at fields. Side-table must preserve this shape. alist() is offline-only (migrator), not production.