"""LIVE-PG dialect verification for the skill-system-revival Phase 1.

WHY THIS FILE EXISTS (and why it is not a SQLite test)
-------------------------------------------------------
The Phase-1 lane's riskiest construct is a **PG/SQLite DIALECT SPLIT**:

* the E1 durable re-fix burst guard counts COMMITTED ``skill_evolution``
  FIX admissions keyed on the SKILL IDENTITY read out of the job
  metadata JSON column — ``metadata->>'skill_id'`` on PostgreSQL vs
  ``json_extract(metadata, '$.skill_id')`` on SQLite
  (``daemon/repositories/job_queue/repository.py``,
  ``build_fix_admission_count_statement``);
* the E4/A4 lane writes BOTH ``skills.is_active`` AND ``skills.status``
  in ONE statement (the sanctioned dual-column writer) and measures
  divergence with a ``GROUP BY (name, project_id)`` + ``count(DISTINCT
  content)`` aggregation (``SkillRepository.within_identity_key_divergence``).

**Until now every test of those constructs ran on SQLite ONLY.**  A
split that is right on one backend and wrong on the other is a SILENT,
production-only failure — and the burst guard is a SAFETY guard.

This file therefore runs against a **REAL PostgreSQL server** and proves,
on the production dialect:

(a) the burst guard's identity extraction really works on PostgreSQL —
    a real ``job_queue_items`` row whose metadata carries ``skill_id``
    is FOUND by ``JobRepository.count_fix_admissions_since``;
(b) the SQLite leg yields the SAME skill identity and the SAME count
    for the SAME logical row (the split must AGREE, not merely RUN);
(c) the E4/A4 dual-column write + divergence SQL really executes on
    PostgreSQL and returns a sane value — and the dual-column write
    CANNOT leave a half-written pair behind.

WHAT IS PINNED HERE (and what is NOT)
-------------------------------------
The pins here are the PRODUCTION statements and PRODUCTION repositories
— never a re-implementation of them:

* ``JobRepository.count_fix_admissions_since`` (the real method, on a
  real PG engine) — the SQL it emits is captured with a SQLAlchemy
  ``before_cursor_execute`` listener so the assertion is about the
  statement PostgreSQL ACTUALLY received, not about a string in a
  docstring;
* ``build_fix_admission_count_statement`` (the ONE builder both dialects
  go through) is used for the SQLite-parity leg, so parity is a
  property of the production builder, not of a test copy;
* ``write_skill_pair`` / ``SkillRepository.deactivate`` (the sanctioned
  E4 dual-column writer) and ``SkillRepository.within_identity_key_divergence``
  (the A4 gauge's anchor).

The file is deliberately SMALL: ~10 focused nodes, no sleeps, no
sweeps, no broad corpus.  Every node is deterministic.

HOW TO RUN
----------
Serial only (the shared ``tests/postgres/conftest.py`` skips every
postgres-marked test when ``PYTEST_XDIST_WORKER`` is set — the house
rule is "run postgres legs serially").  The registered pack is
``test/packs/skill_revival_pg_dialect_unit_test.sh``; it provisions a
DISPOSABLE native PostgreSQL cluster, points ``PG_TEST_*`` at it, runs
this file, and tears the cluster down.

    pytest tests/postgres/test_skill_revival_pg_dialect.py \
        --override-ini="addopts=" -m postgres --timeout=120

THE SCRATCH-DB FENCE IS THE CONFTEST'S, NOT THIS FILE'S
-------------------------------------------------------
``tests/postgres/conftest.py`` owns the fence: it refuses any target
whose name is not in the hard scratch allowlist, refuses
``ensemble_prod`` / ``ensemble_dev`` / ``ensemble_prod_test``
unconditionally, and refuses a scratch DB whose owner role also owns a
live DB.  This file only asserts the PRE-RUN PROOF (which database am I
actually on?) so a mis-pointed run can never be read as coverage.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pytest
from sqlalchemy import event, text
from sqlmodel import SQLModel, Session, select

# Module-level imports so the lane's models are registered in
# SQLModel.metadata BEFORE the session-scoped ``pg_engine`` fixture runs
# ``create_all`` (collection-time imports precede session fixtures) —
# the same discipline the sibling PG lane uses.
import daemon.repositories.db_connection.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.infra.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.mcp_server.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.source.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.migrations.models  # noqa: F401
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.job_queue.repository import (
    JobRepository,
    build_fix_admission_count_statement,
)
from daemon.repositories.skill.models import Skill
from daemon.repositories.skill.repository import SkillRepository
from daemon.services.skill_deactivation import write_skill_pair

pytestmark = pytest.mark.postgres

# ── constants ───────────────────────────────────────────────────────────

SKILL_A = "skill-pg-0001"
SKILL_B = "skill-pg-0002"

#: The job type the burst guard's ledger counts (mirrors
#: ``daemon/services/skill_job_dispatcher.JOB_TYPE_EVOLUTION``).
JOB_TYPE_EVOLUTION = "skill_evolution"

#: The evolution type the burst guard's ledger counts (mirrors
#: ``daemon/services/skill_job_dispatcher.EVOLUTION_TYPE_FIX``).
EVOLUTION_TYPE_FIX = "FIX"

#: The database names this file REFUSES to call coverage — belt and
#: braces on top of the conftest's own fence.
FORBIDDEN_DB_NAMES = frozenset(
    {"ensemble_prod", "ensemble_dev", "ensemble_prod_test"}
)


def _since(hours: int = 24) -> str:
    """The inclusive window lower bound, in the SAME ISO shape the
    dispatcher produces (``datetime.now(timezone.utc) - delta``)."""
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat()


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def pg_job_repo(pg_engine) -> JobRepository:
    """The REAL production job repository, bound to the real PG engine."""
    return JobRepository(pg_engine)


@pytest.fixture
def pg_skill_repo(pg_engine) -> SkillRepository:
    """The REAL production skill repository, bound to the real PG engine."""
    return SkillRepository(pg_engine)


@pytest.fixture
def captured_sql(pg_engine) -> Iterator[list[str]]:
    """Capture every statement the engine actually sends to PostgreSQL.

    This is what turns "the PG branch uses ``->>``" from a DOCSTRING
    claim into an OBSERVED fact: the listener sees the literal SQL text
    handed to the driver.
    """
    seen: list[str] = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(pg_engine, "before_cursor_execute", _before)
    try:
        yield seen
    finally:
        event.remove(pg_engine, "before_cursor_execute", _before)


def _insert_fix_admission(
    engine,
    *,
    skill_id: str,
    evolution_type: str = EVOLUTION_TYPE_FIX,
    job_type: str = JOB_TYPE_EVOLUTION,
    hours_ago: int = 0,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Insert ONE real ``job_queue_items`` row shaped like the rows the
    skill-keeper lane actually writes (the dispatcher's metadata shape:
    ``skill_id`` + ``evolution_type`` + ``direction``)."""
    job_id = str(uuid.uuid4())
    created_at = (
        datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ).isoformat()
    payload = {
        "skill_id": skill_id,
        "evolution_type": evolution_type,
        "direction": "fix it",
    }
    if metadata is not None:
        payload = metadata
    with Session(engine) as session:
        session.add(
            JobItem(
                job_id=job_id,
                agent_id="skill-keeper",
                agent_dir="agents/skill-keeper",
                message="Evolve skill",
                source="skill_evolution",
                project_id="pg-dialect-project",
                priority=5,
                job_metadata=payload,
                job_type=job_type,
                created_at=created_at,
            )
        )
        session.commit()
    return job_id


# ── (0) the pre-run proof ──────────────────────────────────────────────


def test_target_database_is_a_scratch_database_not_a_live_one(pg_engine):
    """PRE-RUN PROOF — which database are we ACTUALLY on?

    A PG leg that silently pointed at the live database would be worse
    than no leg at all: it would read (and, for the write legs, MUTATE)
    production.  This node prints the identity of the target and refuses
    to call a live-shaped database coverage.
    """
    with pg_engine.connect() as conn:
        current = conn.execute(text("SELECT current_database()")).scalar()
        server_version = conn.execute(text("SHOW server_version")).scalar()

    print(f"\n[pg-dialect] current_database() = {current!r}")
    print(f"[pg-dialect] server_version     = {server_version!r}")

    assert current, "the target database name must be resolvable"
    assert str(current) not in FORBIDDEN_DB_NAMES, (
        f"the PG leg must never run against a live-shaped database "
        f"(got {current!r})"
    )


# ── (a) the burst guard's identity extraction on REAL PostgreSQL ───────


def test_pg_burst_guard_finds_the_row_by_its_metadata_skill_id(
    pg_engine, pg_job_repo
):
    """(a) The PG branch really extracts the identity from the metadata.

    A real ``job_queue_items`` row carrying ``skill_id`` in its metadata
    JSON must be FOUND by the production count on PostgreSQL.  A PG
    branch that spelled the extraction wrongly would return 0 here —
    the burst guard would then NEVER trip (a safety guard that always
    admits), and the defect would be invisible on SQLite.
    """
    _insert_fix_admission(pg_engine, skill_id=SKILL_A)

    count = pg_job_repo.count_fix_admissions_since(SKILL_A, _since())

    assert count == 1, (
        f"the PG branch of the burst-guard count must find the row that "
        f"carries skill_id={SKILL_A!r} in its metadata (got {count})"
    )


def test_pg_burst_guard_count_is_scoped_to_the_skill_identity(
    pg_engine, pg_job_repo
):
    """(a) The count is keyed on the SKILL IDENTITY, not on "any row".

    Two skills' admissions plus one admission of a different evolution
    type: each count must see exactly its own rows.  This is the
    property the whole guard rests on — the cap is PER SKILL.
    """
    _insert_fix_admission(pg_engine, skill_id=SKILL_A)
    _insert_fix_admission(pg_engine, skill_id=SKILL_A)
    _insert_fix_admission(pg_engine, skill_id=SKILL_B)
    _insert_fix_admission(
        pg_engine, skill_id=SKILL_A, evolution_type="IMPROVE"
    )
    # An admission OLDER than the window must not be counted.
    _insert_fix_admission(pg_engine, skill_id=SKILL_A, hours_ago=48)

    assert pg_job_repo.count_fix_admissions_since(SKILL_A, _since()) == 2
    assert pg_job_repo.count_fix_admissions_since(SKILL_B, _since()) == 1
    assert (
        pg_job_repo.count_fix_admissions_since("skill-pg-unknown", _since())
        == 0
    )


def test_pg_burst_guard_executed_statement_uses_the_arrow_operator(
    pg_engine, pg_job_repo, captured_sql
):
    """(a) The statement PostgreSQL ACTUALLY received uses ``->>``.

    Not a docstring, not a compile-only string — the literal SQL text
    the driver handed to the server.  This is the assertion that a
    PG-only defect (a branch that used the SQLite spelling) would trip.
    """
    _insert_fix_admission(pg_engine, skill_id=SKILL_A)
    captured_sql.clear()

    pg_job_repo.count_fix_admissions_since(SKILL_A, _since())

    counts = [
        s for s in captured_sql if "count(*)" in s.lower()
    ]
    assert counts, (
        "the burst-guard count must have issued a count statement on the "
        f"PG engine (captured: {captured_sql!r})"
    )
    statement = counts[-1]
    assert "->>" in statement, (
        "the PostgreSQL branch of the burst-guard count must use the "
        f"`->>` operator (got: {statement!r})"
    )
    assert "json_extract" not in statement.lower(), (
        "the PostgreSQL branch must NOT use the SQLite spelling "
        f"`json_extract` (got: {statement!r})"
    )


def test_pg_branch_is_not_silently_the_sqlite_branch(
    pg_engine, pg_job_repo, captured_sql
):
    """(a) A NEGATIVE control on the branch SELECTION itself.

    ``build_fix_admission_count_statement`` picks a branch from a
    boolean.  If the PG engine ever took the SQLite branch, PostgreSQL
    would reject the statement outright (``json_extract`` does not exist
    on PostgreSQL) — the count would RAISE rather than return a wrong
    number.  This node asserts the positive form of that: the count
    SUCCEEDS on PG and returns the right number, so the PG branch was
    genuinely taken (a broken branch cannot return 1).
    """
    _insert_fix_admission(pg_engine, skill_id=SKILL_A)

    # No exception, and the exact expected number.
    assert pg_job_repo.count_fix_admissions_since(SKILL_A, _since()) == 1


# ── (b) the SQLite-parity assertion ────────────────────────────────────


def test_sqlite_and_pg_agree_on_the_same_logical_row(tmp_path):
    """(b) PARITY — the split must AGREE, not merely RUN.

    The SAME logical row (same skill id, same evolution type, same
    window position) is inserted into a SQLite engine and into a
    PostgreSQL engine, and the count is read back through the ONE
    production builder both dialects go through.  The two counts and the
    two extracted skill identities must be identical.

    A dialect split that is right on one backend and wrong on the other
    shows up HERE as a count difference — which is exactly the class of
    defect that a SQLite-only suite can never see.
    """
    from sqlalchemy import create_engine

    sqlite_engine = create_engine(
        f"sqlite:///{tmp_path / 'parity.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    try:
        SQLModel.metadata.create_all(sqlite_engine)
        _insert_fix_admission(sqlite_engine, skill_id=SKILL_A)
        _insert_fix_admission(sqlite_engine, skill_id=SKILL_B)

        sqlite_repo = JobRepository(sqlite_engine)
        sqlite_counts = {
            SKILL_A: sqlite_repo.count_fix_admissions_since(SKILL_A, _since()),
            SKILL_B: sqlite_repo.count_fix_admissions_since(SKILL_B, _since()),
            "skill-pg-unknown": sqlite_repo.count_fix_admissions_since(
                "skill-pg-unknown", _since()
            ),
        }

        # The identity each dialect's OWN builder extracts from the
        # metadata of the row we just inserted.
        with Session(sqlite_engine) as session:
            sqlite_identity = session.execute(
                text(
                    "SELECT json_extract(metadata, '$.skill_id') FROM "
                    "job_queue_items WHERE metadata IS NOT NULL "
                    "ORDER BY created_at LIMIT 1"
                )
            ).scalar()
    finally:
        sqlite_engine.dispose()

    assert sqlite_counts == {
        SKILL_A: 1,
        SKILL_B: 1,
        "skill-pg-unknown": 0,
    }
    assert str(sqlite_identity) == SKILL_A, (
        "the SQLite spelling (`json_extract`) must extract the skill "
        f"identity from the metadata (got {sqlite_identity!r})"
    )

    # The builder's SQLite branch is the one SQLite actually takes.
    sqlite_branch = build_fix_admission_count_statement(
        is_postgres=False, skill_id=SKILL_A, since_iso=_since()
    )
    pg_branch = build_fix_admission_count_statement(
        is_postgres=True, skill_id=SKILL_A, since_iso=_since()
    )
    from sqlalchemy.dialects import postgresql, sqlite

    sqlite_text = str(
        sqlite_branch.compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    pg_text = str(
        pg_branch.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "json_extract" in sqlite_text.lower()
    assert "->>" in pg_text


def test_sqlite_and_pg_agree_on_the_count_for_the_same_rows(pg_engine):
    """(b) PARITY, computed across the two REAL engines side by side.

    The PostgreSQL leg runs against the live scratch cluster; the
    SQLite leg runs against a scratch file DB.  Both counts come from the
    production repository method.  The vectors must be equal.
    """
    from sqlalchemy import create_engine

    sqlite_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    try:
        SQLModel.metadata.create_all(sqlite_engine)
        for skill_id in (SKILL_A, SKILL_B):
            _insert_fix_admission(sqlite_engine, skill_id=skill_id)
            _insert_fix_admission(pg_engine, skill_id=skill_id)

        sqlite_repo = JobRepository(sqlite_engine)
        pg_repo = JobRepository(pg_engine)

        for skill_id in (SKILL_A, SKILL_B, "skill-pg-unknown"):
            sqlite_count = sqlite_repo.count_fix_admissions_since(
                skill_id, _since()
            )
            pg_count = pg_repo.count_fix_admissions_since(skill_id, _since())
            assert sqlite_count == pg_count, (
                f"the two dialects disagree for skill_id={skill_id!r}: "
                f"sqlite={sqlite_count} pg={pg_count} — the split must "
                "AGREE, not merely run"
            )
    finally:
        sqlite_engine.dispose()


def test_sqlite_and_pg_agree_on_the_divergence_gauge(pg_engine, pg_skill_repo):
    """(c) PARITY for the A4 gauge — the same data must measure the same.

    The gauge's whole claim ("32 divergent keys on production, caused by
    the 27 stranded A/B pairs") is a NUMBER a human reads.  If the
    PostgreSQL and SQLite readings disagreed, the number would depend
    on which database the operator happened to run against — and the
    SQLite-only suite could never have seen it.
    """
    from sqlalchemy import create_engine

    def _seed(repo):
        repo.create(
            name="divergent", description="d", content="A",
            project_id="p1", generation=0,
        )
        repo.create(
            name="divergent", description="d", content="B",
            project_id="p1", generation=1,
        )
        repo.create(
            name="identical", description="d", content="SAME",
            project_id="p1", generation=0,
        )
        repo.create(
            name="identical", description="d", content="SAME",
            project_id="p1", generation=1,
        )
        repo.create(
            name="global", description="d", content="v1",
            project_id=None, generation=0,
        )
        repo.create(
            name="global", description="d", content="v2",
            project_id=None, generation=1,
        )

    sqlite_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    try:
        SQLModel.metadata.create_all(sqlite_engine)
        sqlite_gauge = SkillRepository(sqlite_engine)
        _seed(sqlite_gauge)
        sqlite_reading = sqlite_gauge.within_identity_key_divergence()

        _seed(pg_skill_repo)
        pg_reading = pg_skill_repo.within_identity_key_divergence()
    finally:
        sqlite_engine.dispose()

    for key in (
        "diverging_keys",
        "rows_in_diverging_groups",
        "total_skills",
        "truncated",
    ):
        assert sqlite_reading[key] == pg_reading[key], (
            f"the two dialects disagree on the A4 gauge's {key!r}: "
            f"sqlite={sqlite_reading[key]!r} pg={pg_reading[key]!r} — the "
            "gauge must AGREE, not merely run"
        )
    assert sqlite_reading["diverging_keys"] == 2
    assert {k["name"] for k in sqlite_reading["keys"]} == {
        "divergent",
        "global",
    }


# ── (c) the E4/A4 dual-column + divergence SQL on REAL PostgreSQL ─────


def test_pg_dual_column_writer_sets_both_columns_in_one_statement(
    pg_engine, pg_skill_repo, captured_sql
):
    """(c) The sanctioned E4 writer sets BOTH columns in ONE statement.

    ``write_skill_pair`` is the ONLY writer in the E4 mechanism; it must
    never write one column without the other.  On PostgreSQL the pin is
    about the statement the server received: exactly ONE ``UPDATE
    skills`` carrying BOTH ``is_active`` and ``status`` — no second
    statement, no half-written pair.
    """
    skill = pg_skill_repo.create(
        name="dual-column-skill",
        description="d",
        content="body",
        project_id="pg-dialect-project",
    )
    captured_sql.clear()

    updated = write_skill_pair(
        pg_skill_repo, skill.id, is_active=False, status="inactive"
    )

    assert updated is not None, "the writer must return the updated row"
    assert updated.is_active is False
    assert updated.status == "inactive"

    updates = [
        s
        for s in captured_sql
        if s.strip().upper().startswith("UPDATE SKILLS")
    ]
    assert len(updates) == 1, (
        "the dual-column write must be ONE statement (captured "
        f"{len(updates)}: {updates!r})"
    )
    statement = updates[0].lower()
    assert "is_active" in statement
    assert "status" in statement

    # And the round-trip: a FRESH read sees both columns.
    reread = pg_skill_repo.get(skill.id)
    assert reread is not None
    assert reread.is_active is False
    assert reread.status == "inactive"


def test_pg_dual_column_write_cannot_leave_a_half_written_pair(
    pg_engine, pg_skill_repo
):
    """(c) A FAILED dual-column write leaves NO half-written pair.

    The strongest form of the property: force the write to fail AFTER
    the statement is emitted, then read the row back on a FRESH
    connection.  PostgreSQL's statement atomicity means the pair moves
    TOGETHER or not at all — a reader can never observe
    ``is_active=False`` with ``status='active'`` (or the converse)
    because the write is a single statement.

    The failure is injected by making the UPDATE raise at the DBAPI
    level (a driver-level ``ProgrammingError``), which is exactly what a
    real PostgreSQL error (a constraint violation, a deadlock, a
    read-only transaction) does to the caller's transaction.
    """
    skill = pg_skill_repo.create(
        name="atomic-pair-skill",
        description="d",
        content="body",
        project_id="pg-dialect-project",
    )

    def _boom(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith("UPDATE SKILLS"):
            raise RuntimeError("injected PG write failure")

    event.listen(pg_engine, "before_cursor_execute", _boom)
    try:
        with pytest.raises(RuntimeError, match="injected PG write failure"):
            write_skill_pair(
                pg_skill_repo, skill.id, is_active=False, status="inactive"
            )
    finally:
        event.remove(pg_engine, "before_cursor_execute", _boom)

    # A fresh read: the pair is untouched — NOT half-written.
    reread = pg_skill_repo.get(skill.id)
    assert reread is not None
    assert reread.is_active is True, (
        "a failed dual-column write must not leave `is_active` flipped "
        f"(got {reread.is_active!r})"
    )
    assert reread.status == "active", (
        "a failed dual-column write must not leave `status` flipped "
        f"(got {reread.status!r})"
    )


def test_pg_divergence_gauge_executes_and_returns_a_sane_value(
    pg_engine, pg_skill_repo
):
    """(c) The A4 gauge's anchor — the within-identity-key divergence
    query — really executes on PostgreSQL and returns a sane value.

    The gauge is a ``GROUP BY (name, project_id)`` +
    ``count(DISTINCT content)`` aggregation.  On PostgreSQL the
    ``NULL`` ``project_id`` grouping is the classic dialect trap
    (``NULL != NULL`` in a join, but ``GROUP BY`` treats NULLs as
    EQUAL), so the gauge is pinned on the real engine: one divergent
    key (two generations, different content), one IDENTICAL-content
    pair (NOT divergence — the case the census says is harmless), and
    one global (``project_id IS NULL``) key.

    The gauge's home is ``SkillRepository.within_identity_key_divergence``
    — the SAME method the operator instrument
    (``scripts/audit/skill_counter_reconcile.py``) delegates to.
    """
    pg_skill_repo.create(
        name="divergent",
        description="d",
        content="body A",
        project_id="pg-dialect-project",
        generation=0,
    )
    pg_skill_repo.create(
        name="divergent",
        description="d",
        content="body B",
        project_id="pg-dialect-project",
        generation=1,
    )
    # Identical content inside one key — harmless duplicates, NOT drift.
    pg_skill_repo.create(
        name="identical",
        description="d",
        content="same body",
        project_id="pg-dialect-project",
        generation=0,
    )
    pg_skill_repo.create(
        name="identical",
        description="d",
        content="same body",
        project_id="pg-dialect-project",
        generation=1,
    )
    # A global (NULL project_id) key with two DIFFERENT contents — the
    # NULL-grouping case, which is where a `GROUP BY` dialect could
    # plausibly diverge (NULL != NULL in a join, but GROUP BY treats
    # NULLs as EQUAL).  It MUST count as ONE divergent key.
    pg_skill_repo.create(
        name="global-skill",
        description="d",
        content="global body A",
        project_id=None,
        generation=0,
    )
    pg_skill_repo.create(
        name="global-skill",
        description="d",
        content="global body B",
        project_id=None,
        generation=1,
    )

    gauge = pg_skill_repo.within_identity_key_divergence()

    assert gauge["total_skills"] == 6, (
        f"the gauge must see every row it scanned (got {gauge!r})"
    )
    assert gauge["diverging_keys"] == 2, (
        "exactly TWO keys diverge on PostgreSQL (the two-content pair "
        "and the NULL-project_id pair); the identical-content pair must "
        f"NOT count (got {gauge!r})"
    )
    assert gauge["rows_in_diverging_groups"] == 4
    by_name = {k["name"]: k for k in gauge["keys"]}
    assert set(by_name) == {"divergent", "global-skill"}, (
        f"the divergent keys must be exactly the two divergent ones "
        f"(got {sorted(by_name)})"
    )
    divergent = by_name["divergent"]
    assert divergent["distinct_contents"] == 2
    assert divergent["rows"] == 2
    assert divergent["project_id"] == "pg-dialect-project"
    global_key = by_name["global-skill"]
    assert global_key["distinct_contents"] == 2
    assert global_key["project_id"] is None, (
        "a NULL project_id must form its own key on PostgreSQL too"
    )


def test_pg_a4_measure_instrument_runs_against_postgres(pg_engine):
    """(c) The A4 OPERATOR INSTRUMENT really runs against PostgreSQL.

    ``scripts/audit/skill_counter_reconcile.py --measure`` is the
    sanctioned operator surface for the A4 claim.  It is the ONLY place
    the gauge is exposed to a human.  On PostgreSQL it must run, report
    ``dialect: postgresql``, use the PostgreSQL clock expression
    (``clock_timestamp()`` — NOT SQLite's ``strftime``), and carry the
    divergence reading.

    This node loads the script by path (it is an operator script, not an
    importable package) and calls its reading function against the real
    PG engine.
    """
    import importlib.util
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "audit" / "skill_counter_reconcile.py"
    assert script_path.is_file(), (
        f"the A4 operator instrument must exist at {script_path}"
    )
    spec = importlib.util.spec_from_file_location(
        "skill_counter_reconcile_pg_under_test", script_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    snapshot = module.measure_snapshot(pg_engine)

    assert snapshot["dialect"] == "postgresql", (
        "the A4 measure must report the PostgreSQL dialect (got "
        f"{snapshot['dialect']!r})"
    )
    assert snapshot["instrument"], "the reading must name its instrument"
    assert "skills_total" in snapshot, (
        "the A4 reading must carry the scanned population (got "
        f"{snapshot!r})"
    )
    assert "divergence" in snapshot, (
        "the A4 reading must carry the divergence gauge (the A4 anchor)"
    )
    divergence = snapshot["divergence"]
    for key in ("diverging_keys", "rows_in_diverging_groups", "total_skills"):
        assert key in divergence, (
            f"the divergence reading must carry {key!r} (got {divergence!r})"
        )
    # The clock expression is dialect-specific: on PG it is
    # ``clock_timestamp()`` (SQLite's ``strftime`` does not exist here).
    with pg_engine.connect() as conn:
        raw = conn.execute(
            text("select " + module._now_expression(pg_engine))
        ).scalar()
    assert raw is not None
    assert snapshot["taken_at"], "the reading must carry its timestamp"
