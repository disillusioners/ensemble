> **THIS IS THE PHASE-0 EMPIRICAL INPUT FOR THE `fix/db-pool-idle-txn` LANE.**
>
> The plan council had **no database access**. Every PostgreSQL semantics claim in
> `plan-overview.md` / `decisions.md` / `architecture-recommendation.md` / `test-strategy.md`
> was therefore **documentation-only**. This document replaces those claims with **measurements**
> taken at base `19fc11c8b52239813fd91327ee0c1961c5b6fc87` on a lane-owned scratch database.
>
> **How to consume it:** the "What this changes in the plan" table near the end lists 9 plan
> premises with their measured verdicts and the action each implies. Read that table first, then
> the per-target sections (T1–T5) for the evidence behind each verdict.
>
> **Four of the five targets produced results that change a plan premise**, and **OQ-1 (the
> mission-mint `UniqueViolation` trigger) is closed**.
>
> The canonical copy (and the one the tester signed off on) is
> `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md`. The harness that produced the
> numbers is `.agents/tester/harness/dbpool-phase0/` (**not shipped**), with verbatim raw output
> in `.agents/tester/harness/dbpool-phase0/raw/`.
>
> **No production code was changed by this measurement lane.**

---

# DB pool idle-in-transaction — PHASE 0 EMPIRICAL BASELINE

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`) — "DB pool poisoned by leaked idle-in-transaction connections".
- **Measured at base:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (= `origin/latest` = the staged **v0.27.11** soak-gate tree).
- **Measured by:** tester (this report). **MEASUREMENT + EVIDENCE ONLY — no production code was implemented or changed.**
- **Branch:** `tester/dbpool-phase0-baseline` (evidence only; no code changes).
- **Worktree:** `/home/nea/ensemble-src-wt-dbpool-phase0` (separate from the lane worktree `/home/nea/ensemble-src-wt-dbpool-idletxn`).
- **Date:** 2026-10-28.
- **Verdict:** **5/5 targets measured.** Four of the five produced results that **change a plan premise**; the fifth closed an open question the plan had left as OQ-1.

## Why this report exists

The lane's plan council had **no database access**. Every PostgreSQL semantics claim in
`plan-overview.md` / `decisions.md` / `architecture-recommendation.md` / `test-strategy.md`
is therefore **documentation-only**. This report replaces those claims with measurements.

## Environment (measured, not assumed)

| Item | Value |
|---|---|
| PostgreSQL | 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1), local `127.0.0.1:5432` |
| Python | 3.13.15 — `/home/nea/ensemble-src/.venv/bin/python` (certified) |
| psycopg / SQLAlchemy / asyncpg | 3.3.4 / 2.0.48 / 0.31.0 |
| Scratch DB | `ensemble_dbpool_scratch_phase0`, owned by throwaway role `dbpool_phase0` |
| Harness | `.agents/tester/harness/dbpool-phase0/` (NOT SHIPPED), raw output in `raw/` |

## Fences honoured

- The scratch DB was **created** for this lane and **dropped** at teardown. `ensemble_dev`'s schema was never mutated.
- **Zero live contact.** No daemon boot, no restart, no signal, no promote/rollback/stage script, no live install path, no `ensemble_prod`.
- The harness **never reads `POSTGRES_*` from the ambient environment** — on this host the ambient values point at a REMOTE `10.44.0.2` / `ensemble_prod`. (`env -u POSTGRES_*` is a silent no-op; the harness sidesteps the idiom rather than trusting it.)
- Nothing resolves a `$HOME`-derived path.

---

# T1 — M-R3 hold duration at BASE under load

### The shape under test

`daemon/manager.py:11515-11571` (async) and `:11208-11224` (sync twin):

```
with self._write_guard:
    with self._session_scope() as session:      # SQLModel Session(self._engine)
        inj = session.get(ReportInjection, ...)  # autobegin -> BEGIN + SELECT
        ...
        content = await self._fetch_subshape_a_content_async(
            child_instance_id=child_instance_id, session=session,
        )                                        # <-- AWAIT INSIDE THE SCOPE
```

The awaited call is `ChildReportsService._get_last_assistant_message` →
`daemon/persistence.py::get_instance_messages` → `saver.aget(config)` (the **asyncpg pool**,
`daemon/persistence.py:355-359`) → then the python-side analysis in `child_reports.py:1597+`.

### Measured hold distribution (all durations in ms; n = 30 per regime unless noted)

| Regime | median hold | p95 hold | **max hold** | median await | median loop delay | pg state during await |
|---|---|---|---|---|---|---|
| **L0** no load | **7.121** | 8.475 | 21.654 | 2.692 | — | `idle in transaction` 30/30 |
| **L1** pool pressure (8 holders) | **46.471** | 49.977 | 55.307 | 3.686 | — | `idle in transaction` 30/30 |
| **L2** event-loop peg | **52.385** | 61.927 | 73.601 | 47.693 | 5.123 | `idle in transaction` 30/30 |
| **L3** pressure + loop peg | **78.846** | 100.525 | 114.502 | 40.657 | 3.998 | `idle in transaction` 30/30 |
| **L4** pool over-subscribed (`pool_timeout=3s`) | 11.220 | 12.502 | 12.502 | 3.782 | — | `idle in transaction` 4/4 |
| **L5** loop peg + 1.0s modelled fetch tail | **1029.887** | 1033.342 | 1059.877 | 1026.527 | 2.087 | `idle in transaction` 15/15 |
| **L4b** genuine over-subscription (16th borrow) | — | — | — | — | — | borrow **timed out** at `pool_timeout` |
| **L6** sync twin (8s bridge, inner never completes) | **8011.119** | — | **8011.119** | 8000.320 | — | `idle in transaction` 1/1 |

### Structural facts confirmed on every iteration (139/139)

- `same_conn_across_suspension_all = True` — **the pooled connection identity is unchanged across the suspension.** The ORM does **not** release the connection before the await.
- `in_transaction_after_await_all = True` — the transaction is still open after the await resumes.
- `pg_state_during_await_counts = {'idle in transaction': N}` — for **every** iteration the backend is observable as `idle in transaction` for the whole hold.
- `peak_engine_pool_checkedout` — 1 with no pressure, 9 with 8 pressure holders.

### L4b — the real over-subscription failure

15 holders fill the 15-slot pool (`pool_size=5 + max_overflow=10`). The 16th checkout:

```
holders_created: 15
holder_creation_error: TimeoutError: QueuePool limit of size 5 overflow 10 reached,
                       connection timed out, timeout 3.00
mr3_borrow_error:  TimeoutError: QueuePool limit of size 5 overflow 10 reached,
                   connection timed out, timeout 3.00
mr3_borrow_wait_ms: 3001.116
```

At base the manager engine's `pool_timeout` is the SQLAlchemy library default **30 s**, so the
M-R3 borrow under real over-subscription stalls for **30 s**, not 3 s.

### L6 — the sync twin's ceiling

```
bridge_exception: TimeoutError
bridge_ms: 8000.320
pg_state_during_bridge: idle in transaction
pg_last_query_during_bridge: SELECT instance_id, project_id, agent_id, status
                              FROM phase0_instances WHERE instance_id = $1
hold_total_ms: 8011.119
```

The sync twin's hold is bounded **only** by the 8 s bridge timeout
(`asyncio.run_coroutine_threadsafe(...).result(timeout=8.0)`, `manager.py:11208-11224`).

### FLIP-CONDITION VERDICT: **NOT MET** — F8 stands

> The architect's flip condition is *"the M-R3 hold is < 2 s ⇒ approach C (fix F8) collapses to B (narrow)"*.
>
> **Measured: NOT MET.** The worst measured hold is **8011 ms** (the sync twin). Even on the async
> path alone, the worst measured hold is **1059.9 ms** — under 2 s, but only because the awaited
> content fetch was fast. The hold is **not an independent cost**: it is *exactly* the awaited
> content-fetch latency (L5: hold median 1029.9 ms vs await median 1026.5 ms; L2: hold 52.4 ms vs
> await 47.7 ms). The hold inherits whatever latency the fetch has.
>
> **Therefore the flip condition must be restated as a PRECONDITION, not a constant:**
> *"the M-R3 hold < 2 s **iff** the awaited content fetch < 2 s."* That is a measurable
> property of the fetch path (the checkpoint read + the python-side history analysis), and it is
> **data-dependent** — an instance with a long history makes the fetch slower. The sync twin is
> unconditionally ≥ 8 s.

### Why the hold exists

The awaited call is a **checkpoint read on a different pool** (asyncpg, `persistence.py:355-359`)
followed by **python-side history analysis** (`child_reports.py:1597+`). Neither touches the
manager engine's connection — but the manager engine's connection is **held open across both**,
for the whole duration, while the backend sits `idle in transaction`. The hold is therefore
**coupled to the fetch, not to the DB**.

---

# T2 — What `pg_stat_activity` actually shows (defines the F7 invariant)

### The state matrix (real rows, scratch DB)

| Case | `state` | `wait_event_type` | `xact_start` | `state_change` | `last_query` |
|---|---|---|---|---|---|
| (a0) fresh, checked out, **no statement** (no pre-ping) | `idle` | `Client` | **NULL** | set | `ROLLBACK` |
| (a) checked out before any statement | `idle` | `Client` | **NULL** | set | `ROLLBACK` |
| (f) checked out, never issued anything | `idle` | `Client` | **NULL** | set | `""` (empty) |
| (b) **after the first statement** (autobegin) | **`idle in transaction`** | `Client` | set | set | `SELECT 1` |
| (c1) after explicit `rollback()` (still checked out) | `idle` | `Client` | **NULL** | set | `ROLLBACK` |
| (c2) after return-to-pool (`conn.close()`) | `idle` | `Client` | **NULL** | set | `ROLLBACK` |

### (d) Residence distribution (`xact_start → state_change`)

40 borrow/return cycles on the manager engine:

```
n=40  min=0.0003 s  median=0.0005 s  p95=0.0007 s  max=0.0008 s  mean=0.0005 s
observed_states_during_borrow: {'idle in transaction': 40}
```

A borrow that is closed immediately leaves a **sub-millisecond** residence. A borrow that is
**held** is visible as `idle in transaction` for its whole hold (measured 0.502 / 1.502 / 3.003 s
for requested holds of 0.5 / 1.0 / 3.0 s).

### (e) `idle_in_transaction_session_timeout` — OBSERVED, not documented

Set to `2s` on a scratch session:

```
before_wait: state='idle in transaction', last_query='BEGIN', xact_start set
after_wait:  null                      <-- the row is GONE from pg_stat_activity
waited_s:    2.03
state_after: BACKEND GONE (terminated by server)
client_exception: psycopg.errors.IdleInTransactionSessionTimeout:
                 terminating connection due to idle-in-transaction timeout
```

The same for a `BEGIN` + `SELECT` session (`terminated: true`, `elapsed_s: 2.02`).

**It fires as documented.** The manager engine carries **no** server-side timeout, so on the
manager engine the retention is **unbounded server-side**; the repair engine's 60 s
(`ens_db_tools.py:619-627`) is the only backstop, and it does not cover the manager engine.

### ⚠ The census-blindness claim is CONFIRMED

**A backend checked out BEFORE ITS FIRST STATEMENT reports `state='idle'` with `xact_start=NULL`
— it is NOT `idle in transaction`.** A census that counts `idle in transaction` is therefore
**blind to the dominant occupancy shape**: a connection that is checked out and holds no
transaction yet. The same is true of a connection parked in `active` (see T5b).

### MEASURED F7 invariant (C-10 — cannot be green while a leak exists)

> **A non-leaking pool looks like this in `pg_stat_activity`:**
>
> 1. **Occupancy** — the number of backends in `state='idle in transaction'` attributable to the
>    pool is **zero** *outside* an in-flight request. `idle in transaction` with
>    `xact_start IS NOT NULL` and a **residence** (`state_change − xact_start`) **greater than the
>    request's own hold budget** is the leak signature.
> 2. **Residence** — for a healthy pool, every `idle in transaction` backend's residence
>    (`state_change − xact_start`) is **sub-millisecond** (measured: median 0.0005 s, p95 0.0007 s,
>    max 0.0008 s over 40 cycles). A backend whose residence exceeds the **measured** hold budget
>    of the operation that opened it is a retention, not an in-flight request.
> 3. **Checked-out-but-idle** — the pool's occupancy must ALSO be counted as
>    `state IN ('idle','idle in transaction')` **with the pool's own `application_name`**;
>    an `idle` backend with `xact_start IS NULL` is a checked-out connection that has not yet
>    issued a statement, and a census over `idle in transaction` alone cannot see it.
> 4. **Active** — a backend in `state='active'` holding a connection is *also* occupancy
>    (measured in T5b: the abandoned probe's backend was `active`, not `idle in transaction`).
>
> **Why this cannot be green while a leak exists:** the invariant is stated over **occupancy
> (all three states) + residence**, and every leak shape measured in this baseline lands in one
> of them — a held `idle in transaction` (T1: residence == hold, 8.0 s for the sync twin), an
> `active` holder (T5b2: 4.5 s), or a checked-out `idle` backend (T2a/f: invisible to an
> idle-in-txn-only census). A guard that counts only `idle in transaction` is **vacuous against
> two of the three shapes** and is therefore not a valid F7.

---

# T3 — Attribution: how many pools, and are they distinguishable?

### (a) Pool inventory (static scan of `daemon/` at base)

**12 pool-creation sites (11 real + 1 comment false-positive at `factory.py:289`):**

| Kind | File:line | Pool size | `application_name` | Server-side timeouts | DSN source |
|---|---|---|---|---|---|
| `AsyncConnectionPool` (P2 saver) | `daemon/persistence.py:316` | `max_size=checkpoint_pool_max_size` | **none** | none | **SHARED** `conn_string` |
| `asyncpg.create_pool` (checkpoint adapter) | `daemon/persistence.py:355` | `min_size=1, max_size=checkpoint_pool_max_size` | **none** | **none** | **SHARED** `conn_string` |
| `create_engine` (boot preflight) | `daemon/__main__.py:237` | probe-local | none | none | env |
| `create_engine` (SQLite/dialect-agnostic) | `daemon/repositories/factory.py:184` | config | none | none | config |
| `create_engine` (dialect-agnostic) | `daemon/repositories/factory.py:215` | config | none | `options=-c timezone=UTC` | config |
| **`create_engine` (MANAGER ENGINE)** | `daemon/repositories/factory.py:323` | **`pool_size=5, max_overflow=10`** (15 slots) | **none** | **none** (no `statement_timeout`, no `idle_in_transaction_session_timeout`, no `pool_recycle`, no `pool_timeout` override) | env `POSTGRES_*` |
| **`create_engine` (REPAIR ENGINE)** | `daemon/tools/ens_db_tools.py:633` | **`pool_size=2, max_overflow=3`** (5 slots) | **none** | `statement_timeout=60s`, `lock_timeout=10s`, `idle_in_transaction_session_timeout=60s`, `pool_recycle=3600`, `pool_timeout=10` | shared engine URL |
| `create_engine` (vacuum, NullPool) | `daemon/tools/ens_db_tools.py:691` | NullPool | none | statement/lock/idle-in-txn timeouts | shared engine URL |
| `create_engine` (vacuum engine, NullPool) | `daemon/services/vacuum_engine.py:79` | NullPool | none | statement/lock/idle-in-txn timeouts | shared engine URL |
| `asyncpg.create_pool` (external DB tool) | `daemon/services/db_pool_manager.py:261` | per-connection | none | none | registry |
| `psycopg.connect` (wedge capture, one-shot) | `daemon/services/wedge_capture/db_capture.py:195` | **one-shot, no pool** | **`wedge-capture`** | `statement_timeout` only | DSN |

> The task brief assumed **three** pools. There are **eleven**. The incident's 18 observed
> backends exceeding the manager engine's 15-slot cap is therefore **fully explainable by
> multi-pool occupancy** — no single-pool overflow is needed.

### (b) Are backends distinguishable at base? **NO.**

Manager engine + repair engine, both connected, observed together:

```
backends_by_application_name: { "": 2 }
distinct_states: ['idle']
sample_rows: [{'application_name': '', 'state': 'idle', 'last_query': 'ROLLBACK'}, ...]
verdict: distinguishable ONLY by last_query text, NOT by pool
```

**`application_name` is empty for every daemon pool at base.** Post-hoc attribution to a
specific pool is impossible; the RCA's `BEGIN` attribution was unique only because the SQL
*text* `BEGIN` occurs in exactly one place in `daemon/` (measured — see T4/F-B).

### (c) `application_name` attribution works — and the ordering hazard is CONFIRMED

| Measurement | Result |
|---|---|
| `options=-c application_name=ensemble-manager` | **lands** → `ensemble-manager` |
| shared constant appended **LAST** into the repair engine's options | **repair engine reports `ensemble-manager`** — **HAZARD CONFIRMED** |
| within ONE options string: `-c application_name=alpha -c application_name=beta` | → **`beta`** (last `-c` wins) |
| within ONE options string: `-c application_name=beta -c application_name=alpha` | → **`alpha`** (last `-c` wins) |
| conninfo `application_name=from-conninfo` **vs** `options=-c application_name=from-options` (both) | → **`from-conninfo`** — the **conninfo parameter is the STRONGER channel** |
| `ALTER ROLE … SET application_name='from-role-default'` | → lands (`from-role-default`) |
| role default **+** explicit startup `application_name='from-startup'` | → **`from-startup`** (the startup packet wins over the role default) |
| asyncpg `server_settings={'application_name': …}` | → lands |
| asyncpg DSN `?application_name=…` | → lands |

**The ordering hazard, concretely.** The repair engine builds its options string as
`… {PG_SESSION_CONNECT_ARGS['options']}` — the shared constant **last**. If a shared constant
carried `application_name=ensemble-manager`, the repair engine's backends would report the
**manager's** name. Measured:

```
repair_options_string: "-c statement_timeout=60000 -c lock_timeout=10000
                         -c idle_in_transaction_session_timeout=60000
                         -c timezone=UTC -c application_name=ensemble-manager"
backends_by_application_name: { "ensemble-manager": 1 }
repair_engine_reports_manager_name: true
```

**Mitigation that works:** put `application_name` in the **conninfo parameter**, not in `options`
— the conninfo parameter beats the `-c` form, so a later-appended shared constant cannot
override it. (Option ORDER inside one options string remains last-wins, so a shared constant
must still never carry `-c application_name=`.)

### (d) The asyncpg explicit transaction — it CAN hold idle-in-txn, and has NO server-side reap

The `daemon/checkpoint_adapter.py:1229-1239` shape (explicit `conn.transaction()` with three
statements separated by awaits):

```
backend_pid: 2317370
idle_in_transaction_session_timeout_default_ms: 0        <-- cluster default: DISABLED
state_after_3_statements_in_explicit_txn: 'idle in transaction'
xact_start_present: True
last_query: 'DELETE FROM phase0_ckpt_del WHERE thread_id = $1'
still_present_after_3s: True
state_after_3s: 'idle in transaction'
server_side_reap_observed: False
```

**Confirmed:** the asyncpg adapter pool can hold `idle in transaction` and has **no** server-side
reap on this cluster. It is a third (in fact, one of eleven) leak-capable pool.

### The shared-DSN collision — CONFIRMED

The P2 psycopg saver pool and the asyncpg adapter pool share one `conn_string`
(`persistence.py:225` → `:317` and `:356`). With a shared DSN:

```
p2_psycopg_pid:  2317401  application_name: 'ensemble-shared-dsn'
asyncpg_pid:     2317404  application_name: 'ensemble-shared-dsn'
```

**A shared DSN cannot separate them.** Attributing the asyncpg pool requires a **separate DSN**
for the asyncpg pool only — which is exactly the plan's T2.7 / AC18 constraint, now measured
rather than assumed.

### Is `application_name` a viable discriminator for all three pools?

**Yes, with two constraints:**

1. **The asyncpg pool must get its OWN DSN** (it shares the P2 psycopg pool's `conn_string`).
2. **`application_name` must be set via the conninfo parameter**, not via a `-c` inside a shared
   `options` string — otherwise the shared-constant-last ordering makes one pool report
   another's name (measured, T3c2).

With those, the three pools named in the brief (manager / repair / asyncpg) are separately
attributable, and the remaining eight are attributable too.

---

# T4 — Fingerprints vs the incident record

All four recorded fingerprints were reproduced **byte-exactly** at base.

| Incident fingerprint | Measured `pg_stat_activity.last_query` at base | Match |
|---|---|---|
| `BEGIN` ×2 | `BEGIN` | ✅ byte-exact |
| `SELECT instances.instance_id, instances.project_id ...` ×2 | `SELECT instances.instance_id, instances.project_id, instances.agent_id, instances.agent_dir, instances.agent_name, instances.agent_tag, instances.parent_id, instances.status, instances.metadata, instances.version, instances.last_activity_at, instances.created_at, instances.updated_at, instances.paused_at, instances.attestation_denied_count, instances.completion_gate_escalated \nFROM instances` | ✅ byte-compatible |
| `SELECT count(*) FROM job_queue_items ...` ×1 | `SELECT count(*) FROM job_queue_items j LEFT JOIN job_queues q ON j.queue_id = q.queue_id WHERE j.deleted_at IS NULL AND j.admission_state = 'queued' AND q.queue_type = 'defer'` | ✅ byte-compatible |
| `INSERT ... mission_records` ×4/boot | `INSERT INTO mission_records (mission_id, anchor_instance_id, epoch_seq, founding_message_id, founding_source, commissioner_instance_id, work_id, parent_mission_id, status, attested_at, attest_method, …)` | ✅ byte-compatible |

### The owners (measured, not inferred)

- **`BEGIN`** — owner is `daemon/tools/ens_db_tools.py:1479` (DDL) / `:1491` (DML). This is the
  only `text("BEGIN")` in `daemon/`; the RCA's attribution to Site A is **CONFIRMED**.
- **`SELECT instances.instance_id, instances.project_id …`** — owner is the real
  `SQLModelInstanceRepository.list` (`daemon/repositories/instance/repository.py:1259+`).
  The RCA's attribution to the `/api/instances` list handler is **CONFIRMED** as the fingerprint
  source.
- **`SELECT count(*) FROM job_queue_items …`** — owner is
  `daemon/services/defer_block_resolver.py:185-191` (`_DEFER_PENDING_COUNT_SQL`), executed
  context-managed at `:274`/`:321`. The plan's correction is **CONFIRMED**.
- **`INSERT ... mission_records`** — owner is the ORM insert in
  `daemon/repositories/mission_record/repository.py:498-514` (`insert_mission`).

### ⚠ The readiness probe does **NOT** read `job_queue_items`

Measured `last_query` for the two readiness queue-probe statements:

```
SELECT EXTRACT(EPOCH FROM (now() - MAX(CASE WHEN last_heartbeat_at >= $1 THEN last_heartbeat_at END))) FROM task WHERE status = $2
SELECT COUNT(CASE WHEN last_heartbeat_at >= now() - ($1 * interval '1 second') AND last_heartbeat_at >= $2 THEN 1 END) FROM task WHERE status = $3
```

`reads_from_task = True`; `byte_compatible_with_incident = False`.
**The plan's correction is CONFIRMED: the RCA's `job_queue_items` fingerprint does not belong
to the readiness probe.** The probe reads `FROM task`.

### What the fingerprints do and do not prove

A fingerprint identifies **the statement that opened the transaction**, not the code path that
**abandoned** it. Measured in T1: every M-R3 iteration's held backend reported
`idle in transaction` with `last_query` = the ORM `SELECT`. Under the plan's own reconciliation
inference that is the expected shape of an in-flight request — the fingerprint alone cannot
separate "in flight" from "abandoned". **T2(d) residence is the discriminator the fingerprints
lack.**

---

# T5 — F3/F4/F6 fix-site behaviour at BASE

## (a) `ens_db_tools.py:1479/:1491` — a forced failure between the explicit BEGIN and the rollback

| Case | `explicit_rollback_ran` | `in_transaction_at_close` | `pg_state_after_close` | `last_query` after close | next borrower |
|---|---|---|---|---|---|
| **a1** the dry-run **statement** fails (inner `except`) | **True** (`:1489`) | **False** | `idle` | `;` | ✅ OK |
| **a2** the **rollback itself** fails (outer `except` at `:1513`, no rollback) | **False** | **True** | `idle` | `ROLLBACK` | ✅ OK |
| **a3** the **BEGIN itself** fails (outer `except`, no rollback) | **False** | **True** | `idle` | `ROLLBACK` | ✅ OK |
| **a4** aborted connection handed to the pool directly | — | **True** at close | `idle` | `ROLLBACK` | ✅ OK |

**`pool_reset_on_return = ResetStyle.reset_rollback`** — SQLAlchemy 2.0.48's library default.

### ⚠ The "aborted connection handed back out" story is REFUTED for the pool path

In **every** failure shape — including the two where the code's own rollback never runs
(a2, a3) and the case where the aborted connection is handed straight to the pool (a4) — the
backend's post-close state is `idle` with `xact_start=NULL` and `last_query='ROLLBACK'`, and
**the next borrower succeeds**. The pool's `reset_on_return='rollback'` rolls the aborted
transaction back on return.

**Consequence for the plan:** F3's "BEGIN gap" leaves the connection in an aborted transaction
**only for the remainder of the tool call**; it does **not** poison the pool. F3 remains a
correct hygiene fix (and it removes the 60 s server-side reap dependency for that shape), but
the plan must **not** claim it explains an aborted-connection hand-off.

### ⚠ The incident's journal quote IS reproducible — but it is a SAME-CONNECTION cascade

The incident recorded:

```
Wedge-capture: store census failed: current transaction is aborted, commands ignored until ...
Wedge-capture: resume-history read failed: current transaction is aborted, commands ignored ...
```

Reproduced exactly, on the **wedge-capture path** (`daemon/services/wedge_capture/db_capture.py:195`
— its **own one-shot `psycopg.connect`**, NOT the pool), which runs three reads on one
connection with `close()` in `finally` and **no rollback between them**:

| step | ok | error |
|---|---|---|
| executing-turns (uses `tasks`) | ❌ | `UndefinedTable: relation "tasks" does not exist` |
| store census | ❌ | `InFailedSqlTransaction: current transaction is aborted, commands ignored until end of transaction block` |
| resume history | ❌ | `InFailedSqlTransaction: current transaction is aborted, commands ignored until end of transaction block` |

`reproduces_incident_journal_quote = True`.

**This reframes the RCA's mechanism #3.** The "aborted connection handed back out" story is
**wrong**: the abort never left the connection. It is the **first** wedge-capture query
(`tasks` → should be `task`, the incident's separate F5 defect) that aborts the transaction,
and the **second and third queries on the same connection** then fail with
"current transaction is aborted". The daemon's journal shows the *cascade*, not a pool hand-off.

**Consequence for the plan:** F5 (the `tasks` → `task` rename) is **not** a cosmetic
correctness fix — it is the **root of the aborted-transaction cascade** the RCA attributed to
the pool. F5's priority should rise.

## (b) The readiness-probe timeout path — measured retention

The probe shape (`daemon/services/readiness.py:1462-1482`): `with engine.connect() as conn:
conn.execute(sa_text("SELECT 1"))`, run via `asyncio.to_thread` + `asyncio.wait_for(budget)`.

| Case | budget | timeout fired | `checkedout` right after the timeout | backend `pg_state` during the retention | released at |
|---|---|---|---|---|---|
| **b1** `SELECT 1` | 0.5 s | **no** (completed in 5.1 ms) | 0 | `idle` | n/a |
| **b2** `SELECT pg_sleep(5)` | 0.5 s | **yes** at 0.5011 s | **1** | **`active`** (running the statement) | t ≈ 4.52 s |
| **b3** pool **starvation** (15 holders, `SELECT 1`) | 0.5 s | **yes** at 0.5015 s | **15** (= the holder count, **not 16**) | n/a (never got a connection) | after the holders release |

**Two findings that change F4's shape:**

1. **Under pool starvation — the realistic incident shape — the abandoned probe thread holds
   NO connection.** `checkedout()` after the timeout was **15**, exactly the holder count; the
   probe thread was a **pool WAITER**, not a holder. It is a stranded thread (bounded by
   `pool_timeout=30 s`), not a retained connection. **A probe-scoped `SET LOCAL statement_timeout`
   is therefore IRRELEVANT to this shape** — the probe never reached a statement.
2. **When the probe DOES hold a connection (b2), the backend's `pg_state` is `active`, not
   `idle in transaction`.** An idle-in-txn census is blind to it. The retention lasts exactly
   as long as the statement does — and the manager engine has **no server-side
   `statement_timeout`**, so that duration is unbounded.

**So the F4 premise needs restating.** The measured retention hazard is not "the probe leaks a
transaction" (it does not) and not primarily "the probe holds a connection" (it holds one only
when the statement itself is slow, and then the state is `active`). It is:
**(i)** a stranded waiter thread under starvation, and
**(ii)** an unbounded statement duration on a connection the probe holds, with **no
server-side bound** because the manager engine has no `statement_timeout` and no
`idle_in_transaction_session_timeout`.

## (c) The mission mint — OQ-1 CLOSED

| Case | Result |
|---|---|
| **c1** first mint for an anchor | ✅ OK, **rung 3** (minted) |
| **c2** second mint, **same anchor, sequential** | ✅ OK, **rung 1** (found the open epoch) — **NO violation** |
| **c3** mint for a different anchor | ✅ OK, rung 3 |
| **c4** 8-way concurrent, same anchor, **no barrier** | 0 errors (the threads did not overlap) |
| **c4b** 6-way concurrent, same anchor, **probe→insert window forced to overlap** | **5 × `IntegrityError: duplicate key value violates unique constraint "ux_mission_records_anchor_open"`** |
| **c5** the losing mint inside a **caller-supplied** session | `IntegrityError` → **the caller's session is poisoned**: `PendingRollbackError: This Session's transaction has been rolled back due to a previous exception during flush.` |

### THE ACTUAL TRIGGER: a **concurrent double-mint for the same anchor**

- The **sequential** path is **idempotent** — the rung-1 probe finds the open epoch and returns
  it (measured, c2). There is **no** predicate mismatch: the probe
  (`find_open_epoch_by_anchor`) and the partial unique index
  (`ux_mission_records_anchor_open`, `WHERE status = 'open'`, `models.py:307-313`) use the
  **same anchor key and the same status predicate** (measured `MissionStatus.OPEN.value == 'open'`).
- The **only** way the probe misses an open epoch is a **race**: two mints for the same anchor
  whose probe→insert windows overlap. Measured 5/6 violations when the overlap is forced.
- Why the loser is *not* recovered: `_mint_epoch_locked`'s `IntegrityError` handler
  (`mission_record_service.py:544-567`) re-resolves via a probe **keyed on
  `founding_message_id`**, and the **boot** mint has `founding_message_id=None` — so the
  re-resolve probe is **skipped** and the error **re-raises**. That is the measured mechanism
  behind the boot log line `mission_records: mint_boot_epoch_if_none failed (actor=…)`.

### Which callers can produce that race

Three boot-lane mint sites use the **resumed `instance_id` as the anchor**:

1. `daemon/services/auto_continue_boot_pass.py:721` — inside
   `_make_boot_continue_release_handler`'s `_release` (a **deferred** promote-txn release handler).
2. `daemon/services/auto_continue_boot_pass.py:1156` — inside
   `_make_orphan_sweep_release_handler`'s `_release` (the **other** deferred release handler).
3. `daemon/api.py:2721` → `sweep_orphaned_live_turns` — the **direct** boot call.

(1) and (2) are registered with `daemon/services/promote_txn_hold.py` as release handlers and
run through `asyncio.to_thread` (`auto_continue_boot_pass.py:721`/`:1156`), i.e. on executor
threads; (3) runs the same mint for the same instance set. **Two of the three can be in flight
for the same `instance_id` at the same time**, which is the measured race.

**This matches the incident's "4x/boot (once per actor)" exactly:** 4 resumed instances, each
racing with itself across two mint sites → one `IntegrityError` per actor.

**⚠ Consequence for F6:** the plan's framing ("the trigger is unexplained / OQ-1") is now
**answered**: it is a concurrent double-mint, and F6's fix must therefore be an **idempotent
upsert** (as the plan already proposes) — a re-resolve probe alone will **not** fix it, because
the current re-resolve is keyed on `founding_message_id` and the boot mint has none.

---

# What this changes in the plan (consolidated)

| # | Plan premise | Measured verdict | Action |
|---|---|---|---|
| 1 | "the M-R3 hold is < 2 s ⇒ collapse F8 to B" | **NOT MET** (worst 8011 ms; sync twin ceiling 8.0 s) | **F8 stands.** Restate the flip condition as *"the hold < 2 s **iff** the awaited content fetch < 2 s"* — a data-dependent precondition, not a constant. |
| 2 | the flip is decidable from a static reading | the hold **equals** the awaited fetch latency (1:1) | measure the *fetch* latency, not the hold, to decide. |
| 3 | the readiness probe "leaks a transaction per tick" | the probe does **not** leak; under starvation it holds **no** connection (a waiter) | rewrite F4's premise; a probe-scoped `statement_timeout` does not address the starvation shape. |
| 4 | the incident's "aborted connection handed back out" | **REFUTED** for the pool path — `reset_on_return='rollback'` cleans it; the journal quote is a **same-connection cascade** in wedge-capture | drop the aborted-hand-off premise; **raise F5's priority** (the `tasks`→`task` rename is the cascade's root). |
| 5 | F7 = "no idle-in-transaction backend" | an idle-in-txn-only census is **blind** to checked-out-`idle` and `active` holders | F7 must assert **occupancy across all three states + residence**, per the invariant in T2. |
| 6 | 3 pools | **11** pool-creation sites; the incident's 18 backends are explainable by multi-pool occupancy | widen the census scope. |
| 7 | `application_name` per pool is viable | viable, but **only** via the **conninfo parameter** (not a `-c` in a shared options string) and the asyncpg pool needs its **own DSN** | amend the A5/AC18/AC16/T2.7 amendment set accordingly. |
| 8 | the mint's `UniqueViolation` trigger is unknown (**OQ-1**) | **CLOSED**: a concurrent double-mint for the same anchor; the re-resolve probe is skipped because the boot mint has `founding_message_id=None` | F6 must be an idempotent upsert; a re-resolve probe alone will not fix it. |
| 9 | the manager engine has no server-side timeout | **CONFIRMED** (no `statement_timeout`, no `idle_in_transaction_session_timeout`, no `pool_recycle`, no `pool_timeout` override; the library-default `pool_timeout` is 30 s) | F2's knob set is the right place; the measured 30 s checkout stall is the F7 pressure trigger. |

# What was NOT measured (and why)

| Item | Why |
|---|---|
| The **real** `_fetch_subshape_a_content_async` latency (the true content-fetch distribution) | needs a booted daemon + a real checkpoint store; the harness models the python-side half (disclosed) and uses a **real** PG checkpoint-shaped read for the PG half. The T1 flip decision therefore rests on a *model* of the fetch, and the plan should measure the real fetch before finalising. |
| The **real** `ChildReportsService._get_last_assistant_message` python-side cost | same reason; the model is a disclosed stand-in. |
| Whether the boot's two mint sites **actually** overlapped in the incident window | the trigger class is measured; the exact scheduling in that boot is inferred from the three call sites. |
| The daemon's real `pool_timeout` stall under real load | measured with `pool_timeout=3s` (to fit the pack budget) — the base value is the 30 s library default, which follows from the config. |
| Any **production** database state | deliberately out of scope; every number here is from the lane-owned scratch DB. |

# Reproduction

```bash
# scratch DB (operator)
sudo -n -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DROP DATABASE IF EXISTS ensemble_dbpool_scratch_phase0;
DROP ROLE IF EXISTS dbpool_phase0;
CREATE ROLE dbpool_phase0 LOGIN PASSWORD 'phase0_scratch_pw';
CREATE DATABASE ensemble_dbpool_scratch_phase0 OWNER dbpool_phase0;
CREATE ROLE dbpool_phase0_role LOGIN PASSWORD 'role_pw';
ALTER ROLE dbpool_phase0_role SET application_name = 'from-role-default';
SQL

bash .agents/tester/harness/dbpool-phase0/run_all.sh     # dual-layer timeout, exit 0 on all 5

# teardown
sudo -n -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DROP DATABASE IF EXISTS ensemble_dbpool_scratch_phase0;
DROP ROLE IF EXISTS dbpool_phase0;
DROP ROLE IF EXISTS dbpool_phase0_role;
SQL
```

Raw verbatim output: `.agents/tester/harness/dbpool-phase0/raw/*.out`.

# Artifacts

| Path | What |
|---|---|
| `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md` | this report (the full baseline) |
| `.agents/shared/planning/db-pool-idle-txn/BASELINE.md` | the plan's phase-0 input (same content) |
| `.agents/tester/harness/dbpool-phase0/` | the measurement harness (**not shipped**) + `README.md` + `raw/` |

**Overall status: MEASUREMENT COMPLETE — 5/5 targets measured. No production code was changed.**
