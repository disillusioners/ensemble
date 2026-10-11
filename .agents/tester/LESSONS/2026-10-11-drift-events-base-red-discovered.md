# drift_events ensure-columns base RED — discovered during blobprune real-PG gate (2026-10-11)

## Context
REAL-PG gate for `feature/blob-prune-default-on` @ `0a2e78a0a` (base `1bab756c6`), worktree
`/home/nea/ensemble-src-wt-blobprune-default`. Suite `tests/integration/test_maintenance_checkpoint_cleanup_api.py`
ran live on disposable PG16 :15435 (env-scrubbed, `PG_TEST_*` wiring, 47 collected under
`-m "integration and postgres"`): **45P / 1F / 1S** — the 1S is the suite's documented designed skip
(dual-dialect partial-index render works on SQLite, case 64 covers primary path).

## The RED
`TestExpectedBytesBigIntegerIntegration::test_ensure_postgres_columns_widens_existing_int4_column` (:1016):
```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedTable) relation "drift_events" does not exist
[SQL: CREATE INDEX IF NOT EXISTS ix_drift_events_plugin ON drift_events(plugin)]
```
raised from `daemon/manager.py` `_ensure_postgres_columns` (:7639 @ HEAD).

## Root cause (adjudicated, NOT fixed — report-only gate)
- The `ix_drift_events_plugin` DDL was introduced by commit **`27b48af20`** ("fix(migrations): drift_events
  index alignment + PG ensure path"), which is an **ancestor of base `1bab756c6`**
  (`git merge-base --is-ancestor 27b48af20 1bab756c6` → yes).
- The failing test's fixture builds a **minimal legacy schema without `drift_events`**; the ensure path now
  unconditionally emits `CREATE INDEX … ON drift_events(plugin)` against it.
- The blobprune branch does **not touch `daemon/manager.py`** and its 122-line suite-file edit has no hunk
  near :1016.

## A/B proof (lane standard: node-for-node base reproduction)
Temp detached worktree `/tmp/blobprune-base-ab` @ `1bab756c6` (own uv venv, `daemon.__file__` import-proved
inside the temp worktree — the known editable-install trap), disposable PG16 :15440, single-node run:
**byte-identical failure** (same error class, same relation, same DDL). `1F @ HEAD ≡ 1F @ base`.
⇒ Pre-existing base defect, NOT a blobprune regression. Quarantined (QUARANTINE.md Active, 2026-10-11 row).

## Consequences for future gates
- **Do not re-litigate** this RED at future gates until a fix commission lands (row marked in QUARANTINE.md).
- The prior-art `46P+1S/47` baseline for `int4_pg_api` (PACKS.md:669) **predates `27b48af20`** — it is no
  longer a valid expectation; current shape is 45P/1F(quarantined)/1S.
- Suggested fix (separate commission, production-side): `_ensure_postgres_columns` must create `drift_events`
  before indexing it, or gate the index DDL on table existence (pg_class/pg_tables probe). Alternatively a
  test-side fixture addition — but the ensure-path emitting DDL for a table the schema history doesn't
  guarantee smells like a real boot-order hazard on minimal/legacy DBs, so production-side is the better fix.

## Incident-adjacent note
Same gate observed QUARANTINE row #15 (real_saver 2-node caplog pair, quarantined 2026-09-26) **passes
on-branch** — the branch (or an intermediate commit) appears to have fixed the logger mismatch. Owner may
un-quarantine after the 3×-clean-run rule; NOT done by this gate (report-only).
