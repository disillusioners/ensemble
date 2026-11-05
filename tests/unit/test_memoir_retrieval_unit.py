"""The memoir retrieval service — the SQLite leg, the RRF arithmetic, the timeline.

Phase 2 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase2-plan.md`` AC2 (the
SQLite leg) / AC3 (the fusion) / AC4 (the timeline); pack
``test/packs/memoir_retrieval_unit_test.sh``).

What this suite pins:

* **AC2** — the lexical ranker has ONE interface behind two drivers: the
  SQLite leg returns an ordinal-ranked candidate list of ``(id, score)``
  pairs. The PostgreSQL leg lives in the PG pack (the driver-aware split
  is only provable against a real PG).
* **AC3** — the RRF arithmetic is EXACT against a HAND-COMPUTED fixture:
  competition ranks 1/2/2/4, a row absent from a ranker carries NO term,
  ``RRF_K = 60``, the fixed summation order, no rounding, the TOTAL
  comparator, importance re-applied after the prior multiplication, and
  the NULL-safe idle basis that coalesces to ``created_at``.
* **AC4** — the timeline filters inclusively on ``event_at`` with the
  parameters named ``since`` / ``until``, narrows lexically with ``q``,
  neither prioritises nor deprioritises a keepsake or a stale row,
  writes NO access stats, and the signature carries no ``from`` / ``to``.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.memoir.models import MemoirEpisode
from daemon.repositories.memoir.repository import (
    MEMOIR_RANK_CANDIDATES,
    MemoirRepository,
)
from daemon.services.memoir_retrieval import (
    MEMOIR_PRIOR_TAU_DAYS,
    RRF_K,
    MemoirRetrievalService,
    competition_ranks,
    fuse_rankings,
    recency_multiplier,
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


def _service(repo, **kwargs) -> MemoirRetrievalService:
    kwargs.setdefault("now_fn", lambda: FIXED_NOW)
    return MemoirRetrievalService(repo, **kwargs)


# ── AC3 — the hand-computed RRF fixture ───────────────────────────────────


def test_rrf_k_is_sixty_and_is_a_module_constant():
    """AM-5: the fusion constant is 60 and it is NOT a config knob."""
    assert RRF_K == 60
    assert isinstance(RRF_K, int)


def test_the_candidate_pool_is_one_hundred_per_ranker():
    """AM-2: 100 candidates PER RANKER; the two pools are independent."""
    assert MEMOIR_RANK_CANDIDATES == 100


def test_competition_ranks_skip_after_a_tie():
    """D2.5 / AM-3: equal scores SHARE a rank; the next distinct score skips."""
    rows = [("ep-a", 10.0), ("ep-b", 8.0), ("ep-c", 8.0), ("ep-d", 5.0)]
    assert competition_ranks(rows) == {"ep-a": 1, "ep-b": 2, "ep-c": 2, "ep-d": 4}


def test_the_fusion_matches_the_hand_computed_fixture():
    """The exact arithmetic — hand-computed, not derived from the code.

    lexical ranks: a=1, b=2, c=2, d=4  (scores 10, 8, 8, 5)
    semantic ranks: b=1, e=2          (scores 1, 0.5)

    fused(a) = 1/(60+1)
    fused(b) = 1/(60+2) + 1/(60+1)     <- in BOTH pools
    fused(c) = 1/(60+2)                <- ABSENT from the semantic ranker: NO term
    fused(d) = 1/(60+4)
    fused(e) = 1/(60+2)                <- ABSENT from the lexical ranker: NO term
    """
    lexical = [("ep-a", 10.0), ("ep-b", 8.0), ("ep-c", 8.0), ("ep-d", 5.0)]
    semantic = [("ep-b", 1.0), ("ep-e", 0.5)]

    fused = fuse_rankings(lexical, semantic)

    assert fused["ep-a"] == 1.0 / 61
    assert fused["ep-b"] == 1.0 / 62 + 1.0 / 61
    assert fused["ep-c"] == 1.0 / 62
    assert fused["ep-d"] == 1.0 / 64
    assert fused["ep-e"] == 1.0 / 62


def test_an_absent_ranker_contributes_NO_term_not_a_zero_term():
    """D2.5: "absent" means the term is MISSING, not that the row scores zero.

    A zero term would be ``1/(60+0)`` — a rank of ZERO, which is not a
    rank at all and would hand an absent row a LARGER contribution than
    a present one.
    """
    fused = fuse_rankings([("ep-a", 1.0)], [("ep-b", 1.0)])
    assert fused["ep-a"] == 1.0 / 61
    assert fused["ep-b"] == 1.0 / 61
    # An absent-from-both row is not in the fusion at all.
    assert "ep-c" not in fuse_rankings([("ep-a", 1.0)], [("ep-b", 1.0)])


def test_the_fusion_does_not_round():
    """D2.6: rounding manufactures ties and breaks monotonicity."""
    fused = fuse_rankings([("ep-a", 1.0), ("ep-b", 1.0)], [("ep-a", 1.0), ("ep-b", 1.0)])
    # 1/61 + 1/61 is 0.03278688524590164... — a rounded value would be
    # 0.032787 and would not equal the exact sum.
    assert fused["ep-a"] == 1.0 / 61 + 1.0 / 61
    assert fused["ep-a"] != round(fused["ep-a"], 6)


def test_the_summation_order_is_fixed_lexical_then_semantic():
    """D2.6 / AM-5: the terms are summed in a FIXED ranker order.

    Float addition is order-sensitive in general (and IEEE addition is
    commutative for two terms, so the ORDER is pinned at the SOURCE —
    which is where the determinism actually lives).
    """
    source = inspect.getsource(fuse_rankings)
    assert source.index("lexical_ranks[row_id]") < source.index(
        "semantic_ranks[row_id]"
    )


def test_the_recency_prior_decays_and_exempts_the_keepsake():
    """D2.7: a keepsake is never decayed; tau is 180 days."""
    assert MEMOIR_PRIOR_TAU_DAYS == 180.0
    assert recency_multiplier(is_keepsake=True, idle_days=10_000.0) == 1.0
    assert recency_multiplier(is_keepsake=False, idle_days=0.0) == 1.0
    assert recency_multiplier(is_keepsake=False, idle_days=180.0) == pytest.approx(
        0.36787944117144233
    )


# ── AC2 — the SQLite lexical leg ──────────────────────────────────────────


def test_the_sqlite_lexical_leg_returns_an_ordinal_ranked_list(repo):
    repo.insert_episode(agent_id="ari", body="the preserved first day")
    repo.insert_episode(agent_id="ari", body="an unrelated afternoon")
    repo.insert_episode(agent_id="ari", body="the first day again, preserved")

    ranked = repo.lexical_rank("ari", "preserved")
    assert ranked, "the SQLite leg returned nothing for a matching query"
    assert all(isinstance(row_id, str) for row_id, _score in ranked)
    assert all(isinstance(score, float) for _row_id, score in ranked)
    # A non-matching query returns nothing (the PG leg behaves the same:
    # the tsquery match is the filter).
    assert repo.lexical_rank("ari", "zebra") == []


def test_the_lexical_leg_is_scoped_to_the_agent(repo):
    """DD-13: the identity is the agent, never a caller-supplied scope."""
    repo.insert_episode(agent_id="ari", body="the preserved first day")
    repo.insert_episode(agent_id="someone-else", body="the preserved first day")
    ranked = repo.lexical_rank("ari", "preserved")
    assert len(ranked) == 1


def test_the_lexical_leg_orders_by_score_then_id(repo):
    repo.insert_episode(agent_id="ari", body="preserved")
    repo.insert_episode(agent_id="ari", body="preserved preserved")
    ranked = repo.lexical_rank("ari", "preserved")
    assert len(ranked) == 2
    assert ranked[0][1] >= ranked[1][1]


# ── AC3 — the comparator and the idle basis (behavioural) ─────────────────


def _two_episodes_same_instant(repo, *, first_importance: int, second_importance: int):
    """Two episodes with the SAME fused score and the SAME idle basis."""
    created = FIXED_NOW - timedelta(days=1)
    first = repo.insert_episode(
        agent_id="ari",
        body="the preserved first day",
        title="same",
        importance=first_importance,
        created_at=created,
    )
    second = repo.insert_episode(
        agent_id="ari",
        body="the preserved first day",
        title="same",
        importance=second_importance,
        created_at=created,
    )
    return first, second


def test_importance_is_re_applied_after_the_prior_multiplication(repo):
    """D2.7: the comparator is (-prior_multiplied, importance, event_at, id).

    The two episodes carry the SAME fused score and the SAME idle basis,
    so the prior multiplication is identical for both and the importance
    decides — which is exactly what "importance is re-applied AFTER the
    multiplication" means.
    """
    low, high = _two_episodes_same_instant(
        repo, first_importance=3, second_importance=9
    )
    service = _service(repo)
    result = service.recall("ari", "preserved", k=5)
    assert result.rendered_ids[0] == high
    assert result.rendered_ids[1] == low


def test_the_fused_score_dominates_the_importance(repo):
    """The comparator is (-score, importance): a lower score does not win."""
    created = FIXED_NOW - timedelta(days=1)
    weak = repo.insert_episode(
        agent_id="ari",
        body="preserved",
        title="weak",
        importance=10,
        created_at=created,
    )
    strong = repo.insert_episode(
        agent_id="ari",
        body="preserved preserved preserved preserved preserved",
        title="strong",
        importance=1,
        created_at=created,
    )
    service = _service(repo)
    result = service.recall("ari", "preserved", k=5)
    assert result.rendered_ids[0] == strong
    assert result.rendered_ids[1] == weak


def test_the_idle_basis_coalesces_to_created_at(repo):
    """D2.7 / AM-4: ``idle = now − coalesce(last_accessed_at, created_at)``.

    A never-recalled row has a NULL ``last_accessed_at``. If the basis
    were NULL-unsafe the row would carry no prior (or a 1.0 prior) and
    would out-rank a fresh row. The OLD row must lose to the FRESH one.
    """
    old = repo.insert_episode(
        agent_id="ari",
        body="the preserved first day",
        title="old",
        created_at=FIXED_NOW - timedelta(days=360),
    )
    fresh = repo.insert_episode(
        agent_id="ari",
        body="the preserved first day",
        title="fresh",
        created_at=FIXED_NOW - timedelta(days=1),
    )
    with Session(repo.engine) as session:
        assert session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == old)
        ).first().last_accessed_at is None

    service = _service(repo)
    result = service.recall("ari", "preserved", k=5)
    assert result.rendered_ids[0] == fresh
    assert result.rendered_ids[1] == old


def test_the_comparator_is_total_and_stable(repo):
    """D2.7: ``id ASC`` makes the order stable — no two rows tie."""
    created = FIXED_NOW - timedelta(days=1)
    ids = sorted(
        repo.insert_episode(
            agent_id="ari",
            body="the preserved first day",
            title="same",
            importance=5,
            created_at=created,
        )
        for _ in range(4)
    )
    service = _service(repo)
    first = service.recall("ari", "preserved", k=5).rendered_ids
    second = service.recall("ari", "preserved", k=5).rendered_ids
    assert first == second
    assert first == ids


# ── AC4 — the timeline ────────────────────────────────────────────────────


def test_the_timeline_parameters_are_since_and_until(repo):
    """DR-4 / AM-42: ``from`` / ``to`` are Python keywords."""
    signature = inspect.signature(MemoirRetrievalService.timeline)
    names = list(signature.parameters)
    assert "since" in names
    assert "until" in names
    assert "from" not in names
    assert "to" not in names


def test_the_timeline_window_is_inclusive_at_both_ends(repo):
    base = FIXED_NOW - timedelta(days=10)
    inside = repo.insert_episode(
        agent_id="ari", body="inside", created_at=base, event_at=base
    )
    at_since = repo.insert_episode(
        agent_id="ari",
        body="at since",
        created_at=base,
        event_at=base + timedelta(days=2),
    )
    at_until = repo.insert_episode(
        agent_id="ari",
        body="at until",
        created_at=base,
        event_at=base + timedelta(days=4),
    )
    outside = repo.insert_episode(
        agent_id="ari",
        body="outside",
        created_at=base,
        event_at=base + timedelta(days=6),
    )
    service = _service(repo)
    result = service.timeline(
        "ari", since=base + timedelta(days=2), until=base + timedelta(days=4)
    )
    ids = set(result.rendered_ids)
    assert inside not in ids
    assert at_since in ids
    assert at_until in ids
    assert outside not in ids


def test_the_timeline_narrows_lexically_with_q(repo):
    base = FIXED_NOW - timedelta(days=10)
    hit = repo.insert_episode(
        agent_id="ari", body="the preserved first day", created_at=base, event_at=base
    )
    miss = repo.insert_episode(
        agent_id="ari", body="an unrelated afternoon", created_at=base, event_at=base
    )
    service = _service(repo)
    result = service.timeline("ari", q="preserved")
    assert result.rendered_ids == [hit]
    assert miss not in result.rendered_ids
    assert "rank=timeline_lexical" in result.footer


def test_the_timeline_writes_no_access_stats(repo):
    """D2.9: a timeline read is not an act of attention (DD-12)."""
    base = FIXED_NOW - timedelta(days=10)
    episode = repo.insert_episode(
        agent_id="ari", body="the preserved first day", created_at=base, event_at=base
    )
    service = _service(repo)
    service.timeline("ari")
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode)
        ).first()
        assert row.access_count == 0
        assert row.last_accessed_at is None


def test_the_timeline_does_not_prioritise_a_keepsake(repo):
    """D2.9: NO keepsake priority — the order is the event order."""
    base = FIXED_NOW - timedelta(days=10)
    plain = repo.insert_episode(
        agent_id="ari", body="plain", created_at=base, event_at=base
    )
    keepsake = repo.insert_episode(
        agent_id="ari", body="keepsake", created_at=base, event_at=base
    )
    repo.set_pin(keepsake, actor="empress", reason="a keepsake")
    service = _service(repo)
    result = service.timeline("ari")
    # The two share an event_at, so the stable tie-break (id ASC) decides
    # — the keepsake gets no priority.
    assert result.rendered_ids == sorted([plain, keepsake])


def test_the_timeline_refuses_an_inverted_window(repo):
    service = _service(repo)
    with pytest.raises(Exception) as excinfo:
        service.timeline(
            "ari",
            since=FIXED_NOW,
            until=FIXED_NOW - timedelta(days=1),
        )
    assert getattr(excinfo.value, "code", "") == "invalid_window"


# ── the recall refusals ───────────────────────────────────────────────────


@pytest.mark.parametrize("bad_k", [0, -1, 21, 1000])
def test_an_out_of_range_k_is_refused_not_clamped(repo, bad_k):
    """D2.10: an out-of-range k is a TYPED REFUSAL, never a silent clamp."""
    service = _service(repo)
    with pytest.raises(Exception) as excinfo:
        service.recall("ari", "preserved", k=bad_k)
    assert getattr(excinfo.value, "code", "") == "k_out_of_range"


def test_an_empty_query_is_refused(repo):
    service = _service(repo)
    with pytest.raises(Exception) as excinfo:
        service.recall("ari", "   ")
    assert getattr(excinfo.value, "code", "") == "query_required"


def test_the_recall_marks_exactly_the_shipped_ids(repo):
    """D2.8 / AM-14: the marked-id list EQUALS the rendered-id list."""
    base = FIXED_NOW - timedelta(days=10)
    episode = repo.insert_episode(
        agent_id="ari", body="the preserved first day", created_at=base, event_at=base
    )
    service = _service(repo)
    result = service.recall("ari", "preserved", k=5)
    assert result.marked_ids == result.rendered_ids
    assert episode in result.marked_ids
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode)
        ).first()
        assert row.access_count == 1
        assert row.last_accessed_at is not None
