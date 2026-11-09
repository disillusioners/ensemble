"""Memoir Store v2 — the POIGNANCY lane on REAL PostgreSQL.

Phase 4 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase4-plan.md`` T4.2/T4.3;
pack ``test/packs/memoir_v2_poignancy_pg_test.sh``).

The SQLite lane (the companion unit packs) cannot see the PostgreSQL
half of this lane. These legs prove it:

* the SCORING QUEUE query runs on PostgreSQL — the unscored rows, oldest
  first, with the attempt cap excluding the exhausted rows;
* the LEGACY BACKFILL scan runs on PostgreSQL — the cutover-bounded
  ``'legacy'`` set only, and exactly N of them;
* the SCORE write-back runs on PostgreSQL — ``poignancy`` lands and the
  AUTHORED ``importance`` is UNCHANGED (the write-frozen record);
* the ATTEMPT RECORDER's dialect-portable statement runs on PostgreSQL —
  the boolean-bound ``CASE WHEN`` must work on BOTH drivers (the SQLite
  leg proves the other half), and the row terminalizes at the cap;
* the REFLECTION TRIGGER's ``COALESCE(poignancy, importance)`` sum sees
  the score on PostgreSQL (no drift between the two consumers).

PG-only lane: run with ``--override-ini="addopts=" -m postgres`` (the
pack does it) against a sanctioned scratch database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from daemon.repositories.memoir.models import MemoirEpisode
from daemon.repositories.memoir.reflection_repository import (
    MemoirReflectionRepository,
)
from daemon.repositories.memoir.repository import (
    MEMOIR_POIGNANCY_ATTEMPT_CAP,
    MemoirRepository,
)

pytestmark = pytest.mark.postgres

AGENT = "ari"


@pytest.fixture()
def repo(pg_engine):
    return MemoirRepository(pg_engine)


@pytest.fixture()
def reflection_repo(pg_engine):
    return MemoirReflectionRepository(pg_engine)


def _seed(repo, count=1, *, importance=5, status=None, created_at=None):
    ids = []
    for index in range(count):
        episode_id = repo.insert_episode(
            agent_id=AGENT,
            body=f"postgres memory {index}",
            importance=importance,
            created_at=created_at,
        )
        if status is not None:
            with Session(repo.engine) as session:
                row = session.exec(
                    select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
                ).first()
                row.poignancy_status = status
                session.add(row)
                session.commit()
        ids.append(episode_id)
    return ids


def _row(repo, episode_id):
    with Session(repo.engine) as session:
        return session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()


# ── the queue ────────────────────────────────────────────────────────────


def test_the_queue_returns_the_unscored_rows_oldest_first(repo):
    """§3.2: the queue is the column's emptiness, oldest first."""
    base = datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, created_at=base)

    pending = repo.get_pending_poignancy_episodes()

    assert len(pending) == 3
    created = [row.created_at for row in pending]
    assert created == sorted(created)


def test_the_queue_excludes_the_exhausted_rows(repo):
    """The attempt cap keeps the exhausted rows OUT of the window — the
    worker advances past them."""
    ids = _seed(repo, count=2)
    for _ in range(MEMOIR_POIGNANCY_ATTEMPT_CAP):
        repo.record_poignancy_attempt(ids[0], "parse_failure")

    pending = repo.get_pending_poignancy_episodes()
    assert [row.id for row in pending] == [ids[1]]


def test_the_queue_honours_the_batch_cap(repo):
    _seed(repo, count=5)
    assert len(repo.get_pending_poignancy_episodes(2)) == 2


# ── the legacy backfill ──────────────────────────────────────────────────


def test_the_legacy_rows_are_not_in_the_queue_by_default(repo):
    """AC5: the default budget NEVER scores the legacy corpus."""
    _seed(repo, count=2, status="legacy")
    assert repo.get_pending_poignancy_episodes() == []


def test_the_backfill_scan_returns_exactly_the_budget(repo):
    """AC5: a budget of 5 returns EXACTLY 5 legacy rows."""
    _seed(repo, count=8, status="legacy")
    rows = repo.get_pending_poignancy_episodes(10, backfill_max=5)
    assert len(rows) == 5
    assert all(row.poignancy_status == "legacy" for row in rows)


# ── the score write-back ─────────────────────────────────────────────────


def test_the_score_lands_and_the_authored_importance_is_unchanged(repo):
    """AC2 on PostgreSQL: the score lands in ``poignancy``; the authored
    ``importance`` is UNCHANGED (the write-frozen record)."""
    episode_id = _seed(repo, importance=8)[0]

    assert repo.record_poignancy_score(episode_id, 9) is True

    row = _row(repo, episode_id)
    assert row.poignancy == 9
    assert row.poignancy_status == "scored"
    assert int(row.importance) == 8


def test_the_score_is_clamped_on_postgres(repo):
    """The column's CHECK constraint is the last line of defence."""
    episode_id = _seed(repo)[0]
    repo.record_poignancy_score(episode_id, 99)
    assert _row(repo, episode_id).poignancy == 10


# ── the attempt recorder (the dialect-portable statement) ───────────────


def test_the_attempt_recorder_counts_and_keeps_the_status(repo):
    """A failed attempt counts, and the status is LEFT ALONE until the
    cap — so the row stays in the queue."""
    episode_id = _seed(repo)[0]

    attempts, terminal = repo.record_poignancy_attempt(episode_id, "parse_failure")

    assert attempts == 1
    assert terminal is None
    row = _row(repo, episode_id)
    assert row.poignancy_attempts == 1
    assert row.poignancy_status is None


def test_the_attempt_recorder_terminalizes_at_the_cap(repo):
    """AC3 on PostgreSQL: the fifth failure flips the row to
    ``terminal:<reason>``."""
    episode_id = _seed(repo)[0]

    for _ in range(MEMOIR_POIGNANCY_ATTEMPT_CAP - 1):
        repo.record_poignancy_attempt(episode_id, "parse_failure")

    attempts, terminal = repo.record_poignancy_attempt(episode_id, "parse_failure")

    assert attempts == MEMOIR_POIGNANCY_ATTEMPT_CAP
    assert terminal == "terminal:parse_failure"
    assert _row(repo, episode_id).poignancy_status == "terminal:parse_failure"


def test_an_immediate_terminal_lands_on_the_first_attempt(repo):
    """A dead credential terminalizes the row at once."""
    episode_id = _seed(repo)[0]

    attempts, terminal = repo.record_poignancy_attempt(
        episode_id, "poignancy_auth_rejected", immediate_terminal=True
    )

    assert attempts == 1
    assert terminal == "terminal:poignancy_auth_rejected"
    assert (
        _row(repo, episode_id).poignancy_status
        == "terminal:poignancy_auth_rejected"
    )


def test_the_legacy_row_keeps_its_status_until_the_cap(repo):
    """A legacy row stays ``'legacy'`` while it has attempts left — the
    backfill scan must still find it."""
    episode_id = _seed(repo, status="legacy")[0]

    repo.record_poignancy_attempt(episode_id, "poignancy_connection_error")
    assert _row(repo, episode_id).poignancy_status == "legacy"


# ── the trigger consumer (no drift) ──────────────────────────────────────


def test_the_reflection_trigger_sums_the_effective_importance(
    repo, reflection_repo
):
    """The OTHER consumer sees the score — and the sum REPLACES the
    authored value (it never adds to it)."""
    episode_id = _seed(
        repo, count=1, importance=5, created_at=datetime.now(timezone.utc)
    )[0]
    window = datetime.now(timezone.utc) - timedelta(days=2)

    before = reflection_repo.derive_trigger(AGENT, window_start=window)
    assert before[0] == 5

    repo.record_poignancy_score(episode_id, 9)

    after = reflection_repo.derive_trigger(AGENT, window_start=window)
    assert after[0] == 9


def test_the_poignancy_columns_exist_on_postgres(repo):
    """The four phase-1 columns are present in the live PG schema."""
    with Session(repo.engine) as session:
        columns = {
            row[0]
            for row in session.exec(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'memoir_episodes'"
                )
            ).all()
        }
    assert {
        "poignancy",
        "poignancy_status",
        "poignancy_attempts",
        "reflected_at",
    } <= columns
