# Phase 1 — Tool-Usage Metrics: the measurement slice (BE ledger + tap + door)

Lane: **tool-usage-metrics-p1** (commission ENSEMBLE-37 / TokenEconomizer adoption — Phase 1 measurement slice)
Phase owner: plan-worker (BE phase)
Status: Draft for the planner's review
Depends on: **none** (this is the foundational BE phase; Phase 2 metrics family, Phase 3 digest/seeder, Phase 4 FE tab all CONSUME it).
Worktree (READ-ONLY source of truth for every anchor below): `/home/nea/ensemble-src-wt-tool-metrics-p1` @ `0b5624dc9` (branch `feature/tool-usage-metrics-p1`).

---

## 0. Dispatch mode

**Mode 1 — Greenfield.** The directory `.agents/shared/planning/tool-usage-metrics-p1/` exists but contains only `phase4-plan.md` (a sibling phase's artifact); no committed `phase1-plan.md` or `decisions.md` is being revised. This file is the first Phase-1 artifact. No committed plan set is being revised.

---

## 1. Objective

Ship the **measurement substrate** for tool-usage frequency so layer-2 Tool-Architecture candidacy (ranking key = schema token weight × call frequency; zero/near-zero usage = strongest candidates) becomes evidence-based rather than anecdotal:

1. A **durable ledger** — `tool_usage_daily` (one table, aggregated counters per `(day, project_id, tool_name, agent_id)`), NAMES ONLY (no argument/argv/content columns, ever).
2. A **non-load-bearing tap** at the tools node that observes tool NAME + instance identity per executed tool_call and fans out to registered in-process sinks (ledger queue + the future Phase-2 metrics family counter).
3. An **off-loop write path** (bounded queue + batch flusher) so DB writes never block the event loop.
4. A **read-only domain door** `GET /api/tool-usage/{summary,daily}` exposing the ledger to the FE tab (Phase 4) with the auth posture replicated verbatim from `nest_reports.py`.

**Phase 1 owns measurement only.** It does NOT compute the digest (Phase 3), does NOT wire the metrics family (Phase 2), does NOT touch the FE (Phase 4), and does NOT ship a scheduler/seeder (Phase 3).

---

## 2. Scope

### In scope (daemon/** only)

- `daemon/repositories/tool_usage/` (NEW: `models.py`, `repository.py`) — the SQLModel table + sync dialect-aware upsert repository.
- `daemon/services/tool_usage_observation.py` (NEW) — the tools-node observation wrapper (the tap), the bounded queue + batch flusher, the sink registry, the kill-switch gate.
- `daemon/graph.py` — **ONE hunk region only**: the tools-node observation wrapper wiring in the `add_node("tools", ...)` chain (~14464–14483). No other graph.py edits.
- `daemon/manager.py` — **TWO hunks**: (1) model registration before `create_all` (~1006–1020 region); (2) flusher/queue startup + collection kill-switch wiring in the boot sequence (~1145–1148 region). Phase 3 will add a THIRD disjoint hunk (seeder near 1144–1166) — enumerate ours precisely so the cross-writer sweep proves disjointness.
- `daemon/migrations/versions/<new>.sql` (NEW) — SQLite-executable, idempotent, additive-only.
- `daemon/routers/tool_usage.py` (NEW) — the domain door + the one registration line in `daemon/routers/__init__.py`.
- `daemon/services/settings_service.py` — ONE entry in `MANAGED_KEYS` for the collection kill-switch (additive; no edit to existing keys).
- `tests/unit/...` — repo, tap wrapper, flusher, kill-switch, router, migration.

### Out of scope (REJECT-grade if touched)

- **Any frontend file** — Phase 4 owns the tab.
- **Any metrics-wiring file** (`daemon/services/metrics_query/wiring/*`) — Phase 2 owns the F24 family.
- **Any scheduler / seeder / digest file** — Phase 3 owns `tool_usage_digest` + the boot-time idempotent seeder.
- **Any existing table/column** — additive-only migration.
- **`ToolThrottleSlot`** — do not pollute it (it reads `last ToolMessage.name` only for throttle machinery, graph.py:9567–9580).
- **`message_tap_slot`** — ids-only; cannot observe tool executions.

---

## 3. FROZEN CONTRACTS (interfaces / invariants / bounds — NEVER node counts)

### 3.1 Ledger schema (FROZEN — Dispatcher ruling A)

Table `tool_usage_daily`:

| Column | Type | Null | Notes |
|---|---|---|---|
| `day` | TEXT (ISO date `YYYY-MM-DD`, UTC) | NOT NULL | PK part |
| `project_id` | TEXT | NOT NULL | PK part; sentinel `""` (empty string) for un-attributable calls |
| `tool_name` | TEXT | NOT NULL | PK part; NAMES ONLY |
| `agent_id` | TEXT | NOT NULL | PK part; sentinel `'unknown'` for un-attributable calls |
| `call_count` | BIGINT (INTEGER on SQLite) | NOT NULL, default 0 | aggregated counter |
| `updated_at` | TEXT (ISO-8601) | NULL | last flush timestamp |

- **PK**: `(day, project_id, tool_name, agent_id)` (composite).
- **NAMES ONLY is a FROZEN invariant**: no `args`, `argv`, `content`, `tool_call_id`, `instance_id`, or `session_id` column, ever. The signature itself (§3.2) is the guarantee.
- **Aggregated counters only** — per-call rows are REJECTED (volume + content exposure).
- **`agent_id` is NOT NULL** with sentinel `'unknown'` for un-attributable calls (house-consistent with `instance_id` fallback at graph.py:8871).
- **Project attribution**: `project_id` may be `None` at run time (late binding). For the ledger, un-attributable project ⇒ sentinel `""` (empty string) — unambiguous against real project ids (UUIDs), sorts first, and is filterable as `project_id=` on the door. **Flagged as assumption A1** (phase1-plan.md §8); the architect pass may rename to a named sentinel (single-point change, recorded as a superseding decision entry). See decisions.md D11.
- **`call_count` is BIGINT** — PG `BIGINT`, SQLite `INTEGER` (SQLite has no BIGINT affinity but stores as int64).
- **`updated_at`** is optional (last flush timestamp), ISO-8601 UTC.

### 3.1.1 Indexes

- PK index on `(day, project_id, tool_name, agent_id)` (composite, created by the PK).
- Secondary index `idx_tool_usage_daily_project_day` on `(project_id, day)` — supports the door's project+days filter and the digest rollup (Phase 3).
- Secondary index `idx_tool_usage_daily_tool_day` on `(tool_name, day)` — supports the per-tool daily series.

Index names must be byte-identical between the `.sql` file and the model's `__table_args__` (3-site lockstep precedent, memoir).

### 3.2 Tap (C-observer) sink signature (FROZEN contract)

The observation wrapper fans out to registered in-process sinks. The sink signature (frozen — Phase 2 consumes with ZERO Phase-1 edits):

```python
SinkCallable = Callable[
    [str, str | None, str, str, bool],
    None,
]
# positional: (tool_name, tool_category, agent_id, project_id, ok)
```

- **`tool_name`**: the executed tool's name (from the AI message's `tool_calls[].name`). NAMES ONLY.
- **`tool_category`**: resolved by the tap at emission time from a tool-name → category lookup (the house "existing closed vocabulary" — see §3.5). Sinks do NOT resolve category. **Rationale**: centralizing resolution in the tap guarantees no sink can accidentally receive or re-derive argument data; the tap is the single point that touches tool identity.
- **`agent_id`**: resolved from the instance row (`instance.agent_id`), sentinel `'unknown'` on lookup failure / no instance.
- **`project_id`**: resolved via the ContextSlot pattern (`instance_metadata["project_id"]`), sentinel `""` on failure / no project.
- **`ok`**: best-effort success flag — `True` unless the corresponding returned ToolMessage carries an error indicator. Default `True` on ambiguity. The tap inspects the ToolNode result to set it.
- **No argument payload is possible by construction** — the signature has no args slot. This is the NAMES-ONLY guarantee at the type level.

Sinks are registered at process startup (the flusher registers the ledger sink; Phase 2 registers the family counter). Registration is idempotent and thread-safe. A sink that raises is isolated (logged, never propagates) — the tap is non-load-bearing.

### 3.3 Off-loop write contract (FROZEN)

- The tap's hot path does **no** DB I/O. It resolves identity (instance_repository.get — already sync; wrap in `asyncio.to_thread`? The tap runs inside the async tools node; instance_repository is sync. Use `asyncio.to_thread` for the get, OR rely on the sync call being fast. Plan: wrap in `asyncio.to_thread` to honor the off-loop rule) and enqueues an event into a **bounded** `asyncio.Queue` (capacity: configurable, default 10 000; overflow drops the event, increments a drop counter, logs at WARNING, never blocks).
- A single background **batch flusher** task drains the queue: batches by **size** (default 500 events) OR **interval** (default 5 s), whichever comes first. Each batch is one upsert transaction (`INSERT ... ON CONFLICT ... DO UPDATE SET call_count = call_count + excluded delta`). The flusher runs on the event loop but DB work is dispatched via `asyncio.to_thread(repo.upsert_batch, ...)`.
- **Graceful shutdown drain**: on daemon shutdown, the flusher drains remaining events (best-effort, bounded by a timeout, default 10 s) before exit. Undrained events are dropped with a WARNING log + metric.
- **Failure tolerance**: a ledger/DB failure NEVER fails a tool call or a turn. The flusher logs and continues; the queue keeps accepting. The tap swallows all exceptions (non-load-bearing, like message_tap_slot).
- **Kill-switch**: `limits.tool_usage_collection_enabled` (managed key, default ON). When OFF: tap does not enqueue, flusher idles, queue is drained-and-discarded. Evaluated at call time (not cached) — D0.5/AM-27 precedent. Fail-closed: key absent/misconfigured ⇒ collection OFF.

### 3.4 Door contract (FROZEN — Phase 4 consumes)

New router `daemon/routers/tool_usage.py`, `APIRouter(prefix="/tool-usage", tags=["tool-usage"])`, registered in `daemon/routers/__init__.py` with one line.

Endpoints:

- `GET /api/tool-usage/summary?project_id=<optional, omit=all-projects>&days=7` → `{ generated_at, rows: [{ tool_name, tool_category, calls_24h, calls_7d, calls_total, agents: [{ agent_id, calls_7d }] }] }`.
  - `days` default 7, clamped [1, 90].
  - `project_id` omitted ⇒ aggregate across all projects.
  - `calls_24h` / `calls_7d` / `calls_total` are sums over the ledger rows in the respective windows (24h = today+; 7d = last 7 UTC days; total = all days).
  - Rows returned unsorted (FE sorts).
  - `generated_at` = server UTC ISO-8601 (never client clock).
- `GET /api/tool-usage/daily?project_id=&tool_name=&days=` → `{ series: [{ day, calls }] }`.
  - `tool_name` required; unknown tool (no ledger rows ever) ⇒ 404 `unknown_tool`.
  - `days` default 30, clamped [1, 90].
  - One point per UTC day in the window, zero-filled.

Auth posture: **replicates `_require_auth_on` verbatim** (nest_reports.py:94–114): 503 `AUTH_NOT_CONFIGURED` when auth is configured off; scopes `maintenance:read | metrics:read`. 403 `SCOPE_INSUFFICIENT` on scope miss. Read-only — no writes through this door.

Error vocabulary (closed): `AUTH_NOT_CONFIGURED` (503), `SCOPE_INSUFFICIENT` (403), `bad_request` (400), `unknown_tool` (404), `tool_usage_unavailable` (503, transient substrate failure).

### 3.5 Tool category vocabulary (house rule)

The tap resolves `tool_category` from the tool name using the **existing closed vocabulary** reused as-is (no `VOCABULARY_VERSION` bump unless a new value is needed — and none is). The lookup is a static map `TOOL_NAME → category` populated from the tool registry's declared category (the same source the FE/agent tool catalog uses). If a tool name is not in the map, category = `None` (null in JSON). Phase 2's F24 family labels use `tool_category` from this same vocabulary (no new values ⇒ no version bump, per correction #1: `VOCABULARY_VERSION` is 11 at `daemon/checkpoint_metrics.py:290`).

### 3.6 Managed key (collection kill-switch)

Add to `MANAGED_KEYS` in `daemon/services/settings_service.py`:

```
"limits.tool_usage_collection_enabled": ManagedKeySpec(
    key="limits.tool_usage_collection_enabled",
    config_path="limits.tool_usage_collection_enabled",
    env_var="LIMITS_TOOL_USAGE_COLLECTION_ENABLED",
    value_type="bool",
    default=True,                       # this phase IS the measurement slice
    requires_restart=False,
    sensitive=False,
    description="Tool-usage collection kill-switch ...",
)
```

Default ON. Fail-closed: key absent/misconfigured ⇒ collection OFF. Independent from `limits.metrics_enabled` / `ENSEMBLE_METRICS_ENABLED` (Phase 2 stacks under those).

---

## 4. Tasks

Each task lists file-level claims + per-task verification. Single-ownership invariant: every file edit is claimed by exactly ONE task below.

### T1 — Repository + model (NEW files)

**Files**: `daemon/repositories/tool_usage/__init__.py`, `daemon/repositories/tool_usage/models.py`, `daemon/repositories/tool_usage/repository.py`.

- `models.py`: `ToolUsageDaily` SQLModel `table=True`, `__tablename__ = "tool_usage_daily"`, columns per §3.1, `__table_args__` with the two secondary indexes named per §3.1.1. All string columns `TEXT` (no VARCHAR). `call_count` uses `sa_column=Column("call_count", BigInteger, ...)`. Follow the memoir/POK precedent (brand-new table ⇒ no `_ensure_postgres_columns` mirror).
- `repository.py`: `ToolUsageRepository(engine)` sync, with `upsert_batch(deltas: list[ToolUsageDelta])` (dialect-aware `INSERT ... ON CONFLICT (day, project_id, tool_name, agent_id) DO UPDATE SET call_count = call_count + excluded.call_count, updated_at = excluded.updated_at`), `query_summary(project_id, days)`, `query_daily(project_id, tool_name, days)`. Empty input short-circuits to 0.
- `__init__.py`: export `ToolUsageDaily`, `ToolUsageRepository`, `ToolUsageDelta`.

**Verification**: unit tests `tests/unit/repositories/test_tool_usage_repository.py` — upsert aggregation (two increments on the same PK sum), PK conflict paths (re-upsert keeps first `day`/identity, increments count), sentinel-agent handling (`agent_id='unknown'` round-trips), empty-batch no-op, SQLite + PG dialect branches.

### T2 — Migration `.sql` (NEW file)

**File**: `daemon/migrations/versions/<YYYYMMDD_HHMMSS>_create_tool_usage_daily.sql`.

- Format follows `20261105_102821_create_memoir_tables.sql`: header comments, `CREATE TABLE IF NOT EXISTS tool_usage_daily (...)`, the two secondary indexes `CREATE INDEX IF NOT EXISTS`, all string columns TEXT, `call_count INTEGER NOT NULL DEFAULT 0` (SQLite; PG uses BIGINT via the model). Idempotent.
- No `PRECONDITION` header (no version-sensitive DDL).
- Filename timestamp: use the next free slot after `20261119_000001` (e.g. `20261121_020000_create_tool_usage_daily.sql`).

**Verification**: migration test parses + applies on a throwaway SQLite DB; additive-only assertion (no DROP/ALTER on existing tables); 3-site index lockstep (names + columns byte-identical to model `__table_args__`).

### T3 — Manager model registration (hunk 1)

**File**: `daemon/manager.py`, in the late-import block before `SQLModel.metadata.create_all` (~1006–1020 region), append:

```python
from .repositories.tool_usage.models import (  # noqa: F401  # tool-usage P1: register before create_all
    ToolUsageDaily,
)
```

Brand-new table ⇒ NO `_ensure_postgres_columns` mirror. Follow the POK/room precedent comment style.

**Verification**: grep confirms the import lands before `create_all`; boot smoke materializes the table on SQLite.

### T4 — Observation wrapper + queue + flusher (NEW file)

**File**: `daemon/services/tool_usage_observation.py`.

Components:
- `ToolUsageEvent` (dataclass): `tool_name`, `tool_category`, `agent_id`, `project_id`, `ok`, `day` (UTC date, set at enqueue).
- `ToolUsageSink` type alias = `Callable[[str, str | None, str, str, bool], None]`.
- `ToolUsageTap` (or `ToolUsageObserver`): wraps an inner node callable. In `__call__(state, config)`:
  1. Extract `instance_id` from `config.configurable.thread_id`.
  2. Resolve `project_id` + `agent_id` via `instance_repository` (ContextSlot pattern), with sentinels.
  3. Inspect `state["messages"]` for the last AI message with `tool_calls`; for each `tc`, resolve category via the static map.
  4. Delegate to `inner(state, config)`; capture result.
  5. Determine `ok` per tool_call from the result's ToolMessages.
  6. If collection kill-switch ON: enqueue one event per tool_call (best-effort, `put_nowait`, drop on full).
  7. Fan out to registered sinks (isolated, never raises).
  8. Return inner's result unchanged.
  - All exceptions swallowed (non-load-bearing).
- `ToolUsageQueue`: bounded `asyncio.Queue`, `enqueue(event)` (sync, `put_nowait`, overflow drop + counter), `drain()` async.
- `ToolUsageFlusher`: background task; batches by size/interval; calls `asyncio.to_thread(repo.upsert_batch, batch)`; graceful shutdown drain with timeout; exposes drop counter + flush error counter.
- `register_sink(sink)`, `unregister_sink(sink)` — module-level registry, thread-safe.
- `is_collection_enabled()` — reads the managed key at call time; fail-closed.

**Verification**: tap wrapper tests assert (a) tool name + identity captured, (b) the sink signature carries NO argument payload (NAMES-ONLY pin — the test inspects the call args), (c) tap swallows exceptions, (d) queue never awaits DB (the enqueue path is sync). Flusher tests: batching, drain-on-shutdown, drop-counter, off-loop semantics.

### T5 — Graph wiring (hunk 1, only hunk)

**File**: `daemon/graph.py`, ~14464–14483 region. Change:

```python
graph.add_node(
    "tools",
    create_compact_note_tools_node(
        wrapped_tools_node(tools, registry=LONG_TOOL_REGISTRY),
        ...
    ),
)
```

to wrap the inner with the observation wrapper:

```python
from daemon.services.tool_usage_observation import wrap_tools_node  # or factory
...
graph.add_node(
    "tools",
    create_compact_note_tools_node(
        wrap_tools_node(
            wrapped_tools_node(tools, registry=LONG_TOOL_REGISTRY),
            instance_repository=instance_repository,  # already in scope
            ...
        ),
        ...
    ),
)
```

The exact factory signature is pinned in §3.2/§4 T4; the implementer threads `instance_repository` and the queue/flusher handle the same way `manager` is threaded at 14446/14476. **No other graph.py edits.**

**Verification**: AST/grep gate — only the `add_node("tools", ...)` line changes; the diff hunk is confined to ~14464–14483.

### T6 — Manager flusher startup (hunk 2)

**File**: `daemon/manager.py`, boot sequence ~1145–1148 region (after the memoir persona seed at 1164–1166? No — the flusher should start alongside other background tasks). Pick the site: immediately after `SQLModel.metadata.create_all` + migration runner + `_ensure_postgres_columns`, and before the boot returns, start the flusher task and register the ledger sink. Also wire the collection kill-switch read at startup (log its resolved state once, D0.7/AM-26 precedent).

Enumerate the hunk precisely:
- Hunk 1 (T3): model import before `create_all` (~1006–1020).
- Hunk 2 (T6): flusher startup + sink registration + kill-switch boot log, in the block after `_ensure_postgres_columns` (~1145–1148 region, before the memoir persona seed at 1164–1166). **Phase 3's seeder hunk will land at 1164–1166** — ours is disjoint and precedes it.

**Verification**: boot smoke starts the flusher; shutdown drains; kill-switch OFF ⇒ flusher idles.

### T7 — Collection kill-switch managed key

**File**: `daemon/services/settings_service.py`, `MANAGED_KEYS` dict — add the entry per §3.6. Also update the managed-key count comment if present (house rule: keep the count honest).

**Verification**: settings tests include the new key; fail-closed resolution test.

### T8 — Domain door (NEW file + registration line)

**File**: `daemon/routers/tool_usage.py` (NEW) + one line in `daemon/routers/__init__.py`.

- Router, endpoints, auth posture per §3.4.
- Repository access via `request.app.state` manager (nest_reports precedent).
- Error vocabulary per §3.4.

**Registration line** in `__init__.py`:
```python
from .tool_usage import router as tool_usage_router  # /api/tool-usage (tool-usage-metrics-p1: ledger read door)
```
and append `"tool_usage_router"` to `__all__`.

**Verification**: router tests — summary/daily shapes vs the frozen contract, auth posture (503 OFF, 403 scope), 404 unknown_tool, clamping, empty-window zero-fill.

### T9 — Tests (file list)

- `tests/unit/repositories/test_tool_usage_repository.py`
- `tests/unit/services/test_tool_usage_observation.py` (tap wrapper + queue + flusher + kill-switch)
- `tests/unit/routers/test_tool_usage_router.py`
- `tests/unit/migrations/test_tool_usage_migration.py` (parse + apply + additive-only + index lockstep)

All under `tests/unit/...` (no live contact).

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| AC1 | `tool_usage_daily` table materializes on SQLite boot (migration applies) and on PG boot (create_all). |
| AC2 | Two increments on the same `(day, project, tool, agent)` PK sum into `call_count` (upsert aggregation). |
| AC3 | The tap captures tool NAME + instance identity per executed tool_call; the sink signature carries NO argument payload (NAMES-ONLY pin, asserted by test). |
| AC4 | The enqueue path never awaits DB (off-loop); flusher batches + drains on shutdown; overflow increments drop counter. |
| AC5 | Kill-switch ON/OFF/fail-closed behave per §3.3. |
| AC6 | Door returns the frozen shapes; auth posture matches nest_reports verbatim; 404 on unknown tool. |
| AC7 | Migration is additive-only (no DROP/ALTER on existing tables). |
| AC8 | No frontend, metrics-wiring, scheduler, or seeder file is touched. |

---

## 6. Risks

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | `instance_repository.get` on the hot path adds latency | Medium | Wrap in `asyncio.to_thread`; cache nothing (late binding); failures swallowed. |
| R2 | Ok-determination from ToolNode results is heuristic | Medium | Default True on ambiguity; Phase 2's family counter treats `ok` as best-effort; document the rule. |
| R3 | Bounded queue overflow under burst drops real counts | Medium | Capacity default 10 000; drop counter + WARNING log; size/interval triggers keep pace under normal load. |
| R4 | Composite PK sentinel collisions (empty project id) | Low | Project ids are UUIDs; `""` is unambiguous. Flagged as A1 for architect override. |
| R5 | Cross-writer hunk collision with Phase 3 seeder | High | Hunk 2 precedes the 1164–1166 seed block; enumerate both precisely in the cross-writer sweep. |
| R6 | Tool category map staleness when new tools ship | Low | Map sourced from the tool registry's declared category at startup; unmapped ⇒ `None`, never a crash. |

---

## 7. Success criteria (phase-exit gates)

- All T9 tests GREEN.
- Boot smoke (SQLite + PG) materializes `tool_usage_daily` with the composite PK + two secondary indexes.
- `git diff --stat` shows ONLY files under `daemon/repositories/tool_usage/`, `daemon/services/tool_usage_observation.py`, `daemon/graph.py` (one hunk), `daemon/manager.py` (two hunks), `daemon/migrations/versions/<new>.sql`, `daemon/routers/tool_usage.py`, `daemon/routers/__init__.py`, `daemon/services/settings_service.py`, `tests/unit/...` (tool-usage packs). No frontend, no metrics-wiring, no scheduler/seeder.
- Zero live contact before staging (house rule).

---

## 8. Assumptions (explicit)

- A1: Un-attributable `project_id` ⇒ sentinel `""` (empty string). Architect pass may rename to a named sentinel.
- A2: `ok` default `True` on ambiguity; Phase 2 treats it as best-effort.
- A3: Tool category resolved by the tap from a static map sourced from the tool registry; unmapped ⇒ `None`.
- A4: F24 is the next family number (Phase 2 verifies against the registrar).
- A5: The flusher's DB handle is the shared sync engine from `daemon/repositories/factory.py` (same as message_metadata).
- A6: `days` clamping [1, 90] on both endpoints.

---

## 9. Open questions for the orchestrator

- OQ1: If the architect pass changes the sink signature (e.g. adds a timestamp), Phase 2's family counter must be updated in lockstep — flag as a contract-break.
- OQ2: If the tap's `instance_repository.get` proves too slow on the hot path, the fallback is to read `agent_id`/`project_id` from a closure-captured cache populated at instance creation — but that misses late binding (ContextSlot precedent warns against it). Escalate before changing.
- OQ3: If a named sentinel for un-attributable project is preferred over `""`, rename in one place (§3.1) before implementation.

---

## Evidence citations (file:line)

- `daemon/graph.py:10799-10808` message_tap_slot (ids-only, tools_node explicitly OOS per `daemon/services/message_tap.py:24-25`).
- `daemon/graph.py:10134-10141` compaction_tap_slot; `:8174/:8188/:9771` precall_compaction_tap_slot.
- `daemon/graph.py:14464-14483` `add_node("tools", create_compact_note_tools_node(wrapped_tools_node(tools, registry=LONG_TOOL_REGISTRY), ...))`.
- `daemon/graph.py:9042` ContextSlot `resolve_project_id` (project resolution precedent).
- `daemon/graph.py:9567-9580` ToolThrottleSlot (do not pollute).
- `daemon/graph.py:8871` instance_id fallback `'unknown'`.
- `daemon/services/long_tool_nudge.py:752-900` `wrapped_tools_node` (the blessed observation-seam precedent).
- `daemon/services/message_tap.py:1-40` (tools_node OOS note).
- `daemon/manager.py:1006-1020` (model registration before create_all).
- `daemon/manager.py:1072` `create_all`; `:1075-1077` migration runner (SQLite-only).
- `daemon/manager.py:1145-1148` boot-order comment; `:1164-1166` memoir persona seed.
- `daemon/routers/nest_reports.py:57` (prefix); `:94-114` `_require_auth_on` (auth posture).
- `daemon/routers/__init__.py:36` (registration).
- `daemon/services/settings_service.py:253+` MANAGED_KEYS; `:518-530` metrics kill-switch precedent.
- `daemon/checkpoint_metrics.py:290` VOCABULARY_VERSION = 11.
- `daemon/repositories/message_metadata/repository.py:77-125` (upsert dialect pattern).
- `daemon/migrations/versions/20261105_102821_create_memoir_tables.sql` (migration format).
- `daemon/repositories/instance/models.py:53` `agent_id` (per-agent dimension source).
- `daemon/services/metrics_query/wiring/w4.py` (F23 registrar precedent; F24 = next).
