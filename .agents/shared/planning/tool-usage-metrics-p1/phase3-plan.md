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
| `daemon/manager.py` | ONE hunk: import + seeder call. Seeder builds its own repos from `self._engine` (D-1) OR the hunk moves to ≥ `:2113`; boot-ordering (seed before lifespan `start_all()` at `:16929-16945`) stays correct. Disjoint from Phase 1's two hunks. |
| `tests/**` for phase 3 (digest unit + determinism + tool-registration + seed idempotency + GC-safety) | NEW/owned |

**Owns ZERO edits to:** `daemon/graph.py`, the ledger repo, the routers, `daemon/checkpoint_metrics.py`, `frontend/**`, any Phase-1 file, `agents/*/meta.json` (holder set is inherited — see §3.7) (review fix U12: was §3.6, stale).

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
- New tool category / vocab bump — ruled out (§3.7) (review fix U12: was §3.6, stale).

---

## 3. FROZEN CONTRACTS (interfaces / invariants / bounds — NEVER node counts)

1. **Ledger read-only.** `tool_usage_daily` is Phase 1's frozen table: PK `(day, project_id, tool_name, agent_id)`, `agent_id='unknown'` sentinel, `call_count BIGINT`. The rollup issues SELECTs only; no writes, no schema edits.
2. **Digest content (frozen shape).** Per project: last-24h + cumulative-week per-tool counts. Primary sort = `schema_token_weight × calls DESC`, with explicit secondary sort keys for cross-dialect byte-determinism (U6): after `(weight × calls) DESC` → `tool_name ASC`, then `project_id ASC` (and `agent_id ASC` inside per-agent sections) — ASCII/UTF-8 collation, no locale-dependent collation. Ties WILL occur (the 230-average fallback weight makes many tools tie) and PG vs SQLite default tie ordering differs, so the secondary keys are load-bearing for byte-identical output. A ZERO/NEAR-ZERO section = tools present in agent allow-lists but 0 calls in window (strongest layer-2 candidates). A per-agent breakdown (allow-list intersection visibility).
3. **Weight join + fallback (see §10 A1 — correction).** `fc9_baselines.json` carries ONLY aggregate averages (`51218/223 ≈ 230` tok/tool pre-narrowing at `:125`/`:139`; `19933/74 ≈ 269` post-narrowing at `:167`/`:169`) — it has **no per-tool weight table**. Ruling: build a committed per-tool weight artifact from the fc9 measurement point (`convert_to_openai_tool` + `estimate_tokens`, sort_keys+compact form, `fc9_baselines.json:formula`/`serialization_form_note`); join `tool_name → weight` from it; **fallback for tools absent from the baseline = the 230-average** (documented, deterministic). Never compute weights at digest time from a live registry (that would break determinism).
4. **Determinism invariant.** Same ledger fixture + same date → byte-identical output modulo the generated-at header. Pin a determinism unit test. No clock/random/live-registry reads in the rollup body.
5. **Destination (runtime-canonical) — D-6 amended, constant NAMED (U8).** The canonical root is the env constant **`ENSEMBLE_TOOL_USAGE_DIGEST_ROOT`** (config/env constant — no pre-existing config mechanism in this phase names a root, so the env constant is the canonical seam). Default resolution rule (precise): when the env var is unset, the root resolves to the daemon's canonical project-data root for THIS project — i.e. `<live install tree>/.agents/shared/planning/tool-usage-metrics-p1/digests` (the live install tree, NOT a worktree and NOT the repo checkout). `manager.workdir` fallback is a phantom (never assigned) and the system-default project carries no `main_directory`, so `_get_project_workdir` returns None for the seeded schedule. Digest path: `<root>/YYYY-MM-DD.md` (the root already carries the `/digests` leaf — the leaf is PART of the default constant value, not appended by the caller); same-day re-run OVERWRITES (idempotent). This removes the cross-project write hazard for on-demand jober/leader calls. Live tree has no `.git` → daily files are not git pollution; ~365 small files/year is fine. Optional board-note pointer alongside the durable file. Read as a RAW env var, not a config field — no house rule requires config registration; operators set it via the unit Environment=/env channel when the default is wrong. (approver fix A5, iteration-001: confirms the raw-env-var reading — no config-registration obligation for this constant.)

*Amended by architect pass @ fd254b335 — D-6 (destination).*
6. **Tool registration seam (three steps).** (a) `@tool` + `@register_tool_category("scheduling")` decorator in `daemon/tools/tool_usage.py`; (b) registry entry — add `"daemon.tools.tool_usage"` to `CATEGORY_MODULES["scheduling"]` (currently a single string at `_tool_registry.py:1315`; the scanner accepts a list, `:1085`); (c) construction — factory import + `tools.extend(...)` in `daemon/tools/instance.py` near `:7040-7043` (the scheduling construction site, `scheduling.py:132-163` factory captures caller ids at factory time). Pattern per `instance.py:7035-7043`: decorator-only registration is SILENTLY INVISIBLE — the extend is load-bearing.
7. **Holder set — D-8 amended (accept `scheduling` category, disclose jober).** Reuse the EXISTING `"scheduling"` category — held by `ari`, `leader`, `jober` (`agents/ari/meta.json:34`, `agents/leader/meta.json:38`, `agents/jober/meta.json:11`). Jober inheritance is BENIGN: the digest tool is SQL read + one deterministic file write, no LLM call, ~zero tokens, overwrite-idempotent, and with D-6 the destination no longer depends on the caller. Disclose; exact-holder-set (new category + `meta.json` edits) stays out of scope. Rationale: (a) seeded schedule invokes `agent='ari'`, so the tool MUST be in a category ari holds; (b) reusing an existing category adds NO new tool-category value → NO `VOCABULARY_VERSION` bump / artifact regen (keeps Phase 2's no-vocab-bump invariant); (c) `leader` gets it for oversight/manual invocation. `instance_mode='new_instance'` is the house default (all 14 existing scheduler rows); ~365 COMPLETED instance rows/year is normal house behavior, not sprawl. Intended callers = ari + leader.

*Amended by architect pass @ fd254b335 — D-8 (holder set).*
8. **Seeded row (frozen config) — D-3 amended (adapter shape).** `source_type='scheduler'` (`scheduling_service.py:105`); adapter reads `config["schedule"]` (cron) — NOT `when`. Persist the ADAPTER shape: `config = {agent:'ari', message:<minimal deterministic digest instruction>, timezone:'Asia/Ho_Chi_Minh', recurrence:'daily', local_time:'08:00', schedule:'0 8 * * *', instance_mode:'new_instance', project_id:<system default project (§3.9)>}`; `priority` default 5; `enabled=True`, `autostart=True` (`scheduling_service.py:818-819`). Status seeded in the state the compensated create-path produces (startable/registered — not STOPPED) so `start_all()` registers+starts it (D-2); see §3.10.

Idempotency (D-4): deterministic `source_id = uuid5(NAMESPACE_DNS, "tool-usage-daily-digest")` — PK collision makes the second INSERT a no-op (mirrors `ensure_system_default_project`); `name` is a logical key only (no unique constraint).

*Amended by architect pass @ fd254b335 — D-3 (adapter config shape) + D-2 (startable status) + D-4 (idempotency hardening).*
9. **project_id resolution + repo-readiness — D-1 amended.** `constants.SYSTEM_DEFAULT_PROJECT_ID` is set at `api.py:2979` (lifespan), AFTER the manager-boot seed site (`manager.py:1166`); `self._project_repository` does not exist until `:2113` (source repo not until `:1779`). Seeder must NOT take `self._project_repository`. Fix: like the memoir seed, the seeder BUILDS ITS OWN REPOS from `self._engine` (`create_project_repository` / `create_source_repository`, `create_tables=False`), or the hunk moves to ≥ `:2113`. It then calls `ensure_system_default_project()` itself (idempotent get-or-create; deterministic `uuid5(NAMESPACE_DNS, "__system_default__")`, `project/repository.py:292/:304`) to obtain the id — do NOT read the not-yet-set constant. This id enables durable job-queue routing (`scheduler.py:305-315`, `:423/:429`). Boot-ordering claim (seed before lifespan `start_all()`, `manager.py:16929-16945`) remains correct.

*Amended by architect pass @ fd254b335 — D-1 (seeder repo-readiness).*
10. **Boot activation + GC-safety — D-2 + D-5 amended.** Seeder runs during manager init (site per D-1), so the enabled+autostart row exists when `source_registry.start_all()` runs in the lifespan (`manager.py:16945`) → registered+started with NO restart. D-2: `start_all()` SKIPS stopped/cancelled rows — the create-path compensation the plan bypasses (`scheduling_service.py:822-829`) is exactly what makes a fresh row startable. Fix: seed the row in the state the compensated create-path produces (startable/registered), or invoke the compensation. GC predicate (verified): collectable = scheduler ∧ `enabled==False` ∧ status ∈ {stopped, cancelled} ∧ age > 14 d — an **enabled row is never collectable**, so the core GC-safety claim holds once the row actually starts (status → running). D-5 opt-out: managed key `limits.tool_usage_digest_seeding_enabled` (default ON; registered in phase 1's settings edit per the placement ruling — one `MANAGED_KEYS` row, `config_path=None`); seeder NO-OPS when OFF. Additionally the seeder must NO-OP on an EXISTING row in ANY state (get-by-id-or-name first), narrowing re-seed to the post-GC window only — so an operator's disable survives re-seed.

*Amended by architect pass @ fd254b335 — D-2 (start_all vs STOPPED) + D-5 (disable-lifecycle).*
11. **Seed failure tolerance.** Like `memoir/seed.py:12-28/:80-83`: failures log WARNING and NEVER propagate; content failure must not become an availability failure. Idempotent get-or-create keyed on stable identity (name `"tool-usage-daily-digest"` + `source_type='scheduler'`).
12. **Per-agent allow-list source — committed snapshot artifact (U7).** The digest's per-agent allow-list intersection reads from a COMMITTED SNAPSHOT ARTIFACT, never a live scan of `agents/` at digest time (consistent with the per-tool weight artifact pattern, §3.3, and §3.4's no-live-reads rule): (a) the artifact is `.agents/shared/planning/tool-usage-metrics-p1/allowlists.json` (alongside the per-tool weight artifact of T2 — one convention, no second), produced by a deterministic generation step that scans `agents/*/meta.json` `tools` arrays at BUILD/COMMIT time and emits them sorted; the generation command is recorded in §10 (A8) so the snapshot is reproducible; (b) join + fallback semantics: a `tool_name` present in an agent's allow-list carries an allow-list marker in that agent's section of the digest; a tool absent from ALL allow-lists is still listed, with no allow-list marker; (c) the digest NEVER scans the live `agents/` tree at run time — §3.4's determinism invariant extends to the allow-list source.

**Window semantics (D-7) — frozen.** `D` = UTC date of the fire (08:00 ICT = 01:00 UTC; ICT has no DST; cron evaluated in the row's timezone). "last-24h" := complete previous UTC day (`D−1`); "cumulative-week" := `[D−7, D−1]` complete UTC days. Deterministic given (fixture, run-date); matches the day-granular ledger; avoids the non-deterministic partial-day. Disclose the deliberate divergence from the door's `calls_24h = today+` (different surface, vaguer by design).

*Amended by architect pass @ fd254b335 — D-7 (window semantics).*

**Rollback disclosure (D-9) — frozen text.** The seeded row persists after rollback; the old binary starts it and each 08:00 fire degrades to an ari error turn (tool missing — no crash, no data loss). Operators may disable the row pre-rollback; GC reaps disabled rows at 14 d and re-upgrade re-seeds (unless D-5's flag is OFF). This keeps `rollback_safe=true` honest: fix-loss-only refers to code/data, plus this disclosed noise channel.

*Amended by architect pass @ fd254b335 — D-9 (rollback disclosure).*

---

## 4. Tasks (file-level claims + per-task verification)

| # | Task | Owns | Depends On | Acceptance / verification |
|---|---|---|---|---|
| T1 | Build `daemon/services/tool_usage_digest.py`: `SELECT` last-24h + cumulative-week per `(project_id, tool_name)` from `tool_usage_daily`; join per-tool weight (§3.3); compute `weight × calls` sort with secondary keys `tool_name ASC, project_id ASC` (and `agent_id ASC` per-agent) (§3.2, U6); build zero/near-zero (allow-list intersection) + per-agent sections; render deterministic markdown (generated-at header isolated). Windows frozen: last-24h = complete previous UTC day (D−1); cumulative-week = [D−7, D−1] complete UTC days (D-7). | `daemon/services/tool_usage_digest.py` | none (consumes frozen ledger) | Unit test with fixed fixture + date → expected markdown; determinism test byte-identical modulo header (incl. windows); TIE FIXTURE (U6): ≥3 tools sharing the 230 fallback weight with different call counts + equal products where constructible, assert byte-identical output across two runs AND across both dialects where the test matrix runs SQLite. |
| T2 | Build the committed per-tool weight artifact from the fc9 measurement point; document the join + 230-average fallback (§3.3, §10 A1). ALSO build the committed per-agent allow-list snapshot artifact `allowlists.json` (§3.12, U7): deterministic generation step scanning `agents/*/meta.json` tools arrays at BUILD/COMMIT time, sorted, generation command recorded (§10 A8); digest never scans the live `agents/` tree at run time. | artifacts under `.agents/shared/planning/tool-usage-metrics-p1/` (`tool-weights.json` + `allowlists.json`, or module constant) | T1 | Every ledger `tool_name` resolves to a weight; absent tools use 230; every per-agent section join resolves against `allowlists.json`; tools absent from all allow-lists still listed with no marker; no live-registry or live-`agents/` read in rollup. |
| T3 | Build `daemon/tools/tool_usage.py`: `tool_usage_digest` @tool + `create_tool_usage_tools_if_available(...)` factory; calls T1's rollup, writes to the §3.5 path, returns a short status string. | `daemon/tools/tool_usage.py` | T1 | Tool callable; writes exactly one `YYYY-MM-DD.md`; no prompt-driven rollup. |
| T4 | Three-step registration: (a) decorators in T3's module; (b) `CATEGORY_MODULES["scheduling"]` → list incl. `"daemon.tools.tool_usage"`; (c) factory import + `tools.extend(...)` in `instance.py` near `:7040-7043`. | `_tool_registry.py`, `instance.py` | T3 | Tool discovered by name (AST scan) AND constructed for ari/leader/jober; frozen-name drift test GREEN. |
| T5 | Build `daemon/repositories/source/seed.py`: `seed_tool_usage_digest_schedule(engine)` (seeder builds its own repos from `self._engine`, D-1) — get-or-create by deterministic `source_id = uuid5(NAMESPACE_DNS, "tool-usage-daily-digest")` (PK collision → second INSERT no-op, D-4); build §3.8 adapter config; `enabled=True/autostart=True`; no-op when managed key `limits.tool_usage_digest_seeding_enabled` is OFF (D-5); no-op on an EXISTING row in ANY state (get-by-id-or-name first); failures never propagate, idempotent. | `daemon/repositories/source/seed.py` | none | Seed twice → exactly one row (uuid5 idempotency); opt-out flag no-op; failure → WARNING, no raise. |
| T6 | Add the `manager.py` hunk: import + call, passing `self._engine` (seeder builds its own repos, D-1 — do NOT pass `self._project_repository`, which does not exist at `:1166`); site may move to ≥ `:2113` if the giter prefers. Require line-disjointness from Phase 1's two hunks (§9 / architect I1 enumeration). | `daemon/manager.py` (one hunk) | T5 | Seed runs at boot; row present + startable before `start_all()` (`manager.py:16945`); no Phase-1 hunk overlap. |
| T7 | Tests: digest unit + determinism (T1) — extend to the T3 FILE-WRITE path (byte-identical modulo the generated-at header), not just the rollup; weight join/fallback (T2); tool-registration (T4); seed idempotency (uuid5, seed twice → one row) + failure (T5); opt-out flag no-op; startable status; adapter config shape carries `schedule=`; GC-safety (enabled row not collectable, `repository.py:765`); boot ordering (T6); windows D−1 / [D−7,D−1]. | `tests/**` | T1–T6 | All GREEN in worktree, dev-fenced env, zero live contact. |
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
| R2 | Seeder runs before `self._project_repository` exists (D-1) → AttributeError; or reads unset constant → wrong project_id. | High | Medium | D-1: seeder builds its own repos from `self._engine` (or hunk moves to ≥ `:2113`); calls `ensure_system_default_project()` directly; idempotent + deterministic uuid5. |
| R3 | Seeded row not started (D-2): `start_all()` skips stopped/cancelled rows; the bypassed compensation is what makes a fresh row startable. | High | Low | D-2: seed in startable/registered state (or invoke compensation); test boot ordering + started status. |
| R4 | New tool category would trigger vocab bump (conflicts Phase 2). | Medium | Medium | §3.7 reuse `"scheduling"`; zero new category value; zero meta.json edits. |
| R5 | Digest non-determinism (live weights/clock/random) → byte-diff test fails. | Medium | Low | §3.4: committed weights, no live reads, generated-at header isolated; pin determinism test. |
| R6 | Digest destination resolves to wrong checkout (D-6): `manager.workdir` is a phantom; system-default project has no `main_directory`. | Medium | Medium | D-6 (U8): canonical root NAMED — env `ENSEMBLE_TOOL_USAGE_DIGEST_ROOT`, default `<live install tree>/.agents/shared/planning/tool-usage-metrics-p1/digests`; path `<root>/YYYY-MM-DD.md`; same-day overwrite; test path resolution. |
| R7 | manager.py hunk collides with Phase 1's two hunks. | Medium | Low | §0 enumerate my single hunk at `:1166`; require integration-time line-disjointness check (§9). |

---

## 7. Success criteria

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| S1 | Deterministic digest | fixed fixture + date, two runs | byte-identical modulo header; windows D−1 / [D−7,D−1] |
| S2 | Content shape | per-project 24h+week counts, weight×freq sort, zero/near-zero + per-agent sections | all present |
| S3 | Weight join/fallback | every tool has weight; absent → 230 | exact |
| S4 | Tool registration | discovered by name AND constructed for ari/leader | pass, drift test GREEN |
| S5 | Seed idempotency | seed twice | exactly one row |
| S6 | GC-safety | run GC sweep with seeded enabled row | row survives (not collectable) |
| S7 | Boot activation | seed at init → row started by `start_all` | enabled+autostart, started |
| S8 | Zero live contact | test run | no prod/env writes, no restart |
| S9 | uuid5 idempotency | seed twice | exactly one row (PK collision) |
| S10 | Adapter config shape | seeded row config | carries `schedule=` cron |
| S11 | Startable status | seeded row | registered/started by `start_all` |
| S12 | Opt-out flag | flag OFF | seeder no-op |
| S13 | Digest determinism windows | fixed fixture + date | byte-identical modulo header; D−1 / [D−7,D−1] |

---

## 8. Test strategy

- **Unit:** rollup math (24h/week windows, sentinel `'unknown'`, sort key), weight join + 230 fallback, markdown shape sections.
- **Determinism:** two runs on identical fixture+date → byte-identical (header normalized); mutation: inject a live-registry weight read → RED; tie fixture (U6): ≥3 tools at the 230 fallback weight + equal products → sort stable via secondary keys (`tool_name ASC`, `project_id ASC`, `agent_id ASC`), byte-identical across two runs and across both dialects where the matrix runs SQLite.
- **Registration:** decorator-only is invisible (construction hunk required) — mutation: drop the `tools.extend` → tool not constructed → RED; frozen-name drift GREEN.
- **Seed:** idempotency (twice→one), failure tolerance (WARNING, no raise), config field assertions (timezone/cron/agent/project_id/enabled/autostart), GC-safety (enabled row not collectable per `repository.py:765`).
- **Boot ordering:** seed row present before `start_all` (manager.py:1166 vs :16945).
- **Dev-fenced:** all tests in worktree; POSTGRES_* scrubbed per variable (never `env -u POSTGRES_*` — silent no-op, critical note); zero live contact.

---

## 9. Cross-phase interface notes / open questions (for the architect)

Resolved by architect pass @ fd254b335:

1. **Per-tool weights (R1).** RESOLVED — committed per-tool weight artifact + 230-average fallback validated as the determinism mechanism; keep. No per-tool measurement lane needed for v1.
2. **Holder set exactness (§3.7).** RESOLVED — accept `scheduling` category (ari+leader+jober); jober inheritance benign and disclosed (D-8); exact-holder-set stays out of scope.
3. **D-7 window semantics.** RESOLVED — frozen: last-24h = D−1 complete UTC day; cumulative-week = [D−7, D−1] (D-7).
4. **D-9 rollback disclosure.** RESOLVED — frozen text recorded in §3 (D-9).

Still open:

5. **manager.py hunk disjointness (R7).** Confirm the hunk sets are line-disjoint at integration (planner sweeps; architect I1 enumeration at gate time). D-1 may move the hunk to ≥ `:2113`.

---

## 10. Assumptions

- **A1 (fc9 has no per-tool weights).** Verified by walking all keys of `fc9_baselines.json`: only aggregate `tool_schema_tokens` / `resolved_toolset_size` and the `plane_sync_project` single-tool example (`65`/`84` tokens) exist; no per-tool weight table. The ~230 average is `51218/223` (`:125`/`:139`), ~269 is `19933/74` (`:167`/`:169`).
- **A2 (constant sequencing).** `SYSTEM_DEFAULT_PROJECT_ID` set at `api.py:2979` (lifespan) — after `manager.py:1166`. Seeder must derive the id itself (`ensure_system_default_project()`, `project/repository.py:292/:304`).
- **A3 (start_all covers boot start).** `start_all()` loads all enabled adapters from DB and schedules autostart (`sources/registry.py:287-321`); called at `manager.py:16945` during `start_sources`.
- **A4 (scheduling category holders).** `ari`/`leader`/`jober` all have `"scheduling"` in tools.allow (meta.json lines 34/38/11). Reusing it grants the tool to all three; no new category value → no vocab bump.
- **A5 (cron build).** `recurrence='daily'` + `when='08:00'` → `"0 8 * * *"` at `scheduling_service.py:787-790`; DST-safe next-fire via `compute_next_cron_fire` (`util/tz.py:529`).
- **A6 (canonical root, D-6 — NAMED, U8).** The digest destination pins the env constant `ENSEMBLE_TOOL_USAGE_DIGEST_ROOT`; default (unset) = the daemon's canonical project-data root for this project, i.e. `<live install tree>/.agents/shared/planning/tool-usage-metrics-p1/digests`; digest path `<root>/YYYY-MM-DD.md`, same-day overwrite. `manager.workdir` is a phantom and the system-default project has no `main_directory` — neither is a fallback. Ruled and closed at review (U8); §9 records the resolution.
- **A7 (window semantics, D-7).** `D` = UTC date of the fire (08:00 ICT = 01:00 UTC); "last-24h" = complete previous UTC day (D−1); "cumulative-week" = [D−7, D−1] complete UTC days. Frozen.
- **A8 (allow-list snapshot generation, U7).** `allowlists.json` is regenerated deterministically at BUILD/COMMIT time with a recorded generation command — contract: scan `agents/*/meta.json` `tools` arrays, sort agents and tools, emit JSON carrying a `generated_from_commit` field; the exact generator script is T2's deliverable. Sketch of the recorded command:
  ```bash
  python3 scripts/gen_tool_usage_allowlists.py --agents-dir agents/ --out .agents/shared/planning/tool-usage-metrics-p1/allowlists.json
  ```
  One convention only: same committed-artifact pattern as the per-tool weight artifact (T2) — same directory, same build/commit-time generation, same recorded-command discipline. No second convention is introduced.

---

## 11. Appendix — alternative activation (architect override)

Primary delivery = boot-time seeder (automatic at promote, per commission). If the architect overrides to a one-line post-promote arm, the equivalent manual step is:

```
POST /api/schedules  { "label":"tool-usage-daily-digest", "agent":"ari",
  "message":"Call tool_usage_digest for the previous day and write the durable digest.",
  "timezone":"Asia/Ho_Chi_Minh", "recurrence":"daily", "local_time":"08:00",
  "schedule":"0 8 * * *", "instance_mode":"new_instance",
  "project_id":<system default project id> }
```

(or the `task_schedule` tool held by ari/leader/jober, `daemon/tools/scheduling.py`). Both options are executable; the plan defaults to the seeder.

---

## 12. Phase exit gate

Phase 3 is DONE when: (1) `tool_usage_digest` registered via the three-step seam and callable by ari/leader; (2) rollup deterministic with frozen content shape + weight join/fallback; (3) digest written to the runtime-canonical path; (4) seeder idempotent, enabled/autostart, GC-safe, boot-ordered; (5) all tests GREEN in worktree with zero live contact; (6) open questions in §9 surfaced to the architect; (7) ZERO edits to Phase-1 files / graph.py / routers / checkpoint_metrics / frontend / meta.json. No commit, no promote, no live contact — the planner sweeps + commits.
