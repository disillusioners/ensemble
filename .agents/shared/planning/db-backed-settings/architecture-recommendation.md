# Architecture Recommendation: DB-backed runtime settings — `limits.llm_concurrency`

Date: 2026-10-18
Author: architect (Standard Design — 2-worker fan-out: `data-flow-design` + `security-design`, instances 7922c408 / 791cfc38)
Worktree: `/home/nea/ensemble-src-wt-db-backed-settings` (branch `feature/db-backed-settings`, base latest @ e0e826866)
Status: **BINDING** for implementation. Rulings on `decisions.md` OPEN items 1–6 + blind-spot adjudications (a)–(d).

Binding commission constraints (NOT reopened, restated as invariants): explicit resolver precedence DB > os.environ > yaml literal > default (pydantic env tier never relied upon); restart-required apply via boot hook between `load_config()` and `InstanceManager` (manager.py:555 untouched); registry = exactly one managed key v1; additive migration with own `.sql`; extend `daemon/routers/settings.py` DI idiom; FE extends existing `/settings` page.

---

## Ruling 1 — `app_settings` schema (OPEN 1)

**DECISION: keep the plan's draft verbatim — TEXT/JSON-string columns, registry-side type validation.** Native typed columns are rejected.

```sql
CREATE TABLE IF NOT EXISTS app_settings (
  key        TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,   -- JSON-encoded scalar (int|float|bool|str)
  value_type TEXT NOT NULL,   -- "int"|"float"|"bool"|"str"
  updated_by TEXT NULL,       -- AuthContext.subject; NULL when auth off (never "")
  updated_at TEXT NOT NULL    -- ISO-8601 UTC, application-stamped
);
```

SQLModel: `key: str = Field(primary_key=True, sa_column=Column(Text))`, `value_json: str`, `value_type: str`, `updated_by: Optional[str] = None`, `updated_at: str`.

- **Per-key type validation lives in the managed-key registry** (`value_type` + `validator` mirroring the Field constraint `ge=1` exactly — no extra `le` in v1; two truths of validation would drift). NOT in DB CHECK constraints — that breaks dialect parity for zero benefit.
- **Cross-backend portability: zero dialect-specific SQL is required.** The table's full query surface is PK lookups + one boot-time full read. No SUBSTR-prefix-style workaround, no `_ensure_postgres_columns` mirror (brand-new-table precedent, manager.py:596-601). Dual-driver posture per `custom_agent_configs` (20261014_040000) header.
- **No indexes beyond PK** (≤10 rows forever at v1 scope).
- `updated_by` NULL is correct (precedent `infra/models.py:257` nullable actor); NULL only when auth-off/unknown, never empty string.

## Ruling 2 — bootstrap engine + runtime engine (OPEN 2)

**DECISION: throwaway engine for the boot read; the RUNTIME `SettingsService` rides the manager's single shared engine.** Engine handover at boot is rejected (one boot-time SELECT does not justify coupling lifespan ordering; the manager's engine does not exist yet at api.py:401).

1. `apply_db_settings_overrides(config, ensemble_config)` builds + disposes its own engine (`factory.py:232` PG / `factory.py:138` SQLite), reads all rows, re-validates, mutates `config.limits.llm_concurrency` in place (Config is mutable — config.py:4070-4073), and returns `(config, snapshot)` (snapshot per Ruling on blind spot b).
2. **Plan gap closed — runtime engine:** wire `set_settings_service(SettingsService(engine=manager.engine))` in the lifespan at the existing `set_settings_project_repo` block (api.py:2451-2453), immediately after manager construction. One shared engine (manager.py:557-563 one-engine invariant); no second pool. **The plan never specified which engine serves GET/PUT/DELETE — this is the binding answer.**
3. **Missing-table tolerance is load-bearing on EVERY first boot, both backends** — not a legacy-DB edge case: the boot read at api.py:401+ε runs BEFORE `create_all` (manager.py:649) on PG and BEFORE the SQLite migration runner (manager.py:652-655). First boot = no overrides applied; rows written later apply on next restart. Fail-open everywhere (Ruling 3).

## Ruling 3 — invalid stored value at boot (OPEN 3)

**DECISION: fail-open to the lower tier + ERROR log — for BOTH parse-failure and range-failure.** The posture does not differ by class; only the log wording does (`failed parse: <exc>` vs `failed validation (ge=1)`), each naming the fallback tier actually used.

- At WRITE time both classes are rejected synchronously with 422 (belt-and-braces) — the boot re-validation guards direct-DB tampering and schema drift only.
- Refuse-boot REJECTED: turns a settings typo into an outage, and is asymmetric with lower tiers (a bad env/yaml value would sail into the semaphore today).
- Silent skip REJECTED: silent misconfiguration is the exact failure mode this feature exists to eliminate.

## Ruling 4 — auth scopes (OPEN 4)

**DECISION: register BOTH `settings:read` AND `settings:write` in `SCOPE_TAXONOMY` (daemon/auth/scopes.py:53-83). GET → `settings:read`; PUT/DELETE → `settings:write`.**

- **This OVERTURNS the plan's "GET open like siblings"** (decisions.md:102/106, phase2-plan.md:13). That reading mixed two precedent classes that both live in this router: the user-preference class gates NOTHING (language/timezone/editor GETs *and* PUTs ungated — settings.py:160/169/194/208/246/269/289), while the infrastructure class gates reads (snapshot GET :661 + PUT :675 both `snapshot:write`; `sources` gates its GETs, sources.py:126/324; control-plane reads elsewhere are read-scoped: `instances:read`, `maintenance:read`, `workspace:read`). `GET /api/settings/daemon` reports effective daemon config **including env/yaml tier values** — infrastructure class. Its right precedent is instances/maintenance, not language/timezone.
- **Reuse is impossible:** the taxonomy is CLOSED (`normalize_scope_list` rejects unknown names, scopes.py:120-122 — registration is mandatory, not cosmetic), no `settings:*` exists, and the plan's fallback candidate `maintenance:write` **does not exist** (`maintenance` is a read-only family, scopes.py:63).
- Cost of gating: zero. SPA session lane auto-carries the whole taxonomy (scopes.py:36-40, :93); when auth is OFF every gate is inert (decorators.py:128-130). FE needs no change.
- Naming conforms: `<resource>:<verb>`; `settings` is a mass noun like `workspace`/`maintenance`; read/write split mirrors the dominant instances family.
- **Citation corrections for the planner docs:** settings.py:661/:675 are GET/PUT (not "POSTs"); `maintenance:write` does not exist; the family list at decisions.md:102 is a subset (omits `system:*`, `messages:write`, `mcpkb:admin`, `prompts:*`).

## Ruling 5 — FE co-bound line (OPEN 5)

**DECISION: one muted documentation line, with CORRECTED arithmetic — the effective bound is `min(llm_concurrency, 7)`, not ~5.**

Mechanics (verified): `WORKER_POOL_SIZE=5` + `CHAT_WORKER_POOL_SIZE=2` (constants.py:86/94) back two thread pools (pool_orchestrator.py:163-235); every message flows through a pool thread that bridges onto the single main asyncio loop (`MainLoopBridge.run_coroutine_threadsafe`, main_loop_bridge.py:15-39, pool_orchestrator.py:351-362), where the sole semaphore acquire wraps `graph.astream` (instance_messaging.py:4887). Raising `llm_concurrency` above 7 is a no-op. The planner's "~5" omitted the 2 chat-lane threads.

- Suggested wording: *"Real parallelism is also capped by the fixed worker thread pools (5 worker + 2 chat = 7 concurrent graphs — constants, not settable here)."* Must NOT imply the knob controls total throughput (constraint upheld).
- Registry entry gains an `effective_max` documentation hint **computed from the constants at import time** (`WORKER_POOL_SIZE + CHAT_WORKER_POOL_SIZE`) so it cannot drift — documentation-grade only, never enforced.

## Ruling 6 — integration test (OPEN 6)

**DECISION: EXCLUDE the full-lifespan PG e2e from v1 (disproportionate).** Cost: disposable-PG spin-up per the e2e convention (daemon cannot boot on SQLite — migration 20260714_000001). **INCLUDE one cheap addition:** a **source-order pin test** asserting `apply_db_settings_overrides(` appears in `daemon/api.py` between `load_config()` and `InstanceManager(` — the cheapest lockstep pin on the load-order invariant, matching the grep-pin pattern already used in the secrets migration header. Together with the planned temp-SQLite bootstrap unit and the shadowing-case unit, the load-bearing invariants (precedence ladder, mutation visibility, boot ordering) are pinned without a PG e2e. The true end-to-end acceptance stays the manual completion sentence (restart → DB tier active).

---

## Blind-spot adjudications

**(a) Startup races — NONE for the same process.** Uvicorn gates request serving on lifespan startup completion (standard `uvicorn.Server.startup` ordering; not version-verified in the lockfile — standard behavior); the :401→:543 window is not request-visible. Keep the 503-when-unwired DI idiom as a cheap backstop for test harnesses that bypass lifespan (not load-bearing in production). Concurrent PUTs: last-writer-wins on PK upsert, benign for this key; optional per-key `asyncio.Lock` is cheap hardening, NOT required for v1. PUT-during-shutdown writes a row the next boot applies — benign. The single-process invariant (c) closes cross-process races.

**(b) Resolver SSOT — ONE `resolve(key, snapshot)` used by BOTH boot-apply and GET, over a pristine pre-override tier snapshot.** The hazard is real: boot apply mutates `config.limits.llm_concurrency` in place, so any GET that recomputes the yaml tier from the live Config would report the DB value as the yaml value. Binding shape: `apply_db_settings_overrides` returns `(config, snapshot)` with `snapshot = {key: {default, env_value, yaml_value}}` captured at read time BEFORE mutation (yaml from the un-mutated field; env from `os.environ` at the same moment; default from registry). Store it module-global in `daemon/services/settings_service` via `set_settings_resolution_snapshot(snapshot)`, called in lifespan right after `set_settings_service`. GET semantics: `value` = running truth (boot-applied); `source` = live re-resolve (live DB row > live env > snapshot yaml > default); divergence between `value` and `source` is HONEST restart-required reporting, not drift. Known caveat to document in the registry docstring: yaml is a boot-time read; no config.yaml hot-reload path exists today.

**(c) Multi-process — single-process is VERIFIED current reality; state the invariant explicitly.** `daemon/__main__.py:421-430` runs `uvicorn.run` with no `workers=` kwarg (default 1); dev.sh:142 single app child; launcher.sh delegates to `run_app.py`. If `workers>1` is ever set: the semaphore becomes per-process (multiplying effective LLM concurrency by worker count), boot apply runs per-process, and a PUT in worker A is invisible to worker B until both restart. Document in v1; any future multi-process v2 needs a cross-process coordination lane — out of scope.

**(d) Migration runner PG no-op — existing PG deployments get `app_settings` on next boot with ZERO operator action, conditional on model registration.** Requirements: (1) import `AppSetting` into `SQLModel.metadata` BEFORE `manager.py:649 create_all(self._engine)` — add it to the late-import block at manager.py:580-645 (snapshot/maintenance_runs/drift_events precedent); (2) model column set byte-matches the `.sql` record; (3) nothing else. `create_all` is `checkfirst=True` — creates missing tables, never alters, so existing PG DBs gain the new table additively. The `.sql` file (20261018_000001_create_app_settings.sql) is the SQLite-side persistent record AND the convention artifact; the runner is a PG no-op (runner.py:721-726). **Column-ensure path is irrelevant** (it exists for columns on EXISTING tables; this is a brand-new table). Precedent note: secrets tables used lane-(b) (per-model create_all in api lifespan, per comment at manager.py:1264-1267); app_settings uses lane-(a) (manager-level create_all + late-import) — the cleaner shape, and the first-boot ordering (read-before-create_all → tolerance) is handled by Ruling 2.3.

---

## Security addendum (binding, from the security-design analysis)

1. **Registry `sensitive` flag — day 1, not later.** `sensitive: bool = False` per registry entry; GET OMITS `env_value`/`yaml_value`/`db_override` for sensitive keys. Retrofitting later breaks the frozen Phase-3 FE contract; authoring it now costs one field. v1's key is `sensitive=False` (harmless int). Docstring tripwire: any future managed key whose value or tier values carry secret material MUST set `sensitive=True` — review blocker if omitted. Secrets never belong in `app_settings`.
2. **Actor capture:** `updated_by = request.state.auth_context.subject` via the `actor_from_request` pattern (prompt_version/repository.py:593-637); NULL when auth-off; NEVER body-asserted (anti-forgery, R3 precedent); NEVER `request.client.host` fallback (spoofable — a NULL row is more honest).
3. **Standard write scope suffices — do NOT mint `settings:admin`.** No role model or tier above operator exists in this codebase; the session lane IS the operator (scopes.py:93); `-admin` verbs are reserved for CRUD-over-auth-entities (`auth:admin`, `mcpkb:admin`). Daemon-global effect via standard write scope matches `instances:write` precedent (pause/resume/stop).
4. **CSRF inherited free:** the global double-submit predicate covers PUT/DELETE on the cookie lane (cookie.py:21-30, :70-74) — zero router code needed.
5. **Cheap audit:** one INFO log line per PUT/DELETE with subject + key (+ "auth-off" for NULL rows).

## Plan deltas (apply to the planner docs before implementation)

| # | Where | Change |
|---|-------|--------|
| 1 | phase2 t3/t5, decisions.md ruling d | GET gated `settings:read` (not open); `scopes.py` registers BOTH scopes; fix :661/:675 GET/PUT citation + drop nonexistent `maintenance:write` |
| 2 | phase1 t3 | Registry entry gains `sensitive: bool = False` + `effective_max` hint computed from `daemon.constants` (documentation-grade) |
| 3 | phase1 t5/t6 | `apply_db_settings_overrides` returns `(config, snapshot)`; lifespan calls `set_settings_resolution_snapshot(snapshot)` + `set_settings_service(SettingsService(engine=manager.engine))` at the :2451-2453 block |
| 4 | phase3 t5 | Co-bound line says 7 (5 worker + 2 chat), not ~5 |
| 5 | phase4 t1/t2 | Add the source-order pin test (api.py ordering grep-pin) |
| 6 | decisions.md ruling e/overview §In-Scope | Registry docs note: yaml tier is a boot-time snapshot; no hot-reload |

## Verdict

The plan is architecturally sound as written — the binding constraints are correctly captured, every anchor spot-checked true, and the two hardest problems (bootstrap chicken-and-egg, zero-diff restart apply) were already correctly solved by the planner. Two substantive corrections land from this review: **(1) the GET endpoint must be scope-gated `settings:read`** (the plan cited the wrong precedent class — the ungated sibling GETs are user preferences whose PUTs are equally ungated; infrastructure reads in this codebase are uniformly read-scoped), and **(2) the co-bound documentation number is 7, not ~5** (the chat-lane's 2 threads also bridge onto the semaphore-gated loop). One plan gap is closed bindingly (the runtime `SettingsService` engine = manager's shared engine), one guardrail is added day-1 (registry `sensitive` flag), and one cheap test is added (source-order pin). None of these change the plan's shape, phases, or effort class.

**Confidence: High.** Flip assumptions: (i) if the scope taxonomy were open rather than closed-and-fail-loud (scopes.py:120-122), scope-reuse arguments would gain weight — it is not; (ii) if any deployment ran uvicorn `workers>1`, blind spot (c) would reopen with per-process semaphores — none does today.
