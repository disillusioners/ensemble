# Architecture Recommendation — Tool-Usage Metrics Phase 1 (measurement slice)

**Commission:** ENSEMBLE-37 (TokenEconomizer adoption) — Phase 1 measurement slice; architect validation + deepening pass over the delivered plan set.
**Lane:** plan-only. Worktree `/home/nea/ensemble-src-wt-tool-metrics-p1` @ branch `feature/tool-usage-metrics-p1` (base `0b5624dc9`, plan set committed at `fcab57221`).
**Date:** 2026-11-21. **Architect instance:** this lane's architect controller.
**Evidence base:** 6 dispatched design-analyst workers (read-only, evidence-cited, anchors re-verified in-worktree):
W1 tap-seam `98d904e1` (structural) · W2 flusher `14461184` (resilience) · W3 FE-lazytab `13438c29` (structural, high tier) · W4 ledger/migration `eb7fd799` (data-flow) · W5 seeder/digest `c3271c17` (resilience) · W6 door-security `3c653166` (security).
All rulings below carry worker evidence; where workers disagreed or corrected the plan, the correction is carried through, never dropped.

---

## 1. Executive verdict per plan-phase

| Phase | Verdict | One-line reason |
|---|---|---|
| **P1 — ledger + tap + door** | ✅ **VALIDATE with amendments** (7 architect amendments A-1…A-7 below) | Core seams are correct (tap seam validated against two alternatives; schema/indexes/upsert sound; door fail-closed confirmed); the off-loop buffer shape, kill-switch wiring details, and one sentinel need amendment. |
| **P2 — F24 metrics family** | ✅ **VALIDATE with corrections** (2 factual corrections + 1 registration placement) | Every conformance anchor verified (67-value vocab v11, band site, name rule, gate polarity); the plan's global-total arithmetic uses the wrong convention and one anchor cites the wrong module. |
| **P3 — digest + seeder** | ⚠️ **AMEND MATERIALLY — the architecture stands, the activation wiring is wrong as written** | Three structural defects (seeder repo-readiness, `start_all` vs STOPPED, adapter config shape) mean the 08:00 schedule would never fire as specified; plus a disable-lifecycle gap and a phantom path fallback. Fixes are local and specified below — no re-architecture. |
| **P4 — FE tab** | ✅ **VALIDATE with amendments + ONE LEADER DECISION** | Lazy mechanism FEASIBLE (high confidence, proven from the measure tool's own logic) and slot `custom` validated — but **no 9th-tab variant can keep B4b ≤ 185,000**: an explicit re-baseline to 188,000 is unavoidable and must be ruled by the leader (R-08). |
| **P5 — gates + staged release** | ✅ **VALIDATE with I1 reframed + OQ1 resolved** | Gate order and NO-LIVE-FLIP boundary sound; the collision premise behind I1 was factually wrong (branch is merged, disjoint) — the proof is re-specified against gate-time drift; OQ1 resolves to the house `stage.sh` guard reading. |

---

## 2. Ruling 1 — Tap point (D2): VALIDATED, seam sharpened

**Verdict: the tools-node observation wrapper is the right seam. Amended insertion point: BETWEEN `create_compact_note_tools_node` (outermost) and `wrapped_tools_node`.**

Chain inventory (W1, verified): `add_node("tools", create_compact_note_tools_node(wrapped_tools_node(tools, registry=LONG_TOOL_REGISTRY), …))` at `graph.py:14464-14483`. Innermost = bare `ToolNode` (constructed in `long_tool_nudge.py:776`); `wrapped_tools_node` (`long_tool_nudge.py:752`) stamps `LONG_TOOL_REGISTRY` at batch entry, resolves instance_id from `config.configurable.thread_id` (:787), and clears stamps in `finally`; `create_compact_note_tools_node` (`graph.py:13577`) is outermost post-processing. **Throttle is NOT in this chain** — `ToolThrottleSlot` runs in `agent_node` pre-LLM (`graph.py:9567-9580`), so there is no ordering interaction and no need to see throttled-out calls (they never execute).

Three-seam comparison (five axes):

| Seam | Complexity | Coverage | Maintainability | Risk | Cost (hot path) | Recommendation |
|---|---|---|---|---|---|---|
| A: tools-node obs wrapper (plan) | Low — one wrapper, house precedent | **Full** — ALL tool kinds merge into the single ToolNode (`create_instance_tools` merges MCP via `_load_mcp_tools`, plugin, dynamic-skill, factory) | High — mirrors `long_tool_nudge` precedent | Low — observation-only is structurally strongest here (wraps outside bare ToolNode, swallows all exceptions, returns inner result unchanged) | 1 repo-get per BATCH + N dict ops per call | **ADOPT** |
| B: tool-factory wrapper | High — must wrap every construction path | **Incomplete** — MCP/plugin/dynamic tools constructed elsewhere → undercount | Low — N wrap sites drift | High — silent undercount corrupts the ranking; weaker ok-flag (raw exceptions vs ToolMessage.status) | N repo-gets per batch unless cached | Reject |
| C: ToolNode subclass | Medium — fights the existing composition | Full | Low — couples to LangGraph internals (`_arun_batch`) across versions | Medium — version-fragile, awkward compose with the two existing wrappers | ~same as A | Reject |

Placement rationale (W1): the wrapper must see the ToolNode **result** to set `ok` (via `ToolMessage.status != "error"`, default True on ambiguity) → it must sit outside the bare ToolNode; it must sit **below** `compact_note` so post-processing append stays outermost. Final shape: `create_compact_note_tools_node(observation_wrapper(wrapped_tools_node(...)), ...)`.

Architect amendments to phase 1 (tap):
- **A-1 (🔴 plan fix):** phase1 T5 sketches `instance_repository` as in-scope in `build_instance_graph` — only `manager` is in scope; the implementer must use `manager._instance_repository` (same as `create_compact_note_tools_node`, `graph.py:13606`).
- **A-2:** identity resolution is **per node invocation (per batch), never per tool call** — one `asyncio.to_thread(repo.get)` per turn's batch; a per-call or per-turn cache is not justified. Sinks fire **only from the async wrapper on the main event loop** (pin as contract; guards the cross-loop emit hazard W2-③).
- **A-3 (hygiene, from W6):** single choke at the tap — **truncate `tool_name` / `agent_id` / `project_id` to 256 chars** before enqueue, with an overflow counter + throttled WARNING. Bounds pathological MCP/dynamic names; keeps NAMES-ONLY total.
- **A-4:** coverage disclosure: the only unobserved path is the direct `ainvoke` at `instance_messaging.py:1055` (zero production callers) — acceptable.

## 3. Ruling 2 — Off-loop flusher (D4): VALIDATED WITH AMENDMENTS — buffer shape replaced

**Verdict: keep the off-loop contract; replace the event-queue with a counter-map + dirty-key set; relax cadence; never drop counters; flush-the-tail on kill-switch; drain explicitly at shutdown.**

Verified substrate facts (W2): worker pool = **threads**, bridged via `run_coroutine_threadsafe` onto the **single main loop** (`worker_pool.py:166`, `main_loop_bridge.py:83,133`) — the graph/tools node executes as coroutines on one loop. Retries resume the graph from checkpoint (`task_processor.py:531,594`) — an interrupted tools node re-executes. Shutdown: lifespan → `manager.shutdown()` cancels `_background_tasks` FIRST, `dispose_db_pools` LAST (`manager.py:17152-17280`); systemd `TimeoutStopUSec=90s`.

Buffer shape comparison:

| Shape | Complexity | Scalability (outage) | Maintainability | Risk | Cost |
|---|---|---|---|---|---|
| Event queue (plan as written) | Low | **Poor** — PG outage fills 10k queue then DROPS; drops bias the ranking toward zero (corrupts the digest's own signal) | Medium | 🟡 drop-bias | ~µs/event + 2 MB bound |
| **Counter map + dirty set (recommended)** | Low | **Good** — increments coalesce into existing keys; backpressure bounded by key cardinality, not event rate | Medium | Low | `dict[key] += 1` (~ns, no alloc) |

The event queue's per-event fidelity buys nothing: the ledger persists only per-key deltas, and Phase 2 consumes `ok` from its own in-memory counter. **The map's coalescing is also load-bearing for PG correctness** (see A-6: duplicate PKs in one upsert batch are a PG error) — the map guarantees one row per key per flush by construction.

Architect amendments to phase 1 (flusher) — these supersede D4 details (core contract unchanged):
- **A-5 (shape):** `dict[(day, project_id, tool_name, agent_id)] → int` + dirty-key set; no hard cap; emergency flush at ≥10,000 dirty keys (**never drop counters** — overflow means flush, not discard); evict CLEAN keys with `day < today−2` only.
- **A-5a (cadence):** flush every **30 s** (if dirty) or ≥500 dirty keys. 5 s is overkill at ranking grade (17,280 vs 2,880 txns/day for a 6× tighter crash window that does not matter).
- **A-5b (kill-switch semantics):** the tap gates at enqueue (call-time eval — keep); **the flusher is switch-blind**: toggling OFF performs one final tail flush, then idles. Drain-and-discard (plan as written) is REJECTED — it truncates day counters mid-day and the digest would faithfully report the cliff as a usage collapse, a false signal in the very artifact this slice exists to produce.
- **A-5c (shutdown):** explicit bounded 10 s drain **in the ordered shutdown sequence before `dispose_db_pools`** (beside the db_offload drain slot, `api.py:4611-4615`); the flusher must NOT ride `manager._background_tasks` (cancel-without-drain). 10 s fits the 90 s systemd budget with 80 s headroom; SIGKILL loses ≤1 interval — accepted.
- **A-5d (watchdog):** flusher loop wrapped in respawn-on-death outer task; `flush_error_counter` + drop/pressure counter as canaries; WARNING throttled to 1/5 min during outages. DB failure ⇒ leave keys dirty (coalesced retry), never drop, never fail a tool call.
- **A-6 (🔴 must-fix, from W4):** upsert batches must contain **at most one row per PK**. PG raises `cardinality_violation` ("cannot affect row a second time") on duplicate PKs in one multi-row `VALUES`; SQLite silently accumulates — a latent prod-only divergence. The map design satisfies this by construction; pin it with a dual-dialect repository test ("two deltas same PK in one batch → one row, summed").

**Counting contract (freeze verbatim in phase 1):**

> Tool-usage counters are counted **AT-LEAST-ONCE, per EXECUTED tool_call**, at the tools node, on the main event loop. Retries and checkpoint resumes may re-execute an interrupted tools node and re-increment its counters; duplicates are **accepted, not deduplicated**: the frozen NAMES-ONLY contract carries no `tool_call_id`, and dedupe would require an in-memory LRU of `(thread_id, tool_call_id)` pairs purely to suppress recount events that are rare, bounded (≤ the tool_calls of one node batch per incident), and directionally harmless — duplicates inflate activity but never invent usage of an unused tool, so zero/near-zero — the layer-2 candidacy signal — is unaffected. Deliberate re-invocations are genuine executions and MUST count. The ledger records executions, not unique invocations.

## 4. Ruling 3 — Ledger schema (D1): VALIDATED; project sentinel renamed

- **PK / types / TEXT-day:** validated. Worst-case key ≈ 90 B ≪ btree limits; the hot-mutated columns (`call_count`, `updated_at`) are in NO index → increments qualify as HOT updates on PG. ISO-date TEXT sorts chronologically; UTC discipline enforced at the enqueue site (the single point where local time could leak).
- **Agent sentinel `'unknown'`:** validated — no agent directory is named `unknown` (55 defs checked); matches the `instance_id` fallback idiom (`graph.py:8871`).
- **Project sentinel `""` → AMENDED to `"__none__"`** (the D11 architect-renameable call, exercised): the empty string is ambiguous with the door's *omitted = all-projects* param semantics and fragile in query-string handling. `"__none__"` is self-documenting, unambiguous vs UUIDs, and matches the house reserved-name idiom (`uuid5(..., "__system_default__")`). Single-point change recorded here as superseding D11; door semantics: `project_id` omitted = all rows INCLUDING the `__none__` bucket (total truth); explicit `project_id=__none__` filters the un-attributable bucket.
- **NAMES-ONLY:** structurally enforced twice — no args column can exist (schema) and the sink signature has no args slot (types). Verified.
- **Indexes:** both secondaries earn their keep ((project_id, day) serves project-filtered summary + digest; (tool_name, day) serves the daily series; PK's leading `day` serves the no-project summary as a bounded range scan — not a full scan). Keep both; 3-site name lockstep pinned. 🟢 minor: per-agent grouping in the summary reuses the day-range scan in memory — fine at this scale.
- **Growth / retention:** rows/day ≈ 1,440 realistic (~3 proj × 40 tools × 12 agents active), 22.4k busy ceiling → **~0.1 GB/year realistic, ~2 GB/year ceiling**. **No pruning in Phase 1 — correct**; pruning is a multi-year-horizon concern. Disclosure: when pruning eventually lands, `calls_total` semantics shift (lifetime → windowed) — decide then, not now.
- **Upsert (exact contract, both dialects — from W4, verified in-memory on SQLite):** `INSERT … VALUES (…), … ON CONFLICT (day, project_id, tool_name, agent_id) DO UPDATE SET call_count = tool_usage_daily.call_count + EXCLUDED.call_count, updated_at = EXCLUDED.updated_at` — identical on SQLite ≥ 3.24 (case-insensitive `excluded`). Use `EXCLUDED.updated_at` (flush time), never `now()` (PG-only + wrong clock). Multi-row VALUES (one round trip) with the A-6 coalescing precondition.

## 5. Ruling 4 — F24 family (phase 2): VALIDATED with two corrections

Conformance verified by W6 against code: vocabulary 66 + reserved `other` = **67 values, version 11** (`checkpoint_metrics.py:290,:439-463`; artifact `count:66, vocabulary_version:11`); band site `adjudication.py:175-212` with zero-headroom reserved to F23/decision_overlay only; name rule `^ensemble_[a-z0-9_]+$` (`checkpoint_metrics.py:110`); AM-2 `allowed_values` enforcement (`registry.py:102-114`); gate polarity (env explicit-not-truthy = OFF; managed read via `metrics_gate.live_managed_value`).

- **Band math: CORRECT.** 67 × 2 = 134 series; band 160 = 134 + 26 explicit headroom. ✅
- **🟡 Correction 1 — global total convention:** the plan's "≈1095 ≪ 2000" is a **band-sum** (Σ bands 846 + F24's 160 + baseline 89). The **measured closed-space total is 491 today; +134 = 625 ≤ 2000**. Both are safe; the plan must state the measured number (625) as the claim and may cite the band-sum separately — mixing conventions misleads future cardinality gates (house A-6 warning).
- **🟡 Correction 2 — citation:** phase 2 cites `kill.py:105/:128-131` for the gate pattern — that is the **decision-module** kill store, not the metrics gate. Correct reference: `metrics_gate.py` (env TRUTHY polarity + `live_managed_value`).
- **Gauge question (adds value or scope-creep?):** **REJECT for Phase 1** — a `distinct tools observed` gauge adds a second series family, another vocabulary surface, and near-zero decision value for layer-2 candidacy (the ledger answers distinctness exactly). Scope-creep; do not add.
- **Operator writability (W6 🟡):** the family managed key `limits.metrics_tool_usage_enabled` is read via `live_managed_value` but NOT registered in `MANAGED_KEYS` → not operator-writable via the settings surface in v1 (env-only off-switch). **Architect placement ruling:** register BOTH managed keys (`limits.tool_usage_collection_enabled` AND `limits.metrics_tool_usage_enabled`) in **phase 1's** `MANAGED_KEYS` edit (phase 1 already owns `settings_service.py`; phase 2's zero-edit stance on that file is preserved). `config_path=None` (DB/ENV-ONLY) for the family key; see A-7 for the collection key.

## 6. Ruling 5 — FE lazy tab (phase 4, the DELEGATED question): HYBRID RULING

**6.1 The lazy mechanism is FEASIBLE — confidence HIGH (W3, evidence-anchored at every link):**

- Angular **v21.2.5**, esbuild application builder, standalone components, zoneless; `/metrics` is already a lazy route. The in-tree precedent is **VCR `createComponent` + dynamic `import()`** (`app.ts:357-414` — the chat component), NOT `@defer` and NOT router-based.
- **Exact-cover is a COMPILE contract** (`MetricsTabId` derived from the manifest; missing key → TS2739, extra → TS2353). No spec asserts map-value identity, so a **thin static HOST as the map value** breaks zero existing assertions; the lazy pin (map value === host) is purely additive.
- **B4b exclusion is BY CONSTRUCTION, proven from the measure tool's own code** (`fe-t1-build/bundle-baseline.measure.js`): the initial set and route-dep walks use static-import regexes only; the call form `import("chunk.js")` does not match — dynamic targets never enter the fetch set. Corroborated in-tree: the 206,857 B chart.js vendor chunk (a dynamic target) is provably absent from the 184,866 B B4b number. **The heavy body (≈18–24 kB) contributes ZERO B4b bytes.**
- The vendor-static-edge invariant automatically extends: the route chunk's dynamic-target set becomes {chart, tool-usage} — the new chunk's laziness is guarded for free.

**Developer-ready pattern spec (freeze into phase 4):** host = standalone, OnPush, statically imported, declares **ALL FIVE shell inputs** (`state, onState, window, baseline, onRangeChange` — the shell passes the same 5 to every tab; undeclared inputs THROW in dev / silently drop in prod); `ngOnInit`: `import('./tool-usage-tab.component')` → `vcr.createComponent` → `setInput` ×5 → explicit `detectChanges()` (zoneless); `ngOnDestroy`: `ref.destroy()`; loading placeholder + **visible chunk-error state with retry** (never-blank; re-selection re-runs init; module cache makes remount near-instant; mat-tab destroys deselected bodies — polling stops, desired). The auth-off banner lives in the HEAVY body via `MetricsDoorStateComponent` (already in the route chunk via nest-reports — zero new vendor bytes); a host-side banner is impossible because auth posture is a per-request outcome. Spec amendments: `tabs.manifest.spec.ts` census 8→9, ids, labels, custom 1→2 / central 7→8; `metricsCustomTab()` "exactly one custom" prose is now false — amend docstring + pin returns-resource-first; pin the `metrics-range` snap (entering tool-usage from a 15m/1h frame snaps to `7d`); one AOT dev-build pass after template edits. [Correction 2026-11-21, leader-directed: census arithmetic above is wrong — 9 tabs = 2 custom + 7 central; central is UNCHANGED 7 → 7 (the new tab is custom, binds no family).]

**6.2 BUT B4b does NOT stay flat — and cannot, for ANY 9th-tab variant (🔴 arithmetic):** static residue into the route chunk = manifest entry ≈ 125 B + map line/import ≈ 60 B + host ≈ 0.8–1.1 kB min ≈ **~1.0–1.3 kB, versus 134 B headroom. Even a zero-byte host breaches (~185 B minimum).** Expected measured B4b under lazy ≈ **185,800–186,200 B**. The plan's "lazy keeps B4b within the frozen bound" premise is therefore FALSE; the tester already disclosed this wall (2026-11-05 G8: any further FE addition to this route breaches).

**Ruling:** keep LAZY as primary — it shrinks the unavoidable ask from ~+20 kB to ~+1 kB — and **re-baseline `threshold_B4b_route_fetch_set_lazy_only_bytes` 185,000 → 188,000** (expected value + ~2 kB margin; records the 184,866 provenance). The static-only fallback needs ≈205–210 kB and is strictly worse — rejected. If the leader refuses ANY re-baseline, the honest fallback is **defer phase 4**, not "go static." This also resolves the plan's OQ2: the re-baseline is required for the residue, not because lazy failed. **→ Leader decision required (§12).**

**6.3 Slot ruling: `custom` VALIDATED.** `MetricsCentralTab` requires `familyIds: NonEmpty<…>` (compile-forced); tool-usage binds no family (data = domain door). Forcing a family binding to reach the central slot would corrupt the roster census and lie to the registry SSOT. A second custom tab is type-legal (the "exactly one" constraint is prose/spec-count only).

## 7. Ruling 6 — Digest + seeder (phase 3): AMEND MATERIALLY — three structural defects + lifecycle gaps

The architecture (scheduler row + deterministic SQL rollup + committed weight artifact) stands. **As written, the schedule would never fire.** All fixes are local:

- **D-1 (🔴) Seeder repo-readiness:** the seeder call at `manager.py:~1166` passes `self._project_repository` — that attribute does not exist until `:2113` (source repo not until `:1779`). Fix: like the memoir seed, the seeder **builds its own repos from `self._engine`** (`create_project_repository` / `create_source_repository`, `create_tables=False`), or the hunk moves to ≥ `:2113`. Boot-ordering claim (seed before lifespan `start_all()`, `manager.py:16929-16945`) is otherwise correct; sync-on-boot + WARNING-never-propagate is exactly the memoir pattern (`seed.py:128-130`).
- **D-2 (🔴) STOPPED rows are not started by `start_all`:** the plan seeds `status=STOPPED` (repo default) and claims `start_all()` covers boot. The registry skips stopped/cancelled rows; the create-path compensation the plan bypasses (`scheduling_service.py:822-829`) is exactly what makes a fresh row startable. Fix: seed the row in the state the compensated create-path produces (startable/registered), or invoke the compensation. GC predicate (verified): collectable = scheduler ∧ `enabled==False` ∧ status ∈ {stopped, cancelled} ∧ age > 14 d — an **enabled row is never collectable**, so the core GC-safety claim holds once the row actually starts (status → running).
- **D-3 (🔴) Adapter config shape:** the adapter reads `config["schedule"]` (cron) — NOT `when`. The plan's payload-shaped config (`when='08:00'`) never fires. Persist the adapter shape: `{agent, message, timezone, recurrence, local_time, schedule:"0 8 * * *", instance_mode, project_id}`.
- **D-4 (🟡) Idempotency hardening:** `source_configs.name` has NO unique constraint — the plan's get-or-create is a logical key only. Harden with deterministic `source_id = uuid5(NAMESPACE_DNS, "tool-usage-daily-digest")` (PK collision makes the second INSERT a no-op) — mirrors `ensure_system_default_project`.
- **D-5 (🔴 disable-lifecycle) Opt-out that survives re-seed:** operator disables the row → GC reaps it at 14 d → next boot re-seeds `enabled=True` — the operator's opt-out is silently reversed, and there is no house precedent to copy. Fix: add managed key `limits.tool_usage_digest_seeding_enabled` (default ON; registered in phase 1's settings edit or phase 3's own — one `MANAGED_KEYS` row, `config_path=None`); seeder no-ops when OFF. Additionally the seeder must no-op on an EXISTING row in ANY state (get-by-id-or-name first), narrowing re-seed to the post-GC window only.
- **D-6 (🟡) Destination:** `manager.workdir` fallback is a **phantom** (never assigned anywhere); the system-default project carries no `main_directory`, so `_get_project_workdir` returns None for the seeded schedule. Fix: pin an **explicit canonical root** (config/env constant) — digest path `<root>/.agents/shared/planning/tool-usage-metrics-p1/digests/YYYY-MM-DD.md`, same-day re-run **overwrites** (idempotent). This also removes the cross-project write hazard for on-demand jober/leader calls. Live tree has no `.git` → daily files are not git pollution; 365 small files/year is fine.
- **D-7 (🟡) Window semantics (frozen):** `D` = UTC date of the fire (08:00 ICT = 01:00 UTC, ICT has no DST — cron evaluated in the row's timezone, verified `:858-880`). **"last-24h" := complete previous UTC day (`D−1`); "cumulative-week" := `[D−7, D−1]` complete UTC days.** Deterministic given (fixture, run-date); matches the day-granular ledger; avoids the non-deterministic partial-day. Disclose the deliberate divergence from the door's `calls_24h = today+` (different surface, vaguer by design).
- **D-8 (🟡) Holder set / jober:** accept `scheduling` category (ari+leader+jober). Jober inheritance is **benign**: the digest tool is SQL read + one deterministic file write, no LLM call, ~zero tokens, overwrite-idempotent — and with D-6 the destination no longer depends on the caller. Disclose; exact-holder-set (new category + meta.json edits) stays out of scope. `instance_mode='new_instance'` is the house default (all 14 existing scheduler rows); ~365 COMPLETED instance rows/year is normal house behavior, not sprawl.
- **D-9 (🟡) Rollback disclosure (freeze the text):** the seeded row persists after rollback; the old binary starts it and each 08:00 fire degrades to an ari error turn (tool missing — no crash, no data loss). Operators may disable the row pre-rollback; note GC reaps disabled rows at 14 d and re-upgrade re-seeds (unless D-5's flag is OFF). This keeps `rollback_safe=true` honest: fix-loss-only refers to code/data, plus this disclosed noise channel.
- **Determinism tests:** extend to the T3 file-write path (byte-identical modulo the generated-at header), not just the rollup. Weight join + 230-fallback (committed artifact, no live-registry reads) validated as the right determinism mechanism.

## 8. Ruling 7 — Migration + PG evolution (D5): VALIDATED, rollback-safe HOLDS

- Register-before-`create_all` pattern confirmed (`manager.py:1001-1072`; `create_all` creates only missing tables — manager comment :8840). Own `.sql` matches the memoir format; runner SQLite-only early-return confirmed (`runner.py:837-842`); no `_ensure_postgres_columns` mirror for a brand-new table — correct (that path evolves existing tables). No PRECONDITION header needed (no version-sensitive DDL). G3's dual-path gate (throwaway SQLite apply + PG create_all grep) is the right verification.
- **Rollback-safety verified against the scary precedent:** `MANAGED_KEYS` is a **code-defined registry** — on rollback the OLD binary simply lacks the new keys; `_read_managed_rows` skips unregistered `app_settings` rows with a WARNING, and `apply_db_settings_overrides` now has per-key fail-soft (`settings_service.py:1477-1505`) — the v0.31.8 boot-crash class is NOT reproducible at base. Residual nit → **A-7 (🟡):** the collection key's `config_path="limits.tool_usage_collection_enabled"` has NO backing `LimitsConfig` field → the apply is skipped with a permanent boot WARNING and an ambiguous read path. Fix (pick one, recommend the first): **(a)** add `LimitsConfig.tool_usage_collection_enabled` + dotted `config_path` + a registration-shape regression pin (mirrors `metrics_enabled`, which has both; costs one `daemon/config.py` edit — add it to phase 1's file ownership); or **(b)** `config_path=None` (DB/ENV-ONLY, `wedge_notice_emission_enabled` shape) if consumption is purely via the managed-value getter. Do NOT ship the key with a dotted path and no field.
- The only rollback residue is the scheduler row (D-9) — disclosed, not blocking.

## 9. Ruling 8 — Collision risk + the I1 proof spec (reframed on corrected evidence)

**Premise correction (W4, git-verified):** `feature/skill-system-defects` is **NOT unmerged** — `868c6c616f` is an ancestor of base (merged via `17e100532`); it touches only `skill_evolution_service.py` + its test, **not manager.py**. Its branch-span manager.py hunks (`f3ac558d0..1834f5c11`) sit at ~4815–4828 and ~7986–8067 — **zero overlap** with the three planned hunks (h1 ~1006–1020, h2 ~1145–1148, h3 ~1166+). No file-level collision with the plan's set either (graph.py, settings_service.py, routers/__init__.py, tools/instance.py: zero).

**The I1 proof owed at gate time (spec — freeze into phase 5):**
1. **Enumerate our hunks exactly:** `git diff <base>..HEAD -- daemon/manager.py` → assert EXACTLY three hunks with their `@@` ranges, mapped to h1/h2/h3 (h1 model registration before create_all; h2 flusher startup + kill-switch wiring; h3 seeder call — h3 may land ≥ :2113 per D-1; the enumeration, not the line number, is the claim).
2. **Internal disjointness:** assert the three hunks are mutually disjoint (region enumeration — trivial but explicit).
3. **Gate-time drift re-check (the real risk):** list every branch merged into `origin/latest` AFTER base that touches `daemon/manager.py`; for each, diff its hunk ranges against ours — assert no overlap. Re-run at execution time; never cite this document's census (anchors drift).
4. **File-set sweep:** repeat for the plan's other shared files (graph.py, settings_service.py, config.py, routers/__init__.py, tools/instance.py, tools/_tool_registry.py) — assert zero foreign overlap or disclose.
5. **Upstream freshness:** verify base == origin/latest at merge time (house FINALIZE rule: moved ⇒ STOP).

## 10. Ruling 9 — Cross-cutting

**10.1 Door security posture (D6): VALIDATED + pins.** `_require_auth_on` reads `app.state.auth_config`; `None` ⇒ 503 `AUTH_NOT_CONFIGURED` as the first statement — **fail-closed under the deployed auth-OFF state confirmed by walk-through** (scope decorator is inert when OFF; the 503 fires before any repo read). No bypass found (no localhost hole, no preflight data, openapi is shape-only — same as every existing door; state it). It is **copy-paste across 5 routers** (no shared helper) → pin a **replication-invariant gate**: the new router's `_require_auth_on` executable body must byte-match the metrics door's (or extract a shared helper — cleaner but out of phase-1 scope). DB errors → typed 503 `tool_usage_unavailable`, redacted server-side logging (`_redact.redact_exc_str`), never raw exception text.

**10.2 Project scoping:** freeze the rule — **"`project_id` is a filter, not a boundary"** under an explicit single-operator / single-tenant threat model (projects are organizational units within one operator's daemon). Any `maintenance:read|metrics:read` holder sees all projects. If multi-tenant ever arrives, add project-membership filtering then.

**10.3 Granularity split (frozen):** central family = aggregate-only (67×2 series, no entity labels — B-2); domain door = per-tool/per-project/per-agent detail under scopes; FE = the door's rendering. Per-agent exposure acceptable for single-operator (behavioral fingerprinting is a multi-tenant concern; the operator already sees sessions/logs). Coherent as designed.

**10.4 Abuse bounds:** worst-case `/summary` ≈ 340 kB (268 tools × ~30 agents nested). No pagination for v1; **freeze a 1 MB response-size test** as the guard + **cap nested agents at top-50 per tool**; revisit at >1,000 tools. Auth-OFF ⇒ 503 bounds unauthenticated volume.

**10.5 Test architecture (what is mutation-pinned vs behavior-tested):**

| Mutation-pinned (regression pins — the contract fences) | Behavior-tested (the semantics) |
|---|---|
| NAMES-ONLY schema (no args/argv/content/tool_call_id/instance/session column can be added without a pin trip) + no-args sink signature | Tap observation-only: sink raise → tool call unaffected; ok-flag from ToolMessage.status |
| Upsert coalescing dual-dialect (two same-PK deltas → one row summed, PG + SQLite) | Flusher: drain-before-dispose ordering; kill-switch tail-flush; emergency-flush at bound |
| `MANAGED_KEYS` registration shape (field + dotted path per A-7) | Door: shapes, clamps [1,90], `__none__` filter, typed error vocabulary, 503 under auth-OFF |
| Auth replication invariant (body-equality vs metrics door) | Digest determinism (byte-identical file modulo header) + window D−1 / [D−7,D−1] |
| Exact-cover: map value === host; census 8→9; custom 1→2/central 7→8; `metricsCustomTab` returns-resource; snap-to-7d | FE: sort by calls_7d, zero/near-zero distinct, per-agent expand, ONE banner, chunk-error state | [Correction 2026-11-21, leader-directed: census arithmetic above is wrong — 9 tabs = 2 custom + 7 central; central is UNCHANGED 7 → 7 (the new tab is custom, binds no family).]
| Seeder: uuid5 idempotency (seed twice → one row); adapter config shape carries `schedule`; startable status; opt-out flag no-op | Counting contract: replay re-counts (at-least-once accepted) — document-as-test |
| Band 134≤160, `VOCABULARY_VERSION==11`, measured-total 625 ≤ 2000 | Migration: throwaway-SQLite apply (G3) additive-only |

**10.6 Frozen-contract list (amended superset of plan-overview §"Frozen contracts" — freeze THESE, never counts):** (1) ledger schema + NAMES ONLY + sentinel `__none__`/`'unknown'` (amended); (2) sink signature (no args slot) + 256-char tap choke; (3) door shapes + replicated auth posture + replication-invariant pin + filter-not-boundary rule; (4) off-loop contract as amended (map+dirty-set, 30 s/500-key, never-drop, tail-flush, explicit drain placement, main-loop-only emits); (5) counting contract (at-least-once, verbatim §3); (6) F24 closed labels + band + v11 + measured-total convention; (7) B4b lazy-only ≤ **188,000 (pending leader ruling)** + host-pattern pins; (8) gate order G1→G7 + NO-LIVE-FLIP; (9) digest windows (D−1 / [D−7,D−1]) + determinism + explicit canonical root + same-day overwrite; (10) seeder idempotency (uuid5) + opt-out flag + rollback disclosure text.

## 11. Approach comparison (the three genuinely contested choices)

| Choice | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| Tap: tools-node wrapper (A) vs factory (B) vs subclass (C) | A lowest | A full-coverage | A highest (house precedent) | A lowest (B undercounts, C version-fragile) | A ~1 repo-get/batch | **A** |
| Buffer: event queue vs counter map | Equal | Map outage-proof | Equal | Map eliminates drop-bias + PG batch hazard | Map cheaper (~ns) | **Map** |
| FE: lazy host vs static vs defer | Lazy +~1 kB residue | — | Lazy first-of-pattern (pinned) | Static breaches by ~20 kB; defer loses the deliverable | Lazy ≈ +1.0–1.3 kB | **Lazy + re-baseline 188k** |

## 12. Decisions Pending (leader), Gaps, Open Questions

**Decisions Pending (leader must rule before implementation):**
1. **B4b re-baseline (R-08):** accept `threshold_B4b…lazy_only_bytes` 185,000 → **188,000** (expected 185.8–186.2k; residue arithmetic makes ANY 9th tab breach the old bound — lazy minimizes the ask to ~1 kB). Refusal ⇒ defer phase 4 (static fallback rejected: ~205–210 kB, strictly worse).
2. **OQ1 (stage guard):** confirm the house `stage.sh live` form (binds `ENSEMBLE_UPGRADE_LIVE=1` as the releases-dir write guard — NOT a flip; `promote.sh` never runs). The commission's own constraint text already reads this way; default stands unless overridden.

**Gaps surfaced (plan amendments owed — none blocking the architecture):** A-1 (T5 scope fix), A-3 (256 choke), A-5* (flusher amendments), A-6 (batch coalescing pin), A-7 (managed-key shape), project sentinel rename, phase-2 arithmetic/citation corrections + family-key registration placement, D-1…D-9 (phase-3 activation + lifecycle), phase-4 spec amendments (5-input host, census, `metricsCustomTab`, snap pin), I1 reframe. The planner should fold these as superseding decision-ledger entries (D-numbers appended, never edited in place).

**Open Questions (architecture-tolerant, answered by the data or at execution):** actual per-day key cardinality (the 7-day data answers it; bounds hold at 10× estimate); whether MCP tool names can ever be templated with ids (mitigated by A-3); deployed auth state (walk-through conclusion holds either way).

---

### Evidence appendix — worker instance IDs
W1 `98d904e1-b82b-4d4f-8f22-57ab0eadfcb3` (structural-design, tap) · W2 `14461184-31f3-49dc-901b-1f2e7d17c43b` (resilience-design, flusher) · W3 `13438c29-3c0b-4593-8107-e5d092e7f87f` (structural-design high-tier, FE) · W4 `eb7fd799-f050-4912-a31e-2cc037449e62` (data-flow-design, ledger/migration/collision) · W5 `c3271c17-05a7-43ab-a393-0893d3b2af01` (resilience-design, seeder/digest) · W6 `3c653166-caa4-45a3-8e2c-c6e68f14f5c4` (security-design, door/F24).
