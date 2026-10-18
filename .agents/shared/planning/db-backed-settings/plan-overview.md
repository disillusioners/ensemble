# Plan Overview: DB-backed runtime settings — first setting: LLM worker limit — plus FE settings page

Date: 2026-10-18
Author: planner[v2] via plan-creation worker
Status: Reconciled — iteration 002 (architect rulings folded; approver iteration-001 blocking fix + notes applied). Pending approver re-check.
Worktree: `/home/nea/ensemble-src-wt-db-backed-settings` (branch `feature/db-backed-settings`, base latest @ e0e826866)
Companion docs: `decisions.md` (rulings a–f, each with OPEN items), `phase1-plan.md` … `phase4-plan.md`

---

## Objective

Operators can set the daemon's LLM worker limit (`limits.llm_concurrency`) from the Frontend, with the value persisted in the database, surviving restarts, and resolving with explicit precedence **DB > env > yaml > default** — replacing today's situation where the env tier is silently shadowed by the yaml literal and the only way to change the knob is editing config.yaml on the host. v1 semantics are honestly **restart-required** (the FE says so clearly); no live semaphore re-arm.

Testable completion sentence: *On a running daemon, a user sets the LLM worker limit from the settings page, sees "requires restart", restarts, and the new value is the one constructing the LLM semaphore — with the settings page showing the DB tier as active — without anyone editing config.yaml or the environment.*

## Background (evidence-verified, this worktree)

- The knob: `LimitsConfig.llm_concurrency` — `Field(default=10, ge=1)` (`daemon/config.py:593`), yaml key `limits.llm_concurrency` (`config.yaml:193`).
- Binding: constructed ONCE into a daemon-wide `asyncio.Semaphore` at `InstanceManager.__init__` (`daemon/manager.py:555`); sole acquire site `daemon/services/instance_messaging.py:4887` wrapping `graph.astream`. Read at process startup (`daemon/api.py:401` `load_config()` → `:543` `InstanceManager(...)`). No hot reload; `config.yaml:185-186` itself documents restart-after-edit. → restart-required is the honest v1 semantic.
- **Env gotcha (the reason the env tier is dead today):** `load_config` passes yaml sections as pydantic init kwargs (`config.py:5560-5561` → `Config(**config_dict)` `:5908`); init kwargs beat env vars in pydantic-settings, so `LIMITS_LLM_CONCURRENCY` (`env_prefix="LIMITS_"`, `config.py:588`) has NO effect while the yaml literal exists. The plan's resolver computes the env tier explicitly (ruling c).
- Storage is GREENFIELD (grep = 0 for app_settings/SettingsService); router (`daemon/routers/settings.py`, 763 lines) and FE settings page (`frontend/src/app/pages/settings/`) are EXTEND.
- Bootstrap: `ensemble.json` (+ `POSTGRES_*` env) — the DB connection spec — is loaded BEFORE config.yaml by existing design (`daemon/api.py:386-394`, in-code comment says "chicken-and-egg resolution"). The settings read needs NO new bootstrap machinery (ruling b).

## Scope

### In Scope
- New `app_settings` SQLModel table + one additive migration + thin `SettingsService` (registry-gated managed keys, tier resolver, bootstrap read+apply).
- Explicit 4-tier precedence for managed keys: DB > os.environ > yaml literal > field default, with per-key source reporting.
- Lifespan hook `apply_db_settings_overrides(config, ensemble_config, raw_yaml_limits=None)` between `load_config()` (api.py:401) and `InstanceManager(...)` (api.py:543) returning `(config, snapshot)` — snapshot stored module-global via `set_settings_resolution_snapshot(...)` — restart-apply with zero changes at the semaphore site. **All api.py edits are Phase-1-owned (t6); Phase 2 edits none.**
- Three API endpoints on the existing settings router: GET `/api/settings/daemon` (gated `settings:read`), PUT/DELETE `/api/settings/daemon/{key}` (gated `settings:write`); BOTH scopes registered in `SCOPE_TAXONOMY` (architect Ruling 4).
- FE: one new "Daemon" section on the existing `/settings` page — numeric input (ge=1), tier chips, prominent "requires restart" badge, save/clear-override.
- `config.yaml` comment update documenting the new ladder (comments only — the yaml literal STAYS as tier 3).
- Tests: resolver unit (incl. the shadowing case), bootstrap-read unit vs temp SQLite, source-order pin test (api.py load-order), migration-runner smoke (20261018_000001 on temp SQLite), API tests (GET `settings:read` gate + `sensitive` masking + `settings:write` gates), FE specs.
- Read-only documentation line in FE for the worker-thread-pool co-bound (`daemon/constants.py:86,94`: 5 worker + 2 chat = 7 concurrent graphs) — documentation, not controls.

### Out of Scope
- **Live-apply / semaphore re-arm** — v1 is restart-required (commission constraint; `config.yaml` itself documents restart semantics; re-arming a shared asyncio.Semaphore mid-flight is not trivially safe and is not gold-plated here).
- **Other settings keys** — v1 registry contains exactly ONE managed key (`limits.llm_concurrency`). The architecture makes adding keys cheap (registry entry + nothing else), but no second key ships in this slice.
- **Compaction chunk concurrency** (`config.py:1325`, default 3) — bypasses the semaphore entirely; not exposed, not managed.
- **WORKER_POOL_SIZE / CHAT_WORKER_POOL_SIZE controls** — compile-time constants (`daemon/constants.py:86,94`); their emergent cap is 7 concurrent graphs (5 + 2, both pools bridging onto the semaphore-gated loop); documented in FE, not settable.
- **Marker-resolution / redaction / secrets** — unrelated surfaces.
- **Generic KV API without registry gating** — refused by design (ruling d).
- **New FE page/route or nav changes** — the existing page and gear-menu entry are reused as-is.

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Backend storage + resolution + boot apply | Table, migration, SettingsService with registry + tier resolver, lifespan boot-apply | 7 | Tight with 2 (service contract); independent of 3 | pending |
| 2 | Settings API | GET/PUT/DELETE on existing router + scope + schemas + DI | 5 | Tight with 1 (service), tight with 3 (API contract) | pending |
| 3 | FE settings surface | "Daemon" section on existing settings page + service client | 5 | Tight with 2 (API contract); independent of 1 | pending |
| 4 | Tests + review gate | Resolver/bootstrap/API/FE tests + source-order pin + migration-runner smoke; docs; governor council review | 7 | Loose with 1-3 (consumes all contracts) | pending |

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| Phase 1 | — | **tight** — `SettingsService` public surface (`resolve`, `get_overrides/set/clear`, `apply_db_settings_overrides`) is Phase 2's entire backend | independent (FE only sees API JSON) | loose (Phase 4 unit-tests Phase 1 directly) |
| Phase 2 | tight | — | **tight** — API JSON contract (`{key, value, source, default, env_value, yaml_value, db_override, requires_restart, description}`) is Phase 3's input; freeze it in Phase 2 | loose |
| Phase 3 | independent | tight | — | loose (FE spec consumes contract) |
| Phase 4 | loose | loose | loose | — |

Cross-phase risk: the API response shape is the shared contract between 2 and 3 — define it in Phase 2 task 1 and freeze before Phase 3 starts (contract drift mid-flight = rework in 3 files).

**Ownership invariant (iteration-002, Shape A — binding):** every `daemon/api.py` edit is owned by exactly ONE phase — **Phase 1 (t6)**, which also creates the router-DI symbols (`_settings_service` / `set_settings_service()` + 503 getter, `set_settings_resolution_snapshot()` / getter) inside `daemon/routers/settings.py`. Phase 2 owns ZERO api.py edits: its t2 consumes/verifies the P1-created DI and its t5 verifies the P1-t6-owned wiring is present and green. P1's exit criterion references only symbols created within Phase 1. Phase 1 therefore runs to completion (including t6) before Phase 2's endpoints are implemented.

## Rollout & Rollback (adopted note 8)

- **Rollout is purely additive:** one new `app_settings` table (own `.sql`, `create_all`-registered via late-import), one new service module, three new endpoints, one new FE section — no existing column, row, table, or endpoint is altered.
- **The boot read can never fail the boot:** `apply_db_settings_overrides` is fail-open by construction (missing table → no overrides; dead/unreadable DB → ERROR log + lower tier; corrupt row → skipped with class-specific ERROR). Worst case at every failure point is "the feature is inert", never "the daemon does not start".
- **Rollback paths (all safe, none destructive to existing state):** remove a DB override via the DELETE endpoint (or `DELETE FROM app_settings WHERE key=...` direct — unregistered/unknown rows are refused/ignored by the service); full feature rollback = `DROP TABLE app_settings` (or leave it — a missing table is indistinguishable from "no overrides") + revert the code slice; the daemon boots identically with the table absent.
- **No data-loss surface:** `app_settings` holds only operator-set overrides; config.yaml, env vars, and field defaults are never written to by this feature.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Resolver reintroduces the shadowing bug in a new form (e.g. reads pydantic's env tier instead of os.environ) | High — the env tier silently dead again, core requirement #2 unmet | Medium | Dedicated unit test pins yaml-literal-present + env-set → env wins (Phase 4); `decisions.md` ruling c makes the "never rely on pydantic env tier for managed keys" rule explicit at implementation time |
| 2 | Bootstrap settings read creates a new boot-failure mode (DB hiccup at startup kills daemon before manager engine build) | Medium | Medium | Fail-open posture: try/except around the read, ERROR log, continue with lower tier (ruling b); missing-table treated as "no overrides" (un-migrated DBs) |
| 3 | API/FE contract drift (response shape changes after FE built) | Medium | Medium | Freeze the JSON contract as Phase 2 task 1 with the FE types mirrored verbatim in `settings.service.ts`; coupling map marks it the only tight cross-track seam |
| 4 | Migration ordering / dual-driver mistakes (new table not in `create_all`, or .sql sorts before secrets migration) | Medium | Low | Filename `20261018_000001_create_app_settings.sql` sorts after `20261017_000001_create_secrets_tables.sql` (verified latest); follow the `custom_agent_configs` dual-driver header pattern; runner is SQLite-only (no-op on PG — PG schema comes from `SQLModel.metadata.create_all()`) |
| 5 | Un-validated DB value poisons boot (e.g. `llm_concurrency: 0` or `"...junk"` stored) | High (boot crash or silent no-op) | Low | Validate at write (API) AND at read (re-validate against Field constraints before assignment; reject+log invalid, keep lower tier) — ruling b step 3, ruling c OPEN item (i) resolved as belt-and-braces |
| 6 | Users expect live-apply and file bugs when the knob "doesn't work" until restart | Low | Medium | FE shows the restart badge prominently BEFORE and AFTER save (hard requirement #6); PUT response carries `restart_required: true`; GET exposes it per key |
| 7 | Scope-gating mismatch locks legitimate users out of GET/PUT (auth-on installs) | Low | Medium | RESOLVED by architect Ruling 4: register BOTH `settings:read` and `settings:write` in `SCOPE_TAXONOMY` (`daemon/auth/scopes.py:53-83`, closed taxonomy — registration mandatory, scopes.py:120-122); GET → `settings:read`, PUT/DELETE → `settings:write`; gates inert when auth off |
| 8 | Co-bound confusion: raising llm_concurrency above 7 appears to "do nothing" (5 worker + 2 chat = 7 concurrent graphs cap — emergent from the two pool sizes, no code-level `min()`) | Low | High | FE muted info line documents the co-bound with the corrected 7 arithmetic (ruling e / architect Ruling 5); out of scope to expose controls |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | DB tier wins precedence | Unit + API: PUT override → GET shows `source="db"`, `value=<stored>` | 100% of precedence unit tests pass, incl. the shadowing case |
| 2 | Env tier effective for managed keys despite yaml literal | Unit: yaml `llm_concurrency: 10` present + `LIMITS_LLM_CONCURRENCY=7` set → effective 7, `source="env"` | test passes; fails against naive pydantic-env-tier impl |
| 3 | Restart-apply works end to end | Bootstrap-read unit vs temp SQLite: seeded row → `apply_db_settings_overrides` mutates `config.limits.llm_concurrency` → value is what `InstanceManager.__init__` would read | asserted value equality on the Config object |
| 4 | FE settable + persisted + honest restart UX | FE spec + API tests: input validated `min=1`; PUT persists row; restart badge always visible for `requires_restart` keys | spec green; badge renderable in every state |
| 5 | No new boot-failure mode | Unit: missing table / dead DB / corrupt row → boot-apply returns Config with lower-tier value + ERROR log, no raise | all failure-posture tests pass |
| 6 | Migration conventions honored | `ls daemon/migrations/versions/` — exactly one new `.sql`, name `20261018_*`, sorts after `20261017_000001`; dual-driver header present | reviewer checklist item |
| 7 | Scope tight: exactly one managed key | grep registry: single entry `limits.llm_concurrency` | reviewer checklist item |
| 8 | Full test suite green | pytest + FE jest | 0 new failures vs base e0e826866 |

## Files-to-Touch Summary

**New (backend):**
- `daemon/models/app_settings.py` — `AppSetting` SQLModel table (registered in `SQLModel.metadata` so PG `create_all` picks it up; mirror the import-registration pattern used by manager.py:580-587 for late tables)
- `daemon/migrations/versions/20261018_000001_create_app_settings.sql` — SQLite-side persistent record (dual-driver header per `custom_agent_configs` precedent)
- `daemon/services/settings_service.py` — managed-key registry, tier resolver, bootstrap read+apply (`apply_db_settings_overrides`), override CRUD
- `tests/test_settings_resolution.py` — resolver + bootstrap-read units (incl. the shadowing case)
- `tests/test_daemon_settings_api.py` — API tests per the `test_scheduler_api.py` fixture idiom

**Edited (backend):**
- `daemon/api.py` — **Phase-1-owned exclusively (t6)**: lifespan `config, snapshot = apply_db_settings_overrides(...)` between :401 and :543; post-manager wiring at the :2451-2453 block: `set_settings_service(SettingsService(engine=manager.engine))` + `set_settings_resolution_snapshot(snapshot)`. Phase 2 performs ZERO api.py edits (verify-only)
- `daemon/routers/settings.py` — split by phase: **P1 t6** adds the module-global service DI + resolution-snapshot setter/getter (503-when-unwired idiom, :95-101 pattern); **P2** adds the 3 endpoints
- `daemon/routers/schemas.py` — `DaemonSettingsResponse` / `DaemonSettingEntry` / `DaemonSettingUpdate` models (incl. `sensitive` + masking rule)
- `daemon/auth/scopes.py` — register BOTH `settings:read` and `settings:write` in `SCOPE_TAXONOMY`
- `daemon/manager.py` — ONE-line addition only: import `AppSetting` into the late-import block (:580-645) so the table joins `create_all` (architect blind spot d)
- `daemon/config.py` — **minimal, P1-owned (iteration-003 shape (a))**: raw-limits module accessor only — module-global captured inside `load_config` from the processed limits-section dict BEFORE the pydantic merge, exposed via a getter (precedent `_install_service_tool_enabled`); no other config.py change; keeps its read-only anchor-reference role for limits/env-prefix/interpolation/load_config
- `config.yaml` — comments only (document the ladder; literal stays)
- `frontend/src/app/services/settings.service.ts` — daemon-settings types + GET/PUT/DELETE client methods
- `frontend/src/app/pages/settings/settings.component.ts/.html/.scss` — new "Daemon" section
- `frontend/src/app/pages/settings/settings.component.spec.ts` — section specs

**Read-only (reference, not touched):** `daemon/services/instance_messaging.py:4887`, `daemon/constants.py:86,94`, `daemon/repositories/factory.py:138,232`. (the limits/env-prefix/interpolation/load_config anchor-reference file moves to the edited list — see the minimal raw-limits accessor below; its anchor-reference role is unchanged.) (`daemon/manager.py` moves to the edited list — the one-line late-import registration above; the semaphore site :555 itself is untouched.)

## Research Insights

- Bootstrap ordering pre-solved: `daemon/api.py:386-394` loads `ensemble.json`→`EnsembleConfig` BEFORE `load_config()`, with the explicit "chicken-and-egg resolution" comment; `create_postgres_engine` reads `POSTGRES_*` env over ensemble.json values (`daemon/repositories/factory.py:232-262`). This de-fanged the hardest design question (b).
- `Config` is mutable plain `BaseSettings` (`config.py:4070-4073`) → post-load field overwrite works without `model_copy(validate=...)` gymnastics; re-validate manually.
- Existing `/settings` FE page + gear-menu entry reused; page uses `<section class="setting-section">` blocks; API contracts for settings live in global `settings.service.ts`.
- `scope_required` FastAPI-dependency idiom (`daemon/auth/decorators.py:160`) with `<resource>:<verb>` scope families in `daemon/auth/scopes.py`.
- Review flow: reviewer convenes governor council via `convene_council_with_skill(max_councilors≤4)` (`agents/reviewer/workflow.md:163-194`); approver does not convene councils.

## Open Questions (for architect)

**ALL RESOLVED** by `architecture-recommendation.md` (Rulings 1–6 + blind spots a–d + security addendum), reconciled into this plan set 2026-10-18. Historical list kept for traceability:

1. Ruling (a): PG column types — DECIDED: keep draft verbatim (TEXT/JSON-string, registry-side validation; no native typed columns).
2. Ruling (b): bootstrap engine — DECIDED: throwaway engine for the boot read; runtime `SettingsService` rides manager's shared engine; fail-open both classes.
3. Ruling (c): belt-and-braces rejection of invalid stored values — DECIDED: yes at write (422) + boot re-validation guards tampering, fail-open to lower tier.
4. Ruling (d): scopes — DECIDED: BOTH `settings:read` and `settings:write` registered in `SCOPE_TAXONOMY` (scopes.py:53-83); GET gated `settings:read` (OVERTURNS the earlier "GET open" recommendation — infrastructure precedent class); no `settings:admin`.
5. Ruling (e): co-bound line — DECIDED: ships with corrected arithmetic, 7 = 5 worker + 2 chat concurrent graphs (binding wording in decisions.md ruling e).
6. Ruling (f): full-lifespan PG e2e — DECIDED: excluded (disproportionate); source-order pin test + migration-runner smoke added instead.
