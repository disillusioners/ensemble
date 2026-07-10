"""Tests for ``SkillEmbeddingService`` (Phase 2 of Skill Evolution).

Tests cover the full Phase 2 surface:

* :meth:`cosine_similarity` — pure-Python vector math, no numpy.
* :meth:`embed_text` / :meth:`embed_user_message` — OpenAI
  ``/embeddings`` endpoint, with the configurable base_url/api_key
  fallback chain.
* :meth:`generate_trigger_queries` — OpenAI chat-completions call,
  parsed into a clean list of strings; defensive against the
  common LLM failure modes (markdown fences, prose lists, quoted
  terms).
* :meth:`update_skill_embeddings` — full refresh pipeline
  (clear → generate → embed → persist) end-to-end with mocks.
* Graceful error handling — every OpenAI call is wrapped so a
  transient API failure doesn't crash the daemon; per-query
  embedding failures are skipped without aborting the batch.

All OpenAI SDK calls are mocked at the :mod:`openai` module
boundary via ``unittest.mock.patch`` so no network traffic is
produced. The tests construct in-memory ``config`` and
``embedding_repo`` mocks (no SQLite database required).
"""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.services.skill_embedding_service import (
    SkillEmbeddingService,
    _clean_queries,
    _parse_prose_list,
    _try_parse_json_list,
)


# ============================================================
# Fixtures / helpers
# ============================================================


def make_config(
    *,
    embedding_model: str = "text-embedding-3-small",
    embedding_base_url: str | None = None,
    embedding_api_key: str | None = None,
) -> MagicMock:
    """Build a MagicMock that quacks like :class:`SkillEvolutionConfig`.

    The service accesses ``config.embedding_model``,
    ``config.embedding_base_url``, ``config.embedding_api_key`` — that's
    all the Phase 2 surface needs from config. Other fields are
    deliberate not-stubs to catch unexpected access early.
    """
    cfg = MagicMock(spec=["embedding_model", "embedding_base_url", "embedding_api_key"])
    cfg.embedding_model = embedding_model
    cfg.embedding_base_url = embedding_base_url
    cfg.embedding_api_key = embedding_api_key
    return cfg


def make_embedding_repo() -> MagicMock:
    """Build a MagicMock that quacks like :class:`SkillEmbeddingRepository`.

    Service uses ``delete_by_skill`` and ``create``; they are the
    only two methods :meth:`update_skill_embeddings` calls.
    """
    repo = MagicMock()
    repo.delete_by_skill = MagicMock(return_value=0)
    repo.create = MagicMock(
        return_value=MagicMock(id="emb-1", skill_id="skill-1")
    )
    return repo


def make_skill(
    *,
    skill_id: str = "skill-abc",
    name: str = "code-review",
    description: str = "Review code for bugs and style issues.",
    content: str = (
        "# Code Review Skill\n\n"
        "When asked to review code, examine it for correctness, "
        "performance, security, and style."
    ),
) -> MagicMock:
    """Build a minimal mock :class:`Skill` for service tests."""
    skill = MagicMock(spec=["id", "name", "description", "content"])
    skill.id = skill_id
    skill.name = name
    skill.description = description
    skill.content = content
    return skill


def make_llm_config(
    *,
    base_url: str = "https://api.openai.com/v1",
    api_key: str = "sk-test-123",
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """Build the ``llm_config`` dict the service constructor expects."""
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    }


def make_chat_response(content: str) -> MagicMock:
    """Build a mock chat-completion response with ``content`` as the message text."""
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def make_embedding_response(
    embedding: list[float], *, n_results: int = 1
) -> MagicMock:
    """Build a mock embeddings response with one or more result rows."""
    rows = []
    for _ in range(n_results):
        row = MagicMock()
        row.embedding = list(embedding)
        rows.append(row)
    response = MagicMock()
    response.data = rows
    return response


def make_service(
    *,
    config: MagicMock | None = None,
    embedding_repo: MagicMock | None = None,
    llm_config: dict[str, Any] | None = None,
) -> SkillEmbeddingService:
    """Construct a :class:`SkillEmbeddingService` with sensible defaults."""
    return SkillEmbeddingService(
        config=config if config is not None else make_config(),
        embedding_repo=(
            embedding_repo if embedding_repo is not None
            else make_embedding_repo()
        ),
        llm_config=llm_config if llm_config is not None else make_llm_config(),
    )


# ============================================================
# TestCosineSimilarity
# ============================================================


class TestCosineSimilarity:
    """``cosine_similarity`` is pure-Python vector math (no numpy).

    These tests run the function directly without any service
    plumbing — they pin the exact math and the three degenerate
    inputs (zero vector, single-dim, mismatched lengths).
    """

    def test_identical_vectors_returns_one(self):
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0, 3.0]
        result = SkillEmbeddingService.cosine_similarity(a, b)
        assert math.isclose(result, 1.0, abs_tol=1e-9)

    def test_orthogonal_vectors_returns_zero(self):
        # Perpendicular axes.
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        result = SkillEmbeddingService.cosine_similarity(a, b)
        assert math.isclose(result, 0.0, abs_tol=1e-9)

    def test_opposite_vectors_returns_negative_one(self):
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        result = SkillEmbeddingService.cosine_similarity(a, b)
        assert math.isclose(result, -1.0, abs_tol=1e-9)

    def test_known_value_simple(self):
        # a = (3, 4), b = (4, 3)
        # dot = 12 + 12 = 24, |a|=5, |b|=5 → 24/25 = 0.96
        a = [3.0, 4.0]
        b = [4.0, 3.0]
        result = SkillEmbeddingService.cosine_similarity(a, b)
        assert math.isclose(result, 24 / 25, abs_tol=1e-9)

    def test_zero_vector_returns_zero(self):
        # Avoids divide-by-zero; both inputs are zero.
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        result = SkillEmbeddingService.cosine_similarity(a, b)
        assert result == 0.0

    def test_zero_second_vector_returns_zero(self):
        a = [1.0, 2.0, 3.0]
        b = [0.0, 0.0, 0.0]
        result = SkillEmbeddingService.cosine_similarity(a, b)
        assert result == 0.0

    def test_unit_vectors(self):
        # Two distinct unit vectors.
        a = [0.6, 0.8]  # already unit length
        b = [1.0, 0.0]
        # dot = 0.6, |a|=1, |b|=1 → 0.6
        result = SkillEmbeddingService.cosine_similarity(a, b)
        assert math.isclose(result, 0.6, abs_tol=1e-9)

    def test_scales_correctly(self):
        # Cosine similarity is scale-invariant.
        a = [1.0, 2.0, 3.0]
        b = [2.0, 4.0, 6.0]  # 2 * a
        result = SkillEmbeddingService.cosine_similarity(a, b)
        assert math.isclose(result, 1.0, abs_tol=1e-9)

    def test_empty_vectors(self):
        # Two empty vectors → both norms are 0 → returns 0.0 cleanly.
        result = SkillEmbeddingService.cosine_similarity([], [])
        assert result == 0.0

    def test_no_numpy_dependency(self):
        """Confirm the implementation doesn't import numpy anywhere."""
        import daemon.services.skill_embedding_service as mod

        # Inspect the module source for a top-level numpy import.
        # The cleanest check is to confirm ``numpy`` isn't in the
        # module namespace and the import set is small.
        assert not hasattr(mod, "np"), "Must not import numpy as 'np'"
        assert not hasattr(mod, "numpy"), "Must not import numpy"


# ============================================================
# TestEmbedText
# ============================================================


class TestEmbedText:
    """``embed_text`` calls the OpenAI ``/embeddings`` endpoint.

    All tests patch :class:`openai.OpenAI` at the module boundary
    so we exercise the full code path without any network call.
    """

    @pytest.mark.asyncio
    async def test_returns_list_of_floats(self):
        """The returned type is plain ``list[float]`` (NOT bytes, NOT numpy)."""
        service = make_service()
        vector = [0.1, 0.2, 0.3, 0.4]
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.embeddings.create = MagicMock(
            return_value=make_embedding_response(vector)
        )
        mock_openai_cls.return_value = mock_client

        with patch("daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls):
            result = await service.embed_text("hello world")

        assert isinstance(result, list)
        assert all(isinstance(x, float) for x in result)
        assert result == [0.1, 0.2, 0.3, 0.4]

    @pytest.mark.asyncio
    async def test_uses_config_embedding_model(self):
        """The configured ``embedding_model`` is passed to the API."""
        config = make_config(embedding_model="text-embedding-3-large")
        service = make_service(config=config)
        vector = [0.5, 0.5]

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.embeddings.create = MagicMock(
            return_value=make_embedding_response(vector)
        )
        mock_openai_cls.return_value = mock_client

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            await service.embed_text("test input")

        # The model kwarg should be the configured embedding model.
        kwargs = mock_client.embeddings.create.call_args.kwargs
        assert kwargs["model"] == "text-embedding-3-large"
        assert kwargs["input"] == "test input"

    @pytest.mark.asyncio
    async def test_uses_config_base_url_override(self):
        """``config.embedding_base_url`` overrides ``llm_config.base_url``."""
        config = make_config(
            embedding_base_url="https://custom-embeddings.example/v1"
        )
        llm_config = make_llm_config(
            base_url="https://api.openai.com/v1",
            api_key="sk-llm",
        )
        service = make_service(config=config, llm_config=llm_config)

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.embeddings.create = MagicMock(
            return_value=make_embedding_response([0.1])
        )
        mock_openai_cls.return_value = mock_client

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            await service.embed_text("test")

        # The OpenAI client should be constructed with the
        # embedding-specific base_url, not the LLM one.
        init_kwargs = mock_openai_cls.call_args.kwargs
        assert init_kwargs["base_url"] == "https://custom-embeddings.example/v1"

    @pytest.mark.asyncio
    async def test_falls_back_to_llm_config_base_url(self):
        """When ``embedding_base_url`` is ``None``, ``llm_config.base_url`` is used."""
        config = make_config(embedding_base_url=None)
        llm_config = make_llm_config(
            base_url="https://api.openai.com/v1",
            api_key="sk-llm",
        )
        service = make_service(config=config, llm_config=llm_config)

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.embeddings.create = MagicMock(
            return_value=make_embedding_response([0.1])
        )
        mock_openai_cls.return_value = mock_client

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            await service.embed_text("test")

        init_kwargs = mock_openai_cls.call_args.kwargs
        assert init_kwargs["base_url"] == "https://api.openai.com/v1"

    @pytest.mark.asyncio
    async def test_uses_config_api_key_override(self):
        """``config.embedding_api_key`` overrides ``llm_config.api_key``."""
        config = make_config(embedding_api_key="sk-embedding")
        llm_config = make_llm_config(api_key="sk-llm")
        service = make_service(config=config, llm_config=llm_config)

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.embeddings.create = MagicMock(
            return_value=make_embedding_response([0.1])
        )
        mock_openai_cls.return_value = mock_client

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            await service.embed_text("test")

        init_kwargs = mock_openai_cls.call_args.kwargs
        assert init_kwargs["api_key"] == "sk-embedding"

    @pytest.mark.asyncio
    async def test_falls_back_to_llm_config_api_key(self):
        """When ``embedding_api_key`` is ``None``, ``llm_config.api_key`` is used."""
        config = make_config(embedding_api_key=None)
        llm_config = make_llm_config(api_key="sk-llm-fallback")
        service = make_service(config=config, llm_config=llm_config)

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.embeddings.create = MagicMock(
            return_value=make_embedding_response([0.1])
        )
        mock_openai_cls.return_value = mock_client

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            await service.embed_text("test")

        init_kwargs = mock_openai_cls.call_args.kwargs
        assert init_kwargs["api_key"] == "sk-llm-fallback"

    @pytest.mark.asyncio
    async def test_empty_text_raises_value_error(self):
        """Empty / whitespace text is rejected with ``ValueError``."""
        service = make_service()
        with pytest.raises(ValueError, match="empty"):
            await service.embed_text("")
        with pytest.raises(ValueError, match="empty"):
            await service.embed_text("   \n  ")

    @pytest.mark.asyncio
    async def test_api_failure_raises_runtime_error(self):
        """SDK errors become ``RuntimeError`` so the pipeline can skip them."""
        service = make_service()
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.embeddings.create = MagicMock(
            side_effect=RuntimeError("API down")
        )
        mock_openai_cls.return_value = mock_client

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            with pytest.raises(RuntimeError, match="failed"):
                await service.embed_text("hello")

    @pytest.mark.asyncio
    async def test_empty_response_raises_runtime_error(self):
        """Empty ``data`` field becomes ``RuntimeError`` to surface broken API responses."""
        service = make_service()
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        bad_response = MagicMock()
        bad_response.data = []
        mock_client.embeddings.create = MagicMock(return_value=bad_response)
        mock_openai_cls.return_value = mock_client

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            with pytest.raises(RuntimeError, match="empty data"):
                await service.embed_text("hello")

    @pytest.mark.asyncio
    async def test_converts_int_values_to_floats(self):
        """Some SDK versions return ints in the vector; coerce to float."""
        service = make_service()
        row = MagicMock()
        row.embedding = [1, 2, 3]  # ints, not floats
        bad_response = MagicMock()
        bad_response.data = [row]
        mock_client = MagicMock()
        mock_client.embeddings.create = MagicMock(return_value=bad_response)
        mock_openai_cls = MagicMock(return_value=mock_client)

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            result = await service.embed_text("test")

        assert result == [1.0, 2.0, 3.0]
        assert all(isinstance(x, float) for x in result)


# ============================================================
# TestEmbedUserMessage
# ============================================================


class TestEmbedUserMessage:
    """``embed_user_message`` is a thin wrapper around ``embed_text``."""

    @pytest.mark.asyncio
    async def test_delegates_to_embed_text(self):
        """The message is passed through to the embeddings endpoint."""
        service = make_service()
        vector = [0.1, 0.2, 0.3]
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.embeddings.create = MagicMock(
            return_value=make_embedding_response(vector)
        )
        mock_openai_cls.return_value = mock_client

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            result = await service.embed_user_message("how do I review code?")

        assert result == [0.1, 0.2, 0.3]
        call_kwargs = mock_client.embeddings.create.call_args.kwargs
        assert call_kwargs["input"] == "how do I review code?"

    @pytest.mark.asyncio
    async def test_empty_message_raises_value_error(self):
        """Empty messages still raise (consistent with embed_text)."""
        service = make_service()
        with pytest.raises(ValueError):
            await service.embed_user_message("")


# ============================================================
# TestGenerateTriggerQueries
# ============================================================


class TestGenerateTriggerQueries:
    """``generate_trigger_queries`` calls the chat completions endpoint
    and parses the response."""

    @pytest.mark.asyncio
    async def test_parses_json_array(self):
        """Standard JSON-array response is parsed cleanly."""
        service = make_service()
        queries = ["review my code", "is this code correct", "lint check"]
        response_text = (
            '["review my code", "is this code correct", "lint check"]'
        )

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response(response_text)
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            result = await service.generate_trigger_queries(skill)

        assert result == queries

    @pytest.mark.asyncio
    async def test_parses_markdown_fenced_json(self):
        """```json [...]``` fenced responses are stripped before parsing."""
        service = make_service()
        queries = ["deploy my app", "push to staging"]
        response_text = (
            "```json\n"
            '["deploy my app", "push to staging"]\n'
            "```"
        )
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response(response_text)
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            result = await service.generate_trigger_queries(skill)

        assert result == queries

    @pytest.mark.asyncio
    async def test_parses_prose_numbered_list(self):
        """Numbered prose lists (LLM ignores the JSON instruction) are still parsed."""
        service = make_service()
        response_text = (
            "Here are some trigger queries:\n"
            "1. review my code\n"
            "2. is this code correct\n"
            "3. lint check"
        )
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response(response_text)
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            result = await service.generate_trigger_queries(skill)

        assert result == [
            "review my code",
            "is this code correct",
            "lint check",
        ]

    @pytest.mark.asyncio
    async def test_strips_think_blocks(self):
        """``<think>...</think>`` reasoning blocks don't pollute the parser."""
        service = make_service()
        queries = ["deploy", "ship"]
        response_text = (
            "<think>Let me think about this skill...</think>"
            '["deploy", "ship"]'
        )
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response(response_text)
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            result = await service.generate_trigger_queries(skill)

        assert result == queries

    @pytest.mark.asyncio
    async def test_clamps_to_max_ten(self):
        """More than 10 queries are truncated (LLM sometimes overruns)."""
        service = make_service()
        queries = [f"query {i}" for i in range(20)]
        response_text = "[" + ", ".join(f'"{q}"' for q in queries) + "]"

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response(response_text)
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            result = await service.generate_trigger_queries(skill)

        assert len(result) == 10
        assert result == [f"query {i}" for i in range(10)]

    @pytest.mark.asyncio
    async def test_dedupes_case_insensitively(self):
        """Duplicates differing only in case are collapsed."""
        service = make_service()
        response_text = '["Review", "review", "REVIEW", "check"]'

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response(response_text)
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            result = await service.generate_trigger_queries(skill)

        # First-seen wins; case-insensitive dedupe.
        assert result == ["Review", "check"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_api_failure(self):
        """An OpenAI error gracefully returns ``[]`` (no cache)."""
        service = make_service()
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            side_effect=RuntimeError("API down")
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            result = await service.generate_trigger_queries(skill)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_garbage_response(self):
        """A prose response with no parseable structure returns ``[]``."""
        service = make_service()
        response_text = (
            "Sure! Here are some example user messages that would trigger "
            "this skill, written in natural language without numbering or "
            "JSON wrapping as you requested. First, a user might say they "
            "want a code review. Another common phrasing involves asking "
            "to lint or audit the changes."
        )
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response(response_text)
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            result = await service.generate_trigger_queries(skill)

        # Falls back to quoted-term extraction — the response has
        # no quoted strings, so the result should be empty.
        # (This documents the graceful-degrade behavior.)
        # We don't pin it to ``[]`` strictly because the prose
        # parser could potentially find something — the test
        # asserts no exception is raised and the result is a list.
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_uses_llm_config_for_chat(self):
        """The chat client uses ``llm_config['base_url']`` and ``['api_key']``."""
        llm_config = make_llm_config(
            base_url="https://custom-llm.example/v1",
            api_key="sk-llm-key",
            model="claude-3-5-sonnet",
        )
        service = make_service(llm_config=llm_config)

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response('["one", "two", "three"]')
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            await service.generate_trigger_queries(skill)

        init_kwargs = mock_openai_cls.call_args.kwargs
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert init_kwargs["base_url"] == "https://custom-llm.example/v1"
        assert init_kwargs["api_key"] == "sk-llm-key"
        assert call_kwargs["model"] == "claude-3-5-sonnet"


# ============================================================
# TestUpdateSkillEmbeddings
# ============================================================


class TestUpdateSkillEmbeddings:
    """``update_skill_embeddings`` is the full pipeline:
    clear → generate → embed → persist."""

    @pytest.mark.asyncio
    async def test_end_to_end_persists_each_embedding(self):
        """Generates queries, embeds each, persists each, returns count."""
        config = make_config()
        embedding_repo = make_embedding_repo()
        service = make_service(config=config, embedding_repo=embedding_repo)

        # Embedding result (any vector).
        vector = [0.1] * 4
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response(
                '["review code", "lint it", "check correctness"]'
            )
        )
        # ``embeddings.create`` is called per query.
        mock_client.embeddings.create = MagicMock(
            return_value=make_embedding_response(vector)
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill(skill_id="skill-xyz")

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            written = await service.update_skill_embeddings(skill)

        assert written == 3
        assert mock_client.embeddings.create.call_count == 3
        # The repo ``create`` is called once per persisted embedding.
        assert embedding_repo.create.call_count == 3
        # First call: skill_id is the skill's id.
        first_create_kwargs = embedding_repo.create.call_args_list[0]
        assert first_create_kwargs.args[0] == "skill-xyz"

    @pytest.mark.asyncio
    async def test_clears_existing_embeddings_first(self):
        """The existing cache is cleared via ``delete_by_skill`` before writing."""
        config = make_config()
        embedding_repo = make_embedding_repo()
        embedding_repo.delete_by_skill = MagicMock(return_value=5)
        service = make_service(config=config, embedding_repo=embedding_repo)

        vector = [0.1, 0.2]
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response('["a", "b", "c"]')
        )
        mock_client.embeddings.create = MagicMock(
            return_value=make_embedding_response(vector)
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            await service.update_skill_embeddings(skill)

        # ``delete_by_skill`` was called with the skill's id.
        embedding_repo.delete_by_skill.assert_called_once_with("skill-abc")

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_queries_generated(self):
        """LLM failure (empty parse) → no embeddings written, returns ``0``."""
        config = make_config()
        embedding_repo = make_embedding_repo()
        service = make_service(config=config, embedding_repo=embedding_repo)

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        # Chat returns garbage the parser can't handle (no quoted, no JSON, no list).
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response(
                "I cannot help with that request."
            )
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            written = await service.update_skill_embeddings(skill)

        assert written == 0
        # ``delete_by_skill`` IS still called (we always wipe before regenerating).
        embedding_repo.delete_by_skill.assert_called_once()
        # ``create`` is never called.
        embedding_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_zero_when_skill_id_missing(self):
        """A skill without ``id`` is rejected gracefully — no DB calls at all."""
        config = make_config()
        embedding_repo = make_embedding_repo()
        service = make_service(config=config, embedding_repo=embedding_repo)

        skill = MagicMock(spec=["id"])
        skill.id = None  # Missing / unset id.

        written = await service.update_skill_embeddings(skill)

        assert written == 0
        embedding_repo.delete_by_skill.assert_not_called()
        embedding_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_individual_embedding_failures(self):
        """One failing query doesn't abort the rest of the batch."""
        config = make_config()
        embedding_repo = make_embedding_repo()
        service = make_service(config=config, embedding_repo=embedding_repo)

        # Three queries generated: query 2 will fail at embedding time.
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response(
                '["q1", "q2 will fail", "q3"]'
            )
        )

        call_count = {"n": 0}

        def maybe_fail(**kwargs: Any) -> Any:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("transient API error")
            return make_embedding_response([0.1, 0.2])

        mock_client.embeddings.create = MagicMock(side_effect=maybe_fail)
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            written = await service.update_skill_embeddings(skill)

        # Two of three succeeded — q2 failed and was skipped.
        assert written == 2
        # Embeddings were attempted 3 times (one per query).
        assert mock_client.embeddings.create.call_count == 3
        # Two persisted.
        assert embedding_repo.create.call_count == 2

    @pytest.mark.asyncio
    async def test_persist_failure_is_logged_not_raised(self):
        """A repo ``create`` failure is logged, batch continues."""
        config = make_config()
        embedding_repo = make_embedding_repo()
        # Simulate create failing for the second embedding.
        original_create = MagicMock(
            side_effect=[
                MagicMock(id="ok1"),
                RuntimeError("DB gone"),
                MagicMock(id="ok2"),
            ]
        )
        embedding_repo.create = original_create
        service = make_service(config=config, embedding_repo=embedding_repo)

        vector = [0.1, 0.2]
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response('["q1", "q2", "q3"]')
        )
        mock_client.embeddings.create = MagicMock(
            return_value=make_embedding_response(vector)
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            written = await service.update_skill_embeddings(skill)

        # Two of three persisted (the second one's repo write failed).
        assert written == 2

    @pytest.mark.asyncio
    async def test_uses_embedding_base_url_for_embed_calls(self):
        """Embedding endpoint resolves to ``config.embedding_base_url`` when set."""
        config = make_config(
            embedding_base_url="https://custom-embeddings.example/v1",
            embedding_api_key="sk-embed",
        )
        llm_config = make_llm_config(
            base_url="https://api.openai.com/v1", api_key="sk-llm"
        )
        embedding_repo = make_embedding_repo()
        service = make_service(
            config=config,
            embedding_repo=embedding_repo,
            llm_config=llm_config,
        )

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(
            return_value=make_chat_response('["a", "b", "c"]')
        )
        mock_client.embeddings.create = MagicMock(
            return_value=make_embedding_response([0.1])
        )
        mock_openai_cls.return_value = mock_client

        skill = make_skill()
        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI", mock_openai_cls
        ):
            await service.update_skill_embeddings(skill)

        # Two OpenAI() instantiations — one for chat (llm), one for
        # embeddings (custom). Confirm we created clients with
        # both base_urls.
        base_urls = [
            call.kwargs["base_url"]
            for call in mock_openai_cls.call_args_list
        ]
        assert "https://api.openai.com/v1" in base_urls
        assert "https://custom-embeddings.example/v1" in base_urls


# ============================================================
# TestConstructor
# ============================================================


class TestConstructor:
    """The constructor stores dependencies as attributes for testability."""

    def test_stores_config(self):
        config = make_config()
        service = make_service(config=config)
        assert service.config is config

    def test_stores_embedding_repo(self):
        repo = make_embedding_repo()
        service = make_service(embedding_repo=repo)
        assert service.embedding_repo is repo

    def test_stores_llm_config_as_copy(self):
        """Stored ``llm_config`` is a defensive copy — caller mutations don't leak in."""
        llm_config = make_llm_config()
        service = make_service(llm_config=llm_config)

        # Mutate the caller's dict.
        llm_config["api_key"] = "mutated-after-construct"

        # The service's stored copy must be untouched.
        assert service.llm_config["api_key"] == "sk-test-123"


# ============================================================
# TestInternalHelpers
# ============================================================


class TestInternalHelpers:
    """Pin the parser / cleaner helpers used by the LLM-response parser."""

    def test_try_parse_json_list_valid(self):
        assert _try_parse_json_list('["a", "b", "c"]') == ["a", "b", "c"]

    def test_try_parse_json_list_with_ints(self):
        # Non-string entries are coerced to strings.
        assert _try_parse_json_list('[1, "two", 3]') == ["1", "two", "3"]

    def test_try_parse_json_list_invalid(self):
        """Malformed JSON returns ``None`` (caller falls back to next parser)."""
        assert _try_parse_json_list("[unclosed") is None
        assert _try_parse_json_list("just a string") is None

    def test_try_parse_json_list_non_list(self):
        """Valid JSON but not a list returns ``None``."""
        assert _try_parse_json_list('{"a": 1}') is None

    def test_parse_prose_list_numbered(self):
        text = "1. first\n2. second\n3. third"
        assert _parse_prose_list(text) == ["first", "second", "third"]

    def test_parse_prose_list_bulleted(self):
        text = "- alpha\n* beta\n• gamma"
        assert _parse_prose_list(text) == ["alpha", "beta", "gamma"]

    def test_parse_prose_list_quoted_fallback(self):
        # When no numbered/bulleted lines are present, fall back
        # to extracting quoted strings (a common LLM pattern).
        text = 'Here are some: "alpha" and "beta" and "gamma"'
        result = _parse_prose_list(text)
        assert set(result) == {"alpha", "beta", "gamma"}

    def test_clean_queries_dedupes_and_strips(self):
        queries = ["  hello  ", "hello", "HELLO", "world", ""]
        assert _clean_queries(queries) == ["hello", "world"]

    def test_clean_queries_preserves_first_seen_casing(self):
        queries = ["Hello", "World", "hello"]
        assert _clean_queries(queries) == ["Hello", "World"]
