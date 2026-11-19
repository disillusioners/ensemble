"""Memoir Store v2 — the keepsake-proposal DECISION axis (the review store).

A proposal that was REVIEWED must never re-notify, and must never be
confusable with one nobody read. This module pins the DECISION axis:

* **the schema** — three nullable columns on ``memoir_proposal_ledger``
  (``decision_status`` / ``decided_by`` / ``decided_at``) and a
  model-bound CHECK on the two-token decision vocabulary. The DELIVERY
  vocabulary (``notification_status``) is untouched: it stays the closed
  four-token set — a decision token must never enter it;
* **the exclusion** — a DECIDED row leaves the OPEN set for both the
  briefing's zone render AND the notify target, and the exclusion lives
  in ONE place (the statement builder) so the two paths cannot drift;
* **the CAS** — the notify claim keeps its ``notification_status ==
  'pending'`` predicate and ADDS ``decision_status IS NULL``, so a
  proposal decided between the selection and the claim is never
  announced;
* **the service surface** — ``count_pending_reviews`` (a cheap COUNT,
  never a row materialization) and ``record_review`` (the resolve +
  compare-and-set recorder, returning a plain dict and refusing with a
  typed ``.code``).

FENCE: throwaway in-memory SQLite; no ``POSTGRES_*`` variable is read;
the actor vocabulary is imported from :data:`PIN_ACTORS` (never spelled
as a literal anywhere in this file).
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import DateTime, Text, update
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from daemon.migrations.runner import MigrationFile
from daemon.repositories.memoir.models import (
    PIN_ACTORS,
    PROPOSAL_DECISIONS,
    PROPOSAL_DECISION_ACCEPTED,
    PROPOSAL_DECISION_DECLINED,
    MemoirEpisode,
    MemoirProposalLedger,
)
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.memoir_keepsake_proposal_service import (
    MemoirKeepsakeProposalService,
    PROPOSAL_STATUS_ENQUEUED,
    PROPOSAL_STATUS_PENDING,
)

AGENT = "ari"
FIXED_NOW = datetime(2026, 11, 19, 9, 0, 0, tzinfo=timezone.utc)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "daemon" / "migrations" / "versions"
DECISION_MIGRATION = MIGRATIONS_DIR / "20261119_000001_add_proposal_decision_columns.sql"

#: The decision CHECK — byte-identical in the model and the migration.
DECISION_CHECK_TEXT = (
    "decision_status IS NULL OR decision_status IN ('accepted', 'declined')"
)

#: A body long enough to clear the enduring-significance substance floor.
LONG_BODY = (
    "We argued about the font for three hours and settled on the serif; "
    "the decision became canon for every later page we shipped together."
)

#: The actor vocabulary is IMPORTED, never spelled — the reviewer's claim
#: token is a CONTRACT, not a literal this module owns.
ACTOR = sorted(PIN_ACTORS)[0]


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def engine():
    """Throwaway in-memory SQLite with the model's schema."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


@pytest.fixture()
def service(engine):
    return MemoirKeepsakeProposalService(engine, clock=lambda: FIXED_NOW)


def _insert(repo, *, body=LONG_BODY, importance=9, title="A memory"):
    return repo.insert_episode(
        agent_id=AGENT, body=body, title=title, importance=importance
    )


def _stage(service, repo, *, title="A memory", importance=9):
    """Insert an episode and stage its proposal row; return the ledger id."""
    episode_id = _insert(repo, importance=importance, title=title)
    assert service.process_pass(notify=False).staged == 1
    with Session(service._engine) as session:  # noqa: SLF001 — test introspection
        row = session.exec(
            MemoirProposalLedger.__table__.select()
            .where(MemoirProposalLedger.episode_id == episode_id)
        ).first()
    assert row is not None
    return str(row[0]), episode_id


def _ledger_row(engine, ledger_id):
    with Session(engine) as session:
        return session.get(MemoirProposalLedger, ledger_id)


def _set_status(engine, ledger_id, status):
    """Force a delivery status directly (the DB-level fixture)."""
    with Session(engine) as session:
        row = session.get(MemoirProposalLedger, ledger_id)
        row.notification_status = status
        session.add(row)
        session.commit()


def _set_decision(engine, ledger_id, decision):
    """Force a decision row directly (the DB-level fixture)."""
    with Session(engine) as session:
        row = session.get(MemoirProposalLedger, ledger_id)
        row.decision_status = decision
        row.decided_by = ACTOR
        row.decided_at = FIXED_NOW
        session.add(row)
        session.commit()


def _running_ari_instance(engine, *, instance_id="ari-inst-1"):
    from daemon.repositories.instance.models import Instance

    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                project_id=None,
                agent_id=AGENT,
                agent_dir="agents/ari",
                status="running",
                updated_at="2026-11-19T00:00:00",
            )
        )
        session.commit()
    return instance_id


# ── the schema: the three nullable columns + the bound CHECK ──────────────


def test_the_three_decision_columns_are_nullable_on_the_model():
    table = SQLModel.metadata.tables["memoir_proposal_ledger"]
    columns = {col.name: col for col in table.columns}
    for name in ("decision_status", "decided_by", "decided_at"):
        assert name in columns, f"{name} is missing from the ledger model"
        assert columns[name].nullable is True, f"{name} must be nullable"
    assert isinstance(columns["decision_status"].type, Text)
    assert isinstance(columns["decided_by"].type, Text)
    assert isinstance(columns["decided_at"].type, DateTime)
    assert columns["decided_at"].type.timezone is True


def test_the_decision_check_is_bound_to_the_model():
    """The CHECK lives on the MODEL — on PostgreSQL the table is built
    FROM the model, so a constraint that lived only in the .sql would
    not exist there at all."""
    checks = {
        str(constraint.sqltext)
        for constraint in SQLModel.metadata.tables[
            "memoir_proposal_ledger"
        ].constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert DECISION_CHECK_TEXT in checks, checks


def test_the_delivery_vocabulary_is_untouched_and_still_closed():
    """A decision token must NEVER enter the DELIVERY vocabulary."""
    checks = {
        str(constraint.sqltext)
        for constraint in SQLModel.metadata.tables[
            "memoir_proposal_ledger"
        ].constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert (
        "notification_status IN ('pending', 'enqueued', 'sent', 'failed')"
        in checks
    ), checks
    # The four-token set is unchanged — no decision token rode in.
    assert not any(
        "accepted" in text or "declined" in text
        for text in checks
        if "notification_status" in text
    )


def test_the_decision_vocabulary_is_the_closed_two_token_set():
    assert PROPOSAL_DECISIONS == frozenset({"accepted", "declined"})
    assert PROPOSAL_DECISION_ACCEPTED in PROPOSAL_DECISIONS
    assert PROPOSAL_DECISION_DECLINED in PROPOSAL_DECISIONS
    assert len(PROPOSAL_DECISIONS) == 2


def test_the_database_rejects_a_third_decision_token(engine):
    """The vocabulary is enforced by the DATABASE, not merely by Python."""
    ledger_id, episode_id = "row-1", "ep-1"
    with Session(engine) as session:
        session.add(
            MemoirProposalLedger(
                id=ledger_id,
                agent_id=AGENT,
                episode_id=episode_id,
                proposal_date="2026-11-19",
                proposed_at=FIXED_NOW,
                notification_status=PROPOSAL_STATUS_PENDING,
                reason="",
                created_at=FIXED_NOW,
                decision_status=PROPOSAL_DECISION_ACCEPTED,
            )
        )
        session.commit()
    with Session(engine) as session:
        row = session.get(MemoirProposalLedger, ledger_id)
        row.decision_status = "maybe"
        session.add(row)
        with pytest.raises(Exception):
            session.commit()


# ── the migration (the additive SQLite file) ─────────────────────────────


def test_the_migration_file_exists_and_parses():
    assert DECISION_MIGRATION.is_file(), DECISION_MIGRATION
    parsed = MigrationFile.parse(DECISION_MIGRATION)
    assert parsed.version == "20261119_000001"
    assert parsed.manual_only is False


def test_the_migration_uses_the_plain_add_column_form():
    """``ADD COLUMN IF NOT EXISTS`` is a hard syntax error in SQLite."""
    sql = MigrationFile.parse(DECISION_MIGRATION).up_sql
    assert "ADD COLUMN IF NOT EXISTS" not in sql.upper()
    for column, declaration in (
        ("decision_status", "TEXT"),
        ("decided_by", "TEXT"),
        ("decided_at", "TIMESTAMP"),
    ):
        assert (
            f"ALTER TABLE memoir_proposal_ledger ADD COLUMN {column} "
            f"{declaration}" in sql
        ), f"the {column} column is not declared as planned"


def test_the_migration_carries_the_same_check_text_as_the_model():
    sql = MigrationFile.parse(DECISION_MIGRATION).up_sql
    assert DECISION_CHECK_TEXT in sql


def test_the_migration_has_the_additive_rollback_note():
    down = MigrationFile.parse(DECISION_MIGRATION).down_sql
    assert down.strip(), "the DOWN section is empty"
    assert "additive" in down.lower()


def test_the_migration_applies_cleanly_on_a_real_sqlite_database():
    """The file applies on REAL SQLite — every statement, zero failures."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE memoir_proposal_ledger ("
            "id TEXT NOT NULL PRIMARY KEY, agent_id TEXT NOT NULL, "
            "episode_id TEXT NOT NULL, proposal_date TEXT NOT NULL, "
            "proposed_at DATETIME NOT NULL, notification_status TEXT NOT NULL, "
            "notified_at DATETIME, reason TEXT NOT NULL DEFAULT '', "
            "created_at DATETIME NOT NULL)"
        )
        conn.execute(
            "INSERT INTO memoir_proposal_ledger (id, agent_id, episode_id, "
            "proposal_date, proposed_at, notification_status, reason, "
            "created_at) VALUES ('row-1', 'ari', 'ep-1', '2026-11-19', "
            "'2026-11-19 09:00:00', 'pending', '', '2026-11-19 09:00:00')"
        )
        conn.commit()
        sql = MigrationFile.parse(DECISION_MIGRATION).up_sql
        code_lines = [
            line for line in sql.splitlines() if not line.lstrip().startswith("--")
        ]
        statements = [s.strip() for s in "\n".join(code_lines).split(";") if s.strip()]
        assert len(statements) == 3, statements
        for statement in statements:
            conn.execute(statement)
        conn.commit()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(memoir_proposal_ledger)")}
        assert {
            "decision_status",
            "decided_by",
            "decided_at",
        } <= columns, columns
        # The CHECK rides the column definition and binds on a POPULATED
        # table (the pre-existing row carries NULL, which the leading
        # ``IS NULL`` arm admits).
        conn.execute(
            "UPDATE memoir_proposal_ledger SET decision_status = 'declined' "
            "WHERE id = 'row-1'"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE memoir_proposal_ledger SET decision_status = 'maybe' "
                "WHERE id = 'row-1'"
            )
    finally:
        conn.close()


# ── the exclusion: a DECIDED row leaves the OPEN set ─────────────────────


def test_a_decided_row_is_not_rendered_in_the_zone(engine, repo, service):
    ledger_id, _episode_id = _stage(service, repo, title="Reviewed memory")
    assert len(service.select_zone_rows()) == 1
    _set_decision(engine, ledger_id, PROPOSAL_DECISION_ACCEPTED)
    assert service.select_zone_rows() == []
    assert service.zone_lines(limit=3) == []


def test_a_decided_row_is_never_announced(engine, repo, service):
    """The NOTIFY path narrows to ``('pending',)`` — the decided row must
    not be re-announced (and must not burn the day's slot)."""
    ledger_id, _episode_id = _stage(service, repo, title="Reviewed memory")
    _set_decision(engine, ledger_id, PROPOSAL_DECISION_DECLINED)
    sent: list[str] = []
    _running_ari_instance(engine)
    bridge = MemoirKeepsakeProposalService(
        engine,
        clock=lambda: FIXED_NOW,
        enqueue_bridge=lambda iid, msg, src: sent.append(msg),
    )
    enqueued, failure = bridge.notify_once()
    assert enqueued is False, failure
    assert failure == "no_proposal", failure
    assert sent == []


def test_the_exclusion_lives_in_the_one_statement_builder():
    """ONE home: the predicate is in ``_zone_selection_statement``, and
    BOTH consumers (the zone render + the notify target) go through it.

    Two mirrored predicates could drift apart — and the drift would be
    invisible until a reviewed proposal was announced again.
    """
    source = (
        REPO_ROOT / "daemon" / "services" / "memoir_keepsake_proposal_service.py"
    ).read_text(encoding="utf-8")
    builder_start = source.index("def _zone_selection_statement(")
    builder_end = source.index("def count_pending_reviews(")
    builder = source[builder_start:builder_end]
    assert "decision_status" in builder, (
        "the decided-row exclusion must live in the ONE statement builder"
    )
    # The zone render and the notify target BOTH call the builder.
    assert source.count("_zone_selection_statement(") >= 3, (
        "the builder must be reached from the zone render AND the notify "
        "target (one call site each, plus its definition)"
    )


def test_the_notify_claim_keeps_its_delivery_cas_and_adds_the_decision_cas(
    engine, repo, service
):
    """BOTH predicates are load-bearing on the claim.

    Dropping the ``notification_status == 'pending'`` predicate is DOUBLE
    DELIVERY (the repo's own mutation probe). Dropping the
    ``decision_status IS NULL`` predicate lets a proposal that was
    reviewed between the selection and the claim be announced anyway.
    """
    ledger_id, _episode_id = _stage(service, repo, title="Race memory")
    with Session(engine) as session:
        moved = service._mark_enqueued_in(  # noqa: SLF001 — the CAS itself
            session, ledger_id, now=FIXED_NOW
        )
        assert moved == 1
        session.commit()

    # A DECIDED row cannot be claimed (the decision CAS holds).
    other_id, _ = _stage(service, repo, title="Second memory")
    _set_decision(engine, other_id, PROPOSAL_DECISION_ACCEPTED)
    with Session(engine) as session:
        assert (
            service._mark_enqueued_in(  # noqa: SLF001 — the CAS itself
                session, other_id, now=FIXED_NOW
            )
            == 0
        )


# ── the cheap pending-review COUNT ───────────────────────────────────────


def test_count_pending_reviews_counts_open_undecided_rows(engine, repo, service):
    _stage(service, repo, title="Open memory")
    assert service.count_pending_reviews() == 1


def test_count_pending_reviews_excludes_decided_rows(engine, repo, service):
    ledger_id, _ = _stage(service, repo, title="Reviewed memory")
    assert service.count_pending_reviews() == 1
    _set_decision(engine, ledger_id, PROPOSAL_DECISION_ACCEPTED)
    assert service.count_pending_reviews() == 0


def test_count_pending_reviews_counts_a_delivered_but_unreviewed_row(
    engine, repo, service
):
    """DELIVERED and UNDECIDED are different questions — a proposal that
    was announced but never reviewed is exactly what the count exists to
    surface."""
    ledger_id, _ = _stage(service, repo, title="Announced memory")
    _set_status(engine, ledger_id, PROPOSAL_STATUS_ENQUEUED)
    assert service.count_pending_reviews() == 1


def test_count_pending_reviews_is_a_count_not_a_materialization(engine, repo, service):
    """The COUNT never materializes rows — the whole point of the cheap
    read is that the caller only wants a number.

    The COUNT is the ONE statement builder's ``count_only`` projection:
    the same join and the same predicates as the zone render, with
    ``COUNT(*)`` instead of the row columns. So the count can never
    disagree with the zone it annotates."""
    _stage(service, repo, title="Open memory")
    source = (
        REPO_ROOT / "daemon" / "services" / "memoir_keepsake_proposal_service.py"
    ).read_text(encoding="utf-8")
    start = source.index("def count_pending_reviews(")
    end = source.index("def record_review(")
    body = source[start:end]
    assert "count_only=True" in body, (
        "the pending-review read must go through the ONE statement builder"
    )
    assert "select(MemoirProposalLedger)" not in body, (
        "the pending-review read must not materialize rows"
    )
    # The builder's count projection really is a COUNT.
    builder_start = source.index("def _zone_selection_statement(")
    builder_end = source.index("def _resolve_open_proposal_in(")
    builder = source[builder_start:builder_end]
    assert "func.count()" in builder, "the count projection must be COUNT(*)"
    assert "MemoirProposalLedger.id," in builder, (
        "the zone projection must still select the rows"
    )


def test_the_count_agrees_with_the_zone_it_annotates(engine, repo, service):
    """THE ANTI-DRIFT PROPERTY: the count is the number of rows the
    zone selection would render (uncapped) — the two are built from the
    SAME join and the SAME predicates, so a counter beside the zone can
    never contradict it."""
    for index in range(4):
        _stage(service, repo, title=f"Open memory {index}")
    rendered = service.select_zone_rows(limit=100)
    assert service.count_pending_reviews() == len(rendered) == 4


def test_the_count_excludes_a_proposal_the_zone_would_not_render(
    engine, repo, service
):
    """A proposal whose episode was PINNED afterwards is not an open
    question — the zone does not render it, so the count must not
    report it either."""
    episode_id = _insert(repo, importance=9, title="Pinned later")
    assert service.process_pass(notify=False).staged == 1
    assert service.count_pending_reviews() == 1
    with Session(engine) as session:
        row = session.get(MemoirEpisode, episode_id)
        row.is_keepsake = True
        session.add(row)
        session.commit()
    assert service.select_zone_rows() == []
    assert service.count_pending_reviews() == 0


def test_count_pending_reviews_is_scoped_to_the_agent(engine, repo, service):
    _stage(service, repo, title="Open memory")
    assert service.count_pending_reviews(agent_id="someone-else") == 0
    assert service.count_pending_reviews(agent_id=AGENT) == 1


# ── the review recorder ──────────────────────────────────────────────────


def test_record_review_writes_the_decision_triple(engine, repo, service):
    ledger_id, episode_id = _stage(service, repo, title="Reviewable memory")
    result = service.record_review(
        title="Reviewable memory", decision=PROPOSAL_DECISION_ACCEPTED, actor=ACTOR
    )
    assert result == {
        "ledger_id": ledger_id,
        "episode_id": episode_id,
        "decision": PROPOSAL_DECISION_ACCEPTED,
        "title": "Reviewable memory",
        "reason": result["reason"],
    }
    row = _ledger_row(engine, ledger_id)
    assert row.decision_status == PROPOSAL_DECISION_ACCEPTED
    assert row.decided_by == ACTOR
    assert row.decided_at is not None
    # The stamp names ONE instant (SQLite stores its UTC wall digits).
    assert row.decided_at.hour == 9, row.decided_at


def test_record_review_returns_a_plain_dict(engine, repo, service):
    _stage(service, repo, title="Reviewable memory")
    result = service.record_review(
        title="Reviewable memory", decision=PROPOSAL_DECISION_DECLINED, actor=ACTOR
    )
    assert type(result) is dict


def test_record_review_resolves_the_strongest_open_proposal(engine, repo, service):
    """The resolution order is the ZONE's own (strongest first) — the
    proposal a reviewer is told about is the one their decision lands
    on."""
    weak = _insert(repo, importance=8, title="Same title")
    strong = _insert(repo, importance=10, title="Same title")
    assert service.process_pass(notify=False).staged == 2
    result = service.record_review(
        title="Same title", decision=PROPOSAL_DECISION_ACCEPTED, actor=ACTOR
    )
    assert result["episode_id"] == strong
    assert result["episode_id"] != weak
    # The weaker one is still OPEN — one decision, one proposal.
    assert service.count_pending_reviews() == 1


def test_record_review_resolves_the_rendered_title(engine, repo, service):
    """The reviewer quotes the RENDERED title — the zone collapses a
    title that carries a newline, so the rendered form is not byte-equal
    to the stored one. The resolver matches the RENDERED title too."""
    _stage(
        service,
        repo,
        title="The lantern\nwalk",
    )
    rendered = service.zone_lines(limit=1)[0]
    quoted = rendered.split('"')[1]
    assert quoted == "The lantern walk", rendered
    result = service.record_review(
        title=quoted, decision=PROPOSAL_DECISION_ACCEPTED, actor=ACTOR
    )
    assert result["title"] is not None
    assert service.count_pending_reviews() == 0


def test_the_review_cas_loses_to_a_concurrent_decision(engine, tmp_path):
    """THE REVIEW'S OWN COMPARE-AND-SET: the FIRST decision stands.

    The decision is written with ``decision_status IS NULL`` in the
    WHERE clause. If a second reviewer decides the SAME proposal while
    the first review is between its SELECT and its UPDATE, the first
    UPDATE matches ZERO rows — the second decision is NOT recorded and
    the first one stands. Dropping the predicate would let the second
    review SILENTLY OVERWRITE the first decision.

    The race is planted by a subclass that decides the row through a
    SECOND session (a different connection) right after the selection
    and before the write. The file-backed WAL database is what makes
    the two connections real: a reader and a writer can coexist, so the
    interleaving is the production shape and not a mock.
    """
    file_engine = create_engine(
        f"sqlite:///{tmp_path / 'review-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with file_engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    SQLModel.metadata.create_all(file_engine)
    try:
        repo = MemoirRepository(file_engine)
        _insert(repo, importance=9, title="Raced memory")
        service = MemoirKeepsakeProposalService(
            file_engine, clock=lambda: FIXED_NOW
        )
        assert service.process_pass(notify=False).staged == 1

        class RacingService(MemoirKeepsakeProposalService):
            """Decides the row through a SECOND connection, after the
            selection and before the write."""

            def _resolve_open_proposal_in(self, session, *, title):
                rows = super()._resolve_open_proposal_in(session, title=title)
                if rows:
                    other = Session(self._engine)
                    try:
                        other.exec(
                            update(MemoirProposalLedger)
                            .where(MemoirProposalLedger.id == rows[0].ledger_id)
                            .values(
                                decision_status=PROPOSAL_DECISION_ACCEPTED,
                                decided_by=ACTOR,
                                decided_at=FIXED_NOW,
                            )
                        )
                        other.commit()
                    finally:
                        other.close()
                return rows

        racer = RacingService(file_engine, clock=lambda: FIXED_NOW)
        with pytest.raises(ValueError) as excinfo:
            racer.record_review(
                title="Raced memory",
                decision=PROPOSAL_DECISION_DECLINED,
                actor=ACTOR,
            )
        assert getattr(excinfo.value, "code", None) == "not_found"
        # THE FIRST DECISION STANDS — the second one was not recorded.
        with Session(file_engine) as session:
            row = session.exec(
                MemoirProposalLedger.__table__.select()
                .where(
                    MemoirProposalLedger.decision_status.is_not(None)
                )
            ).first()
        assert row is not None
        assert row[8] == PROPOSAL_DECISION_ACCEPTED, row[8]
    finally:
        file_engine.dispose()


def test_record_review_refuses_an_unknown_title(engine, repo, service):
    _stage(service, repo, title="Known memory")
    with pytest.raises(ValueError) as excinfo:
        service.record_review(
            title="No such memory", decision=PROPOSAL_DECISION_ACCEPTED, actor=ACTOR
        )
    assert getattr(excinfo.value, "code", None) == "not_found"


def test_record_review_refuses_a_decision_outside_the_vocabulary(
    engine, repo, service
):
    _stage(service, repo, title="Known memory")
    with pytest.raises(ValueError) as excinfo:
        service.record_review(title="Known memory", decision="maybe", actor=ACTOR)
    assert getattr(excinfo.value, "code", None) == "invalid_decision"


def test_record_review_refuses_an_actor_outside_the_vocabulary(engine, repo, service):
    _stage(service, repo, title="Known memory")
    with pytest.raises(ValueError) as excinfo:
        service.record_review(
            title="Known memory",
            decision=PROPOSAL_DECISION_ACCEPTED,
            actor="not-an-actor",
        )
    assert getattr(excinfo.value, "code", None) == "invalid_actor"


def test_a_second_review_of_the_same_proposal_refuses(engine, repo, service):
    """A decision is recorded AT MOST ONCE — the second reviewer is told
    the proposal is already settled (the first decision stands)."""
    _stage(service, repo, title="Reviewable memory")
    service.record_review(
        title="Reviewable memory", decision=PROPOSAL_DECISION_ACCEPTED, actor=ACTOR
    )
    with pytest.raises(ValueError) as excinfo:
        service.record_review(
            title="Reviewable memory", decision=PROPOSAL_DECISION_DECLINED, actor=ACTOR
        )
    assert getattr(excinfo.value, "code", None) == "not_found"


def test_the_decided_row_leaves_the_open_set_after_the_review(
    engine, repo, service
):
    _stage(service, repo, title="Reviewable memory")
    service.record_review(
        title="Reviewable memory", decision=PROPOSAL_DECISION_ACCEPTED, actor=ACTOR
    )
    assert service.select_zone_rows() == []
    assert service.count_pending_reviews() == 0


def test_the_decision_stamp_is_aware_utc(engine, repo, service):
    """The stamp is coerced to aware UTC — one unambiguous instant.

    SQLite stores the aware value's UTC WALL DIGITS (the driver drops
    the tzinfo on read), so the assertion is on the DIGITS: a value
    given in another timezone must land as its UTC instant, and a naive
    value must be read as UTC (the house assume-UTC policy).
    """
    shifted = datetime(
        2026, 11, 19, 16, 0, 0, tzinfo=timezone(timedelta(hours=7))
    )
    _stage(service, repo, title="Reviewable memory")
    service.record_review(
        title="Reviewable memory",
        decision=PROPOSAL_DECISION_ACCEPTED,
        actor=ACTOR,
        now=shifted,
    )
    with Session(engine) as session:
        row = session.exec(
            MemoirProposalLedger.__table__.select()
            .where(MemoirProposalLedger.decision_status.is_not(None))
        ).first()
    assert row is not None
    stored = row[10] if not isinstance(row[10], datetime) else row[10]
    if isinstance(stored, str):
        stored = datetime.fromisoformat(stored)
    # 16:00+07 == 09:00Z — the DIGITS are the UTC instant.
    assert stored.hour == 9, stored
    assert stored.minute == 0, stored


def test_a_naive_stamp_is_read_as_utc(engine, repo, service):
    """A naive ``now`` follows the house assume-UTC policy."""
    naive = datetime(2026, 11, 19, 9, 0, 0)
    _stage(service, repo, title="Reviewable memory")
    service.record_review(
        title="Reviewable memory",
        decision=PROPOSAL_DECISION_ACCEPTED,
        actor=ACTOR,
        now=naive,
    )
    with Session(engine) as session:
        row = session.exec(
            MemoirProposalLedger.__table__.select()
            .where(MemoirProposalLedger.decision_status.is_not(None))
        ).first()
    stored = row[10]
    if isinstance(stored, str):
        stored = datetime.fromisoformat(stored)
    assert stored.hour == 9, stored


# ── the PG ensure path (a SEPARATE, fail-soft method) ───────────────────


def test_the_pg_ensure_is_a_separate_method_not_the_shared_list():
    """The shared ensure list runs inside ONE transaction: a bad entry
    there aborts the boot and rolls back every other lane's ensure
    statement. This lane's DDL therefore lives in its OWN method with
    its OWN try/except."""
    source = (REPO_ROOT / "daemon" / "manager.py").read_text(encoding="utf-8")
    assert "def _ensure_memoir_proposal_decision_columns(" in source
    start = source.index("def _ensure_memoir_proposal_decision_columns(")
    end = source.index("def _ensure_postgres_drop_legacy_columns(")
    body = source[start:end]
    assert "ADD COLUMN IF NOT EXISTS" in body
    assert "EXCEPTION WHEN others THEN" in body
    assert "RAISE WARNING" in body
    # The method NEVER raises: it catches, logs and returns.
    tree = ast.parse(source)
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_ensure_memoir_proposal_decision_columns"
    )
    assert any(
        isinstance(node, ast.ExceptHandler) for node in ast.walk(method)
    ), "the ensure must catch its own failure"
    raises = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Raise)
    ]
    assert not raises, "the fail-soft ensure must never re-raise"


def test_the_pg_ensure_is_called_at_the_boot():
    source = (REPO_ROOT / "daemon" / "manager.py").read_text(encoding="utf-8")
    assert "self._ensure_memoir_proposal_decision_columns()" in source
    # ... and NOT inside the shared statement list (the boot-abort
    # transaction).
    shared_start = source.index("def _ensure_postgres_columns(")
    shared_end = source.index("def _ensure_memoir_proposal_decision_columns(")
    shared = source[shared_start:shared_end]
    assert "memoir_proposal_ledger" not in shared, (
        "the decision DDL must not ride the shared all-or-nothing ensure list"
    )


def test_the_pg_ensure_uses_one_idempotent_do_block():
    source = (REPO_ROOT / "daemon" / "manager.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_ensure_memoir_proposal_decision_columns"
    )
    blocks = [
        node.value
        for node in ast.walk(method)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "statements"
            for target in node.targets
        )
        and isinstance(node.value, ast.List)
    ]
    assert len(blocks) == 1, "the ensure must carry ONE statement list"
    literals = [
        element.value
        for element in blocks[0].elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]
    assert len(literals) == 1, "the ensure must be ONE atomic DO block"
    sql = literals[0]
    assert sql.count("DO $$") == 1, "the ensure must be ONE atomic DO block"
    assert sql.count("ADD COLUMN IF NOT EXISTS") == 3, (
        "the three columns must be added idempotently"
    )
    assert "EXCEPTION WHEN others THEN" in sql
    assert "RAISE WARNING" in sql
