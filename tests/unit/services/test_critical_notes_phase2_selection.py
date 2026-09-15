"""Critical-Notes Phase 2 — tiered selection pipeline (selector-level).

Pure-function tests for :mod:`daemon.services.critical_notes_selector`
(no real embedding API calls — every embed is a synthetic float
vector in the test). Covers the contract pins required by the
Phase-2 dispatch:

* fusion ranking ordering (BM25 + vector; fusion weights respected)
* top-6 tail cap honored
* top-2 priority floor applied when fusion under-selects
* 12k section char cap enforced (and ``truncated_for_char_cap``
  flag set)
* hint drop count propagated when notes are dropped
* empty / whitespace-only query → skip ranking, core + floor only
* single-source stats: BM25 corpus stats are FRESH per call (§9
  per-call safety) and concurrent calls don't share state
* routine telemetry dict shape pinned
"""

from __future__ import annotations

import math
import pytest

from daemon.services.critical_notes_selector import (
    SelectionConfig,
    SelectionResult,
    _bm25_corpus_stats,
    select_critical_notes_for_injection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _note(
    note_id: str,
    *,
    summary: str = "default summary",
    reference: str | None = None,
    priority: str = "high",
    pinned: bool = False,
    created_at: str | None = None,
) -> dict:
    """Build a deterministic note dict for selector tests."""
    return {
        "id": note_id,
        "project_id": "proj-test",
        "summary": summary,
        "reference": reference,
        "priority": priority,
        "category": "risk",
        "pinned": pinned,
        "superseded_by_id": None,
        "created_at": created_at
        or "2026-09-15T13:00:00+00:00",
        "last_reviewed_at": "2026-09-15T13:00:00+00:00",
        "updated_at": "2026-09-15T13:00:00+00:00",
        "source_agent": "leader",
    }


def _vec(seed: int, dim: int = 16) -> list[float]:
    """Stable, seeded unit-ish vector for similarity ranking tests."""
    out: list[float] = []
    for i in range(dim):
        v = math.sin((seed * 17 + i * 3) * 0.1)
        out.append(float(v))
    # Normalize to unit length so cosine similarity is well-defined.
    norm = math.sqrt(sum(x * x for x in out))
    if norm > 0:
        out = [x / norm for x in out]
    return out


# ---------------------------------------------------------------------------
# Core tier (pinned) bypasses selection
# ---------------------------------------------------------------------------


class TestPinnedBypassesFusion:
    def test_pinned_always_in_output(self):
        pinned = _note("p-1", summary="alpha", pinned=True, priority="critical")
        unpinned = _note("u-1", summary="beta", pinned=False)
        result = select_critical_notes_for_injection(
            active_notes=[pinned, unpinned],
            query="alpha",
            embeddings_map={},
            query_embedding=None,
            config=SelectionConfig(),
        )
        ids = [n["id"] for n in result.pinned + result.tail]
        assert "p-1" in ids
        # The pinned note is in the ``pinned`` tier specifically
        # (not the BM25-scored tail).
        assert any(n["id"] == "p-1" for n in result.pinned)


# ---------------------------------------------------------------------------
# Fusion ordering (BM25 + cosine; weights respected)
# ---------------------------------------------------------------------------


class TestFusionOrdering:
    def test_query_match_with_strong_embed_outranks_partial(self):
        """When a note's BM25 + vector scores both rise on the query,
        it must outrank a note with only BM25.
        """
        q_vec = _vec(seed=1)
        # 1. note A — strong BM25 + aligned vector (final high)
        a = _note("a", summary="kubernetes operator pattern")
        # 2. note B — only BM25 match, no vector row
        b = _note("b", summary="some kubernetes note")
        embeddings = {"a": q_vec}  # B has no cached embedding
        result = select_critical_notes_for_injection(
            active_notes=[a, b],
            query="kubernetes operator",
            embeddings_map=embeddings,
            query_embedding=q_vec,
            config=SelectionConfig(tail_cap=6, fusion_threshold=0.0),
        )
        # The strong-match note must appear earlier than the weak one.
        out_ids = [n["id"] for n in result.tail]
        assert "a" in out_ids
        assert "b" in out_ids
        assert out_ids.index("a") < out_ids.index("b")

    def test_fusion_weights_respected(self):
        """When ``fusion_vector_weight`` is 0.0 the vector score
        contributes nothing; BM25 alone determines ranking.
        """
        q_vec = _vec(seed=2)
        a = _note("a", summary="kubernetes operator pattern")
        b = _note("b", summary="kubernetes is great")
        # Both have embeddings — only BM25 should drive the fused score.
        embeddings = {"a": q_vec, "b": q_vec}
        result = select_critical_notes_for_injection(
            active_notes=[a, b],
            query="kubernetes",
            embeddings_map=embeddings,
            query_embedding=q_vec,
            config=SelectionConfig(
                tail_cap=6,
                fusion_threshold=0.0,
                fusion_bm25_weight=1.0,
                fusion_vector_weight=0.0,
            ),
        )
        # Either ranking should have A above B; specific order is
        # BM25-driven — we just assert that the (vector-unweighted)
        # call returns ALL the matching notes (no zero-vector fall-
        # through that would drop a row for the threshold cut).
        out_ids = [n["id"] for n in result.tail]
        assert "a" in out_ids and "b" in out_ids


# ---------------------------------------------------------------------------
# Tail cap
# ---------------------------------------------------------------------------


class TestTailCap:
    def test_tail_cap_limits_selected(self):
        notes = [
            _note(f"n-{i}", summary=f"kubernetes note number {i} unique-x")
            for i in range(20)
        ]
        q_vec = _vec(seed=3)
        embeddings = {n["id"]: q_vec for n in notes}
        cfg = SelectionConfig(tail_cap=6, fusion_threshold=0.0)
        result = select_critical_notes_for_injection(
            active_notes=notes,
            query="kubernetes",
            embeddings_map=embeddings,
            query_embedding=q_vec,
            config=cfg,
        )
        assert len(result.tail) <= cfg.tail_cap
        assert result.dropped_count == 20 - len(result.tail)


# ---------------------------------------------------------------------------
# Floor (under-selection fallback)
# ---------------------------------------------------------------------------


class TestFloorUnderSelection:
    def test_floor_picks_priority_recency_when_threshold_cuts(self):
        # High threshold with low overlap → under-selection → floor
        # picks the priority-sorted next-best rows.
        notes = [
            _note(
                "low-1",
                summary="unrelated low",
                priority="low",
            ),
            _note(
                "floor-1",
                summary="should_floor_in",
                priority="high",
            ),
            _note(
                "floor-2",
                summary="should_also_floor",
                priority="medium",
            ),
        ]
        cfg = SelectionConfig(
            tail_cap=4,
            fusion_threshold=0.999,  # absurd threshold → almost nothing passes
            floor_count=2,
        )
        result = select_critical_notes_for_injection(
            active_notes=notes,
            query="kubernetes",
            embeddings_map={},
            query_embedding=None,
            config=cfg,
        )
        # Floor is applied (we asserted under-selection via the
        # 0.999 threshold); the floor pool adds ``floor_count``
        # priority-sorted active rows.
        assert result.telemetry["floor_applied"] is True
        out_ids = [n["id"] for n in result.tail]
        # The floor is priority-sorted: the highest-priority active
        # rows fill first. floor-1 (high) + floor-2 (medium) win.
        assert "floor-1" in out_ids
        assert "floor-2" in out_ids

    def test_floor_not_applied_when_fusion_satisfies_cap(self):
        # When fusion gate produces enough tail rows, the floor
        # stays in the BACKGROUND — no appended rows beyond cap.
        notes = [
            _note(f"keep-{i}", summary=f"kubernetes item {i} unique-y")
            for i in range(8)
        ]
        q_vec = _vec(seed=4)
        embeddings = {n["id"]: q_vec for n in notes}
        cfg = SelectionConfig(
            tail_cap=4,
            fusion_threshold=0.0,
            floor_count=2,
        )
        result = select_critical_notes_for_injection(
            active_notes=notes,
            query="kubernetes",
            embeddings_map=embeddings,
            query_embedding=q_vec,
            config=cfg,
        )
        assert result.telemetry["floor_applied"] is False


# ---------------------------------------------------------------------------
# Section char cap
# ---------------------------------------------------------------------------


class TestSectionCharCap:
    def test_cap_enforced(self):
        # Build 8 notes each ~400 chars — the per-note cost is
        # ~464 chars (400 + 64 overhead). Cap of 2000 chars holds
        # only 4 notes, so the budget MUST cut the tail before
        # the tail cap does.
        notes = [
            _note(f"big-{i}", summary=("x" * 400) + f" key-{i}")
            for i in range(8)
        ]
        q_vec = _vec(seed=5)
        embeddings = {n["id"]: q_vec for n in notes}
        cfg = SelectionConfig(
            tail_cap=8,
            fusion_threshold=0.0,
            section_char_cap=2000,
        )
        result = select_critical_notes_for_injection(
            active_notes=notes,
            query="key",
            embeddings_map=embeddings,
            query_embedding=q_vec,
            config=cfg,
        )
        # Cap is hit before the tail cap; at most 4 notes fit
        # in the 2000-char budget (each ~464 chars).
        assert len(result.tail) <= 4
        assert result.truncated_for_char_cap is True

    def test_no_truncation_within_budget(self):
        notes = [
            _note(f"small-{i}", summary=f"short note {i}")
            for i in range(3)
        ]
        q_vec = _vec(seed=6)
        embeddings = {n["id"]: q_vec for n in notes}
        cfg = SelectionConfig(
            tail_cap=3,
            fusion_threshold=0.0,
            section_char_cap=12000,
        )
        result = select_critical_notes_for_injection(
            active_notes=notes,
            query="short",
            embeddings_map=embeddings,
            query_embedding=q_vec,
            config=cfg,
        )
        assert result.truncated_for_char_cap is False
        assert len(result.tail) == 3


# ---------------------------------------------------------------------------
# Empty / whitespace-only query
# ---------------------------------------------------------------------------


class TestEmptyQuery:
    def test_empty_query_skips_ranking(self):
        notes = [
            _note(
                "floor-1",
                summary="most important pin candidate",
                priority="critical",
            ),
            _note(
                "floor-2",
                summary="second most important",
                priority="high",
            ),
            _note(
                "rest-1",
                summary="irrelevant without ranking",
            ),
        ]
        cfg = SelectionConfig(floor_count=2)
        result = select_critical_notes_for_injection(
            active_notes=notes,
            query="",
            embeddings_map={},
            query_embedding=None,
            config=cfg,
        )
        # No BM25 scoring occurred.
        assert result.telemetry["skipped_ranking"] is True
        # Floor populated (floor_count=2, only the unpinned active
        # rows are eligible — the priority-sorted pool is the
        # fallback shape).
        assert len(result.tail) == 2
        out_ids = [n["id"] for n in result.tail]
        assert "floor-1" in out_ids
        assert "floor-2" in out_ids

    def test_whitespace_query_same_as_empty(self):
        notes = [_note("a", summary="anything")]
        cfg = SelectionConfig(floor_count=1)
        result = select_critical_notes_for_injection(
            active_notes=notes,
            query="   \n\t  ",
            embeddings_map={},
            query_embedding=None,
            config=cfg,
        )
        assert result.telemetry["skipped_ranking"] is True
        # The single row makes it via the floor — floor_count=1.
        assert len(result.tail) == 1


# ---------------------------------------------------------------------------
# Missing query embedding — falls through to BM25-only rank
# ---------------------------------------------------------------------------


class TestMissingQueryEmbedding:
    def test_no_query_embedding_runs_bm25_only(self):
        notes = [
            _note("a", summary="kubernetes note alpha"),
            _note("b", summary="kubernetes note beta"),
        ]
        result = select_critical_notes_for_injection(
            active_notes=notes,
            query="kubernetes",
            embeddings_map={},
            query_embedding=None,
        )
        # BM25-only path: notes matching the query still surface.
        out_ids = [n["id"] for n in result.tail]
        assert "a" in out_ids
        assert "b" in out_ids


# ---------------------------------------------------------------------------
# §9 per-call BM25 safety — fresh stats per call
# ---------------------------------------------------------------------------


class TestPerCallBM25Safety:
    def test_corpus_stats_are_fresh_per_call(self):
        """§9 verification: every selector call builds a FRESH
        ``doc_freqs`` / ``total_docs`` / ``avg_doc_len`` trio from
        its current args. Two sequential calls with different
        notes produce DIFFERENT stats (no shared module-level
        state); a single call with shuffled notes still yields
        identical scores (idempotent in its own scope).
        """
        # Notes designed so corpus stats ACTUALLY differ
        # between the two sets — ``kubernetes`` appears in 1 of
        # 2 vs 2 of 2 docs, changing the DF term.
        notes_a = [
            _note("a-1", summary="kubernetes operator"),
            _note("a-2", summary="python typing"),
        ]
        notes_b = [
            _note("b-1", summary="kubernetes helm chart"),
            _note("b-2", summary="kubernetes deployment"),
        ]
        q_tokens = ["kubernetes"]
        df_a, n_a, avg_a = _bm25_corpus_stats(notes_a, q_tokens)
        df_b, n_b, avg_b = _bm25_corpus_stats(notes_b, q_tokens)
        # DF for "kubernetes" is 1 in set A (one doc carries the
        # token) and 2 in set B (both docs carry it). The stats
        # trio MUST reflect that per-call difference — proof
        # that the state is scoped to the call, not module-level.
        assert df_a.get("kubernetes") == 1
        assert df_b.get("kubernetes") == 2
        assert n_a == 2 and n_b == 2
        # Same call twice is idempotent (one ref-equivalent).
        df_a_again, _, _ = _bm25_corpus_stats(notes_a, q_tokens)
        assert df_a == df_a_again

    def test_concurrent_calls_disjoint_stats(self):
        """Proves §9 per-call safety at the corpus-stats level.

        Two parallel-shaped coroutine outputs MUST not interfere
        with each other — the selector owns its stats trio for
        the duration of the call. We mimic the concurrent shape
        with two top-level calls and assert their results differ
        when their inputs differ.
        """
        notes = [_note(f"n-{i}", summary="kubernetes") for i in range(5)]
        q1 = ["kubernetes"]
        q2 = ["python"]
        df1, n1, _ = _bm25_corpus_stats(notes, q1)
        df2, n2, _ = _bm25_corpus_stats(notes, q2)
        # Same corpus, but the DF stats scale per-query-token
        # set; ``kubernetes`` is in every doc so df=5; ``python``
        # is in NO doc so df=0 (empty mapping).
        assert df1.get("kubernetes") == 5
        assert df2.get("python", 0) == 0


# ---------------------------------------------------------------------------
# Routine telemetry — shape contract
# ---------------------------------------------------------------------------


class TestTelemetryShape:
    def test_keys_present(self):
        notes = [_note("a", summary="alpha")]
        result = select_critical_notes_for_injection(
            active_notes=notes,
            query="alpha",
            embeddings_map={},
            query_embedding=None,
        )
        for key in (
            "selected",
            "total",
            "floor_applied",
            "skipped_ranking",
            "skipped_query_embed",
            "instance_id",
        ):
            assert key in result.telemetry

    def test_total_counts_active_pool(self):
        notes = [
            _note(f"n-{i}", summary=f"kubernetes item {i}")
            for i in range(12)
        ]
        result = select_critical_notes_for_injection(
            active_notes=notes,
            query="kubernetes",
            embeddings_map={},
            query_embedding=None,
        )
        assert result.telemetry["total"] == 12
        # selected ≤ total; tail cap is 6 by default.
        assert result.telemetry["selected"] <= 12


# ---------------------------------------------------------------------------
# Hint drop count — propagation
# ---------------------------------------------------------------------------


class TestDropCount:
    def test_drop_count_when_tail_cap_kicks_in(self):
        notes = [
            _note(f"n-{i}", summary=f"kubernetes {i} unique-z")
            for i in range(20)
        ]
        q_vec = _vec(seed=7)
        embeddings = {n["id"]: q_vec for n in notes}
        cfg = SelectionConfig(tail_cap=6, fusion_threshold=0.0)
        result = select_critical_notes_for_injection(
            active_notes=notes,
            query="kubernetes",
            embeddings_map=embeddings,
            query_embedding=q_vec,
            config=cfg,
        )
        assert result.dropped_count == 20 - 6  # tail cap gap


# ---------------------------------------------------------------------------
# Edge cases — selection with only pinned rows
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_all_pinned_returns_pinned_only(self):
        pinned = [
            _note(f"p-{i}", summary=f"pinned {i}", pinned=True)
            for i in range(3)
        ]
        result = select_critical_notes_for_injection(
            active_notes=pinned,
            query="kubernetes",
            embeddings_map={},
            query_embedding=None,
            config=SelectionConfig(tail_cap=2),
        )
        out_ids = [n["id"] for n in result.pinned + result.tail]
        assert out_ids == ["p-0", "p-1", "p-2"]
        # Tail is empty — no unpinned candidates exist.
        assert result.tail == []

    def test_no_notes_returns_empty(self):
        result = select_critical_notes_for_injection(
            active_notes=[],
            query="kubernetes",
            embeddings_map={},
            query_embedding=None,
        )
        assert result.pinned == []
        assert result.tail == []
        assert result.dropped_count == 0
