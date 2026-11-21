# Phase 3: Daily 08:00 ICT Digest + Scheduler Delivery (boot-time seeder)

Lane: **tool-usage-metrics-p1** (commission ENSEMBLE-37 / TokenEconomizer adoption — Phase 1 measurement slice)
Phase owner: plan-worker (digest + scheduler-delivery phase)
Status: Draft for the planner's review
Consumes (ZERO edits): Phase 1's frozen ledger `tool_usage_daily` (PK `(day, project_id, tool_name, agent_id)`, `agent_id` sentinel `'unknown'`, `call_count BIGINT`) + its `/api/tool-usage/*` domain door.
Worktree (READ-ONLY source of truth for every anchor below): `/home/nea/ensemble-src-wt-tool-metrics-p1` @ `0b5624dc9` (branch `feature/tool-usage-metrics-p1`).
Framing: `.agents/shared/planning/tokeneconomizer-study/writing-block-tiering.md` §3–§4 (ranking key = schema token weight × call frequency; zero/near-zero = strongest layer-2 candidates; per-agent allow-list intersection matters).

---

## 0. Dispatch mode & file ownership

**Mode 1 — Greenfield.** No digest tool, rollup module, seed module, or seeded schedule exists yet.

**Single-ownership invariant (REJECT-grade).** Phase 3 owns EXACTLY:

| Owned file | Edit |
|---|---|
| `daemon/tools/tool_usage.py` (**NEW**) | the `tool_usage_digest` @tool wrapper + factory |
| `daemon/tools/_tool_registry.py` | `CATEGORY_MODULES["scheduling"]` → list incl. the new module (registry-entry step) |
| `daemon/tools/instance.py` | import + construction hunk near `:7040-7043` |
| `daemon/services/tool_usage_digest.py` (**NEW**) | deterministic SQL rollup + markdown rendering |
| `daemon/repositories/source/seed.py` (**NEW**) | idempotent schedule seeder (memoir-seed layout) |
| `daemon/manager.py` | ONE hunk: import + seeder call immediately after `manager.py:1166` (a THIRD hunk, disjoint from Phase 1's two) |
| `tests/**` for phase 3 (digest unit + determinism + tool-registration + seed idempotency + GC-safety) | NEW/owned |

**Owns ZERO edits to:** `daemon/graph.py`, the ledger repo, the routers, `daemon/checkpoint_metrics.py`, `frontend/**`, any Phase-1 file, `agents/*/meta.json` (holder set is inherited — see §3.6).

---

## 1. Objective

After ~7 days of ledger data, the question "which tools go to layer 2 per project" is answerable from a durable artifact alone: a deterministic daily 08:00 ICT job writes a compact markdown digest with per-project × per-tool counts (last-24h + cumulative-week), sorted so high-frequency vs zero/near-zero candidacy is OBVIOUS, plus a zero/near-zero section (allow-listed but never called) and a per-agent breakdown. Outcome test: with a fixed ledger fixture + date, the digest tool emits byte-identical markdown (modulo a generated-at header); a seeded `source_configs` row is idempotent (seed twice → one row), enabled/autostart, GC-sweep-safe, and activates automatically at promote via the boot seeder.

---

## 2. Scope

### In scope
- A purpose-built read-only tool `tool_usage_digest` (three-step registration seam).
- A deterministic query/rollup module over `tool_usage_daily`.
- A durable markdown digest under the **runtime-canonical** planning dir (not this worktree).
- A boot-time seeder creating the daily schedule row (agent `ari`, `Asia/Ho_Chi_Minh`, `daily 08:00`, durable job-queue routing).
- Determinism + idempotency + GC-safety tests.

### Out of scope
- Prompt-driven rollup — the scheduled message is MINIMAL and deterministic; the tool does all work.
- The FE tab (Phase 4), the central family (Phase 2), the ledger/tap/door (Phase 1).
- Any live contact: no daemon restart, no prod/env writes, no promote. The seeder activates the schedule **at promote** (boot), not now.
- New tool category / vocab bump — ruled out (§3.6).

---

## 3. FROZEN CONTRACTS (interfaces / invariants / bounds — NEVER node counts)

1. **Ledger read-only.** `tool_usage_daily` is Phase 1's frozen table: PK `(day, project_id, tool_name, agent_id)`, `agent_id='unknown'` sentinel, `call_count BIGINT`. The rollup issues SELECTs only; no writes, no schema edits.
2. **Digest content (frozen shape).** Per project: last-24h + cumulative-week per-tool counts. Primary sort = `schema_token_weight × frequency DESC`. A ZERO/NEAR-ZERO section = tools present in agent allow-lists but 0 calls in window (strongest layer-2 candidates). A per-agent breakdown (allow-list intersection visibility).
3. **Weight join + fallback (see §10 A1 — correction).** `fc9_baselines.json` carries ONLY aggregate averages (`51218/223 ≈ 230` tok/tool pre-narrowing at `:125`/`:139`; `19933/74 ≈ 269` post-narrowing at `:167`/`:169`) — it has **no per-tool weight table**. Ruling: build a committed per-tool weight artifact from the fc9 measurement point (`convert_to_openai_tool` + `estimate_tokens`, sort_keys+compact form, `fc9_baselines.json:formula`/`serialization_form_note`); join `tool_name → weight` from it; **fallback for tools absent from the baseline = the 230-average** (documented, deterministic). Never compute weights at digest time from a live registry (that would break determinism).
4. **Determinism invariant.** Same ledger fixture + same date → byte-identical output modulo the generated-at header. Pin a determinism unit test. No clock/random/live-registry reads in the rollup body.
5. **Destination (runtime-canonical).** `<project_workdir>/.agents/shared/planning/tool-usage-metrics-p1/digests/YYYY-MM-DD.md`, where `project_workdir` resolves from the calling instance's `project.main_directory` (house pattern `_get_project_workdir`, `instance.py:1531-1548`; fallback `manager.workdir`, `doc_write.py:245-262`). The runtime tree is the LIVE checkout (`/home/nea/agents-ensemble` per project metadata), **NOT this worktree**. Optional board-note pointer alongside the durable file.
6. **Tool registration seam (three steps).** (a) `@tool` + `@register_tool_category("scheduling")` decorator in `daemon/tools/tool_usage.py`; (b) registry entry — add `"daemon.tools.tool_usage"` to `CATEGORY_MODULES["scheduling"]` (currently a single string at `_tool_registry.py:1315`; the scanner accepts a list, `:1085`); (c) construction — factory import + `tools.extend(...)` in `daemon/tools/instance.py` near `:7040-7043` (the scheduling construction site, `scheduling.py:132-163` factory captures caller ids at factory time). Pattern per `instance.py:7035-7043`: decorator-only registration is SILENTLY INVISIBLE — the extend is load-bearing.
7. **Holder set (recommend ari+leader, rationale).** Reuse the EXISTING `"scheduling"` category — held by `ari`, `leader`, `jober` (`agents/ari/meta.json:34`, `agents/leader/meta.json:38`, `agents/jober/meta.json:11`). Rationale: (a) the seeded schedule invokes `agent='ari'`, so the tool MUST be in a category ari holds; (b) reusing an existing category adds NO new tool-category value → NO `VOCABULARY_VERSION` bump / artifact regen (keeps Phase 2's no-vocab-bump invariant); (c) `leader` gets it for oversight/manual invocation; `jober` inherits it benignly. Intended callers = ari + leader. If the architect wants EXACTLY ari+leader, that requires a new category + `meta.json` edits (out of scope) — see §9 open question.
8. **Seeded row (frozen config).** `source_type='scheduler'` (`scheduling_service.py:105`); `config`: `agent='ari'`, `message` = the minimal deterministic digest instruction, `timezone='Asia/Ho_Chi_Minh'`, `recurrence='daily'`, `when='08:00'` → service builds cron `"0 8 * * *"` (`scheduling_service.py:787-790`), `instance_mode='new_instance'` (`:117/:165-168/:798-802`), `project_id` = system default project (§3.9), `priority` default 5; `enabled=True`, `autostart=True` (`scheduling_service.py:818-819`); `status` initialized `STOPPED` (`source/repository.py:165`).
9. **project_id resolution (sequencing correction — §10 A2).** `constants.SYSTEM_DEFAULT_PROJECT_ID` is set at `api.py:2979` (lifespan), AFTER the manager-boot seed site (`manager.py:1166`). Seeder must call `manager._project_repository.ensure_system_default_project()` itself (idempotent get-or-create; deterministic `uuid5(NAMESPACE_DNS, "__system_default__")`, `project/repository.py:292/:304`) to obtain the id — do NOT read the not-yet-set constant. This id enables durable job-queue routing (`scheduler.py:305-315`, `:423/:429`).
10. **Boot activation + GC-safety.** Seeder runs during manager init (`manager.py:1166`), so the enabled+autostart row exists when `source_registry.start_all()` runs in the lifespan (`manager.py:16945`) → registered+started with NO restart. (The create-path `_rebuild_register_start` compensation at `scheduling_service.py:822-829` is bypassed by the direct repo insert, but `start_all` covers boot.) GC-safe: `gc_find_candidates` predicate requires `enabled == False` (`source/repository.py:735-765`, `sources_gc_service.py:11-13`) — an ENABLED row is never collectable.
11. **Seed failure tolerance.** Like `memoir/seed.py:12-28/:80-83`: failures log WARNING and NEVER propagate; content failure must not become an availability failure. Idempotent get-or-create keyed on stable identity (name `"tool-usage-daily-digest"` + `source_type='scheduler'`).

---

## 4. Tasks (file-level claims + per-task verification)

| # | Task | Owns | Depends On | Acceptance / verification |
|---|---|---|---|---|
| T1 | Build `daemon/services/tool_usage_digest.py`: `SELECT` last-24h + cumulative-week per `(project_id, tool_name)` from `tool_usage_daily`; join per-tool weight (§3.3); compute `weight × frequency` sort; build zero/near-zero (allow-list intersection) + per-agent sections; render deterministic markdown (generated-at header isolated). | `daemon/services/tool_usage_digest.py` | none (consumes frozen ledger) | Unit test with fixed fixture + date → expected markdown; determinism test byte-identical modulo header. |
| T2 | Build the committed per-tool weight artifact from the fc9 measurement point; document the join + 230-average fallback (§3.3, §10 A1). | artifact under `.agents/shared/planning/tool-usage-metrics-p1/` (or module constant) | T1 | Every ledger `tool_name` resolves to a weight; absent tools use 230; no live-registry read in rollup. |
| T3 | Build `daemon/tools/tool_usage.py`: `tool_usage_digest` @tool + `create_tool_usage_tools_if_available(...)` factory; calls T1's rollup, writes to the §3.5 path, returns a short status string. | `daemon/tools/tool_usage.py` | T1 | Tool callable; writes exactly one `YYYY-MM-DD.md`; no prompt-driven rollup. |
| T4 | Three-step registration: (a) decorators in T3's module; (b) `CATEGORY_MODULES["scheduling"]` → list incl. `"daemon.tools.tool_usage"`; (c) factory import + `tools.extend(...)` in `instance.py` near `:7040-7043`. | `_tool_registry.py`, `instance.py` | T3 | Tool discovered by name (AST scan) AND constructed for ari/leader/jober; frozen-name drift test GREEN. |
| T5 | Build `daemon/repositories/source/seed.py`: `seed_tool_usage_digest_schedule(engine, project_repo)` — get-or-create by `(source_type, name)`, build §3.8 config, `enabled=True/autostart=True`, failures never propagate, idempotent. | `daemon/repositories/source/seed.py` | none | Seed twice → exactly one row (memoir-test precedent `test_memoir_persona_seed.py:107-111`); failure → WARNING, no raise. |
| T6 | Add the THIRD `manager.py` hunk: import + call immediately after `manager.py:1166`, passing `self._engine` and `self._project_repository`; require line-disjointness from Phase 1's two hunks (§9). | `daemon/manager.py` (one hunk) | T5 | Seed runs at boot; row present before `start_all()` (`manager.py:16945`); no Phase-1 hunk overlap. |
| T7 | Tests: digest unit + determinism (T1), weight join/fallback (T2), tool-registration (T4), seed idempotency + failure (T5), GC-safety (enabled row not collectable, `repository.py:765`), boot ordering (T6). | `tests/**` | T1–T6 | All GREEN in worktree, dev-fenced env, zero live contact. |
| T8 | Appendix: document the one-line post-promote arm fallback (`task_schedule` / `POST /api/schedules`) in the plan for the architect-override case (§11). | plan doc only | none | Both activation options executable. |

**Ordering:** T1→T2→T3→T4 (chain); T5 independent → T6 → T7 (fan-in); T8 parallel.

---

## 5. Coupling

- **Tight with Phase 1** — reads the frozen ledger + domain door; zero Phase-1 edits. Digest content must match the ledger's `(day, project_id, tool_name, agent_id)` identity.
- **Tight with Phase 2** — both must agree the per-tool signal comes from the LEDGER, not the central family. Phase 2's no-vocab-bump ruling depends on Phase 3 reusing the `"scheduling"` category (§3.7).
- **Loose with Phase 4 (FE)** — the FE tab reads the domain door; the digest is a separate durable artifact. No shared contract.
- **Independent of the seeder** except the schedule's `agent='ari'` must hold the tool (§3.7).

---

## 6. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | fc9 has no per-tool weights → ranking key ill-defined; assuming uniform weight hides layer-2 signal. | High | HIGH (confirmed) | §3.3 committed per-tool weight artifact + documented 230 fallback; flag to architect (§9). |
| R2 | Seeder reads `SYSTEM_DEFAULT_PROJECT_ID` before it is set (None) → wrong/absent project_id → non-durable routing. | High | Medium | §3.9: call `ensure_system_default_project()` directly; idempotent + deterministic uuid5. |
| R3 | Seeded row not started (bypassed `_rebuild_register_start`) → no 08:00 fire. | High | Low | §3.10: boot `start_all()` registers+starts enabled/autostart rows; test boot ordering. |
| R4 | New tool category would trigger vocab bump (conflicts Phase 2). | Medium | Medium | §3.7 reuse `"scheduling"`; zero new category value; zero meta.json edits. |
| R5 | Digest non-determinism (live weights/clock/random) → byte-diff test fails. | Medium | Low | §3.4: committed weights, no live reads, generated-at header isolated; pin determinism test. |
| R6 | Runtime path resolves to the WRONG checkout (worktree vs live install). | Medium | Medium | §3.5: resolve via `project.main_directory` (house pattern); document canonical live path; test path resolution. |
| R7 | manager.py hunk collides with Phase 1's two hunks. | Medium | Low | §0 enumerate my single hunk at `:1166`; require integration-time line-disjointness check (§9). |

---

## 7. Success criteria

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| S1 | Deterministic digest | fixed fixture + date, two runs | byte-identical modulo header |
| S2 | Content shape | per-project 24h+week counts, weight×freq sort, zero/near-zero + per-agent sections | all present |
| S3 | Weight join/fallback | every tool has weight; absent → 230 | exact |
| S4 | Tool registration | discovered by name AND constructed for ari/leader | pass, drift test GREEN |
| S5 | Seed idempotency | seed twice | exactly one row |
| S6 | GC-safety | run GC sweep with seeded enabled row | row survives (not collectable) |
| S7 | Boot activation | seed at init → row started by `start_all` | enabled+autostart, started |
| S8 | Zero live contact | test run | no prod/env writes, no restart |

---

## 8. Test strategy

- **Unit:** rollup math (24h/week windows, sentinel `'unknown'`, sort key), weight join + 230 fallback, markdown shape sections.
- **Determinism:** two runs on identical fixture+date → byte-identical (header normalized); mutation: inject a live-registry weight read → RED.
- **Registration:** decorator-only is invisible (construction hunk required) — mutation: drop the `tools.extend` → tool not constructed → RED; frozen-name drift GREEN.
- **Seed:** idempotency (twice→one), failure tolerance (WARNING, no raise), config field assertions (timezone/cron/agent/project_id/enabled/autostart), GC-safety (enabled row not collectable per `repository.py:765`).
- **Boot ordering:** seed row present before `start_all` (manager.py:1166 vs :16945).
- **Dev-fenced:** all tests in worktree; POSTGRES_* scrubbed per variable (never `env -u POSTGRES_*` — silent no-op, critical note); zero live contact.

---

## 9. Cross-phase interface notes / open questions (for the architect)

1. **Per-tool weights (R1).** `fc9_baselines.json` has NO per-tool weights — only ~230/~269 averages. The plan rules a committed per-tool weight artifact + 230 fallback. If the architect wants true per-tool weights, a separate measurement lane must produce that artifact (out of scope). **Open question:** accept committed-artifact + 230 fallback, or commission a per-tool weight measurement?
2. **Holder set exactness (§3.7).** Reusing `"scheduling"` grants the tool to ari+leader+jober. **Open question:** is jober's inherited access acceptable, or must the holder set be exactly ari+leader (requiring a new category + `meta.json` edits + a vocab amendment, out of scope)?
3. **manager.py hunk disjointness (R7).** Phase 1 owns two `manager.py` hunks (ledger tap wiring); this phase adds a third at `:1166`. **Open question:** confirm the two sets of hunks are line-disjoint at integration (planner sweeps).
4. **Digest destination confirmation (§3.5).** Canonical target is the LIVE checkout's planning dir (`/home/nea/agents-ensemble/...`), resolved via `project.main_directory`. **Open question:** confirm the seeded schedule's project (system default) has `main_directory` set to the live checkout; if not, the fallback `manager.workdir` applies — document which.

---

## 10. Assumptions

- **A1 (fc9 has no per-tool weights).** Verified by walking all keys of `fc9_baselines.json`: only aggregate `tool_schema_tokens` / `resolved_toolset_size` and the `plane_sync_project` single-tool example (`65`/`84` tokens) exist; no per-tool weight table. The ~230 average is `51218/223` (`:125`/`:139`), ~269 is `19933/74` (`:167`/`:169`).
- **A2 (constant sequencing).** `SYSTEM_DEFAULT_PROJECT_ID` set at `api.py:2979` (lifespan) — after `manager.py:1166`. Seeder must derive the id itself (`ensure_system_default_project()`, `project/repository.py:292/:304`).
- **A3 (start_all covers boot start).** `start_all()` loads all enabled adapters from DB and schedules autostart (`sources/registry.py:287-321`); called at `manager.py:16945` during `start_sources`.
- **A4 (scheduling category holders).** `ari`/`leader`/`jober` all have `"scheduling"` in tools.allow (meta.json lines 34/38/11). Reusing it grants the tool to all three; no new category value → no vocab bump.
- **A5 (cron build).** `recurrence='daily'` + `when='08:00'` → `"0 8 * * *"` at `scheduling_service.py:787-790`; DST-safe next-fire via `compute_next_cron_fire` (`util/tz.py:529`).

---

## 11. Appendix — alternative activation (architect override)

Primary delivery = boot-time seeder (automatic at promote, per commission). If the architect overrides to a one-line post-promote arm, the equivalent manual step is:

```
POST /api/schedules  { "label":"tool-usage-daily-digest", "agent":"ari",
  "message":"Call tool_usage_digest for the previous day and write the durable digest.",
  "timezone":"Asia/Ho_Chi_Minh", "recurrence":"daily", "when":"08:00",
  "instance_mode":"new_instance", "project_id":<system default project id> }
```

(or the `task_schedule` tool held by ari/leader/jober, `daemon/tools/scheduling.py`). Both options are executable; the plan defaults to the seeder.

---

## 12. Phase exit gate

Phase 3 is DONE when: (1) `tool_usage_digest` registered via the three-step seam and callable by ari/leader; (2) rollup deterministic with frozen content shape + weight join/fallback; (3) digest written to the runtime-canonical path; (4) seeder idempotent, enabled/autostart, GC-safe, boot-ordered; (5) all tests GREEN in worktree with zero live contact; (6) open questions in §9 surfaced to the architect; (7) ZERO edits to Phase-1 files / graph.py / routers / checkpoint_metrics / frontend / meta.json. No commit, no promote, no live contact — the planner sweeps + commits.
