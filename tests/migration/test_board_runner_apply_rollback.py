"""Board migration — the REAL ``MigrationRunner`` lifecycle (ari-tools P1).

This file is the RUNNER-lifecycle counterpart to
``tests/migration/test_board_index_parity.py``:

* the parity file pins the THREE index sites (the model
  ``__table_args__`` / the ``.sql`` / the expected set) and applies the
  ``.sql`` VERBATIM as raw SQL;
* THIS file drives the REAL
  :class:`daemon.migrations.runner.MigrationRunner` — a real
  SQLAlchemy engine, the real ``schema_migrations`` ledger, the real
  ``run_pending_migrations`` / ``rollback_migration`` calls. No service
  mocks, no monkeypatching of the runner, no fake ledger.

What it proves:

(a) BOOT-ORDER FIDELITY — ``SQLModel.metadata.create_all`` FIRST and
    the runner AFTER it (the real daemon boot order, pinned in
    ``daemon/manager.py``). The apply must SUCCEED and must leave a
    ledger row for ``20261020_060000``, the three board tables, and
    exactly the six declared board indexes — and no auto-named ``ix_*``
    index on any board table;
(b) IDEMPOTENCY — a second ``run_pending_migrations()`` is a no-op: no
    error, no second ledger row, no index drift;
(c) ROLLBACK — ``rollback_migration('20261020_060000')`` executes the
    ``-- DOWN`` section: the three tables AND the six indexes are gone,
    the ledger row is removed, and the ``schema_migrations`` ledger
    table itself survives;
(d) ROUND-TRIP — a re-apply after the rollback brings every object
    back, and the re-created schema still matches the models
    column-for-column (after the rollback the tables are created BY THE
    ``.sql``, not by ``create_all`` — so this is a real parity proof);
(e) the FULL migration dir cannot reach the board file on SQLite, and
    the reason is PRE-EXISTING, not the board file.

WHY A SCOPED MIGRATIONS DIR FOR (a)-(d)
---------------------------------------
The apply/rollback lifecycle runs the runner over a migrations dir that
contains ONLY the board ``.sql`` (a byte-identical copy under
``tmp_path``). Reason: the FULL dir cannot reach the board file on
SQLite, because TWO pre-existing defects sit in FRONT of it:

* ``20260714_000001_widen_job_queue_type_constraint.sql`` —
  ``ALTER TABLE ... DROP CONSTRAINT IF EXISTS`` is a SQLite SYNTAX
  error (SQLite has no ``DROP CONSTRAINT``); it aborts the whole run;
* the DUPLICATE version prefix ``20261018_000001`` — two files share
  the prefix and ``schema_migrations.version`` is a PRIMARY KEY, so the
  second INSERT raises ``IntegrityError: UNIQUE constraint failed``.

Both are PRE-EXISTING and are NOT board defects — they reproduce with
the board file absent (see ``TestFullMigrationDirReality``, which
asserts exactly that: the run dies BEFORE the board file is reached and
the board version never lands in the ledger).

``TestFullMigrationDirReality`` additionally proves the POSITIVE case in
the FULL dir: with the two known blockers ledger-pre-marked (the
documented workaround — a ledger row with a ``skip_reason``, which makes
the runner skip that version forever), the board file applies cleanly
inside the real 96-migration sequence.

SAFETY FENCE: every database is a FRESH file directly under pytest's
``tmp_path`` — no HOME-derived, live or service-shaped path is ever
resolved, and no live database is written. The fence is asserted in
:func:`_fresh_engine`, not merely intended.
"""

from __future__ import annotations

import datetime
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

# Importing the board models registers the three board tables (and the
# ``projects`` table the ``project_id`` FK points at) on
# ``SQLModel.metadata`` — the same late-import the daemon boot performs
# in ``daemon/manager.py`` BEFORE ``create_all``.
from daemon.migrations.runner import MigrationError, MigrationRunner
from daemon.models.board import BoardItem, BoardItemTag, BoardNote
from daemon.repositories.project.models import Project  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[2]
BOARD_MIGRATION = (
    REPO_ROOT
    / "daemon/migrations/versions/20261020_060000_create_board_tables.sql"
)
BOARD_VERSION = "20261020_060000"

#: The three board tables of phase1-plan §4.1.
BOARD_TABLES = ("board_items", "board_notes", "board_item_tags")

#: The six declared board indexes — the expectation this file asserts
#: against. The lockstep with the model and with the ``.sql`` is
#: re-verified AT RUNTIME below (not merely assumed), so a drift in
#: either of the other two sites turns this file RED too.
EXPECTED_INDEXES: dict[str, tuple[str, list[str]]] = {
    "idx_board_items_status_updated": (
        "board_items",
        ["status", "updated_at"],
    ),
    "idx_board_items_updated_at": ("board_items", ["updated_at"]),
    "idx_board_items_project_id": ("board_items", ["project_id"]),
    "idx_board_items_due_at": ("board_items", ["due_at"]),
    "idx_board_notes_item_id_ts": ("board_notes", ["item_id", "ts"]),
    "idx_board_item_tags_tag_item": (
        "board_item_tags",
        ["tag", "item_id"],
    ),
}

#: SQLite materialises an implicit index for every PRIMARY KEY that is
#: not an INTEGER rowid alias. Two board tables qualify: ``board_items``
#: (TEXT PK) and ``board_item_tags`` (composite PK ``(item_id, tag)``).
#: These are SQLite INTERNALS, not declared indexes, so they are counted
#: separately from the six — the accounting is explicit rather than a
#: silent filter.
SQLITE_INTERNAL_INDEX_PREFIX = "sqlite_autoindex_"
EXPECTED_SQLITE_INTERNAL_INDEXES = {
    "sqlite_autoindex_board_items_1",
    "sqlite_autoindex_board_item_tags_1",
}

#: The PRE-EXISTING SQLite blockers that sit in front of the board file
#: in the full migration dir. Documented in the module docstring.
KNOWN_SQLITE_BLOCKERS = {
    "20260714_000001": (
        "ALTER TABLE ... DROP CONSTRAINT IF EXISTS is invalid on SQLite"
    ),
    "20261018_000001": (
        "duplicate migration version prefix -> UNIQUE constraint on "
        "schema_migrations.version"
    ),
}


# ── helpers ──────────────────────────────────────────────────────────────


def _fresh_engine(tmp_path: Path, name: str) -> Engine:
    """Create an engine on a FRESH sqlite file directly under ``tmp_path``.

    The fence is ASSERTED, not merely intended: the file must not exist
    yet and must sit directly in the pytest tmp dir (never a HOME-derived
    or live-shaped path).
    """
    tmp_root = tmp_path.resolve()
    db_path = tmp_root / name
    assert db_path.parent == tmp_root, f"fence violation: {db_path}"
    assert db_path.suffix == ".db", f"fence violation: {db_path}"
    assert not db_path.exists(), f"fence violation: {db_path} already exists"
    return create_engine(f"sqlite:///{db_path}")


def _board_table_names(conn) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('board_items','board_notes','board_item_tags')"
        )
    ).fetchall()
    return {row[0] for row in rows}


def _board_index_names(conn) -> set[str]:
    """Every index row in ``sqlite_master`` that belongs to a board table.

    ``tbl_name`` is the OWNING TABLE, so this picks up both the six
    declared ``idx_*`` indexes AND SQLite's internal ``sqlite_autoindex_*``
    primary-key indexes.
    """
    rows = conn.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN "
            "('board_items','board_notes','board_item_tags')"
        )
    ).fetchall()
    return {row[0] for row in rows}


def _split_board_indexes(names: set[str]) -> tuple[set[str], set[str]]:
    """Split board index names into (declared, sqlite_internal)."""
    declared = {n for n in names if not n.startswith(SQLITE_INTERNAL_INDEX_PREFIX)}
    internal = {n for n in names if n.startswith(SQLITE_INTERNAL_INDEX_PREFIX)}
    return declared, internal


def _ledger_versions(conn) -> list[str]:
    rows = conn.execute(
        text("SELECT version FROM schema_migrations ORDER BY version")
    ).fetchall()
    return [row[0] for row in rows]


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        text(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=:n"
        ),
        {"n": table_name},
    ).fetchone()
    return bool(row[0])


def _table_columns(conn, table_name: str) -> list[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return [row[1] for row in rows]


def _model_indexes() -> dict[str, tuple[str, list[str]]]:
    declared: dict[str, tuple[str, list[str]]] = {}
    for model in (BoardItem, BoardNote, BoardItemTag):
        table = model.__table__
        for index in table.indexes:
            declared[index.name] = (
                table.name,
                [col.name for col in index.columns],
            )
    return declared


def _sql_declared_indexes() -> dict[str, tuple[str, list[str]]]:
    """Parse the ``CREATE INDEX`` statements out of the ``.sql`` UP section.

    Split the way the RUNNER splits (strip full-line ``--`` comments,
    then split on ``;``) so the parse reflects what the runner executes.
    """
    import re

    content = BOARD_MIGRATION.read_text(encoding="utf-8")
    up_match = re.search(
        r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL
    )
    assert up_match, "the board migration has no -- UP section"
    code = "\n".join(
        line
        for line in up_match.group(1).splitlines()
        if not line.lstrip().startswith("--")
    )
    declared: dict[str, tuple[str, list[str]]] = {}
    for stmt in (s.strip() for s in code.split(";") if s.strip()):
        match = re.match(
            r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(\S+)\s+ON\s+(\S+)\s*\(([^)]*)\)",
            stmt,
            re.IGNORECASE,
        )
        if not match:
            continue
        name, table, columns = match.groups()
        declared[name] = (table, [c.strip() for c in columns.split(",")])
    return declared


def _premark_known_blockers(engine: Engine) -> None:
    """Ledger-pre-mark the TWO known pre-existing SQLite blockers.

    The documented workaround for them is a ledger row carrying a
    ``skip_reason``: the runner then never re-fires that version. This
    is scaffolding AROUND the board file — it changes nothing in the
    board migration itself.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with engine.begin() as conn:
        for version, reason in KNOWN_SQLITE_BLOCKERS.items():
            conn.execute(
                text(
                    "INSERT INTO schema_migrations "
                    "(version, name, applied_at, execution_time_ms, checksum, "
                    " precondition, skip_reason) "
                    "VALUES (:v, :n, :a, 0, NULL, NULL, :s)"
                ),
                {
                    "v": version,
                    "n": f"pre-marked {version}",
                    "a": stamp,
                    "s": f"pre-marked (known pre-existing blocker): {reason}",
                },
            )


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def scoped_migrations_dir(tmp_path: Path) -> Path:
    """A migrations dir holding ONLY the board ``.sql`` (byte-identical copy)."""
    target = tmp_path / "migrations"
    target.mkdir()
    shutil.copy2(BOARD_MIGRATION, target / BOARD_MIGRATION.name)
    return target


@pytest.fixture
def booted_engine(tmp_path: Path) -> Engine:
    """A sqlite engine whose schema was built by ``create_all`` (boot order)."""
    engine = _fresh_engine(tmp_path, "board_boot.db")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def scratch_engine(tmp_path: Path) -> Engine:
    """A sqlite engine with NO schema at all (no ``create_all``)."""
    return _fresh_engine(tmp_path, "board_scratch.db")


@pytest.fixture
def scoped_runner(booted_engine: Engine, scoped_migrations_dir: Path) -> MigrationRunner:
    """A runner over the REAL engine, discovering only the board ``.sql``."""
    return MigrationRunner(booted_engine, migrations_dir=scoped_migrations_dir)


# ── the 3-site lockstep, re-verified at runtime ─────────────────────────


class TestRuntimeLockstep:
    """The expectation this file asserts against must still agree with
    the OTHER two index sites — the models (the canonical PG source) and
    the ``.sql`` (the SQLite persistent record)."""

    def test_the_models_still_declare_exactly_the_six_indexes(self):
        assert _model_indexes() == EXPECTED_INDEXES

    def test_the_sql_still_declares_exactly_the_six_indexes(self):
        assert _sql_declared_indexes() == EXPECTED_INDEXES


# ── (a) apply in the real boot order ─────────────────────────────────────


class TestBoardMigrationApply:
    def test_apply_records_the_ledger_row(self, scoped_runner: MigrationRunner):
        applied = scoped_runner.run_pending_migrations()
        assert applied == [BOARD_VERSION]

        with scoped_runner.engine.connect() as conn:
            versions = _ledger_versions(conn)
        assert BOARD_VERSION in versions
        assert versions.count(BOARD_VERSION) == 1

    def test_apply_leaves_the_three_board_tables(
        self, scoped_runner: MigrationRunner, booted_engine: Engine
    ):
        scoped_runner.run_pending_migrations()
        with booted_engine.connect() as conn:
            assert _board_table_names(conn) == set(BOARD_TABLES)

    def test_apply_leaves_exactly_the_six_declared_board_indexes(
        self, scoped_runner: MigrationRunner, booted_engine: Engine
    ):
        scoped_runner.run_pending_migrations()
        with booted_engine.connect() as conn:
            declared, internal = _split_board_indexes(_board_index_names(conn))

        assert declared == set(EXPECTED_INDEXES)
        assert len(declared) == 6
        # No auto-named (``ix_*``) index on any board table.
        assert not any(name.startswith("ix_") for name in declared | internal)
        # The two extra sqlite_master rows are SQLite's INTERNAL
        # primary-key indexes (TEXT PK on board_items, composite PK on
        # board_item_tags) — counted separately, never silently dropped.
        assert internal == EXPECTED_SQLITE_INTERNAL_INDEXES

    def test_the_sql_creates_the_schema_on_its_own_without_create_all(
        self, scoped_migrations_dir: Path, scratch_engine: Engine
    ):
        """The file is NOT a no-op: with no ``create_all`` it creates the
        three tables and the six indexes from nothing."""
        runner = MigrationRunner(scratch_engine, migrations_dir=scoped_migrations_dir)
        applied = runner.run_pending_migrations()
        assert applied == [BOARD_VERSION]

        with scratch_engine.connect() as conn:
            assert _board_table_names(conn) == set(BOARD_TABLES)
            declared, internal = _split_board_indexes(_board_index_names(conn))
        assert declared == set(EXPECTED_INDEXES)
        assert internal == EXPECTED_SQLITE_INTERNAL_INDEXES


# ── (b) idempotency ──────────────────────────────────────────────────────


class TestBoardMigrationIdempotency:
    def test_a_second_run_is_a_no_op(
        self, scoped_runner: MigrationRunner, booted_engine: Engine
    ):
        scoped_runner.run_pending_migrations()
        second = scoped_runner.run_pending_migrations()
        assert second == [], "the second run re-applied the migration"

        with booted_engine.connect() as conn:
            versions = _ledger_versions(conn)
            declared, _ = _split_board_indexes(_board_index_names(conn))

        assert versions.count(BOARD_VERSION) == 1
        assert declared == set(EXPECTED_INDEXES)


# ── (c) rollback ─────────────────────────────────────────────────────────


class TestBoardMigrationRollback:
    def test_rollback_drops_every_board_object_and_the_ledger_row(
        self, scoped_runner: MigrationRunner, booted_engine: Engine
    ):
        scoped_runner.run_pending_migrations()
        elapsed_ms = scoped_runner.rollback_migration(BOARD_VERSION)
        assert elapsed_ms >= 0

        with booted_engine.connect() as conn:
            assert _board_table_names(conn) == set(), (
                "the rollback left board tables behind"
            )
            assert _board_index_names(conn) == set(), (
                "the rollback left board indexes behind"
            )
            assert BOARD_VERSION not in _ledger_versions(conn)
            # The ledger TABLE itself survives the rollback.
            assert _table_exists(conn, "schema_migrations")

    def test_rollback_of_an_unknown_version_raises(
        self, scoped_runner: MigrationRunner
    ):
        with pytest.raises(MigrationError):
            scoped_runner.rollback_migration("19700101_000000")


# ── (d) round-trip ───────────────────────────────────────────────────────


class TestBoardMigrationRoundTrip:
    def test_reapply_after_rollback_restores_every_object(
        self, scoped_runner: MigrationRunner, booted_engine: Engine
    ):
        scoped_runner.run_pending_migrations()
        scoped_runner.rollback_migration(BOARD_VERSION)

        applied = scoped_runner.run_pending_migrations()
        assert applied == [BOARD_VERSION]

        with booted_engine.connect() as conn:
            assert _board_table_names(conn) == set(BOARD_TABLES)
            declared, internal = _split_board_indexes(_board_index_names(conn))
            versions = _ledger_versions(conn)

        assert declared == set(EXPECTED_INDEXES)
        assert internal == EXPECTED_SQLITE_INTERNAL_INDEXES
        assert versions.count(BOARD_VERSION) == 1

    def test_the_reapplied_schema_still_matches_the_models(
        self, scoped_runner: MigrationRunner, booted_engine: Engine
    ):
        """After the rollback the tables are created BY THE ``.sql`` (not
        by ``create_all``), so their columns must still match the models
        column-for-column."""
        scoped_runner.run_pending_migrations()
        scoped_runner.rollback_migration(BOARD_VERSION)
        scoped_runner.run_pending_migrations()

        with booted_engine.connect() as conn:
            for model in (BoardItem, BoardNote, BoardItemTag):
                table = model.__table__
                expected = {col.name for col in table.columns}
                actual = set(_table_columns(conn, table.name))
                assert actual == expected, (
                    f"{table.name} was re-created by the .sql with a "
                    f"different column set: {sorted(actual)} != "
                    f"{sorted(expected)}"
                )


# ── (e) the full migration dir: the board file is NOT the blocker ────────


class TestFullMigrationDirReality:
    def test_the_full_dir_run_dies_at_the_preexisting_blocker(
        self, tmp_path: Path
    ):
        """A fresh sqlite DB (schema built by ``create_all``, the real boot
        order) + the REAL migrations dir: the run aborts at the
        PRE-EXISTING ``20260714_000001`` blocker — BEFORE the board file
        is reached."""
        engine = _fresh_engine(tmp_path, "board_full_dir_blocker.db")
        SQLModel.metadata.create_all(engine)
        runner = MigrationRunner(engine)

        with pytest.raises(MigrationError) as excinfo:
            runner.run_pending_migrations()

        message = str(excinfo.value)
        assert "20260714_000001" in message, message
        assert "DROP CONSTRAINT" in message, message

        with engine.connect() as conn:
            versions = _ledger_versions(conn)
        # The board file was never reached — it is NOT the blocker.
        assert BOARD_VERSION not in versions

    def test_the_full_dir_run_without_create_all_also_dies_before_the_board_file(
        self, tmp_path: Path
    ):
        """No ``create_all`` at all: the run dies even EARLIER (the very
        first ALTER hits a table that does not exist yet) — still before
        the board file, still on a pre-existing defect."""
        engine = _fresh_engine(tmp_path, "board_full_dir_noboot.db")
        runner = MigrationRunner(engine)

        with pytest.raises(MigrationError) as excinfo:
            runner.run_pending_migrations()

        message = str(excinfo.value)
        assert "failed" in message, message

        with engine.connect() as conn:
            versions = _ledger_versions(conn)
        assert BOARD_VERSION not in versions

    def test_the_board_migration_applies_in_the_full_dir_once_the_known_blockers_are_pre_marked(
        self, tmp_path: Path
    ):
        """The POSITIVE case in the REAL migrations dir: with the two
        known pre-existing blockers ledger-pre-marked (the documented
        workaround), the board file applies cleanly inside the real
        96-migration sequence."""
        engine = _fresh_engine(tmp_path, "board_full_dir_ok.db")
        SQLModel.metadata.create_all(engine)
        runner = MigrationRunner(engine)
        runner.ensure_migrations_table()
        _premark_known_blockers(engine)

        applied = runner.run_pending_migrations()
        assert BOARD_VERSION in applied

        with engine.connect() as conn:
            versions = _ledger_versions(conn)
            assert _board_table_names(conn) == set(BOARD_TABLES)
            declared, internal = _split_board_indexes(_board_index_names(conn))

        assert versions.count(BOARD_VERSION) == 1
        assert declared == set(EXPECTED_INDEXES)
        assert internal == EXPECTED_SQLITE_INTERNAL_INDEXES
