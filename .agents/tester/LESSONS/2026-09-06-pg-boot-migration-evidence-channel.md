# PG boot smokes: the .sql migration file is the SQLite companion — catalog query is the evidence channel

Discovered: 2026-09-06, post-merge boot smoke on `latest` @ 42cb9518 (worker efb17a71).

## Trap

Expecting a per-migration "applying/applied" log line for a shipped `.sql` migration (e.g. LCA's `20260905_000001_attestation_ledger_columns.sql`) during a **PostgreSQL** daemon boot will always fail:

- The SQL migration runner logs `Skipping migrations for non-SQLite database (schema evolution handled by EnsembleManager._ensure_postgres_columns)` and does nothing on PG.
- PG schema evolution happens in the dual-driver ensure-path (`daemon/manager.py` ~4830/4842: `ALTER TABLE instances ADD COLUMN IF NOT EXISTS …`), which logs **nothing per statement**.

## Correct evidence channel

Read-only PG catalog query after boot, against the table the migration/ensure-path targets. Example proving the LCA ledger columns landed PG-valid on `ensemble_dev`:

```
attestation_denied_count | integer | 0
completion_gate_escalated | boolean | false
```

Strength check: query a table with pre-existing rows (instances had 138) — a populated table proves `create_all` is not the author, so the ensure-path of THIS boot applied the column. `boolean`/`false` (not `integer`/`0`) also directly re-proves the 6ab16261 hotfix class (PG-invalid boolean integer default) at every boot smoke.

## Rules

1. Boot-smoke briefs for PG must specify catalog-query evidence for schema assertions — never "grep the apply line".
2. Fresh-SQLite boots remain broken (migration 20260714_000001 PG-only syntax — see 2026-09-04 lesson); the SQLite companion path is not smoke-testable today.
3. Log forensics unchanged: time-bracket windows, never line-number windows.
