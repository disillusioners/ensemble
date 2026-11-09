"""Memoir Store v2 — the REFLECTION lane on REAL PostgreSQL.

Phase 2 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase2-plan.md`` AC3; pack
``test/packs/memoir_v2_reflection_pg_test.sh``).

The SQLite lane (the companion unit packs) cannot see the PostgreSQL half
of the reflection lane. These legs prove it:

* the DERIVED TRIGGER query runs on PostgreSQL with the DIALECT-NEUTRAL
  binding (``created_at >= :window_start`` — the plan's ``now() -
  interval '2 days'`` is PostgreSQL syntax, but the repository binds the
  cutoff as a PARAMETER so both drivers share one predicate) (ND-10);
* the ``COALESCE(poignancy, importance)`` effective-importance sum is
  what the trigger sees;
* the CLAIM's compare-and-set runs on PostgreSQL: a fresh claim blocks
  the second claimant and a STALE claim (older than 15 minutes) is
  re-takeable (V2-35 / SF5);
* the atomic ``commit_pass`` lands the ``reflected_at`` marks AND the
  nightly date in ONE transaction;
* the syntheses writer's atomic supersession + the concurrent
  double-claim guard behave on PostgreSQL;
* the dedup key and the recall-side search work on PostgreSQL.

PG-only lane: run with ``--override-ini="addopts=" -m postgres`` (the
pack does it) against a sanctioned scratch database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session

from daemon.repositories.memoir.constellation_repository import (
    MemoirConstellationRepository,
)
from daemon.repositories.memoir.models import MemoirEpisode, MemoirSynthesis
from daemon.repositories.memoir.reflection_repository import (
    MEMOIR_REFLECTION_CLAIM_STALE_SECONDS,
    MemoirReflectionRepository,
)
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.repositories.memoir.syntheses_repository import (
    MemoirSynthesesRepository,
    synthesis_dedup_key,
)

pytestmark = pytest.mark.postgres

AGENT = "ari"


@pytest.fixture()
def repo(pg_engine):
    return MemoirRepository(pg_engine)


@pytest.fixture()
def reflection_repo(pg_engine):
    return MemoirReflectionRepository(pg_engine)


@pytest.fixture()
def syntheses_repo(pg_engine):
    return MemoirSynthesesRepository(pg_engine)


@pytest.fixture()
def constellation_repo(pg_engine):
    return MemoirConstellationRepository(pg_engine)


def _seed(repo, count=3, importance=9, created_at=None):
    return [
        repo.insert_episode(
            agent_id=AGENT,
            body=f"memory {index}",
            importance=importance,
            created_at=created_at,
        )
        for index in range(count)
    ]


def test_the_derived_trigger_runs_on_postgres(repo, reflection_repo):
    """The trigger query is dialect-neutral (ND-10) and PG-correct."""
    _seed(repo, count=3, importance=9)
    total, newest = reflection_repo.derive_trigger(
        AGENT, window_start=datetime.now(timezone.utc) - timedelta(days=2)
    )
    assert total == 27
    assert newest is not None


def test_the_trigger_uses_the_effective_importance(repo, reflection_repo):
    """``COALESCE(poignancy, importance)`` — the importance ruling."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=2)
    with Session(reflection_repo.engine) as session:
        episode = session.get(MemoirEpisode, episode_id)
        episode.poignancy = 9
        session.add(episode)
        session.commit()
    total, _newest = reflection_repo.derive_trigger(
        AGENT, window_start=datetime.now(timezone.utc) - timedelta(days=2)
    )
    assert total == 9


def test_the_trigger_window_excludes_the_older_episodes(repo, reflection_repo):
    """The window is a bound PARAMETER — the old rows are outside it."""
    old = datetime.now(timezone.utc) - timedelta(days=10)
    repo.insert_episode(agent_id=AGENT, body="ancient", importance=9, created_at=old)
    total, _newest = reflection_repo.derive_trigger(
        AGENT, window_start=datetime.now(timezone.utc) - timedelta(days=2)
    )
    assert total == 0


def test_the_claim_is_a_compare_and_set_on_postgres(reflection_repo):
    """V2-35: a fresh claim blocks the second claimant."""
    now = datetime.now(timezone.utc)
    assert reflection_repo.claim(now=now) is True
    assert reflection_repo.claim(now=now) is False


def test_a_stale_claim_is_re_takeable_on_postgres(reflection_repo):
    """V2-35 / SF5: a claim older than 15 minutes is re-takeable."""
    now = datetime.now(timezone.utc)
    assert reflection_repo.claim(now=now) is True
    stale = now + timedelta(seconds=MEMOIR_REFLECTION_CLAIM_STALE_SECONDS + 60)
    assert reflection_repo.claim(now=stale) is True


def test_the_release_claim_clears_the_claim(reflection_repo):
    now = datetime.now(timezone.utc)
    assert reflection_repo.claim(now=now) is True
    assert reflection_repo.release_claim() is True
    assert reflection_repo.claim(now=now) is True


def test_the_commit_pass_lands_the_marks_and_the_date(
    repo, reflection_repo
):
    """THE ATOMIC COMMIT: the marks + the nightly date, ONE transaction."""
    episode_ids = _seed(repo, count=2)
    now = datetime.now(timezone.utc)
    marked = reflection_repo.commit_pass(
        episode_ids,
        reflected_at=now,
        last_nightly_date=now.date().isoformat(),
    )
    assert marked == 2
    assert reflection_repo.last_nightly_date() == now.date().isoformat()
    total, _newest = reflection_repo.derive_trigger(
        AGENT, window_start=now - timedelta(days=2)
    )
    assert total == 0


def test_the_scan_is_oldest_first_on_postgres(repo, reflection_repo):
    now = datetime.now(timezone.utc)
    ids = [
        repo.insert_episode(
            agent_id=AGENT,
            body=f"memory {index}",
            importance=9,
            created_at=now + timedelta(seconds=index),
        )
        for index in range(3)
    ]
    rows = reflection_repo.scan_unreflected(AGENT, limit=30, window_start=None)
    assert [row.id for row in rows] == ids


def test_the_catch_up_scan_ignores_the_window_on_postgres(repo, reflection_repo):
    """C2 / V2-27: the nightly catch-up walks the pre-cutover backlog."""
    old = datetime.now(timezone.utc) - timedelta(days=400)
    repo.insert_episode(agent_id=AGENT, body="ancient", importance=9, created_at=old)
    rows = reflection_repo.scan_unreflected(AGENT, limit=30, window_start=None)
    assert len(rows) == 1
    assert rows[0].body == "ancient"


def test_the_known_episode_ids_guard_on_postgres(repo, reflection_repo):
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)
    known = reflection_repo.known_episode_ids(AGENT, [episode_id, "not-an-episode"])
    assert known == {episode_id}


def test_the_synthesis_dedup_key_on_postgres(syntheses_repo):
    """V2-17: the composite key is stable and order-independent."""
    first = synthesis_dedup_key("the Emperor is patient", ["b", "a"])
    second = synthesis_dedup_key("the Emperor is patient", ["a", "b"])
    assert first == second
    assert first != synthesis_dedup_key("the Emperor is patient", ["a"])


def test_the_dedup_returns_the_existing_row_on_postgres(syntheses_repo):
    episode_id = "ep1"
    row = syntheses_repo.create_synthesis(
        AGENT, "the Emperor is patient", [episode_id], "CREATE", 0.9
    )
    duplicate = syntheses_repo.find_duplicate(
        AGENT, "the Emperor is patient", [episode_id]
    )
    assert duplicate is not None
    assert duplicate.id == row.id


def test_the_search_matches_the_claim_on_postgres(syntheses_repo):
    syntheses_repo.create_synthesis(
        AGENT, "the Emperor prefers the zebra", ["ep1"], "CREATE", 0.9
    )
    rows = syntheses_repo.search_active(AGENT, "zebra", 5)
    assert len(rows) == 1
    assert rows[0].verb == "CREATE"


def test_the_supersession_is_atomic_on_postgres(syntheses_repo):
    old = syntheses_repo.create_synthesis(
        AGENT, "the Emperor is patient", ["ep1"], "CREATE", 0.9
    )
    new = syntheses_repo.supersede_with(
        AGENT,
        old.id,
        {
            "claim": "the Emperor is very patient",
            "source_episode_ids": ["ep1"],
            "verb": "REFINE",
            "confidence": 0.95,
        },
    )
    assert new.supersedes == old.id
    assert new.superseded_by is None
    with Session(syntheses_repo.engine) as session:
        reloaded = session.get(MemoirSynthesis, old.id)
        assert reloaded is not None
        assert reloaded.superseded_by == new.id


def test_a_second_supersede_fails_loudly_on_postgres(syntheses_repo):
    """The concurrent double-claim guard: loud, never a silent double-link."""
    old = syntheses_repo.create_synthesis(
        AGENT, "the Emperor is patient", ["ep1"], "CREATE", 0.9
    )
    syntheses_repo.supersede_with(
        AGENT,
        old.id,
        {
            "claim": "the Emperor is very patient",
            "source_episode_ids": ["ep1"],
            "verb": "REFINE",
            "confidence": 0.95,
        },
    )
    with pytest.raises((ValueError, RuntimeError)):
        syntheses_repo.supersede_with(
            AGENT,
            old.id,
            {
                "claim": "the Emperor is impatient",
                "source_episode_ids": ["ep1"],
                "verb": "CORRECT",
                "confidence": 0.6,
            },
        )
    active = syntheses_repo.list_active_syntheses(AGENT, limit=50)
    assert len(active) == 1


def test_the_record_line_pass_id_guard_on_postgres(
    repo, constellation_repo
):
    """The phase-1 review note, proven on PostgreSQL: a NULL pass id
    NEVER increments ``mention_count``."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)
    star = constellation_repo.upsert_star(AGENT, "the Emperor", "person")
    first = constellation_repo.record_line(AGENT, star.id, episode_id, pass_id=None)
    assert first.mention_count == 1
    second = constellation_repo.record_line(AGENT, star.id, episode_id, pass_id=None)
    assert second.mention_count == 1
    third = constellation_repo.record_line(
        AGENT, star.id, episode_id, pass_id="reflect-abc"
    )
    assert third.mention_count == 2


def test_the_heat_increment_on_postgres(repo, constellation_repo):
    """W5's write path works on PostgreSQL."""
    repo.insert_episode(agent_id=AGENT, body="one", importance=9)
    star = constellation_repo.upsert_star(AGENT, "the Emperor", "person")
    heat = constellation_repo.increment_heat(AGENT, star.id)
    assert heat == 1.0
    with Session(constellation_repo.engine) as session:
        row = session.execute(
            text("SELECT heat FROM memoir_star WHERE id=:id"), {"id": star.id}
        ).first()
        assert float(row[0]) == 1.0
