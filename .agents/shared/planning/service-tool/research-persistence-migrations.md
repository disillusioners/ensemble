# Research: Persistence + Boot-Service Conventions (for `service` tool category)

Repo: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`, branch `feature/service-tool`.
Read-only investigation. Every claim carries `file:line` evidence. `UNVERIFIED` marks anything not directly confirmed in source.

---

## Q1. Migrations discipline (`daemon/migrations/`)

### Layout, naming, ordering
- SQL files live in `daemon/migrations/versions/` (runner default: `runner.py:140`). ~70 files, naming `YYYYMMDD_HHMMSS_<name>.sql` (see `daemon/migrations/versions/` listing; newest: `20260906_192100_add_task_claim_wake_lane_index.sql`).
- Version is parsed from filename via regex `^(\d{8}_\d{6})` (`runner.py:77-85`); files apply **sorted lexicographically by version** (`runner.py:239`). No dependency graph — timestamp ordering is the contract.
- Required `-- UP` section; optional `-- DOWN` (used by `rollback_migration`) (`runner.py:88-95`, `413-456`). Missing UP → `ValueError` at parse (`runner.py:91-92`).
- Optional `-- MANUAL: TRUE` header marker → migration is skipped by auto-apply; operator applies explicitly via `apply_migration()` (`runner.py:97-103`, `503-511`).

### Checksum + tracking table
- Checksum = SHA-256 of full file content (`runner.py:56-60`), recorded per-application in the `schema_migrations` table alongside `version`, `name`, `applied_at`, `execution_time_ms` (`runner.py:396-406`).
- `schema_migrations` created with `CREATE TABLE IF NOT EXISTS` (`runner.py:151-161`) and auto-column-synced to the `SchemaMigration` model (`runner.py:167-192`).
- Pending = discovered minus applied versions (`runner.py:241-249`).

### Transactional application
- Each migration applies inside `with self.engine.begin() as conn:` — one transaction (`runner.py:325`).
- Statements are split on `;` **after stripping full-line `--` comments** (an in-comment semicolon previously corrupted the split — defensive fix at `runner.py:328-339`). Rule: do not rely on semicolons inside SQL comments.
- Idempotent-skip error classification inside the transaction: "duplicate column name", "no such table" (CREATE-only), "already exists", "no such column", "has no column" are logged-and-skipped; anything else re-raises as `MigrationError` (`runner.py:344-392`).
- A migration is recorded as applied **even when all statements idempotently no-op** (`runner.py:394-406`) — prevents re-runs.
- Special-case pre-check: the `rename session to instance` migration is recorded as applied without executing when no old `session*` schema exists (`runner.py:304-323`).

### SQLite↔PG compatibility rules (the 20260714 trap)
- **Trap (confirmed)**: `20260714_000001_widen_job_queue_type_constraint.sql` executes `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS …` — PG-only; SQLite has no `DROP CONSTRAINT` → `sqlite3.OperationalError` → **any fresh-SQLite boot fails at this migration**. The migration's own header falsely claimed SQLite 3.35+ support (doc-truth rot). Source: `.agents/tester/LESSONS/2026-09-04-fresh-sqlite-boot-migration-20260714-pg-only.md:8-11`.
- **Concrete dual-compat rule**: use only DDL valid on BOTH dialects — `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, additive `ALTER TABLE ADD COLUMN` guarded by the runner's idempotent-skip. Never `DROP CONSTRAINT`. The exemplar migration states this explicitly: *"Plain `CREATE INDEX IF NOT EXISTS` is valid on PostgreSQL AND SQLite — unlike migration 20260714_000001 (fresh-SQLite boot broken by PG-only `DROP CONSTRAINT IF EXISTS`), this file applies cleanly on both backends."* (`20260906_192100_add_task_claim_wake_lane_index.sql:35-39`).
- **PG boolean defaults**: `DEFAULT 0` is invalid on PG (psycopg 42P16); use `DEFAULT FALSE` / `'f'` / `0::boolean`; model side `Boolean.server_default=text("false")` (convention per Production-DB blueprint; model exemplar `daemon/repositories/task/models.py:97-99, 115-117`).
- **Runner NO-OP on PG**: `run_pending_migrations()` returns `[]` for non-SQLite engines (`runner.py:486-491`). Documented rationale (`runner.py:464-485`): fresh PG DBs get schema from `SQLModel.metadata.create_all()` at manager init; existing PG DBs get evolution from `_ensure_postgres_columns`. Do **not** "fix" the runner to apply .sql on PG — its naive `split(";")` parser plus PG-incompatible constructs in the .sql files are the SQLite path only.
- **`_ensure_postgres_columns`**: `daemon/manager.py:4787` — idempotent PG schema evolution, run at manager init (`manager.py:529`; the SQLite runner is invoked at `manager.py:512`). Contract: ADD-only via `IF NOT EXISTS` clauses; the sole DROP is a guarded `DROP CONSTRAINT` for `job_watchers.job_id` (`manager.py:4821-4832`). Every new .sql migration that adds schema elements **must** also extend this method with an idempotent statement (`runner.py:472-478`).

### 3-site index pattern (triple registration)
Index name must be **byte-identical** at all three sites (`20260906_192100…sql:40-49`; `manager.py:5405-5408` "Triple registration"):
1. `.sql` migration (SQLite path): `20260906_192100…sql:58-59` — `CREATE INDEX IF NOT EXISTS idx_task_status_type_created ON task (status, task_type, created_at);`
2. SQLModel `__table_args__` (fresh DBs via `create_all`): `daemon/repositories/task/models.py:141` — `Index("idx_task_status_type_created", "status", "task_type", "created_at")`.
3. `manager._ensure_postgres_columns` (existing PG): `manager.py:5409-5412`.

Related fixture-gap trap: a `create_all`-built test DB gets CURRENT model schema while prod carries legacy DDL — a fixture can pass what prod fails; pinned by `test_create_all_vs_migration_chain_difference_documented` (`tests/unit/test_ensure_deferred_schema_pin.py:477`, per Production-DB blueprint).

---

## Q2. New-domain repository convention

### Domain directory shape
- One directory per domain under `daemon/repositories/` (listing): `task/`, `report_injection/`, `job_queue/`, `skill/`, etc. Each contains `__init__.py`, `models.py`, `repository.py` (confirmed for `task/`; `report_injection/` likewise per blueprint).
- `models.py`: SQLModel table classes + `str` enums for type/status (`TaskType`, `TaskStatus` at `daemon/repositories/task/models.py:19-52`). Booleans that gate queries are module-level `sa.Column`s with `server_default=text("false")`, `index=True` — because Pydantic `Field(default=False)` does NOT emit a SQL DEFAULT, which would break raw-SQL INSERTs omitting the column (`task/models.py:83-117`). Composite indexes declared in `__table_args__` (`task/models.py:141`).
- `repository.py`: plain class, ctor takes `engine: Engine` (+ optional `on_pending_task` callback) (`daemon/repositories/task/repository.py:77-85`). **All methods are sync** — sync `sqlmodel.Session` (`repository.py:21`). Read-only lookups use `engine.connect()` NOT a session, to avoid holding SQLite write locks while a later `engine.begin()` opens a second connection (documented lock-regression fix, `repository.py:116-130`). Multi-statement atomic writes use `engine.begin()`.
- Status-write discipline: direct `UPDATE task SET status=` outside a named-transition context is forbidden by the C7 write guard / `DirectWriteError` (`repository.py:39-58`, `91-104`) — for a new domain, either adopt explicit state transitions or keep writes in repo methods only.

### Factory + engine wiring
- `daemon/repositories/factory.py` provides `create_*_repository(config, engine, create_tables=True)` module functions; engine takes precedence over config; repos are instantiated with the shared engine (`factory.py:50-77`).
- **ONE shared engine** across all repositories to avoid SQLite lock contention (`factory.py:115-135` docstring).
- `create_engine_from_config` sets SQLite pragmas via a connect-event listener: `journal_mode=WAL`, `busy_timeout=30000`, `synchronous=NORMAL`, `foreign_keys=ON` (`factory.py:146-153`); PG gets pool_size/max_overflow (`factory.py:154-158`).
- Manager wiring: `InstanceManager` constructs repos directly in `__init__` with `engine=self._engine` (e.g., `TaskRepository(engine=self._engine, on_pending_task=…)` at `manager.py:690-693`; `WatchoverService(self)` at `manager.py:980`; `InstanceMessagingService` at `manager.py:1232`). New repo: construct in manager init with the shared engine, expose as `self._<name>_repo` (the lifespan reads `manager._task_repo` / `manager._instance_repository` via `getattr`, `daemon/api.py:620, 632-634`).

### Async pattern
- Repos are sync; async callers wrap calls in `asyncio.to_thread` — exemplar `EligiblePendingSweepService.sweep_once`: `await asyncio.to_thread(self._task_repository.list_pending_tasks_older_than, …)` (`daemon/services/eligible_pending_sweep.py:212-215`).

---

## Q3. Boot-time background services + boot-probe log convention

### Lifespan boot sequence
- FastAPI lifespan: `@asynccontextmanager async def lifespan(app)` — `daemon/api.py:198-199`. All periodic services start inside it.
- **EligiblePendingSweepService** (`api.py:600-654`): imported lazily inside lifespan (`api.py:608-612`); constructed with repos pulled from the manager (`manager._task_repo`, `manager._worker_pool`, `manager._instance_repository` via `getattr`, `api.py:619-635`); config knobs from `config.services.eligible_pending_sweep_*` (`api.py:613-618`); sanity floor `interval < 1` → `logger.error("… DISABLED …")` and no start (`api.py:640-644`); else `start()` + stored on `app.state` + boot INFO `EligiblePendingSweepService started: interval=…s (default …), min_pending_age=…s` (`api.py:645-654`).
- **OrphanWatcherSweepService**: identical construct/floor/start/app.state/boot-log pattern (`api.py:656-696`).
- **Drift reconciler**: raw `asyncio.create_task(_periodic_drift_reconcile_loop(…), name="drift-reconciler")` + `app.state` + boot log (`api.py:582-598`).
- **WatchoverService**: constructed in manager `__init__` (`manager.py:980`); activation goes through the pause-first-then-quiesce convention (Core blueprint: `WatchoverService.activate_watchover`).

### Interval-loop service pattern (canonical template)
`daemon/services/eligible_pending_sweep.py` is the pattern to copy:
- Module-level defaults `DEFAULT_SWEEP_INTERVAL_SECONDS=90`, `DEFAULT_MIN_PENDING_AGE_SECONDS=60` (`:64-65`) — "kept module-level so the constants have one source of truth and the config can reference them in pydantic Field default factories".
- `start()` idempotent — silent no-op if task alive; `asyncio.create_task(self._run_loop(), name="eligible-pending-sweep")` + boot INFO (`:146-169`).
- `stop(timeout=5.0)` — sets `asyncio.Event` stop flag (prompt sleep exit), `task.cancel()` + `await`, swallows `CancelledError`, clears handle (`:171-191`).
- `sweep_once()` public single tick for tests/on-demand use, returns structured counters (`:193-207`); per-tick errors caught into `errors` counter (`:136-140`, `208-215`).
- Docstring records the ALWAYS-ON hard policy: *"a kill-switch defaulting OFF that gates a fix is an unacceptable deliverable"* (`:26-31`) — note: that policy applied to a bug-fix backstop; a new operational tool category is a different case (your kill-switch plan is consistent with flag-guarded features like vscode-CSP / symptom-ladder).
- DB access from the loop via `asyncio.to_thread` (`:212-215`).

### Graceful shutdown (lifespan finally-block)
Pattern with `getattr(app.state, key, None)` to survive partial startup (`api.py:1330-1347` rationale comment):
- Watchdog task cancel/await (`api.py:1334-1347`); long-tool-nudge task (`:1352-1365`).
- Services with `stop()`: `await eligible_sweep.stop()` inside try/except logging `<Name> shutdown error` (`api.py:1367-1381`); orphan sweep same (`:1383-1397`). New service: add a mirror branch there.

### Boot-probe log-line convention (kill-switch observability)
Emitted **at `load_config` config-resolution time**, NOT lazily — a lazy first-call emit makes quiet-daemon boot-log greps false-fail (`config.py:3502-3509` S13 reviewer gate rationale; repeated `:3520-3528`). Exact style: `"[Component] flag_name=<resolved bool> (env ENSEMBLE_NAME)"`:
- `logger.info("[ContextMessages] kv_ambient_system_default_enabled=%s (env %s)", …)` — `config.py:3513-3518`
- `logger.info("[VSCode] webview_csp_fix=%s (env %s)", …)` — `config.py:3529-3536`
- `logger.info("[ResponseValidation] empty_response_guard=%s (env ENSEMBLE_EMPTY_RESPONSE_GUARD), …")` — `config.py:3555-3565`
- `logger.info("[SymptomRepair] symptom_repair_ladder=%s (env ENSEMBLE_SYMPTOM_REPAIR_LADDER), repair_loop_durable=%s (env …)")` — `config.py:3581-3591`
Each is preceded by a `resolve → _install_<flag>(module cache) → logger.info` triple (`config.py:3529-3536`, `3547-3565`, `3575-3591`). Lazy imports inside `load_config` to avoid module-load import cycles (`config.py:3545-3547`).
Service-start boot lines use a parallel style: `"EligiblePendingSweepService started: interval=…s"` (`api.py:648-654`; also emitted inside the service itself, `eligible_pending_sweep.py:165-169`).
Maintenancer rollout kill-switch `ENSEMBLE_REPAIR_ENABLED` (per critical-notes / `agents/maintenancer/ROLLOUT.md`): **UNVERIFIED in `daemon/` source** (grep for the env name in `daemon/*.py` returned no match — it may live behind a different symbol or be agent-config only).

---

## Q4. Kill-switch / config conventions

### Precedence resolver pattern (`_resolve_*`)
- Canonical: `_resolve_proactive_enabled(yaml_value, *, ens_value, cpe_value) -> bool` (`daemon/config.py:2702-2770`). Precedence: (1) `ENSEMBLE_*` env when set and non-empty; (2) legacy alias env; (3) yaml value; (4) documented default `True` (`:2720-2736`). Empty/whitespace env = UNSET via `_clean_env_value` (`:2749-2754`) — a bare `KEY=` in `.env` must never brick boot (`:2738-2747`).
- Called from `load_config` with `os.environ.get(...)` read once (`config.py:3349`); the resolved bool is passed as init kwarg so pydantic-settings never re-reads env.
- Strict-parse flavor: `resolve_injected_notes_absorb()` (`config.py:3097-3139`) — env-only (deliberately NOT a pydantic field), falsy/`0/false/no/off` → OFF, truthy set → ON, **any other non-empty string raises ValueError naming the flag** (`:3136-3139`).

### The pydantic-settings init-kwarg > env inversion trap (avoid)
- pydantic-settings treats any passed init kwarg — even `None` — as beating env vars (`config.py:3421-3427`). Consequence: a YAML value passed through un-resolved would silently defeat an operator `ENSEMBLE_*…=0` kill-switch (`config.py:2711-2718`, `3432-3444`). Therefore **every new boolean kill-switch needs an explicit `_resolve_*` call in `load_config`** (mirroring `_resolve_compaction_model`); adding a bare pydantic bool field without a resolver reintroduces the trap (`config.py:3109-3115`).
- **Section-less configs**: read the env at TOP LEVEL of `load_config` (outside the `if "section" in …` guard) so configs omitting the YAML section still honor the env kill-switch (review MAJOR-2 finding, `config.py:3446-3454`, section-absent branch `:3474-3496`). Pydantic's `env_prefix` binds only the unprefixed name; `ENSEMBLE_*` forms must be read manually (`:3449-3452`).
- Default-ON contract: OFF must restore byte-identical legacy behavior; flip is restart-to-flip (install at boot) (`config.py:3543-3544`, `3572-3574`, `2775-2781`).
- Boot probe install trio (resolve → `_install_*` module cache → `logger.info`) per Q3; emit-once guard pattern for boot WARNINGs that re-enter `load_config` twice (`config.py:3142-3146`).
- Config knobs (intervals etc.) live in a pydantic `ServicesConfig` with `Field(ge=1)`-style constraints so out-of-range values fail fast at boot (exemplar fields `eligible_pending_sweep_interval_seconds` / `…_min_pending_age_seconds`, consumed `api.py:613-618`; constraint enforced at boot via the floor check `api.py:640-644`).

### Where a new flag goes (recipe)
1. Env name `ENSEMBLE_<AREA>_<FLAG>`; resolver `_resolve_<flag>(raw: str | None) -> bool` beside the precedents (`config.py:2702` / `3097` area).
2. In `load_config`: top-level env read → resolve → `_install_<flag>` module-cache install → `logger.info("[<Area>] <flag>=%s (env %s)")` boot line (style clone of `config.py:3547-3591`).
3. If a YAML field also exists: resolve it explicitly (never pass raw yaml through as init kwarg); handle the section-absent case (`config.py:3474-3496`).

---

## Q5. Testing conventions for repositories/services

### File-backed SQLite fixture (canonical)
`tests/test_chart_tools_reuse_integration.py:80-109` is the named pattern ("tmp_path + NullPool + WAL + busy_timeout", `:75-77`):
- `create_engine(f"sqlite:///{tmp_path}/x.db", connect_args={"check_same_thread": False, "timeout": 30}, poolclass=NullPool)` (`:90-94`) — **never in-memory StaticPool** (`:75-77`, `:84-88`).
- WAL + busy_timeout pragmas are **connection-local** → set via an `event.listens_for(eng, "connect")` cursor per connection: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=30000`, `PRAGMA foreign_keys=ON` (`:96-105`). (Live exemplars use 30000; the Testing-QC blueprint cites 10000 — either satisfies the convention; runtime factory uses 30000, `factory.py:150`.)
- `SQLModel.metadata.create_all(eng)` then `yield` / `eng.dispose()` (`:107-109`).
- Execution gate: only `uv run python -m pytest` from worktree root (Testing & QC blueprint — bare `pytest` hits a broken foreign Homebrew install).

### Where tests live
- Repository/service unit tests: `tests/unit/` tree — `tests/unit/repositories/`, `tests/unit/services/`, `tests/unit/routers/`, plus flat `tests/unit/test_*.py` (e.g. `tests/unit/test_ensure_deferred_schema_pin.py`, `tests/unit/test_injected_notes_absorb_boot_validation.py`; directory listing). Kill-switch boot-probe pack exists: `test/packs/boot_probes_unit_test.sh` (listing).
- Kill-switch coverage discipline: flag-ON exercised via the REAL service; flag-OFF pinned byte-identical (Testing & QC blueprint).

### PG-only test packs worth mirroring (test/packs/ listing)
- `ensure_deferred_pg_smoke_integration_test.sh` + `ensure_deferred_pg_migration_smoke_integration_test.sh` — the canonical new-table PG smoke pair (proves prod-shaped legacy DDL + self-heal).
- `admittable_work_pg_test.sh`, `c2_pg_manager_unit_test.sh`, `initiative_message_pg_test.sh`, `ladder_pg_test.sh`, `pg_pending_watchers_probe_test.sh`.
- Schema-pin: mirror `tests/unit/test_ensure_deferred_schema_pin.py` (pins the create_all-vs-migration-chain gap; blueprint cites the documented-difference test at `:477`).
- Boot traps for any live-smoke (LESSONS 2026-09-04:14-16): `dev.sh` hardcodes `PORT=8079` (bypass with direct uvicorn); inherited `POSTGRES_*` env silently points "SQLite" boots at prod PG (scrub/pin a data dir).

---

## New-table + new-boot-service checklist (ordered)

1. **Model** — `daemon/repositories/service_tool/models.py`: SQLModel table (`name, command, pid, cwd, status, started_by, timestamps, log paths`), `str` enums for status, bool columns as `sa.Column(server_default=text("false"), index=True)`, composite indexes in `__table_args__` with a stable name (pattern: `daemon/repositories/task/models.py:19-52, 97-117, 141`).
2. **SQLite migration** — `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_service_tracking.sql` with `-- UP` / `-- DOWN`; **dual-dialect DDL only** (CREATE … IF NOT EXISTS; NO `DROP CONSTRAINT`; PG-valid boolean defaults `DEFAULT FALSE`) — clone header style of `20260906_192100_add_task_claim_wake_lane_index.sql` incl. triple-registration note.
3. **PG mirror** — extend `EnsembleManager._ensure_postgres_columns` (`daemon/manager.py:4787`) with idempotent `IF NOT EXISTS` ADD COLUMN / CREATE INDEX statements; index name byte-identical across all 3 sites (pattern: `manager.py:5395-5412`).
4. **Repository** — `daemon/repositories/service_tool/repository.py`: sync class, ctor `(engine, …)`; reads via `engine.connect()`, atomic writes via `engine.begin()`; status changes only via repo methods/transitions (pattern: `task/repository.py:77-130`).
5. **Manager wiring** — construct with the shared engine in `InstanceManager.__init__`, expose `self._service_tool_repo` (pattern: `manager.py:690-693, 980`); optional `factory.py` `create_service_tool_repository(engine=…)` helper (pattern: `factory.py:50-77`).
6. **Config + kill-switch** — `ENSEMBLE_SERVICE_TOOL_ENABLED`-style env; `_resolve_*` (precedence env > yaml > default, empty-string safe, strict-parse ValueError) beside `config.py:2702/3097`; top-level env read in `load_config` (section-absent branch, `config.py:3474-3496`); `_install_*` + boot probe `logger.info("[ServiceTool] service_tracking=%s (env %s)")` on the boot path (style: `config.py:3529-3591`); OFF = byte-identical no-op; interval knobs in `ServicesConfig` with `Field(ge=1)`.
7. **Boot reconciliation service** — `daemon/services/service_reconciliation.py` cloned from `EligiblePendingSweepService`: module-level DEFAULT_* constants, idempotent `start()`, `stop(timeout)` with stop-event + cancel/await, public `sweep_once()` + counters, DB reads via `asyncio.to_thread` (`eligible_pending_sweep.py:60-215`). Sweep = PID liveness check → mark dead rows `exited`.
8. **Lifespan boot** — after the sweeps in `daemon/api.py` (~`:600-654` style): lazy import, construct with `getattr(manager, …)` deps, floor-check (`< 1 → DISABLED` error log), `start()`, store on `app.state`, boot log line `"ServiceReconciliationService started: interval=…s"`.
9. **Lifespan shutdown** — add a getattr-guarded `await <svc>.stop()` branch in the lifespan finally-block mirroring `api.py:1367-1397`.
10. **Tests** — file-backed SQLite fixture (tmp_path + NullPool + WAL + busy_timeout, `test_chart_tools_reuse_integration.py:80-109`); unit tests under `tests/unit/repositories/` + `tests/unit/services/`; schema-pin test for create_all-vs-migration-chain; flag-ON-real-service / OFF-byte-identical pins; boot-probe pack under `test/packs/` mirroring `boot_probes_unit_test.sh`; PG smoke pack mirroring `ensure_deferred_pg_smoke_integration_test.sh`; run via `uv run python -m pytest`.
11. **Ops note** — record restart-pending activation semantics (flag flips and schema changes need daemon restart; verify via `grep '<flag>' data/logs/ensemble.log` boot line, per `config.py:3502-3509`).
