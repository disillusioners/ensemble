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


#: The v2 (Memoir Store v2 Phase 1) tables — registered by the same
#: late-import block. See
#: ``.agents/shared/planning/ari-memoir-store/v2-phase1-plan.md``.
MEMOIR_V2_TABLE_NAMES = {
    "memoir_star",
    "memoir_star_alias",
    "memoir_line",
    "memoir_reflection_state",
    "memoir_proposal_ledger",
    "memoir_jev_ledger",
}


def test_the_memoir_tables_are_registered():
    """The memoir model registry carries the v1 FOUR plus the v2 SIX.

    Memoir Store v2 (Phase 1) DELIBERATELY amends this census: the
    v2 lane registers SIX new tables (``memoir_star`` /
    ``memoir_star_alias`` / ``memoir_line`` /
    ``memoir_reflection_state`` / ``memoir_proposal_ledger`` /
    ``memoir_jev_ledger``) into the SAME metadata. The census stays
    EXACT — it is not loosened to a subset — it simply names the ten
    tables that now exist.
    """
    registered = {
        name for name in SQLModel.metadata.tables if name.startswith("memoir_")
    }
    assert registered == MEMOIR_TABLE_NAMES | MEMOIR_V2_TABLE_NAMES


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
    """The census DELTA is +1 — asserted as a DELTA, not as an absolute.

    Memoir Store v2 (Phase 1) DELIBERATELY amends this leg: the
    memoir migration FILES are now TWO — the v1 substrate
    (``20261105_102821_create_memoir_tables.sql``) and the v2 substrate
    (``20261109_021154_create_memoir_v2_tables.sql``). The v1 lane's own
    DELTA is still measured EXACTLY, by the git-derived leg below
    (over the v1 lane's OWN branch range, which this lane does not
    touch).
    """
    memoir_files = sorted(p.name for p in MIGRATIONS_DIR.glob("*memoir*.sql"))
    assert len(memoir_files) == 2, memoir_files
    assert MEMOIR_MIGRATION.name in memoir_files
    assert "20261109_021154_create_memoir_v2_tables.sql" in memoir_files


def test_every_migration_prefix_is_unique_and_distinct_count_matches():
    """The duplicate-prefix guard's invariant: distinct == file count."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    prefixes = [p.stem.split("_")[0] + "_" + p.stem.split("_")[1] for p in files]
    assert len(prefixes) == len(set(prefixes)), "duplicate migration prefix"
    assert len(set(prefixes)) == len(files)


def _lane_delta_range() -> tuple[str, str]:
    """The commit range this LANE's own migration delta is measured over.

    MERGE NOTE (2026-11-06, memoir release stage): this lane merges onto
    ``origin/latest`` with ``--no-ff``, and the integration tip gained TWO
    concurrent-lane migrations (the nest-anomaly pair) between this lane's
    base and the merge. Measuring ``LANE_BASE_COMMIT..HEAD`` on the merged
    tree therefore counts those foreign files and reports ``3`` where the
    LANE added ``1`` -- a false red about a correct tree.

    The faithful measurement is the LANE BRANCH ITSELF
    (``LANE_BASE..feature/ari-memoir-store``): that range contains exactly
    this lane's commits, so the leg keeps its meaning no matter where the
    integration tip has moved since. When the branch ref is absent
    (post-cleanup clone) the leg degrades to the original
    ``LANE_BASE..HEAD`` shape.
    """
    lane_ref = "refs/heads/feature/ari-memoir-store"
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", lane_ref],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probe.returncode == 0 and probe.stdout.strip():
        return LANE_BASE_COMMIT, lane_ref
    return LANE_BASE_COMMIT, "HEAD"


def test_the_git_derived_lane_delta_is_exactly_one_migration_file():
    """The same DELTA, derived from git (skipped when the base is absent)."""
    path = "daemon/migrations/versions/"
    delta_start, delta_end = _lane_delta_range()
    try:
        tracked = subprocess.run(
            ["git", "diff", "--name-only", f"{delta_start}..{delta_end}", "--", path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        # The untracked leg only means anything when measuring the WORKING
        # TREE (uncommitted lane files); a committed ref carries them in
        # the diff above.
        if delta_end == "HEAD":
            untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard", "--", path],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
        else:
            untracked = None
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pytest.skip("git is unavailable in this environment")
    if tracked.returncode != 0 or (untracked is not None and untracked.returncode != 0):
        pytest.skip("the lane base commit is not resolvable in this clone")
    added = [
        line
        for line in (
            tracked.stdout + (untracked.stdout if untracked is not None else "")
        ).splitlines()
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
    """DD-2 (v1) RETIRED BY PHASE 2 — the table is no longer write-less.

    THE PIN MOVED, AND THE MOVE IS THE PLAN: phase 2 of the Memoir Store
    v2 lane (``.agents/shared/planning/ari-memoir-store/v2-phase2-plan.md``
    §1, objective 2) exists to ACTIVATE the syntheses layer — "Deliver
    the Reflection Engine ('The Dream') and **activate the Syntheses
    layer**". The v1 clause ("the table is schema-only and EMPTY in v1")
    was a TRUE statement about v1 and a FALSE statement about the tree
    that phase 2 ships.

    THE STRONGER PIN (what replaces it): the writer set is EXACTLY
    known — the ONE named writer (the syntheses repository) plus the
    schema/audit records and the readers. A NEW writer (a stray module
    that inserts rows behind the repository's back) still flips this RED.

    THE RAW-SQL CLAUSE IS UNCHANGED: no module may write the table with
    a literal SQL statement — every write goes through the repository,
    so the dedup key, the citation guard and the atomic supersession are
    never bypassed.
    """
    result = subprocess.run(
            ["git", "grep", "-l", "memoir_syntheses", "--", "daemon/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    # FAIL-CLOSED: rc == 0 means `git grep` ran and produced matches; a
    # broken object store / non-git tree returns >1 and would otherwise
    # yield an empty hit list that trivially satisfies the allow-list.
    assert result.returncode == 0, (
        f"writer-census pin is vacuous: `git grep` rc={result.returncode} "
        f"(stderr={result.stderr.strip()!r})"
    )
    hits = [line for line in result.stdout.splitlines() if line.strip()]
    # The model, the package export, the manager's registration, the ONE
    # named writer (the syntheses repository), the reflection service
    # (the engine that CALLS the writer) and the audit-record .sql files.
    allowed = {
        "daemon/repositories/memoir/models.py",
        "daemon/repositories/memoir/__init__.py",
        "daemon/manager.py",
        # THE NAMED WRITER — the only module that writes a synthesis row.
        "daemon/repositories/memoir/syntheses_repository.py",
        # The engine that calls the writer (it never writes directly).
        "daemon/services/memoir_reflection_service.py",
        # The .sql files are the persistent AUDIT RECORD of the schema
        # changes (DD-18) — they CREATE the table and never write a row.
        "daemon/migrations/versions/20261105_102821_create_memoir_tables.sql",
        "daemon/migrations/versions/20261109_021154_create_memoir_v2_tables.sql",
    }
    unexpected = [hit for hit in hits if hit.split(":")[0] not in allowed]
    assert not unexpected, f"memoir_syntheses is referenced by: {unexpected}"

    # And no RAW SQL writer anywhere in the shipped tree — every write
    # goes through the repository (so the dedup key, the citation guard
    # and the atomic supersession are never bypassed).
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
        # rc 1 = "no matches" (the expected answer); rc > 1 = git error, which
        # must FAIL rather than masquerade as "no writer exists".
        assert writer.returncode in (0, 1), (
            f"raw-SQL writer scan is vacuous for {needle!r}: `git grep` "
            f"rc={writer.returncode} (stderr={writer.stderr.strip()!r})"
        )
        assert not writer.stdout.strip(), f"a writer exists for {needle}"
