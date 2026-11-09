"""Memoir Store v2 — the schema substrate (the SQLite lane).

Phase 1 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase1-plan.md`` AC1 / AC2
/ AC6; pack ``test/packs/memoir_v2_schema_unit_test.sh``).

What this suite pins:

* **AC1** — the migration file is discovered without a prefix collision;
  every ``CREATE TABLE`` carries ``IF NOT EXISTS``; every ``ALTER TABLE``
  uses the PLAIN ``ADD COLUMN`` form (zero ``IF NOT EXISTS`` in the
  ``.sql`` — the SQLite-invalid form); every string column is TEXT; the
  DOWN section exists with its additive-rollback note; the file applies
  cleanly on a throwaway SQLite database AND through the real migration
  runner in the real boot order (create_all FIRST, then the runner);
* **AC2** — the SQLModel metadata carries the six v2 tables; the CHECK
  constraints are bound to the MODELS (on PG the tables are built by
  ``create_all`` FROM the models, so a constraint that lived only in the
  ``.sql`` would not exist there at all); the
  ``notification_status`` vocabulary is the EXACT closed four-token set —
  TEST-ENFORCED (all four accepted, a fifth REJECTED by the database),
  in BOTH the ``.sql`` DDL and the model, not merely greppable; the four
  new ``memoir_episodes`` columns are present; the INSERT names
  ``poignancy_status`` explicitly;
* **AC3 (the SQLite half)** — the cutover-bounded legacy seed: a row
  written BEFORE the cutover flips to ``'legacy'``, a row written after
  it stays NULL, and the cutover literal is SPACE-SEPARATED (the
  ISO-8601 ``'T'`` form would misclassify the whole day, because SQLite
  compares datetimes lexicographically).

The 4-token vocabulary is enforced by INSERTING: every one of the four
tokens must be accepted and a fifth (``'notified'``) must be rejected —
on the table built from the ``.sql`` AND on the table built from the
model. A grep that finds the vocabulary in the source proves nothing
about the database that ships.
"""

from __future__ import annotations

import pathlib
import re
import sqlite3
import subprocess

import pytest
from sqlalchemy import DateTime, Integer, Text
from sqlmodel import SQLModel, create_engine

from daemon.migrations.runner import MigrationFile, MigrationRunner
from daemon.repositories.memoir.models import (
    MemoirEpisode,
    MemoirJevLedger,
    MemoirLine,
    MemoirProposalLedger,
    MemoirReflectionState,
    MemoirStar,
    MemoirStarAlias,
    MemoirSynthesis,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "daemon" / "migrations" / "versions"
MEMOIR_V2_MIGRATION = MIGRATIONS_DIR / "20261109_021154_create_memoir_v2_tables.sql"

#: The lane's base commit — the migration census DELTA is measured
#: against it (never against a hardcoded absolute count).
LANE_BASE_COMMIT = "f49006b922492f72ca032c56e26d20468f7a3afd"

#: The six NEW tables of this lane.
V2_TABLE_NAMES = {
    "memoir_star",
    "memoir_star_alias",
    "memoir_line",
    "memoir_reflection_state",
    "memoir_proposal_ledger",
    "memoir_jev_ledger",
}

#: The EXACT closed four-token vocabulary of
#: ``memoir_proposal_ledger.notification_status`` (B1' / V2-28).
NOTIFICATION_STATUS_VOCABULARY = ("pending", "enqueued", "sent", "failed")

#: The cutover literal — SPACE-SEPARATED (SF11 / V2-33).
CUTOVER_LITERAL = "2026-11-09 00:00:00"


# ── helpers ──────────────────────────────────────────────────────────────


def _up_sql(path: pathlib.Path) -> str:
    return MigrationFile.parse(path).up_sql


def _down_sql(path: pathlib.Path) -> str:
    return MigrationFile.parse(path).down_sql


def _split_statements(sql: str) -> list[str]:
    """The runner's OWN split: full-line ``--`` comments stripped, then
    split on ``;``. Mirrors ``MigrationRunner.apply_migration`` so the
    test exercises the same statement boundaries the runner does."""
    code_lines = [
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    ]
    return [stmt.strip() for stmt in "\n".join(code_lines).split(";") if stmt.strip()]


def _v1_shaped_sqlite_db() -> sqlite3.Connection:
    """A throwaway SQLite DB shaped like the v1 memoir tables.

    The v2 migration references ``memoir_episodes`` and
    ``memoir_syntheses`` (the foreign keys and the cutover seed), so the
    v1 tables must exist for the file to apply. They are created from
    the v1 DDL of the v1 migration — NOT from the models, so the v2
    migration really does the work.
    """
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE memoir_episodes (
            id TEXT NOT NULL PRIMARY KEY,
            agent_id TEXT NOT NULL,
            title TEXT,
            body TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            event_at DATETIME NOT NULL,
            importance INTEGER NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
            stars TEXT,
            is_keepsake BOOLEAN NOT NULL DEFAULT false,
            pinned_by TEXT CHECK (pinned_by IS NULL OR pinned_by IN ('empress', 'emperor')),
            pinned_reason TEXT,
            pinned_at DATETIME,
            last_accessed_at DATETIME,
            access_count INTEGER NOT NULL DEFAULT 0,
            embedding TEXT,
            embedding_model TEXT,
            embedding_dims INTEGER,
            embedded_at TEXT,
            embedding_attempts INTEGER NOT NULL DEFAULT 0,
            embedding_error TEXT,
            superseded_by TEXT
        );
        CREATE TABLE memoir_syntheses (
            id TEXT NOT NULL PRIMARY KEY,
            agent_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            source_episode_ids TEXT,
            valid_from TEXT,
            valid_to TEXT,
            verb TEXT NOT NULL CHECK (verb IN ('CREATE', 'CORROBORATE', 'REFINE', 'CORRECT')),
            supersedes TEXT,
            superseded_by TEXT,
            confidence REAL,
            created_at DATETIME NOT NULL
        );
        """
    )
    return con


def _apply_up_statements(con: sqlite3.Connection) -> list[str]:
    """Apply EVERY UP statement, raising on the first failure.

    The runner swallows a few error classes (duplicate column, already
    exists) on its idempotent branch. This helper deliberately does NOT:
    the point of the leg is to prove that the file applies CLEANLY on a
    v1-shaped database.
    """
    statements = _split_statements(_up_sql(MEMOIR_V2_MIGRATION))
    for statement in statements:
        con.execute(statement)
    con.commit()
    return statements


def _table_names(con: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})")]


@pytest.fixture()
def v1_shaped_db():
    con = _v1_shaped_sqlite_db()
    yield con
    con.close()


# ── AC1 — the migration file ─────────────────────────────────────────────


def test_the_migration_file_is_present_and_parses():
    assert MEMOIR_V2_MIGRATION.is_file(), MEMOIR_V2_MIGRATION
    parsed = MigrationFile.parse(MEMOIR_V2_MIGRATION)
    assert parsed.version == "20261109_021154"
    assert parsed.manual_only is False


def test_the_migration_prefix_is_unique_across_the_versions_directory():
    """The duplicate-prefix guard raises at DISCOVERY — the prefix must
    be unique among all 127 files."""
    prefixes = [p.name.split("_")[0] + "_" + p.name.split("_")[1] for p in MIGRATIONS_DIR.glob("*.sql")]
    assert len(prefixes) == len(set(prefixes)), "a duplicate migration prefix exists"
    assert prefixes.count("20261109_021154") == 1


def test_the_migration_is_discovered_without_a_duplicate_prefix_error():
    """The runner's own discovery accepts the file (no raise)."""
    engine = create_engine("sqlite:///:memory:")
    try:
        runner = MigrationRunner(engine)
        versions = [m.version for m in runner.discover_migrations()]
        assert "20261109_021154" in versions
        assert len(versions) == len(set(versions))
    finally:
        engine.dispose()


def test_the_migration_census_delta_is_exactly_one_new_file():
    """The lane's DELTA against its base commit — re-derived, never a
    hardcoded absolute.

    The WORKING TREE is compared against the base commit (not ``HEAD``)
    so the leg is meaningful both before and after the commit lands.
    """
    def _at(ref: str) -> set[str]:
        out = subprocess.run(
            ["git", "ls-tree", "-r", ref, "--name-only", "daemon/migrations/versions/"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return {line.strip() for line in out.stdout.splitlines() if line.strip()}

    base = _at(LANE_BASE_COMMIT)
    working = {
        f"daemon/migrations/versions/{path.name}"
        for path in MIGRATIONS_DIR.glob("*.sql")
    }
    delta = working - base
    assert delta == {"daemon/migrations/versions/20261109_021154_create_memoir_v2_tables.sql"}, delta


def test_every_create_table_carries_if_not_exists():
    sql = _up_sql(MEMOIR_V2_MIGRATION)
    creates = re.findall(r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?(\w+)", sql, re.IGNORECASE)
    assert creates, "the migration creates no table"
    for guard, _name in creates:
        assert guard, f"a CREATE TABLE lacks IF NOT EXISTS: {sql}"


def test_every_alter_uses_the_plain_add_column_form():
    """``ADD COLUMN IF NOT EXISTS`` is a HARD SYNTAX ERROR in SQLite.

    The mutation leg (AC6): changing the ``.sql`` to the
    ``IF NOT EXISTS`` form breaks SQLite execution and flips THIS pin
    RED.
    """
    sql = _up_sql(MEMOIR_V2_MIGRATION)
    alters = re.findall(r"ALTER\s+TABLE\s+[^;]+;?", sql, re.IGNORECASE)
    assert len(alters) == 4, alters
    for alter in alters:
        assert "ADD COLUMN" in alter.upper(), alter
        assert "IF NOT EXISTS" not in alter.upper(), alter
    assert "ADD COLUMN IF NOT EXISTS" not in sql.upper()


def test_the_four_added_columns_are_the_planned_ones():
    sql = _up_sql(MEMOIR_V2_MIGRATION)
    for column, declaration in (
        ("poignancy_status", "TEXT"),
        ("poignancy", "INTEGER"),
        ("poignancy_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("reflected_at", "DATETIME"),
    ):
        assert (
            f"ALTER TABLE memoir_episodes ADD COLUMN {column} {declaration}" in sql
        ), f"the {column} column is not declared as planned"


def test_every_string_column_is_text():
    """The house "never VARCHAR" rule — on BOTH artifacts."""
    sql = _up_sql(MEMOIR_V2_MIGRATION)
    assert "VARCHAR" not in sql.upper()
    assert "CHAR(" not in sql.upper()
    for match in re.finditer(r"^\s*(\w+)\s+(TEXT|INTEGER|REAL|BOOLEAN|DATETIME)\b", sql, re.MULTILINE):
        column, declared = match.group(1), match.group(2).upper()
        if declared in {"INTEGER", "REAL", "BOOLEAN", "DATETIME"}:
            continue
        assert declared == "TEXT", f"{column} is declared {declared}, not TEXT"


def test_the_down_section_exists_with_the_additive_rollback_note():
    down = _down_sql(MEMOIR_V2_MIGRATION)
    assert down, "the DOWN section is empty"
    assert "DROP TABLE IF EXISTS memoir_jev_ledger" in down
    assert "DROP TABLE IF EXISTS memoir_proposal_ledger" in down
    assert "DROP TABLE IF EXISTS memoir_line" in down
    assert "DROP TABLE IF EXISTS memoir_star_alias" in down
    assert "DROP TABLE IF EXISTS memoir_star" in down
    assert "DROP TABLE IF EXISTS memoir_reflection_state" in down
    # The additive-rollback note: the four ADD COLUMNs are RETAINED.
    assert "additive" in down.lower()


def test_the_migration_applies_cleanly_on_a_v1_shaped_sqlite_database(v1_shaped_db):
    """EVERY statement executes — zero failures, zero swallowed errors."""
    statements = _apply_up_statements(v1_shaped_db)
    assert len(statements) == 17, statements
    names = _table_names(v1_shaped_db)
    assert V2_TABLE_NAMES <= names, names
    for column in ("poignancy_status", "poignancy", "poignancy_attempts", "reflected_at"):
        assert column in _columns(v1_shaped_db, "memoir_episodes")


def test_the_migration_applies_through_the_runner_in_the_real_boot_order():
    """The REAL fresh-SQLite boot order: ``create_all`` FIRST, then the
    runner. The runner's idempotent branch absorbs the duplicate
    columns — the file must not ABORT the boot."""
    engine = create_engine("sqlite:///:memory:")
    try:
        SQLModel.metadata.create_all(engine)
        runner = MigrationRunner(engine)
        runner.ensure_migrations_table()
        migration = MigrationFile.parse(MEMOIR_V2_MIGRATION)
        # apply_migration RAISES MigrationError on a real failure.
        runner.apply_migration(migration)
        applied = runner.get_applied_versions()
        assert "20261109_021154" in applied
    finally:
        engine.dispose()


def test_the_down_section_rolls_the_new_tables_back(v1_shaped_db):
    _apply_up_statements(v1_shaped_db)
    before = _table_names(v1_shaped_db)
    assert V2_TABLE_NAMES <= before
    for statement in _split_statements(_down_sql(MEMOIR_V2_MIGRATION)):
        v1_shaped_db.execute(statement)
    v1_shaped_db.commit()
    after = _table_names(v1_shaped_db)
    assert not (V2_TABLE_NAMES & after), after


# ── AC3 (the SQLite half) — the cutover-bounded legacy seed ─────────────


def test_the_cutover_literal_is_space_separated_not_iso_8601():
    """SF11 / V2-33. The mutation leg (AC6): an ISO-8601 ``'T'`` literal
    misclassifies the cutover fixture and flips THIS pin RED."""
    sql = _up_sql(MEMOIR_V2_MIGRATION)
    assert f"'{CUTOVER_LITERAL}'" in sql
    assert "2026-11-09T00:00:00" not in sql
    # A 'T' separator sorts AFTER the ' ' separator, so every stored
    # timestamp of the cutover day would compare GREATER than the
    # literal and the seed would skip the whole day.
    assert "T" > " "


def test_the_seed_flips_only_the_pre_cutover_rows(v1_shaped_db):
    v1_shaped_db.execute(
        "INSERT INTO memoir_episodes (id, agent_id, title, body, created_at, event_at) "
        "VALUES ('pre','ari','t','b','2026-11-05 12:00:00.000000','2026-11-05 12:00:00.000000')"
    )
    v1_shaped_db.execute(
        "INSERT INTO memoir_episodes (id, agent_id, title, body, created_at, event_at) "
        "VALUES ('post','ari','t','b','2026-11-09 00:00:01.000000','2026-11-09 00:00:01.000000')"
    )
    _apply_up_statements(v1_shaped_db)
    rows = dict(
        v1_shaped_db.execute("SELECT id, poignancy_status FROM memoir_episodes")
    )
    assert rows["pre"] == "legacy"
    assert rows["post"] is None


# ── AC2 — the model registry ─────────────────────────────────────────────


def test_the_six_v2_tables_are_registered():
    registered = {
        name for name in SQLModel.metadata.tables if name.startswith("memoir_")
    }
    assert V2_TABLE_NAMES <= registered, registered


def test_the_model_table_names_match_the_table_names():
    assert MemoirStar.__tablename__ == "memoir_star"
    assert MemoirStarAlias.__tablename__ == "memoir_star_alias"
    assert MemoirLine.__tablename__ == "memoir_line"
    assert MemoirReflectionState.__tablename__ == "memoir_reflection_state"
    assert MemoirProposalLedger.__tablename__ == "memoir_proposal_ledger"
    assert MemoirJevLedger.__tablename__ == "memoir_jev_ledger"


def _check_texts(table) -> set[str]:
    return {
        str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }


def test_the_model_bound_check_constraints_are_present():
    """The CHECKs are bound to the MODELS — on PG the table is built
    FROM the model, so a constraint that lived only in the ``.sql``
    would not exist there at all."""
    assert "star_type IN ('person', 'project', 'place', 'concept', 'domain')" in _check_texts(
        SQLModel.metadata.tables["memoir_star"]
    )
    assert "heat >= 0.0" in _check_texts(SQLModel.metadata.tables["memoir_star"])
    assert "mention_count > 0" in _check_texts(SQLModel.metadata.tables["memoir_line"])
    assert (
        "notification_status IN ('pending', 'enqueued', 'sent', 'failed')"
        in _check_texts(SQLModel.metadata.tables["memoir_proposal_ledger"])
    )
    assert "status IN ('pending', 'drafted', 'suppressed', 'terminal')" in _check_texts(
        SQLModel.metadata.tables["memoir_jev_ledger"]
    )
    assert any(
        "classification IS NULL OR classification IN ('conflict', "
        "'boundary_change', 'new_rule', 'reinforcement', 'none')" == text
        for text in _check_texts(SQLModel.metadata.tables["memoir_jev_ledger"])
    )


def test_the_ddl_and_the_model_agree_on_every_check_constraint():
    """The constraint TEXTS are byte-identical across the two artifacts."""
    sql = _up_sql(MEMOIR_V2_MIGRATION)
    for text in (
        "star_type IN ('person', 'project', 'place', 'concept', 'domain')",
        "heat >= 0.0",
        "mention_count > 0",
        "notification_status IN ('pending', 'enqueued', 'sent', 'failed')",
        "status IN ('pending', 'drafted', 'suppressed', 'terminal')",
        "classification IS NULL OR classification IN ('conflict', "
        "'boundary_change', 'new_rule', 'reinforcement', 'none')",
    ):
        assert text in sql, f"the .sql does not carry {text!r}"


def test_the_index_names_are_in_lockstep_with_the_sql():
    """3-site lockstep: the .sql index names AND the model's index
    names are the same set."""
    sql = _up_sql(MEMOIR_V2_MIGRATION)
    sql_indexes = set(re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", sql))
    model_indexes: set[str] = set()
    for name in V2_TABLE_NAMES:
        model_indexes |= {idx.name for idx in SQLModel.metadata.tables[name].indexes}
    assert sql_indexes == model_indexes, (sql_indexes, model_indexes)
    assert {
        "idx_memoir_star_agent_heat",
        "idx_memoir_star_alias_star",
        "idx_memoir_line_episode",
        "idx_memoir_line_agent",
        "idx_memoir_proposal_ledger_date",
        "idx_memoir_jev_status",
    } == sql_indexes


def test_the_unique_constraints_are_in_lockstep_with_the_sql():
    sql = _up_sql(MEMOIR_V2_MIGRATION)
    for constraint in (
        "uq_memoir_star_agent_name",
        "uq_memoir_alias_agent_alias",
        "uq_memoir_line_star_episode",
        "uq_memoir_proposal_agent_episode",
        "uq_memoir_jev_synthesis",
    ):
        assert constraint in sql, constraint
    model_constraints = {
        constraint.name
        for name in V2_TABLE_NAMES
        for constraint in SQLModel.metadata.tables[name].constraints
        if constraint.name
    }
    for constraint in (
        "uq_memoir_star_agent_name",
        "uq_memoir_alias_agent_alias",
        "uq_memoir_line_star_episode",
        "uq_memoir_proposal_agent_episode",
        "uq_memoir_jev_synthesis",
    ):
        assert constraint in model_constraints, constraint


def test_the_memoir_episode_carries_the_four_new_columns():
    table = SQLModel.metadata.tables["memoir_episodes"]
    columns = {col.name: col for col in table.columns}
    assert isinstance(columns["poignancy_status"].type, Text)
    assert columns["poignancy_status"].nullable is True
    assert isinstance(columns["poignancy"].type, Integer)
    assert columns["poignancy"].nullable is True
    assert isinstance(columns["poignancy_attempts"].type, Integer)
    assert columns["poignancy_attempts"].nullable is False
    assert str(columns["poignancy_attempts"].server_default.arg) == "0"
    assert isinstance(columns["reflected_at"].type, DateTime)
    assert columns["reflected_at"].type.timezone is True
    assert columns["reflected_at"].nullable is True


def test_the_insert_names_poignancy_status_explicitly():
    """V2-D 1.14 — the column is NAMED in the INSERT with a literal NULL."""
    from daemon.repositories.memoir.repository import MEMOIR_INSERT_SQL

    assert "poignancy_status" in MEMOIR_INSERT_SQL
    assert ":poignancy_status" not in MEMOIR_INSERT_SQL
    values = MEMOIR_INSERT_SQL[
        MEMOIR_INSERT_SQL.index("VALUES") : MEMOIR_INSERT_SQL.rindex(")")
    ]
    assert values.rstrip().endswith("NULL")


def test_the_record_columns_family_is_untouched():
    """``RECORD_COLUMNS`` (the write-once family) stays GREEN — the v2
    column is an OPERATIONAL column, never a record column."""
    from daemon.repositories.memoir.models import MemoirEpisode as _Episode

    record_columns = {
        "id",
        "agent_id",
        "title",
        "body",
        "created_at",
        "event_at",
        "importance",
        "stars",
    }
    assert record_columns <= set(_Episode.model_fields)
    assert "poignancy_status" not in record_columns


# ── AC2 — the four-token vocabulary is TEST-ENFORCED, not greppable ──────


def test_the_sql_ddl_accepts_all_four_tokens_and_rejects_a_fifth(v1_shaped_db):
    """The vocabulary is enforced by the DATABASE, in the ``.sql`` DDL."""
    _apply_up_statements(v1_shaped_db)
    for index, token in enumerate(NOTIFICATION_STATUS_VOCABULARY):
        v1_shaped_db.execute(
            "INSERT INTO memoir_proposal_ledger (id, agent_id, episode_id, proposal_date, "
            "proposed_at, notification_status, reason, created_at) "
            "VALUES (?, 'ari', ?, '2026-11-09', '2026-11-09 00:00:00', ?, '', "
            "'2026-11-09 00:00:00')",
            (f"row-{index}", f"ep{index}", token),
        )
    v1_shaped_db.commit()
    stored = {
        row[0]
        for row in v1_shaped_db.execute(
            "SELECT notification_status FROM memoir_proposal_ledger"
        )
    }
    assert stored == set(NOTIFICATION_STATUS_VOCABULARY)

    with pytest.raises(sqlite3.IntegrityError):
        v1_shaped_db.execute(
            "INSERT INTO memoir_proposal_ledger (id, agent_id, episode_id, proposal_date, "
            "proposed_at, notification_status, reason, created_at) "
            "VALUES ('row-5th', 'ari', 'ep2', '2026-11-09', '2026-11-09 00:00:00', "
            "'notified', '', '2026-11-09 00:00:00')"
        )


def test_the_model_accepts_all_four_tokens_and_rejects_a_fifth():
    """The vocabulary is enforced by the DATABASE, on the MODEL-built
    table (the one PostgreSQL actually creates)."""
    from datetime import datetime

    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    engine = create_engine("sqlite:///:memory:")
    stamp = datetime(2026, 11, 9, 0, 0, 0)
    try:
        MemoirProposalLedger.__table__.create(engine)
        with engine.begin() as conn:
            for index, token in enumerate(NOTIFICATION_STATUS_VOCABULARY):
                conn.execute(
                    MemoirProposalLedger.__table__.insert().values(
                        id=f"row-{index}",
                        agent_id="ari",
                        episode_id=f"ep{index}",
                        proposal_date="2026-11-09",
                        proposed_at=stamp,
                        notification_status=token,
                        reason="",
                        created_at=stamp,
                    )
                )
            with pytest.raises(SAIntegrityError):
                conn.execute(
                    MemoirProposalLedger.__table__.insert().values(
                        id="row-5th",
                        agent_id="ari",
                        episode_id="ep2",
                        proposal_date="2026-11-09",
                        proposed_at=stamp,
                        notification_status="notified",
                        reason="",
                        created_at=stamp,
                    )
                )
    finally:
        engine.dispose()


def test_the_vocabulary_is_closed_at_four_tokens_in_both_artifacts():
    """The count is pinned in BOTH artifacts — a fifth token added, or
    ``'enqueued'`` dropped, flips THIS pin RED (AC6)."""
    sql = _up_sql(MEMOIR_V2_MIGRATION)
    ddl_vocabulary = re.search(
        r"notification_status TEXT NOT NULL CHECK \(notification_status IN \(([^)]*)\)\)",
        sql,
    )
    assert ddl_vocabulary, "the .sql carries no notification_status CHECK"
    ddl_tokens = tuple(
        token.strip().strip("'") for token in ddl_vocabulary.group(1).split(",")
    )
    assert ddl_tokens == NOTIFICATION_STATUS_VOCABULARY, ddl_tokens

    model_check = next(
        str(constraint.sqltext)
        for constraint in SQLModel.metadata.tables["memoir_proposal_ledger"].constraints
        if constraint.__class__.__name__ == "CheckConstraint"
        and "notification_status" in str(constraint.sqltext)
    )
    model_vocabulary = re.search(r"IN \(([^)]*)\)", model_check)
    assert model_vocabulary
    model_tokens = tuple(
        token.strip().strip("'") for token in model_vocabulary.group(1).split(",")
    )
    assert model_tokens == NOTIFICATION_STATUS_VOCABULARY, model_tokens


# ── the v1 models are untouched by the v2 lane ───────────────────────────


def test_the_v1_memoir_models_are_still_registered():
    registered = {
        name for name in SQLModel.metadata.tables if name.startswith("memoir_")
    }
    assert {
        "memoir_episodes",
        "memoir_persona",
        "memoir_persona_versions",
        "memoir_syntheses",
    } <= registered


def test_the_synthesis_model_is_unchanged():
    """DD-2: the syntheses table is PRE-STAGED (empty in v1) — this lane
    only adds a BEHAVIOUR, never a schema churn."""
    columns = {col.name for col in SQLModel.metadata.tables["memoir_syntheses"].columns}
    assert columns == {
        "id",
        "agent_id",
        "claim",
        "source_episode_ids",
        "valid_from",
        "valid_to",
        "verb",
        "supersedes",
        "superseded_by",
        "confidence",
        "created_at",
    }
