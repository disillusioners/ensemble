# Phase 3 — Regression guard (F7) and the lane's gate structure

- **Phase objective:** build the greenfield regression guard the RCA asked for (fix-list item 7) and assemble the lane's release gate so that every gate **runs in a form the runner actually executes** and **asserts its expected collected count**.
- **Base:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87`.
- **Depends on:** Phases 1-2 (the guard pins the FINAL behavior of the fixed code).
- **Sibling artifact:** `test-strategy.md` — the guard's design, the repro, the mutation pins, and the C3 runner form are authored there in parallel; this phase file defines the **GATE STRUCTURE** and the placement/isolation decisions, and references `test-strategy.md` by filename.

---

## 1. Objective

At the end of Phase 3:

1. A **behavioral** guard exists that FAILS when a connection is returned to the pool carrying an open transaction, and FAILS when an `idle in transaction` backend survives a read cycle against the daemon's real manager engine. **AMENDED (C-10):** the guard measures **OCCUPANCY AND RESIDENCE with attribution** — never a bare count==0 assertion.
2. The guard is **mutation-meaningful**: with the defect present the pin is RED, without it GREEN — proven on at least one leg by a planted mutation (the technique and the leg list live in `test-strategy.md`).
3. The guard is **reachable**: a default-collected static pin fails if the guard is deleted, un-marked, or moved out of the house runner form — so a bare `pytest` run cannot silently lose it.
4. The lane has a single, ordered gate ladder in which **every** command either runs in the house form or is default-collected, and every one of them asserts its expected collected count. **AMENDED (C-1):** every gate uses the CERTIFIED INTERPRETER (`/home/nea/ensemble-src/.venv/bin/python`), NOT `uv run`, NOT `/usr/bin/python3`.

## 2. Decisions (canonical records live in `decisions.md`)

| ID | Decision | Rationale (cite) | Rejected alternatives |
|----|----------|------------------|------------------------|
| **D-23** | **The guard rides the INTEGRATION placement** — `tests/integration/test_db_pool_idle_in_transaction_guard.py` with `pytestmark = pytest.mark.integration` — executed by `/home/nea/ensemble-src/.venv/bin/python -m pytest --override-ini="addopts=" -m integration <paths> -q` | The guard must (a) build the **daemon's own manager engine** (`create_postgres_engine`) so the *engine's* connect args and pool knobs are what is under test, and (b) observe `pg_stat_activity` **filtered by `datname`** — which requires a **dedicated scratch database** the test owns. The `tests/postgres/` shared `pg_engine` fixture owns its own schema lifecycle (`drop_all`/`create_all` + per-test `TRUNCATE`, `tests/postgres/conftest.py:187-195/:240-258`) and does not give the test a database it can observe in `pg_stat_activity`. The soak lane's real-pool PG pins took exactly this shape (`tests/integration/test_checkpoint_pool_burst_shapes.py:49`, `test_checkpoint_pool_ghost_waiters.py:58`), each with `pytestmark = pytest.mark.integration`, and that is the shape this guard takes | (a) `tests/postgres/` — the shared fixture's lifecycle and its xdist auto-skip (`:44-45/:94-95`) work against an observation-based guard, and the guard would be invisible to the `-m integration` house form. (b) A default-collected PG test — impossible: a bare `pytest` run has no PG. (c) `pg_stat_activity` attribution via `application_name` — the daemon's connections carry an **empty** `application_name` (research-leak-sites.md:173) and changing that is out of scope; the **scratch-database** filter solves attribution without touching the daemon. **AMENDED (D-30):** attribution is "by `application_name` where present, else by scratch-DB isolation" |
| **D-24** | **Attribution is by scratch-database isolation, not `application_name`** | The test creates its **own** database (unique name per test), points the daemon's engine factory at it, and asserts on `pg_stat_activity WHERE datname = <that db>`. Every backend in that database belongs to the test | Adding an `application_name` to the daemon's engine — a daemon-behavior change outside this lane's scope, and unnecessary given D-23's isolation. **AMENDED (D-30):** reconciled, not replaced |
| **D-25** | **The guard has a default-collected static leg** (G-A) beside the PG legs (G-B/G-C/G-D) | A default `pytest` run collects **zero** PG tests (C3). Without a default-collected leg, deleting or un-marking the guard would leave the whole tree green — the exact false-green shape the house rule exists to prevent (R7). G-A asserts: the guard file exists, carries the `integration` marker, uses the scratch-DB isolation, and that the manager engine's connect args carry the server-side timeouts | Relying on the PG legs alone — a false-green surface |
| **D-26** | **Every gate in the lane's ladder asserts its EXPECTED COLLECTED COUNT** (`pytest <paths> --collect-only -q` must print the expected N > 0) **before** "0 failed" is read as a pass | `pyproject.toml:95` deselects `integration`/`postgres`/`slow`/`conformance` by default; a bare `pytest tests/integration/…` reports "0 failed" with **nothing executed** (soak `plan-overview.md:362-366`; house rule in the soak lane's `requirements.md:1133/:1169-1172`). This is coupling **C3** and it applies to *every* gate in this plan set | Reading "0 failed" as a pass — the documented false-green failure mode |
| **D-27** | **Every PG gate uses the per-variable env scrub and a fenced HOME**; `env -u POSTGRES_*` is **forbidden** | `env -u POSTGRES_*` is a silent NO-OP — bash does not glob env names, so an ambient `POSTGRES_DB=ensemble_prod` stays visible and a "fenced" run can hit production (project convention note, 2026-10-21). The house form scrubs one variable at a time and uses a `mktemp -d` HOME | A glob scrub — a documented no-op that produces a false fence |
| **D-38 (NEW)** | **F7 measures occupancy AND residence with attribution** (C-10) — never a bare count==0 assertion | A connection checked out before its first statement is PG state `idle`, not `idle in transaction`. The incident's shape was 15/15 slots held with only 6 in `idle in transaction` and 9 idle-but-checked-out ⇒ a census-only guard would be GREEN on the incident. | The old "count == 0 at every sample" invariant (withdrawn) |

### 2.1 The guard legs (design detail in `test-strategy.md`) — AMENDED (C-10)

| Leg | Kind | What it asserts | Where it lives |
|-----|------|------------------|----------------|
| **G-A** | default-collected unit (static + config) | The guard file exists, is `integration`-marked, uses scratch-DB isolation, and the manager engine's connect args carry the server-side timeouts | `tests/unit/…` (new) |
| **G-B** | PG behavioral | After a read/write cycle through the **real manager engine**, `pg_stat_activity` for the scratch database shows **zero** backends in `idle in transaction` **AND** `manager.engine.pool.checkedout()` returns to baseline | `tests/integration/test_db_pool_idle_in_transaction_guard.py` |
| **G-C** | PG behavioral (the mutation anchor) | Deliberately open a transaction on a pooled connection and return it to the pool **without** a rollback → the pool's `reset_on_return='rollback'` must clear it → **zero** `idle in transaction` backends. With the knob removed/changed the pin is RED | same file |
| **G-D** | PG behavioral (bounded churn) | After N read cycles (bounded so the worst case fits the house timeout), the idle-in-transaction count is still **zero** **AND** `checkedout()` returns to baseline | same file |
| **G-E (NEW — C-10)** | PG behavioral (occupancy) | After a churn cycle, `manager.engine.pool.checkedout()` returns to its baseline (in-memory, no DB round trip). **This is the pin that would have caught the incident** (15/15 slots held, only 6 in idle-in-txn). | same file |
| **G-F (NEW — C-10)** | PG behavioral (residence) | No backend's idle-in-txn residence exceeds the budget (`now() - state_change <= DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S`). **Not** a count assertion. | same file |

**Gate ladder for the lane (all commands name the interpreter explicitly; `python` does not exist on this host):**

| Gate | Scope | Command shape | Non-vacuity assertion |
|---|---|---|---|
| **G3.4** (legacy G0) | The guard's own reachability | `/home/nea/ensemble-src/.venv/bin/python -m pytest tests/unit/<g-a file> -q` | collected count == expected (> 0) |
| **G1.1–G1.4** (legacy G1) | Phase-1 packs (F3/F4/F6 unit legs + F8 static pin) | `/home/nea/ensemble-src/.venv/bin/python -m pytest tests/unit/... -q` (default-collected files) | collected count == expected |
| **G2.1–G2.3** (legacy G2) | Phase-2 packs (knob matrix, repair-engine, P2 immutability) | `/home/nea/ensemble-src/.venv/bin/python -m pytest tests/unit/... -q` | collected count == expected |
| **G2.1–G2.3** (legacy G3) | Phase-1 PG legs (F4 retention, F6 idempotency) | `/home/nea/ensemble-src/.venv/bin/python -m pytest --override-ini="addopts=" -m postgres <paths> -q` | `pytest <paths> --collect-only -q` == expected, **> 0** |
| **G2.1–G2.3** (legacy G4) | Phase-2 PG legs (wedge-capture correctness) | `/home/nea/ensemble-src/.venv/bin/python -m pytest --override-ini="addopts=" -m integration <paths> -q` | `pytest <paths> --collect-only -q` == expected, **> 0** |
| **G3.1–G3.4** (legacy G5) | **The F7 guard** | `/home/nea/ensemble-src/.venv/bin/python -m pytest --override-ini="addopts=" -m integration tests/integration/test_db_pool_idle_in_transaction_guard.py -q` | `pytest <path> --collect-only -q` == expected, **> 0** |
| **G1.5 / G3.5** (legacy G6) | Whole-tree sweep | `/home/nea/ensemble-src/.venv/bin/python -m pytest -q` (default form) | collected count > 0 and **net-new reds == 0** versus `19fc11c8b` |

**Fenced form for every PG gate (D-27):** `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT HOME="$(mktemp -d)" /home/nea/ensemble-src/.venv/bin/python -m pytest --override-ini="addopts=" -m <marker> <paths> -q` — **one variable per `-u`**, never `env -u POSTGRES_*`.

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|----------------|----|
| **T3.1** | **F7 — implement the guard legs G-B/G-C/G-D/G-E/G-F** per `test-strategy.md`, using the daemon's **real** `create_postgres_engine` against a **unique scratch database** created and dropped by the test. **AMENDED (C-10):** G-E asserts occupancy (checkedout() returns to baseline); G-F asserts residence (no backend's idle-in-txn residence exceeds the budget). G-B asserts both count==0 AND checkedout() back to baseline. | The guard fails when a pooled connection is returned with an open transaction and passes otherwise; the guard fails when occupancy does not return to baseline | `tests/integration/test_db_pool_idle_in_transaction_guard.py` | `test-strategy.md` | R7 | AC24 |
| **T3.2** | **F7 — implement the default-collected static leg G-A** | Deleting or un-marking the guard turns the default `pytest` run RED | `tests/unit/…` (new) | T3.1 | R7 | AC25 |
| **T3.3** | **F7 — mutation-proven-ness.** On at least one leg (G-C is the natural anchor), plant the defect (remove the reset / open a transaction and return it), show the pin goes RED, restore, show GREEN — and record the digests. **AMENDED (C-10):** also plant an occupancy defect (hold 2 manager slots across the churn cycle) and show G-E REDs. | The guard is not vacuous | `tests/integration/…`, `tests/unit/…` | T3.1, T3.2 | — | AC26 |
| **T3.4** | **F7 — the guard's own timeout discipline.** Every PG leg carries an explicit `@pytest.mark.timeout(N)` with `N` greater than its worst case (house convention: the repo default is 30 s with `timeout_method="thread"` — `pyproject.toml:97-98`) | No leg can hang the suite or be killed mid-assertion | `tests/integration/…` | T3.1 | — | AC27 |
| **T3.5** | **Assemble the gate ladder (G1.1–G3.5)** into a single executable list with each command's **expected collected count** written next to it, and the fenced env form for the PG gates. **AMENDED (C-1):** every command uses the certified interpreter. | The lane's gate is one artifact; every command states its non-vacuity assertion | the lane's gate script/notes (no new daemon code) | T3.1-T3.4 | R7 | AC28 |
| **T3.6** | **Phase gate.** (a) G1.1–G3.5 all green, each with its collected-count assertion satisfied; (b) the mutation evidence from T3.3 attached; (c) whole-tree sweep with **net-new reds = 0**; (d) the DO-NOT-TOUCH surfaces re-checked by diff (`daemon/services/readiness.py` detector regions, `daemon/persistence.py` knobs, `scripts/upgrade/lib.sh`, `daemon/api.py` wiring, `daemon/manager.py` outside the allowed region, `daemon/tools/instance.py` outside the bus-watch site); (e) confirm **zero** new `.sql` migration files across the whole lane; (f) **F8 pins green** (F8-a through F8-e) | The lane's evidence set is complete and non-vacuous | — | T3.5 | R7, R11, R13 | AC28, AC29, AC30-AC33 |

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| **AC24** | Against a real PG, the guard observes `pg_stat_activity` **scoped to its own scratch database** and asserts **zero** `idle in transaction` backends after a read/write cycle through the daemon's real manager engine **AND** `manager.engine.pool.checkedout()` returns to baseline — and is RED when a transaction is deliberately left open on a returned connection **OR** when occupancy does not return to baseline |
| **AC25** | A **default-collected** unit leg fails if the guard file is deleted, un-marked, or moved out of the `integration` placement |
| **AC26** | The guard is **mutation-proven**: with the defect planted the pin is RED (digest recorded), and GREEN after restore — for at least one leg (including an occupancy defect for G-E) |
| **AC27** | Every PG leg carries an explicit `@pytest.mark.timeout(N)` with `N` greater than its worst case |
| **AC28** | Every gate command (G1.1–G3.5) is written down with its **expected collected count** and its runner form; every PG gate uses the **per-variable** env scrub (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT`, never `env -u POSTGRES_*`) and a `mktemp -d` HOME; every gate uses the **certified interpreter** (`/home/nea/ensemble-src/.venv/bin/python`) |
| **AC29** | Whole-tree sweep: **net-new reds = 0** versus `19fc11c8b`; the DO-NOT-TOUCH surfaces are byte-identical outside the permitted changes; **zero** new `.sql` migration files across the lane |

## 5. Pin-pack inventory (all phases — where each pack lives and who executes it)

| Pack | Phase | Marker | Executed by | Collected-count assertion |
|------|-------|--------|-------------|--------------------------|
| F3 dry-run rollback pack | 1 | none (default) | G1 | yes |
| F4 probe-bound pack (unit) | 1 | none (default) | G1 | yes |
| F4 retention pack (PG) | 1 | `postgres` or `integration` (per `test-strategy.md`) | G3 | yes (non-vacuity) |
| F6 idempotency packs (unit + PG) | 1 | none / `postgres` | G1.1–G1.4 / G3.1–G3.3 | yes |
| F8 static pin (F8-a) | 1 | none (default) | G1 | yes |
| F8 PG pins (F8-b/c/d/e) | 1 | `integration` | G3/G5 | yes |
| Manager-engine knob matrix | 2 | none (default) — extends `tests/unit/repositories/test_pg_engine_utc_session.py` | G2.1–G2.3 | yes |
| Repair-engine knob pin | 2 | none (default) | G2.1–G2.3 | yes |
| P2-immutability pin | 2 | none (default) | G2.1–G2.3 | yes |
| wedge-capture correctness pack (PG) | 2 | `integration` | G2.1–G2.3 | yes |
| F7 guard legs G-A/G-B/G-C/G-D/G-E/G-F | 3 | none / `integration` | G3.1–G3.4 | yes |
| **G-consistency (NEW — W-11)** | 3 | none (default) | G1.1–G1.4 (static) | yes — asserts every gate ID referenced in decisions.md and architecture-recommendation.md exists in the canonical ladder (G1.1…G3.5); the legacy→canonical mapping table in plan-overview.md is an explicitly exempt block; plantable mutation: a NON-EXEMPT reference to a non-canonical gate ID ⇒ RED |

> The **never-green / leak-refusal** mutation legs remain owned by the soak lane (`scripts/upgrade/lib.sh` semantics; the soak lane's own tester-side mutation evidence). This lane does **not** re-derive or weaken them — coupling **C2**.

## 6. DO-NOT-TOUCH list for this phase (from `research-pool-couplings.md` §Q4 + council fold)

Reproduced verbatim so the implementer does not have to re-derive it:

- `daemon/services/readiness.py` — `:312-325` (detail fields), `:408-446` (readyz detail serialization), `:500-522` (`resolve_psycopg_pool`), `:525-549` (`sample_sqlalchemy_pool_state` — P1 ADVISORY, **MUST NOT arm**), `:552-567` (`sample_psycopg_pool_state` — P2 ORACLE), `:656-671` (tracker init), `:690-781` (`observe()` — the **conjunction** at :734-743, arm-on-first-eligible :745-763, re-arm-after-full-lapse), `:784-941` (`_evaluate_leak` — waiting-streak arm :848-867, exhaustion arm :874-903, trend arm DELIBERATELY SUBSUMED :806-843/:905-915, S9 streak arm :917-935), `:961-983` (`_checkedout_rising` — published, never arming).
- `daemon/constants.py` — `READINESS_GRACE_HORIZON_S=120.0` (:165), `READINESS_LEAK_TICK_THRESHOLD=3` (:175), `CHECKPOINT_POOL_MAX_SIZE_DEFAULT=10` (:197), `CHECKPOINT_POOL_TIMEOUT_S_DEFAULT=5.0` (:205) and the honesty block (:207-217 — **Phase 2 refreshes the wording only**; this phase does not touch it).
- `daemon/api.py` — `:3939-3987` (probe wiring, `manager.engine.pool` access shape, `resolve_psycopg_pool`), `:5049-5087` (the no-composite `boot_incomplete` branch).
- `daemon/persistence.py` — `:231-257` (A4 knob ladder), `:316-331` (saver pool construction), `:341-359` (asyncpg adapter pool).
- `scripts/upgrade/lib.sh` — the soak semantics (3 consecutive greens; never-green unconditional at cap-reach).
- `daemon/write_pause_guard.py` — untouched (F8.9).
- `daemon/manager.py` — outside the allowed region (coupling C5).
- `daemon/tools/instance.py` — outside the bus-watch site (C-6).

**Permitted change in `daemon/services/readiness.py` for this phase: the bodies of `make_db_probe` and `make_queue_probe` only** (T1.4, T1.5), plus the module's own timeout constants **if** a new derived constant is introduced (the existing `DB_PROBE_TIMEOUT_S` / `QUEUE_PROBE_TIMEOUT_S` values are **not** changed).

**Permitted change in `daemon/manager.py` for this phase: the allowed region only** (coupling C5).

**Permitted change in `daemon/tools/instance.py` for this phase: the bus-watch site only** (`:1207/:1360`), or the residual is disclosed (AC31).
