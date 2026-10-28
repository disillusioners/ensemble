# db-pool-idle-txn — Decisions

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`) · **Base:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (= `origin/latest` = the staged v0.27.11 tree).
- **All `file:line` citations are at this commit.** The RCA's line numbers are stale (coupling **C4**) and are never quoted from the incident snapshot.
- **This file is the single canonical home for the decision records.** The phase files' "Decisions" tables are **pointers** to the IDs below — no ID is minted twice, and no ID here is renumbered.
- **Sibling artifacts referenced by filename only** (authored in parallel, not read): `test-strategy.md`, `wi-78967dba-verdict.md`, `audit-method.md`.
- **Binding couplings:** C1 (the leak detector and its refusal path are fail-closed by design), C2 (no conditional weakening near rollback; never-green stays unconditional at cap-reach), C3 (false-green discipline: gates run in a form the runner executes + assert the expected collected count), C4 (stale line numbers).
- **Safety-precedence rule applied throughout:** if a requirement conflicts with a safety property, preserve the safety property, narrow the feature, and say so.

---

## 1. Index

| ID | Area | Decision (one line) |
|----|------|--------------------|
| **D-01** | F3 | **DROP** the explicit `BEGIN` (rather than move it inside the inner `try`) |
| **D-02** | F3 | The rollback moves into a **`finally`** so every exit path rolls back |
| **D-03** | F3 | The agent-visible preview strings / docstrings are updated **in the same change** (audit-contract wording left alone) |
| **D-04** | F4 | Probe-scoped **`SET LOCAL statement_timeout`** (house idiom) is the primary vehicle; dedicated probe pool **rejected on C1** |
| **D-05** | F4 | The server bound is **2× the client budget**, so the client classifies the failure first |
| **D-06** | F4 | **PG-only** guard; the SQLite path is untouched |
| **D-07** | F4 | An **explicit end-of-transaction** precedes the probe's connection return |
| **D-08** | F6 | **Idempotent mint** via a dialect upsert (`ON CONFLICT … DO NOTHING`) + anchor-scoped re-resolve; **no schema change** |
| **D-09** | F6 | The boot path's re-resolve is keyed on the **index's own predicate** (anchor + `status='open'`), not on `founding_message_id` |
| **D-10** | F6 | F6 is **scoped to the own-session (boot) path**; the caller-session path is untouched |
| **D-11** | F1 | The audit record is a **new artifact in this planning dir**, not an edit to the read-only RCA |
| **D-12** | phasing | **Phase 1 lands no pool knob** — the knob table is one table with one pin |
| **D-13** | F2 | F2 scope = the **manager engine** + the repair engine's explicit reset pin; every other engine is untouched |
| **D-14** | F2 | Exact manager-engine knob values (5/10/pre_ping/reset=rollback/recycle=3600/timeout=30; no LIFO) |
| **D-15** | F2 | `pool_timeout` **review outcome: keep 30s**, pinned explicitly; residual disclosed |
| **D-16** | F2 | Engine-level server-side timeouts: `statement_timeout=30000`, `idle_in_transaction_session_timeout=60000` |
| **D-17** | F2 | A **NEW** connect-args constant; `PG_SESSION_CONNECT_ARGS` is **not** extended (it would silently retune the repair engine) |
| **D-18** | F2 | Knob **values** live in `daemon/constants.py`; the options **assembly** stays in `factory.py` |
| **D-19** | F2 | The repair engine changes by **exactly one** knob (`pool_reset_on_return`); its options string stays byte-identical |
| **D-20** | F5 | F5 is a **correctness** fix (no pool involved), scoped to the **table name** only |
| **D-21** | F2/F5 | The **honesty blocks** are refreshed (they currently name this lane as the owner of an unfixed defect) |
| **D-22** | F2 | The latent config-driven PG branch is **not** hardened |
| **D-23** | F7 | The guard rides the **integration** placement (scratch DB, `-m integration`) |
| **D-24** | F7 | Attribution is by **scratch-database isolation**, not `application_name` |
| **D-25** | F7 | A **default-collected static leg** (G-A) guards the guard's own reachability |
| **D-26** | C3 | Every gate asserts its **expected collected count** before "0 failed" is read as a pass |
| **D-27** | C3 | Every PG gate uses the **per-variable** env scrub + fenced HOME; `env -u POSTGRES_*` is forbidden |
| **D-28** | phasing | **Phasing rationale** (why F3/F4/F6/F1 first, then F2/F5, then F7) |

---

## 2. F3 — the repair-tool transaction-abort gap (Site A)

### D-01 — DROP the explicit `BEGIN` (not "move it inside the inner try")

**The defect (verified at HEAD).** `daemon/tools/ens_db_tools.py`:

```
1475:  try:                                   # OUTER try
1479:      session.execute(text("BEGIN"))     # DDL branch — OUTSIDE the inner try
1480:      try:
1481:          session.execute(text(sql))
1483:      except Exception as exc:           # no re-raise
1488:      session.rollback()                 # ← the rollback that owns the branch
1491:      session.execute(text("BEGIN"))     # DML branch — OUTSIDE the inner try
1492:      try:
1501:          session.rollback()
1503:          session.rollback()             # except branch
1513:  except Exception as exc:               # OUTER except — NO rollback
```

So the BEGIN sits **inside the outer try but outside the inner try** that owns the rollback. A failure at the BEGIN (or at the DDL branch's own `rollback()`) reaches the outer `except:` at :1513 with **no rollback**.

**Option (a) — DROP the explicit BEGIN.** SQLAlchemy autobegins on the next `session.execute(...)`; the ROLLBACKs at :1488/:1501/:1503 — not the BEGIN — carry the "does not commit" property. No savepoint, isolation-level, or multi-statement-txn semantics anywhere in the function depend on the literal BEGIN. The only observable differences today are a PG `WARNING: there is already a transaction in progress` (the session has already autobegun when the literal BEGIN executes) and the `BEGIN` string appearing in `pg_stat_activity.last_query` (research-leak-sites.md:59). **No test in `tests/` asserts `text("BEGIN")`** (grep: zero hits).

**Option (b) — MOVE the BEGIN inside the inner try.** A BEGIN failure would then hit the inner `except` and the existing rollback would run. Side effect: the failure is labelled `"DRY-RUN DDL FAILED (…)"` although the SQL never ran (slightly misleading label).

**Option (c) — leave it, rely on the backstop.** The repair engine already carries `idle_in_transaction_session_timeout=60000` (`ens_db_tools.py:619-627`, `REPAIR_IDLE_IN_TXN_TIMEOUT_MS` at :188), so a leaked BEGIN backend is reaped within a minute. That is a **backstop, not a fix** — the defect remains, and the RCA's `BEGIN` fingerprint (the only fingerprint uniquely attributable to a code site) stays possible.

**Decision: (a) — DROP.** Rationale beyond the mechanics: dropping the literal BEGIN **removes the fingerprint class entirely**. `text("BEGIN")` exists nowhere else in `daemon/` (grep), so a future `idle in transaction` backend whose last query is `BEGIN` would be attributable to *this* site — and after this change, no such site exists. Option (b) would leave the fingerprint live and therefore leave the incident's triage ambiguity in place. Option (b) also keeps the double-BEGIN warning noise.

### D-02 — The rollback moves into a `finally`

After D-01, the uncovered path is a failure **of the rollback itself** (:1488): today that also lands in the outer `except:` (:1513) with no rollback. Only a `finally` covers SQL-success, SQL-error, and rollback-error uniformly. The DML branch's row-count projection (`_rows_to_dicts(result)`) must stay **inside** the `try`, before the `finally`'s rollback — the result is consumed before the transaction ends.

**Not chosen:** keeping the two existing `session.rollback()` statements in place — leaves the rollback-error path uncovered.

### D-03 — Agent-visible surface updated in the same change

The dry-run wording is part of the tool's contract with agents. To update: the preview strings `"DRY-RUN DDL EXECUTED INSIDE BEGIN/ROLLBACK — no commit"` (:1482) and its DML twin (:1496); the tool docstring's "DDL preview is a `BEGIN; <DDL>; ROLLBACK;` wrapper" (:963); the `_dry_run_sqlite_safe` docstring (:1407); the inline comments (:1076-1077).

**Deliberately NOT changed:** the module docstring (:37) and the tool docstring (:942) mention `BEGIN/COMMIT` in the **audit contract** ("every repair writes a row to `repair_log` in the SAME `BEGIN/COMMIT` as the repair on the SAME connection"). That describes the **committed** path, not the dry-run. Rewriting it would misdescribe a contract that is unchanged.

**Contract that must survive the change (AC3):** the dry-run's `previewed` audit row is written in a **separate** session via `_write_audit_outcome` (:1530-1538, "Persist the previewed audit row in a SEPARATE session so the ROLLBACK above does not eat the audit row"). The `finally`-rollback does not touch it.

---

## 3. F4 — readiness-probe timeout-path retention (Site D)

### D-04 — Probe-scoped `SET LOCAL statement_timeout` (house idiom); a dedicated probe pool is rejected

**The defect (verified at HEAD).** The DB and queue probes run under `asyncio.wait_for(asyncio.to_thread(probe), timeout=…)` (`readiness.py:1279-1281`). On timeout the asyncio side gives up but **the worker thread keeps running with its connection checked out** (`:1477` / `:1752`). The module's own docstring calls this "bounded leakage" (:107-113) — but on the **manager engine** that bound does not exist: the engine carries **no** server-side `statement_timeout` and **no** `idle_in_transaction_session_timeout` (the manager engine's only session option is `-c timezone=UTC`, `factory.py:252/:337`), and there is no client-side execute timeout, so the retention is unbounded in theory (research-leak-sites.md:118, §4 premise 8).

**The house idiom for exactly this problem already exists.** `ens_db_tools.py:787-797` bounds a statement on the **shared, pooled** engine with:

```python
with manager.engine.begin() as conn:
    # Per-tx statement_timeout inside the tool's own tx (NOT engine-wide connect_args).
    if manager.engine.url.get_backend_name().startswith("postgres"):
        conn.execute(text(f"SET LOCAL statement_timeout = {int(SELECT_TX_STATEMENT_TIMEOUT_MS)}"))
    result = conn.execute(text(query))
```

with the explicit rationale that it is per-tx and not engine-wide. `SET LOCAL` is scoped to the transaction and is reverted by the pool's `reset_on_return='rollback'`, so it does not leak to the next borrower. This is the same shape the probes need: each probe owns its transaction.

**Options considered:**

- **(a) A dedicated readiness-probe engine/pool** — the RCA's own suggestion ("it should run on a dedicated short-lived connection", RCA fix-list item 4). **REJECTED on coupling C1.** The readiness `database`/`queue_freshness` components are the daemon's *observers* of the manager pool: if the probes ran on their own pool, the readiness board would report green while the manager pool was poisoned — i.e. **detector leniency by relocation**, which is exactly what C1 forbids. A separate pool would also need the same server-side timeouts, and it would add a pool (more connections, more knobs, more incident surface). It is rejected on **safety**, not on complexity.
- **(b) Engine-wide `connect_args` timeouts only** — the whole daemon's blast radius for a probe-scoped need. Retained as the **second layer** in Phase 2 (D-16) and as F2's engine-level backstop; it is *not* the primary carrier because it cannot be scoped per probe.
- **(c) Per-probe asyncio cancellation** — impossible: `asyncio` cannot cancel a running thread's synchronous DBAPI call. The saver probe's `future.cancel()` precedent works only because that probe is an **async** coroutine scheduled on the loop (`readiness.py:1669-1684`), not a sync DBAPI call in a worker thread (research-leak-sites.md:120).

**Decision:** (b)-as-second-layer + the house's (a)-equivalent idiom (`SET LOCAL`) as the **primary**, scoped per probe. The probe-scoped bound alone is sufficient to satisfy F4; the engine-level layer is defense in depth and can be dropped (see OQ-3) without reopening F4.

### D-05 — The bound is 2× the client budget

The client-side budget is what **classifies** the failure (`asyncio.TimeoutError` → `FAILURE_CLASS_PROBE_TIMEOUT`, `readiness.py:1284-1285`). A server bound at or below the client budget would race the client and could reclassify a timeout as a hard component red — a behavior change in the readiness contract. A server bound of **2×** the client budget means the client always gives up first (deterministic classification preserved) and the server releases the connection shortly afterwards. Concretely: DB probe `2 × DB_PROBE_TIMEOUT_S` (0.5 s → 1000 ms); queue probe `2 × QUEUE_PROBE_TIMEOUT_S` (2.0 s → 4000 ms). The millisecond value is **derived from the module's own constant** in one place, so the two cannot drift.

**Residual to disclose:** the queue probe issues **two** statements inside one transaction, so its worst-case server-side hold is ~2× the per-statement bound. Bounded, but not a single number.

### D-06 — PG-only; SQLite untouched

SQLite has no `SET LOCAL` and would raise `OperationalError` — the same dual-engine concern the house already documented at `ens_db_tools.py:790-792`. The guard is the dialect check the repo already uses (`readiness.py:1747` — `engine.dialect.name == "postgresql"`). The SQLite install path must be provably unchanged.

### D-07 — An explicit end-of-transaction precedes the probe's connection return

RCA fix-list item 4 asks the probe to "explicitly commit/rollback". `Connection.rollback()` is a documented **no-op when no transaction is active** (SQLAlchemy 2.0.48, `engine/base.py:998-1023`), so calling it unconditionally is safe. It ends the probe's transaction at the probe rather than at pool reset, and it makes the intent visible to a reader (and assertable by a pin).

### D-12 — Phase 1 lands no pool knob

The engine-level `connect_args` layer (D-16) belongs to the **F2 knob table** — one table, one pin, one owner. Splitting it across phases risks a half-configured intermediate state and two competing pins. F4's *guarantee* in Phase 1 is carried entirely by the probe-scoped bound (D-04), which is independent of the engine knobs. Phase 2 completes the table and the pin.

---

## 4. F6 — the mission boot mint (Site E)

### D-08 — Idempotent mint via a dialect upsert; no schema change

**What the census established.** The rollback-guarantee half of the RCA's fix-item 6 **already holds**: the boot lane passes **no** session (`auto_continue_boot_pass.py:1154-1159`, and the pass loop at :721); `insert_mission` opens `with Session(self._engine) as own:` (`mission_record/repository.py:510`) and commits at :512 — an `IntegrityError` exits the block ⇒ `close()` ⇒ the pool's reset-on-return rollback. **689 audited borrow sites, 0 unclosed** (research-audit-census.md §1b). So the "guarantee the transaction is rolled back" option is **belt-and-braces on an already-guaranteed path** — it would not remove the error.

**What is actually broken.** The mint is **not idempotent** for the boot shape: the deterministic 4×/boot `IntegrityError` on `ux_mission_records_anchor_open` (RCA :61, :149) is a real observable whose trigger is unexplained (OQ-1).

**The fix.** Replace the own-session ORM insert with a dialect-appropriate insert carrying `.on_conflict_do_nothing(index_elements=["anchor_instance_id"], index_where=text(OPEN_EPOCH_STATUS_SQL))` — the **exact house shape** already in `daemon/repositories/job_queue/repository.py:485-496` (same `index_elements` + `index_where` idiom on a partial index), with the dialect helper of `daemon/repositories/source/repository.py:33-46` (`pg_insert` for PostgreSQL, `sqlite_insert` otherwise). On `rowcount == 0` the repository re-resolves the winning row (see D-09) and returns it.

**Migration implications — VERIFIED, expected NO schema change.** The constraint is a **partial** unique index defined **identically in three places**:
- `daemon/repositories/mission_record/models.py:307-313` (`Index("ux_mission_records_anchor_open", "anchor_instance_id", unique=True, sqlite_where=…, postgresql_where=…)` over `OPEN_EPOCH_STATUS_SQL`);
- `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69`;
- `daemon/manager.py:8549` (the PG runtime `_ensure_postgres_columns` mirror).

No new table, column, or index is introduced ⇒ the house rule ("every new SQLModel table needs its own `.sql` file") is **not triggered** ⇒ **zero new `.sql` migration files** in this lane (asserted at the gate, AC14/AC23/AC29). PG's `ON CONFLICT` against a partial index requires the index predicate to be repeated — satisfied by reusing the byte-identical `OPEN_EPOCH_STATUS_SQL` constant (the migration header pins the 3-site byte-identity, `:30-35`).

**A second, independent benefit.** `DO NOTHING` raises **no** error, so the *caller's* transaction is no longer poisoned by a statement-level `IntegrityError` — the exact failure `_mint_epoch_locked` documents today ("The caller's transaction is poisoned on PG after a statement-level IntegrityError — fail the mint", `mission_record_service.py:548-554`). Poisoned caller transactions are themselves an aborted-transaction source, so this removes a secondary contributor to the incident's aborted-transaction errors.

### D-09 — The boot path's re-resolve is keyed on the index's own predicate

`ON CONFLICT … DO NOTHING` returns **no row**, so the caller must re-resolve the winning epoch. The existing re-resolve is keyed on `founding_message_id` — and the code itself records that this probe "can NEVER be found by that probe" for a NULL-founding epoch (the boot epoch's `founding_message_id` is NULL by construction, `mission_record_service.py:558-565`, :686-688). The **anchor-scoped** rung-1 probe (`find_open_epoch_by_anchor`, `mission_record/repository.py:69-93`) filters on exactly the index's predicate (`anchor_instance_id` + `status = 'open'`) and is therefore the correct key for the boot path.

**Why this matters for OQ-1:** the upsert is idempotent under **any** trigger (race, duplicate call site, pre-existing duplicate rows), so the fix does not depend on resolving the trigger. The trigger still needs to be understood for the **disclosure** — see OQ-1 and §9.

### D-10 — Scope: the own-session (boot) path only

The caller-session path (`session is not None`) keeps its documented semantics — "the caller's transaction is poisoned … fail the mint and let the caller's fail-open wrapper skip it" (:548-556). That re-raise is load-bearing for the commissioning/revive lanes, whose re-resolve (keyed on `founding_message_id`) **does** work there, and the observed 4×/boot defect does **not** occur on that path. Changing it would alter commissioning-lane semantics for a defect that does not occur there (R5).

---

## 5. F1 — the transaction-hygiene audit record

### D-11 — A new artifact in this planning dir; the RCA is not edited

The census's headline is the honest result of F1: **~689 borrow sites across 6 families, 0 unclosed borrows, 0 leaks** (research-audit-census.md §1b). The method and the full counts are authored by a sibling worker in **`audit-method.md`** — this plan cites it **by filename** and does not restate its contents.

F1's deliverable is therefore **not** a code change. It is `.agents/shared/planning/db-pool-idle-txn/audit-findings.md`, which records:
1. the census headline + the method citation (`audit-method.md`);
2. RCA sites **B** and **C** as **audited-and-clean**, with their premise corrections (`repository.py:1383/:1429/:1434`; `defer_block_resolver.py:185-191/:274/:306/:321`; `instance_read.py:1408-1416`);
3. the five-backend reconciliation with its **inference** labels;
4. the explicit statement that no leak class was found where the census found none.

**Why not edit the RCA:** it lives in the main checkout and is under this lane's **read-only fence**. **Why not a code comment:** a one-time audit is not a code invariant.

---

## 6. F2 — pool hardening

### D-13 — Scope

Harden the **manager engine** (`daemon/repositories/factory.py:323-338`) and add the repair engine's **explicit reset pin** (`daemon/tools/ens_db_tools.py:633`). Untouched: the SQLite engine (:184-199), the latent config-driven PG branch (:215-222 — D-22), the boot preflight (`daemon/__main__.py:237-240`), the **P2 saver pool** (`daemon/persistence.py:316-331` — coupling **C2**, and the leak-signal ORACLE), the asyncpg adapter pool (`persistence.py:355-359`), `ConnectionPoolManager` (`db_pool_manager.py:261-271` — the daemon's own DB is excluded by the standing R13 guard at `ens_db_tools.py:584-589`), the vacuum NullPool (`ens_db_tools.py:649-695`), and the auth CLI engines (`auth/cli.py:152-160`).

### D-14 — Exact manager-engine knob values

| Knob | Before | After | Rationale |
|---|---|---|---|
| `pool_size` | `5` | `5` | The soak lane's A4 sizing applies to the **P2** pool; the manager engine's 5+10 stays. Changing it changes the concurrency envelope under the incident's load |
| `max_overflow` | `10` | `10` | same |
| `pool_pre_ping` | `True` | `True` | Already set (:328) — RCA fix-item 2's first clause was already satisfied |
| `pool_reset_on_return` | *absent* (library default `'rollback'`) | `"rollback"` | The property "an aborted connection is never handed out" becomes an **asserted invariant** instead of an implicit library default. `reset_on_return` is set **nowhere** in `daemon/` (research-pool-couplings.md:56). Note (from the same source): reset-on-return does **not** save a connection that is checked out and never returned — the R2 shape — which is why the server-side timeouts (D-16) are the actual cure |
| `pool_recycle` | *absent* | `3600` | Matches the only in-repo recycle precedent (`REPAIR_POOL_RECYCLE_S = 3600`, `ens_db_tools.py:182`). Bounds exposure to server-side/credential churn; at 15 slots that is ~15 reconnects/hour — negligible |
| `pool_timeout` | *absent* (library default `30`) | `30` | See D-15 |
| `pool_use_lifo` | *absent* | *absent* (deliberate) | LIFO reduces idle-time churn, but `pool_pre_ping` already covers dead connections and `pool_recycle` now governs age. Adding it changes checkout semantics under load with no measured benefit |
| `connect_args` | `{"options": "-c timezone=UTC"}` | `{"options": "-c timezone=UTC -c statement_timeout=30000 -c idle_in_transaction_session_timeout=60000"}` | See D-16/D-17. The **UTC option is preserved** (the B2/B3 UTC-frame guarantee) and stays **first**, so the existing byte-shape pin's prefix is untouched; the option **order** becomes part of the pinned shape |
| URL `connect_timeout` | `BOOT_DB_TIMEOUT_S` (10) | unchanged | The connect-phase bound is already explicit (:316) |

**Knob values live in `daemon/constants.py`** (D-18).

### D-15 — `pool_timeout` review outcome: keep 30s, pinned explicitly, residual disclosed

The probe budgets (0.5 s DB / 2.0 s queue) are **asyncio** budgets: `asyncio.wait_for` returns "timed out" at the budget while the worker thread continues waiting for a pool checkout (`readiness.py:1279-1281`). Lowering `pool_timeout` would convert pool pressure into **hard errors** (500s) on legitimate requests during exactly the incident's conditions (the endpoint was already at 5-8 s). The repair engine's 10s value would be parity-motivated but behaviorally riskier on the hot path.

**Residual, stated rather than hidden:** an abandoned probe **worker thread** may linger in the pool's checkout queue for up to 30 s. It holds **no pool slot** while waiting (it has no connection yet), and it is bounded — unlike today's unbounded server-side retention.

**Explicitly pinning the default** (rather than leaving it unset) means a future edit cannot silently change it, and the pin documents that 30s was a **decision**, not an oversight.

### D-16 — Engine-level server-side timeouts

`-c statement_timeout=30000` — the house number for a shared-engine statement cap (`SELECT_TX_STATEMENT_TIMEOUT_MS = 30_000`, `ens_db_tools.py:191`), i.e. the daemon already accepts a 30 s statement cap on the shared engine for the agent-facing reader tool. `-c idle_in_transaction_session_timeout=60000` — the repair engine's proven value (`REPAIR_IDLE_IN_TXN_TIMEOUT_MS = 60_000`, `ens_db_tools.py:188`).

Together they bound **every** idle-in-transaction backend on the manager engine — the exact symptom the incident recorded — regardless of what the client does (including the request paths that hold a transaction across in-Python work, `repository.py:1429`).

**`lock_timeout` is deliberately NOT added**: the manager engine's failure mode is idle-in-txn, not lock contention; a global lock timeout could abort legitimate contended writes.

**This layer is optional (OQ-3):** F4's guarantee is carried by the probe-scoped bound (D-04). Dropping the engine-wide layer costs F4 nothing; keeping it buys a global backstop. Recommendation: include, at the values above.

### D-17 — A NEW connect-args constant; `PG_SESSION_CONNECT_ARGS` is not extended

`PG_SESSION_CONNECT_ARGS` (`factory.py:252`) is **embedded** into the repair engine's options string (`ens_db_tools.py:619-627`), which already sets `-c statement_timeout=60000`. Extending the shared constant with `-c statement_timeout=30000` would make the repair engine's effective `statement_timeout` **30 s instead of 60 s** (PG's last `-c` wins) — a silent, unintended retune of a pool this lane is otherwise leaving alone.

So: a **new** manager-engine constant is assembled in `factory.py` from values that live in `daemon/constants.py`; `PG_SESSION_CONNECT_ARGS` stays **byte-identical** and keeps serving the repair engine. A dedicated pin (AC15/AC16) asserts the repair engine's rendered options string is byte-identical.

### D-18 — One canonical home per value

Knob **values** → `daemon/constants.py` (precedent: `factory.py:14` already imports `BOOT_DB_TIMEOUT_S` from it; `CHECKPOINT_POOL_MAX_SIZE_DEFAULT` + its honesty block at `constants.py:197-217` are the same pattern). The options **string assembly** stays in `factory.py` (a rendering detail, and the repair engine's assembly is there too).

### D-19 — The repair engine changes by exactly one knob

`pool_reset_on_return="rollback"` is added; `pool_size=2`, `max_overflow=3`, `pool_pre_ping=True`, `pool_recycle=3600`, `pool_timeout=10` and the whole `connect_args` options string stay **byte-identical**. Rationale: the repair engine is the best-configured pool in the repo; the only gap is the same implicit-reset gap, and it is the pool the incident's `BEGIN` fingerprint came from — so the reset semantics there deserve to be pinned, not assumed.

### D-21 — The honesty blocks are refreshed

`daemon/constants.py:207-217` and `daemon/persistence.py:306-315` both state that the P1 idle-in-transaction leak "is a SEPARATE defect owned by a follow-up commission". That becomes false the moment this phase lands. Both blocks are updated to record the repair (branch + date) while keeping their still-true content (the P2 pool's sizing does not cure a leak on another pool; the P2 oracle remains the arming source). **No knob value in either file moves** — and in `daemon/persistence.py` **nothing but the comment changes**.

### D-22 — The latent config-driven PG branch is not hardened

`create_engine_from_config`'s PostgreSQL branch (`factory.py:215-222`) has **zero** daemon-runtime PG callers (its own comment at :201-203 says so) and is deliberately **config-driven** (`config.pool_size` / `config.max_overflow` — a different contract from the canonical engine). Changing it widens the blast radius for no benefit. It is recorded in the out-of-scope list with a note that a future caller must migrate it deliberately.

---

## 7. F5 — the wedge-capture table name

### D-20 — A correctness fix, not a pool fix; the table name only

`daemon/services/wedge_capture/db_capture.py:308` (`FROM tasks t`) and `:387` (`FROM tasks`) are wrong — the table is `task` (`daemon/repositories/task/models.py:171`) — and the module docstring (:34) repeats the error.

**It is not a pool fix:** the capture rides a **one-shot `psycopg.connect`** (`_connect` :185-200, `connect_timeout` + per-statement `statement_timeout`, `application_name='wedge-capture'`) with `close()` in a `finally` (:289-294). **No pool** ⇒ it cannot leak an idle-in-txn backend. The incident's `current transaction is aborted` lines are **same-connection cascades**: the first (wrong-table) query aborts the transaction, so the following census and resume-history reads fail even though their own SQL is valid (research-leak-sites.md:157). Renaming fixes both the error and the cascade.

**Scope: the table name only.** Every selected column was verified present on `task`: `work_id`, `instance_id`, `task_type`, `status`, `created_at`, `started_at`, `last_heartbeat_at`, `suspension_reason`, `resume_target_turn_id` (`task/models.py:196-284`) and `auto_continued_at` (`task/models.py:294`; added on PG by `manager.py:7559`). The `LEFT JOIN instances` is correct. **Verification note:** the PG pin must run against a database where `auto_continued_at` exists (the manager's boot-time `_ensure_postgres_columns` adds it).

---

## 8. F7 — the regression guard, and the gate discipline

### D-23 — Integration placement, with a scratch database

The guard must build the **daemon's own manager engine** (so the engine's connect args and pool knobs are what is under test) and observe `pg_stat_activity`. The `tests/postgres/` shared `pg_engine` fixture owns its own schema lifecycle (`drop_all`/`create_all`, then a per-test `TRUNCATE … RESTART IDENTITY CASCADE`, `tests/postgres/conftest.py:187-195/:240-258`) and gives the test no database it can observe in `pg_stat_activity`; it is also auto-skipped under xdist (:44-45/:94-95). The soak lane's real-pool PG pins took the **integration** shape (`tests/integration/test_checkpoint_pool_burst_shapes.py:49`, `test_checkpoint_pool_ghost_waiters.py:58`), each with `pytestmark = pytest.mark.integration`, and that is the shape this guard takes.

### D-24 — Attribution by scratch-database isolation, not `application_name`

The daemon's connections carry an **empty** `application_name` (research-leak-sites.md:173), so `pg_stat_activity` cannot attribute a backend to a pool post-hoc — the same gap that makes the incident's five-backend attribution partly inference. For the guard, the test creates its **own** database and filters `pg_stat_activity WHERE datname = <that database>`; every backend there is the test's. This avoids changing the daemon's connection metadata (out of scope) and gives the guard *stronger* attribution than the incident had.

### D-25 — A default-collected static leg

A default `pytest` run collects **zero** PG tests (C3). Without a default-collected leg, deleting or un-marking the guard would leave the tree green — the exact false-green shape the house rule exists to prevent. G-A asserts the guard file exists, carries the `integration` marker, uses the scratch-DB isolation, and that the manager engine's connect args carry the server-side timeouts.

### D-26 / D-27 — Gate discipline

Every gate: (i) runs in the house form — `--override-ini="addopts=" -m postgres|integration`, or is default-collected; (ii) **asserts its expected collected count > 0** before "0 failed" is read as a pass; (iii) names the interpreter explicitly (`python` does not exist on this host; `uv` is at `/home/nea/.local/bin/uv`); (iv) scrubs ambient `POSTGRES_*` **one variable at a time** and fences `HOME` with `mktemp -d` — **`env -u POSTGRES_*` is a silent NO-OP** (bash does not glob env names) and is forbidden.

---

## 9. Phasing rationale (D-28)

**Phase 1 — transaction hygiene (F3, F4, F6) + the F1 audit record.** These are the behavior-changing fixes with the highest incident leverage, and each is independently verifiable without touching the pool configuration. F3 and F4 address the two mechanisms that survive the census; F6 removes the boot-time error class; F1 publishes the honest audit record so the lane's premise corrections are durable.

**Phase 2 — pool hardening (F2) + the wedge-capture fix (F5).** The knob table is one table with one pin and lands after Phase 1 so the pin asserts the **final** configuration (including the timeout options, whose vehicle is shared with F4). F5 is independent of the pool work and rides here because it is small, self-contained, and its PG pin shares the phase's PG gate.

**Phase 3 — the regression guard (F7) + the gate structure.** Last, so the guard pins the **final** behavior of the fixed code, and so the lane's evidence set (mutation-proven-ness, net-new-reds, DO-NOT-TOUCH diff review) is assembled against a settled tree.

**Why the F4/F2 vehicle split is explicit (D-12).** F4's *guarantee* lands in Phase 1 via the probe-scoped bound; the engine-level backstop lands in Phase 2 with the rest of the knob table. A reader of the phase gates must be able to tell which artifact carries which guarantee — hence the explicit statement in `phase1-plan.md` §1 and `phase2-plan.md` §3.1.

---

## 10. Re-targeting ledger — every WHAT from the dispatch, and what it became

The dispatch permits refining the HOW but forbids silently dropping a WHAT. Every refinement is recorded here with its citation.

| Dispatch item | WHAT preserved | How refined / re-targeted | Why |
|---|---|---|---|
| **F1** — transaction hygiene + site audit | "Document the census honestly; the method + counts live in `audit-method.md`" | The **code** content of F1 collapses to zero: the census found **0 unclosed borrows** across ~689 sites. F1 = the audit document (B/C audited-and-clean + RCA premise corrections), delivered as `audit-findings.md` in this planning dir | research-audit-census.md §1b (689 CLEAN / 0 LEAK); the RCA's premise-2/4 corrections |
| **F2** — pool hardening | All five knob families | `pool_pre_ping` was **already** `True`; `reset_on_return` was **already** effectively `'rollback'` (library default). The genuine delta is: the reset pin made **explicit**, `pool_recycle`, the two server-side timeout options, and a `pool_timeout` **review outcome** (keep 30s, pinned, residual disclosed) | research-pool-couplings.md §Q3 knob matrix; `factory.py:323-338` |
| **F3** — the BEGIN gap | The site + the guarantee of rollback on every path | HOW chosen: **drop the BEGIN + `finally`-rollback** rather than move-inside-try | research-leak-sites.md:59 + the fingerprint argument in D-01 |
| **F4** — probe retention | "The readiness probe must not leak / amplify" | The **per-tick** premise is **REFUTED**, so the fix is re-targeted from "stop the per-tick leak" to "**bound the timeout-path retention server-side**" (the WHAT — the probe must not leak or amplify — is preserved). HOW: probe-scoped `SET LOCAL` + explicit end-of-transaction; a dedicated probe pool is **rejected on C1** | research-leak-sites.md §1 Site D + §4 premise 8; soak `decisions.md` §2 premise 10 |
| **F5** — wedge-capture | The rename `tasks` → `task` | Unchanged; **added** the observation that this path rides no pool and that the incident's aborted-transaction lines are same-connection cascades — so it is a correctness fix, not a pool fix | research-leak-sites.md:155-157 |
| **F6** — mint `UniqueViolation` | Idempotency vs disclosure; record the unexplained trigger | The **rollback-guarantee branch collapses** (already guaranteed by the census); **upsert** chosen. HOW narrowed to the boot/own-session path. Migration expectation **verified: NO schema change** (the partial unique index exists in 3 places) | research-leak-sites.md:140-144; `models.py:307-313` + `20261025_140000_…sql:67-69` + `manager.py:8549` |
| **F7** — regression guard | The guard | Placement, isolation model, attribution technique, and the mutation-proven-ness requirement are **decided here**; the detailed design stays in `test-strategy.md` | research-pool-couplings.md §Q5 (runner forms) + §Q6 (no existing idle-in-txn assertion) |

---

## 11. Open questions carried forward (for the architect)

| ID | Question | Does it change the plan? |
|---|---|---|
| **OQ-1** | Why is the boot-mint `UniqueViolation` deterministic (4×/boot, once per resumed actor)? Candidate hypothesis (named, **not asserted**): the boot pass has **two** mint call sites for the same instance (`auto_continue_boot_pass.py:721` and `:1156`) — 4 instances × 1 duplicate = 4 collisions, matching the count. Needed: the boot-log lines + a `mission_records` census | **No** — the upsert is idempotent under every trigger. It changes the **disclosure**: if the trigger is a data condition (pre-index duplicate OPEN rows), a data remediation is additionally needed (out of scope). Decide: accept the upsert as the whole fix with the trigger routed to a follow-up, or commission the investigation in parallel |
| **OQ-2** | Which pool did the incident's five backends belong to? `application_name` is empty, so `pg_stat_activity` cannot distinguish manager-engine from repair-engine backends post-hoc; and the repair engine's 60 s `idle_in_transaction_session_timeout` should have reaped a leaked BEGIN backend within a minute — either the live v0.27.8 binary predates that `connect_args` option (**dating not verified**) or the backends were on another engine | **No** — F3 removes the BEGIN entirely and F2/F4 bound both engines' retention. It changes the RCA follow-up narrative and the instrumentation priority |
| **OQ-3** | Engine-wide `statement_timeout` on the manager engine: **include or drop**? | **Recommendation: include at 30 s.** Dropping it costs F4 nothing (the probe-scoped bound is the guarantee) — that is the safety-precedence narrowing (D-16) |
| **OQ-4** | Is "keep `pool_timeout=30`" acceptable given the probe budget is 0.5 s? | **Recommendation: yes** — the probe budget is an asyncio budget, not a checkout budget; lowering it converts load into 500s. The residual (a probe worker thread lingering up to 30 s in the checkout queue, holding no pool slot) is disclosed in D-15 |
| **OQ-5** | Should `_enrich_instances` be moved outside the session scope now (the one known hot spot holding a transaction across in-Python work, `repository.py:1429`)? | **Recommendation: out of scope**, disclosed under R2. The architect may fold it in; it is a repository-semantics change to a shared read path |
