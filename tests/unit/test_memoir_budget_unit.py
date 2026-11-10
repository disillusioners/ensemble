"""The memoir recall budget — the envelope, the clamp, the footer, the re-check.

Phase 2 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase2-plan.md`` D2.8 / D2.10 /
D2.11; pack ``test/packs/memoir_budget_unit_test.sh``).

What this suite pins:

* the per-episode BODY CLAMP (1,200 tokens) with the house marker
  ``[…body truncated, N chars total]``;
* the total ENVELOPE (6,000 tokens) with a RANKED-DROP — the head results
  stay whole;
* the FOOTER BYTES — the counts, the rank basis, the embedding-identity
  disclosure and the stats flag;
* the FINAL RE-CHECK: the footer is part of the shipped bytes, so the
  envelope is re-measured with it in place and the tail is dropped again
  when the footer tips it over;
* the MARKED-ID EQUALITY: the marked ids EQUAL the rendered ids, and an
  episode dropped for the budget is NEITHER returned NOR marked.

The three MUTATION legs live in the pack: the final re-check removed, the
trim measuring the RAW rows instead of the shipped projection, and the
pre-clamp candidate set being marked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.memoir.models import MemoirEpisode
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services import memoir_retrieval
from daemon.services.memoir_retrieval import (
    MEMOIR_RECALL_BODY_TOKEN_CLAMP,
    MEMOIR_RECALL_TOKEN_BUDGET,
    MemoirRetrievalService,
    render_episode,
)

FIXED_NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


@pytest.fixture()
def service(repo):
    return MemoirRetrievalService(repo, now_fn=lambda: FIXED_NOW)


def _estimate(text: str) -> int:
    from daemon.loader import estimate_tokens

    return estimate_tokens(text)


def _long_body(words: int = 1600) -> str:
    """A body whose RAW size is comfortably over the per-episode clamp."""
    return ("zebra " * words)


# ── the per-episode clamp ─────────────────────────────────────────────────


def test_a_long_body_is_clamped_with_the_house_marker(repo, service):
    """D2.10: the clamp marker carries the ORIGINAL character count."""
    body = _long_body()
    repo.insert_episode(agent_id="ari", body=body, title="a long memory")
    result = service.recall("ari", "zebra", k=5)
    assert len(result.episodes) == 1
    episode = result.episodes[0]
    assert episode["body_truncated"] is True
    assert episode["body_chars_total"] == len(body)
    assert f"[…body truncated, {len(body)} chars total]" in episode["body"]
    # The shipped body is UNDER the clamp.
    assert _estimate(episode["body"]) <= MEMOIR_RECALL_BODY_TOKEN_CLAMP


def test_a_short_body_is_not_clamped(repo, service):
    body = "a short memory of the first day"
    repo.insert_episode(agent_id="ari", body=body, title="short")
    result = service.recall("ari", "first", k=5)
    assert result.episodes[0]["body"] == body
    assert result.episodes[0]["body_truncated"] is False


# ── the envelope + the ranked drop ────────────────────────────────────────


def test_the_envelope_is_six_thousand_tokens():
    assert MEMOIR_RECALL_TOKEN_BUDGET == 6000


def test_the_ranked_drop_keeps_the_head_results_whole(repo, service):
    """D2.10: the head stays whole; the TAIL is dropped."""
    base = FIXED_NOW - timedelta(days=10)
    ids = []
    for index in range(8):
        episode = repo.insert_episode(
            agent_id="ari",
            body=_long_body(1500 + index),
            title=f"memory {index}",
            created_at=base + timedelta(minutes=index),
        )
        ids.append(episode)
    result = service.recall("ari", "zebra", k=8)
    assert result.dropped > 0, "the corpus did not exceed the envelope"
    assert len(result.episodes) == 8 - result.dropped
    assert result.total_tokens <= MEMOIR_RECALL_TOKEN_BUDGET
    # The head results are whole — none of the shipped bodies is truncated
    # away by the DROP (they are clamped individually, never dropped).
    assert all("zebra" in episode["body"] for episode in result.episodes)


def test_a_dropped_episode_is_neither_returned_nor_marked(repo, service):
    """D2.8 / AM-14: a dropped episode is not marked."""
    base = FIXED_NOW - timedelta(days=10)
    ids = []
    for index in range(8):
        ids.append(
            repo.insert_episode(
                agent_id="ari",
                body=_long_body(1500 + index),
                title=f"memory {index}",
                created_at=base + timedelta(minutes=index),
            )
        )
    result = service.recall("ari", "zebra", k=8)
    dropped = [episode_id for episode_id in ids if episode_id not in result.rendered_ids]
    assert dropped, "the corpus did not exceed the envelope"
    assert result.marked_ids == result.rendered_ids
    with Session(repo.engine) as session:
        for episode_id in dropped:
            row = session.exec(
                select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
            ).first()
            assert row.access_count == 0
            assert row.last_accessed_at is None


# ── the footer bytes ──────────────────────────────────────────────────────


def test_the_footer_carries_the_counts_and_the_disclosure(repo, service, monkeypatch):
    """D2.10 / D2.11: the footer is the agent-visible envelope."""
    # The v1 SHAPE: the reflection lane is OFF. The ``syntheses=`` token
    # rides the LANE (never the match count) — with the lane ON a service
    # without a syntheses repository honestly reports ``syntheses=0``
    # (the v2 disclosure, pinned in the v2 dual-recall suite). The
    # ABSOLUTE absence pin below is therefore a LANE-OFF claim, and the
    # v1 envelope is exactly the lane-OFF envelope.
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", "false")
    repo.insert_episode(agent_id="ari", body="the preserved first day")
    result = service.recall("ari", "preserved", k=5)
    footer = result.footer
    assert footer.startswith("[memoir_recall: ")
    assert footer.endswith("]")
    assert "episodes=1" in footer
    assert "dropped=0" in footer
    assert "budget=6000" in footer
    assert "rank=rrf" in footer
    assert "missing_embedding=1" in footer
    assert "active_model=none" in footer
    assert "embedding_excluded=0" in footer
    # AM-33 footer extension (tidier H7): the degrade REASON rides the
    # footer — this service has no embedder, so the token is
    # ``no_embedder`` and the semantic lane is disclosed as off.
    assert "semantic=off" in footer
    assert "semantic_reason=no_embedder" in footer
    assert "stats_updated=true" in footer
    assert "note=partial_recall" not in footer
    # THE ABSOLUTE v1 BYTE-PIN: the v1 footer carries NO syntheses
    # token at all (not ``syntheses=0`` — no token).
    assert "syntheses=" not in footer


def test_the_footer_carries_the_partial_recall_note(repo):
    """D2.11: an identity/dims mismatch is VISIBLE to the agent."""
    repo.insert_episode(agent_id="ari", body="the preserved first day")
    repo.store_embedding(
        repo.insert_episode(agent_id="ari", body="another preserved day"),
        [1.0, 0.0],
        "other-model",
        2,
    )
    service = MemoirRetrievalService(
        repo,
        embedder=lambda query: [1.0, 0.0],
        embedding_model="active-model",
        embedding_dims=2,
        now_fn=lambda: FIXED_NOW,
    )
    result = service.recall("ari", "preserved", k=5)
    assert result.embedding_excluded == 1
    assert "embedding_excluded=1" in result.footer
    assert "note=partial_recall" in result.footer
    assert "active_model=active-model" in result.footer
    # The healthy semantic lane reports the NONE reason explicitly, so a
    # missing token is itself detectable (H7).
    assert "semantic=on" in result.footer
    assert "semantic_reason=none" in result.footer


def test_the_footer_of_a_timeline_never_claims_the_stats(repo, service):
    repo.insert_episode(agent_id="ari", body="the preserved first day")
    result = service.timeline("ari")
    assert result.footer.startswith("[memoir_timeline: ")
    assert "stats_updated=false" in result.footer
    assert "rank=timeline" in result.footer


# ── the final re-check ────────────────────────────────────────────────────


def _rendered_size(repo, service, episode_id: str) -> int:
    """The SHIPPED size of one episode — measured on the projection."""
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()
        projection = service._project(row)
    return _estimate(render_episode(projection))


def test_the_final_re_check_drops_the_tail_when_the_footer_tips_it_over(
    monkeypatch, repo, service
):
    """The footer is part of the shipped bytes.

    The budget is set so that the EPISODES alone fit and the FOOTER is
    what pushes the total over. Without the final re-check the shipped
    projection would exceed the envelope.
    """
    base = FIXED_NOW - timedelta(days=10)
    first = repo.insert_episode(
        agent_id="ari", body="zebra " * 200, title="first", created_at=base
    )
    second = repo.insert_episode(
        agent_id="ari",
        body="zebra " * 200,
        title="second",
        created_at=base + timedelta(minutes=1),
    )
    episodes_fit = _rendered_size(repo, service, first) + _rendered_size(
        repo, service, second
    )
    monkeypatch.setattr(
        memoir_retrieval, "MEMOIR_RECALL_TOKEN_BUDGET", episodes_fit
    )

    result = service.recall("ari", "zebra", k=5)

    assert len(result.episodes) == 1, "the footer did not tip the envelope over"
    assert result.dropped == 1
    assert result.total_tokens <= episodes_fit
    assert result.body_tokens + _estimate(result.footer) <= episodes_fit


def test_the_shipped_projection_is_measured_not_the_raw_rows(
    monkeypatch, repo, service
):
    """The trim measures the SHIPPED bytes, never the raw rows (M-1).

    The corpus is sized so that the CLAMPED projection fits the envelope
    while the RAW rows do not. A trim that measured the raw rows would
    drop an episode the agent should have seen.
    """
    base = FIXED_NOW - timedelta(days=10)
    ids = []
    for index in range(3):
        ids.append(
            repo.insert_episode(
                agent_id="ari",
                body=_long_body(1600 + index),
                title=f"memory {index}",
                created_at=base + timedelta(minutes=index),
            )
        )
    clamped_total = sum(_rendered_size(repo, service, episode_id) for episode_id in ids)
    raw_total = 0
    with Session(repo.engine) as session:
        for episode_id in ids:
            row = session.exec(
                select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
            ).first()
            raw_total += _estimate(row.body)
    # The envelope leaves room for the FOOTER on top of the clamped
    # projection, and the RAW rows do not fit in it — so a trim that
    # measured the raw rows would drop an episode the agent should see.
    budget = clamped_total + 200
    assert raw_total > budget, "the corpus does not discriminate"

    monkeypatch.setattr(
        memoir_retrieval, "MEMOIR_RECALL_TOKEN_BUDGET", budget
    )
    result = service.recall("ari", "zebra", k=5)
    assert sorted(result.rendered_ids) == sorted(ids)
    assert result.dropped == 0
