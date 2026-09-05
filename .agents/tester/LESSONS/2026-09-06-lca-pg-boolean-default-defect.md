# LCA Gate 2026-09-06: PG-invalid boolean default — three registration sites, two wrong, guard vacuous

## Defect
`completion_gate_escalated` (Boolean) registered with int-literal defaults at 2 of 3 sites on feature/leader-completion-attestation (commit 871da567, phase 3):

- `daemon/repositories/instance/models.py:112` — `server_default=text("0")` on `Column(Boolean)` → **live PG break**: `SQLModel.metadata.create_all()` emits `BOOLEAN NOT NULL DEFAULT 0`; PostgreSQL rejects implicit int→bool in DDL default context (`psycopg.errors.DatatypeMismatch`, SQLSTATE 42P16).
- `daemon/migrations/versions/20260905_000001_attestation_ledger_columns.sql:54` — `BOOLEAN NOT NULL DEFAULT 0` (latent: file dialect-gated SQLite-only per its header; still contradicts its own "PG+SQLite portable" claim).
- `daemon/manager.py:4774` — `DEFAULT FALSE` ✅ (proves the correct form was known and used one site over).

## Empirical proof
`tests/integration/test_message_metadata_send_message_revive.py` 2 nodes fail deterministically on real PG with exactly that DatatypeMismatch. They pass at base e866c116 (column is branch-added). SQLite's loose typing masked it: all 313 attestation-matrix tests green on SQLite.

## Why tests missed it (guard vacuity)
`tests/migration/test_attestation_migration.py::TestMigrationIsPgSqliteSafe` (17 params, green) = 7-substring grep for PG-only syntax (DROP CONSTRAINT/USING gin/::regclass/JSONB/gen_random_uuid/RETURNS TRIGGER/EXECUTE PROCEDURE). It checks the fresh-SQLite direction only; no default-literal/type agreement check, no ORM server_default inspection. `test_completion_gate_escalated_default_is_false` asserts only `default is not None` on a SQLite inspector despite its name.

## Fix direction (2 lines + follow-up)
1. `models.py:112`: `server_default=text("0")` → `text("false")` (or `false()`).
2. migration `:54`: `DEFAULT 0` → `DEFAULT FALSE`.
3. Follow-up: extend the migration guard with a default-literal/type-agreement check or compile the DDL against a PG dialect in-test.

## Lesson (generalizable)
**Every new column registration must be checked at ALL registration sites, not one.** This repo registers columns in up to three places (ORM model, SQL migration, manager bootstrap ALTER). A correct literal at one site masks wrong literals at the others. And: **a dialect-portability guard that only greps one direction (PG-only syntax) cannot catch type-agreement defects that only the OTHER dialect rejects** — boolean-int defaults explode on PG while passing on SQLite. Green SQLite tests are not PG-safety evidence.
