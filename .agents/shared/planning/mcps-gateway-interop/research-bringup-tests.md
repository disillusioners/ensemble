# MCPS Gateway Interop — Bring-Up & Test-Harness Recon (read-only)

Repo: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble` — branch `latest` @ `9632badd4`.
Sibling (Go client): `/Users/nguyenminhkha/All/Code/opensource-projects/mcps_client`.

## Confidence: HIGH

## Concise
The existing mcps_gateway suite boots the REAL `create_app()` on an ephemeral port but with `lifespan="off"`, injects a hand-built `McpGatewayService` with a `MemoryAuditSink`, so it proves route registration and protocol conformance and proves NOTHING about audit rows reaching a database. A real-DB interop lane is feasible from the repo tree (never the frozen install dir) by booting `uvicorn daemon.api:app` with `MCPS_GATEWAY_ENABLED=1` against a disposable PostgreSQL, but the shipped Go client REFUSES `ws://` outright (TLS-only) and requires a supervised child MCP server, so a plaintext `ws://127.0.0.1:<port>` interop run is not possible without TLS termination or a client patch.

---

# 1. EXISTING HARNESS

## 1.1 Does it use the REAL `create_app()` + REAL router registration? — YES (route); NO (lifespan)

- `tests/integration/mcps_gateway/harness.py` (288 L) is the only module in the package allowed to import `daemon.*` (harness.py:3-31).
- Imports: `from daemon.api import create_app` (harness.py:186), `load_gateway_config` (:188), `McpGatewayService` (:189), `MemoryAuditSink` (:187).
- `app = create_app()` — harness.py:195. This is the REAL composition function (`daemon/api.py:4149 def create_app(auth_config=None)`), so the route table is the production one.
- Route registration is inside `create_app` and CONDITIONAL on `MCPS_GATEWAY_ENABLED`, read at create_app time (`daemon/api.py:4563-4589`): `gateway_config = load_gateway_config()` (:4563) → `if gateway_config.enabled:` (:4566) → `build_mcps_gateway_router(path=...)` (:4569) → `api_router.include_router(...)` (:4573). Disabled path logs "route ... is not registered" (:4585-4589) and the path 404s.
- **The lifespan does NOT run**: `uvicorn.Config(..., lifespan="off")` (harness.py:209) with the comment at :196-199. Therefore the production wiring `app.state.mcps_gateway_service = manager.mcps_gateway_service` (`daemon/api.py:2401`, inside the lifespan) never executes; the harness sets it by hand at harness.py:199.

## 1.2 Ephemeral port — how

- `uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off", access_log=False)` — harness.py:204-211.
- Wait loop on `server.started` with a 20 s deadline; on timeout `should_exit = True` then re-raise (harness.py:214-220).
- Actual port read from the bound socket: `sock = server.servers[0].sockets[0]; port = sock.getsockname()[1]` (harness.py:222-224).
- `BootedGateway.ws_url` = `ws://{host}:{port}{ws_path}` (harness.py:127-129).

## 1.3 Audit sink — IN-MEMORY, not DB (the decisive gap)

- `audit_sink = MemoryAuditSink()` then `service = McpGatewayService(config, audit_sink=audit_sink)` — harness.py:192-193; surfaced on `BootedGateway.audit_sink` (:229).
- `MemoryAuditSink` is a plain list (`daemon/services/mcps_gateway/audit.py:135-149`).
- The only audit assertion reads that list: `records = list(getattr(gw.audit_sink, "records", []) or [])` — `tests/integration/mcps_gateway/test_mcps_gateway_protocol.py:1087-1113` (`TestAudit.test_audit_records_an_args_hash_and_never_the_raw_arguments`, :1063-1113).
- `COVERAGE.md:116-117` records that only the `McpGatewayAuditRepository` CONSTRUCTOR SIGNATURE was consulted "to judge whether the audit trail is reachable from a test" — i.e. the DB sink was never exercised.
- **Answer: the existing suite proves audit RECORDS are produced; it does not prove rows reach any database.**
- `tests/integration/test_mcps_gateway_ws.py` (505 L) has ZERO occurrences of "audit" (grep) — it is the route/auth/hello/session/tools interop file.

## 1.4 Env it sets / restores

- `GatewayEnv` dataclass (harness.py:77-106) → `as_environ()` emits 11 knobs: `MCPS_GATEWAY_ENABLED`, `_TOKENS`, `_HEARTBEAT_INTERVAL_MS`, `_HEARTBEAT_TIMEOUT_MS`, `_CLIENT_DEAD_DETECT_MS`, `_COMMAND_TIMEOUT_MS`, `_SESSION_TTL_MS`, `_SESSION_IDLE_TIMEOUT_MS`, `_ALLOWED_MACHINES`, `_PATH`, `_AUDIT_ENABLED` (constants at :49-59; `CONTRACT_ENV_KNOBS` at :62-71 omits `_AUDIT_ENABLED`).
- `_EnvPatch` saves/restores each key, popping when the old value was absent (harness.py:136-154); entered at :184, restored in `finally` at :251-252.
- NOT set: any `POSTGRES_*`, `ENSEMBLE_SELF_ENV`, `OPENAI_API_KEY`, `DATA_DIR`. The harness inherits ambient env for everything else.

## 1.5 Shutdown

- `server.should_exit = True` → `await asyncio.wait_for(task, timeout=15)` (suppressing `asyncio.TimeoutError`) → `sock.close()` → `await asyncio.sleep(0.05)` (harness.py:243-250).
- `port_is_listening(host, port)` helper (harness.py:255-263) is the "nothing left listening" assertion primitive; the Phase-B report records "teardown test asserts no listener survives" (`.agents/tester/RESULTS/2026-10-22-mcps-gateway-phaseb-fake-client.md:7`).
- No subprocess, no kill -9, no PID file — the server is in-process (asyncio task).

## 1.6 Runtime / marker

- File header + run command: `timeout 300 uv run python -m pytest -m integration tests/integration/mcps_gateway/ --timeout=30` (RESULTS …phaseb-fake-client.md:7).
- Measured: **24.10 s / 24.26 s / 24.03 s** across 3 identical runs (same doc, line 12), for 33 tests (30 PASS / 3 FAIL at that moment; the failures were gateway findings F1-F3, later fixed and re-run to 34/34).
- Env used by the WS file: `FAST_ENV` heartbeat 200 ms / timeout 600 ms / command timeout 1500 ms (`tests/integration/test_mcps_gateway_ws.py:73-82`) — deliberately inside the 30 s per-test budget.
- Suite composition: `fake_remote_client.py` 855 L (zero `daemon.*` imports), `harness.py`, `test_mcps_gateway_protocol.py` 1290 L, `test_fake_client_selftest.py` 247 L (pure protocol, no network — file docstring :1-9).

---

# 2. DEV/TEST BRING-UP

## 2.1 The "ensemble_dev_test_only" fence — what it is, where, and what it refuses

There is **no single central fence file**. It is a per-file, session-scoped autouse fixture copy-pasted across ~40 test modules (grep `HARD ABORT` / `ensemble_dev_test_only`).

Canonical implementations:

| File:line | Guard |
|---|---|
| `tests/test_resource_monitor_api.py:47-63` | `os.environ["ENSEMBLE_SELF_ENV"]="dev"`; `allowed_dbs={"ensemble_dev_test_only","ensemble_test_only"}`; if `POSTGRES_DB` is `None`/`""`/`"ensemble_prod"` → **rewrite** to `ensemble_dev_test_only`; elif not in allowed → `pytest.exit(f"HARD ABORT: POSTGRES_DB=...", returncode=1)` |
| `tests/test_prompt_cache_invalidation.py:119-141` | refuses `POSTGRES_DB == "ensemble_prod"`; refuses `ENSEMBLE_SELF_ENV` not in `{dev, demo, sandbox}` (empty allowed) |
| `tests/test_custom_agent_lifecycle.py:67-80`, `tests/test_custom_agents_envelope_h3.py:59-65`, `tests/test_loader_empty_content_m16.py:44-50`, `tests/test_resource_*` (6 files), `tests/unit/services/test_resource_*.py` | same shape (`allowed_dbs` set + `HARD ABORT`) |
| `tests/test_resource_monitor_api.py:58-62` | also forces `FAKE_HOME`/`HOME` to a `mkdtemp` if not already absolute |

What makes it pass:

- `ENSEMBLE_SELF_ENV=dev` and `POSTGRES_DB` either unset/empty (rewritten to `ensemble_dev_test_only`) or exactly `ensemble_dev_test_only` / `ensemble_test_only`.
- **Trap:** the rewrite does NOT create the database. If PG has no `ensemble_dev_test_only`, the run dies with `psycopg FATAL: database "ensemble_dev_test_only" does not exist` / `SystemExit 75` — recorded verbatim as an environmental quarantine at `.agents/tester/QUARANTINE.md:141` (Phase-C merge gate; "27/27 PASS under the gate's sanctioned `env -u POSTGRES_*` scrub"). A "fence KV says `ensemble_dev_test_only` but the DB is `ensemble_dev`" reconciliation issue is recorded at `.agents/tester/RESULTS/2026-10-22-mcps-gateway-fe-phaseC-merge-gate.md:123` (shard aborts rc=3).
- The `mcps_gateway` suite itself has **no fence** and needs no DB (harness has no engine).

## 2.2 Minimal boot recipe (env → engine → app)

**Engine selection.** `daemon/api.py:542-558` loads `ensemble.json` BEFORE config.yaml:
`data_dir = ENSEMBLE_DATA_DIR or DATA_DIR or "./data"` (:548-552) → `EnsembleConfig.load_or_create(data_dir)` (:553).

`daemon/ensemble_config.py:55-96`:
- if `<data_dir>/ensemble.json` EXISTS it wins outright (:71-79) — **a stale file silently pins `database:"sqlite"`**;
- else `POSTGRES_HOST` AND `POSTGRES_DB` both set (:82) AND no `instances.db`/`checkpoints.db` in the data dir (:83-86) → `database="postgres"` (:88-89);
- otherwise `"sqlite"` (:90-92). The decision is then **persisted** to `ensemble.json` (:95).

Engine construction: `daemon/manager.py:693-698` — `if ensemble_config.is_postgres: create_postgres_engine(...)` else `DatabaseConfig.sqlite(...)`.

**Connection vars.** `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` override `ensemble.json` values (`daemon/repositories/factory.py:265-269`; same precedence in `daemon/ensemble_config.py:114-130`).
`POSTGRES_URL` is honored **only by the checkpointer** (`daemon/persistence.py:132-147`), NOT by the repository engine (`factory.py:189-198` reads only `POSTGRES_*` parts) — the known F-DR1-2 split-brain. Do not configure a real-DB test with `POSTGRES_URL` alone.

**Gateway knobs** (`daemon/services/mcps_gateway/config.py`):
- `MCPS_GATEWAY_ENABLED` default **false** (:11, `_env_bool` :104-126 accepts `1/true/yes/on`).
- `MCPS_GATEWAY_PATH` default `/api/mcps-gateway/ws` (:12, `DEFAULT_PATH` :52).
- `MCPS_GATEWAY_TOKENS` empty = **nobody may enroll** (:24) — fail closed.
- heartbeat/timings (:13-22), `MCPS_GATEWAY_AUDIT_ENABLED` default true (:18).
- All read at CALL time (`docs/mcps-gateway.md:184-186`) **except** `ENABLED` and `PATH`, which are consumed at `create_app` time (`api.py:4563-4573`) — see risk R4.

**PORT.** `uvicorn` CLI `--port` is authoritative when booting by module; the maintained scripts also export `DAEMON_PORT` and `PORT` (`frontend/scripts/boot-e2e-maintenance-daemon.sh:137-139`). `daemon.api` has no module-level port read.

**Is `uv run python -m uvicorn daemon.api:app` supported?** YES.
- `daemon/api.py:4836` (repeated at :4839) `app = create_app()` — module-level app for the uvicorn import string.
- Precedent in-tree: `frontend/scripts/boot-e2e-maintenance-daemon.sh:169-172` runs exactly `uv run python -m uvicorn daemon.api:app --host 127.0.0.1 --port "$DAEMON_PORT" --log-level info --timeout-graceful-shutdown 10`.
- `docs/mcps-gateway.md:154-166` states activation = env + **daemon restart**, because route registration is composed once at boot inside `create_app`; env changes are not hot-reloaded.

**Frozen-install-dir hazard.** `~/agents-ensemble` is a frozen PyInstaller install (`Makefile:10 INSTALL_DIR ?= $(HOME)/agents-ensemble`; binary `ensemble-prod`, `Makefile:30-32`). Running there exercises the frozen binary and the install's own `.env`/data dir, not the tree; `make install` also (a) always builds `latest` tip via `ensure-latest` (so a tag checkout is ignored) and (b) can silently refuse to stop the old daemon (`Makefile:42-58` help text; project critical notes: "make install traps"). A test lane must run from the checkout via `uv run`, with its own `ENSEMBLE_DATA_DIR`.

**Competing prod daemon on 9797.**
- Prod port comes from `.env.prod` with fallback `9797` (`Makefile:19-27`).
- Avoidance is structural: bind `127.0.0.1:0` (in-process) or a checked-free ephemeral port in a script; never export `PORT=9797`; never use `make stop` / port-kill (`Makefile:75-100`; `scripts/stop-ensemble.sh` is ownership-scoped).
- The existing companion lane uses `DAEMON_PORT=8099` + PG `15432` (`frontend/scripts/boot-e2e-maintenance-daemon.sh:13-16`) — i.e. "pick ports no other daemon uses" is the established convention.

**Lifespan prerequisite.** The daemon lifespan requires `OPENAI_API_KEY`; the e2e boot script hard-fails with a clear message when it is absent (`frontend/scripts/boot-e2e-maintenance-daemon.sh:144-150`) and notes the key is never used by that spec.

---

# 3. REAL DB FOR AUDIT ASSERTIONS

## 3.1 (a) Can an integration test get a real PG session? — YES

In-pytest convention (independent of the daemon env): `tests/helpers/checkpoint_prune_pg.py:32-38`

```
PG_TEST_HOST (localhost) / PG_TEST_PORT (5432) / PG_TEST_USER (ensemble)
PG_TEST_PASSWORD (ensemble_dev) / PG_TEST_DB (ensemble_test)  → ADMIN_DSN
```

- Disposable DB per test: `create_disposable_db()` / `drop_database(name)` used at `tests/integration/checkpoint_prune_limit_batching_real_pg.py:88-99` (fixture `pg_db`), "safe under xdist; never touches shared DBs" (:89-91).
- Unavailable PG ⇒ **loud skip**, never a mock: `_probe_pg_or_skip` (`checkpoint_prune_limit_batching_real_pg.py:72-85`, "HONESTY CONTRACT" at :33-35).
- The `PG_TEST_*` convention is shared with `tests/postgres/conftest.py` (cited at `tests/helpers/checkpoint_prune_pg.py:6-7`).
- `tests/integration/conftest.py:15-77` only patches `normalize_project_id`; it provides no DB fixture and no fence.

## 3.2 (b) Does SQLite work for this path? — YES, with caveats

- Engine branch: `daemon/manager.py:696-698` (`DatabaseConfig.sqlite(db_path=...)`).
- The audit model is dialect-neutral: all columns `Text`, ISO-8601 TEXT `created_at` (`daemon/repositories/mcps_gateway/models.py:43-112`; timestamp rationale :17-24).
- `SQLModel.metadata.create_all(manager.engine)` runs at lifespan for the audit table — `daemon/api.py:996-1004` — and works on both dialects.
- SQLite migration exists and is idempotent: `daemon/migrations/versions/20261022_000001_create_mcp_gateway_audit.sql:78-99` (`CREATE TABLE IF NOT EXISTS` + 3 indexes); header states PG's schema comes from `create_all` and the .sql runner is a NO-OP on PG (:46-56).
- Caveat: choosing SQLite requires `POSTGRES_*` unset — which collides with the fence's rewrite of an empty `POSTGRES_DB` to `ensemble_dev_test_only`. The sanctioned posture for a SQLite/scrubbed run is `env -u POSTGRES_*` (`scripts/run_tests_scrubbed.sh`; `QUARANTINE.md:141`).

## 3.3 (c) What must be true for "audit rows land in the DB" to be provable

1. **The DB sink must be the one the daemon builds.** Production wiring is in the MANAGER, not the API: `daemon/manager.py:1453-1456`
   `McpGatewayService(config=load_gateway_config(), audit_sink=McpGatewayAuditRepository(engine=self._engine))` — same engine as every repository. `app.state.mcps_gateway_service = manager.mcps_gateway_service` (`daemon/api.py:2401`).
2. **The table must exist** — created only in the lifespan (`daemon/api.py:1003-1004`). A `lifespan="off"` harness has no table, which is exactly why the current harness cannot assert DB rows.
3. **`MCPS_GATEWAY_AUDIT_ENABLED` must not be false** — `if not self.config.audit_enabled: return` (`daemon/services/mcps_gateway/service.py:1088-1089`).
4. **A command must actually be forwarded** — `_audit_command(...)` is called from the `command()` path; the write is awaited off-loop and bounded: `await asyncio.wait_for(asyncio.to_thread(self._audit.record, record), timeout=self._audit_write_timeout_seconds)` (`service.py:1122-1129`). Audit failure is swallowed-and-counted by design (`audit.py:23-38`).
5. **The row shape** — `created_at` (now_utc_iso), `agent_id`, `instance_id`, `machine_id`, `tool`, `args_hash`, `outcome`, `session_id`, `error_code`, `detail`; NO arguments column (`repository.py:45-69`; `models.py:8-15`).
6. **Query seam for the assertion** — `McpGatewayAuditRepository.list_recent(limit=50, offset=0)` (`repository.py:73-97`, ORDER BY `created_at DESC`, defensive on read) or a direct `SELECT ... FROM mcp_gateway_audit`. `agent_id` is NOT NULL (`models.py:88`), so any row-producing path must carry an agent identity.

**Conclusion:** the DB path is provable only with a real lifespan boot (or a hand-built `McpGatewayAuditRepository(engine=<real engine>)` + explicit `create_all`). The current suite proves neither.

---

# 4. TEST CONVENTIONS

## 4.1 Markers (registered, not ad-hoc)

`pyproject.toml:86-97`:

```
testpaths = ["tests"]
markers = integration | postgres | no_xdist | slow
addopts = "-m 'not integration and not postgres and not slow'"
asyncio_mode = "auto"
timeout = 30
timeout_method = "thread"
```

- Any new test marked `integration` is **deselected by default**; run with `--override-ini="addopts="` (`testing-guide.md:13-16`) or `-m integration`.
- Per-test timeout is 30 s, thread method — a WS test must finish well inside that (the conformance suite uses millisecond heartbeats for this reason, `tests/integration/test_mcps_gateway_ws.py:71-82`).

## 4.2 Invocation

- `.venv/bin/pytest` (`testing-guide.md:7-19`) or `uv run python -m pytest <paths>` (RAG-documented repo gotcha: bare `pytest` is broken; run from the worktree root).
- `scripts/run_tests_scrubbed.sh` (95 L) is the mandated env-scrub wrapper: unsets `POSTGRES_*`, `DATABASE_URL_POSTGRES`, `POSTGRES_URL`, and the bare libpq set (`PGHOST…PGSERVICE`), echo-verifies ZERO survivors family-wide, exits **78** on leak, then `exec .venv/bin/pytest "$@"` (`:25-95`). It deliberately does NOT touch `PG_TEST_*` (comment :36-40) — that namespace is the test harness's own.
- **`Makefile` has NO test targets at all** (grep `test` over the 407-line file: no match; targets are build/install/start/stop/deploy/upgrade, `Makefile:42-72`).

## 4.3 Runtime classes

unit (default lane, no external services) → `integration` (live externals) → `postgres` (live PG) → `slow` (opt-in, ~200 MB downloads / minutes). E2E (LLM, ~45-140 s per test) is a separate lane (`testing-guide.md:22-53`).

## 4.4 Quarantine ledger + process

- Location: `.agents/tester/QUARANTINE.md` (158 L). Header: "Pre-existing failures that are skipped and do NOT count toward a pack's PASS/FAIL" (:3).
- Columns: `Test | Pack / File | Date Quarantined | Reason | Retry Budget | Attempts (P/F) | Status` (:8). Each row carries a base-attribution narrative and an evidence path.
- Process = append a row with (a) the exact node/file scope, (b) base-vs-branch attribution evidence, (c) the deselect/workaround, (d) retry budget. Precedent for exactly this subsystem: `QUARANTINE.md:141` (Phase-C merge gate — `POSTGRES_DB` fence, environmental, cleared by `env -u POSTGRES_*`).
- Surrounding governance: `.agents/tester/PACKS.md` (pack definitions / gate commands) and `.agents/tester/RESULTS/` (evidence-chain docs, e.g. `2026-10-22-mcps-gateway-phaseb-fake-client.md`, `2026-10-22-mcps-gateway-fe-phaseC-merge-gate.md`).

## 4.5 Where a shell-driven interop script conventionally lives

- **Root `scripts/`** is the home for repo-level operational scripts: `run_tests_scrubbed.sh`, `stop-ensemble.sh`, `test-migration.sh`, `test_builtin_mcp_servers.sh`, `ensure.sh`, `deploy.sh`, plus `scripts/upgrade/`, `scripts/systemd/`, `scripts/migrations/`.
- The nearest full "boot a disposable daemon + disposable PG for a test lane" precedent is `frontend/scripts/boot-e2e-maintenance-daemon.sh` (221 L) — it lives under `frontend/` only because it serves the Playwright lane; the daemon+PG mechanics are backend-general (see §5).
- `tests/integration/chat_source_harness.py` + `tests/integration/chat_source_live_injection_e2e.py` show the in-process Python harness convention (file-backed SQLite, no PG).

---

# 5. CLEAN SHUTDOWN — existing patterns

| Pattern | Evidence |
|---|---|
| In-process uvicorn: `should_exit` → bounded `await task` (15 s) → `sock.close()` → 50 ms settle | `tests/integration/mcps_gateway/harness.py:243-250`; start-timeout path also sets `should_exit` before re-raising (:214-220) |
| "Nothing left listening" probe | `harness.py:255-263 port_is_listening()`; RESULTS doc line 7 records the teardown assertion |
| Subprocess daemon + disposable PG, 3+ layers of teardown | `frontend/scripts/boot-e2e-maintenance-daemon.sh` |
| — TERM/INT trap **and** EXIT trap **and** explicit `cleanup()` on normal exit | `:160`, `:168`, `:196`; comment at :161-167 explains the Playwright SIGKILL race |
| — `cleanup()` kills the daemon by PID file, stops PG only if `$DATA_DIR` exists (foreign-cluster refusal at :75-79), `rm -rf` data dirs unless `ENSEMBLE_E2E_KEEP=1` | `:54-89` |
| — Port-in-use handling: if `:PG_PORT` is up but `$DATA_DIR` is missing → **exit 1** rather than adopt a stale/foreign cluster | `:99-108` |
| — Scrub inherited DB env before boot | `:93-97` (`unset POSTGRES_HOST/PORT/DB/USER/PASSWORD/URL`, `ENSEMBLE_DB_DSN`) |
| — Graceful uvicorn shutdown budget | `:171` `--timeout-graceful-shutdown 10` |
| — Playwright `globalTeardown` as deterministic backstop | `frontend/e2e/global-teardown-maintenance.ts` (referenced at script :166) |

No port-in-use retry loop exists anywhere; the convention is "refuse and exit" (script :102-108) or "bind 0 and read the kernel-assigned port" (harness :204-224).

---

# 6. RISK LIST (bring-up specific)

| # | Failure mode | Produced by |
|---|---|---|
| R1 | **PG not running** → daemon dies at boot. Under `uvicorn daemon.api:app` the `_boot_db_preflight` does NOT run (it lives in `daemon/__main__.py:104/:212/:246`, and `__main__.py:40-44` explicitly documents that the `python -m uvicorn daemon.api:app` path "never imports this module"). Consequence: no exit-75 classification, no bounded probe — the failure surfaces as a lifespan/engine exception. | `daemon/__main__.py:40-44, 75-81, 195-215`; `daemon/manager.py:693-695` |
| R2 | **Fence-selected DB absent** → `psycopg FATAL: database "ensemble_dev_test_only" does not exist` / `SystemExit 75` | `.agents/tester/QUARANTINE.md:141`; fence rewrite at `tests/test_resource_monitor_api.py:51-52` |
| R3 | **Fence refuses** → `pytest.exit(... returncode=1)` for any `POSTGRES_DB` outside `{ensemble_dev_test_only, ensemble_test_only}`; `ENSEMBLE_SELF_ENV` outside `{dev,demo,sandbox}` also aborts | `tests/test_resource_monitor_api.py:50-57`; `tests/test_prompt_cache_invalidation.py:121-136` |
| R4 | **Gateway route absent (404)** because env was set after `create_app` ran — including after `import daemon.api` (module-level `app = create_app()` at :4836 executes at import) | `daemon/api.py:4836`, `:4563-4573`, `:4584-4589`; `docs/mcps-gateway.md:154-166` |
| R5 | **Every WS upgrade 401** because `MCPS_GATEWAY_TOKENS` is empty (fail-closed) — looks like an auth bug, is a config gap | `daemon/services/mcps_gateway/config.py:24`; `docs/mcps-gateway.md:226-247`; route auth at `daemon/routers/mcps_gateway.py:401-411` |
| R6 | **Route resolves no service** (503/refusal) when the lifespan did not run and nothing set `app.state.mcps_gateway_service` | route lookup `daemon/routers/mcps_gateway.py:270`; production set `daemon/api.py:2401`; harness manual set `harness.py:199` |
| R7 | **Audit rows never reach a DB** because the service was hand-built with `MemoryAuditSink` (harness precedent) — the exact trap a "reuse the harness" plan falls into | `harness.py:187,192-193`; prod sink `daemon/manager.py:1453-1456` |
| R8 | **Audit table missing** on a lifespan-off boot → every `record()` raises, is logged, and the command still succeeds → silent zero rows | table DDL `daemon/api.py:1003-1004`; swallow semantics `daemon/services/mcps_gateway/audit.py:23-38`, `service.py:1122-1131` |
| R9 | **Audit write times out** (bounded `wait_for` + `to_thread`) with a slow/stalled PG → command OK, no row | `daemon/services/mcps_gateway/service.py:1127-1129` |
| R10 | **Port collision / stealing prod** if 9797, 8079, 8099 or 15432 is assumed free; the prod daemon is 9797 | `Makefile:19-27`; `frontend/scripts/boot-e2e-maintenance-daemon.sh:13-16, 99-108` |
| R11 | **Go client refuses `ws://`** — `normalizeURL` rejects `ws`/`http` schemes and config validation rejects anything not `wss://`/`https://`. A plaintext `ws://127.0.0.1:<port>` interop run CANNOT start. | `mcps_client/internal/remote/transport.go:257-281`; `mcps_client/internal/config/config.go:930-933` |
| R12 | **Go client TLS trust** — with a self-signed cert the ordinary chain verification still runs even when a pin is set (`VerifyPeerCertificate` calls `leaf.Verify` against `RootCAs` first) → a `ca_pem` trust anchor is required in addition to (optionally) `server_pin_sha256` | `transport.go:290-338`; config keys `server_pin_sha256`/`ca_pem` at `config.go:232-241` |
| R13 | **Wrong path** — with a path-less `center_url` the Go client appends `/remote-hands/v1`, which this daemon does not serve → 404 | `docs/mcps-gateway.md:249-264`; `transport.go:277-279` |
| R14 | **Go client needs a supervised child MCP server** — it is a wrapper that supervises the cu server; the tool catalog comes from the child, so a `tools/call` needs a real child tool | `mcps_client/cmd/mcps-client/main.go:1-9`; catalog in `session.begin` per `docs/mcps-gateway.md:104-107` |
| R15 | **Agent identity gate** — the four tools authorize exactly `{ari, leader}`; `agent_id` is NOT NULL in the audit row, so driving the tool surface with an arbitrary agent id yields a typed refusal, not a row | `daemon/tools/mcp_rpc_tools.py:82-87`; `daemon/repositories/mcps_gateway/models.py:88` |
| R16 | **Lifespan dies without `OPENAI_API_KEY`** — before the route is ever usable | `frontend/scripts/boot-e2e-maintenance-daemon.sh:144-150` |
| R17 | **Stale `ensemble.json` in the data dir silently forces SQLite** (env ignored) → "real DB" becomes a local file | `daemon/ensemble_config.py:71-79, 95` |
| R18 | **`POSTGRES_URL` alone does nothing for repositories** — only the checkpointer honors it (F-DR1-2 split-brain) | `daemon/persistence.py:132-147` vs `daemon/repositories/factory.py:189-198, 244-269` |
| R19 | **mcp SDK is mocked at collection time** in the repo's pytest root conftest → an in-process Python MCP child would see MagicMocks; real-SDK paths need subprocess e2e | `tests/conftest.py:107-119`; convention in `tests/unit/test_mcp_quote_sanitization.py` |
| R20 | **Frozen install dir** — booting `~/agents-ensemble` tests the frozen binary + its own env/data, not the branch under test | `Makefile:10, 29-34, 42-58` |

---

# 7. CONCRETE RECIPE (copy-pasteable)

> Constraints honoured: repo tree (never `~/agents-ensemble`), ephemeral ports, disposable PG, disposable data dir, no `make stop`, clean PID-based teardown.

## 7.1 Variant A — in-process (Python), real PG, real lifespan, audit-to-DB

```python
# tests/integration/mcps_gateway/test_audit_db_interop.py  (sketch — not written)
# Marker: pytestmark = pytest.mark.integration   (pyproject addopts deselected by default)
import os, socket, asyncio, pytest

pytestmark = pytest.mark.integration

@pytest.fixture
def ephemeral_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

# Env must be set BEFORE create_app() executes (api.py:4836 / 4563).
# DB must exist and be fence-allowed: ensemble_dev_test_only.
```

Key sequencing (each step is load-bearing, code-cited):

1. `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=<pg port>`, `POSTGRES_DB=ensemble_dev_test_only`, `POSTGRES_USER=<user>`, `POSTGRES_PASSWORD=<pw-or-empty>` — `factory.py:265-269`.
2. `ENSEMBLE_DATA_DIR=<mktemp -d>` **and delete any `ensemble.json`** in it — `ensemble_config.py:71-79`.
3. `MCPS_GATEWAY_ENABLED=1`, `MCPS_GATEWAY_TOKENS=<token>`, `MCPS_GATEWAY_AUDIT_ENABLED=1` — `config.py:11/13/18`.
4. `OPENAI_API_KEY=<dummy>` — boot script `:144-150`.
5. Call `create_app()`; assert `/api/mcps-gateway/ws` in the WS routes (pattern: `harness.py:157-162, 238-240`).
6. Boot with the lifespan **ON** (`uvicorn.Config(app, host="127.0.0.1", port=<ephemeral>, lifespan="on")`) so `api.py:1003-1004` creates `mcp_gateway_audit`.
7. Drive the center from the service object: `app.state.mcps_gateway_service.connect(machine_id=..., agent_id="ari")` then `.command(agent_id="ari", tool="echo", arguments={...})` — signature precedent `test_mcps_gateway_protocol.py:1075-1083`.
8. Assert with the DB reader: `McpGatewayAuditRepository(engine=app.state.manager.engine).list_recent()` (`repository.py:73-97`) — or `SELECT count(*) FROM mcp_gateway_audit`.
9. Teardown: `server.should_exit = True; await wait_for(task, 15); sock.close()` (`harness.py:243-250`), then drop the disposable DB.

**Note:** the peer in step 7 must be a real WS client (the existing `FakeRemoteMachine`) — the service will not complete a command without an enrolled connection.

## 7.2 Variant B — out-of-process daemon + real Go client (TLS required)

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO=/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
CLIENT=/Users/nguyenminhkha/All/Code/opensource-projects/mcps_client
WORK=$(mktemp -d /tmp/gw-interop.XXXXXX)
PGDATA="$WORK/pg"; DBDIR="$WORK/data"; LOGS="$WORK/logs"; mkdir -p "$LOGS" "$DBDIR"

# 1) Pick free ports (never 9797 / 8079 / 8099 / 15432).
pick() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
PG_PORT=$(pick); GW_PORT=$(pick)

# 2) Disposable PG cluster (trust auth, local only).
initdb -A trust -D "$PGDATA" --no-locale -E UTF8 >>"$LOGS/boot.log" 2>&1
pg_ctl -D "$PGDATA" -l "$LOGS/pg.log" -o "-p $PG_PORT -k $WORK" start >>"$LOGS/boot.log" 2>&1
createdb -h 127.0.0.1 -p "$PG_PORT" ensemble_dev_test_only   # fence-allowed name AND it must exist

# 3) Scrub the ambient DB env (prod bleed is the documented incident class).
unset POSTGRES_URL DATABASE_URL_POSTGRES ENSEMBLE_DB_DSN PORT
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="$PG_PORT" POSTGRES_DB=ensemble_dev_test_only
export POSTGRES_USER="$(whoami)"; unset POSTGRES_PASSWORD
rm -f "$DBDIR/ensemble.json"                                  # else ensemble_config pins sqlite

# 4) Gateway + daemon identity.
export MCPS_GATEWAY_ENABLED=1
export MCPS_GATEWAY_TOKENS="tok-interop"
export MCPS_GATEWAY_AUDIT_ENABLED=1
export ENSEMBLE_SELF_ENV=dev
export ENSEMBLE_DATA_DIR="$DBDIR"
export OPENAI_API_KEY="sk-dummy-interop"                      # lifespan requires it; never used

# 5) TLS (REQUIRED for the Go client: transport.go:257-281 refuses ws://).
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "$WORK/key.pem" -out "$WORK/cert.pem" \
  -subj "/CN=127.0.0.1" -addext "subjectAltName=IP:127.0.0.1" >/dev/null 2>&1

# 6) Boot from the REPO TREE (never ~/agents-ensemble).
cd "$REPO"
uv run python -m uvicorn daemon.api:app --host 127.0.0.1 --port "$GW_PORT" \
  --ssl-keyfile "$WORK/key.pem" --ssl-certfile "$WORK/cert.pem" \
  --timeout-graceful-shutdown 10 --log-level info >>"$LOGS/daemon.log" 2>&1 &
DAEMON_PID=$!

for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$GW_PORT/livez" >/dev/null 2>&1 && break; sleep 1; done

# 7) Real Go client.  center_url MUST include the gateway path; child MCP server required.
cat >"$WORK/mcps-client.yaml" <<YAML
remote:
  enabled: true
  center_url: "wss://127.0.0.1:$GW_PORT/api/mcps-gateway/ws"
  enrollment_token: "tok-interop"
  machine_id: "mach_interop"
  ca_pem: "$WORK/cert.pem"
child:
  command: ["<path-to-cu-mcp-server>"]     # the client supervises this child
YAML
( cd "$CLIENT" && go run ./cmd/mcps-client -config "$WORK/mcps-client.yaml" ) >>"$LOGS/client.log" 2>&1 &
CLIENT_PID=$!

# 8) Drive the center (agent side), then assert the DB row.
#    mcp_rpc_* tools authorize exactly {ari, leader} (mcp_rpc_tools.py:82-87).
#    Simplest driver: call app.state.mcps_gateway_service.connect/command from a
#    Python helper process against the SAME daemon (HTTP has no connect endpoint).
psql -h 127.0.0.1 -p "$PG_PORT" -d ensemble_dev_test_only \
  -c "SELECT agent_id, machine_id, tool, args_hash, outcome FROM mcp_gateway_audit ORDER BY created_at DESC LIMIT 5;"

# 9) TEARDOWN — PID-scoped, no port-kill, no make stop.
kill -TERM "$CLIENT_PID" 2>/dev/null || true; wait "$CLIENT_PID" 2>/dev/null || true
kill -TERM "$DAEMON_PID" 2>/dev/null || true; wait "$DAEMON_PID" 2>/dev/null || true
for _ in $(seq 1 20); do lsof -ti "tcp:$GW_PORT" >/dev/null 2>&1 || break; sleep 0.5; done
lsof -ti "tcp:$GW_PORT" && { echo "LISTENER SURVIVED"; exit 1; } || true
pg_ctl -D "$PGDATA" stop -m fast >>"$LOGS/boot.log" 2>&1 || true
rm -rf "$WORK"
```

**Hard rules encoded above (each is a risk citation):**
- never `POSTGRES_DB=ensemble_prod` / never `PORT=9797` (R3, R10);
- `ensemble_dev_test_only` must be CREATED, not merely exported (R2);
- env before process start (R4);
- tokens non-empty (R5);
- `lifespan` ON, else no audit table and no manager sink (R6-R8);
- `wss://` + `ca_pem` + IP SAN for the Go client (R11, R12);
- gateway path in `center_url` (R13);
- real child MCP server (R14);
- repo tree, not the frozen install (R20).

---

# 8. What is NOT proven today (gap statement)

- No test anywhere writes an audit row through `McpGatewayAuditRepository` — grep over `tests/` shows `audit` only against `MemoryAuditSink.records` (`test_mcps_gateway_protocol.py:1087`).
- No test boots `create_app()` with the lifespan ON; every gateway test uses `lifespan="off"` (`harness.py:209`).
- No test exercises the manager-built service (`daemon/manager.py:1453-1456`); the harness always injects its own (`harness.py:193`).
- No in-repo test drives the real Go client; the existing interop peer is the Python transcription (`fake_remote_client.py`, 855 L, zero daemon imports).
- No pytest fixture exists that yields a daemon `app` bound to a real PG engine (`tests/helpers/checkpoint_prune_pg.py` yields a checkpointer stack, not a daemon).

## Sources
- Files read: `tests/integration/mcps_gateway/{harness.py,test_fake_client_selftest.py,COVERAGE.md}`, `tests/integration/{test_mcps_gateway_ws.py,conftest.py,checkpoint_prune_limit_batching_real_pg.py}`, `tests/integration/mcps_gateway/test_mcps_gateway_protocol.py` (audit section), `tests/helpers/checkpoint_prune_pg.py`, `tests/conftest.py`, `tests/test_resource_monitor_api.py`, `tests/test_prompt_cache_invalidation.py`, `daemon/{api.py,manager.py,persistence.py,ensemble_config.py,__main__.py}`, `daemon/repositories/{factory.py,mcps_gateway/repository.py,mcps_gateway/models.py}`, `daemon/services/mcps_gateway/{config.py,audit.py,service.py}`, `daemon/routers/mcps_gateway.py`, `daemon/tools/mcp_rpc_tools.py`, `daemon/migrations/versions/20261022_000001_create_mcp_gateway_audit.sql`, `docs/mcps-gateway.md`, `pyproject.toml`, `Makefile`, `testing-guide.md`, `scripts/run_tests_scrubbed.sh`, `frontend/scripts/boot-e2e-maintenance-daemon.sh`, `.agents/tester/QUARANTINE.md`, `.agents/tester/RESULTS/2026-10-22-mcps-gateway-phaseb-fake-client.md`, `mcps_client/{internal/config/config.go,internal/remote/transport.go,cmd/mcps-client/main.go}`.
- Shared context directory: no prior files matching "mcp gateway" (empty).
- RAG knowledge base (mode: hybrid) — cross-checked the harness/audit-sink descriptions; all load-bearing claims above were re-verified against source.
