# Phase 1 — Tool-Usage Metrics: the measurement slice (BE ledger + tap + door)

Lane: **tool-usage-metrics-p1** (commission ENSEMBLE-37 / TokenEconomizer adoption — Phase 1 measurement slice)
Phase owner: plan-worker (BE phase)
Status: Draft for the planner's review — amended in place by the architect pass @ fd254b335 (see architecture-recommendation.md; governing ledger entries D12–D22 in decisions.md)
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
- `daemon/manager.py` — **TWO hunks**: (1) model registration before `create_all` (~1006–1020 region); (2) flusher/queue startup + collection kill-switch wiring in the boot sequence (~1145–1148 region). **Amended by architect pass @ fd254b335 — D15 (A-5c): hunk 2 starts the flusher via a respawn-on-death outer task and does NOT ride `manager._background_tasks` (cancel-without-drain).** Phase 3 will add a THIRD disjoint hunk (seeder near 1144–1166) — enumerate ours precisely so the cross-writer sweep proves disjointness.
- `daemon/migrations/versions/<new>.sql` (NEW) — SQLite-executable, idempotent, additive-only.
- `daemon/routers/tool_usage.py` (NEW) — the domain door + the router registration edits in `daemon/routers/__init__.py` (one import line + one `__all__` entry — two edits; `routers/__init__.py:36`/`:73` precedent). (approver fix B5-note, iteration-001)
- `daemon/services/settings_service.py` — **THREE entries in `MANAGED_KEYS`** (amended by architect pass @ fd254b335 — D17 (A-7), extended by review fix U3): (1) the collection kill-switch `limits.tool_usage_collection_enabled`; (2) the F24 family key `limits.metrics_tool_usage_enabled` (`config_path=None`, DB/ENV-ONLY); (3) the D-5 seeder opt-out key `limits.tool_usage_digest_seeding_enabled` (`config_path=None`, DB/ENV-ONLY, default ON). Additive; no edit to existing keys.
- `daemon/config.py` — **ONE additive field** `LimitsConfig.tool_usage_collection_enabled` so the collection key's dotted `config_path` has a backing field (A-7 option (a), mirrors `metrics_enabled`). Added to scope by architect pass @ fd254b335 — D17.
- `daemon/api.py` — **ONE hunk**: the flusher's bounded 10 s shutdown drain beside the db_offload drain slot (~4611–4615), BEFORE `dispose_db_pools` (A-5c). Added to scope by architect pass @ fd254b335 — D15.
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
| `project_id` | TEXT | NOT NULL | PK part; sentinel `"__none__"` for un-attributable calls (amended by architect pass @ fd254b335 — D16, superseding D11) |
| `tool_name` | TEXT | NOT NULL | PK part; NAMES ONLY |
| `agent_id` | TEXT | NOT NULL | PK part; sentinel `'unknown'` for un-attributable calls |
| `call_count` | BIGINT (INTEGER on SQLite) | NOT NULL, default 0 | aggregated counter |
| `updated_at` | TEXT (ISO-8601) | NULL | last flush timestamp |

- **PK**: `(day, project_id, tool_name, agent_id)` (composite).
- **NAMES ONLY is a FROZEN invariant**: no `args`, `argv`, `content`, `tool_call_id`, `instance_id`, or `session_id` column, ever. The signature itself (§3.2) is the guarantee.
- **Aggregated counters only** — per-call rows are REJECTED (volume + content exposure).
- **`agent_id` is NOT NULL** with sentinel `'unknown'` for un-attributable calls (house-consistent with `instance_id` fallback at graph.py:8871).
- **Project attribution**: `project_id` may be `None` at run time (late binding). For the ledger, un-attributable project ⇒ sentinel `"__none__"` (amended by architect pass @ fd254b335 — D16, superseding D11 and resolving the former assumption A1). Self-documenting, unambiguous against real project ids (UUIDs), and matches the house reserved-name idiom (`uuid5(..., "__system_default__")`).
- **Door semantics (sentinel)** (D16): `project_id` omitted on the door = all rows INCLUDING the `__none__` bucket (total truth); explicit `project_id=__none__` filters the un-attributable bucket.
- **Upsert exact contract (both dialects)** (D16/D15-A-6): `INSERT … VALUES (…), … ON CONFLICT (day, project_id, tool_name, agent_id) DO UPDATE SET call_count = tool_usage_daily.call_count + EXCLUDED.call_count, updated_at = EXCLUDED.updated_at` — use `EXCLUDED.updated_at` (flush time), never `now()` (PG-only + wrong clock). Multi-row `VALUES` with the A-6 coalescing precondition.
- **A-6 (🔴 MUST-FIX pin)** (D15): upsert batches must contain **AT MOST ONE ROW PER PK** — PG raises `cardinality_violation` on duplicate PKs in one multi-row `VALUES` while SQLite silently accumulates (latent prod-only divergence). The counter map satisfies this **by construction**; pin it with the dual-dialect repository test ("two deltas same PK in one batch → one row, summed" on PG + SQLite).
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
- **`project_id`**: resolved via the ContextSlot pattern (`instance_metadata["project_id"]`), sentinel `"__none__"` on failure / no project (amended by architect pass @ fd254b335 — D16).
- **`ok`**: best-effort success flag — `True` unless the corresponding returned ToolMessage carries an error indicator. Default `True` on ambiguity. The tap inspects the ToolNode result to set it.
- **No argument payload is possible by construction** — the signature has no args slot. This is the NAMES-ONLY guarantee at the type level.

Sinks are registered at process startup (the flusher registers the ledger sink; Phase 2 registers the family counter). Registration is idempotent and thread-safe. A sink that raises is isolated (logged, never propagates) — the tap is non-load-bearing.

**Amended by architect pass @ fd254b335 — Ruling 1 / D14 (A-1…A-4):**
- **Chain placement (frozen)**: final shape `create_compact_note_tools_node(observation_wrapper(wrapped_tools_node(...)), ...)` — the observation wrapper sits **BETWEEN** `compact_note` (outermost) and `wrapped_tools_node`. It must see the ToolNode **result** to set `ok` (via `ToolMessage.status != "error"`, default `True` on ambiguity) → it sits outside the bare ToolNode; it sits below `compact_note` so post-processing append stays outermost.
- **A-1 (🔴 plan fix)**: only `manager` is in scope in `build_instance_graph` — the wrapper takes `manager._instance_repository` (same as `create_compact_note_tools_node`, graph.py:13606), NOT a bare `instance_repository`.
- **A-2**: identity resolution is PER NODE INVOCATION (per batch), never per tool call — one `asyncio.to_thread(repo.get)` per turn's batch. **Contract pin**: sinks fire only from the async wrapper on the main event loop (guards the cross-loop emit hazard, W2-③).
- **A-3 (hygiene)**: single choke at the tap — truncate `tool_name` / `agent_id` / `project_id` to 256 chars before enqueue, with an overflow counter + throttled WARNING. Keeps NAMES-ONLY total bounded.
- **A-4 (coverage disclosure)**: the only unobserved path is the direct `ainvoke` at `instance_messaging.py:1055` (zero production callers) — acceptable.
- **Three-seam verdict (comparison folded)**: tools-node observation wrapper ADOPTED; tool-FACTORY wrapper REJECTED (incomplete coverage — MCP/plugin/dynamic-skill tools are constructed elsewhere → silent undercount corrupts the ranking); ToolNode SUBCLASS REJECTED (couples to LangGraph internals — version-fragile).
- **Throttle note**: `ToolThrottleSlot` is NOT in this chain (runs in `agent_node` pre-LLM, graph.py:9567–9580) — no ordering interaction, and throttled-out calls never execute so they are never counted.

### 3.3 Off-loop write contract (FROZEN)

**Amended by architect pass @ fd254b335 — Ruling 2 / D15 (A-5, A-5a–d):** the off-loop contract is KEPT; the event-queue buffer shape is REPLACED by a counter map + dirty-key set. These amendments supersede D4 details (core contract unchanged).

- The tap's hot path does **no** DB I/O and never blocks. It resolves identity (A-2: one `asyncio.to_thread(repo.get)` per batch) and increments `dict[(day, project_id, tool_name, agent_id)] → int` + adds the key to the dirty set (~ns, no allocation).
- **A-5 (buffer shape)**: counter map + dirty-key set; **no hard cap**; **emergency flush at ≥10,000 dirty keys**; **NEVER drop counters** (overflow means flush, not discard); evict **CLEAN** keys with `day < today−2` only.
- **A-5a (cadence)**: flush every **30 s** (if dirty) or at **≥500 dirty keys** (5 s was overkill at ranking grade — 17,280 vs 2,880 txns/day for a crash window that does not matter).
- **A-5b (kill-switch semantics)**: the tap gates at enqueue (call-time eval — keep, D0.5/AM-27 precedent; fail-closed: absent ⇒ code default (ON per D7); present-but-unparseable ⇒ collection OFF (approver fix B6, iteration-001)). **The FLUSHER IS SWITCH-BLIND**: toggling OFF performs one final tail flush, then idles. Drain-and-discard is REJECTED — it would truncate day counters mid-day and the digest would faithfully report the cliff as a usage collapse (a false signal in the very artifact this slice exists to produce).
- **A-5c (shutdown)**: explicit **bounded 10 s drain in the ordered shutdown sequence BEFORE `dispose_db_pools`** (beside the db_offload drain slot, api.py:4611–4615). The flusher must **NOT ride `manager._background_tasks`** (cancel-without-drain). 10 s fits the 90 s systemd budget with 80 s headroom; SIGKILL loses ≤1 interval — accepted.
- **A-5d (watchdog)**: the flusher loop is wrapped in a **respawn-on-death outer task**; `flush_error_counter` + dirty-set pressure counters serve as canaries; WARNING throttled to 1/5 min during outages. **DB failure leaves keys dirty** (coalesced retry) — never drop, never fail a tool call.
- Each flush builds ONE multi-row upsert transaction via `asyncio.to_thread(repo.upsert_batch, deltas)`; the map guarantees **at most one row per PK per batch by construction** (A-6, §3.1).

### 3.3.1 Counting contract (FROZEN — verbatim from the architect pass)

**Amended by architect pass @ fd254b335 — Ruling 2 (§3), frozen character-for-character:**

> Tool-usage counters are counted **AT-LEAST-ONCE, per EXECUTED tool_call**, at the tools node, on the main event loop. Retries and checkpoint resumes may re-execute an interrupted tools node and re-increment its counters; duplicates are **accepted, not deduplicated**: the frozen NAMES-ONLY contract carries no `tool_call_id`, and dedupe would require an in-memory LRU of `(thread_id, tool_call_id)` pairs purely to suppress recount events that are rare, bounded (≤ the tool_calls of one node batch per incident), and directionally harmless — duplicates inflate activity but never invent usage of an unused tool, so zero/near-zero — the layer-2 candidacy signal — is unaffected. Deliberate re-invocations are genuine executions and MUST count. The ledger records executions, not unique invocations.

### 3.4 Door contract (FROZEN — Phase 4 consumes)

New router `daemon/routers/tool_usage.py`, `APIRouter(prefix="/tool-usage", tags=["tool-usage"])`, registered in `daemon/routers/__init__.py` with one import line + one `__all__` entry (two edits; `routers/__init__.py:36`/`:73` precedent). (approver fix B5-note, iteration-001)

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

**Amended by architect pass @ fd254b335 — Ruling 9 / D21 (door pins):**
- **Replication invariant**: the new router's `_require_auth_on` **executable body must byte-match** the metrics door's (copy-paste across 5 routers, no shared helper; helper extraction stays out of phase-1 scope). DB errors ⇒ typed 503 `tool_usage_unavailable` with **redacted server-side logging** (`_redact.redact_exc_str`), never raw exception text.
- **Scoping rule frozen (filter-not-boundary)**: "`project_id` is a filter, not a boundary" under the explicit single-operator / single-tenant threat model — any `maintenance:read|metrics:read` holder sees all projects. If multi-tenant ever arrives, add project-membership filtering then.
- **Abuse bounds**: no pagination for v1; freeze a **1 MB response-size test** as the guard + **cap nested agents at top-50 per tool**; revisit at >1,000 tools. Auth-OFF ⇒ 503 bounds unauthenticated volume.

### 3.5 Tool category vocabulary (house rule)

The tap resolves `tool_category` from the tool name using the **existing closed vocabulary** reused as-is (no `VOCABULARY_VERSION` bump unless a new value is needed — and none is). The lookup is a static map `TOOL_NAME → category` populated from the tool registry's declared category (the same source the FE/agent tool catalog uses). If a tool name is not in the map, category = `None` (null in JSON). Phase 2's F24 family labels use `tool_category` from this same vocabulary (no new values ⇒ no version bump, per correction #1: `VOCABULARY_VERSION` is 11 at `daemon/checkpoint_metrics.py:290`).

### 3.6 Managed keys (collection kill-switch + family key + D-5 seeder opt-out) (review fix U3)

**Amended by architect pass @ fd254b335 — D17 (A-7), extended by review fix U3:** the collection key's `config_path="limits.tool_usage_collection_enabled"` MUST have a backing `LimitsConfig` field (option (a) — do NOT ship a dotted path with no field). Add `daemon/config.py` to this phase's file ownership (one additive field, mirroring `metrics_enabled`), plus a registration-shape regression pin. Additionally, the **second** managed key `limits.metrics_tool_usage_enabled` (the F24 family key) and the **third** managed key `limits.tool_usage_digest_seeding_enabled` (the D-5 seeder opt-out key) are registered here with `config_path=None` (DB/ENV-ONLY) — all three keys land in this phase's `settings_service.py` edit; phase 2 and phase 3 keep their zero-edit stance on that file (phase 3 consumes the third key as its opt-out gate: drain/seeder no-ops when OFF).

Add to `MANAGED_KEYS` in `daemon/services/settings_service.py`:

```
"limits.tool_usage_collection_enabled": ManagedKeySpec(
    key="limits.tool_usage_collection_enabled",
    config_path="limits.tool_usage_collection_enabled",   # backed by LimitsConfig field per A-7
    env_var="LIMITS_TOOL_USAGE_COLLECTION_ENABLED",
    value_type="bool",
    default=True,                       # this phase IS the measurement slice
    requires_restart=False,
    sensitive=False,
    description="Tool-usage collection kill-switch ...",
),
"limits.metrics_tool_usage_enabled": ManagedKeySpec(
    key="limits.metrics_tool_usage_enabled",
    config_path=None,                    # DB/ENV-ONLY (wedge_notice shape)
    env_var="ENSEMBLE_METRICS_TOOL_USAGE_ENABLED",
    value_type="bool",
    default=True,
    requires_restart=False,
    sensitive=False,
    description="Tool-usage metrics family kill-switch (Phase 2 stacks under this) ...; mirrors the authoritative per-request env var ENSEMBLE_METRICS_TOOL_USAGE_ENABLED (fail-closed off-switch; the managed key is the operator-writable tier).",),
"limits.tool_usage_digest_seeding_enabled": ManagedKeySpec(
    key="limits.tool_usage_digest_seeding_enabled",
    config_path=None,                    # DB/ENV-ONLY (D-5 opt-out; survives re-seed)
    env_var="LIMITS_TOOL_USAGE_DIGEST_SEEDING_ENABLED",
    value_type="bool",
    default=True,                       # seeder enabled by default; OFF => no-op
    requires_restart=False,
    sensitive=False,
    description="Tool-usage digest seeder opt-out (Phase 3 consumes; no-op when OFF) ...",
),
```

Default ON. Kill-switch semantics (absent vs malformed): key ABSENT from settings ⇒ code default applies (collection ON — the knob is opt-OUT, and absence must never crash: the v0.31.8 absent-config crash class is the motivation); key PRESENT with a parseable boolean ⇒ honored; key PRESENT but unparseable/unknown value ⇒ FAIL-CLOSED: collection OFF + throttled boot WARNING (never a crash, never a silent default). Independent from `limits.metrics_enabled` / `ENSEMBLE_METRICS_ENABLED` (Phase 2 stacks under those). (approver fix B6, iteration-001: replaces the contradictory 'default ON + absent ⇒ OFF' wording; the Kill-switch-semantics sentence above is mirrored verbatim in decisions.md D7.)

---

## 4. Tasks

Each task lists file-level claims + per-task verification. Single-ownership invariant: every file edit is claimed by exactly ONE task below.

### T1 — Repository + model (NEW files)

**Files**: `daemon/repositories/tool_usage/__init__.py`, `daemon/repositories/tool_usage/models.py`, `daemon/repositories/tool_usage/repository.py`.

- `models.py`: `ToolUsageDaily` SQLModel `table=True`, `__tablename__ = "tool_usage_daily"`, columns per §3.1, `__table_args__` with the two secondary indexes named per §3.1.1. All string columns `TEXT` (no VARCHAR). `call_count` uses `sa_column=Column("call_count", BigInteger, ...)`. Follow the memoir/POK precedent (brand-new table ⇒ no `_ensure_postgres_columns` mirror).
- `repository.py`: `ToolUsageRepository(engine)` sync, with `upsert_batch(deltas: list[ToolUsageDelta])` (dialect-aware `INSERT ... ON CONFLICT (day, project_id, tool_name, agent_id) DO UPDATE SET call_count = call_count + excluded.call_count, updated_at = excluded.updated_at`), `query_summary(project_id, days)`, `query_daily(project_id, tool_name, days)`. Empty input short-circuits to 0.
- `__init__.py`: export `ToolUsageDaily`, `ToolUsageRepository`, `ToolUsageDelta`.

**Verification**: unit tests `tests/unit/repositories/test_tool_usage_repository.py` — upsert aggregation (two increments on the same PK sum), PK conflict paths (re-upsert keeps first `day`/identity, increments count), sentinel-agent handling (`agent_id='unknown'` round-trips), empty-batch no-op, SQLite + PG dialect branches. **Amended by architect pass @ fd254b335 — D15 (A-6):** dual-dialect repository test pin — "two deltas same PK in one batch → one row, summed" on PG + SQLite (cardinality_violation guard); exact upsert contract `call_count = tool_usage_daily.call_count + EXCLUDED.call_count, updated_at = EXCLUDED.updated_at`; `__none__` project sentinel round-trips.

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
  2. Resolve `project_id` + `agent_id` via `manager._instance_repository` (A-1) — **per node invocation (per batch), never per tool call** (A-2): one `asyncio.to_thread(repo.get)` per turn's batch. **Contract pin**: sinks fire only from the async wrapper on the main event loop.
  3. Inspect `state["messages"]` for the last AI message with `tool_calls`; for each `tc`, resolve category via the static map.
  4. Delegate to `inner(state, config)`; capture result.
  5. Determine `ok` per tool_call from the result's ToolMessages.
  6. If collection kill-switch ON: increment the counter map + dirty set (A-5), with the A-3 256-char choke before enqueue (truncate `tool_name`/`agent_id`/`project_id` to 256, overflow counter + throttled WARNING).
  7. Fan out to registered sinks (isolated, never raises).
  8. Return inner's result unchanged.
  - All exceptions swallowed (non-load-bearing).
- `ToolUsageFlusher`: background task wrapped in a **respawn-on-death outer task** (A-5d); batches by size (≥500 dirty) OR interval (30 s) (A-5a); builds ONE multi-row upsert via `asyncio.to_thread(repo.upsert_batch, deltas)`; kill-switch **switch-blind** (A-5b — final tail flush then idle); explicit bounded 10 s drain at shutdown per A-5c (NOT via `manager._background_tasks`); `flush_error_counter` + pressure canaries; WARNING throttled 1/5 min; DB failure leaves keys dirty (never drop, never fail a tool call).
- `register_sink(sink)`, `unregister_sink(sink)` — module-level registry, thread-safe.
- `is_collection_enabled()` — reads the managed key at call time; fail-closed.

**Amended by architect pass @ fd254b335 — D14/D15 (A-1..A-5d).** Chain placement (T5): `create_compact_note_tools_node(observation_wrapper(wrapped_tools_node(...)), ...)`.

**Verification**: tap wrapper tests assert (a) tool name + identity captured, (b) the sink signature carries NO argument payload (NAMES-ONLY pin — the test inspects the call args), (c) tap swallows exceptions, (d) the enqueue path is sync (~ns counter-map increment, no DB call). Flusher tests: batching (30 s/500), tail-flush-on-kill-switch, drain-before-dispose ordering, emergency-flush at ≥10,000 dirty, respawn-on-death, never-drop, throttled WARNING.

### T5 — Graph wiring (hunk 1, only hunk)

**File**: `daemon/graph.py`, ~14464–14483 region. Change (amended by architect pass @ fd254b335 — D14, A-1, A-2, A-4):

```python
from daemon.services.tool_usage_observation import wrap_tools_node  # or factory
...
graph.add_node(
    "tools",
    create_compact_note_tools_node(
        wrap_tools_node(
            wrapped_tools_node(tools, registry=LONG_TOOL_REGISTRY),
            instance_repository=manager._instance_repository,  # A-1: manager._instance_repository (graph.py:13606)
            ...
        ),
        ...
    ),
)
```

Final chain shape (frozen, A-1/A-2): `create_compact_note_tools_node(observation_wrapper(wrapped_tools_node(...)), ...)`. The observation wrapper sits **BETWEEN** `compact_note` (outermost) and `wrapped_tools_node`: it must see the ToolNode **result** to set `ok` (via `ToolMessage.status != "error"`, default `True` on ambiguity), and sit below `compact_note` so post-processing append stays outermost. Only `manager` is in scope in `build_instance_graph` (A-1). Identity resolution is per node invocation (A-2), never per tool call. **Three-seam verdict folded**: factory wrapper REJECTED (undercounts MCP/plugin/dynamic-skill tools — silent undercount corrupts the ranking); ToolNode subclass REJECTED (version-fragile). `ToolThrottleSlot` is NOT in this chain (runs in `agent_node` pre-LLM, graph.py:9567–9580) — no ordering interaction; throttled-out calls never execute and are never counted. Coverage disclosure (A-4): the only unobserved path is the direct `ainvoke` at `instance_messaging.py:1055` (zero production callers) — acceptable.

The exact factory signature is pinned in §3.2/§4 T4; the implementer threads `manager._instance_repository` and the queue/flusher handle the same way `manager` is threaded at 14446/14476. **No other graph.py edits.**

**Verification**: AST/grep gate — only the `add_node("tools", ...)` line changes; the diff hunk is confined to ~14464–14483.

### T6 — Manager flusher startup (hunk 2) + api.py shutdown drain (A-5c)

**File**: `daemon/manager.py`, boot sequence ~1145–1148 region. `daemon/api.py` (~4611–4615) gets one hunk for the shutdown drain.

- **Manager hunk 2**: after `SQLModel.metadata.create_all` + migration runner + `_ensure_postgres_columns`, start the flusher via a **respawn-on-death outer task** (A-5d) and register the ledger sink. Wire the collection kill-switch read at startup (log its resolved state once, D0.7/AM-26 precedent). **Do NOT ride `manager._background_tasks`** (A-5c — cancel-without-drain); the flusher is an independent task that is drained explicitly at shutdown.
- **api.py hunk (A-5c)**: add the flusher's **bounded 10 s drain** in the ordered shutdown sequence **BEFORE `dispose_db_pools`**, beside the db_offload drain slot (~4611–4615). `manager.shutdown()` cancels `_background_tasks` first; the flusher drain runs after that, before pool disposal. 10 s fits the 90 s systemd budget with 80 s headroom; SIGKILL loses ≤1 interval — accepted.

Enumerate the hunk precisely:
- Hunk 1 (T3): model import before `create_all` (~1006–1020).
- Hunk 2 (T6): flusher startup + sink registration + kill-switch boot log, in the block after `_ensure_postgres_columns` (~1145–1148 region, before the memoir persona seed at 1164–1166). **Phase 3's seeder hunk will land at 1164–1166** — ours is disjoint and precedes it.
- Hunk 3 (T6/api): flusher drain beside the db_offload drain slot, ~4611–4615, before `dispose_db_pools`.

**Amended by architect pass @ fd254b335 — D15 (A-5c, A-5d).**

**Verification**: boot smoke starts the flusher; shutdown drains within 10 s; kill-switch OFF ⇒ flusher idles.

### T7 — Collection kill-switch managed key(s) + LimitsConfig field (A-7) (review fix U3)

**Files**: `daemon/services/settings_service.py` (`MANAGED_KEYS` — THREE entries: collection key + family key + D-5 seeder opt-out key) and `daemon/config.py` (ONE additive field `LimitsConfig.tool_usage_collection_enabled`).

- `settings_service.py`: add all three entries per §3.6 (collection key with dotted config_path; family key and D-5 seeder opt-out key with `config_path=None`).
- `config.py`: add `tool_usage_collection_enabled: bool = ...` to `LimitsConfig` (A-7 option (a)) so the collection key's dotted `config_path` resolves; mirrors `metrics_enabled`. Registration-shape regression pin (mirrors `metrics_enabled`).

**Amended by architect pass @ fd254b335 — D17 (A-7). Extended by review fix U3 (third key).**

**Verification**: settings tests include ALL THREE new keys; fail-closed resolution test; `MANAGED_KEYS` registration-shape pin (three keys); LimitsConfig field present.

### T8 — Domain door (NEW file + registration edits)

**File**: `daemon/routers/tool_usage.py` (NEW) + the registration edits in `daemon/routers/__init__.py` — one import line + one `__all__` entry (two edits; `routers/__init__.py:36`/`:73` precedent). (approver fix B5-note, iteration-001: the 'one line' wording understated the two-edit form; the registration block below shows both edits.)

- Router, endpoints, auth posture per §3.4 (as amended: replication-invariant `_require_auth_on` body byte-match vs metrics door; DB errors ⇒ typed 503 `tool_usage_unavailable` with `_redact.redact_exc_str` server-side logging; filter-not-boundary scoping rule; abuse bounds — no pagination v1, 1 MB response-size test, nested agents capped top-50 per tool).
- Repository access via `request.app.state` manager (nest_reports precedent).
- Error vocabulary per §3.4.
- **Door filter semantics (`project_id`)** (approver fix B4, iteration-001 — the D16 carrier T8 lacked; semantics source §3.1, wording kept consistent with §3.1): the `project_id` query param OMITTED = all rows INCLUDING the `__none__` bucket (total truth); `project_id=__none__` explicitly = filters to the un-attributable bucket; any other value = that project's rows (D16 sentinel).

**Amended by architect pass @ fd254b335 — D21.**

**Registration edits** in `__init__.py` (two edits — one import line + one `__all__` entry; `routers/__init__.py:36`/`:73` precedent) (approver fix B5-note, iteration-001):
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

**Amended by architect pass @ fd254b335 — Ruling 9 §10.5 / D21 (mutation-pinned vs behavior-tested split for everything phase 1 owns):**

Mutation-pinned (regression pins — the contract fences):
- NAMES-ONLY schema (no `args`/`argv`/`content`/`tool_call_id`/`instance`/`session` column can be added without a pin trip) + no-args sink signature.
- Upsert coalescing dual-dialect (two same-PK deltas → one row summed, PG + SQLite) — A-6.
- `MANAGED_KEYS` registration shape (field + dotted path per A-7) — all three keys (collection + family + D-5 seeder opt-out) (review fix U3).
- Auth replication invariant (body-equality vs metrics door).
- 256-char tap choke (overflow counter + throttled WARNING).
- Door shapes / clamps [1,90] / `__none__` filter / typed error vocabulary / 503 under auth-OFF.
- Flusher: drain-before-dispose ordering; tail-flush on kill-switch; emergency-flush at bound; never-drop.
- Counting contract: replay re-counts (at-least-once accepted) — document-as-test.

Behavior-tested (the semantics):
- Tap observation-only: sink raise → tool call unaffected; ok-flag from `ToolMessage.status`.
- Flusher: drain ordering; tail-flush; emergency-flush at bound.
- Door: shapes, clamps, `__none__` filter, typed errors, 503 under auth-OFF.
- Digest determinism (Phase 3 scope) — not phase 1.

All under `tests/unit/...` (no live contact).

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| AC1 | `tool_usage_daily` table materializes on SQLite boot (migration applies) and on PG boot (create_all). |
| AC2 | Two increments on the same `(day, project, tool, agent)` PK sum into `call_count` (upsert aggregation). |
| AC3 | The tap captures tool NAME + instance identity per executed tool_call; the sink signature carries NO argument payload (NAMES-ONLY pin, asserted by test). |
| AC4 | The enqueue path never awaits DB (off-loop); flusher batches + drains on shutdown; overflow triggers an emergency flush (>=10,000 dirty keys) — NEVER drop counters (overflow means flush, not discard); drop/pressure counters are canaries only, not a discard path. (review fix U5) |
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
| R3 | Bounded queue overflow under burst drops real counts | Medium | **Amended by architect pass @ fd254b335 (D15, A-5):** the counter map NEVER drops counters — overflow means an emergency flush at ≥10,000 dirty keys, not discard. |
| R4 | Composite PK sentinel collisions (empty project id) | Low | **Amended by architect pass @ fd254b335 (D16):** sentinel is `"__none__"` (not `""`); project ids are UUIDs; unambiguous. |
| R7 | Cross-loop emit hazard (sink emits from worker thread) | High | **Mitigated by A-2 (D14):** sinks fire only from the async wrapper on the main event loop — pinned as contract. |
| R5 | Cross-writer hunk collision with Phase 3 seeder | High | Hunk 2 precedes the 1164–1166 seed block; enumerate both precisely in the cross-writer sweep. |
| R6 | Tool category map staleness when new tools ship | Low | Map sourced from the tool registry's declared category at startup; unmapped ⇒ `None`, never a crash. |

---

## 7. Success criteria (phase-exit gates)

- All T9 tests GREEN.
- Boot smoke (SQLite + PG) materializes `tool_usage_daily` with the composite PK + two secondary indexes.
- `git diff --stat` shows ONLY files under `daemon/repositories/tool_usage/`, `daemon/services/tool_usage_observation.py`, `daemon/graph.py` (one hunk), `daemon/manager.py` (two hunks), `daemon/migrations/versions/<new>.sql`, `daemon/routers/tool_usage.py`, `daemon/routers/__init__.py`, `daemon/services/settings_service.py`, `daemon/config.py` (one additive field), `daemon/api.py` (one hunk, A-5c drain), `tests/unit/...` (tool-usage packs). No frontend, no metrics-wiring, no scheduler/seeder.
- Zero live contact before staging (house rule).

---

## 8. Assumptions (explicit)

- A1: Un-attributable `project_id` ⇒ sentinel `"__none__"` (amended by architect pass @ fd254b335 — D16, superseding D11; the architect exercised the rename). RESOLVED.
- A2: `ok` default `True` on ambiguity; Phase 2 treats it as best-effort.
- A3: Tool category resolved by the tap from a static map sourced from the tool registry; unmapped ⇒ `None`.
- A4: F24 is the next family number (Phase 2 verifies against the registrar).
- A5: The flusher's DB handle is the shared sync engine from `daemon/repositories/factory.py` (same as message_metadata).
- A6: `days` clamping [1, 90] on both endpoints.
- A7 (added by architect pass @ fd254b335 — D17): the collection key's `config_path` is backed by `LimitsConfig.tool_usage_collection_enabled` (option (a)); the family key uses `config_path=None` (DB/ENV-ONLY).

---

## 9. Open questions for the orchestrator

- OQ1: If the architect pass changes the sink signature (e.g. adds a timestamp), Phase 2's family counter must be updated in lockstep — flag as a contract-break.
- OQ2: If the tap's `instance_repository.get` proves too slow on the hot path, the fallback is to read `agent_id`/`project_id` from a closure-captured cache populated at instance creation — but that misses late binding (ContextSlot precedent warns against it). Escalate before changing.
- OQ3: Resolved by architect pass @ fd254b335 (D16) — named sentinel `"__none__"` adopted; no further rename.

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
