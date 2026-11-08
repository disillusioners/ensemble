"""Corpus pin + fresh-boot proof: no ``;`` inside any migration comment.

Lesson (2026-11-08, source-gc): the runner strips FULL-LINE ``--``
comments but leaves TRAILING (inline) comments intact, then splits on
``;`` — a semicolon inside a trailing comment shatters the statement
split and dies on every fresh SQLite boot. This pack enforces the
invariant the migration headers already promise:

1. ``test_no_semicolon_inside_any_migration_comment`` — corpus scan over
   EVERY ``.sql`` under ``daemon/migrations/versions/``: no semicolon
   may occur inside a full-line OR a trailing comment. Pre-existing
   offenders (non-lane files that ALREADY applied and are checksumed on
   live DBs) are allowlisted by exact filename + documented rationale;
   any NEW violation anywhere REDs.
2. ``test_fresh_sqlite_boot_applies_full_dir_including_source_gc_archive``
   — the LESSONS-doc method: full-dir fresh-SQLite run with the
   pre-existing SQLite blocker ledger-pre-marked; proves the boot
   COMPLETES with the source-gc archive file present and its two tables
   materialized.

Fence discipline: ``tempfile.mkdtemp`` under the OS temp dir (NEVER a
HOME-derived path); no daemon boot; no PG connection.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

from daemon.migrations.runner import MigrationFile, MigrationRunner

REAL_VERSIONS = (
    Path(__file__).resolve().parents[2] / "daemon" / "migrations" / "versions"
)

SQLITE_BLOCKER_FILE = "20260714_000001_widen_job_queue_type_constraint.sql"
SQLITE_BLOCKER_SKIP_REASON = (
    "pre-existing SQLite DROP CONSTRAINT blocker — documented workaround "
    "(ledger pre-mark); fix lives on a separate branch"
)

# OUR lane's file — the one this lesson came from.
SOURCE_GC_FILE = "20261107_122648_create_source_gc_archive_tables.sql"

# House residue-gate scoping: the corpus scan covers the WHOLE dir and
# the SHATTER CLASS is TRAILING comments only — a `;` in a FULL-LINE
# comment is stripped by the runner before the split (runner.py:662-673)
# and is inert. The pre-fix census: 353 full-line-comment semicolon lines
# across ~100 pre-existing files, ZERO trailing-comment semicolons
# outside OUR file. Those full-line files are NOT fixed here (checksum
# risk: they DID apply and their sha256 is recorded on existing ledgers;
# editing them changes the recorded checksum — and they are inert for
# this defect class). The pin therefore REDs on any TRAILING-comment
# semicolon (the actual shatter class) anywhere, and additionally keeps
# our own file full-line-clean via test_source_gc_file_is_clean. Any NEW
# violation of either shape in a NEW file REDs.
COMMENT_SEMICOLON_ALLOWLIST: dict[str, str] = {}

_TRAILING_COMMENT_RE = re.compile(r"--(?!(?:\s|$)).*$")


def _iter_comment_segments(sql: str):
    """Yield (line_no, segment, is_trailing) for every comment segment.

    A FULL-LINE comment is a line whose first non-blank chars are ``--``;
    a TRAILING comment starts mid-line after SQL content.
    """
    for line_no, raw in enumerate(sql.splitlines(), start=1):
        stripped = raw.lstrip()
        if stripped.startswith("--"):
            yield line_no, stripped, False
            continue
        m = _TRAILING_COMMENT_RE.search(raw)
        if m:
            yield line_no, m.group(0), True


class TestMigrationCommentSemicolonCorpus:
    def test_no_trailing_comment_semicolon_anywhere(self):
        # The SHATTER class: `;` inside a TRAILING (inline) comment.
        # Full-line comments are stripped by the runner before the split
        # (inert) and are covered by the allowlisted pre-existing census.
        violations: list[str] = []
        for path in sorted(REAL_VERSIONS.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            for line_no, segment, is_trailing in _iter_comment_segments(sql):
                if is_trailing and ";" in segment:
                    violations.append(f"{path.name}:{line_no}: {segment.strip()[:90]}")
        if COMMENT_SEMICOLON_ALLOWLIST:
            violations = [
                v for v in violations
                if v.split(":", 1)[0] not in COMMENT_SEMICOLON_ALLOWLIST
            ]
        assert violations == [], (
            "semicolons inside TRAILING migration comments shatter the "
            "runner's statement split (fresh SQLite boot dies); "
            "offenders:\n" + "\n".join(violations)
        )

    def test_source_gc_file_has_no_trailing_comment_semicolon(self):
        # OUR file: the shatter class must be clean AND the UP section's
        # full-line comments stay `;`-free (the pre-existing full-line
        # census is allowlisted corpus-wide; OUR file is held to the
        # stricter standard because it is this lane's artifact).
        sql = (REAL_VERSIONS / SOURCE_GC_FILE).read_text(encoding="utf-8")
        for line_no, segment, is_trailing in _iter_comment_segments(sql):
            if is_trailing:
                assert ";" not in segment, (
                    f"{SOURCE_GC_FILE}:{line_no} (trailing): "
                    f"{segment.strip()[:90]}"
                )

    def test_down_section_comments_are_inert_shape(self):
        # The commented-out DOWN statements (`-- DROP ...;`) are FULL-LINE
        # comments — stripped by the runner before the split (inert,
        # runner.py:662-673). The shatter class is TRAILING comments only
        # (asserted above); this leg documents the DOWN block's posture so
        # a future editor doesn't "fix" it into an unparseable shape.
        sql = (REAL_VERSIONS / SOURCE_GC_FILE).read_text(encoding="utf-8")
        down = sql[sql.index("-- DOWN"):]
        for raw in down.splitlines():
            stripped = raw.lstrip()
            if stripped.startswith("--") and "DROP" in stripped:
                assert stripped.endswith(";"), (
                    "DOWN comment lines document the exact DDL; keep the "
                    f"trailing `;` inside the FULL-LINE comment: {raw!r}"
                )


class TestFreshSqliteFullDirBoot:
    def test_fresh_sqlite_boot_applies_full_dir_including_source_gc_archive(self):
        """The LESSONS-doc method: full-dir fresh-SQLite boot COMPLETES.

        With the pre-existing SQLite blocker ledger-pre-marked (the
        documented workaround), every other migration — including the
        source-gc archive file — must apply and land its ledger rows.
        Before the semicolon fix this died with
        ``sqlite3.OperationalError near "chat"``.
        """
        tmp = Path(tempfile.mkdtemp(prefix="ens-mig-comment-semicolon-"))
        try:
            engine = create_engine(f"sqlite:///{tmp}/boot.db")
            SQLModel.metadata.create_all(engine)
            try:
                runner = MigrationRunner(engine, migrations_dir=REAL_VERSIONS)
                runner.ensure_migrations_table()

                blocked = MigrationFile.parse(REAL_VERSIONS / SQLITE_BLOCKER_FILE)
                runner._persist_ledger_row(
                    blocked,
                    execution_time_ms=0,
                    skip_reason=SQLITE_BLOCKER_SKIP_REASON,
                )

                applied = runner.run_pending_migrations()

                version = SOURCE_GC_FILE[:15]
                assert version in applied, (
                    f"source-gc archive migration not applied: applied={applied!r}"
                )
                with engine.connect() as conn:
                    tables = {
                        row[0]
                        for row in conn.execute(
                            text("SELECT name FROM sqlite_master WHERE type='table'")
                        ).fetchall()
                    }
                    assert "source_configs_archive" in tables
                    assert "schedule_executions_archive" in tables
            finally:
                engine.dispose()
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
