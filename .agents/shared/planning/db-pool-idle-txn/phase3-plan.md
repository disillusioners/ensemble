# Phase 3 — Regression guard (F7) and the lane's gate structure

- **Phase objective:** build the greenfield regression guard the RCA asked for (fix-list item 7) and assemble the lane's release gate so that every gate **runs in a form the runner actually executes** and **asserts its expected collected count**.
- **Base:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87`.
- **Depends on:** Phases 1-2 (the guard pins the FINAL behavior of the fixed code).
- **Sibling artifact:** `test-strategy.md` — the guard's design, the repro, the mutation pins, and the C3 runner form are authored there in parallel; this phase file defines the **GATE STRUCTURE** and the placement/isolation decisions, and references `test-strategy.md` by filename.

---

## 1. Objective

At the end of Phase 3:

1. A **behavioral** guard exists that FAILS when a connection is returned to the pool carrying an open transaction, and FAILS when an `idle in transaction` backend survives a read cycle against the daemon's real manager engine.
2. The guard is **mutation-meaningful**: with the defect present the pin is RED, without it GREEN — proven on at least one leg by a planted mutation (the technique and the leg list live in `test-strategy.md`).
3. The guard is **reachable**: a default-collected static pin fails if the guard is deleted, un-marked, or moved out of the house runner form — so a bare `pytest` run cannot silently lose it.
4. The lane has a single, ordered gate ladder in which **every** command either runs in the house form or is default-collected, and every one of them asserts its expected collected count.

## 2. Decisions (canonical records live in `decisions.md`)

| ID | Decision | Rationale (cite) | Rejected alternatives |
|----|----------|------------------|------------------------|
| **D-23** | **The guard rides the INTEGRATION placement** — `tests/integration/test_db_pool_idle_in_transaction_guard.py` with `pytestmark = pytest.mark.integration` — executed by `uv run python -m pytest --override-ini="addopts=" -m integration <paths> -q` | The guard must (a) build the **daemon's own manager engine** (`create_postgres_engine`) so the *engine's* connect args and pool knobs are what is under test, and (b) observe `pg_stat_activity` **filtered by `datname`** — which requires a **dedicated scratch database** the test owns. The `tests/postgres/` shared `pg_engine` fixture owns its own schema lifecycle (`drop_all`/`create_all` + per-test `TRUNCATE`, `tests/postgres/conftest.py:187-195/:240-258`) and does not give the test a database it can observe in `pg_stat_activity`. The soak lane's real-pool PG pins took exactly this shape (`tests/integration/test_checkpoint_pool_burst_shapes.py:49`, `test_checkpoint_pool_ghost_waiters.py:58`) | (a) `tests/postgres/` — the shared fixture's lifecycle and its xdist auto-skip (`:44-45/:94-95`) work against an observation-based guard, and the guard would be invisible to the `-m integration` house form. (b) A default-collected PG test — impossible: a bare `pytest` run has no PG. (c) `pg_stat_activity` attribution via `application_name` — the daemon's connections carry an **empty** `application_name` (research-leak-sites.md:173) and changing that is out of scope; the **scratch-database** filter solves attribution without touching the daemon |
| **D-24** | **Attribution is by scratch-database isolation, not by `application_name`** | The test creates its own database (unique name per test), points the daemon's engine factory at it, and asserts on `pg_stat_activity WHERE datname = <that db>`. Every backend in that database belongs to the test | Adding an `application_name` to the daemon's engine — a daemon-behavior change outside this lane's scope, and unnecessary given D-23's isolation |
| **D-25** | **The guard has a default-collected static leg** (G-A) beside the PG legs (G-B/G-C/G-D) | A default `pytest` run collects **zero** PG tests (C3). Without a default-collected leg, deleting or un-marking the guard would leave the whole tree green — the exact false-green shape the house rule exists to prevent (R7). G-A asserts: the guard file exists, carries the `integration` marker, uses the scratch-DB isolation, and that the manager engine's connect args carry the server-side timeouts | Relying on the PG legs alone — a false-green surface |
| **D-26** | **Every gate in the lane's ladder asserts its EXPECTED COLLECTED COUNT** (`pytest <paths> --collect-only -q` must print the expected N > 0) **before** "0 failed" is read as a pass | `pyproject.toml:95` deselects `integration`/`postgres`/`slow`/`conformance` by default; a bare `pytest tests/integration/…` reports "0 failed" with **nothing executed** (soak `plan-overview.md:362-366`; house rule in the soak lane's `requirements.md:1133/:1169-1172`). This is coupling **C3** and it applies to *every* gate in this plan set | Reading "0 failed" as a pass — the documented false-green failure mode |
| **D-27** | **Every PG gate uses the per-variable env scrub and a fenced HOME**; `env -u POSTGRES_*` is **forbidden** | `env -u POSTGRES_*` is a silent NO-OP — bash does not glob env names, so an ambient `POSTGRES_DB=ensemble_prod` stays visible and a "fenced" run can hit production (project convention note, 2026-10-21). The house form scrubs one variable at a time and uses a `mktemp -d` HOME | A glob scrub — a documented no-op that produces a false fence |

### 2.1 The guard legs (design detail in `test-strategy.md`)

| Leg | Kind | What it asserts | Where it lives |
|-----|------|------------------|----------------|
| **G-A** | default-collected unit (static + config) | The guard file exists, is `integration`-marked, uses scratch-DB isolation, and the manager engine's connect args carry the server-side timeouts | `tests/unit/…` (new) |
| **G-B** | PG behavioral | After a read/write cycle through the **real manager engine**, `pg_stat_activity` for the scratch database shows **zero** backends in `idle in transaction` | `tests/integration/test_db_pool_idle_in_transaction_guard.py` |
| **G-C** | PG behavioral (the mutation anchor) | Deliberately open a transaction on a pooled connection and return it to the pool **without** a rollback → the pool's `reset_on_return='rollback'` must clear it → **zero** `idle in transaction` backends. With the knob removed/changed the pin is RED | same file |
| **G-D** | PG behavioral (bounded churn) | After N read cycles (bounded so the worst case fits the house timeout), the idle-in-transaction count is still **zero** | same file |

**Gate ladder for the lane (all commands name the interpreter explicitly; `python` does not exist on this host and `uv` is at `/home/nea/.local/bin/uv`):**

| Gate | Scope | Command shape | Non-vacuity assertion |
|---|---|---|---|
| **G0** | The guard's own reachability | `uv run python -m pytest tests/unit/<g-a file> -q` | collected count == expected (> 0) |
| **G1** | Phase-1 packs (F3/F4/F6 unit legs) | `uv run python -m pytest tests/unit/... -q` (default-collected files) | collected count == expected |
| **G2** | Phase-2 packs (knob matrix, repair-engine, P2 immutability) | `uv run python -m pytest tests/unit/... -q` | collected count == expected |
| **G3** | Phase-1 PG legs (F4 retention, F6 idempotency) | `uv run python -m pytest --override-ini="addopts=" -m postgres <paths> -q` | `pytest <paths> --collect-only -q` == expected, **> 0** |
| **G4** | Phase-2 PG legs (wedge-capture correctness) | `uv run python -m pytest --override-ini="addopts=" -m integration <paths> -q` | `pytest <paths> --collect-only -q` == expected, **> 0** |
| **G5** | **The F7 guard** | `uv run python -m pytest --override-ini="addopts=" -m integration tests/integration/test_db_pool_idle_in_transaction_guard.py -q` | `pytest <path> --collect-only -q` == expected, **> 0** |
| **G6** | Whole-tree sweep | `uv run python -m pytest -q` (default form) | collected count > 0 and **net-new reds == 0** versus `19fc11c8b` |

**Fenced form for every PG gate (D-27):** `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT HOME="$(mktemp -d)" uv run python -m pytest --override-ini="addopts=" -m <marker> <paths> -q` — **one variable per `-u`**, never `env -u POSTGRES_*`.

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|----------------|----|
| **T3.1** | **F7 — implement the guard legs G-B/G-C/G-D** per `test-strategy.md`, using the daemon's **real** `create_postgres_engine` against a **unique scratch database** created and dropped by the test | The guard fails when a pooled connection is returned with an open transaction and passes otherwise | `tests/integration/test_db_pool_idle_in_transaction_guard.py` | `test-strategy.md` | R7 | AC24 |
| **T3.2** | **F7 — implement the default-collected static leg G-A** | Deleting or un-marking the guard turns the default `pytest` run RED | `tests/unit/…` (new) | T3.1 | R7 | AC25 |
| **T3.3** | **F7 — mutation-proven-ness.** On at least one leg (G-C is the natural anchor), plant the defect (remove the reset / open a transaction and return it), show the pin goes RED, restore, show GREEN — and record the digests | The guard is not vacuous | `tests/integration/…`, `tests/unit/…` | T3.1, T3.2 | — | AC26 |
| **T3.4** | **F7 — the guard's own timeout discipline.** Every PG leg carries an explicit `@pytest.mark.timeout(N)` with `N` greater than its worst case (house convention: the repo default is 30 s with `timeout_method="thread"` — `pyproject.toml:97-98`) | No leg can hang the suite or be killed mid-assertion | `tests/integration/…` | T3.1 | — | AC27 |
| **T3.5** | **Assemble the gate ladder (G0-G6)** into a single executable list with each command's **expected collected count** written next to it, and the fenced env form for the PG gates | The lane's gate is one artifact; every command states its non-vacuity assertion | the lane's gate script/notes (no new daemon code) | T3.1-T3.4 | R7 | AC28 |
| **T3.6** | **Phase gate.** (a) G0-G6 all green, each with its collected-count assertion satisfied; (b) the mutation evidence from T3.3 attached; (c) whole-tree sweep with **net-new reds = 0**; (d) the DO-NOT-TOUCH surfaces re-checked by diff (`daemon/services/readiness.py` detector regions, `daemon/persistence.py` knobs, `scripts/upgrade/lib.sh`, `daemon/api.py` wiring); (e) confirm **zero** new `.sql` migration files across the whole lane | The lane's evidence set is complete and non-vacuous | — | T3.5 | R7, R11 | AC28, AC29 |

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| **AC24** | Against a real PG, the guard observes `pg_stat_activity` **scoped to its own scratch database** and asserts **zero** `idle in transaction` backends after a read/write cycle through the daemon's real manager engine — and is RED when a transaction is deliberately left open on a returned connection |
| **AC25** | A **default-collected** unit leg fails if the guard file is deleted, un-marked, or moved out of the `integration` placement |
| **AC26** | The guard is **mutation-proven**: with the defect planted the pin is RED (digest recorded), and GREEN after restore — for at least one leg |
| **AC27** | Every PG leg carries an explicit `@pytest.mark.timeout(N)` with `N` greater than its worst case |
| **AC28** | Every gate command (G0-G6) is written down with its **expected collected count** and its runner form; every PG gate uses the **per-variable** env scrub (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT`, never `env -u POSTGRES_*`) and a `mktemp -d` HOME |
| **AC29** | Whole-tree sweep: **net-new reds = 0** versus `19fc11c8b`; the DO-NOT-TOUCH surfaces are byte-identical outside the permitted changes; **zero** new `.sql` migration files across the lane |

## 5. Pin-pack inventory (all phases — where each pack lives and who executes it)

| Pack | Phase | Marker | Executed by | Collected-count assertion |
|------|-------|--------|-------------|--------------------------|
| F3 dry-run rollback pack | 1 | none (default) | G1 | yes |
| F4 probe-bound pack (unit) | 1 | none (default) | G1 | yes |
| F4 retention pack (PG) | 1 | `postgres` or `integration` (per `test-strategy.md`) | G3 | yes (non-vacuity) |
| F6 idempotency packs (unit + PG) | 1 | none / `postgres` | G1 / G3 | yes |
| Manager-engine knob matrix | 2 | none (default) — extends `tests/unit/repositories/test_pg_engine_utc_session.py` | G2 | yes |
| Repair-engine knob pin | 2 | none (default) | G2 | yes |
| P2-immutability pin | 2 | none (default) | G2 | yes |
| wedge-capture correctness pack (PG) | 2 | `integration` | G4 | yes |
| F7 guard legs G-A/G-B/G-C/G-D | 3 | none / `integration` | G0 / G5 | yes |

> The **never-green / leak-refusal** mutation legs remain owned by the soak lane (`scripts/upgrade/lib.sh` semantics; the soak lane's own tester-side mutation evidence). This lane does **not** re-derive or weaken them — coupling **C2**.
