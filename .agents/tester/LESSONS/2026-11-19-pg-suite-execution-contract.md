# Lesson: PG-suite execution contract — markers, forbidden DBs, and the ensemble_test throwaway — 2026-11-19

**Context:** wake-path-carrier-fix tester gate (wi-8f60e6f1) — the MANDATORY PostgreSQL list was executed for real on PG 16.15 (proving the "no PG on this box" claim false: `pg_isready` rc 0, `current_database()=ensemble_dev` via psycopg3).

**Contract discovered (all evidence in RESULTS/2026-11-19-wake-path-carrier-fix-tester-gate.md §2.4):**

1. **Vacuous-deselect trap:** repo `addopts` DESELECT the `postgres` marker. A plain `pytest tests/postgres/test_x_pg.py` reports `6 deselected … EXIT:5` — vacuous unless run with:
   `--override-ini="addopts=" -m postgres`
   (Same family as PACKS.md:1249,1459.)

2. **`ensemble_dev`/`ensemble_prod` are HARD-FORBIDDEN as PG test targets** — `tests/postgres/conftest.py:141-147` (`FORBIDDEN_DB_NAMES`, unconditional; `PG_TEST_ALLOWED_DBS` cannot widen). Reason: the session `pg_engine` runs `SQLModel.metadata.drop_all(engine)` at setup AND teardown — targeting `ensemble_dev` would drop its 111 tables twice. The sanctioned throwaway is **`ensemble_test`** (allowlisted, `CO_OWNED_LEGACY_THROW_AWAYS`; measured 0 tables before, restored to 0 after). Run as least-privilege role `ensemble_dev_app`, NOT the conftest default role `ensemble` (co-owns `ensemble_prod`).

3. **Reachability probes ≠ execution targets:** probe connectivity read-only against `ensemble_dev` to prove the fence; execute suites against `ensemble_test`. Never conflate the two — a commission that says "touch only ensemble_dev" for PG SUITES is unsafe by construction in this repo.

4. **Dialect-probe scratch discipline:** marker-prefixed rows (`gate_probe_<lane>_%`), DELETE with rowcount proof at the end (this gate: 12 inserted → count 0), report `public_tables` before/after for both DBs.

Related skill: `pg-suite-gate-preflight` (id d0932d93, created by worker gate-pg during this gate).
