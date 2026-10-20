"""Board migration + index-parity tests (ari-tools Phase 1).

The board's schema has THREE index sites that must stay in lockstep
(phase1-plan §4.1, D7 — the resource_monitor precedent):

  (a) the SQLModel ``__table_args__`` of the three board models — the
      canonical source for the PG ``create_all`` path;
  (b) the ``CREATE INDEX`` statements in
      ``daemon/migrations/versions/20261020_060000_create_board_tables.sql``
      — the SQLite persistent record;
  (c) the expected set asserted in THIS file.

Names alone are not enough — a same-named index with different columns
is a silent mismatch, so the COLUMN LISTS are compared too.

Pins:

* the model index names AND column lists == the ``.sql`` index names
  AND column lists == the expected set (all three sites);
* the ``.sql`` is applied **VERBATIM** to a temp SQLite DB in the REAL
  boot order — ``SQLModel.metadata.create_all`` FIRST, the ``.sql``
  AFTER it — and the apply must SUCCEED against the already-created
  schema. It only does if every ``CREATE`` carries ``IF NOT EXISTS``
  (without it the runner swallows "already exists" and the file
  degrades into a silent no-op on the boot path);
* a static assertion over the ``.sql`` text: every ``CREATE TABLE`` /
  ``CREATE INDEX`` statement in the UP section carries
  ``IF NOT EXISTS`` (a bare ``CREATE`` must be a RED test);
* a source-order assertion on the boot path — in ``daemon/manager.py``
  the ``create_all`` call PRECEDES the ``run_pending_migrations()``
  call, because the ordering is a property of the boot path and must
  be pinned where it lives;
* the migration declares EVERY column of every table;
* the composite PRIMARY KEY ``(item_id, tag)`` exists in both the
  model and the ``.sql``;
* the ``-- DOWN`` section is present and drops the CHILD tables before
  the parent (the rollback path must be order-safe with the NO ACTION
  foreign keys in place).

SAFETY FENCE: the migration smoke test builds its temp DB under
pytest's ``tmp_path`` — no HOME-derived or live-shaped path is ever
resolved.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

# Importing the models registers the board tables (and the ``projects``
# table the board's project_id FK points at) on ``SQLModel.metadata``.
from daemon.models.board import (  # noqa: F401
    BoardItem,
    BoardItemTag,
    BoardNote,
)
from daemon.repositories.project.models import Project  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_SQL = (
    REPO_ROOT
    / "daemon/migrations/versions/20261020_060000_create_board_tables.sql"
)
MANAGER_SOURCE = REPO_ROOT / "daemon" / "manager.py"

#: The six indexes of phase1-plan §4.1 — site (c).
EXPECTED_INDEXES: dict[str, tuple[str, list[str]]] = {
    "idx_board_items_status_updated": (
        "board_items",
        ["status", "updated_at"],
    ),
    "idx_board_items_updated_at": ("board_items", ["updated_at"]),
    "idx_board_items_project_id": ("board_items", ["project_id"]),
    "idx_board_items_due_at": ("board_items", ["due_at"]),
    "idx_board_item_tags_tag_item": (
        "board_item_tags",
        ["tag", "item_id"],
    ),
    "idx_board_notes_item_id_ts": ("board_notes", ["item_id", "ts"]),
}

#: Every column of every table — site (c) for the column parity.
EXPECTED_COLUMNS: dict[str, list[str]] = {
    "board_items": [
        "id",
        "title",
        "body_md",
        "status",
        "project_id",
        "due_at",
        "priority",
        "linked_job_ids",
        "created_by_instance",
        "created_at",
        "updated_at",
    ],
    "board_notes": ["id", "item_id", "ts", "author_instance", "text"],
    "board_item_tags": ["item_id", "tag"],
}


@pytest.fixture(scope="module")
def migration_text() -> str:
    return MIGRATION_SQL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def up_section(migration_text: str) -> str:
    match = re.search(
        r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", migration_text, re.DOTALL
    )
    assert match, "the migration has no -- UP section"
    return match.group(1)


@pytest.fixture(scope="module")
def down_section(migration_text: str) -> str:
    match = re.search(
        r"--\s*DOWN\s*\n(.*?)$", migration_text, re.DOTALL
    )
    assert match, "the migration has no -- DOWN section"
    return match.group(1)


def _sql_statements(section: str) -> list[str]:
    """Split a migration section the way the RUNNER does.

    The runner strips FULL-LINE ``--`` comments and then splits the
    remaining text on ``;`` — an inline comment carrying a ``;`` would
    corrupt the split, which is why the file has none.
    """
    code = "\n".join(
        line for line in section.splitlines() if not line.lstrip().startswith("--")
    )
    return [stmt.strip() for stmt in code.split(";") if stmt.strip()]


# ── site (a): the model __table_args__ ───────────────────────────────────


class TestModelIndexDeclaration:
    def test_model_declares_exactly_the_six_indexes(self):
        declared: dict[str, tuple[str, list[str]]] = {}
        for model in (BoardItem, BoardNote, BoardItemTag):
            table = model.__table__
            for index in table.indexes:
                declared[index.name] = (
                    table.name,
                    [col.name for col in index.columns],
                )
        assert declared == EXPECTED_INDEXES, (
            "the model's __table_args__ indexes drifted from the "
            f"expected set: {declared}"
        )

    def test_model_declares_no_index_beyond_the_six(self):
        count = sum(
            len(model.__table__.indexes)
            for model in (BoardItem, BoardNote, BoardItemTag)
        )
        assert count == len(EXPECTED_INDEXES)


# ── site (b): the .sql ───────────────────────────────────────────────────


class TestMigrationIndexDeclaration:
    def test_sql_declares_exactly_the_six_indexes(self, up_section: str):
        declared: dict[str, tuple[str, list[str]]] = {}
        for stmt in _sql_statements(up_section):
            match = re.match(
                r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(\S+)\s+ON\s+(\S+)\s*\(([^)]*)\)",
                stmt,
                re.IGNORECASE,
            )
            if not match:
                continue
            name, table, columns = match.groups()
            declared[name] = (
                table,
                [col.strip() for col in columns.split(",")],
            )
        assert declared == EXPECTED_INDEXES, (
            f"the .sql indexes drifted from the expected set: {declared}"
        )

    def test_model_and_sql_agree_byte_for_byte(self, up_section: str):
        """3-site lockstep — the model and the .sql must agree on the
        index names AND their column lists."""
        sql_indexes: dict[str, list[str]] = {}
        for stmt in _sql_statements(up_section):
            match = re.match(
                r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(\S+)\s+ON\s+(\S+)\s*\(([^)]*)\)",
                stmt,
                re.IGNORECASE,
            )
            if not match:
                continue
            name, _table, columns = match.groups()
            sql_indexes[name] = [col.strip() for col in columns.split(",")]

        model_indexes: dict[str, list[str]] = {}
        for model in (BoardItem, BoardNote, BoardItemTag):
            for index in model.__table__.indexes:
                model_indexes[index.name] = [
                    col.name for col in index.columns
                ]

        assert sql_indexes == model_indexes, (
            "the .sql and the model disagree on the index names/columns"
        )
        assert sql_indexes == {
            name: cols for name, (_table, cols) in EXPECTED_INDEXES.items()
        }


# ── the IF NOT EXISTS contract (create_all runs FIRST) ──────────────────


class TestIdempotentCreateContract:
    def test_every_create_in_the_up_section_carries_if_not_exists(
        self, up_section: str
    ):
        """A bare ``CREATE`` must be a RED test, not a silent no-op on
        the boot path — the runner swallows "already exists"."""
        creates = [
            stmt
            for stmt in _sql_statements(up_section)
            if re.match(r"CREATE\s+(TABLE|INDEX)\b", stmt, re.IGNORECASE)
        ]
        assert creates, "the UP section declares no CREATE at all"
        for stmt in creates:
            assert re.match(
                r"CREATE\s+(TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\b",
                stmt,
                re.IGNORECASE,
            ), f"a CREATE without IF NOT EXISTS: {stmt}"

    def test_the_down_section_drops_with_if_exists(self, down_section: str):
        drops = [
            stmt
            for stmt in _sql_statements(down_section)
            if re.match(r"DROP\s+(TABLE|INDEX)\b", stmt, re.IGNORECASE)
        ]
        assert drops
        for stmt in drops:
            assert re.match(
                r"DROP\s+(TABLE|INDEX)\s+IF\s+EXISTS\b", stmt, re.IGNORECASE
            ), f"a DROP without IF EXISTS: {stmt}"


# ── the migration smoke test (the REAL boot order) ──────────────────────


class TestMigrationSmoke:
    def test_sql_applies_verbatim_over_an_already_created_schema(
        self, tmp_path: Path, up_section: str
    ):
        """The real boot order: ``create_all`` FIRST, then the ``.sql``
        applied VERBATIM. The apply must succeed — it only does if
        every CREATE carries IF NOT EXISTS."""
        db_path = tmp_path / "board_migration_smoke.db"
        engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(engine)

        inspector = inspect(engine)
        for table in EXPECTED_COLUMNS:
            assert table in inspector.get_table_names(), (
                f"create_all did not create {table} — the boot order "
                "assumption is broken"
            )

        statements = _sql_statements(up_section)
        assert statements
        with engine.begin() as conn:
            from sqlalchemy import text

            for stmt in statements:
                conn.execute(text(stmt))

        # The six index names+columns are still exactly the expected set.
        inspector = inspect(engine)
        for name, (table, columns) in EXPECTED_INDEXES.items():
            indexes = {
                ix["name"]: ix for ix in inspector.get_indexes(table)
            }
            assert name in indexes, f"missing index {name} on {table}"
            assert indexes[name]["column_names"] == columns, (
                f"index {name} column drift: "
                f"{indexes[name]['column_names']} != {columns}"
            )
        engine.dispose()

    def test_the_sql_is_reappliable(self, tmp_path: Path, up_section: str):
        """Applying the .sql twice must not raise (the runner may run it
        on every boot against an already-migrated DB)."""
        db_path = tmp_path / "board_migration_twice.db"
        engine = create_engine(f"sqlite:///{db_path}")
        statements = _sql_statements(up_section)
        from sqlalchemy import text

        for _ in range(2):
            with engine.begin() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))
        engine.dispose()


# ── column + composite-PK parity ────────────────────────────────────────


class TestColumnParity:
    def test_the_migration_declares_every_column_of_every_table(
        self, up_section: str
    ):
        for table, columns in EXPECTED_COLUMNS.items():
            body = None
            for stmt in _sql_statements(up_section):
                match = re.match(
                    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"
                    + re.escape(table)
                    + r"\s*\((.*)\)\s*$",
                    stmt,
                    re.IGNORECASE | re.DOTALL,
                )
                if match:
                    body = match.group(1)
                    break
            assert body is not None, f"no CREATE TABLE for {table}"
            declared = []
            for line in body.splitlines():
                line = line.strip().rstrip(",")
                if not line or line.upper().startswith("PRIMARY KEY"):
                    continue
                declared.append(line.split()[0])
            assert declared == columns, (
                f"{table} column drift — the .sql declares {declared}, "
                f"the model declares {columns}"
            )

    def test_the_composite_primary_key_exists_in_both(self, up_section: str):
        # Model side.
        model_pk = [
            col.name for col in BoardItemTag.__table__.primary_key.columns
        ]
        assert model_pk == ["item_id", "tag"]
        # .sql side.
        body = None
        for stmt in _sql_statements(up_section):
            match = re.match(
                r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+board_item_tags\s*\((.*)\)\s*$",
                stmt,
                re.IGNORECASE | re.DOTALL,
            )
            if match:
                body = match.group(1)
                break
        assert body is not None
        pk_match = re.search(
            r"PRIMARY\s+KEY\s*\(([^)]*)\)", body, re.IGNORECASE
        )
        assert pk_match, "board_item_tags declares no composite PRIMARY KEY"
        columns = [c.strip() for c in pk_match.group(1).split(",")]
        assert columns == ["item_id", "tag"]


# ── the DOWN section ─────────────────────────────────────────────────────


class TestDownSection:
    def test_the_down_section_is_present(self, down_section: str):
        assert down_section.strip(), "the -- DOWN section is empty"

    def test_down_drops_the_child_tables_before_the_parent(
        self, down_section: str
    ):
        order: list[str] = []
        for stmt in _sql_statements(down_section):
            match = re.match(
                r"DROP\s+TABLE\s+IF\s+EXISTS\s+(\S+)", stmt, re.IGNORECASE
            )
            if match:
                order.append(match.group(1).strip('"'))
        assert "board_items" in order
        assert "board_notes" in order
        assert "board_item_tags" in order
        assert order.index("board_item_tags") < order.index("board_items")
        assert order.index("board_notes") < order.index("board_items")

    def test_down_drops_every_index_and_table_it_created(self, down_section: str):
        dropped = " ".join(_sql_statements(down_section)).lower()
        for name in EXPECTED_INDEXES:
            assert name.lower() in dropped, f"{name} is never dropped"
        for table in EXPECTED_COLUMNS:
            assert table in dropped, f"{table} is never dropped"


# ── the boot path order (a property of daemon/manager.py) ───────────────


class TestBootPathOrder:
    def test_create_all_precedes_run_pending_migrations(self):
        source = MANAGER_SOURCE.read_text(encoding="utf-8")
        create_all_idx = source.find("SQLModel.metadata.create_all(self._engine)")
        runner_idx = source.find("run_pending_migrations()")
        assert create_all_idx != -1, "create_all call not found in manager.py"
        assert runner_idx != -1, "run_pending_migrations() not found in manager.py"
        assert create_all_idx < runner_idx, (
            "create_all must run BEFORE the migration runner — the .sql is "
            "applied to a schema that already contains every object it "
            "declares, which is why every CREATE carries IF NOT EXISTS"
        )

    def test_the_board_models_are_imported_before_create_all(self):
        source = MANAGER_SOURCE.read_text(encoding="utf-8")
        import_idx = source.find("from .models.board import")
        create_all_idx = source.find("SQLModel.metadata.create_all(self._engine)")
        assert import_idx != -1, (
            "the board models are not late-imported in manager.py — "
            "create_all would not create the board tables on PG"
        )
        assert import_idx < create_all_idx
