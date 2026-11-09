"""Memoir Store v2 — the RECALL CONSUMER (the effective-importance
tie-break) and the display-freeze.

Phase 4 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase4-plan.md`` T4.6;
AC7 / V2-D 4.8 / V2-41; pack
``test/packs/memoir_v2_poignancy_retrieval_test.sh``).

What this suite pins:

* THE ORDERING TIE-BREAK (AC7) — among the episodes with an EQUAL fused
  score, the one with the higher EFFECTIVE importance
  (``COALESCE(poignancy, importance)``) ranks first. This is the
  MUTATION LEG (i): sorting by the RAW authored importance instead
  flips this assertion RED;
* THE DISPLAY-FREEZE (B6 / V2-41) — the projection and the rendering
  keep showing the AUTHORED ``importance``, so the shipped recall text
  stays byte-identical across every lane. This is the MUTATION LEG (ii):
  rendering the SCORE instead of the authored value flips this
  assertion RED;
* the reflection TRIGGER's COALESCE — the other consumer of the same
  effective importance — is unchanged (no drift).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from daemon.repositories.memoir.models import MemoirEpisode
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.memoir_retrieval import (
    MemoirRetrievalService,
    effective_importance,
    render_episode,
)

FIXED_NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)

AGENT = "ari"


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/memoir-poignancy-retrieval.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


def _service(repo) -> MemoirRetrievalService:
    return MemoirRetrievalService(repo, now_fn=lambda: FIXED_NOW)


def _row(repo, episode_id):
    with Session(repo.engine) as session:
        return session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()


def _two_episodes_same_instant(repo, *, first: int, second: int):
    """Two episodes with the SAME fused score and the SAME idle basis."""
    created = FIXED_NOW - timedelta(days=1)
    first_id = repo.insert_episode(
        agent_id=AGENT,
        body="the preserved first day",
        title="same",
        importance=first,
        created_at=created,
    )
    second_id = repo.insert_episode(
        agent_id=AGENT,
        body="the preserved first day",
        title="same",
        importance=second,
        created_at=created,
    )
    return first_id, second_id


# ── AC7 — the ordering tie-break (MUTATION LEG i) ────────────────────────


def test_the_scored_episode_outranks_the_authored_one(repo):
    """AC7: the tie-break reads the EFFECTIVE importance.

    THE MUTATION LEG (i): if the ranker sorted by the RAW authored
    importance, the authored-8 episode would come FIRST and this
    assertion flips RED.
    """
    scored_id, authored_id = _two_episodes_same_instant(
        repo, first=2, second=8
    )
    # The first episode is SCORED at 9 — its authored importance is 2.
    assert repo.record_poignancy_score(scored_id, 9) is True

    service = _service(repo)
    result = service.recall(AGENT, "preserved", k=5)

    assert result.rendered_ids[0] == scored_id
    assert result.rendered_ids[1] == authored_id


def test_the_authored_importance_still_decides_without_a_score(repo):
    """Without a score the AUTHORED importance decides — the unscored
    rows keep their v1 order."""
    low, high = _two_episodes_same_instant(repo, first=3, second=9)

    service = _service(repo)
    result = service.recall(AGENT, "preserved", k=5)

    assert result.rendered_ids[0] == high
    assert result.rendered_ids[1] == low


def test_the_effective_importance_helper_reads_the_score_first(repo):
    """The helper is the COALESCE consumer: the score wins when it
    exists, and the authored importance stands until then."""
    scored_id, authored_id = _two_episodes_same_instant(repo, first=2, second=8)
    repo.record_poignancy_score(scored_id, 9)

    assert effective_importance(_row(repo, scored_id)) == 9
    assert effective_importance(_row(repo, authored_id)) == 8


def test_the_effective_importance_helper_tolerates_a_missing_column():
    """A row WITHOUT the column (an older shape) falls back to the
    authored importance — never a crash."""
    from types import SimpleNamespace

    row = SimpleNamespace(importance=6)
    assert effective_importance(row) == 6
    row = SimpleNamespace(importance=6, poignancy=None)
    assert effective_importance(row) == 6
    row = SimpleNamespace(importance=6, poignancy=0)
    assert effective_importance(row) == 0


# ── AC7 — the display-freeze (MUTATION LEG ii) ───────────────────────────


def test_the_rendered_text_shows_the_authored_importance(repo):
    """THE DISPLAY-FREEZE: the shipped text keeps the AUTHORED value.

    THE MUTATION LEG (ii): if the projection rendered the SCORE instead
    of the authored importance, this assertion flips RED.
    """
    scored_id, _authored_id = _two_episodes_same_instant(repo, first=2, second=8)
    repo.record_poignancy_score(scored_id, 9)

    service = _service(repo)
    result = service.recall(AGENT, "preserved", k=5)

    projections = {
        projection["id"]: projection for projection in result.episodes
    }
    shipped = render_episode(projections[scored_id])
    assert "importance=2" in shipped
    assert "importance=9" not in shipped


def test_the_projection_keeps_the_authored_importance(repo):
    """The projection is the display surface — it keeps the authored
    value."""
    scored_id, _authored_id = _two_episodes_same_instant(repo, first=2, second=8)
    repo.record_poignancy_score(scored_id, 9)

    service = _service(repo)
    result = service.recall(AGENT, "preserved", k=5)

    projections = {
        projection["id"]: projection for projection in result.episodes
    }
    assert projections[scored_id]["importance"] == 2


def test_the_tool_block_renders_the_authored_importance(repo):
    """The tool-level block (``daemon/tools/memoir.py``) renders the
    projection's ``importance`` — so it too stays on the authored
    value."""
    from daemon.tools.memoir import _render_episode_block

    scored_id, _authored_id = _two_episodes_same_instant(repo, first=2, second=8)
    repo.record_poignancy_score(scored_id, 9)

    service = _service(repo)
    result = service.recall(AGENT, "preserved", k=5)

    projections = {
        projection["id"]: projection for projection in result.episodes
    }
    block = _render_episode_block(projections[scored_id])
    assert "importance: 2" in block
    assert "importance: 9" not in block


def test_the_shipped_text_is_byte_identical_with_and_without_a_score(repo):
    """THE FREEZE, PROVEN BYTE-FOR-BYTE.

    The SAME episode renders identically whether or not it carries a
    score — the score changes the ORDER, never the TEXT.
    """
    episode_id = repo.insert_episode(
        agent_id=AGENT,
        body="a preserved moment",
        title="same",
        importance=6,
        created_at=FIXED_NOW - timedelta(days=1),
    )
    service = _service(repo)

    def _shipped() -> str:
        result = service.recall(AGENT, "preserved", k=5)
        return "\n".join(
            render_episode(projection) for projection in result.episodes
        )

    before = _shipped()

    repo.record_poignancy_score(episode_id, 10)
    after = _shipped()

    assert before == after
    assert "importance=6" in after


def test_the_render_helper_uses_the_projection_value():
    """``render_episode`` renders the PROJECTION's value — the service
    owns the projection, so the two cannot drift."""
    projection = {
        "id": "ep-1",
        "title": "t",
        "body": "b",
        "created_at": "2026-11-09T00:00:00+00:00",
        "event_at": "2026-11-09T00:00:00+00:00",
        "importance": 4,
        "is_keepsake": False,
    }
    assert "importance=4" in render_episode(projection)


# ── the other consumer — no drift ────────────────────────────────────────


def test_the_reflection_trigger_still_reads_the_effective_importance(repo):
    """The reflection trigger is the OTHER consumer of the same COALESCE
    — it is unchanged by this phase (no drift)."""
    from daemon.repositories.memoir.reflection_repository import (
        MemoirReflectionRepository,
    )

    episode_id = repo.insert_episode(
        agent_id=AGENT,
        body="a moment",
        importance=5,
        created_at=FIXED_NOW - timedelta(hours=1),
    )
    repository = MemoirReflectionRepository(repo.engine)

    before = repository.derive_trigger(
        AGENT, window_start=FIXED_NOW - timedelta(days=2)
    )
    assert before[0] == 5

    repo.record_poignancy_score(episode_id, 9)

    after = repository.derive_trigger(
        AGENT, window_start=FIXED_NOW - timedelta(days=2)
    )
    # The trigger sums the EFFECTIVE importance — the score replaces the
    # authored value in the sum (never adds to it).
    assert after[0] == 9
