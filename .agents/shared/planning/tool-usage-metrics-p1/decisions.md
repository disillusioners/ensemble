# Decisions — Tool-Usage Metrics (ENSEMBLE-37 / TokenEconomizer adoption)

Lane: **tool-usage-metrics-p1** (commission ENSEMBLE-37 / TokenEconomizer adoption — Phase 1 measurement slice)
Worktree: `/home/nea/ensemble-src-wt-tool-metrics-p1` @ `0b5624dc9` (branch `feature/tool-usage-metrics-p1`).
Companion plan: `.agents/shared/planning/tool-usage-metrics-p1/phase1-plan.md` (Phase 1 BE). Phase 4 (FE) lives at `phase4-plan.md` in the same directory.

This ledger is the **single source of truth** for the plan set's rulings. Entries are numbered continuously; appended entries never renumber existing ones. Entries marked "ruled by dispatcher" are dispatcher pre-rulings carried verbatim — the architect pass may override with rationale (recorded as a superseding entry).

---

## Decision entries

### D1 — Ledger schema: aggregated counters, one table, per-agent dimension (Phase 1)

**Decision.** Phase 1 ships exactly ONE new table `tool_usage_daily` with PK `(day, project_id, tool_name, agent_id)`, `call_count BIGINT`, optional `updated_at`. Aggregated counters only; per-call rows are REJECTED. NAMES ONLY is a FROZEN invariant: no argument/argv/content/instance/session columns, ever.

**Rationale.** The commission mandates durable tool-call counters per `(project × tool × day)`, NAMES ONLY, preferring aggregated counters over per-call rows. The per-agent dimension is load-bearing for layer-2 candidacy: the writing-block Hybrid Tool Architecture dispatcher tier intersects a per-agent allow-list, so a flat per-tool counter cannot answer "which agents actually use this tool". The cost of the dimension is one PK column (4-part composite PK) — negligible against the volume savings of aggregation. Aggregation also keeps the table small and the digest rollup (Phase 3) a single indexed scan.

**Rejected alternatives.**
- Per-call rows: rejected — volume (one row per tool invocation across all instances) + content exposure risk (a per-call row invites argument columns later). The commission explicitly prefers counters.
- Separate per-agent table: rejected — duplicates the aggregation logic and the upsert path; one table with a PK dimension is simpler and join-free.
- Dropping `agent_id` (per-tool-only): rejected — loses the layer-2 allow-list intersection signal the commission calls out ("Include per-AGENT dimension if cheap").

**Evidence.** Commission requirement 1; `daemon/repositories/instance/models.py:53` (`agent_id` on Instance — the per-agent source, free at tap time); phase1-plan.md §3.1.

**Sensitivity.** Architect pass may override the sentinel for un-attributable agent (`'unknown'`) or project (`""`) — record as a superseding entry; do not edit D1 in place.

---

### D2 — Tap point: tools-node observation wrapper, not an existing slot (Phase 1)

**Decision.** The tap is a **tools-node observation wrapper** inserted into the existing `wrapped_tools_node` chain at `daemon/graph.py:14464-14483`, following the `LONG_TOOL_REGISTRY` stamping precedent. It is NOT a new middleware slot class (the 10-slot map stays untouched), NOT `message_tap_slot` (ids-only, tools_node explicitly OOS per `daemon/services/message_tap.py:24-25`), NOT `ToolThrottleSlot` (reads `last ToolMessage.name` only for throttle machinery — do not pollute it).

**Rationale.** Evidence rules out every existing slot:
- `message_tap_slot` records MESSAGE IDS ONLY (`daemon/services/message_tap.py:1-25` — tools_node explicitly OOS).
- `compaction_tap_slot` / `precall_compaction_tap_slot` tap compaction returns, not tool executions.
- `ToolThrottleSlot` reads `last ToolMessage.name` only for the GII throttle counter (graph.py:9567-9580) — polluting it would corrupt throttle semantics.
- **Decisive fact**: no existing slot/tap observes tool executions with tool name + instance identity.

The tools node IS the execution seam: `graph.add_node("tools", create_compact_note_tools_node(wrapped_tools_node(tools, registry=LONG_TOOL_REGISTRY), ...))` (graph.py:14464-14483). `wrapped_tools_node` (long_tool_nudge.py) already stamps every tool_call at batch entry into a module-level registry then delegates to the bare ToolNode — the house-blessed observation-seam precedent. Inside every node, `config.configurable.thread_id == instance_id`; `manager` is threaded into the wrappers (graph.py:14446/14476). Project resolution follows the ContextSlot pattern (graph.py:9042).

The commission's "reuse, don't re-derive" is honored in substance: no new slot machinery, the existing wrapper chain is the seam. The 10-slot map is untouched.

**Rejected alternatives.**
- New middleware slot class: rejected — adds machinery the commission says to avoid; the wrapper chain already exists.
- Polluting ToolThrottleSlot: rejected — would corrupt throttle semantics (it keys on consecutive GII calls).
- Tapping at the LLM/AI-message level: rejected — that observes tool_calls the LLM *proposes*, not tool_calls that *executed* (and would double-count on retries). The tools node is the execution truth.

**Evidence.** Commission requirement 1 ("Tap the tool-execution path via the existing middleware slots (10-slot map already inventoried — reuse, don't re-derive)"); graph.py:14464-14483; long_tool_nudge.py:752-900; message_tap.py:24-25; graph.py:9567-9580.

**Sensitivity.** Architect pass may prefer the tap at a different seam (e.g. a ToolNode subclass) — record as superseding; the sink signature (D3) is the stable contract.

---

### D3 — C-observer sink signature (frozen contract; Phase 2 consumes) (Phase 1)

**Decision (ruled by dispatcher; architect pass may override with rationale).** The tap fans out to registered in-process sinks with the frozen signature:

```python
SinkCallable = Callable[[str, str | None, str, str, bool], None]
# positional: (tool_name, tool_category, agent_id, project_id, ok)
```

- `tool_name`: executed tool's name (from the AI message's `tool_calls[].name`). NAMES ONLY.
- `tool_category`: resolved by the tap at emission time from a static tool-name → category map (existing closed vocabulary). Sinks do NOT resolve category.
- `agent_id`: resolved from the instance row (`instance.agent_id`), sentinel `'unknown'` on failure.
- `project_id`: resolved via the ContextSlot pattern (`instance_metadata["project_id"]`), sentinel `""` on failure.
- `ok`: best-effort success flag; default `True` on ambiguity.

No argument payload is possible by construction — the signature has no args slot. This is the NAMES-ONLY guarantee at the type level.

**Rationale.** Centralizing category resolution in the tap guarantees no sink can accidentally receive or re-derive argument data; the tap is the single point that touches tool identity. Freezing the signature lets Phase 2 (metrics family counter) consume it with ZERO Phase-1 edits.

**Evidence.** Dispatcher pre-ruling C-observer; phase1-plan.md §3.2.

---

### D4 — Off-loop write path (Phase 1)

**Decision (ruled by dispatcher; architect pass may override with rationale).** The tap's hot path does no DB I/O. It resolves identity and enqueues into a bounded `asyncio.Queue` (default capacity 10 000; overflow drops + counter + WARNING, never blocks). A single background batch flusher drains by size (default 500) OR interval (default 5 s), dispatching DB work via `asyncio.to_thread(repo.upsert_batch, ...)`. Graceful shutdown drains (bounded 10 s). A ledger failure never fails a tool call or a turn. Kill-switch `limits.tool_usage_collection_enabled` (default ON, fail-closed).

**Rationale.** House rule: ANY DB write OFF the event loop. The message_metadata tap precedent (`daemon/services/message_tap.py` + `asyncio.to_thread`) is the canonical bridge. Failure tolerance mirrors the memoir seed (a substrate failure never aborts the turn/boot).

**Evidence.** Dispatcher pre-ruling B; house rule (workflow); message_metadata repository pattern.

---

### D5 — Migration: additive-only, register-before-create_all + own .sql (Phase 1)

**Decision (your ruling F, summarized).** Additive-only: register the `ToolUsageDaily` SQLModel model before `create_all` (manager.py:1006-1020 pattern — lands the table on pre-existing PG); own new `.sql` file `daemon/migrations/versions/<YYYYMMDD_HHMMSS>_create_tool_usage_daily.sql` following the house format (SQLite-executable, idempotent, `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, no `PRECONDITION` header since no version-sensitive DDL). Rollback-safe = the OLD binary never references the new table. Zero DROP/mutate of existing tables. Brand-new table ⇒ NO `_ensure_postgres_columns` mirror (create_all OWNS it).

**Rationale.** The memoir precedent (20261105_102821) is the canonical fresh-PG-table mechanism: SQLModel.metadata.create_all is the PG source of truth; the `.sql` is the persistent record + SQLite path. The migration runner is SQLite-only (daemon/migrations/runner.py:837-842 early-returns on non-SQLite), so a migration-carried INSERT would never seed PG — not applicable here (no seed in Phase 1). `_ensure_postgres_columns` is column-level evolution only and is NOT needed for a brand-new table.

**Rejected alternatives.**
- `_ensure_postgres_columns` mirror: rejected — that path is for evolving EXISTING tables; brand-new tables are owned by create_all (POK/room precedent).
- A migration-carried seed: rejected — runner is SQLite-only; seeds must run on both drivers (memoir precedent: seed runs in manager.py, not in `.sql`).

**Evidence.** manager.py:1006-1020, :1072, :1075-1077, :1145-1148; runner.py:837-842; memoir migration 20261105_102821; POK/room precedents.

**Sensitivity.** Architect pass may adjust the index set; the additive-only + register-before-create_all contract is FROZEN.

---

### D6 — Domain door: `/api/tool-usage/*` read-only, auth posture replicated (Phase 1)

**Decision (ruled by dispatcher; architect pass may override with rationale).** New `daemon/routers/tool_usage.py`, `APIRouter(prefix="/tool-usage")`, one registration line in `daemon/routers/__init__.py`. Endpoints per the frozen contract (phase1-plan.md §3.4). Auth posture replicates `_require_auth_on` verbatim (nest_reports.py:94-114): 503 `AUTH_NOT_CONFIGURED` when auth off; scopes `maintenance:read | metrics:read`; 403 `SCOPE_INSUFFICIENT`. Read-only — no writes through this door.

**Rationale.** The door is the FE's data source (Phase 4) and the digest's read surface (Phase 3). Replicating the nest-reports posture keeps the deployed default (auth-OFF on 0.0.0.0 with permissive CORS) fail-closed: an open route would publish usage rows to anyone who can reach the port. The closed error vocabulary keeps the FE's door-state mapping exhaustive.

**Evidence.** nest_reports.py:57, :94-114; routers/__init__.py:36; dispatcher FROZEN API CONTRACT.

---

### D7 — Collection kill-switch: managed key, default ON, fail-closed (Phase 1)

**Decision.** Add `limits.tool_usage_collection_enabled` to `MANAGED_KEYS` (daemon/services/settings_service.py), bool, default True (this phase IS the measurement slice), `requires_restart=False`, fail-closed (key absent/misconfigured ⇒ collection OFF). Independent from `limits.metrics_enabled` / `ENSEMBLE_METRICS_ENABLED` (Phase 2 stacks under those).

**Rationale.** The managed-key pattern (settings_service.py:518-530 metrics precedent) gives a DB-overridable, env-backed, no-restart kill-switch. Default ON because Phase 1's entire purpose is measurement; default OFF would ship a measurement slice that measures nothing. Fail-closed protects against a misconfigured key silently enabling collection in an unsafe posture. Independence from the metrics global gates lets the Emperor turn off collection without turning off metrics (and vice versa).

**Evidence.** settings_service.py MANAGED_KEYS; metrics_gate.py (kill-switch precedent); dispatcher COLLECTION KILL-SWITCH ruling.

---

### D8 — Metrics family: aggregate-only, F24, new wiring/w5.py (Phase 2 — ruled by dispatcher)

**Decision (ruled by dispatcher; architect pass may override with rationale).** Phase 2 wires one new metrics family (next F-number — likely F24, phase 2 verifies against the registrar) via a NEW `wiring/w5.py`. Aggregate-only: labels `tool_category` (existing closed vocabulary reused as-is — NO `VOCABULARY_VERSION` bump if no new values) + `result ∈ {ok,error}`. NO per-project/per-agent/tool-name labels (decision-module B-2, decisions.md:58). Registrar-only. Band with explicit headroom. Adjudication-pack + catalog §1/§8/§11/§14 + family-pack amendments. Independent per-family kill-switch key stacking under `ENSEMBLE_METRICS_ENABLED + limits.metrics_enabled`.

**Rationale.** The metrics family exposes tool-usage frequency as a Prometheus-style aggregate without cardinality explosion (per-tool/per-agent labels would blow up the series count). Reusing the closed vocabulary avoids a version bump. The independent kill-switch lets collection and metrics be toggled separately.

**Evidence.** Dispatcher pre-ruling C; w4.py (F23 registrar precedent); checkpoint_metrics.py:290 (VOCABULARY_VERSION = 11); decision-module B-2 (decisions.md:58).

**Sensitivity.** Phase 2 verifies the F-number and the band; architect pass may adjust labels.

---

### D9 — FE: 9th tab, slot='custom', lazy body (Phase 4 — ruled by dispatcher)

**Decision (ruled by dispatcher; architect pass may override with rationale).** The FE ships a 9th tab `tool-usage` with `slot: 'custom'` (forced by the type system: `MetricsCentralTab` requires `familyIds`, which tool-usage has none; `MetricsCustomTab` is the only slot shape without `familyIds`). Data door = the frozen `/api/tool-usage/*` domain route (first project-scoped domain door). Tab body ships LAZY (first dynamically-imported tab; VCR dynamic-import mirroring app.ts:391). B4b 185 000 B bound INVARIANT (134 B headroom); fallback = explicit leader re-baseline per R-08, never a silent threshold edit. House SVG only; zero new npm deps.

**Rationale.** The type system forces `custom` (phase4-plan.md Ruling A). Lazy loading keeps the heavy body out of the `/metrics` route fetch set, preserving the 134 B headroom. House SVG avoids chart.js (B2/B3/B4a invariants).

**Evidence.** phase4-plan.md §3 Ruling A, §4, §Task 4; tabs.manifest.ts:120-132/:133-160; app.ts:391.

---

### D10 — Digest + delivery: scheduled agent message + boot-time idempotent seeder (Phase 3 — ruled by dispatcher)

**Decision (ruled by dispatcher; architect pass may override with rationale).** Phase 3 ships a deterministic SQL rollup via a purpose-built read-only tool `tool_usage_digest`, invoked by a scheduled agent message (agent=ari) daily 08:00 Asia/Ho_Chi_Minh. Delivery = boot-time idempotent seeder (memoir seed precedent, manager.py:1144-1166) so activation is AUTOMATIC at promote. Digest = durable file under the plan dir `digests/` + optional board pointer. Fallback if architect overrides = documented one-line post-promote arm step.

**Rationale.** Boot-time idempotent seeding (memoir persona seed precedent) guarantees activation at promote without a manual arm step — the Emperor gets the digest automatically. A scheduled agent message (not a cron daemon) keeps delivery inside the agent system's own scheduling, consistent with the house "agents schedule agents" model. The digest file under the plan dir keeps it durable and reviewable.

**Evidence.** Dispatcher pre-ruling E; memoir seed precedent manager.py:1144-1166; phase1-plan.md §3 (Phase 3 owns seeder hunk at 1164-1166).

---

### D11 — Project sentinel for un-attributable calls (Phase 1)

**Decision.** Un-attributable `project_id` ⇒ sentinel `""` (empty string) in the ledger. (Agent sentinel is `'unknown'` per D1.)

**Rationale.** Project ids are UUIDs, so `""` is unambiguous and sorts first. It keeps the door's `project_id=` (omit) semantics consistent: an empty filter matches un-attributable rows. A named sentinel was considered but adds a magic string with no benefit over the empty value; the empty value mirrors `None` absence semantically.

**Rejected alternatives.** Named sentinel `"_none"` / `"(none)"`: rejected — no added clarity; introduces a magic string the FE would have to know.

**Evidence.** phase1-plan.md §3.1, assumption A1; ContextSlot precedent (project_id can be None at run time).

**Sensitivity.** Architect pass may rename to a named sentinel — record as superseding; single-point change in §3.1.

---

## Corrections (dispatcher adjudicated, from the explorer passes)

These correct prior recon notes. They are recorded as facts, not decisions.

1. **VOCABULARY_VERSION is 11**, located at `daemon/checkpoint_metrics.py:290` — NOT 10, and NOT in `wiring/vocab.py`. Recon was wrong on both count and file. The bump to 11 = group-chat room category (2026-11-16). Phase 2 reuses the closed vocabulary with no new values ⇒ no version bump. (Correction to recon's "VOCABULARY_VERSION is 10, lives in wiring/vocab.py".)

2. **Recon's "tester pack t1_bundle_guard @ PACKS.md:940" is STALE.** No such pack exists in this tree. Enforcement = `bundle-guard.js` + `frontend metrics.bundle-guard.spec.ts:133` threshold pin. (Correction to recon's tester-pack citation.)

3. **Anchor drift vs recon** (re-grep verified in this worktree):
   - graph.py slot classes: `InjectionSlot:314`, `ReportInjectionSlot:697`, `ContextSlot:775`, `ToolThrottleSlot:1093`, `LoopBreakerSlot:1130`, `WatchoverSlot:1217`; `agent_node @ 8729/8862` (recon was +17 on slots).
   - Tap sites: `message_tap_slot @ graph.py:10799-10808`; `compaction_tap_slot @ 10134-10141`; `precall_compaction_tap_slot @ 8174/8188/9771` (recon was ~+370).
   - manager.py seed call: `1144-1166` (recon said `1095-1117`).
   - decision/kill.py managed-key lines: `:105/:128-131` (recon said `129-131`).
   - VOCABULARY_VERSION location: `daemon/checkpoint_metrics.py:290` (recon said `wiring/vocab.py`).

4. **`usage_metadata` is NOT a usable tool-call frequency source.** It is per-LLM-invoke token usage minted from the SSE terminal usage chunk (graph.py:3881-3896; attached to `AIMessageChunk` :3932-3933; `stream_usage=True` injected :4033-4034) — NOT per-tool-call, and NOT persisted anywhere today (rides checkpointed messages only). Verdict: unusable as the tool-call frequency source. (Assessment included in plan per commission.)

5. **The tools node is the execution truth.** `graph.add_node("tools", create_compact_note_tools_node(wrapped_tools_node(tools, registry=LONG_TOOL_REGISTRY), manager=manager, ...))` (graph.py:14464-14483). `wrapped_tools_node` stamps every tool_call at batch entry into `LONG_TOOL_REGISTRY` then delegates to the bare ToolNode — the house-blessed observation-seam precedent. This is where the tap lands.

---

## Open questions (carried, not blocking)

- OQ-A: Sentinel for un-attributable project — `""` vs a named sentinel (D11). Architect pass decides; single-point change.
- OQ-B: Whether the digest's scheduled message (Phase 3) uses the existing scheduler or a new one — Phase 3 owns.
- OQ-C: Whether the FE's near-zero threshold (`NEAR_ZERO_CALLS_7D = 3`) should be data-driven (bottom quartile) — Phase 2/4 concern, not Phase 1.

---

## Assumptions

- A1: F24 is the next family number (Phase 2 verifies against the registrar).
- A2: The flusher's DB handle is the shared sync engine from `daemon/repositories/factory.py`.
- A3: `days` clamping [1, 90] on both endpoints.
- A4: Tool category resolved by the tap from a static map sourced from the tool registry; unmapped ⇒ `None`.
- A5: Phase 3's seeder hunk lands at manager.py:1164-1166 and is disjoint from Phase 1's hunk 2 (which precedes it).

---

## Evidence index

- Commission requirement 1 + process constraints (verbatim in dispatch).
- phase1-plan.md (this lane, Phase 1 BE).
- phase4-plan.md (this lane, Phase 4 FE).
- graph.py:10799-10808, :10134-10141, :8174/:8188/:9771, :14464-14483, :9042, :9567-9580, :8871, :3881-3896, :3932-3933, :4033-4034.
- daemon/services/long_tool_nudge.py:752-900.
- daemon/services/message_tap.py:1-40.
- daemon/manager.py:1006-1020, :1072, :1075-1077, :1145-1148, :1164-1166.
- daemon/migrations/runner.py:837-842.
- daemon/routers/nest_reports.py:57, :94-114.
- daemon/routers/__init__.py:36.
- daemon/services/settings_service.py:253+, :518-530.
- daemon/checkpoint_metrics.py:290.
- daemon/repositories/instance/models.py:53.
- daemon/repositories/message_metadata/repository.py:77-125.
- daemon/migrations/versions/20261105_102821_create_memoir_tables.sql.
- daemon/services/metrics_query/wiring/w4.py (F23 registrar precedent).
- tabs.manifest.ts:120-132/:133-160 (phase4-plan Ruling A).
- app.ts:391 (lazy-mount precedent).
