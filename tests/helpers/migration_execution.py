"""apply_migration-based migration-execution helpers (group-chat P3).

The O-1/O-5 carry-over re-basing: migration assertions that used to
string-match the .sql text now EXECUTE the real file through the real
runner (``MigrationRunner.apply_migration`` — the anchor; there is NO
``_executable_sql`` to hang pins on) and assert on the LIVE schema the
execution produced (PRAGMA inspection on SQLite).

Style follows the ``tests/helpers`` convention (plain functions; the
pytest fixtures stay in the test files and delegate here — see
``room_harness.py`` for the sibling pattern).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from daemon.migrations.runner import MigrationFile, MigrationRunner

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "daemon"
    / "migrations"
    / "versions"
)

CHATROOM_MIGRATION_FILENAME = "20261115_080000_create_chatroom_tables.sql"
PROPOSAL_DEDUP_MIGRATION_FILENAME = (
    "20261115_120000_add_room_proposal_dedup_columns.sql"
)

__all__ = [
    "MIGRATIONS_DIR",
    "CHATROOM_MIGRATION_FILENAME",
    "PROPOSAL_DEDUP_MIGRATION_FILENAME",
    "parse_migration",
    "apply_migration_file",
    "sqlite_columns",
    "sqlite_indexes",
    "sqlite_foreign_keys",
    "table_exists",
]


def parse_migration(filename: str) -> MigrationFile:
    """Parse a real migration file from the repo's versions dir."""
    return MigrationFile.parse(MIGRATIONS_DIR / filename)


def apply_migration_file(engine: Engine, filename: str) -> MigrationRunner:
    """Apply ONE real migration file through ``MigrationRunner``.

    The runner creates the ``schema_migrations`` ledger on first use;
    the migration's UP SQL is executed for real (statement splitter,
    idempotent-skip branch, ledger row). Returns the runner (the
    caller can inspect ``get_applied_versions`` / roll back).
    """
    migration = parse_migration(filename)
    runner = MigrationRunner(engine, migrations_dir=MIGRATIONS_DIR)
    runner.ensure_migrations_table()
    runner.apply_migration(migration)
    return runner


def sqlite_columns(engine: Engine, table: str) -> list[dict[str, Any]]:
    """``PRAGMA table_info`` as dicts (cid/name/type/notnull/dflt/pk)."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).mappings().all()
    return [dict(r) for r in rows]


def sqlite_indexes(engine: Engine, table: str) -> dict[str, dict[str, Any]]:
    """``PRAGMA index_list`` + ``index_info`` →
    ``{index_name: {"unique": bool, "columns": [...]}}``."""
    out: dict[str, dict[str, Any]] = {}
    with engine.connect() as conn:
        idx_rows = conn.execute(text(f"PRAGMA index_list({table})")).mappings().all()
        for idx in idx_rows:
            name = idx["name"]
            cols = [
                r["name"]
                for r in conn.execute(
                    text(f"PRAGMA index_info({name})")
                ).mappings().all()
            ]
            out[name] = {"unique": bool(idx["unique"]), "columns": cols}
    return out


def sqlite_foreign_keys(engine: Engine, table: str) -> list[dict[str, Any]]:
    """``PRAGMA foreign_key_list`` as dicts."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA foreign_key_list({table})")).mappings().all()
    return [dict(r) for r in rows]


def table_exists(engine: Engine, table: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
            ),
            {"t": table},
        ).first()
    return row is not None
