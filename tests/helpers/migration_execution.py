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
RESOLUTION_WARNINGS_MIGRATION_FILENAME = (
    "20261116_083000_add_room_resolution_warnings.sql"
)

#: The PRAGMA shape the migration-execution engines carry. The
#: ``foreign_keys`` pragma is the LOAD-BEARING one (the FK shape is
#: part of what the executed-schema pins assert); ``busy_timeout`` is
#: the lock-wait bound.
#:
#: M-ro4: the two migration-test modules each built their OWN engine
#: with the SAME boilerplate (file-backed SQLite + the FK pragma ON);
#: the builder now lives HERE, once, and the per-call-site differences
#: (the busy_timeout) are an explicit parameter. The equivalence proof
#: (``test_the_consolidated_engine_pragmas_match_the_per_file_engines``
#: in the runner-execution module) pins that the consolidated engine's
#: PRAGMA state is EXACTLY the state the two per-file engines had.
MIGRATION_ENGINE_FOREIGN_KEYS = 1
MIGRATION_ENGINE_BUSY_TIMEOUT_MS = 10000
#: SQLite's own default busy timeout (5 s) — the shape the
#: models-and-migration module's per-file engine had (it set the FK
#: pragma only).
SQLITE_DEFAULT_BUSY_TIMEOUT_MS = 5000

__all__ = [
    "MIGRATIONS_DIR",
    "CHATROOM_MIGRATION_FILENAME",
    "PROPOSAL_DEDUP_MIGRATION_FILENAME",
    "RESOLUTION_WARNINGS_MIGRATION_FILENAME",
    "MIGRATION_ENGINE_FOREIGN_KEYS",
    "MIGRATION_ENGINE_BUSY_TIMEOUT_MS",
    "SQLITE_DEFAULT_BUSY_TIMEOUT_MS",
    "parse_migration",
    "apply_migration_file",
    "make_migration_engine",
    "sqlite_columns",
    "sqlite_indexes",
    "sqlite_foreign_keys",
    "sqlite_pragma",
    "table_exists",
]


def make_migration_engine(
    tmp_path,
    name: str = "room_migration.db",
    *,
    busy_timeout_ms: int | None = MIGRATION_ENGINE_BUSY_TIMEOUT_MS,
) -> Engine:
    """The ONE migration-execution engine builder (M-ro4).

    A fresh file-backed SQLite engine with the per-connection PRAGMAs
    the migration-execution pins need — ``foreign_keys=ON`` (the
    load-bearing one: the FK shape is part of what the executed-schema
    pins assert) and, when asked, a ``busy_timeout`` bound. NO
    ``create_all``: everything the migration-execution tests see is
    what the RUNNER's execution built.

    ``busy_timeout_ms=None`` reproduces the SHAPE of the
    models-and-migration module's original per-file engine (FK pragma
    only — SQLite's own 5 s default busy timeout); the default value
    reproduces the runner-execution module's original shape (FK pragma
    + a 10 s busy timeout). The equivalence proof pins both.
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import NullPool

    eng = create_engine(
        f"sqlite:///{tmp_path}/{name}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        if busy_timeout_ms is not None:
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        cursor.close()

    return eng


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


def sqlite_pragma(engine: Engine, name: str) -> Any:
    """Read ONE PRAGMA value (the equivalence-proof probe).

    ``PRAGMA foreign_keys`` → 0/1; ``PRAGMA busy_timeout`` → the
    millisecond bound. The probe runs on a FRESH connection (the
    pragmas are per-connection), so it reflects what every consumer
    connection of the engine sees.
    """
    with engine.connect() as conn:
        row = conn.execute(text(f"PRAGMA {name}")).first()
    if row is None:
        return None
    return row[0]
