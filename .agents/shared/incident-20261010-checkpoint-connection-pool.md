# Incident 2026-10-10: langgraph checkpoint connection pool dies on DB restart — all NEW instances broken

- **Date:** 2026-10-10 (first error 05:53:53 UTC, mitigated 06:06:17 UTC)
- **Severity:** S1 (every new instance failed at its first message turn; existing FE/API reads unaffected)
- **Status:** MITIGATED by daemon restart. **ROOT FIX OPEN** — this doc is the commission for it.
- **Reported by:** operator (DevOps lane); evidence collected on ensemble-vm
- **Audience:** ensemble dev team (this bug is assigned to you — check & fix)

## Summary

After the Postgres server at `10.44.0.2:5432` (k8s ClusterIP, `ensemble_prod`) restarted during a
datacenter node shutdown/restart (2 nodes), the daemon kept serving (livez OK, readyz GREEN,
frontend API DB reads fine) but **every NEW instance died on its first message** with:

```
psycopg.OperationalError: the connection is closed
```

The API/request path opens fresh connections per call (those recover), but the **langgraph
checkpoint saver holds a long-lived psycopg async connection** that was killed server-side and is
**never re-established**. Result: a silently-half-broken daemon — green readiness, dead checkpoint
layer.

## Timeline (UTC, 2026-10-10)

| Time | Event |
|------|-------|
| ~05:40–05:50 | Operator shuts down + restarts 2 DC nodes; Postgres sessions terminated server-side |
| 05:53:53 | First `psycopg.OperationalError: the connection is closed` in unit journal |
| 05:53–05:58 | Instances `f4c57b02…`, `9428b0ac…`, `e6b11e07-e5f1-4b2e-92e0-8c778be78f72` all fail their first-message task; jobs finalized status=error |
| 06:0x | readyz STILL `database:true` (probe uses a fresh connection — blind to the dead pool) |
| 06:06:17 | Mitigation: `systemctl restart ensemble-main.service` |
| 06:10–06:11 | Verification: smoke instance `6d3c5439-fc50-4546-b1ae-b08d29887238` (agent=leader) created + messaged via API → job `03763669` **completed**; checkpoint `aget` latency 3–4 ms; **0 connection-closed errors since restart** |

## Evidence — verbatim traceback (journalctl -u ensemble-main.service, 05:58:18)

```
Traceback (most recent call last):
  File "daemon/services/task_processor.py", line 590, in process
  File "daemon/services/message_processing_pipeline.py", line 471, in execute
  File "daemon/services/execution_gate.py", line 144, in run
  File "daemon/services/message_processing_pipeline.py", line 427, in _do_process
  File "daemon/manager.py", line 8425, in _process_message_with_tracking
  File "daemon/services/instance_messaging.py", line 4865, in _process_message_with_tracking
  File "daemon/services/instance_messaging.py", line 287, in _heal_poisoned_checkpoint_tail
  File "langgraph/pregel/main.py", line 1311, in aget_state
  File "langgraph/checkpoint/postgres/aio.py", line 205, in aget_tuple
  File "contextlib.py", line 214, in __aenter__
  File "langgraph/checkpoint/postgres/aio.py", line 402, in _cursor
  File "psycopg/connection_async.py", line 261, in cursor
  File "psycopg/_connection_base.py", line 532, in _check_connection_ok
psycopg.OperationalError: the connection is closed
```

Accompanying log lines (same failure, other call sites):

- `[Compaction] Failed to compact context for <inst>...: the connection is closed`
- `Worker worker-0 failed task <id>: the connection is closed`
- `MainLoopBridge: error running coroutine: the connection is closed`

## Root cause

1. The checkpoint layer (`langgraph` `AsyncPostgresSaver` over psycopg) holds a **long-lived
   async connection** created at saver init (daemon boot / pool init).
2. DC node restarts → Postgres restart → all server-side sessions terminated.
3. The daemon never detects/replaces the dead connection: the next checkpoint op raises
   `OperationalError`; the message pipeline treats it as an instance-level execution error and
   finalizes the instance terminal. No reconnect, no pool recycle, no retry.
4. **Readiness blind spot:** the `/readyz` database probe uses a fresh connection (it succeeded
   throughout) — readiness stayed GREEN while the checkpoint layer was dead.

## Reproduction (deterministic, no DC needed)

1. Start daemon; create an instance; confirm a message turn works.
2. Terminate the daemon's checkpoint connections server-side, e.g.:

   ```sql
   SELECT pg_terminate_backend(pid) FROM pg_stat_activity
   WHERE datname = 'ensemble_prod' AND application_name LIKE '%checkpoint%';
   ```

   (or simply restart Postgres)
3. Send a message to a NEW instance → observe `psycopg.OperationalError: the connection is closed`
   and the instance going terminal-error while `/readyz` stays green.

## Fix requirements (proposals — final design is the dev team's)

1. **Checkpoint connection resilience (primary):** back the saver with `psycopg_pool`'s
   `AsyncConnectionPool` (with `check=AsyncConnectionPool.check_connection`, `reconnect`,
   `recycle`), OR wrap checkpoint operations in retry-on-`OperationalError` that re-initializes
   the saver. One dead server session must never permanently break instance processing.
2. **Readiness must exercise the checkpoint path:** add a probe that performs a trivial
   checkpoint read (e.g. `aget_tuple` on a sentinel thread) so this failure class flips
   `/readyz` to degraded. Today it reports green and lies.
3. Optional hardening: TCP keepalives on the checkpoint connection(s) to surface dead peers
   faster; a structured alert/log line whenever a checkpoint reconnect occurs.

## Environment facts

- ensemble-vm, service `ensemble-main.service`, INSTALL_DIR `/home/nea/agents-ensemble`
- Live version v0.18.5 (promoted 2026-10-09 23:55:50Z); daemon boot 06:06:18Z post-mitigation
- DB: `10.44.0.2:5432/ensemble_prod` (Postgres on k8s; ClusterIP VIP survives pod moves —
  established connections do not)
- Context: on 2026-10-09 19:53–20:50 UTC the same DB had an outage window that tripped the
  v0.18.5 promote's readyz gate (ADR-005 auto-rollback). Environmental DB churn is recurring —
  the daemon must tolerate it.

## Artifacts

- Smoke-test instance (can be dismissed): `6d3c5439-fc50-4546-b1ae-b08d29887238`
- Failed instances from the incident: `e6b11e07-e5f1-4b2e-92e0-8c778be78f72`, `f4c57b02…`, `9428b0ac…`
- Full logs: `journalctl -u ensemble-main.service --since "2026-10-10 05:50" --until "2026-10-10 06:07"`
- Mitigation command: `systemctl restart ensemble-main.service` (issued 06:06:17 UTC by operator)

## Root fix addendum (2026-10-10)

Status: **pending review/test/merge** (worktree `feature/checkpoint-conn-resilience`, base `0c040e5f8`).

### Design summary

The fix is implemented in three independent layers that compose; each layer alone would close the failure class the incident produced, but composing them gives belt-and-braces coverage plus end-to-end observability.

1. **Pool-back the saver** (primary). ``daemon/persistence.py::create_postgres_checkpointer`` now constructs a ``psycopg_pool.AsyncConnectionPool`` (min_size=1, max_size=5, ``check=AsyncConnectionPool.check_connection``, ``max_lifetime=3600``, ``max_idle=600``, ``reconnect_timeout=300``) and passes it to ``AsyncPostgresSaver(conn=pool)``. The saver's ``_ainternal.get_connection`` (aio.py:374) routes every cursor-open through ``pool.connection()``, which transparently replaces dead connections on the next acquire. The pool is constructed with ``kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}`` — psycopg_pool's ``_connect`` (pool_async.py:682-708) applies these kwargs to every newly-created connection (including replacements after a dead conn is detected), so the saver sees the same per-connection settings on every fresh acquire. We chose ``kwargs=`` over ``configure=`` because the simple constructor-style settings (autocommit, prepare_threshold, row_factory) flow uniformly through ``connection_class.connect(conninfo, **kwargs)``; ``configure=`` is reserved for runtime state that needs re-application per reconnect (e.g. session GUCs) — we have none of that today.
2. **Adapter-layer retry wrapper** (narrow, belt-and-braces). ``daemon/checkpoint_adapter.py::_wrap_saver_with_connection_retry`` wraps the saver in a one-shot retry proxy that retries each method call once on SQLSTATE class 08xxx (connection-class: 08000-08999), 57P01/57P02/57P03 (admin/crash/cannot_connect_now), or ``psycopg.OperationalError: the connection is closed``. Second failure re-raises unchanged so the upstream error-classification pipeline (task_processor.py:651) sees the same exception class as before the fix. Idempotency: ``AsyncPostgresSaver`` upserts are keyed on (thread_id, checkpoint_id); a retry after a cursor-open failure lands the same row in the same state — the previous attempt did not execute the statement. ``setup()`` is NEVER routed through the wrapper (we wrap method calls only). The wrapper is the SMALLER-SURFACE option vs wrapping adapter methods: LangGraph's reads/writes flow through the saver; adapter methods operate on the SEPARATE asyncpg pool which has its own resilience and is exercised by maintenance, not the hot instance path.
3. **Topology-aware close()**. ``daemon/checkpoint_adapter.py::PostgresCheckpointerAdapter.close`` now duck-types the saver's resource via ``hasattr(conn, "get_stats") and hasattr(conn, "min_size")`` — pool-only attributes on ``AsyncConnectionPool`` that ``AsyncConnection`` does not have. Both have an awaitable ``.close()`` so the same ``await conn.close()`` works for both. The log message distinguishes "saver pool closed" from "saver connection closed" so post-mortems surface the topology.
4. **Checkpoint-saver readiness probe** (observability; incident 2026-10-10 blind spot). ``daemon/services/readiness.py::make_checkpoint_saver_probe(checkpointer)`` returns a sync probe that captures the running event loop at construction time and uses ``asyncio.run_coroutine_threadsafe`` to schedule ``AsyncConnectionPool.check()`` on that loop from the worker thread that ``asyncio.to_thread`` runs the probe in. The probe is FAIL-CLOSED — any timeout / exception / missing probe degrades ``checkpoint_saver``. SQLite / single-conn / no-pool topology → passthrough True (the spec-required pass-through; readiness never degrades for SQLite installs). The new ``ReadinessComposite.checkpoint_saver`` field defaults to True (SQLite / no-probe path) so existing tests / consumers don't break; ``compute_readiness_composite`` and ``refresh_readiness_composite`` were extended mechanically like ``database_ok``/``queue_fresh_ok``. /readyz HTTP handler stays O(1) cached (ADR-003) — the probe runs ONLY in ``_periodic_readiness_refresh_loop``. Failure = DEGRADE (503 + reason string), never restart (ADR-005). Wired into ``daemon/api.py::_periodic_readiness_refresh_loop`` via ``make_checkpoint_saver_probe(manager.checkpointer)``.

### File list (worktree changes)

- `daemon/persistence.py` — `create_postgres_checkpointer` pool-backs the connection; lazy-import `psycopg_pool`.
- `daemon/checkpoint_adapter.py` — adds `_wrap_saver_with_connection_retry`, `_is_retryable_connection_error`, wires wrapper into `PostgresCheckpointerAdapter.__init__`, topology-aware `close()`.
- `daemon/services/readiness.py` — adds `make_checkpoint_saver_probe`, `CHECKPOINT_SAVER_PROBE_TIMEOUT_S`, extends `ReadinessComposite` (`checkpoint_saver` field), `compute_readiness_composite` (`checkpoint_saver_ok` kwarg), `refresh_readiness_composite` (`checkpoint_saver_probe` kwarg), `apply_forced_degradation` (preserve new field).
- `daemon/api.py` — wires `make_checkpoint_saver_probe` into the readiness refresh, updates the readyz handler's no-composite default.
- `tests/test_persistence.py` — extends pool-wiring mocks; new tests for pool construction (kwargs, check=, open awaited, setup awaited, adapter constructor args).
- `tests/test_checkpoint_adapter_resilience.py` — new file: retry-wrapper tests, topology-aware close tests.
- `tests/test_readiness_checkpoint_saver.py` — new file: probe factory tests, composite tests, refresh integration.
- `tests/test_health_probes.py` — extends the readyz-200 test for the new `checkpoint_saver` component.
- `tests/helpers/checkpoint_prune_pg.py` — adds a `pool=True` flag (default off; existing consumers unaffected) that constructs the production-shaped pool-backed saver harness.
- `.agents/shared/incident-20261010-checkpoint-connection-pool.md` — this addendum.

### Branch / status

- **Branch:** `feature/checkpoint-conn-resilience`
- **Base:** `0c040e5f8` (v0.18.5 tip)
- **Worktree path:** `/home/nea/ensemble-src-wt-checkpoint-conn-resilience`
- **Status:** pending review/test/merge. The fix builds and the targeted tests (see commit messages) pass; full / regression suites are the tester's job.
- **Constraints honored:** no pyproject/uv.lock changes (pinned: langgraph 1.0.9, langgraph-checkpoint-postgres 3.1.0, psycopg 3.3.4, psycopg-pool 3.3.1); SQLite saver path untouched; the incident doc was copied into the worktree (no edits to the main checkout).
- **Optional items (per round-1 sketch):** TCP keepalives kwargs on pool conns and a structured INFO log on pool reconnect events — **SKIPPED** for complexity-vs-benefit (the pool's own reconnect machinery + the existing connection-level error classification already give the signal we need; adding keepalives adds a knob and a moving piece without changing the failure-mode outcome).
