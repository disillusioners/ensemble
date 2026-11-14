"""Migration-structure tests for the project_future .sql record (AC20).

Marker-grep pattern of test_project_metadata_table.py:583-611. The migration
runner NEVER executes .sql on PG — the file is the SQLite-side canonical DDL +
permanent record (create_all handles the new table).
"""

import re
from pathlib import Path

MIGRATIONS_DIR = Path("daemon/migrations/versions")


def _find_migration() -> Path:
    matches = sorted(MIGRATIONS_DIR.glob("*_create_project_future.sql"))
    assert len(matches) == 1, (
        f"Expected exactly one create_project_future migration, found: {matches}"
    )
    return matches[0]


def test_migration_filename_matches_stamp_convention():
    """AC20: filename matches ^\\d{8}_\\d{6}_create_project_future\\.sql$ (A23 stamp guard)."""
    path = _find_migration()
    assert re.fullmatch(r"\d{8}_\d{6}_create_project_future\.sql", path.name), path.name


def test_migration_has_up_and_down_markers():
    """AC20: -- UP / -- DOWN sections present."""
    content = _find_migration().read_text()
    assert "-- UP" in content
    assert "-- DOWN" in content


def test_migration_has_create_table_if_not_exists():
    """AC20: CREATE TABLE IF NOT EXISTS project_future (memoir doctrine :64-73)."""
    content = _find_migration().read_text()
    assert "CREATE TABLE IF NOT EXISTS project_future" in content


def test_migration_has_cascade_fk():
    """AC20: FK references projects(project_id) ON DELETE CASCADE."""
    content = _find_migration().read_text()
    assert "ON DELETE CASCADE" in content
    assert "REFERENCES projects(project_id)" in content


def test_migration_has_both_indexes():
    """AC20: the exact 3-site lockstep index names (model ↔ migration ↔ create_all)."""
    content = _find_migration().read_text()
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_project_future_project_seq" in content
    assert "CREATE INDEX IF NOT EXISTS ix_project_future_project_id" in content


def test_migration_down_drops_exactly_what_up_created():
    """AC20: DOWN drops exactly the two indexes + the table."""
    content = _find_migration().read_text()
    down = content.split("-- DOWN", 1)[1]
    assert "DROP INDEX IF EXISTS uq_project_future_project_seq" in down
    assert "DROP INDEX IF EXISTS ix_project_future_project_id" in down
    assert "DROP TABLE IF EXISTS project_future" in down
    # nothing else is dropped
    drops = [ln.strip() for ln in down.splitlines() if ln.strip().startswith("DROP")]
    assert len(drops) == 3, drops


def test_migration_no_varchar_on_non_comment_lines():
    """AC20 (C1 scoping): zero VARCHAR tokens on NON-COMMENT lines.

    The mandated W2 doctrine comment contains "VARCHAR" 4× verbatim and MUST
    stay — the naive whole-file negative grep is UNSATISFIABLE against it.
    Filter comment lines first, then assert the absence (equivalent of
    ``grep -v '^[[:space:]]*--'``). PG's create_all-side VARCHAR(300)/(5000)
    from max_length is accepted reality (R4 scope).
    """
    content = _find_migration().read_text()
    non_comment = [
        ln for ln in content.splitlines() if not ln.lstrip().startswith("--")
    ]
    joined = "\n".join(non_comment)
    assert "VARCHAR" not in joined, (
        f"VARCHAR found on a non-comment line: "
        f"{[ln for ln in non_comment if 'VARCHAR' in ln]}"
    )
    # and TEXT is actually used on the string columns
    for col in ("id TEXT", "project_id TEXT", "summary TEXT NOT NULL",
                "details TEXT", "source_agent TEXT NOT NULL",
                "source_instance_id TEXT NOT NULL"):
        assert col in joined, col


def test_migration_no_duplicate_prefix():
    """Risk-15: no other file shares the timestamp prefix (DuplicateMigrationVersionError guard)."""
    path = _find_migration()
    prefix = path.name.split("_create_project_future")[0]
    siblings = [p.name for p in MIGRATIONS_DIR.glob(f"{prefix}_*")]
    assert siblings == [path.name], siblings
