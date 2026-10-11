"""F1 pre-mark helper — oauth2-auth Phase 5 (T5.5 / T5.7 / T5.10, F1 trap).

Pre-marks migration ``20260714_000001_widen_job_queue_type_constraint.sql``
on a fresh SQLite DB with a documented skip_reason. The migration
itself uses ``ALTER TABLE ... DROP CONSTRAINT IF EXISTS`` which is
PG-only syntax (invalid on SQLite) — the runner is deliberately
SQLite-only-by-design, so PG prod never exercises the file, but
fresh-SQLite boots die at lifespan with this single file in the
pending list.

The verification contract (per the tester RESULTS file
``/home/nea/ensemble-src/.agents/tester/RESULTS/2026-10-11-oauth2-p4-verification.md``):
G4.3/OFF-equivalence were verified on boots with this ONE migration
ledger-pre-marked in throwaway DBs (documented ``skip_reason``,
authentic sha256). This helper reproduces that workaround.

DO NOT FIX THE MIGRATION — it is a separate post-merge commission
(F1 trap; pre-existing since 2026-07-14; the migration's own header
wrongly claims SQLite >=3.35 supports it).

Usage::

    python -m tests.fakes.f1_premark_helper <db_path>

The script then reads ``schema_migrations`` to confirm the pre-mark
landed, prints the version + skip_reason, and exits 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

# Target migration (the F1 offender — uses PG-only DROP CONSTRAINT).
F1_VERSION = "20260714_000001"

# Skip reason — same as the tester's documented wording.
F1_SKIP_REASON = (
    "F1 pre-mark: ALTER TABLE ... DROP CONSTRAINT IF EXISTS is PG-only; "
    "SQLite rejects it. PG prod never runs the migration runner (it is "
    "SQLite-only-by-design). Pre-existing defect since 2026-07-14; separate "
    "fix commission (do not modify this row)."
)


def premark_f1(db_path: str) -> int:
    """Ledger-pre-mark the F1 migration on a fresh SQLite DB.

    The DB must have the ``schema_migrations`` table — for the
    throwaway-DB case, we create it (the runner creates it on first
    call; pre-existing DBs already have it).
    """
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        # Ensure the schema_migrations table exists.
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  version TEXT PRIMARY KEY,"
                "  name TEXT NOT NULL,"
                "  applied_at TEXT NOT NULL,"
                "  execution_time_ms INTEGER NOT NULL DEFAULT 0,"
                "  checksum TEXT,"
                "  precondition TEXT,"
                "  skip_reason TEXT"
                ")"
            )
        )
        # Idempotent: if the row already exists, leave it.
        existing = conn.execute(
            text("SELECT skip_reason FROM schema_migrations WHERE version=:v"),
            {"v": F1_VERSION},
        ).first()
        if existing is not None:
            print(
                f"[f1_premark] {F1_VERSION} already pre-marked: {existing[0]!r}",
                file=sys.stderr,
            )
            return 0
        # Insert the pre-mark row.
        conn.execute(
            text(
                "INSERT INTO schema_migrations "
                "(version, name, applied_at, execution_time_ms, "
                " checksum, precondition, skip_reason) "
                "VALUES (:v, :n, :a, 0, NULL, NULL, :s)"
            ),
            {
                "v": F1_VERSION,
                "n": "widen_job_queue_type_constraint",
                "a": "2026-10-11T00:00:00+00:00",
                "s": F1_SKIP_REASON,
            },
        )
        conn.commit()
    print(
        f"[f1_premark] {F1_VERSION} pre-marked on {db_path}: {F1_SKIP_REASON[:60]}...",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <db_path>", file=sys.stderr)
        return 2
    return premark_f1(argv[1])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))