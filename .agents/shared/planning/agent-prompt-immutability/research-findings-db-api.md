# Research Findings — agent-prompt-immutability (DB + API conventions)

Commission: read-only mapping of the conventions a PromptVersion DB schema + REST approval API must follow.
Investigated in worktree `/home/ensemble-worktrees/agent-prompt-immutability` (branch `feature/agent-prompt-immutability`, base latest @ e9cdc25c66). All `file:line` cites verified in this worktree unless marked otherwise. Compiled 2026-10-13 by Explorer.

---

## Summary

- **Migrations**: `daemon/migrations/versions/` holds **92 checksummed `.sql` files**, `YYYYMMDD_HHMMSS_name.sql`, each with `-- UP` / `-- DOWN` sections. The runner is **SQLite-only by design** (PostgreSQL never executes them); PG parity comes from the SQLModel models (`metadata.create_all` at boot) plus `EnsembleManager._ensure_postgres_columns` for evolving existing PG tables. Every new table ships its own checksummed `.sql` artifact + a SQLModel model with byte-identical index/constraint names. Best copy templates: `20261011_000001_create_stored_watches.sql` and `20261007_000001_create_drift_events.sql`.
- **Repositories**: `daemon/repositories/<domain>/{models.py, repository.py}`; repo class = `__init__(self, engine: Engine)` only, every method opens its own short-lived `Session`, all-sync, bridged with `asyncio.to_thread`. Factory `create_*` functions in `daemon/repositories/factory.py`, exported via `daemon/repositories/__init__.py`, constructed in `InstanceManager.__init__` (manager.py:1159–1177).
- **Services**: thin async facades holding **no engine** — one `asyncio.to_thread` per method wrapping the repo call(s) (`daemon/services/skill_store_service.py`). Routers reach services via `request.app.state.*` (auth-scoped) or `request.app.state.manager` (manager-owned).
- **Routers + auth**: registered on `api_router` in `create_app` (api.py:3575–3641, `app.include_router(api_router)` at :3641 BEFORE the SPA catch-all). Auth = pure-ASGI `AuthMiddleware` (outermost) attaching `AuthContext{subject, scopes, auth_method, expires_at, meta}` at `request.state.auth_context`; per-route authorization via `@require_scope(...)`. **"Human" = `auth_method == "session"`** (OIDC cookie lane); bearer = machine lane. Client-supplied origin fields are forgeable (filed ticket 257488dc) — approval gating must key on `AuthContext`, never body fields.
- **Precedents**: job `VALID_TRANSITIONS` set-of-tuples + `JobStateMachine` (draft→pending→approved/rejected guard), `Skill.generation` monotonic counter + `UniqueConstraint(project_id,name,generation)` + append-only `SkillLineage` (versioned rows + active-pointer), blueprint pending queue (status lifecycle + claim/ack), `drift_events` table (audit/event-row convention). No generic audit table exists; convention is a purpose-built event table per domain.
- **Frontend**: Angular `frontend/src/app/services/` — one `@Injectable` service per API family, `inject(HttpClient)`, signals, `{items,total}` unwrap. A prompt-version review/approve page slots in beside skills/skill-bank naturally.

---

## 1. Migration conventions + templates

### Directory, naming, ordering
- Location: `daemon/migrations/versions/*.sql` — **92 files** currently, oldest `20240101_000001_add_job_queue_paused.sql`, newest `20261011_102502_set_checkpoint_trio_autovacuum_reloptions.sql`.
- Filename grammar: `^\d{8}_\d{6}_(.+)$` → `YYYYMMDD_HHMMSS_name.sql`; version = the timestamp prefix, name = suffix with `_`→space (runner.py:88–95). Files sorted by version string (runner.py:297–304).
- Two sub-formats exist for the timestamp: `HHMMSS=000001/000002…` sequence-per-day and unique wall-clock stamps (`20260619_120000`, `20260906_192100`, `20260915_212810`, `20261011_102502`). ⚠️ **Version strings must be unique**: `20260628_000002` was reused by TWO files (`20260628_000002_drop_admission_legacy.sql` and `20260628_000002_drop_job_queue_legacy_columns.sql`) while the ledger is keyed on the version string alone (`get_applied_versions()` returns a set of versions, runner.py:259–283, 306–314). Don't reuse a stamp.

### File format (parsed by `MigrationFile.parse`, runner.py:72–136)
- `-- UP` section mandatory; `-- DOWN` optional (rollback uses it, runner.py:98–105, 626–691).
- Optional header markers: `-- MANUAL: TRUE` (never auto-applied, runner.py:107–113) and `-- PRECONDITION: sqlite>=X.Y.Z` (driver-semver floor; fail → skip-WITH-ledger-marker, never fails boot, never re-fires; runner.py:115–136, 353–393, 476–501). Malformed-look-alike markers WARN loudly (runner.py:129–136).
- Comment-only stub files are legal no-ops (reserved version stamps): `20261011_030500_create_grants.sql` is 16 lines of comments with empty UP/DOWN, accepted as an inert ledger row.

### Checksum + ledger
- Checksum = SHA-256 of the full file text (runner.py:66–70), stored per-apply on the `schema_migrations` row (`SchemaMigration(version, name, applied_at, execution_time_ms, checksum, precondition, skip_reason)`, runner.py:417–474). The ledger table self-heals missing columns at boot (runner.py:250–257).

### Apply path
- `apply_migration` (runner.py:503–624): precondition gate → **one transaction per migration via `engine.begin()`** (runner.py:543) → full-line `--` comments stripped, then naive `split(";")` statement split (runner.py:554–557) → per-statement idempotent-error tolerance ("duplicate column name", "no such table" for CREATE only, "already exists", "no such column", "has no column" — runner.py:562–605) → ledger row written **inside the same transaction** via `bind=conn` (runner.py:612–619). Non-tolerated errors raise `MigrationError` → boot fails.
- ⚠️ The `split(";")` parser: semicolons inside *inline trailing* comments or string literals corrupt the split (runner.py:546–557, 715–718). Keep DDL plain.
- `rollback_migration` (runner.py:626–691): runs DOWN in one txn, deletes the ledger row; DOWN side honors the same precondition gate.

### Dual-driver reality (the big one)
- **The runner is a NO-OP on non-SQLite engines** — binding docstring at runner.py:693–718: fresh PostgreSQL DBs get schema from `SQLModel.metadata.create_all()` at manager init; existing PG DBs evolve via `EnsembleManager._ensure_postgres_columns` (manager.py:6145+, idempotent ADD-only `IF NOT EXISTS` block; the docstring explicitly mandates: "Every new .sql migration that adds schema elements which must exist on existing PG databases MUST also extend `_ensure_postgres_columns`"). "Do not 'fix' this to apply .sql files on PostgreSQL."
- Corroboration: `20260713_000001_create_skill_bank.sql:8–12` ("NO-OP on PostgreSQL… handled by raw DDL in daemon/manager.py `_ensure_postgres_columns()`"); `20261011_000001_create_stored_watches.sql:11–30` (dual-driver header).
- **Brand-new tables need NO `_ensure_postgres_columns` mirror** — `create_all` creates them on fresh AND existing PG DBs at next boot (stored_watches header :14–22; drift_events header :11–18). The ensure path is only for adding columns/indexes to *existing* tables.

### Table↔file pairing rule
- Standing convention (Core Architecture blueprint + the 92-file corpus): every new SQLModel table ships its own checksummed `.sql` migration file — "the file is the persistent record (runner is SQLite-only and cannot stand in); zero exceptions."
  - Verified pairings: `skills` family (6 SQLModel tables in `daemon/repositories/skill/models.py`) ↔ `20260710_000001_create_skill_tables.sql`; `SkillBankItem` ↔ `20260713_000001_create_skill_bank.sql`; `StoredWatch` (`daemon/repositories/job_queue/stored_watch_models.py`) ↔ `20261011_000001_create_stored_watches.sql`; `DriftEvent` (`daemon/plugin_subsystem/drift_event_publisher.py`) ↔ `20261007_000001_create_drift_events.sql`.
  - **Nuance**: granularity in practice is one `.sql` per feature table-GROUP (create_skill_tables.sql = 6 tables; create_snapshot_tables.sql = plural), not literally one file per table. One file per table is the safest reading for a 2-table feature (versions + audit).
  - Known adjudicated exception: `RepairLog` (`daemon/repositories/ens_db/models.py`) was registered with `SQLModel.metadata` to be created on both engines *without* a hand-written migration (manager.py:90–94, "adjudication A4"). Don't follow this exception — the .sql file is the default rule.

### Copy templates (most recent table-creating migrations)
1. **`20261011_000001_create_stored_watches.sql`** (65 lines) — the freshest full template:
   - Rich header documenting purpose, phase, planning ref, DUAL-DRIVER NOTES, and the **index-name parity contract** ("Index / unique-constraint names MUST stay byte-identical to the model's `__table_args__` declaration (the canonical source)", :23–27).
   - `CREATE TABLE IF NOT EXISTS stored_watches (...)`: TEXT PK (`stored_watch_id TEXT PRIMARY KEY`), NOT NULL columns, `JSON NOT NULL DEFAULT '[]'` for list columns, `status TEXT NOT NULL DEFAULT 'armed'`, `created_at/updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP` (:34–45).
   - One UNIQUE index + four lookup/composite indexes, all `CREATE [UNIQUE] INDEX IF NOT EXISTS` (:47–56).
   - DOWN = drop indexes in reverse, then `DROP TABLE IF EXISTS` (:58–65).
2. **`20261007_000001_create_drift_events.sql`** (68 lines) — best template for an **audit/event-flavored table**: `id TEXT PRIMARY KEY`, verbatim payload fields + `observed_at/observed_tag/created_at TEXT NOT NULL`, three `ix_*` indexes, full dual-driver header including the three-way index-parity test contract (model `__table_args__` ↔ .sql ↔ `_ensure_postgres_columns`, :24–41) and a candid correction note about a previously false header claim.
   - (Honorable mention for a *stub-only* reservation: `20261011_030500_create_grants.sql`.)

### Timestamps convention
- Two live flavors: **TEXT ISO-8601 strings** (drift_events, skill_bank, and all SQLModel `default_factory=_now_iso` models — skill/models.py:66–73, 50–51) and **`TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP`** (stored_watches). The SQLModel/model-side convention (ISO strings via `_now_iso()`) is the majority pattern and cross-driver-safe; `JSONBType` (`daemon/repositories/infra/types.py`) is the dual-dialect JSON treatment for JSON-shaped columns (skill/repository.py:37–41).

### Pitfalls / DDL constraints (beyond the known 20260714_000001 fresh-SQLite breaker)
- **`20260714_000001_widen_job_queue_type_constraint.sql`** (`ALTER TABLE … DROP CONSTRAINT IF EXISTS`) is invalid on SQLite → breaks EVERY fresh SQLite boot (known critical note; PG unaffected since the runner skips PG). Documented workaround: ledger-pre-mark the migration with a `skip_reason` row. General lesson: **validate every statement against SQLite** — the runner only tolerates duplicate/missing-object errors, not syntax errors.
- Unique version stamps (the 20260628_000002 double-booking above).
- No semicolons in comments/strings (naive splitter).
- No PG-only constructs assumed in `.sql` — it runs on SQLite only; but the **SQLModel model** must simultaneously be PG-valid because create_all emits it there (use `JSONBType`, `INTEGER 0/1` booleans, TEXT PKs — skill/models.py:44–49, :116–121).
- Boolean storage as INTEGER 0/1 for cross-driver (skill/models.py:116–118); NULL-distinct UNIQUE semantics (both drivers treat NULLs as distinct in UNIQUE columns — skill/models.py:44–49) — relevant if you want "exactly one approved pointer per (agent, artifact)": NULLs won't collide; enforce at repo/service layer or avoid NULL in the key.

---

## 2. Repository + service wiring

### Layout
- `daemon/repositories/` = 24 domain subpackages (`auth_bootstrap/`, `auth_session/`, `blueprint/`, `db_connection/`, `ens_db/`, `event/`, `infra/`, `instance/`, `instance_ui_prefs/`, `job_queue/`, `maintenance_runs/`, `mcp_kb_keys/`, `mcp_server/`, `message_metadata/`, `message_queue/`, `project/`, `report_injection/`, `service_tool/`, `shared_meta_kv/`, `skill/`, `snapshot/`, `source/`, `task/`, `dependency_bus/`) + `factory.py` + `__init__.py` (276 lines of re-exports).
- Per-domain shape: `models.py` (SQLModel tables) + `repository.py` (repo class). `skill/` additionally has `skill_bank_repository.py`.

### Repository class shape (canonical examples)
- **Constructor takes ONLY `engine: Engine`** — `SkillBankRepository.__init__(self, engine)` (skill/skill_bank_repository.py:53–63); `BlueprintPendingRepository.__init__(self, engine)` (blueprint/pending_repository.py:77–78). The engine is the ONE shared engine from `create_engine_from_config` (factory.py; rationale: avoid SQLite lock contention — skill/repository.py:20–24).
- **Every public method opens its own short-lived `Session` and commits**; no instance state beyond the engine → thread-safe sharing (pending_repository.py:67–75).
- **All methods synchronous**; callers bridge via `asyncio.to_thread` — "the project's standard pattern" (skill/repository.py:33–35, 149–151; skill_bank_repository.py:18–19).
- CRUD style: `sqlmodel select`/`col()` queries + raw SQL only for atomic counters (`UPDATE col = col + 1` with a Python-frozenset column whitelist to prevent identifier injection — skill/repository.py:26–31, 102–116). Timestamps stamped explicitly with module-level `_now_iso()` on update paths (skill/repository.py:69–76; skill_bank_repository.py:37–43).
- Manager wires repos with `create_tables=False` — table creation belongs to the .sql migration + create_all, not the repo factory (manager.py:1159–1177).

### Factory + registration points (what a new PromptVersionRepository touches)
1. `daemon/repositories/prompt_version/` (new subpackage): `models.py` + `repository.py`.
2. `daemon/repositories/factory.py`: `create_prompt_version_repository(engine=…, create_tables=False)` — pattern at factory.py:50–77 (`create_mcp_server_repository`) and factory.py:1047 (`create_skill_bank_repository`).
3. `daemon/repositories/__init__.py`: re-export the repo class + factory function (alongside the existing skill imports at :46–60).
4. `daemon/manager.py`: import in the repositories import block (:32–61) and construct in `InstanceManager.__init__` next to the bank repo: `self._skill_bank_repo = create_skill_bank_repository(engine=self._engine, create_tables=False)` at **manager.py:1171–1173** (also `_source_repository` :1159, `_mcp_server_repository` :1168, `_instance_repository` :1177).

### Service pattern
- `daemon/services/skill_store_service.py` (516 lines) is the canonical thin-async-facade docstring (:1–60): service holds **no engine**; every async method = ONE `await asyncio.to_thread(...)` around the repo call; **multiple repo calls in one method share the same thread** to keep the session boundary clean (:49–53); best-effort side-effects (embedding refresh) are failure-isolated (:55–60). Services are imported and held by the manager (manager.py:111–122 imports ChildReportsService, ErrorReportingService, SkillStoreService, BlueprintWriteService, …).
- A `PromptApprovalService` fits: constructor takes the repo(s) (+ manager ref if it must touch instances); pure transition logic (or reuse a `JobStateMachine`-style module); routers get it from `request.app.state.*`.

### Router→service/repo DI (both live patterns)
- Manager-owned: `request.app.state.manager` → `manager._skill_bank_repo` (skill_bank.py:78–80, 100–105).
- App-state-owned (OAuth2-era): `request.app.state.auth_service` (auth.py:65–75) / `request.app.state.mcp_kb_key_store` (mcp_kb_keys.py:93–103) — wired in `create_app`/lifespan, 503 `{error, message}` when absent.

---

## 3. REST routers + auth model

### Registration
- Legacy families: `api_router.include_router(<router>)` lines at **daemon/api.py:3575–3620** (each with a `# /api/...` comment); OAuth2 families **conditionally when auth is ON**: `if auth_enabled:` → `from daemon.routers.mcp_kb_keys import router as mcp_kb_keys_router; api_router.include_router(mcp_kb_keys_router)` (**api.py:3629–3632**), same for auth_router (**:3636–3639**); then **`app.include_router(api_router)` at api.py:3641** — registered BEFORE the SPA catch-all (:2612 per the comment at :3615–3619) and before the MCP KB mounts (:3643+). First-match-wins ordering is load-bearing for path-prefix collisions (mcp_kb_keys vs the `/api/mcp/kb` module Mount — mcp_kb_keys.py:18–22).
- Router object: `router = APIRouter(prefix="/skill-bank", tags=["skill-bank"])` (skill_bank.py:21) / `prefix="/mcp/kb/keys", tags=["mcp-kb-keys"]` (mcp_kb_keys.py:40). Family composition precedent: `routers/jobs.py:44–46` includes crud/mgmt/stream sub-routers.

### Auth model (how an inbound request is authenticated)
- **`AuthMiddleware`** — pure ASGI, `daemon/middleware/auth.py`, registered FOURTH in `create_app` so LIFO makes it run OUTERMOST (Auth → HostCapture → SelectiveAccessLog → CORS) (:5–8). Three-class derivation from route attributes (public / protected / module-lane), logged once, O(1) per request (:10–28). Resolution chain: WS → OPTIONS → public → module-lane → S-19 → SPA assets → **bearer (FR-13 precedence)** → **cookie (+CSRF)** → 302/401 (:12–13). Never-raise: failures collapse to typed 401/302/503 (:26–28). `[AuthAudit]` single-line event taxonomy (:30–42).
- **`AuthContext`** (`daemon/auth/context.py:36–60`): `{subject, scopes, auth_method, expires_at, meta}`; `auth_method ∈ {"session","bearer"}` (:31–32). Attached at **`request.state.auth_context`** (context.py:5–7; middleware/auth.py:219 — `scope.setdefault("state", {})["auth_context"] = …`). `subject` = OIDC `sub` (session lane) or bearer owner label e.g. `operator-cli`.
- **Per-route authorization**: `@require_scope(*scopes)` wrapper or `scope_required(...)` FastAPI-dependency form — `daemon/auth/decorators.py:43–46, 110–124`. Reads `request.state.auth_context` (:105); **inert when auth OFF** (`app.state.auth_config` absent — :74–84); fail-closed `403 SCOPE_INSUFFICIENT` when ON (:101–107). P4 real-grant model: NO lane satisfies scopes by identity — the session lane is persisted with the operator family (the browser lane IS the operator); bearer carries exactly its minted scopes (:10–27).

### Human vs agent discrimination (the approval gate)
- **"Authenticated HUMAN" = `ctx.auth_method == "session"`** (OIDC cookie session — a logged-in browser user) with `subject` naming the human principal. Bearer = machine lane (CLI/scripts). Agents NEVER originate HTTP calls with a session — agent writes ride internal lanes (`internal_agent:` source via `send_message`; raw API messages don't trigger agent flows).
- **Do NOT trust client-asserted origin fields**: the R3 source-forgery ticket (critical note 257488dc) targets exactly this — `daemon/routers/jobs_crud.py:595` (`source=body.source`) and `messages.py:691` (`source="api"`) should derive origin from `AuthContext.subject` when auth is ON. Today the only HTTP-origin guard is the chat-prefix rejection at jobs_crud.py:560–586 (422 if `body.source` uses a chat-channel prefix). **The approval API must stamp approver identity from `AuthContext` (subject + auth_method), never from the request body.**
- Scope convention: `<module>:<action>` names (`jobs:write`, `sources:write`, `mcpkb:admin` — mcp_kb_keys.py:10–16). A new family would mint e.g. `prompts:approve`.
- ⚠️ **Auth-OFF posture is an open design decision**: when auth is disabled the middleware isn't registered, `auth_context` never exists, and `@require_scope` is inert (decorators.py:27–32) — the approval endpoint would be callable by anything that can reach the port (localhost/loopback deployments). The plan must choose: refuse approvals when auth OFF, or treat local-operator as the approver (document either way).

### Error-response + pagination conventions
- Typed error bodies: `JSONResponse(status, {"error": CODE, "message": …})` helper (auth.py:78–82; codes like `LOGIN_NOT_CONFIGURED`, `STATE_REPLAYED` at auth.py:15–21); `HTTPException(status, detail={"error", "message"})` (mcp_kb_keys.py:96–102); 422 validation with per-field `details` list (jobs_crud.py:604–612); 403 `SCOPE_INSUFFICIENT` (decorators.py:68–71, 107). 403 never 404 for authenticated-but-unauthorized (decorators.py:34–37).
- Pagination: `limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)` + separate `repo.count(...)` → response `{items: [...], total: n}` (skill_bank.py:72–108). Filter-params pattern: strip empty filters client-side (skill-bank.service.ts:63–75).
- Copy templates: **`daemon/routers/mcp_kb_keys.py`** (OAuth2-protected CRUD: scope decorator + auth_protected tag + app.state service + create-once-secret response shape) and **`daemon/routers/skill_bank.py`** (plain CRUD + pagination + manager-repo DI + to_thread bridging). `jobs.py` shows CRUD+transition composition (crud/mgmt/stream split).

---

## 4. State-machine / versioning precedents

### Transition guard — `JobStateMachine` (closest for draft→pending_approval→approved/rejected)
- `daemon/services/job_state_machine.py` (142 lines): 4-value `AdmissionState` vocabulary; **`VALID_TRANSITIONS: Set[Tuple[str, str]]`** of 8 allowed pairs (:53–62); `JobStateMachine.can_transition / validate_transition` (:84–100) treat same-state as implicit no-op; create = INSERT, never a transition (:22–27); `InvalidTransitionError(ValueError)` carrying job/from/to (:65–81). A PromptVersion state machine is a direct re-skin: draft→pending_approval, pending_approval→approved/rejected, (+ any recall edge), with approved rows append-only.
- The *consumer* of this machine is the repository's atomic transition (`repository.atomic_transition` with `(from, to)` logging — job_state_machine.py:29–34): state moves happen in guarded, logged, single-statement UPDATEs.

### Versioned rows + selection pointer — Skill evolution (closest overall)
- `daemon/repositories/skill/models.py`: **`Skill.generation`** = monotonic integer bumped per mutation, with `UniqueConstraint("project_id", "name", "generation")` (:44–49, :124–127) — the exact shape of "monotonic integer version code per (agent, artifact)".
- Mutation = **insert a NEW row** linked via `SkillLineage` (append-only parent/child DAG, composite PK, :13–17) — never edit the old row → this IS the append-only precedent.
- Active-variant selection = query "row with `is_active=True`" (`SkillRepository.get_active_variant`, models.py:93–97) — the precedent for a per-(agent, artifact) selection pointer (either an `is_selected` flag toggled transactionally, or a pointer column on an agent_artifact table).
- Status field precedent: `Skill.status` is application-defined free text ('active'/'inactive'/'archived') NOT constrained by the repo (models.py:119–121) — note the contrast with jobs, which formalizes transitions. For approval states, follow the JOBS style (formal machine) rather than free-text status.
- A/B machinery (`SkillABTest`, shared `ab_test_group` UUID + winner resolution, models.py:36–40) exists but is more machinery than the approval flow needs.

### Queue/approval-like lifecycle — Blueprint pending updates
- `daemon/repositories/blueprint/pending_repository.py` (490 lines): statuses `available → claimed → applied | retryable | abandoned` (:8–34, constants :47–58); **atomic claim/acknowledge scoped by `run_token`** so concurrent operators can't ack each other's rows (:16–19, 30–34). Useful mechanics if approval needs "claimed by reviewer X" semantics; otherwise the simpler jobs-style machine suffices.

### Audit-trail conventions (no generic audit table exists)
- **DB event-table style**: `drift_events` (20261007_000001) — one row per event, verbatim payload fields, `observed_at/observed_tag/created_at TEXT`, "unresolved == row exists" resolution-by-delete (:1–9). Also `project_history` (20260521_000001) and `maintenance_runs` (20260927_000001) — each domain ships its own purpose-built table.
- **File-journal style**: the upgrade journal `releases/state.json` — `journal_write` = temp file + fsync + `os.replace` (atomic, torn-safe), `journal_history_append` = newest-last, alert-sink emission hooks (daemon/tools/upgrade_journal.py:1–58). This is release-machinery-owned (state.json MUST never be hand-edited) — not a pattern to reuse for prompt audit, but it's the project's "who/when/what" precedent on the ops side.
- **Ledger style**: `schema_migrations` itself (checksum + `skip_reason`) and the attestations ledger columns (20260905_000001).
- **Recommendation-shaped finding**: for the plan's audit trail, follow drift_events — a `prompt_version_audit` (or `prompt_audit_events`) table with ISO timestamps + actor columns stamped from `AuthContext.subject`/`auth_method`, indexed on (agent, artifact, created_at).

---

## 5. Frontend naturalness (assessment)

`frontend/src/app/services/` holds ~25 API services, one per daemon family (`skill-bank.service.ts`, `skill.service.ts`, `snapshot.service.ts`, `blueprint.service.ts`, …). The house style (skill-bank.service.ts): `@Injectable({providedIn:'root'})`, `inject(HttpClient)`, `API_BASE = '/api/...'`, **signals** (`items/loading/error`), list methods unwrap the backend `{items, total}` envelope (:53–75), error convention = list swallows→empty array (skeleton/empty state), mutations rethrow for snackbar (:38–43). A `prompt-version.service.ts` + a review/approve page slots in naturally beside the existing Skills / Skill Bank pages — same Material components, same service/page pairing, no restructuring; the only novel UI element is the approve/reject action pair + version diff/list, which maps onto existing list+detail page patterns. Natural fit; low frontend risk.

---

## Open questions (for the planner)

1. **Auth-OFF posture for the approval API**: `@require_scope` is inert and `auth_context` absent when auth is disabled (decorators.py:27–32; routes not even registered — api.py:3629/3636 gate). Must the approver-be-human rule fail closed (refuse approvals without auth), or is a local operator trusted? Needs an explicit decision.
2. **Scope name**: follow `<module>:<action>` (`prompts:approve`?) — minting a new scope touches `daemon/auth/scopes.py` grant families (session lane persists operator scopes ∪ family).
3. **One `.sql` or two**: prompt_versions + audit table could ship as one grouped migration (skill-tables precedent, 20260710_000001) or two files; grouped-in-one is the established group style.
4. **Pointer implementation**: `is_selected` flag with transactional clear-and-set (get_active_variant style) vs a pointer row — NULL-key uniqueness won't enforce "one pointer per (agent, artifact)" on either driver (NULLs distinct in UNIQUE — skill/models.py:44–49); enforce in the repo transaction.
5. **Where the service lives**: manager-held (like SkillStoreService, router→`app.state.manager`) vs app.state-held (like AuthService/KB key store). The approval service is auth-adjacent → app.state wiring is the newer pattern, but manager-held keeps it available to inner_soul/loader rewrite paths too.
6. **Collision warning**: at least one inner_soul/tool path already writes persona files into the live release tree and trips the promote integrity gate (2026-10-01 incidents) — the immutability feature's enforcement point (loader reads DB-versioned overrides instead of files) will interact with that flow; the loader composition order is `daemon/loader.py:438` (slot order documented in shared context file `where-are-agent-prompt-md-files…md`).

---

## Key file index (worktree paths)

| Concern | File:lines |
|---|---|
| Migration parse/naming/checksum | daemon/migrations/runner.py:66–136 |
| Precondition + skip-ledger | daemon/migrations/runner.py:353–393, 476–501 |
| Transactional apply + idempotent tolerance | daemon/migrations/runner.py:503–624 |
| SQLite-only runner docstring | daemon/migrations/runner.py:693–718 |
| PG column-ensure path | daemon/manager.py:6145+ |
| Table template (full) | daemon/migrations/versions/20261011_000001_create_stored_watches.sql |
| Audit-table template | daemon/migrations/versions/20261007_000001_create_drift_events.sql |
| Stub-reservation precedent | daemon/migrations/versions/20261011_030500_create_grants.sql |
| Repo class shape | daemon/repositories/skill/skill_bank_repository.py:46–63 |
| Repo factory pattern | daemon/repositories/factory.py:50–77, :1047 |
| Manager repo wiring | daemon/manager.py:32–61 (imports), 1159–1177 (construction) |
| Service facade pattern | daemon/services/skill_store_service.py:1–60 |
| Router registration | daemon/api.py:3575–3641 |
| OAuth2 CRUD router template | daemon/routers/mcp_kb_keys.py (whole file) |
| Plain CRUD + pagination template | daemon/routers/skill_bank.py (whole file) |
| Auth middleware | daemon/middleware/auth.py:1–43, :219 |
| AuthContext shape | daemon/auth/context.py:36–60 |
| require_scope / inert-OFF | daemon/auth/decorators.py:74–124 |
| State machine | daemon/services/job_state_machine.py:53–100 |
| Versioned-row + pointer precedent | daemon/repositories/skill/models.py:44–49, 93–97, 119–127 |
| Approval-ish queue lifecycle | daemon/repositories/blueprint/pending_repository.py:8–78 |
| Frontend service style | frontend/src/app/services/skill-bank.service.ts |
