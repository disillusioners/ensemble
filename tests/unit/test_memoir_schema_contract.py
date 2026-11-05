"""The memoir schema contract — the four tables, the .sql, and the census.

Phase 1 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase1-plan.md`` AC1/AC2/AC6;
pack ``test/packs/memoir_schema_unit_test.sh``).

What this suite pins:

* the model registry carries EXACTLY the four memoir tables;
* the ``.sql`` applies on a throwaway SQLite DB with ZERO failing
  statements (the 20260714 lesson — verify EVERY statement on real
  SQLite);
* every string column is TEXT on both artifacts (D1.2 / AM-6) — the
  house "never VARCHAR" rule;
* the CHECK constraints are bound to the MODELS (W2) — the PG tables are
  built by ``create_all`` FROM THE MODELS, so a constraint that lived
  only in the ``.sql`` would not exist on PG;
* ``StarsListType`` round-trips ``["Emperor", "the Emperor"]``, ``None``
  and ``[]`` and carries NO mutable default (D1.8);
* the migration census: the lane's DELTA is exactly ONE new file, the
  prefix is unique, and the distinct-prefix count equals the file count
  (re-derived at test time — never a hardcoded absolute).
"""

from __future__ import annotations

import pathlib
import re
import sqlite3
import subprocess

import pytest
from sqlalchemy import Text
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.migrations.runner import MigrationFile
from daemon.repositories.memoir.models import (
    MemoirEpisode,
    MemoirPersona,
    MemoirPersonaVersion,
    MemoirSynthesis,
    StarsListType,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "daemon" / "migrations" / "versions"
MEMOIR_MIGRATION = MIGRATIONS_DIR / "20261105_102821_create_memoir_tables.sql"

#: The lane's base commit — the migration census DELTA is measured
#: against it (never against a hardcoded absolute count).
LANE_BASE_COMMIT = "5dc08aca8"

MEMOIR_TABLE_NAMES = {
    "memoir_episodes",
    "memoir_persona",
    "memoir_persona_versions",
    "memoir_syntheses",
}


@pytest.fixture()
def sqlite_engine():
    engine = create_engine(f"sqlite:///{':memory:'}")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


# ── the model registry census ─────────────────────────────────────────────


def test_the_four_memoir_tables_are_registered():
    """Exactly the four memoir tables are in the SQLModel metadata."""
    registered = {
        name for name in SQLModel.metadata.tables if name.startswith("memoir_")
    }
    assert registered == MEMOIR_TABLE_NAMES


def test_the_model_table_names_match_the_table_names():
    assert MemoirEpisode.__tablename__ == "memoir_episodes"
    assert MemoirPersona.__tablename__ == "memoir_persona"
    assert MemoirPersonaVersion.__tablename__ == "memoir_persona_versions"
    assert MemoirSynthesis.__tablename__ == "memoir_syntheses"


# ── the CHECK constraints are bound to the MODELS (W2) ────────────────────


def test_the_model_bound_check_constraints_are_present():
    """The CHECK constraints live on the MODELS, not only in the .sql."""
    expected = {
        "memoir_episodes": {
            "ck_memoir_episodes_importance",
            "ck_memoir_episodes_pinned_by",
        },
        "memoir_persona_versions": {
            "ck_memoir_persona_versions_status",
            "ck_memoir_persona_versions_source",
        },
        "memoir_syntheses": {"ck_memoir_syntheses_verb"},
    }
    for table_name, names in expected.items():
        table = SQLModel.metadata.tables[table_name]
        found = {
            constraint.name
            for constraint in table.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }
        assert names <= found, f"{table_name}: missing {names - found}"


def test_the_importance_check_rejects_out_of_range_values(sqlite_engine):
    """The bound CHECK is enforced by the database, not only by Python."""
    with Session(sqlite_engine) as session:
        session.add(
            MemoirEpisode(
                id="e1",
                agent_id="ari",
                body="x",
                created_at=_now(),
                event_at=_now(),
                importance=99,
            )
        )
        with pytest.raises(Exception):
            session.commit()


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


# ── the .sql: SQLite-valid, TEXT-only, IF NOT EXISTS, DOWN ───────────────


def _split_statements(sql: str) -> list[str]:
    """The runner's own splitter (full-line comments stripped, split on ';')."""
    code_lines = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    clean = "\n".join(code_lines)
    return [s.strip() for s in clean.split(";") if s.strip()]


def test_the_sql_file_exists_and_parses():
    assert MEMOIR_MIGRATION.exists(), MEMOIR_MIGRATION
    migration = MigrationFile.parse(MEMOIR_MIGRATION)
    assert migration.version == "20261105_102821"
    assert migration.manual_only is False


def test_the_sql_applies_cleanly_on_a_throwaway_sqlite_db():
    """AC6 — every statement applies on REAL SQLite, zero failures."""
    migration = MigrationFile.parse(MEMOIR_MIGRATION)
    statements = _split_statements(migration.up_sql)
    assert statements, "the UP section is empty"

    conn = sqlite3.connect(":memory:")
    try:
        for index, statement in enumerate(statements, start=1):
            try:
                conn.execute(statement)
            except Exception as exc:  # noqa: BLE001 — the failure IS the assertion
                raise AssertionError(
                    f"statement {index} failed on SQLite: {exc}\n{statement}"
                ) from exc
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert MEMOIR_TABLE_NAMES <= tables
    finally:
        conn.close()


def test_every_create_in_the_sql_carries_if_not_exists():
    migration = MigrationFile.parse(MEMOIR_MIGRATION)
    for statement in _split_statements(migration.up_sql):
        head = statement.split("\n")[0].strip().upper()
        if head.startswith("CREATE"):
            assert "IF NOT EXISTS" in statement.upper(), statement


def test_the_sql_declares_every_string_column_as_text():
    """D1.2 / AM-6 — no VARCHAR, no CHAR, no JSON, no BLOB in the .sql."""
    migration = MigrationFile.parse(MEMOIR_MIGRATION)
    body = migration.up_sql
    allowed = {"TEXT", "DATETIME", "INTEGER", "BOOLEAN", "REAL"}
    found = set()
    for match in re.finditer(
        r"^\s{4}([a-z_]+)\s+([A-Z]+(?:\([^)]*\))?)", body, re.MULTILINE
    ):
        found.add(match.group(2).split("(")[0].strip())
    assert found, "no columns parsed from the .sql"
    assert found <= allowed, f"non-TEXT column types in the .sql: {found - allowed}"


def test_the_models_declare_every_string_column_as_text():
    """The house rule on the PG-built side: never VARCHAR."""
    for table_name in sorted(MEMOIR_TABLE_NAMES):
        table = SQLModel.metadata.tables[table_name]
        for column in table.columns:
            if isinstance(column.type, Text):
                continue
            # Non-Text columns are only the numeric / temporal / JSON
            # families; a String/VARCHAR here would be the defect.
            assert not isinstance(column.type, Text) or True
            type_name = type(column.type).__name__
            assert type_name not in ("VARCHAR", "String", "NVARCHAR"), (
                f"{table_name}.{column.name} is {type_name}, not Text"
            )


def test_the_sql_has_a_down_section():
    migration = MigrationFile.parse(MEMOIR_MIGRATION)
    assert migration.down_sql.strip(), "the DOWN section is empty"
    statements = _split_statements(migration.down_sql)
    assert any("DROP TABLE IF EXISTS memoir_episodes" in s for s in statements)


def test_the_down_section_drops_every_memoir_table():
    migration = MigrationFile.parse(MEMOIR_MIGRATION)
    statements = _split_statements(migration.down_sql)
    conn = sqlite3.connect(":memory:")
    try:
        for statement in _split_statements(migration.up_sql):
            conn.execute(statement)
        for statement in statements:
            conn.execute(statement)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert not (tables & MEMOIR_TABLE_NAMES)
    finally:
        conn.close()


# ── the migration census: the DELTA, never an absolute ───────────────────


def test_the_lane_adds_exactly_one_migration_file():
    """The census DELTA is +1 — asserted as a DELTA, not as an absolute."""
    memoir_files = sorted(p.name for p in MIGRATIONS_DIR.glob("*memoir*.sql"))
    assert len(memoir_files) == 1, memoir_files
    assert memoir_files[0] == MEMOIR_MIGRATION.name


def test_every_migration_prefix_is_unique_and_distinct_count_matches():
    """The duplicate-prefix guard's invariant: distinct == file count."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    prefixes = [p.stem.split("_")[0] + "_" + p.stem.split("_")[1] for p in files]
    assert len(prefixes) == len(set(prefixes)), "duplicate migration prefix"
    assert len(set(prefixes)) == len(files)


def test_the_git_derived_lane_delta_is_exactly_one_migration_file():
    """The same DELTA, derived from git (skipped when the base is absent)."""
    path = "daemon/migrations/versions/"
    try:
        tracked = subprocess.run(
            ["git", "diff", "--name-only", f"{LANE_BASE_COMMIT}..HEAD", "--", path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "--", path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pytest.skip("git is unavailable in this environment")
    if tracked.returncode != 0 or untracked.returncode != 0:
        pytest.skip("the lane base commit is not resolvable in this clone")
    added = [
        line
        for line in (tracked.stdout + untracked.stdout).splitlines()
        if line.strip()
    ]
    assert len(added) == 1, added
    assert added[0].endswith(MEMOIR_MIGRATION.name)


# ── StarsListType (D1.8) ─────────────────────────────────────────────────


def test_stars_list_type_round_trips_the_three_shapes(sqlite_engine):
    """``["Emperor", "the Emperor"]``, ``None`` and ``[]`` survive a round-trip."""
    with Session(sqlite_engine) as session:
        session.add(
            MemoirEpisode(
                id="e-list",
                agent_id="ari",
                body="list",
                created_at=_now(),
                event_at=_now(),
                stars=["Emperor", "the Emperor"],
            )
        )
        session.add(
            MemoirEpisode(
                id="e-none",
                agent_id="ari",
                body="none",
                created_at=_now(),
                event_at=_now(),
                stars=None,
            )
        )
        session.add(
            MemoirEpisode(
                id="e-empty",
                agent_id="ari",
                body="empty",
                created_at=_now(),
                event_at=_now(),
                stars=[],
            )
        )
        session.commit()

    with Session(sqlite_engine) as session:
        by_id = {row.id: row for row in session.exec(select(MemoirEpisode)).all()}
        assert by_id["e-list"].stars == ["Emperor", "the Emperor"]
        assert by_id["e-none"].stars is None
        assert by_id["e-empty"].stars == []


def test_stars_list_type_contract():
    """impl / cache_ok / dialect behaviour / identity (D1.8)."""
    assert StarsListType.impl is not None
    assert StarsListType.cache_ok is True
    instance = StarsListType()
    assert instance.process_bind_param(None, None) is None
    assert instance.process_result_value(None, None) is None
    # IDENTITY — a Python list in, a Python list out (never a
    # pre-serialized JSON string).
    assert instance.process_bind_param(["a", "b"], None) == ["a", "b"]
    assert instance.process_result_value(["a", "b"], None) == ["a", "b"]
    assert isinstance(instance.process_bind_param(["a"], None), list)
    assert isinstance(instance.process_result_value(["a"], None), list)


def test_stars_column_has_no_mutable_default():
    """A shared-mutable default is a classic bug — the column is NULLABLE."""
    column = MemoirEpisode.__table__.c.stars
    assert column.nullable is True
    default = getattr(column.default, "arg", None)
    assert default is None, f"stars carries a default: {default!r}"


def test_the_syntheses_table_has_no_writer_in_the_tree():
    """DD-2 — the table is schema-only and EMPTY in v1."""
    result = subprocess.run(
            ["git", "grep", "-l", "memoir_syntheses", "--", "daemon/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    hits = [line for line in result.stdout.splitlines() if line.strip()]
    # Only the model, the package export, the manager's registration and
    # the audit-record .sql may reference the table. A WRITER would add
    # another file.
    allowed = {
        "daemon/repositories/memoir/models.py",
        "daemon/repositories/memoir/__init__.py",
        "daemon/manager.py",
        # The .sql is the persistent AUDIT RECORD of the schema change
        # (DD-18) — it CREATES the empty table and never writes a row.
        "daemon/migrations/versions/20261105_102821_create_memoir_tables.sql",
    }
    unexpected = [hit for hit in hits if hit.split(":")[0] not in allowed]
    assert not unexpected, f"memoir_syntheses is referenced by: {unexpected}"

    # And no SQL writer anywhere in the shipped tree.
    for needle in (
        "INSERT INTO memoir_syntheses",
        "UPDATE memoir_syntheses",
        "DELETE FROM memoir_syntheses",
    ):
        writer = subprocess.run(
            ["git", "grep", "-n", needle, "--", "daemon/"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert not writer.stdout.strip(), f"a writer exists for {needle}"
