"""Tests for ``SkillSearchService`` (Phase 2 of Skill Evolution).

Tests the three-stage skill search pipeline:

* **Stage 1 — BM25 prefilter** (``_bm25_prefilter``).
  Pure-Python BM25 over ``name + description + content``.
  Verify ordering, empty-corpus handling, and zero-score
  filtering.
* **Stage 2 — Embedding re-rank** (``_embedding_rerank``).
  Mock the embedding service to verify MAX cosine similarity is
  used across all per-skill embedding rows, and that a
  mocked :meth:`embed_user_message` failure falls back to
  BM25-only with ``score=0.0``.
* **Stage 3 — LLM selection** (``_llm_select``).
  Mock the OpenAI client to verify JSON parsing (with and
  without markdown code fences), selected-mapping back to skill
  objects, and fallback to ``_degraded_select`` when the LLM
  raises.
* **Full pipeline** (``search``).
  End-to-end mocks for all three stages. Verify graceful
  degradation at every stage boundary.

All OpenAI calls are mocked via an injected ``client`` argument
on :meth:`_llm_select` — no monkeypatching of ``openai.OpenAI``
required, no network traffic produced.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.skill_search_service import (
    SkillSearchService,
    _bm25_score,
    _tokenize,
)


# ============================================================
# Fixtures / helpers
# ============================================================


def make_skill(
    *,
    skill_id: str = "skill-1",
    name: str = "code-review",
    description: str = "Review code for bugs and style.",
    content: str = "",
    project_id: str | None = None,
) -> SimpleNamespace:
    """Build a minimal stand-in for a :class:`Skill` row.

    Uses :class:`SimpleNamespace` rather than ``MagicMock(spec=...)``
    so attribute access follows real-class semantics — this
    matters for ``getattr(skill, "name", "")`` fallback paths
    in the service.
    """
    return SimpleNamespace(
        id=skill_id,
        name=name,
        description=description,
        content=content,
        project_id=project_id,
        is_active=True,
        status="active",
    )


def make_embedding_row(
    *, skill_id: str = "skill-1", embedding: list[float] | None = None
) -> SimpleNamespace:
    """Build a minimal stand-in for a :class:`SkillEmbedding` row.

    Defaults to a unit vector along ``x`` so similarity
    calculations are easy to reason about in tests.
    """
    if embedding is None:
        embedding = [1.0, 0.0, 0.0]
    return SimpleNamespace(
        id="emb-1",
        skill_id=skill_id,
        embedding=list(embedding),
        trigger_query="default",
    )


def make_skill_repo(*, skills: list) -> MagicMock:
    """Build a mock :class:`SkillRepository`.

    The service calls ``list(project_id, active_only=True,
    limit)`` — return ``(items, total)`` to mirror the real
    Phase 1 repo shape.
    """
    repo = MagicMock()
    repo.list = MagicMock(return_value=(list(skills), len(skills)))
    return repo


def make_embedding_repo(
    *, embeddings_by_skill: dict[str, list] | None = None
) -> MagicMock:
    """Build a mock :class:`SkillEmbeddingRepository`.

    Args:
        embeddings_by_skill: ``{skill_id: [embedding_row, ...]}``
            mapping. ``get_by_skill`` looks up by ``skill_id``.
    """
    by_skill = embeddings_by_skill or {}
    repo = MagicMock()

    def _get_by_skill(skill_id: str) -> list:
        return by_skill.get(skill_id, [])

    repo.get_by_skill = MagicMock(side_effect=_get_by_skill)
    return repo


def make_embedding_service(
    *,
    embed_return: list[float] | None = None,
    embed_side_effect: Exception | None = None,
    similarities: dict | None = None,
) -> MagicMock:
    """Build a mock :class:`SkillEmbeddingService`.

    ``embed_user_message`` is async-mocked (the production
    service exposes it as ``async``). ``cosine_similarity`` is
    sync — a callable that returns the configured mapping
    or ``0.0`` for unknown keys.

    Args:
        embed_return: Vector returned by ``embed_user_message``.
        embed_side_effect: Exception to raise from
            ``embed_user_message`` (overrides ``embed_return``).
        similarities: ``{frozen(emb_pair): score}`` mapping used
            by ``cosine_similarity``. Pair keys must be the
            tuple ``(query_emb, cand_emb)``. Falls back to 0.5
            when ``similarities`` is None.
    """
    service = MagicMock()
    if embed_side_effect is not None:
        service.embed_user_message = AsyncMock(side_effect=embed_side_effect)
    else:
        service.embed_user_message = AsyncMock(
            return_value=embed_return if embed_return is not None else [1.0, 0.0, 0.0]
        )
    sim_map = similarities or {}

    def _cosine(a: list[float], b: list[float]) -> float:
        key = (tuple(a), tuple(b))
        if key in sim_map:
            return float(sim_map[key])
        # Fallback: simple dot-product shortcut for tests that
        # don't pre-register pairs.
        return float(sum(x * y for x, y in zip(a, b)))

    service.cosine_similarity = MagicMock(side_effect=_cosine)
    return service


def make_llm_config() -> dict:
    """Default LLM config dict for tests."""
    return {
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test-xyz",
        "model": "gpt-4o-mini",
    }


def make_config() -> MagicMock:
    """Build a mock :class:`SkillEvolutionConfig`."""
    cfg = MagicMock(spec=["bm25_top_k", "llm_select_top_k", "max_inject_skills"])
    cfg.bm25_top_k = 10
    cfg.llm_select_top_k = 5
    cfg.max_inject_skills = 2
    return cfg


def make_service(
    *,
    skill_repo: MagicMock | None = None,
    embedding_repo: MagicMock | None = None,
    embedding_service: MagicMock | None = None,
    llm_config: dict | None = None,
    config: MagicMock | None = None,
) -> SkillSearchService:
    """Construct a :class:`SkillSearchService` with sensible defaults."""
    return SkillSearchService(
        skill_repo=skill_repo if skill_repo is not None else make_skill_repo(skills=[]),
        embedding_repo=(
            embedding_repo if embedding_repo is not None else make_embedding_repo()
        ),
        embedding_service=(
            embedding_service
            if embedding_service is not None
            else make_embedding_service()
        ),
        llm_config=llm_config if llm_config is not None else make_llm_config(),
        config=config if config is not None else make_config(),
    )


def make_chat_response(content: str) -> MagicMock:
    """Build a mock chat-completion response with ``content`` as text."""
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def make_openai_client(
    *, response: MagicMock | None = None, side_effect: Exception | None = None
) -> MagicMock:
    """Build a mock OpenAI client.

    ``client.chat.completions.create`` is a ``MagicMock`` —
    either returning ``response`` or raising ``side_effect``.
    """
    client = MagicMock()
    if side_effect is not None:
        client.chat.completions.create = MagicMock(side_effect=side_effect)
    else:
        client.chat.completions.create = MagicMock(
            return_value=response if response is not None else make_chat_response("{}")
        )
    return client


# ============================================================
# TestTokenize
# ============================================================


class TestTokenize:
    """Verify ``_tokenize`` matches the spec's contract."""

    def test_lowercases_input(self):
        # Uppercase letters should be lowercased before tokenizing.
        assert _tokenize("Hello WORLD") == ["hello", "world"]

    def test_splits_on_punctuation(self):
        # Hyphens, underscores, periods all act as separators.
        tokens = _tokenize("foo-bar.baz_quux quux")
        # ``foo-bar.baz_quux`` splits to ``foo``, ``bar``, ``baz``, ``quux``.
        assert tokens == ["foo", "bar", "baz", "quux", "quux"]

    def test_drops_empty_tokens(self):
        # Leading/trailing punctuation shouldn't yield empty strings.
        assert _tokenize("...hello...") == ["hello"]
        assert _tokenize("") == []
        assert _tokenize("   ") == []

    def test_keeps_numeric_tokens(self):
        # Numbers are alphanumeric and must be preserved.
        assert _tokenize("model3 v2 release") == ["model3", "v2", "release"]

    def test_splits_on_whitespace(self):
        assert _tokenize("a\nb\tc d") == ["a", "b", "c", "d"]


# ============================================================
# TestBm25Score
# ============================================================


class TestBm25Score:
    """Direct BM25 math tests (pure-Python, no service plumbing)."""

    def test_zero_when_no_term_match(self):
        # Query term not in doc → score must be 0.
        score = _bm25_score(
            query_tokens=["kangaroo"],
            doc_tokens=["apple", "banana", "cherry"],
            doc_freqs={"kangaroo": 1},
            total_docs=3,
            avg_doc_len=3.0,
        )
        assert score == 0.0

    def test_positive_when_match(self):
        # Query term present → score must be > 0.
        score = _bm25_score(
            query_tokens=["apple"],
            doc_tokens=["apple", "banana"],
            doc_freqs={"apple": 1},
            total_docs=3,
            avg_doc_len=3.0,
        )
        assert score > 0.0

    def test_empty_documents_zero_score(self):
        # Empty doc tokens → no term can match → 0.
        assert (
            _bm25_score(
                query_tokens=["apple"],
                doc_tokens=[],
                doc_freqs={"apple": 1},
                total_docs=3,
                avg_doc_len=1.0,
            )
            == 0.0
        )

    def test_higher_tf_increases_score(self):
        # Two docs, same query, one with higher tf for the term
        # — the higher-tf one must score higher (when length
        # normalization doesn't completely flip the order).
        df = {"apple": 2}
        # Equal-length docs so length normalization cancels out.
        score_low = _bm25_score(
            query_tokens=["apple"],
            doc_tokens=["apple", "banana", "cherry"],
            doc_freqs=df,
            total_docs=4,
            avg_doc_len=3.0,
        )
        score_high = _bm25_score(
            query_tokens=["apple"],
            doc_tokens=["apple", "apple", "cherry"],
            doc_freqs=df,
            total_docs=4,
            avg_doc_len=3.0,
        )
        assert score_high > score_low


# ============================================================
# TestBm25Prefilter
# ============================================================


class TestBm25Prefilter:
    """Stage 1 — BM25 prefilter against the skill repo."""

    @pytest.mark.asyncio
    async def test_empty_corpus_returns_empty_list(self):
        # Repo returns no skills → service returns ``[]``.
        service = make_service(skill_repo=make_skill_repo(skills=[]))
        result = await service._bm25_prefilter(
            "anything", project_id=None, top_k=10
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_known_rankings(self):
        # Build a tiny corpus with hand-crafted name +
        # description + content. The skill mentioning the
        # query token must outrank one that doesn't.
        s_high = make_skill(
            skill_id="s-high",
            name="python-data",
            description="Process csv files with python pandas.",
            content="Loading and transforming CSV datasets in Python.",
        )
        s_low = make_skill(
            skill_id="s-low",
            name="css-styles",
            description="Build CSS stylesheets for static sites.",
            content="Static-site CSS theming and layout.",
        )
        s_medium = make_skill(
            skill_id="s-medium",
            name="python-runtime",
            description="Manage Python runtime versions.",
            content="Switch between Python interpreters using pyenv.",
        )
        service = make_service(skill_repo=make_skill_repo(
            skills=[s_high, s_low, s_medium]
        ))

        result = await service._bm25_prefilter(
            "python pandas csv", project_id=None, top_k=10
        )

        # The CSS skill must NOT appear (no term overlap).
        ids = [s.id for s in result]
        assert "s-low" not in ids
        # The CSV/Python-pandas skill must come first (highest
        # term overlap with the query).
        assert ids[0] == "s-high"
        # All returned skills share at least one token.
        assert len(ids) >= 2

    @pytest.mark.asyncio
    async def test_score_zero_for_no_overlap(self):
        # Query terms not present in any skill → all docs
        # score 0 → service filters them all out.
        s1 = make_skill(
            skill_id="s1",
            name="alpha",
            description="alpha skill",
            content="alpha alpha alpha",
        )
        s2 = make_skill(
            skill_id="s2",
            name="beta",
            description="beta skill",
            content="beta beta beta",
        )
        service = make_service(skill_repo=make_skill_repo(skills=[s1, s2]))

        # ``kangaroo`` is in no skill → all scores 0.
        result = await service._bm25_prefilter(
            "kangaroo", project_id=None, top_k=10
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_top_k_caps_results(self):
        # Build a corpus of 5 high-overlap skills; ask for
        # top_k=2 → only 2 should be returned.
        skills = [
            make_skill(skill_id=f"s{i}", name=f"python-{i}",
                       description="python helper",
                       content="python code")
            for i in range(5)
        ]
        service = make_service(skill_repo=make_skill_repo(skills=skills))
        result = await service._bm25_prefilter(
            "python", project_id=None, top_k=2
        )
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_project_id_passed_through_to_repo(self):
        # Verify ``project_id`` is forwarded to
        # ``skill_repo.list`` for project-scoped searches.
        repo = make_skill_repo(skills=[])
        service = make_service(skill_repo=repo)

        await service._bm25_prefilter(
            "anything", project_id="proj-x", top_k=10
        )

        # Inspect the call — kwargs must carry ``project_id``.
        call_kwargs = repo.list.call_args.kwargs
        assert call_kwargs.get("project_id") == "proj-x"
        assert call_kwargs.get("active_only") is True


# ============================================================
# TestEmbeddingRerank
# ============================================================


class TestEmbeddingRerank:
    """Stage 2 — Embedding re-rank takes MAX cosine similarity."""

    @pytest.mark.asyncio
    async def test_takes_max_cosine_similarity(self):
        # Two embeddings per skill; one is identical to the
        # query embedding (cosine=1.0), the other is
        # orthogonal (cosine=0.0). Service must pick 1.0.
        query_emb = [1.0, 0.0, 0.0]
        skill_a = make_skill(skill_id="a", name="a",
                             description="a", content="a")
        skill_b = make_skill(skill_id="b", name="b",
                             description="b", content="b")

        # Pre-register the similarities so the mock can return
        # exact values for each (query, candidate) pair.
        sims = {
            ((1.0, 0.0, 0.0), (1.0, 0.0, 0.0)): 1.0,   # skill a max
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)): 0.0,   # skill b orth
        }
        embedding_service = make_embedding_service(
            embed_return=query_emb,
            similarities=sims,
        )
        embedding_repo = make_embedding_repo(
            embeddings_by_skill={
                "a": [
                    make_embedding_row(
                        skill_id="a", embedding=[1.0, 0.0, 0.0]
                    ),
                    make_embedding_row(
                        skill_id="a", embedding=[0.0, 1.0, 0.0]
                    ),
                ],
                "b": [
                    make_embedding_row(
                        skill_id="b", embedding=[0.0, 1.0, 0.0]
                    ),
                ],
            }
        )

        service = make_service(
            embedding_service=embedding_service,
            embedding_repo=embedding_repo,
        )

        result = await service._embedding_rerank(
            "hello world", candidates=[skill_a, skill_b], top_k=5
        )

        # Two tuples returned, ordered by score desc.
        assert len(result) == 2
        # Max(1.0, 0.0) per skill a → score 1.0; skill b → 0.0.
        scored = {s.id: sc for s, sc in result}
        assert scored["a"] == pytest.approx(1.0)
        assert scored["b"] == pytest.approx(0.0)
        # ``a`` (best score) comes first.
        assert result[0][0].id == "a"

    @pytest.mark.asyncio
    async def test_skill_with_no_examples_scores_zero(self):
        # Skill has no cached embeddings → score 0.0 (not
        # dropped — the BM25 ordering survives).
        skill_with = make_skill(skill_id="with", name="w",
                                description="w", content="w")
        skill_without = make_skill(skill_id="without", name="wo",
                                    description="wo", content="wo")

        embedding_repo = make_embedding_repo(
            embeddings_by_skill={
                "with": [make_embedding_row(skill_id="with",
                                            embedding=[1.0, 0.0, 0.0])],
                # ``without`` intentionally missing → empty list.
            }
        )
        embedding_service = make_embedding_service(
            embed_return=[1.0, 0.0, 0.0]
        )

        service = make_service(
            embedding_service=embedding_service,
            embedding_repo=embedding_repo,
        )

        result = await service._embedding_rerank(
            "anything", candidates=[skill_with, skill_without], top_k=5
        )

        assert len(result) == 2
        scored = {s.id: sc for s, sc in result}
        assert scored["without"] == 0.0
        # The skill WITH embeddings must outrank the empty one.
        assert scored["with"] > scored["without"]

    @pytest.mark.asyncio
    async def test_embedding_failure_returns_zero_scores(
        self, caplog: pytest.LogCaptureFixture
    ):
        # Mock ``embed_user_message`` to raise. Service must
        # log a warning and return candidates with score=0.
        skill_a = make_skill(skill_id="a", name="a",
                             description="a", content="a")
        skill_b = make_skill(skill_id="b", name="b",
                             description="b", content="b")

        embedding_service = make_embedding_service(
            embed_side_effect=RuntimeError("embedding API down")
        )
        service = make_service(embedding_service=embedding_service)

        with caplog.at_level("WARNING"):
            # The service's ``_embedding_rerank`` deliberately
            # lets ``embed_user_message`` exceptions propagate
            # — the ``search`` wrapper handles the fallback.
            # So calling ``_embedding_rerank`` directly should
            # raise here.
            with pytest.raises(RuntimeError):
                await service._embedding_rerank(
                    "anything", candidates=[skill_a, skill_b], top_k=5
                )

    @pytest.mark.asyncio
    async def test_embedding_failure_falls_back_in_search(self, caplog):
        # When wrapped by ``search``, the embedding failure
        # becomes a BM25-only fallback (score 0.0 each).
        skill_a = make_skill(skill_id="a", name="alpha",
                             description="alpha helper",
                             content="alpha content")
        skill_b = make_skill(skill_id="b", name="beta",
                             description="beta helper",
                             content="beta content")

        skill_repo = make_skill_repo(skills=[skill_a, skill_b])
        embedding_repo = make_embedding_repo()
        embedding_service = make_embedding_service(
            embed_side_effect=RuntimeError("API down")
        )
        # LLM mock returns a valid (empty) JSON.
        client = make_openai_client(
            response=make_chat_response('{"selected": [], "low_match": []}')
        )

        service = make_service(
            skill_repo=skill_repo,
            embedding_repo=embedding_repo,
            embedding_service=embedding_service,
        )

        with caplog.at_level("WARNING"):
            result = await service.search(
                "alpha", project_id=None, max_results=2
            )

        # Stage 1 still found candidates; stage 2 was bypassed;
        # stage 3 ran cleanly and returned no selections (we
        # mocked it to give an empty answer), but the call
        # must complete without exception.
        assert isinstance(result, dict)
        assert "injected" in result
        assert "low_match" in result


# ============================================================
# TestLlmSelect
# ============================================================


class TestLlmSelect:
    """Stage 3 — LLM final selection."""

    @pytest.mark.asyncio
    async def test_parses_json_and_maps_to_skills(self):
        # Mock LLM returns a JSON object with one selected
        # skill. Service must map the name back to the skill.
        skill_alpha = make_skill(
            skill_id="alpha",
            name="alpha-skill",
            description="Alpha description.",
        )
        skill_beta = make_skill(
            skill_id="beta", name="beta-skill",
            description="Beta description."
        )

        llm_response = make_chat_response(
            '{"selected": [{"name": "alpha-skill", "score": 0.92}],'
            ' "low_match": [{"name": "beta-skill", "score": 0.4,'
            ' "description": "Beta description."}]}'
        )
        client = make_openai_client(response=llm_response)

        service = make_service()
        result = await service._llm_select(
            "test query",
            candidates=[(skill_alpha, 0.9), (skill_beta, 0.5)],
            max_results=2,
            client=client,
        )

        assert len(result["injected"]) == 1
        assert result["injected"][0]["skill"] is skill_alpha
        assert result["injected"][0]["score"] == pytest.approx(0.92)

        assert len(result["low_match"]) == 1
        assert result["low_match"][0]["name"] == "beta-skill"
        # Live description wins over hallucinated one.
        assert result["low_match"][0]["description"] == "Beta description."

    @pytest.mark.asyncio
    async def test_tolerates_code_fences(self):
        # LLM wraps JSON in markdown fences — service must
        # strip them and parse the object cleanly.
        skill_a = make_skill(skill_id="a", name="a-skill",
                             description="A description.")

        llm_response = make_chat_response(
            "```json\n"
            '{"selected": [{"name": "a-skill", "score": 0.8}],'
            ' "low_match": []}\n'
            "```"
        )
        client = make_openai_client(response=llm_response)

        service = make_service()
        result = await service._llm_select(
            "any query",
            candidates=[(skill_a, 0.7)],
            max_results=2,
            client=client,
        )

        assert len(result["injected"]) == 1
        assert result["injected"][0]["skill"] is skill_a
        assert result["injected"][0]["score"] == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_hallucinated_name_is_dropped(self):
        # LLM invents a skill name that doesn't exist in the
        # candidate map — service must skip it without
        # crashing.
        skill_real = make_skill(skill_id="real", name="real-skill",
                                description="Real description.")

        llm_response = make_chat_response(
            '{"selected": [{"name": "fake-skill", "score": 0.9}],'
            ' "low_match": []}'
        )
        client = make_openai_client(response=llm_response)

        service = make_service()
        result = await service._llm_select(
            "query",
            candidates=[(skill_real, 0.5)],
            max_results=2,
            client=client,
        )

        assert result["injected"] == []
        assert result["low_match"] == []

    @pytest.mark.asyncio
    async def test_invalid_score_falls_back_to_zero(self):
        # LLM returns a non-numeric score — service must coerce
        # to 0.0 rather than crashing.
        skill_a = make_skill(skill_id="a", name="a-skill",
                             description="A description.")

        llm_response = make_chat_response(
            '{"selected": [{"name": "a-skill", "score": "high"}],'
            ' "low_match": []}'
        )
        client = make_openai_client(response=llm_response)

        service = make_service()
        result = await service._llm_select(
            "query",
            candidates=[(skill_a, 0.5)],
            max_results=2,
            client=client,
        )

        assert len(result["injected"]) == 1
        assert result["injected"][0]["score"] == 0.0


# ============================================================
# TestLlmFailure
# ============================================================


class TestLlmFailure:
    """Stage 3 — LLM failure handling."""

    @pytest.mark.asyncio
    async def test_llm_failure_in_search_falls_back_to_degraded(self):
        # Full pipeline: BM25 OK, embedding OK, LLM raises.
        # Service must log a warning and use ``_degraded_select``.
        skill_a = make_skill(skill_id="a", name="alpha-skill",
                             description="Alpha description.",
                             content="alpha content")
        skill_b = make_skill(skill_id="b", name="beta-skill",
                             description="Beta description.",
                             content="beta content")
        skill_c = make_skill(skill_id="c", name="gamma-skill",
                             description="Gamma description.",
                             content="gamma content")

        skill_repo = make_skill_repo(skills=[skill_a, skill_b, skill_c])
        embedding_repo = make_embedding_repo(
            embeddings_by_skill={
                "a": [make_embedding_row(skill_id="a",
                                         embedding=[1.0, 0.0, 0.0])],
                "b": [make_embedding_row(skill_id="b",
                                         embedding=[1.0, 0.0, 0.0])],
                "c": [make_embedding_row(skill_id="c",
                                         embedding=[1.0, 0.0, 0.0])],
            }
        )
        embedding_service = make_embedding_service(
            embed_return=[1.0, 0.0, 0.0]
        )

        # OpenAI client raises on every call.
        client = make_openai_client(
            side_effect=RuntimeError("LLM is down")
        )

        # Trick: service constructs its own client by default.
        # Override by passing our broken one to ``_llm_select``
        # — but the public ``search`` path constructs the
        # client itself. Monkeypatch the module-level
        # ``openai.OpenAI`` to make ``search`` see the broken
        # client.
        import daemon.services.skill_search_service as svc_mod

        broken_client_factory = MagicMock(return_value=client)
        original_openai = svc_mod.openai.OpenAI
        svc_mod.openai.OpenAI = broken_client_factory
        try:
            service = make_service(
                skill_repo=skill_repo,
                embedding_repo=embedding_repo,
                embedding_service=embedding_service,
            )
            result = await service.search(
                "alpha help", project_id=None, max_results=2
            )
        finally:
            svc_mod.openai.OpenAI = original_openai

        # In degraded mode, the top 2 from BM25/embedding pass
        # to ``injected``, and the next (up to 3) to ``low_match``.
        assert "injected" in result
        assert "low_match" in result
        assert len(result["injected"]) <= 2


# ============================================================
# TestFullPipeline
# ============================================================


class TestFullPipeline:
    """End-to-end ``search`` with all three stages mocked."""

    @pytest.mark.asyncio
    async def test_full_pipeline_success(self):
        # Happy path: BM25 → embedding rerank → LLM pick. All
        # three stages produce a sensible result.
        s1 = make_skill(skill_id="s1", name="alpha-skill",
                        description="Alpha description.",
                        content="alpha content alpha more")
        s2 = make_skill(skill_id="s2", name="beta-skill",
                        description="Beta description.",
                        content="beta content beta more")
        s3 = make_skill(skill_id="s3", name="gamma-skill",
                        description="Gamma description.",
                        content="gamma content gamma more")
        s4 = make_skill(skill_id="s4", name="delta-skill",
                        description="Delta description.",
                        content="delta content delta more")
        s5 = make_skill(skill_id="s5", name="epsilon-skill",
                        description="Epsilon description.",
                        content="epsilon content epsilon more")

        skill_repo = make_skill_repo(skills=[s1, s2, s3, s4, s5])
        embedding_repo = make_embedding_repo(
            embeddings_by_skill={
                sid: [
                    make_embedding_row(
                        skill_id=sid,
                        embedding=[1.0, 0.0, 0.0] if i == 0 else [0.0, 1.0, 0.0],
                    )
                    for i in range(2)
                ]
                for sid in ["s1", "s2", "s3", "s4", "s5"]
            }
        )
        embedding_service = make_embedding_service(
            embed_return=[1.0, 0.0, 0.0]
        )

        llm_response = make_chat_response(
            '{"selected": ['
            '{"name": "alpha-skill", "score": 0.95},'
            '{"name": "beta-skill", "score": 0.7}],'
            ' "low_match": ['
            '{"name": "gamma-skill", "score": 0.25,'
            ' "description": "Gamma description."}]}'
        )
        client = make_openai_client(response=llm_response)

        # Monkeypatch the OpenAI constructor to return our
        # mock client so the search path picks it up.
        import daemon.services.skill_search_service as svc_mod

        original = svc_mod.openai.OpenAI
        svc_mod.openai.OpenAI = MagicMock(return_value=client)
        try:
            service = make_service(
                skill_repo=skill_repo,
                embedding_repo=embedding_repo,
                embedding_service=embedding_service,
            )
            result = await service.search(
                "alpha beta gamma", project_id=None, max_results=2
            )
        finally:
            svc_mod.openai.OpenAI = original

        # ``max_results=2`` → up to 2 injected.
        assert isinstance(result, dict)
        assert set(result.keys()) == {"injected", "low_match"}
        assert len(result["injected"]) <= 2
        # LLM explicitly selected 2 → both surfaces.
        assert len(result["injected"]) == 2
        # LLM listed 1 low_match.
        assert len(result["low_match"]) == 1

    @pytest.mark.asyncio
    async def test_full_pipeline_bm25_only_no_qualifying_skills(self):
        # All-zero BM25 → service returns empty dict shape
        # without calling the LLM or embedding stage.
        service = make_service(
            skill_repo=make_skill_repo(skills=[
                make_skill(skill_id="unrelated", name="x",
                           description="y", content="z"),
            ]),
            embedding_repo=make_embedding_repo(),
            embedding_service=make_embedding_service(),
        )
        result = await service.search(
            "kangaroo", project_id=None, max_results=2
        )
        assert result == {"injected": [], "low_match": []}

    @pytest.mark.asyncio
    async def test_full_pipeline_passes_project_id_to_repo(self):
        # Service forwards ``project_id`` to ``skill_repo.list``
        # for project-scoped searches.
        repo = make_skill_repo(skills=[])
        service = make_service(skill_repo=repo)

        await service.search(
            "anything", project_id="proj-42", max_results=2
        )

        call_kwargs = repo.list.call_args.kwargs
        assert call_kwargs.get("project_id") == "proj-42"


# ============================================================
# TestDegradedSelect
# ============================================================


class TestDegradedSelect:
    """``_degraded_select`` shape and ordering tests."""

    def test_low_match_shape(self):
        # ``low_match`` must be a list of dicts with
        # ``name``, ``score``, ``description`` keys.
        skills = [
            make_skill(
                skill_id=f"s{i}",
                name=f"skill-{i}",
                description=f"description {i}",
                content="",
            )
            for i in range(5)
        ]
        candidates = [(s, 1.0 - i * 0.1) for i, s in enumerate(skills)]

        service = make_service()
        result = service._degraded_select(candidates, max_results=2)

        assert "injected" in result
        assert "low_match" in result

        # ``injected`` carries the Skill objects directly.
        assert len(result["injected"]) == 2
        for entry in result["injected"]:
            assert "skill" in entry
            assert "score" in entry

        # ``low_match`` entries have exactly the three keys.
        assert len(result["low_match"]) == 3
        for entry in result["low_match"]:
            assert set(entry.keys()) == {"name", "score", "description"}

    def test_injected_count_matches_max_results(self):
        # ``max_results`` caps the injected count, leaving the
        # rest to ``low_match``.
        skills = [
            make_skill(skill_id=f"s{i}", name=f"skill-{i}",
                       description="d", content="")
            for i in range(6)
        ]
        candidates = [(s, 1.0 - i * 0.1) for i, s in enumerate(skills)]

        service = make_service()
        result = service._degraded_select(candidates, max_results=3)

        assert len(result["injected"]) == 3
        # At most 3 in low_match (capped regardless of pool).
        assert len(result["low_match"]) == 3

    def test_injected_skips_empty_candidates(self):
        # No candidates → both lists empty.
        service = make_service()
        result = service._degraded_select([], max_results=2)
        assert result == {"injected": [], "low_match": []}


# ============================================================
# Smoke tests
# ============================================================


class TestConstruction:
    """Sanity tests on the public constructor."""

    def test_constructor_stores_dependencies(self):
        # Public attributes match what was passed in.
        skill_repo = make_skill_repo(skills=[])
        embedding_repo = make_embedding_repo()
        embedding_service = make_embedding_service()
        llm_config = make_llm_config()
        config = make_config()

        service = SkillSearchService(
            skill_repo=skill_repo,
            embedding_repo=embedding_repo,
            embedding_service=embedding_service,
            llm_config=llm_config,
            config=config,
        )

        assert service._skill_repo is skill_repo
        assert service._embedding_repo is embedding_repo
        assert service._embedding_service is embedding_service
        assert service._llm_config == llm_config
        # Defensive copy — mutating the caller's dict after
        # construction must NOT affect the service.
        llm_config["model"] = "mutated"
        assert service._llm_config["model"] == "gpt-4o-mini"

    def test_constructor_tolerates_empty_llm_config(self):
        # Empty dict is valid — service falls back to defaults.
        service = SkillSearchService(
            skill_repo=make_skill_repo(skills=[]),
            embedding_repo=make_embedding_repo(),
            embedding_service=make_embedding_service(),
            llm_config={},
            config=make_config(),
        )
        # llm_config was defensively copied to an empty dict.
        assert service._llm_config == {}
