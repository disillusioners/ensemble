# Research: Pool/Engine Inventory + Soak-Lane Couplings + Runner Mechanics (db-pool idle-in-transaction fix lane)

- **Worktree:** `/home/nea/ensemble-src-wt-dbpool-idletxn` — branch `fix/db-pool-idle-txn`
- **HEAD verified:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (`.git` → gitdir `ensemble-src/.git/worktrees/ensemble-src-wt-dbpool-idletxn`, `refs/heads/fix/db-pool-idle-txn` = the exact dispatch SHA). ✓
- **Read-only research.** All `file:line` are at this HEAD. All reads inside the worktree except the RCA (main checkout) and git plumbing under `ensemble-src/.git/`.
- Pinned dependency versions (uv.lock): **SQLAlchemy 2.0.48** (`uv.lock:2165-2172`), **psycopg 3.3.4** + psycopg-binary 3.3.4 (`:1542-1584`), **psycopg-pool 3.3.1** (`:1588-1596`), **asyncpg 0.31.0** (`:143-146`), greenlet 3.3.2 (`:678-681`).

---

## Q1 — Engine/Pool Inventory (daemon/)

### 1. Canonical manager engine — `create_postgres_engine`
- **`daemon/repositories/factory.py:255-339`** (create_engine call `:323-338`). Sync SQLAlchemy Engine, `postgresql+psycopg` driver, QueuePool.
- **Knobs at HEAD:** `pool_size=5` (:326), `max_overflow=10` (:327), `pool_pre_ping=True` (:328), `connect_args={"options": "-c timezone=UTC"}` (:337 → `PG_SESSION_CONNECT_ARGS` :252), libpq `connect_timeout=BOOT_DB_TIMEOUT_S` rides the URL query via `URL.create(...)` (:309-317).
- **NOT set:** `reset_on_return` (ABSENT — SQLAlchemy 2.0.48 library default `'rollback'` applies, but it is NOT explicit in code), `pool_recycle` (ABSENT), `pool_timeout` (ABSENT — SQLAlchemy default 30s), `pool_use_lifo` (ABSENT), `max_lifetime` (ABSENT).
- **Serves:** THE shared engine. `daemon/manager.py:758` "Create ONE shared database engine for all repositories" → `manager.py:768 self._engine = create_postgres_engine(...)`. Every repository (API request paths incl. `/api/instances`), the readiness `database` + `queue_freshness` probes, `MissionRecordService` (manager.py:1070, child_reports.py:4752), and the ens_db repair_log audit writes all ride THIS pool. This is **P1 (ADVISORY)** in the soak-gate leak oracle.

### 2. `create_engine_from_config` — SQLite branch
- **`daemon/repositories/factory.py:184-199`**: `check_same_thread=False`, `pool_pre_ping=True` (:188), WAL/busy_timeout=30000/synchronous=NORMAL/foreign_keys pragmas via connect-event listener (:192-199). SQLite installs only.

### 3. `create_engine_from_config` — non-SQLite (latent PG) branch
- **`daemon/repositories/factory.py:215-222`**: `pool_size=config.pool_size` (DatabaseConfig default 5, :136), `max_overflow=10` (:137/:219), `pool_pre_ping=True` (:220), `connect_args=PG_SESSION_CONNECT_ARGS` when URL is postgres (:210-214). Zero daemon-runtime PG callers (comment :201-203: canonical PG engine is site 1); used by per-repository factories that are not handed a shared engine.

### 4. Boot-preflight probe engine (hard-reboot lane)
- **`daemon/__main__.py:237-240`** (URL builder `_preflight_pg_url` :98-151). `create_engine(url, connect_args={"connect_timeout": BOOT_DB_TIMEOUT_S})` — the bound rides `connect_args` here, deliberately DIFFERENT mechanism from site 1's URL query (comment :224-235; factory comment :285-299: "the two timeouts are complementary, not duplicated").
- **"One-shot probe: no pool tuning, dispose immediately after"** (:236); `engine.dispose()` in finally (:245). Serves only the boot `SELECT 1`.

### 5. Checkpoint saver pool — psycopg `AsyncConnectionPool` (**P2 ORACLE**)
- **`daemon/persistence.py:316-331`** (`create_postgres_checkpointer`, :166).
- **Knobs at HEAD:** `min_size=1`, `max_size=checkpoint_pool_max_size` (config `limits.checkpoint_pool_max_size`; resolved :237-244, default `CHECKPOINT_POOL_MAX_SIZE_DEFAULT=10`), `timeout=checkpoint_pool_timeout_s` (default `CHECKPOINT_POOL_TIMEOUT_S_DEFAULT=5.0`; resolution :245-257), `check=AsyncConnectionPool.check_connection` (:322 — liveness probe on handout), `max_lifetime=3600`, `max_idle=600`, `reconnect_timeout=300`, `kwargs={autocommit: True, prepare_threshold: 0, row_factory: dict_row}` (:326-330).
- **Sizing comments/rationale:** :306-315 — "(soak-gate-mcp-warmup, A4 — delivered knob) … HONESTY (decisions §3d D4): sizing does NOT cure the leak — the confirmed leak is on the OTHER pool. Full arithmetic …: see daemon/constants.py (CHECKPOINT_POOL_MAX_SIZE_DEFAULT) — the single canonical home."
- **Serves:** LangGraph `AsyncPostgresSaver` (checkpoint saver, :338), the readiness `checkpoint_saver` sentinel probe (rides the REAL saver path), and the P2 leak-oracle samples. `autocommit=True` means saver-pool connections do NOT sit `idle in transaction` by construction.

### 6. Checkpoint adapter asyncpg pool
- **`daemon/persistence.py:355-359`**: `asyncpg.create_pool(conn_string, min_size=1, max_size=checkpoint_pool_max_size)`. Shares ONLY the max-size knob — "the saver pool's acquisition `timeout` kwarg has no asyncpg equivalent … deliberately NOT wired here" (:349-354). Serves `PostgresCheckpointerAdapter` raw SQL (maintenance GROUP BY/DELETE/COUNT, :341-347). Separate driver/failure domain from site 5.

### 7. ConnectionPoolManager — asyncpg pools for USER-REGISTERED external connections
- **`daemon/services/db_pool_manager.py:261-271`** (create), module knobs :54-65: `POOL_MIN_SIZE=1`, `POOL_MAX_SIZE=5`, `POOL_MAX_QUERIES=500` (per-connection recycle-by-query-count), `POOL_TIMEOUT=30`. One pool per registered `db_connection` name; singleton at `daemon/manager.py:998-999`, accessor `manager.py:3317`.
- **Serves:** agent-facing `ens_db` query/test tools against EXTERNAL databases. The daemon's OWN database is explicitly excluded (R13 standing guard, `ens_db_tools.py:584-589`). NOT part of the daemon-DB poisoning surface, but is an asyncpg-pool consumer if hardening generalizes.

### 8. ens_db repair engine — the ONLY site with `pool_recycle`
- **`daemon/tools/ens_db_tools.py:573-646`** (`_build_repair_engine`, create_engine :633). **Cached per-process per URL** (`_REPAIR_ENGINE_CACHE` :595-601, :645).
- **Knobs at HEAD:** `pool_size=REPAIR_POOL_SIZE` + `max_overflow=REPAIR_POOL_MAX_OVERFLOW` (docstring :576-577: "2+3"), `pool_pre_ping=True` (:608), **`pool_recycle=REPAIR_POOL_RECYCLE_S`** (:609), `pool_timeout=REPAIR_POOL_TIMEOUT_S` (:610), PG `connect_args={"options": "-c statement_timeout=… -c lock_timeout=… -c idle_in_transaction_session_timeout=… <UTC>"}` (:619-627).
- **Serves:** `ens_db_repair_execute` (:915) — committed DDL/DML `Session(repair_engine)` :1067 AND the **dry-run BEGIN/ROLLBACK preview whose two `session.execute(text("BEGIN"))` sites are `:1479` and `:1491`** (the RCA's leaked-`BEGIN` fingerprints). The explicit BEGIN sits BEFORE the inner `try:` (:1480/:1492), so a failure of the BEGIN execute itself skips the paired `session.rollback()` (:1488/:1501/:1503) — this is RCA fix-item 3's target, on engine 8 (NOT the manager engine).

### 9. ens_db vacuum autocommit engine
- **`daemon/tools/ens_db_tools.py:649-695`**: `poolclass=NullPool` (:695), per-invocation, caller MUST `dispose()` in finally (:666-669, :1470-1473), `autocommit=True`, VACUUM timeouts (10min statement / 45s lock / **idle_in_transaction_session_timeout=60s**) + UTC. Serves the VACUUM/ANALYZE confirm path (:1038).

### 10. Migration worker engine
- **`daemon/services/migration_worker.py:339`**: `create_postgres_engine(config)` — a SECOND engine instance from the same factory → identical knob set as site 1. Serves schema migrations.

### 11. Auth CLI engines
- **`daemon/auth/cli.py:152-160`**: `create_postgres_engine` or `create_engine_from_config` — one-shot CLI process.

### Negative findings
- **`reset_on_return` is set NOWHERE in daemon/** (grep: zero hits). The claimed default `'rollback'` is the SQLAlchemy 2.0.48 library default, not an explicit pin — and note SQLAlchemy's reset-on-return does NOT save a connection that is **checked out and never returned** (the readiness probe's timed-out-`to_thread` retention shape).
- **`pool_recycle` is set ONLY on site 8** (ens_db repair engine).
- **OpenCode SQLite:** no `*opencode*` file exists under `daemon/` (glob: zero hits) — the OpenCode SQLite layer is external to `daemon/`; nothing to inventory in this tree.

---

## Q2 — Pool → Path Mapping

| Path | Pool |
|---|---|
| (a) `GET /api/instances` list handler (`daemon/routers/instances.py:371 list_instances` → repositories) | **Site 1** (manager engine; manager.py:758 "ONE shared database engine for all repositories") |
| (b) readiness `database` probe (`SELECT 1`, readiness.py:1463-1477) and `queue_freshness` probe (readiness.py:1717-1752) | **Site 1** — wired `daemon/api.py:3940-3947`: `make_db_probe(manager.engine)`, `make_queue_probe(manager.engine, …)` |
| (b) readiness `checkpoint_saver` probe | **Site 5** (P2) — `api.py:3957-3961 make_checkpoint_saver_probe(manager.checkpointer)` rides the real saver path (constants.py:219-229) |
| (c) checkpoint saver (LangGraph) | **Site 5** (+ site 6 for maintenance raw SQL) |
| (d) `ens_db_tools` BEGIN sites (`:1479`/`:1491`) | **Site 8** — `Session(repair_engine)` :1067. NOT the manager engine |
| (e) mission_record mint (`MissionRecordService(self._engine)` manager.py:1070; `Session(self._engine)` service :491/:769) | **Site 1** |

**Consequence:** hardening knobs (`pool_pre_ping` already set; `reset_on_return='rollback'` explicit; `pool_recycle`) applied to **site 1** cover paths (a), (b db/queue), (e) — i.e., the entire SQLAlchemy leak surface from the RCA except ens_db (site 8 already recycles + server-side idle-in-txn timeout). Sites 4/9 are pool-free one-shots. Site 5 is psycopg_pool with `autocommit=True` + `check=` liveness + `max_lifetime` — different reset semantics entirely.

---

## Q3 — Knob Presence Matrix (set-vs-absent at HEAD)

| # | Pool | pool_pre_ping | reset_on_return | pool_recycle | Pins / comments |
|---|---|---|---|---|---|
| 1 | manager engine (PG) | **True** factory.py:328 | **ABSENT** (lib default 'rollback') | **ABSENT** | `tests/unit/repositories/test_pg_engine_utc_session.py:114` pins `pool_pre_ping is True` in captured kwargs + pins the connect_args byte-shape (:292 factory comment) |
| 2 | SQLite engine | True :188 | ABSENT (n/a SQLite) | ABSENT | pragmas pinned by listener :192-199 |
| 3 | from_config PG branch | True :220 | ABSENT | ABSENT | latent; zero daemon PG callers (:201-203) |
| 4 | boot preflight | absent (no pool tuning :236) | ABSENT | ABSENT | disposed in finally :245 |
| 5 | saver psycopg pool (P2) | `check=check_connection` :322 (liveness equivalent) | n/a (psycopg_pool; **autocommit=True** :327) | `max_lifetime=3600` + `max_idle=600` :323-324 | persistence.py:306-315 + constants.py:207-217 honesty blocks; `tests/unit/tools/test_checkpoint_pool_config.py`; integration pins below |
| 6 | adapter asyncpg pool | n/a | n/a | n/a | persistence.py:349-354 (max-size only, disclosed) |
| 7 | ConnectionPoolManager | n/a | n/a | `max_queries=500` :61 | R13 exclusion note ens_db_tools.py:584-589 |
| 8 | ens_db repair engine | True :608 | ABSENT | **3600 (REPAIR_POOL_RECYCLE_S) :609 — ONLY site** | + server-side `idle_in_transaction_session_timeout` :623-624; `tests/unit/test_run_vacuum_full_after_cleanup.py:192/:212` pins the vacuum-engine timeout string family |
| 9 | vacuum NullPool | n/a (no pool) | n/a | n/a | dispose-in-finally contract :666-669 |

### Soak-lane pin packs (grace / leak / pool sizing) and how they RUN

**Default-`pytest` visible (under `tests/unit/`, no marker → COLLECTED by default):**
- `tests/unit/tools/test_readiness_failure_class.py` — the class table + leak-predicate + grace-tracker pins: grace-eligible vs never-eligible rows (:67-84, disjoint+cover :84); **P1 advisory checkedout-rise does NOT arm** (:125); P2 sustained requests_waiting arms (:151); P2 exhausted arms + reports availability trend (:172); healthy ramp below ceiling does NOT arm (:210); exhausted arms (:231); busy-but-healthy does NOT arm (:250); streak arm ONLY when pool objects unavailable (:271); arm on first eligible observation (:317); horizon expiry never extended (:338); re-arm requires full lapse (:399); disengage on leak signal (:459); never-eligible class never arms (:489); conjunction rows (:525-557); **sub-ceiling no-recovery series cannot hold the grace open** (:607) + expiry under that series (:636); composite stays degraded with reasons after expiry (:688).
- `tests/unit/tools/test_readyz_grace_detail.py` — readyz detail contract (grace block/leak_signal/pool_state serialization).
- `tests/unit/tools/test_saver_probe_contention_class.py` — saver-probe contention → grace-eligible `probe_timeout` class.
- `tests/unit/tools/test_promote_txn_hold.py` — A3 promote-txn auto-continue resume hold.
- `tests/unit/tools/test_checkpoint_pool_config.py` — A4 checkpoint pool sizing knobs/config ladder.
- `tests/unit/test_readiness_mcp_independence.py` — B2 readiness MCP-independence.

**DESELECTED by default addopts (carry `pytestmark = pytest.mark.integration`):**
- `tests/integration/test_checkpoint_pool_burst_shapes.py` — pytestmark :49; real-pool twins of the oracle: healthy-busy oscillation keeps the grace (:112), monotonic rise classified as leak (:192), sub-ceiling monotonic rise = bounded gap (:262); `@pytest.mark.timeout(180)` :109.
- `tests/integration/test_checkpoint_pool_ghost_waiters.py` — pytestmark :58; abandoned probes leave NO waiting-queue entry (settle-wait pin, :112); non-cancelled waiter bounded by pool timeout (:174); `timeout(120)` :109.

**Mutation legs:** the never-green 10 mutation legs are TESTER-SIDE evidence (`agents` planted RED-then-GREEN during the functional gate), recorded in `.agents/tester/RESULTS/2026-10-28-soak-gate-mcp-warmup-functional-gate.md` (file verified present in the worktree), with the never-green mechanics themselves living in `scripts/upgrade/lib.sh` (soak semantics, Part A2) — NOT as committed pytest files. The committed directional pins are the unit files above.

---

## Q4 — DO-NOT-TOUCH (couplings C1/C2)

Everything below is at HEAD and is the soak-gate lane's delivered, pin-protected surface. The fix lane must not weaken, re-key, or re-shape any of it:

**`daemon/services/readiness.py`**
- `:312-325` — detail fields `pool_state_sqlalchemy` (P1), `pool_state_psycopg` (P2), `leak_signal`, `grace_active`.
- `:408-446` — readyz detail serialization (grace block; leak_signal incl. reason; pool_state P1+P2).
- `:500-522` — `resolve_psycopg_pool` (P2 = `checkpointer.raw_saver.conn`, duck-typed on `get_stats`+`min_size`; returns None → pool objects UNAVAILABLE → S9 streak-arm fallback).
- `:525-549` — `sample_sqlalchemy_pool_state` (P1 ADVISORY: publishes `{checkedout,size,overflow}`; **MUST NOT arm** — docstring :531-538: `overflow()>0` is non-zero on a busy-but-healthy pool BY DESIGN).
- `:552-567` — `sample_psycopg_pool_state` (P2 ORACLE, in-memory `get_stats()` — never a DB round trip).
- `:656-671` — tracker init (`horizon_s=READINESS_GRACE_HORIZON_S`, `leak_tick_threshold=READINESS_LEAK_TICK_THRESHOLD`, in-memory histories).
- `:690-781` — `observe()`: per-component streaks (:708-717); leak evaluation call (:719-724); **the CONJUNCTION (:734-743): eligible = degraded AND all classes grace-eligible AND NOT leak.detected**; arm on FIRST eligible observation, never extended (:745-763); `grace_active` window check (:764-768); re-arm only after full lapse (:643-646, :749-753).
- `:784-941` — `_evaluate_leak`: P2 waiting-streak arm (:848-867, ≥3 ticks); **exhaustion arm `in_use >= pool_max`** (:874-903; "pool_available is the IDLE count: it is 0 during ANY busy period" :884-887); **TREND arm DELIBERATELY SUBSUMED — adjudication F1** (:806-843, :905-915 — availability trend reported inside the exhaustion reason token only, via `_availability_decreasing` :943-959); S9 streak arm fires ONLY when pool objects unavailable (:917-935); `_checkedout_rising` published-never-arming (:961-983).
- `daemon/constants.py`: `READINESS_GRACE_HORIZON_S=120.0` (:165), `READINESS_LEAK_TICK_THRESHOLD=3` (:175), `CHECKPOINT_POOL_MAX_SIZE_DEFAULT=10` (:197), `CHECKPOINT_POOL_TIMEOUT_S_DEFAULT=5.0` (:205) + honesty block (:207-217 — canonical wording that sizing does NOT cure the leak; names THIS fix lane as the owner), `CHECKPOINT_SENTINEL_*` (:227-229).

**`daemon/api.py`**
- `:3939-3987` — A1 wiring: db/queue probes on `manager.engine`; `grace_tracker` from `app.state.readiness_grace_tracker`; `sqlalchemy_pool=manager.engine.pool`; `psycopg_pool=resolve_psycopg_pool(manager.checkpointer)`; burst/resume-hold CONTEXT-only inputs (:3977-3981).
- `:5049-5087` — no-composite branch = `boot_incomplete` class, NEVER grace-eligible, `grace_active:false`.

**`daemon/persistence.py`**
- `:231-257` — A4 knob resolution (4-tier ladder) + sizing log line.
- `:316-331` — saver pool construction (max_size/timeout/check/max_lifetime/max_idle/kwargs incl. `autocommit=True`).
- `:341-359` — asyncpg adapter pool sharing the max-size knob only.
- C2 note: P2's oracle fields (`pool_size`, `pool_available`, `requests_waiting`, `pool_max`) come from psycopg_pool `get_stats()` — any saver-pool construction change must keep `check=`, `autocommit=True`, and the stats-bearing pool object resolvable via `raw_saver.conn` duck-typing, or `resolve_psycopg_pool` returns None and the system silently degrades to the S9 streak arm.

**Behavioral invariants (planning docs, normative):** `pool_leak` NEVER grace-eligible + counter resets + fail fast (requirements.md:1097, :1114-1115; risk-safety-register.md:14, :34 — thresholds deliberately fail-closed); fail-closed `unknown` class; grace never renders healthy; 3-consecutive-greens + never-green UNCONDITIONAL at cap-reach (scripts/upgrade/lib.sh soak semantics — pinned by the 10 tester mutation legs + `test_sub_ceiling_*`/burst-shape pins above); boot/burst CONTEXT is never an arming precondition (api.py:3977-3979; requirements.md §4.2 P2).

**C1 note:** changing site-1 pool KNOBS does not touch the arming logic (the predicate samples synthetic dicts in pins; P1 is advisory-only and P2 is a different pool). What WOULD break: renaming/moving the sampled surfaces, changing `manager.engine`/`manager.engine.pool` access shapes (api.py:3973), or altering the detail schema (:408-446) that `test_readyz_grace_detail.py` pins. The constants.py:207-217 and persistence.py:306-315 honesty comments will become stale once the leak is actually fixed — a docs-followup, not a code conflict.

---

## Q5 — Runner Mechanics (coupling C3: false-green discipline)

**pyproject.toml (:86-98):**
- `testpaths = ["tests"]` (:87).
- Markers (:88-94): `integration`, `postgres`, `no_xdist`, `slow`, `conformance`.
- **`addopts = "-m 'not integration and not postgres and not slow and not conformance'"` (:95)** — DEFAULT pytest DESELECTS all four classes.
- `asyncio_mode="auto"` (:96), `timeout=30` + `timeout_method="thread"` (:97-98).

**tests/postgres/conftest.py:**
- Auto-applies the `postgres` marker to every test under `tests/postgres/` (:82-95) so `pytest -m postgres` selects them and the default addopts skips them.
- **xdist guard: `PYTEST_XDIST_WORKER` set → every postgres test gets a skip marker** (:44-45, :94-95) → postgres tests are SERIAL-ONLY. Recommended form recorded at :39 and :86: `pytest tests/postgres/ --override-ini="addopts=" -m postgres`.
- DSN defaults (:68-74): `PG_TEST_HOST=localhost`, `PG_TEST_PORT=5432`, `PG_TEST_DB=ensemble_test`, `PG_TEST_USER=ensemble`, `PG_TEST_PASSWORD=ensemble_dev` → `postgresql+psycopg://ensemble:ensemble_dev@localhost:5432/ensemble_test`.
- Session `pg_engine` fixture: skip-if-unreachable (:167-169), **`drop_all` BEFORE `create_all`** (:187-188, stale-schema F-1 fix), teardown `drop_all` + dispose (:191-195); autouse per-test `TRUNCATE ... RESTART IDENTITY CASCADE` (:240-258).

**The soak lane's false-green lesson (recorded in their planning dir):**
- `plan-overview.md:362-366`: a bare `python -m pytest tests/integration/…` **"deselects the very tests it names and can report '0 failed'"** because `pyproject.toml:95` excludes the `integration` marker; the marker is opt-in PER FILE (62 of 207 files under `tests/integration/` carry it; `tests/integration/conftest.py` does NOT auto-mark — phase1-plan.md:61).
- Binding house rule (identical block in phase1-plan.md:61, phase2-plan.md:70, phase3-plan.md:62; normative in requirements.md:1133 and :1169-1172): integration gates MUST run as **`uv run python -m pytest --override-ini="addopts=" -m integration <paths> -q`** AND **MUST assert a non-zero EXPECTED COLLECTED COUNT — "0 failed" alone is vacuous**. Worked examples: phase1-plan.md:109 (collected == expected, >0), phase1-plan.md:112 (full fenced form incl. per-variable `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT HOME="$(mktemp -d)"` scratch-PG run), phase2-plan.md:95-96, phase3-plan.md:93.

**Facts for the regression guard's runner form (not a decision):**
- A `pg_stat_activity` idle-in-transaction guard is PG-dependent. Under default `pytest` it executes ONLY if it sits in `tests/` WITHOUT the excluded markers. The two sanctioned PG placements:
  1. **`tests/postgres/…_pg.py`** — auto-marked `postgres` by conftest (:82-95); runs ONLY via explicit `pytest tests/postgres/ --override-ini="addopts=" -m postgres` (serial; xdist auto-skips); rides the shared `pg_engine` fixture with TRUNCATE isolation.
  2. **`tests/integration/…` + `pytestmark = pytest.mark.integration`** — runs ONLY via the house form `--override-ini="addopts=" -m integration`; scratch-PG + per-variable POSTGRES_* scrub per the fenced examples above (the soak lane's own pins took this shape: burst_shapes/ghost_waiters).
  - In BOTH placements a bare default `pytest` collects ZERO of them → "0 failed" means nothing ran.
- Who runs which: default `pytest` (and whole-tree sweeps) execute unmarked `tests/unit/**` + unmarked files only; PG-dependent pins execute when a lane/tester invokes them explicitly (every soak-lane gate in the planning dir names exact paths + the override-ini form; tester packs live in `.agents/tester/PACKS.md`).
- **Expected-collected-count mechanics:** `pytest <paths> --collect-only -q` prints one line per collected test id (plus summary tail); the house convention (phase1-plan.md:109, requirements.md:1133) is: assert collected count == the expected N **and** > 0 BEFORE interpreting the run's "0 failed". This is the exact non-vacuity assert the guard should carry.

---

## Q6 — Existing Pool-Related Tests (inventory)

**Daemon-engine knob pins:**
- `tests/unit/repositories/test_pg_engine_utc_session.py:114` — asserts `pool_pre_ping is True` in captured `create_engine` kwargs (+ pins the UTC connect_args shape). The ONLY committed pin on site-1 knobs.

**Soak-lane grace/leak/pool pins:** listed in Q3 (6 default-run unit packs + 2 integration packs).

**`idle_in_transaction` hits:** only the vacuum/repair engine timeout OPTIONS strings — `tests/unit/test_run_vacuum_full_after_cleanup.py:192/:212` (`idle_in_transaction_session_timeout=120000` in the vacuum engine options). **No test anywhere asserts "zero idle in transaction"** — RCA fix-item 7's regression guard has no existing implementation.

**`pg_stat_activity` hits (all connection-hygiene, NOT leak assertions):** `tests/helpers/checkpoint_prune_pg.py:228`, `tests/helpers/nestops_pg_fixtures.py:314` (remaining-connection census), `tests/integration/test_empty_guard_checkpoint_roundtrip.py:486`, `tests/integration/test_mission_records_pr2_enforce_reads_pg.py:159`, `tests/integration/test_secrets_v1_basic.py:192`, `tests/integration/test_secrets_v1_endtoend.py:198`, `tests/migration/test_secrets_audit_context_parity.py:201/:260`, `tests/test_prompt_approval_service.py:204`, `tests/test_prompt_version_resolution.py:232`, `tests/unit/test_mission_record_commission_persistence_pg.py:153`, `tests/postgres/test_readiness_pg.py:366` — all are `pg_terminate_backend` scratch-DB cleanup / teardown.

**`pool_pre_ping` elsewhere:** ~50 hits are scratch/test-engine fixture plumbing (e.g. `tests/postgres/conftest.py:135`, `tests/e2e/*`, `tests/migration/*`) — not daemon-engine pins. `reset_on_return`: zero hits in tests/.

---

## Open questions for the plan owner
1. `REPAIR_*` constants live where imported in ens_db_tools (referenced :606-610) — exact values not read; pull them before quoting the repair-engine budget in the plan.
2. `scripts/upgrade/lib.sh` soak semantics (3-consecutive-greens, never-green trigger) were taken from the planning docs + RESULTS pointer, not read line-by-line in this pass.
3. Whether the guard should ride the `tests/postgres/` shared-`pg_engine` fixture (TRUNCATE isolation, session-scoped) or the integration scratch-DB pattern (own DB, drop teardown) is a plan decision — both runner forms are documented above.
4. The watchdog/thread-based `queue_freshness` probe leak shape (timed-out `to_thread` retention, readiness.py:40-44 docstring) is the confirmed P1 leak mechanism per the soak lane's adjudication (risk-safety-register.md:78) — the transaction-hygiene fix list vs pool-knob split is the plan's to make.
