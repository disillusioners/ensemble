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

## Round-2 review addendum (2026-10-10)

Status: **pending review/test/merge** (worktree `feature/checkpoint-conn-resilience`, base `0c040e5f8`). Round 2 was triggered by an independent leader-side REJECT whose root-cause pattern the round-1 chain missed.

### The MRO rule (root cause of K1 AND K2)

**Every attribute that is CONCRETE (non-abstract) on `BaseCheckpointSaver` resolves via normal MRO lookup, and `__getattr__` NEVER fires for it.** The pinned base (`.venv/lib/python3.13/site-packages/langgraph/checkpoint/base/__init__.py`, langgraph 1.0.9 / langgraph-checkpoint 3.1.x) declares **zero abstract methods** — all 22 public methods plus the `serde` / `config_specs` surface members are concrete, including:

- `get_next_version` (`:706-707`): raises `NotImplementedError` for `str` versions. Every existing PG thread carries str versions (`postgres/base.py:543-552`, `f"{next_v:032}.{next_h:016}"`); pregel calls `get_next_version` at 8 sites (`pregel/main.py:1538…2279`). Round 1 shipped without a forwarder → **first message to any existing PG instance post-deploy would crash in `prepare_next_tasks`** (K1, deployment blocker).
- `alist` (`:443-465`): a concrete async-generator stub raising `NotImplementedError` on first `__anext__`. Round 1's "de-interception" (f19803dc3) relied on `__getattr__` fall-through — **INERT**: MRO resolves the base stub first, so every `aget_state_history` would still break (K2).

**Cure (systematic enumeration + explicit forwarders, commit `0122db579`):** the proxy now explicitly forwards EVERY concrete public member of the base surface. The retry set stays exactly the seven hot-path async ops; `get_next_version` forwards with NO retry (synchronous ID mint; pregel's own retry covers transients); `alist` forwards as a plain `def` returning the wrapped async generator (no retry — buffering would change the memory profile; pool self-heal + pregel retry-on-NextNotFound cover iteration restarts); the sync twins forward preserving the wrapped saver's own `NotImplementedError` signal (intentionally NotImplementedError surface in this async-only daemon); `with_allowlist` forwards (the base impl shallow-copies SELF — without the forwarder MRO would clone the proxy and drop the wrapped saver's serde); `serde`/`config_specs` forward as properties (same shadowing family). Verified absent from the pinned 3.1.0 aio.py AND base: `adelete` — the round-1 interceptor was dead code and is removed.

### K-guard contract test (systematic, load-bearing)

`tests/test_checkpoint_adapter_resilience.py::TestRealLanggraphSaverProxy::test_k_guard_concrete_surface_forwards_wrapped_overrides` walks the REAL pinned `BaseCheckpointSaver` via `inspect.getmembers(...)`, filters public + concrete + non-abstract (must be ≥20 and must include `get_next_version` + `alist`), overrides EACH method on a fresh fake wrapped saver with a sentinel implementation, and asserts the proxy ROUTES to the wrapped implementation (call-through with hit tracking; NotImplementedError → loud "K1/K2 recurrence" failure). It also covers the shadowable non-method members (`serde`, `config_specs`) and asserts the zero-abstract-methods premise. **Mutation-verified**: stripping the `alist` forwarder fails both K2 and the K-guard; restored → green. A future langgraph bump that adds a base stub without a forwarder now fails this test loudly instead of silently shadowing the wrapped saver in production.

The whole real-pinned-class test family runs under `_real_langgraph_saver_base` — the K3 binding gate: `evict_langgraph_mocks` → `importlib.reload(daemon.checkpoint_adapter)` (rebinds the module-global `BaseCheckpointSaver` to the real class; eviction alone cannot, the module was imported under the conftest mock) → restore + reload-back. Round 1's suite certified INVERTED semantics: the conftest mock has no concrete surface and no `ensure_valid_checkpointer`, and the two `importorskip("langgraph.types")` tests silently skipped under the mocked empty namespace package. Supporting helper evolution (mock-only eviction/restore in `tests/helpers/checkpoint_prune_pg.py`): real modules (`__file__` present) are never evicted and never re-poisoned with the mock — repeated cycles otherwise fork module identity (a second `langgraph.checkpoint.base` whose `BaseCheckpointSaver` fails `isinstance` against the first copy's; observed live during this round). The repo's ONLY `langgraph.checkpoint.base` importer is `daemon/checkpoint_adapter.py`, so once-real-stays-real is safe for every other consumer.

### W1 sentinel probe (redesign of the round-1 readiness probe)

`pool.check()` discards + replaces dead connections and NEVER raises — the round-1 probe could PREVENT the outage class but could not DETECT it (incident doc requirement #2). Replaced with a sentinel checkpoint read through the real hot path (`raw_saver` retry proxy → `AsyncPostgresSaver`):

- **Write-once init**: first tick of the process plants the sentinel via `aput_writes` with a fixed `(thread_id, checkpoint_ns, checkpoint_id, task_id)` key — idempotent upsert (chosen over `aput` because it needs no synthetic checkpoint payload and is re-run-safe); a failed init does NOT latch the flag. The write lands ONLY in `checkpoint_writes` — no `checkpoints` row — so instance lifecycle / pause-resume / prune tools never see it; the PG adapter's `list_thread_ids` additionally excludes the sentinel thread (defense-in-depth for maintenance Operation A).
- **Read-only tick**: `aget_tuple` on the fixed sentinel thread (`daemon/constants.py::CHECKPOINT_SENTINEL_THREAD_ID` = `"__ensemble_readiness_probe__"`). `None` (row absent) is HEALTHY — the probe detects a path that cannot SERVE READS, not row presence. O(1) per tick.
- Failure (exception OR timeout) → composite degrades with a reason string naming the sentinel read; 1s `_guarded` budget, degrade-not-restart (ADR-005), O(1) cached `/readyz` (ADR-003) unchanged. SQLite / single-conn / no-pool passthrough True unchanged.
- The probe's internal warning log was removed — `_guarded` is the single log site (double-log fix).

### W2/W3/W4

- **W2**: `daemon/_redact.py::redact_exc_str` — masks URI netlocs (credentials + host), libpq verbose TCP/unix-socket shapes, keyword-value DSN fields, bare IPv4:port before truncation; routed into the checkpoint retry warning and the readiness probe-failure warning (which previously logged the FULL untruncated `str(exc)`). Documented limitation: bare `hostname:port` pairs are not masked (false-positive risk). Checked existing conventions first: `daemon/util/log_redaction_filter.py` (KMS plaintext handler filter) and `mcp_servers.redact_secrets` (config dict) — neither covers exception strings.
- **W3**: the proxy class docstring rewritten to the MRO rule: (a) base-concrete attrs resolve via MRO, `__getattr__` never fires; (b) sync twins intentionally NotImplementedError (async-only daemon); (c) explicit-forwarders list MUST stay in lockstep with base updates; (d) the K-guard enforces it.
- **W4**: conftest `_REQUIRE_REAL_LANGGRAPH` orphan doc text replaced with the actual mechanism (mock-eviction + reload binding gate). Optional items: `adelete` dead interceptor removed with verification note; ImportError fallback comment refined (covers only older 1.0.x-era pins / packaging accidents — the current pin ships the submodule); `raw_saver` property docstring aligned with the MRO-rule framing; pool min/max-size recorded as a FUTURE knob in `daemon/persistence.py` (no behavior change).

### Files touched in this round

- `daemon/checkpoint_adapter.py` — explicit forwarders for every concrete base member (K1/K2 cure), `adelete` removal, W3 docstring, sentinel exclusion in PG `list_thread_ids`, W2 redaction at the retry-warning site, ImportError/raw_saver doc refinements.
- `daemon/services/readiness.py` — W1 sentinel probe (factory rewrite + reason strings + double-log removal).
- `daemon/constants.py` — `CHECKPOINT_SENTINEL_*` namespace.
- `daemon/_redact.py` — NEW: exception-string redaction helper (W2).
- `daemon/persistence.py` — pool-sizing future-knob comment only (no behavior change).
- `tests/test_checkpoint_adapter_resilience.py` — K-guard + K1/K2 regression tests + real-langgraph binding gate (`_real_langgraph_saver_base`), gate tests converted off the mock, redaction tests.
- `tests/test_readiness_checkpoint_saver.py` — sentinel-probe contract tests (write-once/read-only/failure/timeout/reason strings).
- `tests/helpers/checkpoint_prune_pg.py` — mock-only evict/restore (identity-fork fix).
- `tests/conftest.py` — W4 doc drift fix.
- `.agents/shared/incident-20261010-checkpoint-connection-pool.md` — this addendum.

### Branch / status

- **Branch:** `feature/checkpoint-conn-resilience` — base `0c040e5f8`, round-2 commits: `0122db579` (K1/K2 forwarders + W3), `0f376f63d` (K-guard + K3), `cea8a4c73` (W1 sentinel probe), `97862499e` (W2 redaction), docs commit (W4 + optional items + this addendum).
- **Status: pending review/test/merge** — still awaiting the leader's clean declaration; targeted suites pass (resilience 43, readiness+health 76, persistence unchanged-green); regression suites, real-PG recovery scenarios, and the K-guard in a real-langgraph integration pass remain the tester's lane.
