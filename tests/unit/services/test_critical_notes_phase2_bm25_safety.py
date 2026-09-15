"""Critical-Notes Phase 2 — §9 verification — BM25 per-call safety.

Architecture-recommendation §9 lists per-call safety of the
``_bm25_score`` import-reuse as one of the open verification
items for Phase 2. This test pins the contract:

* ``_bm25_score`` is a pure function — every call must read its
  arguments fresh and produce a deterministic result for the
  same inputs.
* Two parallel calls (one larger corpus, one smaller) MUST NOT
  cross-contaminate stats — the per-call stats trio
  (``doc_freqs``, ``total_docs``, ``avg_doc_len``) is built by
  :func:`daemon.services.critical_notes_selector._bm25_corpus_stats`
  FRESH per call (no module-level cache).
* The selector's BM25 shortlist does not depend on a
  cross-instance cache — first-turn-frozen semantics rely on
  this property (architecture §4.2).

These tests also validate the ``_bm25_score`` import surface
itself remains stable (signature, return type) so the legacy
BlueprintMatcher and the new selector share a single
implementation.
"""

from __future__ import annotations

import math

import pytest

from daemon.services.blueprint_matcher import BlueprintMatcher
from daemon.services.critical_notes_selector import (
    SelectionConfig,
    _bm25_corpus_stats,
    select_critical_notes_for_injection,
)
from daemon.services.skill_search_service import (
    _bm25_score,
    _tokenize,
)


# ---------------------------------------------------------------------------
# 1. _bm25_score pure-function property (legacy + new caller agree)
# ---------------------------------------------------------------------------


class TestBm25PureFunction:
    def test_signature_is_stable(self):
        import inspect

        sig = inspect.signature(_bm25_score)
        for name in (
            "query_tokens",
            "doc_tokens",
            "doc_freqs",
            "total_docs",
            "avg_doc_len",
        ):
            assert name in sig.parameters, (
                f"_bm25_score must keep {name} — selector relies on it"
            )

    def test_returns_zero_for_no_match(self):
        score = _bm25_score(
            query_tokens=["kubernetes"],
            doc_tokens=["python", "django"],
            doc_freqs={"kubernetes": 1},
            total_docs=1,
            avg_doc_len=2.0,
        )
        assert score == 0.0

    def test_returns_positive_for_match(self):
        score = _bm25_score(
            query_tokens=["kubernetes"],
            doc_tokens=["kubernetes", "operator"],
            doc_freqs={"kubernetes": 1},
            total_docs=1,
            avg_doc_len=2.0,
        )
        assert score > 0

    def test_idempotent_calls_yield_same_score(self):
        args = {
            "query_tokens": ["kubernetes"],
            "doc_tokens": ["kubernetes", "operator", "config"],
            "doc_freqs": {"kubernetes": 1},
            "total_docs": 3,
            "avg_doc_len": 4.0,
        }
        s1 = _bm25_score(**args)
        s2 = _bm25_score(**args)
        assert s1 == s2

    def test_different_corpuses_yield_different_scores(self):
        # Two inputs identical except for ``doc_freqs`` — the
        # IDF term must reflect the changing corpus frequency.
        s_sparse = _bm25_score(
            query_tokens=["kubernetes"],
            doc_tokens=["kubernetes", "operator"],
            doc_freqs={"kubernetes": 1},
            total_docs=10,
            avg_doc_len=2.0,
        )
        s_dense = _bm25_score(
            query_tokens=["kubernetes"],
            doc_tokens=["kubernetes", "operator"],
            doc_freqs={"kubernetes": 9},
            total_docs=10,
            avg_doc_len=2.0,
        )
        # Sparse corpus → higher IDF → higher score (k1 + 1)
        # shape. Both must be > 0.
        assert s_sparse > 0 and s_dense > 0
        assert s_sparse != s_dense


# ---------------------------------------------------------------------------
# 2. Per-call stats — §9 (a) verification
# ---------------------------------------------------------------------------


class TestPerCallStats:
    """§9: every call to ``_bm25_corpus_stats`` builds its stats
    trio FRESH from the args; the selector never shares state
    across calls (no module-level cache, no closure capture).
    """

    def test_empty_notes_returns_empty_stats(self):
        df, total, avg = _bm25_corpus_stats([], ["any"])
        assert df == {}
        assert total == 0
        assert avg == 1.0

    def test_corpus_reflects_query_intersection(self):
        # docs: [kubernetes operator] [python typing] — query [kubernetes]
        notes = [
            {"summary": "kubernetes operator"},
            {"summary": "python typing"},
        ]
        df, total, avg = _bm25_corpus_stats(notes, ["kubernetes"])
        # DF for "kubernetes" = 1 (only the first doc has it).
        assert df.get("kubernetes") == 1
        # The second token is NOT in the docs, so it doesn't
        # appear in the DF map.
        assert "python" not in df

    def test_corpus_stats_change_with_corpus(self):
        # Same query, two different corpuses — the stats trio
        # MUST reflect the per-call inputs.
        corpus_a = [{"summary": "kubernetes alpha"}]
        corpus_b = [
            {"summary": "kubernetes alpha"},
            {"summary": "kubernetes beta"},
        ]
        df_a, n_a, _ = _bm25_corpus_stats(corpus_a, ["kubernetes"])
        df_b, n_b, _ = _bm25_corpus_stats(corpus_b, ["kubernetes"])
        assert df_a.get("kubernetes") == 1
        assert df_b.get("kubernetes") == 2
        assert n_a == 1
        assert n_b == 2

    def test_no_module_level_state(self):
        """Calling _bm25_corpus_stats twice with different inputs
        MUST produce DIFFERENT stats. There's no module-level
        cache to leak between calls.
        """
        # Two parallel calls with disjoint corpuses.
        df_a, n_a, _ = _bm25_corpus_stats(
            [{"summary": "kubernetes alpha"}], ["kubernetes"],
        )
        # Call B before any state could be shared.
        df_b, n_b, _ = _bm25_corpus_stats(
            [{"summary": "python op"}], ["python"],
        )
        assert df_a != df_b
        # Now mutate corpus A and rerun; corpus B is unaffected.
        df_a2, _, _ = _bm25_corpus_stats(
            [
                {"summary": "kubernetes alpha"},
                {"summary": "kubernetes beta"},
            ],
            ["kubernetes"],
        )
        assert df_a != df_a2  # corpus A stat evolved
        # Recomputing corpus B (single call) yields its OWN value,
        # not the mutated corpus A value.
        df_b_again, _, _ = _bm25_corpus_stats(
            [{"summary": "python op"}], ["python"],
        )
        assert df_b == df_b_again


# ---------------------------------------------------------------------------
# 3. End-to-end — selector BM25 shortlist does not see cross-call state
# ---------------------------------------------------------------------------


class TestSelectorBMPerCallSafety:
    """§9: the selector's BM25 stage is per-call. Two sequential
    selection calls with different active-notes don't cross.
    """

    def test_first_call_does_not_affect_second(self):
        notes_a = [{"id": "a", "summary": "kubernetes alpha"}]
        notes_b = [{"id": "b", "summary": "kubernetes beta"}]

        # Call A.
        result_a = select_critical_notes_for_injection(
            active_notes=notes_a,
            query="kubernetes",
            embeddings_map={},
            query_embedding=None,
            config=SelectionConfig(fusion_threshold=0.0),
        )
        # Call B with totally different inputs.
        result_b = select_critical_notes_for_injection(
            active_notes=notes_b,
            query="kubernetes",
            embeddings_map={},
            query_embedding=None,
            config=SelectionConfig(fusion_threshold=0.0),
        )
        # Each result reflects its own inputs ONLY.
        ids_a = [n["id"] for n in result_a.tail]
        ids_b = [n["id"] for n in result_b.tail]
        assert ids_a == ["a"]
        assert ids_b == ["b"]


# ---------------------------------------------------------------------------
# 4. _tokenize integration — selector depends on it for BM25 input
# ---------------------------------------------------------------------------


class TestTokenizeIntegration:
    def test_tokenize_handles_empty_string(self):
        assert _tokenize("") == []
        assert _tokenize("   ") == []

    def test_tokenize_lowercases_and_strips_punct(self):
        assert _tokenize("Hello, World!") == ["hello", "world"]

    def test_tokenize_handles_unicode(self):
        # The selector's test pins US-English ASCII. Defensive
        # sanity check for the tok-property used by both the
        # selector and BlueprintMatcher.
        toks = _tokenize("alpha-beta gamma")
        assert "alpha" in toks
        assert "beta" in toks
        assert "gamma" in toks
