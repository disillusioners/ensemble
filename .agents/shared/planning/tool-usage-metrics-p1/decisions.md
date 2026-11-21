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
  - (approver fix B3-note, iteration-001) SUPERSEDED by D16 — the sentinel is `"__none__"` everywhere; D3's `""` above is historical. D16 rules the live value and supersedes D11 by name; this marker extends that same supersession to D3's carried `""` clause without rewriting D3's history.
- `ok`: best-effort success flag; default `True` on ambiguity.

No argument payload is possible by construction — the signature has no args slot. This is the NAMES-ONLY guarantee at the type level.

**Rationale.** Centralizing category resolution in the tap guarantees no sink can accidentally receive or re-derive argument data; the tap is the single point that touches tool identity. Freezing the signature lets Phase 2 (metrics family counter) consume it with ZERO Phase-1 edits.

**Evidence.** Dispatcher pre-ruling C-observer; phase1-plan.md §3.2.

**Sink-registration symbol FROZEN (approver fix N-B9-note, iteration-001).** The sink-registration symbol is frozen as `register_sink` (paired `unregister_sink`), exposed at module level by `daemon/services/tool_usage_observation.py` (phase1-plan.md §4 T4, :267). Phase 2 codes against THIS symbol and module path; renaming it is a contract-break. This closes the one cross-phase coupling phase2 §9:153 left open (that note guessed `register_tool_usage_sink` "(or equivalent)" — the phase1 body's actual `register_sink` governs). D3 freezes the signature; this line freezes the registration symbol alongside it.

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

**Decision (ruled by dispatcher; architect pass may override with rationale).** New `daemon/routers/tool_usage.py`, `APIRouter(prefix="/tool-usage")`, one import line + one `__all__` entry (two edits; `routers/__init__.py:36`/`:73` precedent) in `daemon/routers/__init__.py` (approver fix N-B5-note, iteration-001 — corrects the earlier "one registration line" understatement). Endpoints per the frozen contract (phase1-plan.md §3.4). Auth posture replicates `_require_auth_on` verbatim (nest_reports.py:94-114): 503 `AUTH_NOT_CONFIGURED` when auth off; scopes `maintenance:read | metrics:read`; 403 `SCOPE_INSUFFICIENT`. Read-only — no writes through this door.

**Rationale.** The door is the FE's data source (Phase 4) and the digest's read surface (Phase 3). Replicating the nest-reports posture keeps the deployed default (auth-OFF on 0.0.0.0 with permissive CORS) fail-closed: an open route would publish usage rows to anyone who can reach the port. The closed error vocabulary keeps the FE's door-state mapping exhaustive.

**Evidence.** nest_reports.py:57, :94-114; routers/__init__.py:36; dispatcher FROZEN API CONTRACT.

---

### D7 — Collection kill-switch: managed key, default ON, fail-closed (Phase 1)

**Decision.** Add `limits.tool_usage_collection_enabled` to `MANAGED_KEYS` (daemon/services/settings_service.py), bool, default True (this phase IS the measurement slice), `requires_restart=False`. Kill-switch semantics (absent vs malformed): key ABSENT from settings ⇒ code default applies (collection ON — the knob is opt-OUT, and absence must never crash: the v0.31.8 absent-config crash class is the motivation); key PRESENT with a parseable boolean ⇒ honored; key PRESENT but unparseable/unknown value ⇒ FAIL-CLOSED: collection OFF + throttled boot WARNING (never a crash, never a silent default). (approver fix B6-note, iteration-001 — this sentence is byte-identical to phase1-plan.md:207; it replaces the contradictory 'default True + absent ⇒ OFF' clause.) Independent from `limits.metrics_enabled` / `ENSEMBLE_METRICS_ENABLED` (Phase 2 stacks under those).

**Rationale.** The managed-key pattern (settings_service.py:518-530 metrics precedent) gives a DB-overridable, env-backed, no-restart kill-switch. Default ON because Phase 1's entire purpose is measurement; default OFF would ship a measurement slice that measures nothing. Fail-closed protects against a misconfigured key silently enabling collection in an unsafe posture. Independence from the metrics global gates lets the Emperor turn off collection without turning off metrics (and vice versa).

**Evidence.** settings_service.py MANAGED_KEYS; metrics_gate.py (kill-switch precedent); dispatcher COLLECTION KILL-SWITCH ruling.

---

### D8 — Metrics family: aggregate-only, F24, new wiring/w5.py (Phase 2 — ruled by dispatcher)

**Decision (ruled by dispatcher; architect pass may override with rationale).** Phase 2 wires one new metrics family (next F-number — likely F24, phase 2 verifies against the registrar) via a NEW `wiring/w5.py`. Aggregate-only: labels `tool_category` (existing closed vocabulary reused as-is — NO `VOCABULARY_VERSION` bump if no new values) + `result ∈ {ok,error}`. NO per-project/per-agent/tool-name labels (decision-module B-2, `.agents/shared/planning/decision-module/decisions.md:58` — full-path form per approver fix N-B6-note, iteration-001). Registrar-only. Band with explicit headroom. Adjudication-pack + catalog §1/§8/§11/§14 + family-pack amendments. Independent per-family kill-switch key stacking under `ENSEMBLE_METRICS_ENABLED + limits.metrics_enabled`.

**Rationale.** The metrics family exposes tool-usage frequency as a Prometheus-style aggregate without cardinality explosion (per-tool/per-agent labels would blow up the series count). Reusing the closed vocabulary avoids a version bump. The independent kill-switch lets collection and metrics be toggled separately.

**Evidence.** Dispatcher pre-ruling C; w4.py (F23 registrar precedent); checkpoint_metrics.py:290 (VOCABULARY_VERSION = 11); decision-module B-2 (`.agents/shared/planning/decision-module/decisions.md:58` — full-path form per approver fix N-B6-note, iteration-001).

**Sensitivity.** Phase 2 verifies the F-number and the band; architect pass may adjust labels.

---

### D9 — FE: 9th tab, slot='custom', lazy body (Phase 4 — ruled by dispatcher)

**Decision (ruled by dispatcher; architect pass may override with rationale).** The FE ships a 9th tab `tool-usage` with `slot: 'custom'` (forced by the type system: `MetricsCentralTab` requires `familyIds`, which tool-usage has none; `MetricsCustomTab` is the only slot shape without `familyIds`). Data door = the frozen `/api/tool-usage/*` domain route (first project-scoped domain door). Tab body ships LAZY (first dynamically-imported tab; VCR dynamic-import mirroring app.ts:391). B4b 185 000 B bound INVARIANT (134 B headroom); fallback = explicit leader re-baseline per R-08, never a silent threshold edit. House SVG only; zero new npm deps.

**Rationale.** The type system forces `custom` (phase4-plan.md Ruling A). Lazy loading keeps the heavy body out of the `/metrics` route fetch set, preserving the 134 B headroom. House SVG avoids chart.js (B2/B3/B4a invariants).

**Evidence.** phase4-plan.md §3 Ruling A, §4, §Task 4; tabs.manifest.ts:120-132/:133-160; app.ts:391.

**Supersession pointer (approver fix N-B2-note, iteration-001).** D12 SUPERSEDES D9 on the B4b bound (185,000 INVARIANT → 185,000→188,000 ruled re-baseline; D9's fallback clause rescued it but the pointer makes the supersession explicit). D9 stands as history; D12 owns the live bound.

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

### D12 — Leader ruling LR1: R-08 B4b re-baseline RULED YES (Phase 4)

**Ruling (ruled by lane leader — verbatim).** "R-08 B4b re-baseline: RULED YES — threshold_B4b_route_fetch_set_lazy_only_bytes 185,000 → 188,000. Lazy tab body REMAINS PRIMARY (minimizes incremental fetch-set growth); the re-baseline covers the architect-proven ~1.0–1.3 kB static residue with ~2.2 kB margin. Static fallback REJECTED."

**Ruled by:** lane leader (Emperor-designated decision authority for this lane's R-08 escalation).

**Residue arithmetic (architect pass @ fd254b335, W3 `13438c29`):** manifest entry ≈ 125 B + map line/import ≈ 60 B + host ≈ 0.8–1.1 kB min ≈ ~1.0–1.3 kB, versus 134 B headroom; even a zero-byte host breaches (≈ 185 B minimum). Expected measured B4b under lazy ≈ 185,800–186,200 B.

**Phase file(s) where the same ruling text appears:** propagated verbatim into decisions.md (this entry), plan-overview.md, and phase4-plan.md §3 (char-for-char identical, whitespace-normalized) (review fix U10).

---

### D13 — Leader ruling LR2: OQ1 stage.sh reading CONFIRMED (Phase 5)

**Ruling (ruled by lane leader — verbatim).** "OQ1 stage.sh reading: CONFIRMED — staging uses house stage.sh; ENSEMBLE_UPGRADE_LIVE=1 there is the live-dir write guard, not a flip; promote.sh never invoked; phase5 no-flip invariants (state.json untouched, livez uptime continuous, no service restart) stay as gates."

**Ruled by:** lane leader (Emperor-designated decision authority for this lane's OQ1 escalation).

**Phase file(s) where the same ruling text appears:** propagated verbatim into decisions.md (this entry), plan-overview.md, and phase5-plan.md §3.4 (char-for-char identical, whitespace-normalized) (review fix U10).

---

### D14 — Tap amendments A-1…A-4: placement, scope fix, per-batch identity, 256-char choke, coverage disclosure (Phase 1; amends D2)

**Ruling text.** Final chain shape = `create_compact_note_tools_node(observation_wrapper(wrapped_tools_node(...)), ...)` — the observation wrapper sits BETWEEN `compact_note` (outermost) and `wrapped_tools_node`; it must see the ToolNode **result** to set `ok` (via `ToolMessage.status != "error"`, default `True` on ambiguity). A-1 (🔴 plan fix): only `manager` is in scope in `build_instance_graph` — the implementer uses `manager._instance_repository` (same as `create_compact_note_tools_node`, graph.py:13606), NOT a bare `instance_repository`. A-2: identity resolution is PER NODE INVOCATION (per batch), never per tool call — one `asyncio.to_thread(repo.get)` per turn's batch; pin "sinks fire only from the async wrapper on the main event loop" as contract (cross-loop emit hazard guard, W2-③). A-3 (hygiene): single choke at the tap — truncate `tool_name` / `agent_id` / `project_id` to 256 chars before enqueue, overflow counter + throttled WARNING. A-4: coverage disclosure — the only unobserved path is the direct `ainvoke` at `instance_messaging.py:1055` (zero production callers, acceptable). Three-seam verdict: factory wrapper REJECTED (undercounts MCP/plugin/dynamic-skill tools — silent undercount corrupts the ranking); ToolNode subclass REJECTED (version-fragile). ToolThrottleSlot is NOT in this chain (runs in `agent_node` pre-LLM) — no ordering interaction.

**Ruled by:** architect pass @ fd254b335 (W1 `98d904e1`, structural-design).

**Phase file(s) where the same ruling text appears:** phase1-plan.md §3.2, §4 T4, §4 T5.

---

### D15 — Flusher re-spec A-5/A-5a–d + A-6 batch-coalescing must-fix + counting contract frozen verbatim (Phase 1; supersedes D4 details, core contract unchanged)

**Ruling text.** A-5 (shape): `dict[(day, project_id, tool_name, agent_id)] → int` + dirty-key set; no hard cap; emergency flush at ≥10,000 dirty keys; NEVER drop counters (overflow means flush, not discard); evict CLEAN keys with `day < today−2` only. A-5a (cadence): flush every 30 s (if dirty) or ≥500 dirty keys (5 s was overkill at ranking grade). A-5b (kill-switch semantics): the tap gates at enqueue (keep); the FLUSHER IS SWITCH-BLIND — toggling OFF performs one final tail flush, then idles; drain-and-discard REJECTED (truncates day counters mid-day → digest reports a false usage collapse). A-5c (shutdown): explicit bounded 10 s drain in the ordered shutdown sequence BEFORE `dispose_db_pools` (beside the db_offload drain slot, api.py:4611–4615); the flusher must NOT ride `manager._background_tasks` (cancel-without-drain). A-5d (watchdog): flusher loop wrapped in respawn-on-death outer task; `flush_error_counter` + pressure counters as canaries; WARNING throttled 1/5 min; DB failure leaves keys dirty (coalesced retry), never drop, never fail a tool call. A-6 (🔴 must-fix): upsert batches must contain AT MOST ONE ROW PER PK — PG raises `cardinality_violation` on duplicate PKs in one multi-row `VALUES` while SQLite silently accumulates (latent prod-only divergence); the map design satisfies this by construction; pin with a dual-dialect repository test ("two deltas same PK in one batch → one row, summed").

**Counting contract (FROZEN — verbatim, character-for-character):**

> Tool-usage counters are counted **AT-LEAST-ONCE, per EXECUTED tool_call**, at the tools node, on the main event loop. Retries and checkpoint resumes may re-execute an interrupted tools node and re-increment its counters; duplicates are **accepted, not deduplicated**: the frozen NAMES-ONLY contract carries no `tool_call_id`, and dedupe would require an in-memory LRU of `(thread_id, tool_call_id)` pairs purely to suppress recount events that are rare, bounded (≤ the tool_calls of one node batch per incident), and directionally harmless — duplicates inflate activity but never invent usage of an unused tool, so zero/near-zero — the layer-2 candidacy signal — is unaffected. Deliberate re-invocations are genuine executions and MUST count. The ledger records executions, not unique invocations.

**Ruled by:** architect pass @ fd254b335 (W2 `14461184`, W4 `eb7fd799`).

**Phase file(s) where the same ruling text appears:** phase1-plan.md §3.3, §3.3.1, §4 T4, §4 T6.

---

### D16 — Project sentinel renamed `""` → `"__none__"` (Phase 1; SUPERSEDES D11)

**Ruling text.** Project sentinel is `"__none__"` EVERYWHERE in the ledger and the door — the empty string is ambiguous with the door's omitted = all-projects param semantics and fragile in query-string handling. `"__none__"` is self-documenting, unambiguous vs UUIDs, and matches the house reserved-name idiom (`uuid5(..., "__system_default__")`). Door semantics: `project_id` omitted = all rows INCLUDING the `__none__` bucket (total truth); explicit `project_id=__none__` filters the un-attributable bucket. Upsert exact contract (both dialects): `INSERT … VALUES (…), … ON CONFLICT (day, project_id, tool_name, agent_id) DO UPDATE SET call_count = tool_usage_daily.call_count + EXCLUDED.call_count, updated_at = EXCLUDED.updated_at` — use `EXCLUDED.updated_at` (flush time), never `now()` (PG-only + wrong clock). Agent sentinel stays `'unknown'` (validated, D1 unchanged). This entry SUPERSEDES D11 (D11 stands as history; not edited in place); resolves the former assumption A1 and OQ3.

**Ruled by:** architect pass @ fd254b335 (W4/W6 evidence).

**Phase file(s) where the same ruling text appears:** phase1-plan.md §3.1 (:76, sentinel semantics), §3.2, §4 T8 (door-filter carrier), §6 R4, §8 A1, §9 OQ3. (approver fix B4-note, iteration-001) — the former §3.4 pointer is withdrawn: §3.4 has zero `__none__` occurrences; the sentinel ruling lives at §3.1:76 and the door-filter carrier at §4 T8 (landed by the parallel body fix).

---

### D17 — Managed keys: A-7 option (a) + dual registration placed in phase 1 (Phase 1)

**Ruling text.** A-7 (🟡): the collection key's `config_path="limits.tool_usage_collection_enabled"` has NO backing `LimitsConfig` field — do NOT ship a dotted path with no field. Fix = option (a): ADD `LimitsConfig.tool_usage_collection_enabled` to `daemon/config.py` + dotted `config_path` + a registration-shape regression pin (mirrors `metrics_enabled`, which has both; costs one `daemon/config.py` edit — added to phase 1's file ownership). ALSO register the SECOND managed key `limits.metrics_tool_usage_enabled` (the F24 family key) in phase 1's `MANAGED_KEYS` edit with `config_path=None` (DB/ENV-ONLY, `wedge_notice_emission_enabled` shape). Architect placement ruling: BOTH keys land in phase 1's `settings_service.py` edit; phase 2's zero-edit stance on that file is preserved.

**Ruled by:** architect pass @ fd254b335 (W6 `3c653166`; §8 Ruling 7).

**Phase file(s) where the same ruling text appears:** phase1-plan.md §2, §3.6, §4 T7.

---

### D18 — F24 family corrections (Phase 2)

**Ruling text.** (1) Measured closed-space total 625 ≤ 2000 is THE claim (491 today + 134; the U16b +1 renumbering to 492/626 was wrong and is REVERTED — see the appended architect-style correction below; architecture-recommendation.md §5 stands as written at 491/625); the plan's "≈1095 ≪ 2000" is a band-sum (Σ bands 846 + F24's 160 + baseline 89) — may be cited separately, never as the headline; mixing conventions misleads future cardinality gates. (2) Citation corrected: the gate pattern is `metrics_gate.py` (env TRUTHY polarity + `live_managed_value`), NOT `kill.py:105/:128-131` (that is the decision-module kill store). (3) Gauge ("distinct tools observed") REJECTED for Phase 1 — second series family + near-zero decision value; scope-creep. (4) Operator writability: family key registered per D17 placement. (review fix U16b reconciliation)

**Ruled by:** architect pass @ fd254b335 (W6 `3c653166`; §5 Ruling 4).

**Phase file(s) where the same ruling text appears:** phase2-plan.md (untouched by this amendment's write scope; recorded here as the governing correction).

**Correction (appended; approver fix B5-note, iteration-001; architect-owned style).** (approver iteration-001 B5 correction: the U16b +1 renumbering (491->492/625->626) was wrong — 492 derivable by no convention (pack helper 402+89=491; enforced cumulative 89+813=902); reverted to the architect's measured 491/625 which stands as written in architecture-recommendation.md §5. phase2-plan.md carries 491/625 at :61/:82/:96/:133/:143.) This appended entry discloses the full arc and discharges carried note B8; the ruling text above is reverted in place to the architect's measurement, which is the governing value.

---

### D19 — Seeder + digest material amendments D-1…D-9 (Phase 3)

**Ruling text (summary).** D-1 (🔴 seeder repo-readiness — build own repos from `self._engine` or move hunk ≥ :2113); D-2 (🔴 STOPPED rows not started by `start_all` — seed startable/registered state or invoke the create-path compensation `scheduling_service.py:822-829`); D-3 (🔴 adapter config shape — persist `schedule` cron, not `when`); D-4 (🟡 uuid5 deterministic source_id idempotency); D-5 (🔴 disable-lifecycle — managed key `limits.tool_usage_digest_seeding_enabled` + no-op on existing row in ANY state); D-6 (🟡 explicit canonical root, no `manager.workdir` phantom fallback); D-7 (🟡 frozen window semantics D−1 / [D−7,D−1] complete UTC days); D-8 (🟡 holder set/jober benign; disclose); D-9 (🟡 rollback disclosure text frozen). Determinism tests extend to the file-write path. Full text: architecture-recommendation.md §7.

**Ruled by:** architect pass @ fd254b335 (W5 `c3271c17`).

**Phase file(s) where the same ruling text appears:** phase3-plan.md (untouched here); architecture-recommendation.md §7 is the authoritative text.

---

### D20 — I1 proof re-specified on corrected premise (Phase 5; supersedes the collision premise)

**Ruling text.** Premise correction — `feature/skill-system-defects` (`868c6c616f`) is ALREADY MERGED (ancestor of base via `17e100532`) and NEVER touched `manager.py` (only `skill_evolution_service.py` + its test); branch-span `manager.py` hunks sit at ~4815–4828 / ~7986–8067 — zero overlap with the plan's three hunks (h1 ~1006–1020, h2 ~1145–1148, h3 ~1166+). NEVER cite that census at gate time. The I1 proof is re-specified against gate-time drift: enumerate our `manager.py` hunks exactly (three hunks + `@@` ranges); internal disjointness; gate-time drift re-check (every branch merged into `origin/latest` after base touching `manager.py` — no overlap); file-set sweep (`graph.py`, `settings_service.py`, `config.py`, `routers/__init__.py`, `tools/instance.py`, `tools/_tool_registry.py`); upstream freshness (base == origin/latest at merge time).

**Ruled by:** architect pass @ fd254b335 (W4 git-verified; §9 Ruling 8).

**Phase file(s) where the same ruling text appears:** phase5-plan.md (untouched here; freeze the spec there at execution planning).

---

### D21 — Cross-cutting pins (Phase 1 / 4)

**Ruling text.** (1) Auth replication invariant — the new router's `_require_auth_on` executable body must byte-match the metrics door's (copy-paste across 5 routers, no shared helper; helper extraction out of phase-1 scope); DB errors ⇒ typed 503 `tool_usage_unavailable` with redacted server-side logging (`_redact.redact_exc_str`), never raw exception text. (2) Scoping rule frozen: "`project_id` is a filter, not a boundary" under the explicit single-operator / single-tenant threat model — any `maintenance:read|metrics:read` holder sees all projects. (3) Abuse bounds: no pagination for v1; 1 MB response-size test frozen; nested agents capped at top-50 per tool. (4) B4b 188,000 re-baseline confirmed (LR1, D12) — residue arithmetic per D12.

**Ruled by:** architect pass @ fd254b335 (W6 `3c653166` §10; LR1 leader ruling for the B4b number).

**Phase file(s) where the same ruling text appears:** phase1-plan.md §3.4, §4 T8 (pins 1–3); D12 (pin 4).

---

### D22 — Phase-4 ownership grant (leader ruling LR1 executed)

**Ruling text.** `bundle-baseline.json` (`threshold_B4b_route_fetch_set_lazy_only_bytes` 185,000 → 188,000) and the spec-pin threshold update are now OWNED BY PHASE 4 by leader ruling LR1 — phase 4 may edit the baseline artifact + its pin as part of its own lane; no other lane touches them.

**Ruled by:** lane leader (LR1); recorded by this ledger at the architect pass fold.

**Phase file(s) where the same ruling text appears:** phase4-plan.md (untouched here — ownership granted, execution in phase 4's lane).

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


---

## PREDICATE LEDGER (review fix U11)

Canonical predicates copied char-for-character from the phase bodies where they live. Containment verified by whitespace-normalized substring check (`re.sub(r"\s+", " ", ...)`) — line-based grep is invalid for wrapped strings.

**Scope (approver fix N-B1-note, iteration-001):** this ledger carries the 7 DRIFT-RISK strings only — rulings that live verbatim in 2+ files — NOT an exhaustive gate census; body gates live in their phase files.

| # | Canonical predicate (verbatim) | Source body location |
|---|---|---|
| 1 | sentinel `"__none__"` for un-attributable calls | phase1-plan.md §3.1 (D16) |
| 2 | NEVER drop counters (overflow means flush, not discard) | phase1-plan.md §5 AC4 (:382) — re-pinned from §3.3 (the §3.3 carrier wraps the phrase in `**` emphasis, breaking char-for-char); plain occurrence verified at AC4 (approver fix B2-note, iteration-001) |
| 3 | ≤ 188 000 B (RE-BASELINED by leader ruling LR1 — decisions D12; ownership grant decisions D22; was ≤ 185 000 B) | phase4-plan.md §4.2 (B4b row) / Task 6 (LR1) — re-pinned to body truth (approver fix B1-note, iteration-001) |
| 4 | schedule:'0 8 * * *' | phase3-plan.md §3.8 (seeded-row config, D-3) |
| 5 | complete previous UTC day (`D−1`); "cumulative-week" := `[D−7, D−1]` complete UTC days | phase3-plan.md §3 (D-7) |
| 6 | VOCABULARY_VERSION==11 | phase2-plan.md §3 / T6 |
| 7 | counting contract — byte-identical blockquote in D15 + phase1-plan.md §3.3.1 + architecture-recommendation.md §3 | D15 / phase1 §3.3.1 / architecture-recommendation.md §3 |

## CLOSING ATTESTATION (review fix U11)

This is the approver iteration-001 fix pass on the decisions.md ledger. What changed in THIS pass: B1 (PREDICATE LEDGER row 3 re-pinned to the body's current attribution string — "LR1 — decisions D12; ownership grant decisions D22"), B2 (this attestation rewritten to true results + row 2's source cell re-pinned from §3.3 to its verified plain site §5 AC4), B3 (D3 append-only supersession marker: sentinel is `"__none__"` everywhere; D3's `""` historical), B4 (D16 x-ref repointed from the empty §3.4 to §3.1:76 + §4 T8), B5 (D18 reverted to 491/625 as THE claim + appended architect-style correction entry disclosing the full arc — also discharges carried note B8), B6 (D7 contradictory clause replaced with the adjudicated absent-vs-malformed semantics, byte-identical to phase1:207). Carried notes folded: N-B1 (ledger scope declaration), N-B2 (D12 SUPERSEDES D9 pointer at D9), N-B5 (D6 two-edits form), N-B6 (D8 full-path citation), N-B9 (sink-registration symbol `register_sink` frozen at D3). B7 (phase5 SHA) is a body-file fix outside this worker's decisions.md-only scope and was NOT touched here. Edit scope: decisions.md (this worker); body fixes landed by parallel workers, verified by containment, not by authorship.

Verification method: Python `re.sub(r"\s+", " ", text)` whitespace-normalized CONTAINMENT of each PREDICATE LEDGER row's canonical string against its cited CURRENT body file (line-based grep is invalid for wrapped strings); row 7 verified by direct byte comparison of the blockquote across its three carriers. TRUE per-row results after this pass: row 1 PASS — phase1-plan.md §3.1 (:65); row 2 PASS — phase1-plan.md §5 AC4 (:382), the plain occurrence (the former §3.3 carrier wraps the phrase in `**` emphasis and FAILS char-for-char); row 3 PASS — phase4-plan.md §4.2 B4b row (:313); row 4 PASS — phase3-plan.md §3.8 (:68); row 5 PASS — phase3-plan.md §3 D-7 (:82); row 6 PASS — phase2-plan.md §3/T6 (:82/:96/:136/:143); row 7 PASS — counting-contract blockquote byte-identical across D15 + phase1-plan.md §3.3.1 + architecture-recommendation.md §3. All 7 rows PASS; no residual drift in this file. This supersedes the prior attestation's false "entries 1–6 PASS / No drift found" claim.
