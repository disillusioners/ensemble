# Research: DB-pool idle-in-transaction leak sites — verified facts

- **Verified at:** worktree `/home/nea/ensemble-src-wt-dbpool-idletxn`, branch `fix/db-pool-idle-txn`, HEAD `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (verified via `.git` gitdir → `refs/heads/fix/db-pool-idle-txn`).
- **Method:** read-only code reads at HEAD. RCA = `/home/nea/ensemble-src/.agents/shared/incident-20261026-db-pool-idle-in-transaction.md` (read-only, untouched). Prior art = `.agents/shared/planning/soak-gate-mcp-warmup/decisions.md` §2 premise 10 + §premise 11.
- **Line numbers below are AT THIS HEAD** unless marked `(main)` for the shared checkout.
- **No fixes decided; no plan written; nothing outside this file was modified.**

---

## 1. Summary table

| Site | file:line@HEAD | RCA claim | Verdict | Mechanism class | Pool |
|---|---|---|---|---|---|
| A — `ens_db_repair_execute` dry-run explicit `BEGIN` | `daemon/tools/ens_db_tools.py:1479` (DDL) / `:1491` (DML); outer `try:` :1475, outer `except` :1513 (no rollback) | BEGIN outside the try ⇒ failure skips `session.rollback()` | **CONFIRMED** (structural; precise shape below) | (b) — failure path returns the connection relying on pool reset-on-return, no explicit rollback | Dedicated **repair engine** (`_build_repair_engine` :573, cached per-URL; QueuePool 2+3, `pool_pre_ping=True`, `pool_recycle=3600`, `pool_timeout=10s`, **`idle_in_transaction_session_timeout=60s`**) — NOT the manager engine |
| B — `/api/instances` list handler | `daemon/routers/instances.py:370-537` → `daemon/services/instance_lifecycle.py:5021` → `daemon/repositories/instance/repository.py:1259` (`with SQLModelSession` :1383 flat / :1434 root) | "ORM read path, autobegin without commit/rollback" | **CORRECTED** — session is context-managed; no structural leak | none of (a)/(b)/(c) at HEAD (close→pool reset-on-return='rollback' ends the txn); "no explicit commit/rollback in the code" is literally true | Manager engine (`daemon/repositories/factory.py:323-338`: QueuePool `pool_size=5, max_overflow=10, pool_pre_ping=True`, UTC connect_args; **no** `reset_on_return` override → default `'rollback'`; **no** statement/idle-in-txn timeout; **no** pool_recycle) |
| C — `SELECT count(*) FROM job_queue_items` fingerprint | REAL site: `daemon/services/defer_block_resolver.py:185-191` (`_DEFER_PENDING_COUNT_SQL`), executed at :274 (`with ... engine.connect()`) and :321 (inside `resolve()`'s connect at :306). Second count path: `daemon/tools/instance_read.py:1408-1416` (husk evidence, `with engine.connect()` :1412) | "the readiness `queue_freshness` probe" | **REFUTED** (probe reads `FROM task`; prior-art reconciliation re-verified) / **CORRECTED** for the fingerprint's real owner | none — both real sites context-managed | Manager engine (same as B) |
| D — readiness probes | `daemon/services/readiness.py` — timeouts :113 (DB 0.5s) / :122 (saver 1.0s) / :127 (queue 2.0s); timeout-note docstring :107-112; probe SQL :201-210, :228-237 (`FROM task`); `make_db_probe` :1463-1481 (`with engine.connect()` :1477); `make_queue_probe` :1716 (connect :1752); saver probe :1484+, ghost-waiter cancel :1669-1684; leak predicate :784+; pool sampling :500-582 | "the queue_freshness probe leaks a txn per tick" | **REFUTED** per-tick; **CONFIRMED** the timeout-path connection retention (module's own docstring) | (a)-shaped — on probe timeout the `to_thread` worker keeps running with the connection checked out until its SQL returns (DB/queue probes have NO cancellation; DB/queue/engine has NO server-side timeout) | DB probe + queue probe = **manager engine**; checkpoint-saver probe = the **psycopg saver pool** (`resolve_psycopg_pool` :500; pool sizing in `daemon/persistence.py`, min 1/max 5, timeout kwarg added by soak A4) |
| E — `mint_boot_epoch_if_none` UniqueViolation | `daemon/services/mission_record_service.py:679-692` → `_mint_if_no_open_epoch` :641-677 → `_mint_epoch_locked` :499 (insert :542-543, IntegrityError handler :544-567) → `daemon/repositories/mission_record/repository.py:498-514` (`with Session(self._engine)` :510); callers `daemon/services/auto_continue_boot_pass.py:1154-1159` (orphan sweep) + the auto-continue pass loop (same shape, no session arg) | "exception inside a transaction without a rollback leaks that connection" | **REFUTED as a leak mechanism** (boot lane passes NO session; repo session context-managed; IntegrityError → with-block exit → close → pool reset rollback). The 4×/boot UniqueViolation itself is real but its trigger is unexplained — open question | none at HEAD | Manager engine (mission repo built on the shared engine) |
| F — wedge-capture wrong table | `daemon/services/wedge_capture/db_capture.py:308` (`FROM tasks t`) and `:387` (`FROM tasks`); correct table `task` (`daemon/repositories/task/models.py:171` `__tablename__ = "task"`) | "`tasks` → `task`" | **CONFIRMED** (2 sites; both wrong; docstring :34 also says "tasks") | none — own one-shot psycopg connection, `close()` in `finally` :289-294; the aborted-txn errors are same-connection cascades after the first (wrong-table) failure | **No pool** — one-shot sync `psycopg.connect` (`_connect` :185-200, `connect_timeout` + per-statement `statement_timeout`, `application_name='wedge-capture'`) |

---

## 2. Per-site detail

### Site A — `ens_db_repair_execute` dry-run explicit `BEGIN` (fix F3)

**What it is.** The agent-facing privileged DB repair tool `ens_db_repair_execute` (`daemon/tools/ens_db_tools.py:915`). With `dry_run=True` it dispatches to `_dry_run_sqlite_safe` (:1395) to preview DDL/DML inside BEGIN/ROLLBACK. NOT the wedge-capture path (wedge-capture is `daemon/services/wedge_capture/` and never emits BEGIN). The session comes from the caller's `with Session(repair_engine) as session:` (:1067) on the dedicated cached repair engine.

**Structure at HEAD (exact):**
```python
1475:         try:
1476:             if sql_class == "DDL":
1479:                 session.execute(text("BEGIN"))      # ← outside INNER try
1480:                 try:
1481:                     session.execute(text(sql))
1483:                 except Exception as exc:
1484:                     ... "DRY-RUN DDL FAILED ..."    # no re-raise
1488:                 session.rollback()
1489:             elif sql_class == "DML":
1491:                 session.execute(text("BEGIN"))      # ← outside INNER try
1492:                 try:
1493:                     result = session.execute(text(sql))
1501:                     session.rollback()
1502:                 except Exception as exc:
1503:                     session.rollback()
1504:                     ... "DRY-RUN DML FAILED ..."
1513:         except Exception as exc:                     # OUTER except
1514:             ... "DRY-RUN WRAPPER ERROR ..."          # ← NO session.rollback()
```

**Verdict: CONFIRMED**, with one precision correction to both the RCA and prior-art premise 11: the BEGINs are *inside the outer* `try:` (:1475) but *outside the inner* try/except that owns the rollback. A failure AT the BEGIN (:1479/:1491) — or at the DDL-branch `rollback()` itself (:1488) — jumps to the outer `except` (:1513), which appends a preview line and does **not** roll back.

**Failure/success-path inventory:**
- DDL success: rollback at :1488 ✔. DDL SQL fails: inner except :1483 (no re-raise) → rollback :1488 still runs ✔. BEGIN fails: outer except :1513, **no rollback** ✘. `rollback()` itself fails: outer except, no retry ✘.
- DML success: rollback :1501 ✔. DML SQL fails: rollback :1503 ✔. BEGIN fails: outer except :1513, **no rollback** ✘.
- Session lifetime: the whole thing runs inside `with Session(repair_engine)` (:1067) — on EVERY exit (including exceptions) the session is closed and the connection returned to the repair-engine pool; pool default `reset_on_return='rollback'` then rolls the txn back server-side.
- Engine-side backstop: the repair engine's PG connect_args carry `-c idle_in_transaction_session_timeout=60000` (:619-627, `REPAIR_IDLE_IN_TXN_TIMEOUT_MS = 60_000` :188) — a leaked idle-in-txn backend on THIS engine is killed by PG within 60s.

**Facts for fix shape (a) — DROP the explicit BEGIN:** SQLAlchemy autobegins on the next `session.execute(...)`; the ROLLBACKs at :1488/:1501/:1503 — not the BEGIN — provide the "does not commit" property. No savepoint, isolation-level, or multi-statement-txn semantics depend on the literal BEGIN anywhere in the function. The only observable difference today is a PG `WARNING: there is already a transaction in progress` (the session has already autobegun when the literal BEGIN executes) and the `BEGIN` string appearing as `pg_stat_activity.last_query`. No test in `tests/` asserts `text("BEGIN")` (grep: zero hits).

**Facts for fix shape (b) — MOVE inside the try:** moving the BEGIN into the inner try (DDL: into :1480's try; DML: into :1492's try) makes a BEGIN failure hit the inner except → the existing rollback at :1488/:1503 runs. Side effect: a BEGIN failure would be labelled "DRY-RUN DDL/DML FAILED (...)" (slightly misleading label; the SQL never ran).

### Site B — `/api/instances` list handler

**Route:** `GET /api/instances` → `list_instances` (`daemon/routers/instances.py:370-371`), calls `manager.list_instances(...)` **synchronously** (:476 — no `await`; sync DB work runs on the event loop — adjacent latency observation, not a leak). Service layer: `daemon/services/instance_lifecycle.py:5021 list_instances` → `instance_repository.list(...)` (:5090-5105). Repository: `SQLModelInstanceRepository.list` (`daemon/repositories/instance/repository.py:1259`).

**Session lifecycle:** created per call inside the repo:
```python
1383:             with SQLModelSession(self.engine) as db_session:      # flat path
1384:                 count_stmt = select(func.count()).select_from(Instance)
1385:                 stmt = select(Instance)
...
1405:                 total = db_session.exec(count_stmt).one()
...
1427:                 instances = list(db_session.exec(stmt))
1429:                 return self._enrich_instances(db_session, instances), total, False
...
1434:         with SQLModelSession(self.engine) as db_session:            # root+BFS path
```
The ORM `select(Instance)` is what renders the observed fingerprint `SELECT instances.instance_id, instances.project_id, ...`. **Borrowed → used → closed** inside the `with` block; `Session.__exit__` → `close()` → connection back to the pool; the pool's `reset_on_return` (default `'rollback'`; NOT overridden at `factory.py:323-338`) issues the rollback server-side.

**Verdict: CORRECTED.** The RCA's "autobegin without commit/rollback" is literally true of the code text (there is no explicit commit/rollback), but there is **no structural leak** of class (a) (never returned), (b) (returned without reset — reset IS active), or (c) (aborted state reused — a reset-rolled-back connection is clean). Every read here is a plain SELECT; nothing errors mid-txn in the happy path. The only leak-shaped scenario is a thread hung INSIDE the with-block — not a code defect visible at HEAD.

### Site C — the real `SELECT count(*) FROM job_queue_items`

**Readiness probe: REFUTED.** `_QUEUE_MAX_AGE_SQL_POSTGRES` (`readiness.py:201-205`) and `_INFLIGHT_COUNT_SQL_POSTGRES` (:228-237) both read `FROM task WHERE status = :status_running` — no `job_queue_items` anywhere in the probe (matches the soak lane's premise-10 reconciliation, re-verified at HEAD).

**Fingerprint owner (CORRECTED):** the only non-ORM count whose rendered text starts `SELECT count(*) FROM job_queue_items` is:
```python
185: _DEFER_PENDING_COUNT_SQL: Final[TextClause] = text(
186:     "SELECT count(*) FROM job_queue_items j"
187:     " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
188:     " WHERE j.deleted_at IS NULL"
189:     " AND j.admission_state = 'queued'"
190:     " AND q.queue_type = 'defer'"
191: )
```
(`daemon/services/defer_block_resolver.py:185-191`). Executed at:
- `DeferBlockResolver.defer_pending_count()` :274-276 — `with self._job_repo.engine.connect() as conn: conn.execute(...).scalar_one()` — context-managed.
- `DeferBlockResolver.resolve()` :306-321 — `with self._job_repo.engine.connect()` (:306), the count at :321 inside the same block — context-managed.

Consumers: the defer-blocked preflight surface (`GET /api/jobs/cleanup/preflight` per the docstring :248-252).

**Second non-ORM count over job_queue_items** (different prefix — `SELECT instance_id, COUNT(*) ... GROUP BY`, so it is NOT the incident fingerprint): the husk-evidence probe `daemon/tools/instance_read.py:1408-1416`, again `with engine.connect()` (:1412) — context-managed.

**Full enumeration result:** no OTHER raw `count(*)` over `job_queue_items` exists in `daemon/` (grep across the tree; manager.py's `job_queue_items` SQL is DDL/UPDATE/EXISTS shapes; `message_queue` stats read their own table). ORM counts would render `count(job_queue_items.id)`, not `count(*)` (JobItem `__tablename__ = "job_queue_items"`, `daemon/repositories/job_queue/models.py:272`).

**Verdict: the real site exists but both execution paths are context-managed → no structural leak.** Note `engine.connect()` context-exit = connection close = pool reset rollback, same guarantee as Site B.

### Site D — readiness probes (fix F4)

**Probes (4 components):** `database` (SELECT 1, manager engine), `queue_freshness` (max-age + advisory inflight count over `task`, manager engine), `services` (in-memory, no DB), `checkpoint_saver` (sentinel aput_writes-once + aget_tuple through the langgraph saver's psycopg pool). Wiring: `daemon/api.py:3778-3796` (`make_db_probe(manager.engine)`, `make_queue_probe(...)`, `make_checkpoint_saver_probe(manager.checkpointer)`).

**Constants at HEAD:** `DB_PROBE_TIMEOUT_S = 0.5` (:113), `CHECKPOINT_SAVER_PROBE_TIMEOUT_S = 1.0` (:122), `QUEUE_PROBE_TIMEOUT_S = 2.0` (:127). Refresh cadence: `readiness_refresh_interval_seconds` default **10s** (`daemon/config.py`, main-checkout :2617-2618; value unchanged in the worktree).

**Timeout interaction:** `_guarded` runs `asyncio.wait_for(asyncio.to_thread(probe), timeout=...)`. On timeout the asyncio side gives up but **the worker thread keeps running** — the module's own docstring says exactly this (:107-112: *"A timed-out to_thread leaves the underlying thread running until the driver's own connect/execute timeout releases it — bounded leakage, acceptable at the 10s refresh cadence"*).

**What actually retains the connection on timeout:** for the DB and queue probes, the connection is checked out inside the abandoned thread's `with engine.connect()` (`:1477` / `:1752`). The manager engine has **NO server-side `statement_timeout` and NO `idle_in_transaction_session_timeout`** (factory connect_args = `{"options": "-c timezone=UTC"}` only, `factory.py:252`) and no client-side execute timeout — so "until the driver's own timeout releases it" is **unbounded in theory** (bounded in practice only by how long the starved pool takes to hand over a checkout: QueuePool `pool_timeout` default 30s when the pool is exhausted, then the query itself, which is sub-ms once it runs). The docstring's "bounded" claim is **the weakest link** in the prior reconciliation: bounded only under the assumption the query completes.

**Checkpoint-saver probe:** soak lane hardened it — `future.result(timeout=CHECKPOINT_SAVER_PROBE_TIMEOUT_S - 0.1)` then `future.cancel()` on timeout (GHOST-WAITER WITHDRAWAL, :1669-1684) and the saver pool's own `timeout` kwarg bounds queued acquisitions (per the :1676-1677 comment; pool sizing min 1/max 5 in `daemon/persistence.py`, now config-tunable via soak A4). The DB/queue probes have **no equivalent cancellation**.

**Fix-shape facts (no decision):** dedicated probe engine/pool (removes contention + starvation coupling); per-probe statement timeout (would need engine-level connect_args — today only the repair engine has them); connection-level timeout (`connect_timeout` is already in the URL query for connect, `factory.py:316`); cancellation handling (DB/queue probes cannot cancel a sync `conn.execute`; the saver probe's `future.cancel()` is the only precedent in-module). The leak-predicate machinery (P1 advisory SQLAlchemy pool sample :525-549; P2 oracle psycopg pool sample :552-582; `_evaluate_leak` :784+) is already in place at HEAD to OBSERVE pool exhaustion.

### Site E — `mint_boot_epoch_if_none` IntegrityError (fix F6)

**Call path at HEAD:** `mint_boot_epoch_if_none` (`mission_record_service.py:679-692`) → `_mint_if_no_open_epoch` (:641-677: rung-1 open-epoch probe :656 → mint :664) → `_mint_epoch_locked` (:499: `epoch_seq = 1 + count_epochs_for_anchor` :528; INSERT via `self._repo.insert_mission(record, session=session)` :543).

**IntegrityError handler** (:544-567): with a caller session — re-raise (:554; the wrapper's `begin_nested` savepoint rolls the savepoint back, wrapper catches at :1379). Without a session (the boot shape) — re-resolve probe ONLY when `founding_message_id is not None` (:561-566); the boot epoch has `founding_message_id=NULL` → **`raise`** (:567). So the boot mint deterministically raises IntegrityError out to the fail-open wrapper (`mint_boot_epoch_if_none_fail_open`, def above :1345, catch+log at :1379-1384).

**Session/transaction shape (the leak question):** both boot-lane callers pass **NO session** — `auto_continue_boot_pass.py:1154-1159` (orphan sweep) and the same shape in the auto-continue pass loop (main-checkout :527-540 shows the identical block; worktree loop verified at :505-569 with the same structure). `insert_mission` with `session=None` (`repository.py:498-514`):
```python
510:         with Session(self._engine) as own:
511:             own.add(record)
512:             own.commit()          # ← IntegrityError raises here
513:             own.refresh(record)
514:             return record
```
On IntegrityError the exception exits the `with` block → `Session.close()` → connection back to the pool → `reset_on_return='rollback'` cleans the aborted txn. **No connection is retained; no leak.** The rung-1 probe (:85-93) likewise uses `with Session(self._engine)`. Verdict: **REFUTED as a leak mechanism** at HEAD.

**Fix shape (a) — idempotent mint facts:** current INSERT is ORM (`own.add(record)`; no raw SQL). The constraint is the **partial** unique index `ux_mission_records_anchor_open ON mission_records(anchor_instance_id) WHERE status = 'open'` — defined identically in 3 places: `models.py:307-313` (`__table_args__`), the migration `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69`, and the `manager.py::_ensure_postgres_columns` mirror (per the migration header's 3-SITE PIN, :30-35). `MissionStatus.OPEN.value == "open"` (models.py :81-96) matches the predicate. PG `ON CONFLICT` against a partial index requires the index predicate to be repeated: `ON CONFLICT (anchor_instance_id) WHERE status = 'open' DO NOTHING` — applicable **without any schema change** (house rule "one .sql per new table" is not triggered — no new table/column/index; the index already exists via the 20261025_140000 migration). Caveat fact: `DO NOTHING` returns no row, so the idempotent mint still needs the existing re-resolve probe (or `DO UPDATE ... RETURNING`) to hand back the winning epoch.

**Fix shape (b) — guaranteed rollback in `finally`:** the natural home is `MissionRecordRepository.insert_mission` (:498-514) — e.g. catch IntegrityError → `own.rollback()` → raise — though the `with` block already closes the session today; this is belt-and-braces, not a leak cure.

**Unexplained (open):** why the rung-1 probe (`find_open_epoch_by_anchor`, :69-93, filters `anchor == X AND status == 'open'`) misses an OPEN epoch that the INSERT then collides with — deterministically 4×/boot (once per resumed actor). Probe and INSERT run in **separate** transactions (session=None ⇒ two different `Session`s), so the race window exists, but a deterministic 4× failure is not explained by a race alone. Needs runtime evidence.

### Site F — wedge-capture wrong table

**Confirmed sites** (`daemon/services/wedge_capture/db_capture.py`):
- `_read_executing_turns` :299-315 — `... FROM tasks t LEFT JOIN instances i ... WHERE t.status = 'running'` (:308-310). Wrong table: the running-turns table is `task` (`daemon/repositories/task/models.py:171` `__tablename__ = "task"`; `instances` join is fine).
- `_read_resume_history` :381-393 — `SELECT work_id, instance_id, auto_continued_at, started_at FROM tasks WHERE auto_continued_at IS NOT NULL` (:387-388). Wrong table.
- Module docstring :34 also says "``tasks`` rows" (prose, same error family).

**All wedge-capture DB queries enumerated:** `_read_executing_turns` (:302-315), `_read_store_census` (:351-363, `pg_class`/`pg_namespace` census over CHECKPOINT_TABLES — valid), `_read_resume_history` (:384-393, wrong), plus the metadata-row write in the back half of the module (row insert into its own wedge-capture table — separate concern). Execution order in `capture_db_snapshot` (:279-285): executing-turns → census → resume-history, ALL on ONE connection.

**Connection shape:** one-shot sync `psycopg.connect` with `connect_timeout` + `statement_timeout` options and `application_name='wedge-capture'` (:185-200); `close()` in `finally` (:289-294). No pool, no SQLAlchemy.

**Mechanism consequence (matches the incident log verbatim):** the first query on the connection fails (`relation "tasks" does not exist`) → the PG transaction is ABORTED → the subsequent census/resume reads fail with `current transaction is aborted, commands ignored until ...` even though their own SQL is valid. `connection.close()` at the end aborts server-side — no idle-in-transaction backends from this path.

`.agents/approver/wedge-capture-tracking.md` exists in the worktree — it is the **plan-approval tracking log** (3 iterations, APPROVED 2026-10-22T21:28Z), not an observation record of the Oct-27 runtime failures.

---

## 3. Pool mapping (Q7)

| Engine/pool | Definition | Settings | Consumers among the verified sites |
|---|---|---|---|
| **Manager engine** (the shared repo engine) | `daemon/repositories/factory.py:323-338 create_postgres_engine` | QueuePool `pool_size=5, max_overflow=10`, `pool_pre_ping=True`, `connect_args={"options": "-c timezone=UTC"}` (:252), URL query `connect_timeout` (:316). **No** `reset_on_return` override (default `'rollback'` active), **no** `pool_recycle`, **no** statement_timeout, **no** idle-in-txn timeout | Sites B, C, D (db+queue probes), E (mission repo), instance_read husk count; every `Session(engine)`/`engine.connect()` consumer in the repo layer |
| **Repair engine** | `daemon/tools/ens_db_tools.py:573-646 _build_repair_engine`, cached per-URL (`_REPAIR_ENGINE_CACHE`) | QueuePool 2+3 (`REPAIR_POOL_SIZE=2`, `REPAIR_POOL_MAX_OVERFLOW=3` :180-181), `pool_pre_ping=True`, `pool_recycle=3600`, `pool_timeout=10s`, PG options `-c statement_timeout=60000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=60000` (:619-627, constants :186-188) | Site A (`ens_db_repair_execute` incl. dry-run + `_write_audit_outcome`) |
| **Vacuum autocommit engine** | `ens_db_tools.py:649+` | `NullPool`, `autocommit=True`, disposed per call | vacuum/analyze branch of ens_db_repair_execute |
| **Checkpoint-saver pool** | langgraph psycopg `AsyncConnectionPool` (`.conn` on `raw_saver`), built in `daemon/persistence.py` (min 1 / max 5; soak A4 made it config-tunable and added a pool `timeout` kwarg) | `resolve_psycopg_pool` `readiness.py:500-522` | checkpoint-saver readiness probe |
| **Wedge-capture connection** | one-shot `psycopg.connect`, `application_name='wedge-capture'` | `connect_timeout` + `statement_timeout`, close in finally | Site F |

Note for the incident evidence: the wedge-capture docstring records that the daemon's own connections carry an **empty `application_name`** (:22-25), so `pg_stat_activity` alone cannot distinguish manager-engine vs repair-engine backends post-hoc.

## 4. RCA premises — CONFIRMED vs REFUTED

| # | RCA premise | Verdict | Evidence |
|---|---|---|---|
| 1 | `ens_db_tools.py:1479/:1491` explicit BEGIN outside the try ⇒ failure skips `session.rollback()` | **CONFIRMED** (precision: inside outer try :1475, outside inner try; outer except :1513 has no rollback) | Site A excerpts |
| 2 | `/api/instances` list handler leaks via "ORM read path, autobegin without commit/rollback" | **CORRECTED / NOT SUPPORTED as a leak** — context-managed session + active pool reset; no explicit commit/rollback in code, but none structurally needed | `repository.py:1383/:1434`; `factory.py:323-338` |
| 3 | readiness `queue_freshness` probe leaks a txn per tick (`SELECT count(*) FROM job_queue_items`) | **REFUTED** (re-verified at HEAD; matches soak decisions premise 10) | probe SQL `FROM task` (`readiness.py:201-210/:228-237`), context-managed connect (:1752) |
| 4 | the `SELECT count(*) FROM job_queue_items` pg fingerprint belongs to the readiness probe | **CORRECTED** — fingerprint's only render-match is `defer_block_resolver._DEFER_PENDING_COUNT_SQL` (:185-191); that path is context-managed too | Site C |
| 5 | `mint_boot_epoch_if_none`: "an exception inside a transaction without a rollback leaks that connection" | **REFUTED as a leak** — boot lane passes no session; `insert_mission` uses `with Session(self._engine)`; close → pool reset rollback. The 4×/boot UniqueViolation observable is real; its trigger unexplained | `repository.py:498-514`; `auto_continue_boot_pass.py:1154-1159` |
| 6 | wedge-capture queries use `tasks`, table is `task` | **CONFIRMED** — 2 sites (:308, :387) + docstring prose (:34) | `db_capture.py`; `task/models.py:171` |
| 7 | pool hardening needs `pool_pre_ping=True`, `reset_on_return='rollback'`, `pool_recycle` | **CORRECTED** — `pool_pre_ping=True` already set (`factory.py:328`); `reset_on_return` already default `'rollback'` (not overridden); `pool_recycle` genuinely absent on the manager engine. The REAL gap: no `statement_timeout` / `idle_in_transaction_session_timeout` on the manager engine (only the repair engine has them, :619-627) | `factory.py:252/:316/:323-338` |
| 8 | (prior art) the REAL probe issue is timeout-path connection retention | **CONFIRMED at HEAD** — and sharpened: "bounded" holds only because the trivial queries complete; there is NO server-side execute timeout on the manager engine, and the DB/queue probes have no cancellation (only the saver probe got ghost-waiter withdrawal, :1669-1684) | `readiness.py:107-112/:1463-1481/:1746-1789` |

## 5. Uncertainties / open questions

1. **Why is the boot-mint UniqueViolation deterministic (4×/boot, once per actor)?** The rung-1 probe filters `anchor AND status='open'` — the same predicate as the partial unique index. For the INSERT to collide, an OPEN epoch must exist that the probe did not see. Separate-transactions probe/insert gives a race window, but not an obvious deterministic 4×. Hypotheses needing runtime evidence: concurrent duplicate boot passes (the RCA itself notes two lanes restarted the daemon); pre-index duplicate OPEN rows in prod; a second process/epoch minting between probe and insert. Recommend the fix lane pull the actual boot-log lines + a `mission_records` census before choosing between mint-shape fixes (the two fix shapes are not equivalent if the probe itself is wrong).
2. **Which pool did the incident's 5 `idle in transaction` backends belong to?** The `BEGIN` fingerprint can only be emitted by `ens_db_tools` (grep: no other `text("BEGIN")` in `daemon/`), but the repair engine carries `idle_in_transaction_session_timeout=60s`, which should have reaped such backends within a minute — either the live v0.27.8 binary predates that connect_args option (dating not verified in this pass), or the backends were on a different engine and the fingerprint attribution is coincidental. `application_name` is empty for daemon connections, so `pg_stat_activity` cannot settle it post-hoc. Needs a fresh census with `application_name`/`backend_start`/`state_change` during a repro.
3. **The manager engine has no server-side statement/idle-in-txn timeout** — under pool starvation a probe thread's checkout+query retention is bounded only by QueuePool's 30s `pool_timeout` (default, not overridden) plus query time. The fix lane's pool-hardening work should treat the missing PG timeouts on the manager engine as the primary knob (facts gathered; no decision made).
4. **`manager.list_instances` is invoked synchronously on the event loop** (`instances.py:476`, no `await`) — adjacent latency amplifier during pool starvation, not a leak. Out of scope here; recorded for completeness.
5. **"BEGIN" `pg_stat_activity` fingerprint semantics under SQLAlchemy**: a Session-issued literal `BEGIN` executes *after* the driver's autobegin, so a backend whose last query is `BEGIN` is idle-in-transaction *inside the dry-run window* (between BEGIN and rollback/close). The static window is short (tool-call duration); whether the two observed backends match that window or an older binary needs runtime confirmation (see #2).
