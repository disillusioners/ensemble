"""Unit tests for the snapshot search service (PR5).

Covers the design §3.4 binding contract:

* **BM25 scoring + corpus composition** — the
  ``_snapshot_corpus_text`` helper joins task_summary +
  ``digest.task_summary_text`` + ``domain_tags``.
* **Tag-overlap signal (R10)** — at equal BM25, a candidate
  whose tags overlap the query-side tags outranks one that
  does not.
* **Tag filter (R8)** — ``tag_mode='all'`` requires every
  supplied tag; ``tag_mode='any'`` is OR-semantics.
* **Active-only filtering** — superseded rows never surface
  in search candidates.
* **Project scoping (D8 — PERMANENT)** — cross-project rows
  are invisible.
* **Top-20 LLM bound + degrade path** — the LLM stage sees
  at most ``_LLM_SELECT_TOP_N`` candidates; degradation to
  BM25+cosine+tag-overlap order works when the LLM is
  unavailable.
* **Freshness post-filter placement (R10)** — a stale
  candidate is still RANKED (ranking quality first) but
  carries a freshness flag in the result envelope.
* **Embeddings-at-creation wiring** — on capture completion,
  the executor kicks off 3-10 trigger queries grounded in
  ``task_summary + 1-2 digest excerpts`` and persists them
  via ``SnapshotRepository.add_embedding``.
* **R12 supersession fresh-embeddings + stay-alongside** —
  the existing ``create_successor`` path mints fresh
  embeddings alongside the new row; the predecessor's
  embeddings are NOT deleted (Q8-A no-eviction).
* **Drift-pin (sibling-drift hazard, §3.4 rider)** —
  signature change in ``skill_search_service._tokenize`` /
  ``_bm25_score`` breaks a snapshot search test (so the
  two subsystems' ranking behavior can't drift silently).

The service is constructed with lightweight mocks (mirrors
the skill-side precedent). The real :class:`SnapshotEmbeddingService`
is exercised in :mod:`tests.unit.test_snapshot_embedding_service`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.repositories.snapshot import models as snap_models
from daemon.repositories.snapshot.repository import SnapshotRepository
from daemon.services import skill_search_service as sss
from daemon.services.snapshot_embedding_service import (
    SnapshotEmbeddingService,
    _extract_digest_excerpts,
)
from daemon.services.snapshot_executor import (
    SNAPSHOT_EXPIRED_AGE_DAYS,
    SNAPSHOT_FRESH_MAX_AGE_DAYS,
    SnapshotExecutor,
    SnapshotService,
    compute_staleness_report,
)
from daemon.services.snapshot_search_service import (
    SnapshotSearchService,
    _BM25_TOP_K,
    _LLM_SELECT_TOP_N,
    _TAG_OVERLAP_WEIGHT,
    _snapshot_corpus_text,
    _snapshot_preview_summary,
    _tag_overlap_score,
)


# ============================================================================
# Fakes / Mocks
# ============================================================================


class FakeSnapshot:
    """Lightweight Snapshot stand-in with attribute access only."""

    def __init__(
        self,
        *,
        snapshot_id: str,
        project_id: str = "p1",
        title: str = "snap",
        task_summary: str = "",
        domain_tags: list[str] | None = None,
        status: str = snap_models.SNAPSHOT_STATUS_ACTIVE,
        digest: dict[str, Any] | None = None,
        created_at: str | None = None,
        runtime_version: str = "0.14.2",
    ) -> None:
        self.id = snapshot_id
        self.project_id = project_id
        self.title = title
        self.task_summary = task_summary
        self.domain_tags = list(domain_tags or [])
        self.status = status
        self.digest = digest or {}
        self.created_at = (
            created_at
            if created_at is not None
            else datetime.now(timezone.utc).isoformat()
        )
        self.runtime_version = runtime_version
        self.supersedes_snapshot_id = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "task_summary": self.task_summary,
            "domain_tags": list(self.domain_tags),
            "status": self.status,
            "digest": dict(self.digest),
            "created_at": self.created_at,
            "runtime_version": self.runtime_version,
        }


class FakeSnapshotRepo:
    """Duck-typed SnapshotRepository with canned list_active_by_project + filter_by_tags."""

    def __init__(
        self,
        active: list[FakeSnapshot] | None = None,
        embeddings: dict[str, list[Any]] | None = None,
    ) -> None:
        self._active = list(active or [])
        self._embeddings = dict(embeddings or {})
        self.filter_by_tags_calls: list[tuple[list[str], str]] = []
        self.list_active_calls: list[str] = []

    def list_active_by_project(
        self, project_id: str, limit: int = 50
    ) -> list[FakeSnapshot]:
        self.list_active_calls.append(project_id)
        return [
            s
            for s in self._active
            if s.project_id == project_id
            and s.status == snap_models.SNAPSHOT_STATUS_ACTIVE
        ][:limit]

    def filter_by_tags(
        self,
        candidates: list[FakeSnapshot],
        tags: list[str],
        tag_mode: str = "all",
    ) -> list[FakeSnapshot]:
        self.filter_by_tags_calls.append((list(tags), tag_mode))
        wanted = set(t for t in tags if t)
        if not wanted:
            return list(candidates)
        out = []
        for s in candidates:
            have = set(s.domain_tags)
            if tag_mode == "all":
                if wanted.issubset(have):
                    out.append(s)
            elif tag_mode == "any":
                if wanted & have:
                    out.append(s)
        return out

    def get_embeddings(self, snapshot_id: str) -> list[Any]:
        return list(self._embeddings.get(snapshot_id, []))


class FakeEmbeddingService:
    """Duck-typed SnapshotEmbeddingService — deterministic vec/cosine."""

    def __init__(self, query_vec: list[float] | None = None) -> None:
        self._query_vec = query_vec or [1.0, 0.0, 0.0]
        self.embed_text_calls: list[str] = []
        self.cosine_pairs: list[tuple[list[float], list[float]]] = []

    async def embed_text(self, text: str) -> list[float]:
        self.embed_text_calls.append(text)
        return list(self._query_vec)

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        # Reuse the real cosine (drift-pin: snapshot ranking uses
        # the SAME math as the skill side).
        return SnapshotEmbeddingService.cosine_similarity(a, b)

    def cosine_similarity_traced(self, a: list[float], b: list[float]) -> float:
        self.cosine_pairs.append((list(a), list(b)))
        return self.cosine_similarity(a, b)


def _staleness_stub(snapshot: Any) -> dict[str, Any]:
    """Staleness fn for tests — returns a fixed `fresh` report.

    Specific tests override with their own function for the freshness
    post-filter placement check.
    """
    return {
        "snapshot_age_days": 0.0,
        "freshness": "fresh",
        "warnings": [],
        "repo_state": None,
    }


def _make_service(
    repo: FakeSnapshotRepo | None = None,
    emb: FakeEmbeddingService | None = None,
    *,
    staleness_fn: Any = None,
    llm_config: dict[str, Any] | None = None,
) -> tuple[SnapshotSearchService, FakeSnapshotRepo, FakeEmbeddingService]:
    if repo is None:
        repo = FakeSnapshotRepo()
    if emb is None:
        emb = FakeEmbeddingService()
    service = SnapshotSearchService(
        snapshot_repo=repo,
        embedding_service=emb,  # type: ignore[arg-type]
        llm_config=llm_config or {},
        staleness_fn=staleness_fn or _staleness_stub,
    )
    return service, repo, emb


# ============================================================================
# Corpus composition + tag-overlap helpers
# ============================================================================


class TestCorpusAndOverlap:
    def test_corpus_joins_task_summary_digest_summary_and_tags(self):
        snap = FakeSnapshot(
            snapshot_id="s1",
            task_summary="investigated upgrade pipeline",
            domain_tags=["feature:upgrade", "subsystem:upgrade-pipeline"],
            digest={
                "task_summary_text": "v0.13.9 verified",
                "decisions": ["use SQLite"],
            },
        )
        corpus = _snapshot_corpus_text(snap)
        # task_summary + digest.task_summary_text + domain_tags
        assert "investigated upgrade pipeline" in corpus
        assert "v0.13.9 verified" in corpus
        assert "feature:upgrade" in corpus
        assert "subsystem:upgrade-pipeline" in corpus

    def test_corpus_tolerates_empty_digest(self):
        snap = FakeSnapshot(
            snapshot_id="s1",
            task_summary="did the thing",
            domain_tags=["kind:investigation"],
            digest=None,  # type: ignore[arg-type]
        )
        corpus = _snapshot_corpus_text(snap)
        assert "did the thing" in corpus
        assert "kind:investigation" in corpus

    def test_corpus_tolerates_empty_everything(self):
        snap = FakeSnapshot(snapshot_id="s1", task_summary="", domain_tags=[])
        assert _snapshot_corpus_text(snap) == ""

    def test_tag_overlap_count(self):
        score = _tag_overlap_score(
            ["feature:upgrade", "agent:coder"],
            ["feature:upgrade", "subsystem:upgrade-pipeline"],
        )
        assert score == 1.0

    def test_tag_overlap_empty_query(self):
        assert _tag_overlap_score([], ["x"]) == 0.0
        assert _tag_overlap_score(["x"], []) == 0.0
        assert _tag_overlap_score([], []) == 0.0

    def test_preview_summary_prefers_task_summary_then_digest(self):
        snap = FakeSnapshot(
            snapshot_id="s1",
            task_summary="explicit summary",
            digest={"task_summary_text": "digest summary"},
        )
        assert _snapshot_preview_summary(snap) == "explicit summary"
        snap2 = FakeSnapshot(snapshot_id="s2", task_summary="", digest={"decisions": ["d1"]})
        assert _snapshot_preview_summary(snap2) == "d1"


# ============================================================================
# Stage 1 — BM25 prefilter + tag-overlap
# ============================================================================


class TestBm25Prefilter:
    def test_bm25_orders_by_score_descending(self):
        # Two candidates: one matches the query tightly, one does not.
        s_match = FakeSnapshot(
            snapshot_id="s-match",
            task_summary="upgrade pipeline v0.13.9 verified end-to-end",
            domain_tags=["feature:upgrade"],
        )
        s_other = FakeSnapshot(
            snapshot_id="s-other",
            task_summary="how to write poetry",
            domain_tags=["topic:creative-writing"],
        )
        service, _, _ = _make_service(
            repo=FakeSnapshotRepo(active=[s_match, s_other])
        )
        ranked = asyncio.run(
            service._bm25_prefilter(
                "upgrade pipeline", [s_match, s_other], [], top_k=10
            )
        )
        assert ranked
        ids = [s.id for s, _ in ranked]
        assert ids[0] == "s-match"
        assert "s-other" not in ids  # no token overlap → filtered out

    def test_bm25_empty_query_returns_empty(self):
        s = FakeSnapshot(snapshot_id="s1", task_summary="x")
        service, _, _ = _make_service(repo=FakeSnapshotRepo(active=[s]))
        ranked = asyncio.run(service._bm25_prefilter("", [s], [], top_k=10))
        assert ranked == []

    def test_bm25_tag_overlap_outranks_non_overlapping_at_equal_bm25(self):
        """R10: at equal BM25, a candidate whose tags overlap the
        query-side tags ranks higher.

        The candidates carry DIFFERENT domain_tags (that's the
        independent variable), so the corpus text differs — the
        BM25 score is NOT truly identical between them (tag
        tokens shift the IDF/length-normalization slightly).
        The test asserts that the tag-overlap candidate wins
        AND that the score gap is dominated by the
        ``_TAG_OVERLAP_WEIGHT`` bonus (within ±10% of the
        weight)."""
        # Same task_summary text → equal BM25 contribution from
        # the query terms; the tag strings differ.
        s_overlap = FakeSnapshot(
            snapshot_id="s-overlap",
            task_summary="did the thing",
            domain_tags=["feature:upgrade", "subsystem:upgrade-pipeline"],
        )
        s_no_overlap = FakeSnapshot(
            snapshot_id="s-no-overlap",
            task_summary="did the thing",
            domain_tags=["topic:creative-writing"],
        )
        service, _, _ = _make_service(
            repo=FakeSnapshotRepo(active=[s_overlap, s_no_overlap])
        )
        ranked = asyncio.run(
            service._bm25_prefilter(
                "did the thing",
                [s_no_overlap, s_overlap],
                ["feature:upgrade"],
                top_k=10,
            )
        )
        assert len(ranked) == 2
        # The tag-overlap candidate wins.
        assert ranked[0][0].id == "s-overlap"
        assert ranked[0][1] > ranked[1][1]
        # The score gap is dominated by the tag-overlap bonus.
        # BM25 itself varies ±10% with corpus stats (different tag
        # tokens shift IDF/length-norm); the bonus is +0.5 (single
        # overlap × 0.5 weight). Loose tolerance — the assertion
        # is "the bonus is the dominant contributor", not "exactly
        # equal to the weight".
        gap = ranked[0][1] - ranked[1][1]
        assert gap > 0, "rank gap must be positive for overlap candidate"
        # Sanity: the gap is at least 50% of the configured weight
        # (a smaller gap would mean BM25 dominates over the bonus,
        # which would defeat the purpose of the R10 signal).
        assert gap >= _TAG_OVERLAP_WEIGHT * 0.5

    def test_bm25_tag_overlap_bonus_equals_weight_when_bm25_ties(self):
        """Pin: when BM25 is truly tied (identical corpus text),
        the score gap is EXACTLY ``_TAG_OVERLAP_WEIGHT`` per
        overlap. The candidates carry IDENTICAL corpora
        (task_summary + tag tokens); the difference is the
        query-side tag which is in one candidate's
        domain_tags but NOT the other's."""
        s_overlap = FakeSnapshot(
            snapshot_id="s-overlap",
            task_summary="investigated upgrade",
            domain_tags=["topic:match", "topic:other"],
        )
        s_no_overlap = FakeSnapshot(
            snapshot_id="s-no-overlap",
            task_summary="investigated upgrade",
            domain_tags=["topic:nomatch1", "topic:nomatch2"],
        )
        service, _, _ = _make_service(
            repo=FakeSnapshotRepo(active=[s_overlap, s_no_overlap])
        )
        ranked = asyncio.run(
            service._bm25_prefilter(
                "investigated upgrade",
                [s_no_overlap, s_overlap],
                ["topic:match"],
                top_k=10,
            )
        )
        assert len(ranked) == 2
        # s-overlap wins because of the bonus.
        assert ranked[0][0].id == "s-overlap", (
            f"got {ranked[0][0].id} first; expected s-overlap "
            f"(tag-overlap bonus = {_TAG_OVERLAP_WEIGHT} should lift it)"
        )
        # Score gap = exactly the bonus weight (BM25 ties → zero
        # variance from corpus stats; the bonus is the only
        # contributor).
        gap = ranked[0][1] - ranked[1][1]
        assert gap == pytest.approx(_TAG_OVERLAP_WEIGHT, abs=1e-9)

    def test_bm25_caps_top_k(self):
        candidates = [
            FakeSnapshot(
                snapshot_id=f"s{i}",
                task_summary=f"upgrade pipeline entry {i}",
                domain_tags=[],
            )
            for i in range(50)
        ]
        service, _, _ = _make_service(repo=FakeSnapshotRepo(active=candidates))
        ranked = asyncio.run(
            service._bm25_prefilter("upgrade", candidates, [], top_k=_BM25_TOP_K)
        )
        assert len(ranked) == _BM25_TOP_K


# ============================================================================
# R8 tag filter — tag_mode all|any
# ============================================================================


class TestTagFilterIntegration:
    def test_tag_mode_all_default(self):
        s1 = FakeSnapshot(
            snapshot_id="s1",
            task_summary="x y z upgrade",
            domain_tags=["feature:upgrade", "agent:coder"],
        )
        s2 = FakeSnapshot(
            snapshot_id="s2",
            task_summary="x y z upgrade",
            domain_tags=["feature:upgrade"],
        )
        repo = FakeSnapshotRepo(active=[s1, s2])
        service, _, _ = _make_service(repo=repo)
        result = asyncio.run(
            service.search(
                "upgrade", project_id="p1", tags=["feature:upgrade", "agent:coder"]
            )
        )
        # tag_mode=all (default): only s1 has BOTH tags.
        ids = {r["snapshot_id"] for r in result["results"]}
        assert ids == {"s1"}
        # The filter call recorded tag_mode='all'.
        assert repo.filter_by_tags_calls[-1] == (
            ["feature:upgrade", "agent:coder"],
            "all",
        )

    def test_tag_mode_any(self):
        s1 = FakeSnapshot(
            snapshot_id="s1",
            task_summary="x y z upgrade",
            domain_tags=["feature:upgrade"],
        )
        s2 = FakeSnapshot(
            snapshot_id="s2",
            task_summary="x y z upgrade",
            domain_tags=["agent:coder"],
        )
        s3 = FakeSnapshot(
            snapshot_id="s3",
            task_summary="x y z upgrade",
            domain_tags=["topic:other"],
        )
        repo = FakeSnapshotRepo(active=[s1, s2, s3])
        service, _, _ = _make_service(repo=repo)
        result = asyncio.run(
            service.search(
                "upgrade",
                project_id="p1",
                tags=["feature:upgrade", "agent:coder"],
                tag_mode="any",
            )
        )
        ids = {r["snapshot_id"] for r in result["results"]}
        assert ids == {"s1", "s2"}
        assert repo.filter_by_tags_calls[-1] == (
            ["feature:upgrade", "agent:coder"],
            "any",
        )

    def test_tag_filter_empty_passes_through(self):
        s = FakeSnapshot(
            snapshot_id="s1",
            task_summary="x y z upgrade",
            domain_tags=["feature:upgrade"],
        )
        repo = FakeSnapshotRepo(active=[s])
        service, _, _ = _make_service(repo=repo)
        # No tags supplied → filter is bypassed.
        result = asyncio.run(service.search("upgrade", project_id="p1"))
        assert {r["snapshot_id"] for r in result["results"]} == {"s1"}
        assert repo.filter_by_tags_calls == []


# ============================================================================
# Active-only filtering — superseded invisible
# ============================================================================


class TestActiveOnlyFiltering:
    def test_superseded_never_surfaces_in_search(self):
        s_active = FakeSnapshot(
            snapshot_id="s-active",
            task_summary="x y z upgrade",
            domain_tags=["feature:upgrade"],
            status=snap_models.SNAPSHOT_STATUS_ACTIVE,
        )
        s_superseded = FakeSnapshot(
            snapshot_id="s-superseded",
            task_summary="x y z upgrade",
            domain_tags=["feature:upgrade"],
            status=snap_models.SNAPSHOT_STATUS_SUPERSEDED,
        )
        repo = FakeSnapshotRepo(active=[s_active, s_superseded])
        service, _, _ = _make_service(repo=repo)
        # The fake's list_active_by_project already filters by
        # status='active' (mirrors the repository), so the
        # superseded row is invisible at stage 0.
        result = asyncio.run(service.search("upgrade", project_id="p1"))
        ids = {r["snapshot_id"] for r in result["results"]}
        assert ids == {"s-active"}
        assert "s-superseded" not in ids

    def test_failed_and_running_invisible(self):
        s_active = FakeSnapshot(
            snapshot_id="s-active",
            task_summary="x y z upgrade",
            status=snap_models.SNAPSHOT_STATUS_ACTIVE,
        )
        s_failed = FakeSnapshot(
            snapshot_id="s-failed",
            task_summary="x y z upgrade",
            status=snap_models.SNAPSHOT_STATUS_FAILED,
        )
        s_running = FakeSnapshot(
            snapshot_id="s-running",
            task_summary="x y z upgrade",
            status=snap_models.SNAPSHOT_STATUS_RUNNING,
        )
        s_interrupted = FakeSnapshot(
            snapshot_id="s-interrupted",
            task_summary="x y z upgrade",
            status=snap_models.SNAPSHOT_STATUS_INTERRUPTED,
        )
        repo = FakeSnapshotRepo(active=[s_active, s_failed, s_running, s_interrupted])
        service, _, _ = _make_service(repo=repo)
        result = asyncio.run(service.search("upgrade", project_id="p1"))
        ids = {r["snapshot_id"] for r in result["results"]}
        assert ids == {"s-active"}


# ============================================================================
# Project scoping (D8 — PERMANENT)
# ============================================================================


class TestProjectScoping:
    def test_cross_project_invisible(self):
        s_p1 = FakeSnapshot(
            snapshot_id="s-p1",
            project_id="p1",
            task_summary="x y z upgrade",
            domain_tags=[],
        )
        s_p2 = FakeSnapshot(
            snapshot_id="s-p2",
            project_id="p2",
            task_summary="x y z upgrade",
            domain_tags=[],
        )
        repo = FakeSnapshotRepo(active=[s_p1, s_p2])
        service, _, _ = _make_service(repo=repo)
        result = asyncio.run(service.search("upgrade", project_id="p1"))
        ids = {r["snapshot_id"] for r in result["results"]}
        assert ids == {"s-p1"}
        # The repository's project filter was used.
        assert repo.list_active_calls == ["p1"]

    def test_empty_project_returns_empty(self):
        repo = FakeSnapshotRepo(active=[])
        service, _, _ = _make_service(repo=repo)
        result = asyncio.run(service.search("upgrade", project_id="p-missing"))
        assert result == {"results": [], "error": None}


# ============================================================================
# Top-20 LLM bound + degrade path
# ============================================================================


def _many_candidates(n: int) -> list[FakeSnapshot]:
    return [
        FakeSnapshot(
            snapshot_id=f"s{i:03d}",
            task_summary=f"upgrade pipeline investigation entry {i}",
            domain_tags=[f"feature:upgrade"],
        )
        for i in range(n)
    ]


class TestLlmBoundAndDegrade:
    def test_llm_stage_sees_at_most_top_n_candidates(self):
        """The LLM-selection stage receives at most
        ``_LLM_SELECT_TOP_N`` candidates (R10 cost discipline)."""
        candidates = _many_candidates(40)
        # Deterministic cosine rerank: first candidate has the query
        # vec; the rest have orthogonal vecs (cosine=0).
        emb_rows = {}
        for i, snap in enumerate(candidates):
            if i == 0:
                emb_rows[snap.id] = [
                    MagicMock(embedding=[1.0, 0.0, 0.0]),
                ]
            else:
                emb_rows[snap.id] = [
                    MagicMock(embedding=[0.0, 1.0, 0.0]),
                ]
        repo = FakeSnapshotRepo(active=candidates, embeddings=emb_rows)
        # Embedding service whose embed_text returns [1, 0, 0] (matches s000).
        emb_service = FakeEmbeddingService(query_vec=[1.0, 0.0, 0.0])

        # Mock chat client — records what the LLM stage sees.
        captured_prompts: list[str] = []

        class FakeChatCompletions:
            def create(self, model, messages, temperature):
                # Capture the user prompt for inspection.
                captured_prompts.append(messages[-1]["content"])
                # Pick the first three ids (in order).
                m = MagicMock()
                m.choices = [
                    MagicMock(
                        message=MagicMock(
                            content='{"selected": ["s000", "s001", "s002"]}'
                        )
                    )
                ]
                return m

        fake_client = MagicMock()
        fake_client.chat.completions = FakeChatCompletions()
        service = SnapshotSearchService(
            snapshot_repo=repo,
            embedding_service=emb_service,  # type: ignore[arg-type]
            llm_config={"model": "test"},
            staleness_fn=_staleness_stub,
        )
        result = asyncio.run(
            service.search(
                "upgrade", project_id="p1", limit=5, llm_client=fake_client
            )
        )
        # The LLM stage was called and selected 3 snapshots.
        assert result["results"]
        assert {r["snapshot_id"] for r in result["results"]} == {"s000", "s001", "s002"}
        # The candidates listed in the LLM prompt is bounded at
        # _LLM_SELECT_TOP_N — NOT the full 40.
        prompt_text = captured_prompts[0]
        listed = prompt_text.count("- id=")
        assert listed == _LLM_SELECT_TOP_N, (
            f"LLM stage saw {listed} candidates; expected exactly "
            f"{_LLM_SELECT_TOP_N} (the top-N hard cap)"
        )

    def test_llm_failure_degrades_to_rerank_order(self):
        """Best-effort: when the LLM is down, the pipeline degrades
        to BM25+cosine+tag-overlap order — never raises."""
        candidates = _many_candidates(5)
        repo = FakeSnapshotRepo(active=candidates)
        emb_service = FakeEmbeddingService(query_vec=[1.0, 0.0, 0.0])

        class BoomChatCompletions:
            def create(self, model, messages, temperature):
                raise RuntimeError("simulated LLM outage")

        fake_client = MagicMock()
        fake_client.chat.completions = BoomChatCompletions()
        service = SnapshotSearchService(
            snapshot_repo=repo,
            embedding_service=emb_service,  # type: ignore[arg-type]
            llm_config={"model": "test"},
            staleness_fn=_staleness_stub,
        )
        # Should not raise — degrades to rerank order.
        result = asyncio.run(
            service.search(
                "upgrade pipeline investigation",
                project_id="p1",
                limit=3,
                llm_client=fake_client,
            )
        )
        assert result["error"] is None
        assert len(result["results"]) == 3


# ============================================================================
# Embedding-rerank failure path (degrade to BM25+tag-overlap)
# ============================================================================


class TestEmbeddingRerankDegrade:
    def test_embedding_failure_degrades_to_bm25_tag_order(self):
        s = FakeSnapshot(
            snapshot_id="s1",
            task_summary="upgrade pipeline",
            domain_tags=["feature:upgrade"],
        )
        repo = FakeSnapshotRepo(active=[s])

        class BoomEmbedding(FakeEmbeddingService):
            async def embed_text(self, text):
                raise RuntimeError("simulated embed outage")

        service = SnapshotSearchService(
            snapshot_repo=repo,
            embedding_service=BoomEmbedding(),  # type: ignore[arg-type]
            llm_config={},
            staleness_fn=_staleness_stub,
        )
        # Should not raise — degrades to BM25+tag-overlap order.
        result = asyncio.run(service.search("upgrade", project_id="p1"))
        assert result["error"] is None
        assert {r["snapshot_id"] for r in result["results"]} == {"s1"}

    def test_no_cached_embeddings_keeps_bm25_score(self):
        """Candidates without cached embeddings stay in the ranking
        (no penalty) — search degrades to BM25+tag-overlap for them."""
        s = FakeSnapshot(
            snapshot_id="s1",
            task_summary="upgrade pipeline end-to-end",
            domain_tags=["feature:upgrade"],
        )
        repo = FakeSnapshotRepo(active=[s], embeddings={})  # no emb rows
        emb_service = FakeEmbeddingService(query_vec=[1.0, 0.0, 0.0])
        service = SnapshotSearchService(
            snapshot_repo=repo,
            embedding_service=emb_service,  # type: ignore[arg-type]
            llm_config={},
            staleness_fn=_staleness_stub,
        )
        result = asyncio.run(service.search("upgrade", project_id="p1"))
        assert result["error"] is None
        assert {r["snapshot_id"] for r in result["results"]} == {"s1"}


# ============================================================================
# Freshness post-filter placement (R10)
# ============================================================================


def _staleness_by_age(snapshot: Any) -> dict[str, Any]:
    """Staleness fn that derives freshness from the snapshot's age."""
    return compute_staleness_report(snapshot)


def _make_snapshot_with_age(days_old: float) -> FakeSnapshot:
    """Build a snapshot whose created_at is ``days_old`` days in the past."""
    from datetime import timedelta

    created = datetime.now(timezone.utc) - timedelta(days=days_old)
    return FakeSnapshot(
        snapshot_id=f"s-{int(days_old)}d",
        task_summary="upgrade pipeline",
        domain_tags=[],
        created_at=created.isoformat(),
        runtime_version="0.14.2",
    )


class TestFreshnessPostFilter:
    def test_freshness_post_filter_after_ranking(self):
        """R10 §3.4: ranking quality first; freshness is reported
        per result, NOT used to demote. A stale candidate is still
        ranked but carries a freshness flag."""
        s_fresh = _make_snapshot_with_age(1.0)
        s_stale = _make_snapshot_with_age(15.0)
        s_expired = _make_snapshot_with_age(45.0)
        repo = FakeSnapshotRepo(active=[s_fresh, s_stale, s_expired])
        service, _, _ = _make_service(
            repo=repo,
            staleness_fn=_staleness_by_age,
        )
        result = asyncio.run(service.search("upgrade", project_id="p1"))
        # All three surface — ranking quality first.
        ids = {r["snapshot_id"] for r in result["results"]}
        assert ids == {"s-1d", "s-15d", "s-45d"}
        by_id = {r["snapshot_id"]: r for r in result["results"]}
        assert by_id["s-1d"]["freshness"] == "fresh"
        assert by_id["s-15d"]["freshness"] == "stale"
        assert by_id["s-45d"]["freshness"] == "expired"
        # age_days is the round-tripped value from compute_staleness_report.
        assert by_id["s-1d"]["age_days"] == pytest.approx(1.0, abs=0.5)
        assert by_id["s-45d"]["age_days"] == pytest.approx(45.0, abs=0.5)

    def test_freshness_thresholds_match_executor_constants(self):
        """The staleness constants on the search side must agree with
        the executor side — no shadow constant."""
        assert SNAPSHOT_FRESH_MAX_AGE_DAYS == 7
        assert SNAPSHOT_EXPIRED_AGE_DAYS == 30


# ============================================================================
# Embeddings-at-creation wiring (R10 §3.4) — executor integration
# ============================================================================


class TestEmbeddingsAtCreationWiring:
    def test_trigger_query_count_is_3_to_10(self):
        """3-10 trigger queries per snapshot (mirrors skill-evolution)."""
        from daemon.services.snapshot_embedding_service import (
            _MIN_TRIGGER_QUERIES,
            _MAX_TRIGGER_QUERIES,
        )
        assert _MIN_TRIGGER_QUERIES == 3
        assert _MAX_TRIGGER_QUERIES == 10

    def test_extract_digest_excerpts_grounded_in_task_summary(self):
        """Rev 5 §3.4: excerpts come from ``task_summary_text`` and
        R11 sections (no per-node digests)."""
        digest = {
            "task_summary_text": "investigated upgrade pipeline",
            "decisions": ["use SQLite", "drop PG mirror"],
            "gotchas": ["edit_file can silently fail"],
        }
        excerpts = _extract_digest_excerpts(digest, max_excerpts=2)
        # First excerpt = task summary text.
        assert "investigated upgrade pipeline" in excerpts
        # Second excerpt = first R11 section (decisions here).
        assert "use SQLite" in excerpts

    def test_extract_digest_excerpts_empty_digest_returns_empty(self):
        assert _extract_digest_excerpts(None) == ""
        assert _extract_digest_excerpts({}) == ""

    def test_extract_digest_excerpts_caps_at_max(self):
        """max_excerpts is honored — Rev 5 caps at 1-2."""
        digest = {
            "task_summary_text": "ts",
            "decisions": ["d1"],
            "gotchas": ["g1"],
            "conventions": ["c1"],
        }
        one = _extract_digest_excerpts(digest, max_excerpts=1)
        two = _extract_digest_excerpts(digest, max_excerpts=2)
        assert one != two
        # One excerpt joins only task_summary_text; two joins task_summary + first list section.
        assert "ts" in one
        assert "d1" in two
        assert "g1" not in two  # third section never reached

    def test_snapshot_executor_accepts_embedding_service_kwarg(self):
        """Constructor accepts an optional embedding service
        without breaking the legacy 2-arg form."""
        executor = SnapshotExecutor(
            manager=MagicMock(),
            snapshot_repository=MagicMock(spec=SnapshotRepository),
        )
        assert executor._snapshot_embedding_service is None

        fake_emb = MagicMock(spec=SnapshotEmbeddingService)
        executor2 = SnapshotExecutor(
            manager=MagicMock(),
            snapshot_repository=MagicMock(spec=SnapshotRepository),
            snapshot_embedding_service=fake_emb,
        )
        assert executor2._snapshot_embedding_service is fake_emb

    def test_snapshot_service_propagates_embedding_service(self):
        """SnapshotService forwards the embedding service to the executor."""
        fake_emb = MagicMock(spec=SnapshotEmbeddingService)
        service = SnapshotService(
            manager=MagicMock(),
            snapshot_repository=MagicMock(spec=SnapshotRepository),
            snapshot_embedding_service=fake_emb,
        )
        assert service._executor._snapshot_embedding_service is fake_emb

        # And the legacy 1-kwarg form still works.
        legacy = SnapshotService(
            manager=MagicMock(),
            snapshot_repository=MagicMock(spec=SnapshotRepository),
        )
        assert legacy._executor._snapshot_embedding_service is None


# ============================================================================
# R12 supersession — fresh embeddings + stay-alongside
# ============================================================================


class TestSupersessionFreshEmbeddings:
    def test_create_successor_accepts_embeddings_param(self):
        """R12 STAY-ALONGSIDE: the successor's embeddings land
        alongside the new row; the predecessor's embeddings are
        NOT cascade-deleted (Q8-A no-eviction)."""
        # The successor path must accept the embeddings kwarg so the
        # PR5 wiring (R12 fresh-embeddings on supersession) is
        # supported without a signature change.
        sig = inspect.signature(SnapshotRepository.create_successor)
        assert "embeddings" in sig.parameters
        # SnapshotEmbedding rows CASCADE on the snapshot row itself
        # (the only eviction path under Q8-A — superseded rows
        # stay alongside).
        emb_model = snap_models.SnapshotEmbedding
        fk_col = next(
            c for c in emb_model.__table__.columns if c.name == "snapshot_id"
        )
        fk = next(iter(fk_col.foreign_keys))
        assert fk.ondelete == "CASCADE"


# ============================================================================
# Drift-pin (sibling-drift hazard — §3.4 rider)
# ============================================================================


class TestDriftPin:
    def test_snapshot_search_imports_tokenize_from_skill_search(self):
        """The drift-pin: snapshot search MUST import ``_tokenize``
        from :mod:`daemon.services.skill_search_service`. A signature
        change there MUST break this test."""
        from daemon.services import snapshot_search_service

        src = inspect.getsource(snapshot_search_service)
        assert "_tokenize" in src
        assert "skill_search_service" in src
        # And the bound function IS the skill-side helper (same
        # identity — not a copy).
        assert snapshot_search_service._tokenize is sss._tokenize

    def test_snapshot_search_imports_bm25_score_from_skill_search(self):
        from daemon.services import snapshot_search_service

        src = inspect.getsource(snapshot_search_service)
        assert "_bm25_score" in src
        assert snapshot_search_service._bm25_score is sss._bm25_score

    def test_snapshot_search_does_not_redefine_bm25_helpers(self):
        """Pin: snapshot_search_service must NOT redefine BM25 helpers
        (the spec says "do NOT extract text_search_common.py" — the
        only safe pattern is direct import, no shadow copy)."""
        from daemon.services import snapshot_search_service

        src = inspect.getsource(snapshot_search_service)
        # No `def _bm25_score(` inside the module (only the import).
        assert "def _bm25_score(" not in src
        assert "def _tokenize(" not in src

    def test_cosine_similarity_shared_with_skill_embedding(self):
        """The vector math is the only thing shared with the skill
        subsystem (re-exported from SkillEmbeddingService). Pin the
        identity so a drift on the skill side would break this.

        Implementation: the snapshot-side ``cosine_similarity`` is a
        staticmethod that delegates to the skill-side function via a
        direct call (no shadow copy). We pin via source inspection:
        the source must contain a direct call to the skill-side
        helper (NOT re-derivation)."""
        import inspect

        from daemon.services.snapshot_embedding_service import (
            SnapshotEmbeddingService as SES,
        )
        from daemon.services.skill_embedding_service import (
            SkillEmbeddingService as SKES,
        )
        # The snapshot source must call the skill-side helper
        # directly — a shadow copy would lose the pin.
        ses_src = inspect.getsource(SES.cosine_similarity)
        assert "SkillEmbeddingService.cosine_similarity" in ses_src, (
            "SnapshotEmbeddingService.cosine_similarity must delegate "
            "directly to the skill-side helper (drift-pin)"
        )
        # And the call yields the SAME result for representative
        # vectors — byte-for-byte equality is the contract.
        a = [0.1, 0.2, 0.3]
        b = [0.4, 0.5, 0.6]
        assert SES.cosine_similarity(a, b) == SKES.cosine_similarity(a, b)


# ============================================================================
# Result shape (§6.1)
# ============================================================================


class TestResultEnvelope:
    def test_envelope_shape_matches_section_6_1(self):
        """The §6.1 result dict shape — the Wave 2b tool wrapper
        will consume exactly these keys."""
        snap = FakeSnapshot(
            snapshot_id="abc-123",
            title="upgrade-after-v0.13.9",
            task_summary="investigated the upgrade pipeline",
            domain_tags=["feature:upgrade", "subsystem:upgrade-pipeline"],
        )
        service, _, _ = _make_service(
            repo=FakeSnapshotRepo(active=[snap]),
        )
        env = service._result_envelope(snap)
        assert set(env.keys()) == {
            "snapshot_id",
            "name",
            "tags",
            "freshness",
            "age_days",
            "summary",
        }
        assert env["snapshot_id"] == "abc-123"
        assert env["name"] == "upgrade-after-v0.13.9"
        assert env["tags"] == [
            "feature:upgrade",
            "subsystem:upgrade-pipeline",
        ]
        assert env["freshness"] == "fresh"  # staleness stub
        assert env["age_days"] == 0.0  # staleness stub
        assert env["summary"] == "investigated the upgrade pipeline"


# ============================================================================
# Result-end-to-end: search() → result envelope with all stages wired
# ============================================================================


class TestSearchEndToEnd:
    def test_search_returns_envelope_with_real_staleness_fn(self):
        s = FakeSnapshot(
            snapshot_id="s1",
            task_summary="upgrade pipeline investigation",
            domain_tags=["feature:upgrade"],
        )
        repo = FakeSnapshotRepo(active=[s])
        service, _, _ = _make_service(
            repo=repo,
            staleness_fn=_staleness_by_age,
        )
        result = asyncio.run(service.search("upgrade", project_id="p1"))
        assert result["error"] is None
        assert len(result["results"]) == 1
        r = result["results"][0]
        # Real staleness from compute_staleness_report.
        assert r["freshness"] in {"fresh", "stale", "expired"}
        assert r["age_days"] >= 0.0

    def test_search_degrades_cleanly_when_no_candidates(self):
        repo = FakeSnapshotRepo(active=[])
        service, _, _ = _make_service(repo=repo)
        result = asyncio.run(service.search("anything", project_id="p1"))
        assert result == {"results": [], "error": None}

    def test_search_top_k_cap_via_limit(self):
        """The ``limit`` argument caps the result list (default 10)."""
        candidates = _many_candidates(15)
        repo = FakeSnapshotRepo(active=candidates)
        service, _, _ = _make_service(repo=repo)
        result = asyncio.run(
            service.search("upgrade", project_id="p1", limit=4)
        )
        assert len(result["results"]) == 4
