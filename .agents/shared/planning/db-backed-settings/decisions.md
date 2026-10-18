# Decisions: DB-backed runtime settings (first setting: LLM worker limit)

Date: 2026-10-18
Author: plan-creation worker (evidence-verified in worktree `feature/db-backed-settings` @ e0e826866)
Status: Reconciled — iteration 002 (architect rulings folded; approver iteration-001 blocking fix + notes applied). Rulings (d)/(e)/(f) OPEN items are DECIDED in-body; pending approver re-check.

All file:line anchors below were spot-checked in this worktree during plan writing; drift from the wanderer report was: **none** (one addition: the bootstrap chicken-and-egg is already partially solved by existing design — see ruling b).

---

## Ruling (a): Storage shape — dedicated `app_settings` table + thin `SettingsService`. GREENFIELD. RECOMMENDED.

**Recommendation:** New SQLModel table `app_settings` + a thin `SettingsService` (`daemon/services/settings_service.py`) + one additive migration file. Do NOT reuse existing KV stores.

Schema (recommendation, architect may refine):

```
app_settings
  key          TEXT  PRIMARY KEY          -- registry-gated, e.g. "limits.llm_concurrency"
  value_json   TEXT  NOT NULL             -- JSON-encoded scalar (int/float/bool/str)
  value_type   TEXT  NOT NULL             -- "int"|"float"|"bool"|"str" — FE rendering + coercion target
  updated_by   TEXT  NULL                 -- actor subject (auth off → null)
  updated_at   TEXT  NOT NULL             -- ISO-8601 UTC
```

**Rationale:**
- Both existing KV candidates are wrong-scope, verified:
  - `project_metadata_records` (via `_project_repo.get/set_metadata`, the backing store of every current `/api/settings/*` endpoint) is **per-project** — using it for a global daemon knob would require a synthetic "system project" hack and would silently change meaning per project context.
  - `shared_meta_kv` is **partitioned per instance-tree context_key** — a daemon-global runtime knob must not depend on which agent instance writes it, and its lifecycle is tied to instance trees, not the daemon process.
- The daemon-wide setting has exactly one writer scope (the daemon itself) and one row per key — a dedicated table is the honest shape and stays trivially cheap (< 10 rows forever at v1 scope).
- Registry-gating (ruling c) means the table can never accumulate arbitrary junk keys: the `SettingsService` refuses reads/writes of unregistered keys.

**Trade-off considered:** reusing `project_metadata_records` under a reserved project id — rejected: overloads project semantics, breaks per-project UI queries that enumerate metadata, and forces a fake-project row whose only purpose is namespace squatting.

**OPEN for architect:** exact column types for PG (`TEXT` vs native types; the codebase's dual-dialect convention uses TEXT+JSON strings broadly, e.g. `custom_agent_configs` JSON lists) and whether `updated_by` should be NOT NULL under auth-on. Keep the dual-dialect convention: SQLite `.sql` migration is the persistent record; PG fresh schema comes from `SQLModel.metadata.create_all()` (per `custom_agent_configs` precedent, `20261014_040000_create_custom_agent_configs.sql` header comments).

---

## Ruling (b): Resolution injection point — post-`load_config()`, pre-`InstanceManager`, in lifespan. Bootstrap chicken-and-egg is ALREADY SOLVED by existing layering. RECOMMENDED.

**Recommendation:** In `daemon/api.py` lifespan, after `config = load_config()` (:401) and before `InstanceManager(...)` construction (:543), insert one call:

```
config, snapshot = apply_db_settings_overrides(config, ensemble_config)
```

(RECONCILED per architecture-recommendation.md blind spot (b): the call returns `(config, snapshot)` — the pristine pre-override tier snapshot feeds the ONE shared resolver; see ruling c and the SSOT paragraph below.)

`apply_db_settings_overrides` lives in the new `daemon/services/settings_service.py`. It:
1. Builds a **short-lived, throwaway engine** from `ensemble_config` (PG: `create_postgres_engine(ensemble_config)`, `daemon/repositories/factory.py:232`; SQLite: `create_engine_from_config(DatabaseConfig...)`, `factory.py:138`) — the shared manager engine does not exist yet at this point. **The throwaway engine serves the BOOT READ ONLY** (reconciled, architect Ruling 2): the RUNTIME `SettingsService` rides the manager's single shared engine (`SettingsService(engine=manager.engine)`, wired at the api.py:2451-2453 block after manager construction).
2. Reads all `app_settings` rows (one SELECT; missing table → "no overrides"). **Missing-table tolerance is load-bearing on EVERY first boot, both backends** — the boot read runs BEFORE `create_all` (manager.py:649) on PG and BEFORE the SQLite migration runner (manager.py:652-655), so first boot always has no table yet: no overrides applied; rows written later apply on next restart.
3. Captures the **pristine pre-override tier snapshot** — `{key: {default, env_value, yaml_value, applied_value}}` — with `{default, env_value, yaml_value}` captured BEFORE any mutation (yaml from the `daemon/config.py` raw-limits module accessor — the RAW file literal when present, absent otherwise, never the pydantic-merged field; env from `os.environ` at the same moment; default from registry) and `applied_value` captured AFTER the boot mutation (post-apply, just before return — the running-truth carriage; on the fail-open path the config is unmutated so `applied_value` = the field as-is — always populated).
4. For each registry-managed key present, re-validates the stored value against the field constraints (`ge=1`) and **mutates the pydantic field directly**: `config.limits.llm_concurrency = value`. `Config` is a plain mutable `BaseSettings` (verified `daemon/config.py:4070-4073` — no `frozen`, no `validate_assignment`), so direct assignment works; manual re-validation happens in step 4. Fail-open covers BOTH failure classes (parse failure and range failure) with class-specific log wording (`failed parse: <exc>` vs `failed validation (ge=1)`), each naming the fallback tier actually used.
5. Disposes the throwaway engine and **returns `(config, snapshot)`**.

**Why this alone makes restart-apply work with ZERO semaphore-site changes:** the daemon-wide semaphore is constructed once from the config value at `InstanceManager.__init__` (`self._llm_semaphore = asyncio.Semaphore(config.limits.llm_concurrency)`, `daemon/manager.py:555`) and acquired at the single site `daemon/services/instance_messaging.py:4887`. Patching the Config object before :543 means manager reads the DB-backed value with no changes downstream.

**Bootstrap chicken-and-egg — the hard part of question (b) — status: LARGELY SOLVED BY EXISTING DESIGN.** Verified in `daemon/api.py:386-394`: the lifespan resolves `data_dir` → `ensemble_config = EnsembleConfig.load_or_create(data_dir)` **FIRST, before `load_config()`**, with the in-code comment "This is the chicken-and-egg resolution: ensemble_config picks the DB engine *before* config.yaml is consulted." DB connection parameters come from `ensemble.json` + `POSTGRES_*` env overrides (factory.py:256-260) — **independent of config.yaml**. So the "minimal bootstrap connection" the commission worried about already exists as a load-ordering property, not new machinery: by the time we want to read `app_settings`, `ensemble_config` (the bootstrap connection spec) is already in hand at `app.state.ensemble_config`.

**Failure posture (recommendation):** fail-open. Wrap step 2 in try/except — on any error (DB down, table missing, row corrupt) log ERROR with the fallback tier used and continue boot with env/yaml/default values. Rationale: settings are an override, not a dependency; a broken settings read must not produce a new boot-failure mode ahead of the manager's own engine construction. Architect may prefer fail-closed for PG (where boot would die seconds later anyway at manager engine build) — either is defensible; fail-open is strictly safer for SQLite-mode installs.

**RESOLVED (was OPEN for architect) — decided by architecture-recommendation.md Ruling 2 + blind spot (b):** (1) NO engine handover — the throwaway engine stays boot-read-only; the runtime `SettingsService` is wired `SettingsService(engine=manager.engine)` at the api.py:2451-2453 block (one shared engine, manager.py:557-563 one-engine invariant). (2) Tier reporting rides the SSOT snapshot, not `app.state`: lifespan calls `set_settings_resolution_snapshot(snapshot)` (module-global in `settings_service.py`) right after `set_settings_service`; GET semantics per ruling d.

---

## Ruling (c): Precedence semantics for managed keys — explicit tier resolution, NOT pydantic's env tier. yaml literal STAYS. RECOMMENDED.

**Recommendation:** Managed keys resolve as an **explicit 4-tier ladder computed by the ONE shared resolver `resolve(key, snapshot)`** (reconciled per architecture-recommendation.md blind spot (b) — a single source of truth used by BOTH boot-apply and GET; never two resolution paths):

```
DB override  >  os.environ  >  yaml literal  >  field default
```

with a `source` value (`"db" | "env" | "yaml" | "default"`) returned alongside every effective value.

**Critical implementation note (the env gotcha, verified):** pydantic's own env tier CANNOT be used for managed keys, because `load_config` passes yaml sections as **init kwargs** (`config_dict["limits"] = processed_config["limits"]`, config.py:5560-5561 → `Config(**config_dict)` :5908) and init kwargs beat env vars in pydantic-settings — so with `llm_concurrency: 10` present in config.yaml:193, `LIMITS_LLM_CONCURRENCY` is silently dead today. The resolver must read `os.environ["LIMITS_LLM_CONCURRENCY"]` **directly and explicitly** for managed keys. (For `limits.llm_concurrency` specifically the yaml key IS present in the shipped config.yaml, so the shadowing case is the COMMON case, not an edge case.)

**yaml literal stays in place.** Do NOT migrate config.yaml to `${VAR:-default}` interpolation:
- The yaml tier must remain visible/editable in the file for operators (it keeps its role as tier 3).
- `${VAR:-d}` interpolation (`substitute_env_vars`, config.py:58-76) stays available as the existing escape hatch for non-managed keys — untouched, no behavior change.
- Touching the shipped yaml would churn every deployment for zero gain.

**config.yaml edit in scope:** comments only — update the limits-section comment to document the new ladder for managed keys (DB > env > yaml literal > default) and note that restart is still required.

**Env-var naming for future managed keys:** `<SECTION>_<FIELD>` per each field's existing `env_prefix` (the single managed key today is `LIMITS_LLM_CONCURRENCY`). The managed-key registry (below) carries the env var name per key so no per-key code special-casing creeps in.

**Managed-key registry (recommended):** a small declarative table in `settings_service.py` — `{key, config_path ("limits.llm_concurrency"), env_var ("LIMITS_LLM_CONCURRENCY"), value_type, validator (ge=1), requires_restart: True, sensitive: bool = False, effective_max, description}` (reconciled per architecture-recommendation.md Ruling 5 + security addendum 1: `effective_max = WORKER_POOL_SIZE + CHAT_WORKER_POOL_SIZE` computed from `daemon.constants` AT IMPORT — the literal form, per the adopted O4 ruling — so it cannot drift; documentation-grade only, NEVER enforced in code; `sensitive` is day-1: GET omits `env_value`/`yaml_value`/`db_override` for sensitive keys; v1's key is `sensitive=False`). Registry docstring must state: (i) the yaml tier is a BOOT-TIME snapshot — no config.yaml hot-reload path exists today — sourced from the `daemon/config.py` raw-limits module accessor (module-global captured inside `load_config` pre-merge, exposed via getter; precedent `_install_service_tool_enabled`); (ii) single-process invariant (uvicorn `workers=1`, `daemon/__main__.py:421-430`) — `workers>1` would reopen per-process semantics; (iii) tripwire: any future managed key carrying secret material in its value or tier values MUST set `sensitive=True` — review blocker if omitted. The registry is the gate for API writes (ruling d) and FE rendering (ruling e) — unregistered keys are refused everywhere. v1 contains exactly ONE entry: `limits.llm_concurrency`. Scope stays tight; co-bound knobs are documented, not added (see scope note in plan-overview).

**OPEN for architect:** whether a stored-but-invalid DB value (fails `ge=1` after JSON decode) should (i) be rejected at write time AND ignored at read time (recommended — belt and braces), and (ii) whether writes should also opportunistically prune legacy interpolation usage — recommended NO, out of scope.

---

## Ruling (d): API surface — extend `daemon/routers/settings.py` per its DI idiom; 3 endpoints; `settings:read` (GET) + `settings:write` (PUT/DELETE), BOTH registered. RECOMMENDED → ARCHITECT-DECIDED (Ruling 4).

**Recommendation:** add to the existing settings router (prefix `/settings`, :67; mounted `/api/settings`, api.py:3893):

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/settings/daemon` | Effective value + source tier per managed key: `{settings: [{key, value, source, default, env_value, yaml_value, db_override, requires_restart, description}], restart_required}` — `value` = `snapshot.applied_value` (the boot-applied running-truth carriage) |
| PUT | `/api/settings/daemon/{key}` | Set DB override (body `{value: <json scalar>}`); validates key ∈ registry + value against field constraints; returns the same key-shape with `source: "db"`; response carries `restart_required: true` |
| DELETE | `/api/settings/daemon/{key}` | Clear DB override (falls back down the ladder) |

**DI idiom — follow the router's own pattern exactly:** module-global service + `set_settings_service()` called in lifespan at the existing post-manager injection block (api.py:2451-2453 — where `set_project_repository` is imported under the local alias `set_settings_project_repo`; the alias is an import-site name only, the real router symbol is `set_project_repository`, settings.py:101), getter raising `HTTPException(503, ...)` when unwired (mirrors `get_project_repository()` at settings.py:95-97; a cheap backstop for test harnesses that bypass lifespan, not load-bearing in production). **Runtime engine (reconciled, architect Ruling 2):** `set_settings_service(SettingsService(engine=manager.engine))` — the manager's single shared engine, no second pool; the throwaway engine exists ONLY inside the boot read (ruling b). **Resolver SSOT (reconciled, blind spot b):** lifespan also calls `set_settings_resolution_snapshot(snapshot)` right after `set_settings_service`. GET semantics: `value` = `snapshot.applied_value` (the boot-applied running-truth carriage; NOT a resolver tier — the `resolve(key, snapshot)` ladder stays live DB > live env > snapshot yaml > default, untouched); `source` = live re-resolve over that ladder — divergence between `value` and `source` is HONEST restart-required reporting, not drift. The snapshot is stored module-global, not on `app.state`.

**Auth/scope (RECONCILED — architect Ruling 4 OVERTURNS the earlier "GET open" reading):** GET gated `scope_required("settings:read")`; PUT/DELETE gated `scope_required("settings:write")`; **BOTH scopes registered in `SCOPE_TAXONOMY` (`daemon/auth/scopes.py:53-83`)** — the taxonomy is CLOSED (`normalize_scope_list` rejects unknown names, scopes.py:120-122), so registration is mandatory, not cosmetic, and no `settings:*` scope exists today. The overturn rationale: the earlier reading mixed two precedent classes in this same router — the user-preference class gates NOTHING (language/timezone/editor GETs *and* PUTs ungated, settings.py:160/169/194/208/246/269/289), while the **infrastructure class gates reads** (`snapshot-create` GET :661 + PUT :675 both `scope_required("snapshot:write")` — GET/PUT citations, NOT POSTs; `sources` gates its GETs, sources.py:126/324; control-plane reads are read-scoped: `instances:read`, `maintenance:read`, `workspace:read`). `GET /api/settings/daemon` reports effective daemon config **including env/yaml tier values** — infrastructure class; its right precedent is instances/maintenance, not language/timezone. Naming conforms to the established `<resource>:<verb>` family (the verified family list cited earlier was a subset — the full taxonomy also includes `system:*`, `messages:write`, `mcpkb:admin`, `prompts:*`). No `settings:admin` is minted (security addendum 3: the session lane IS the operator; `-admin` verbs are reserved for CRUD-over-auth-entities). Cost of gating: zero — the SPA session lane auto-carries the taxonomy (scopes.py:36-40, :93) and every gate is inert when auth is OFF (decorators.py:128-130).

**Trade-off considered:** a generic `/api/settings/daemon/{key}` KV passthrough without the registry — rejected: it would let the FE/API write un-validated, un-applied keys that silently do nothing, which is worse than refusing. Registry-gating keeps the API surface honest.

**RESOLVED (was OPEN for architect) — DECIDED by architecture-recommendation.md Ruling 4:** both `settings:read` and `settings:write` are registered in `SCOPE_TAXONOMY` (scopes.py:53-83); GET → `settings:read`, PUT/DELETE → `settings:write`. The previously-considered reuse candidate is moot: no write scope exists for the read-only `maintenance` family (scopes.py:63), and the taxonomy is closed to unregistered names anyway. GET gating is DECIDED (see the auth/scope paragraph above — infrastructure precedent class).

---

## Ruling (e): FE — extend the EXISTING `/settings` page with a new "Daemon" section; restart badge + tier chips. RECOMMENDED.

**Recommendation:** the settings page already exists (`frontend/src/app/pages/settings/settings.component.*`, routed at `app.routes.ts:50`, reached from the gear menu "Settings" item, `app.ts:566-576` → `app.html:77-87`). ADD a fifth `<section class="setting-section">` ("Daemon") to it — do NOT create a new page/route.

Section contents (single managed key at v1, rendered data-driven from the GET payload so future keys fall out for free):
- Numeric input for LLM worker limit, `min=1` validation (mirrors Field `ge=1`), integer-only.
- Effective value line + **tier chip** showing which source is active: `DB` / `ENV` / `YAML` / `DEFAULT` (from GET `source`), plus read-only display of the lower tiers when present (`env_value`, `yaml_value`, `db_override`) so operators can see the full ladder.
- **"Requires restart" badge/hint** — prominent, always shown for `requires_restart: true` keys (hard requirement #6). After a successful PUT, show an explicit "saved — takes effect after daemon restart" confirmation rather than pretending live-apply.
- Save button → PUT; "Reset to file/env" (clear override) → DELETE.

**Service:** add API client methods + response types to the **global** `frontend/src/app/services/settings.service.ts` — the settings page is already its consumer (LanguagePreference/TimezoneOptions/SnapshotCreatePreference contracts live there), so the daemon-settings contract belongs in the same home rather than a page-colocated service. (Colocated `*.service.ts` remains the right pattern for page-private concerns like checkpoint-cleanup; this is not one.)

**Styling:** Material M3 idioms consistent with the existing four sections; styles in `settings.component.scss`; no design-tokens directory exists on this tree (verified absent — ignore any reference to `frontend/design-tokens/`).

**RESOLVED (was OPEN for architect) — DECIDED by architecture-recommendation.md Ruling 5:** the muted co-bound info line ships, with CORRECTED arithmetic — the effective bound is the emergent **7 = 5 worker + 2 chat concurrent graphs** (both thread pools bridge onto the single semaphore-gated asyncio loop; `WORKER_POOL_SIZE=5` + `CHAT_WORKER_POOL_SIZE=2`, `daemon/constants.py:86,94`), NOT "~5". Binding wording: *"Real parallelism is also capped by the fixed worker thread pools (5 worker + 2 chat = 7 concurrent graphs — constants, not settable here)."* Documentation only, no control; no code-level `min()` exists — the cap is emergent from the pool sizes. The registry carries the `effective_max` hint (import-computed) so the number cannot drift.

---

## Ruling (f): Tests — three layers, integration light. RECOMMENDED.

| Layer | File (new unless noted) | Covers |
|-------|------------------------|--------|
| Unit — resolver | `tests/test_settings_resolution.py` | Full precedence ladder: db > env > yaml > default; **the shadowing case** (yaml literal present + `LIMITS_LLM_CONCURRENCY` set → env wins for the managed key — pins the gotcha); no env → yaml literal used; nothing set → default; invalid DB value ignored (fail-open) + ERROR logged; registry gate rejects unknown keys; `apply_db_settings_overrides` mutation visible on the Config object (drives manager.py:555 semantics without constructing a manager) |
| Unit — bootstrap read | same file | `apply_db_settings_overrides` against a **real temp SQLite engine** with seeded rows — proves the throwaway-engine path end-to-end minus lifespan; missing-table posture |
| API | `tests/test_daemon_settings_api.py` | GET/PUT/DELETE via `TestClient` wired with `set_settings_service(...)` + temp SQLite service (DI idiom per `tests/test_scheduler_api.py:22-143` mock-manager/app/client fixture shape); 503 when unwired; 404/422 unknown key or bad value; **scope gates: GET `settings:read` 403-without/200-with AND PUT/DELETE `settings:write` 403** (fixture tests per architecture-recommendation.md Ruling 4); **`sensitive` masking test: GET omits `env_value`/`yaml_value`/`db_override` for sensitive keys, and pins v1's key `sensitive=False`** |
| FE spec | EDIT `frontend/src/app/pages/settings/settings.component.spec.ts` | Section renders; `min=1` validation blocks save; restart badge visible; tier chip reflects mock GET payload; PUT/DELETE calls |
| Migration runner smoke | new ~10-line test in the phase-4 test files | **Apply just `20261018_000001_create_app_settings.sql` to a temp SQLite DB via the real migration runner** — makes the migration acceptance non-vacuous on PG dev hosts (where the runner is otherwise a no-op and the .sql never executes) |
| Source-order pin | in the phase-4 test files | **Text-order grep-pin: `apply_db_settings_overrides(` appears in `daemon/api.py` between `load_config()` (:401) and `InstanceManager(` (:543)** — cheapest lockstep pin on the load-order invariant (grep-pin pattern per the secrets migration header) |
| Integration | NONE beyond the above (deliberate) | full-lifespan PG e2e EXCLUDED (architect Ruling 6: disproportionate — the daemon cannot boot on SQLite for e2e, so it would need disposable-PG spin-up per the e2e convention); the load-bearing invariants are covered by the resolver unit + temp-SQLite bootstrap unit + source-order pin; the true end-to-end acceptance stays the manual completion sentence (restart → DB tier active) |

**Exit gate for the test phase:** the shadowing-case unit test is the acceptance centerpiece — if it doesn't fail against a naive implementation, the test is wrong.

**Review gate (convention, verified `agents/reviewer/workflow.md:163-194`):** reviewer convenes the governor council via `convene_council_with_skill(..., max_councilors≤4)` — deep review only; the approver does NOT convene councils. Suggested `councilor_skill`: `code-review` (dominant review type for a storage+API+FE slice).

**RESOLVED (was OPEN for architect):** integration-test question decided by architecture-recommendation.md Ruling 6 — full-lifespan PG e2e excluded; source-order pin test + migration-runner smoke added instead (rows above).
