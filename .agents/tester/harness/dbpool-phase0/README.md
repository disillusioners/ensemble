# Phase-0 empirical baseline harness — db-pool idle-in-transaction lane

**NOT SHIPPED.** This directory is measurement scaffolding committed under
`.agents/tester/harness/` for the `fix/db-pool-idle-txn` lane's Phase 0.
It contains **zero production code** and is never imported by the daemon.

## What it is

Five scripts that MEASURE the base tree's PostgreSQL behaviour at commit
`19fc11c8b52239813fd91327ee0c1961c5b6fc87` (= `origin/latest` = the staged
v0.27.11 soak-gate tree). They exist because the lane's plan council had no
database access, so its PostgreSQL semantics claims are documentation-only.

| Script | Target | Headline result |
|---|---|---|
| `t1_mr3_hold.py` | T1 — M-R3 hold duration under load | the hold EQUALS the awaited content-fetch latency; the sync twin's ceiling is the 8s bridge |
| `t2_stat_activity.py` | T2 — what `pg_stat_activity` actually shows | a checked-out-before-first-statement backend is `idle`, NOT `idle in transaction` → an idle-in-txn census is BLIND to it |
| `t3_pool_attribution.py` | T3 — pool attribution | 11 pool-creation sites; no `application_name` at base; the shared-constant ordering hazard is CONFIRMED |
| `t4_fingerprints.py` | T4 — incident fingerprints | all four reproduce byte-exactly; the readiness probe reads `FROM task`, not `job_queue_items` |
| `t5_fix_sites.py` | T5 — F3/F4/F6 fix-site behaviour | the aborted-connection hand-off is REFUTED for the pool path; the incident's journal quote is a same-connection cascade |

## Running it

```bash
# 1. provision the lane-owned scratch DB (operator, once)
sudo -n -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DROP DATABASE IF EXISTS ensemble_dbpool_scratch_phase0;
DROP ROLE IF EXISTS dbpool_phase0;
CREATE ROLE dbpool_phase0 LOGIN PASSWORD 'phase0_scratch_pw';
CREATE DATABASE ensemble_dbpool_scratch_phase0 OWNER dbpool_phase0;
CREATE ROLE dbpool_phase0_role LOGIN PASSWORD 'role_pw';
ALTER ROLE dbpool_phase0_role SET application_name = 'from-role-default';
SQL

# 2. run
bash .agents/tester/harness/dbpool-phase0/run_all.sh

# 3. teardown
sudo -n -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DROP DATABASE IF EXISTS ensemble_dbpool_scratch_phase0;
DROP ROLE IF EXISTS dbpool_phase0;
DROP ROLE IF EXISTS dbpool_phase0_role;
SQL
```

## Fences (must hold for every script here)

* **Scratch DB only.** Every connection targets `ensemble_dbpool_scratch_phase0`
  via the explicit `DBPOOL_SCRATCH_*` variables. The harness NEVER reads
  `POSTGRES_*` from the ambient environment. On this host the ambient
  environment points at a REMOTE `10.44.0.2` / `ensemble_prod` — importing it
  would hit production. (`env -u POSTGRES_*` is a silent no-op; bash does not
  glob env names. The harness sidesteps the idiom entirely by never reading
  those names.)
* **No live contact.** No daemon boot, no restart, no signal, no
  promote/rollback/stage script, no live install path, no `ensemble_prod`.
* **Certified interpreter.** `/home/nea/ensemble-src/.venv/bin/python`
  (pytest 9.0.2, psycopg 3.3.4, SQLAlchemy 2.0.48, asyncpg 0.31.0).
  NOT `uv run` (uv is off-PATH on this host) and NOT `/usr/bin/python3`
  (no pytest).
* **Dual-layer timeout.** The runner wraps each script in `timeout 280`
  (command level); each script carries its own internal budget.
* **No `$HOME`-derived paths.** The harness resolves nothing from `HOME`.

## Fidelity notes (what is REAL vs MODELLED)

* **REAL:** every PostgreSQL statement, every `pg_stat_activity` row, every
  pool configuration, the SQLAlchemy/psycopg/asyncpg client behaviour, the
  real daemon SQL (the real `Instance` / `MissionRecord` models, the real
  `SQLModelInstanceRepository.list`, the real readiness probe builder, the
  real `MissionRecordService`).
* **MODELLED, and disclosed as such:**
  * the python-side message-filtering half of the T1 content fetch (the PG
    read half is real);
  * the "1.0s fetch tail" regime (an injected delay standing in for a larger
    instance history);
  * the event-loop peg (a CPU burner coroutine standing in for the incident's
    104% single-core loop).
* **Engine configs** are replicated byte-for-byte from the base tree:
  * manager engine — `daemon/repositories/factory.py:323-338`
  * repair engine — `daemon/tools/ens_db_tools.py:604-633`
  * asyncpg pool — `daemon/persistence.py:355-359`
  * P2 psycopg saver pool — `daemon/persistence.py:316-331`

## Raw evidence

`raw/*.out` holds the verbatim stdout of each script at the recorded run.
Re-running overwrites them; the committed copies are the ones the baseline
report cites.

## Reuse by the lane's pins

The lane's F7 regression guard (and the F3/F4/F6 pins) can import
`common.py` for `make_manager_engine()`, `make_repair_engine()`,
`activity()` and `activity_by_pid()` — all of which point at the scratch DB.
The pins MUST NOT point at `ensemble_dev` (its schema is shared and must not
be mutated) nor at `ensemble_prod`.
