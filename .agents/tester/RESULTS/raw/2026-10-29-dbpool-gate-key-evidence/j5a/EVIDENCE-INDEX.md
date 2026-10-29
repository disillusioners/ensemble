# J5(a)+(d)+(f) evidence index — db-pool idle-in-transaction functional gate leg

Date: 2026-10-29. Executor: tester leg J5a (sole writer of wt-tdbp-j5a / wt-tdbp-j5a-base scratch).
BASE = 19fc11c8b52239813fd91327ee0c1961c5b6fc87. HEAD = 4cc63574a3d816a81a00907437aba6562f3ddde1.
Scratch PG: 16.15 on 127.0.0.1:5441 (role dbpool_g5a, db dbpool_g5a + ensemble_dbpool_scratch_phase0 for t5's hardcoded a5 dsn). TEARDOWN COMPLETE: both dbs dropped, role dropped, cluster stopped, PGDATA removed, port closed.

## Files

| File | What it is |
|---|---|
| `t1_base.out` / `t1_head.out` | Phase-0 harness t1 (instrument of record; script-replicated M-R3 shape) — BASE reproduces Phase-0 (L5 median hold 1028.265 ms vs await 1025.372 ms; sync twin 8008.797 ms; pg_state during await `idle in transaction` 30/30 L0, 15/15 L5). HEAD run measures the SAME replicated shape (1028.966 ms) — instrument limitation, NOT HEAD daemon behaviour. |
| `realcode_base.out` + `realcode_BASE.jsonl` | REAL `InstanceManager._reconcile_deferred_report_async` at BASE, slowed fetch 2.0 s: fetch window 2002.292 ms; checkedout peak during fetch **1**; **39/39 window samples `idle in transaction`**; max xact_age_s **2.0078**; full per-sample trail in the jsonl. |
| `realcode_head.out` + `realcode_HEAD.jsonl` | Same driver at HEAD: fetch window 2002.389 ms; checkedout peak during fetch **0**; **0 idle-in-txn samples** (39 window samples, only `idle` pooled backend, xact_start NULL / max xact_age_s null); artefacts still land (message 1 / task 1). THE HOLD COLLAPSED. |
| `pack_reconcile_head.out` | Lane pack `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` at HEAD — **7 passed** in 16.37 s (F8-b/c/d/e + 2 carrier-dimension + missing-child). |
| `t5_base.out` / `t5_head.out` | Phase-0 harness t5. BASE b3: timeout **0.5011 s**, checkedout_right_after **15** (stranded pool-waiter). HEAD-run b3 identical (0.5009 s / 15) — the instrument replicates the OLD probe shape inline; real-probe evidence is `pack_starvation_head.out` + `probe_numeric_head.out`. Also reproduces a2/a3/a4 pool-reset behaviour and the a5 wedge-capture same-connection cascade (`reproduces_incident_journal_quote: true`). |
| `pack_starvation_head.out` | `tests/postgres/test_dbpool_probe_starvation_pg.py` at HEAD — **2 passed** in 3.36 s (F4-f). |
| `pack_probe_bound_head.out` | `tests/unit/tools/test_dbpool_probe_bound.py` at HEAD — **13 passed** in 0.50 s (F4-a). |
| `probe_numeric_head.out` | REAL `make_db_probe` at HEAD under 15 holders: synchronous **TimeoutError** refusal in **84 µs** ('manager pool at configured capacity (15/15)'), thread join 0.62 ms, checkedout stays 15, no slot taken; below-capacity probe OK (2.45 ms, verdict true, no leak). |
| `factory_base.out` / `factory_head.out` | REAL `create_postgres_engine` runtime kwargs + aborted-txn return probe. BASE: recycle -1 / timeout 30.0 (library defaults), reset_rollback (library default), NO application_name. HEAD: **pool_recycle 3600, pool_timeout 30 explicit, reset_on_return explicit, application_name=ensemble-manager** in conninfo. Aborted-txn probe: CLEAN BOTH sides (before: `idle in transaction (aborted)` txn_status 3; after return: `idle` last_query ROLLBACK; fresh borrower same backend: in_transaction False, txn_status 0, SELECT 42 OK). |
| `driver_*.py` | The three measurement drivers (method, verbatim). |
| `initdb.log` / `pg.log` | Cluster provisioning + server log. |

## Verdicts

- **(a) PASS** — BASE hold == awaited fetch (real code: 39/39 idle-in-txn, xact_age max 2.0078 s vs fetch 2002 ms; instrument: 1028/1025 ms). HEAD: hold structurally GONE (0 checkedout across the await; 0 idle-in-txn; pack 7/7). Fetch is MODEL-INJECTED (async sleep), disclosed; the flip condition's decision instrument (real fetch-latency distribution) remains UNMEASURED per DR-30.
- **(d) PASS** — BASE: stranded pool-waiter (timeout 0.5011 s, checkedout 15 not 16). HEAD: synchronous TimeoutError refusal in 84 µs, no blocking checkout, checkedout 15, thread terminated 0.62 ms; packs 2/2 + 13/13.
- **(f) PASS with correction** — HEAD kwargs verified (reset_on_return=rollback explicit, recycle 3600, timeout 30, application_name=ensemble-manager). The brief's BASE expectation (aborted state survives return) is REFUTED by measurement: the pool path is clean at BASE too (SQLAlchemy 2.0.48 library default reset_rollback) — matches the lane's Phase-0 BASELINE.md. The incident's journal quote reproduces as a SAME-CONNECTION cascade (t5 a5), not a pool hand-off.
