"""Unit tests for ``daemon.services.keyword_extraction``.

Covers:
- ``_normalize_keywords``: list / string / None inputs, dedupe, stop-words,
  length cap, keyword count cap.
- ``_heuristic_keywords``: backtick terms, CamelCase, ALL_CAPS, first line,
  high-signal tokens; empty input → ``[]``.
- ``_parse_llm_keywords``: comma list, numbered/bulleted list, prose
  contamination, empty input.
- ``extract_keywords``: mocked LLM success, timeout → ``[]``, exception → ``[]``,
  empty message → ``[]``, non-string content blocks handled.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.config import Config
from daemon.services.keyword_extraction import (
    KEYWORD_EXTRACTION_TIMEOUT_S,
    _heuristic_keywords,
    _normalize_keywords,
    _parse_llm_keywords,
    extract_keywords,
)


def _make_config(model_keywords: str | None = "quick", model: str = "gpt-4") -> MagicMock:
    cfg = MagicMock(spec=Config)
    cfg.llm = MagicMock()
    cfg.llm.model = model
    cfg.llm.model_keywords = model_keywords
    cfg.llm.base_url = "https://api.openai.com/v1"
    cfg.llm.api_key = "test-key"
    return cfg


# =============================================================================
# _normalize_keywords
# =============================================================================


class TestNormalizeKeywords:
    def test_none_returns_empty(self) -> None:
        assert _normalize_keywords(None) == []

    def test_empty_list_returns_empty(self) -> None:
        assert _normalize_keywords([]) == []

    def test_empty_string_returns_empty(self) -> None:
        assert _normalize_keywords("") == []
        assert _normalize_keywords("   ") == []

    def test_basic_list(self) -> None:
        assert _normalize_keywords(["auth", "login", "payment"]) == [
            "auth", "login", "payment",
        ]

    def test_strips_whitespace(self) -> None:
        assert _normalize_keywords(["  auth  ", "\tlogin\n"]) == ["auth", "login"]

    def test_drops_empty_entries(self) -> None:
        assert _normalize_keywords(["", "  ", "auth", None, "login"]) == ["auth", "login"]

    def test_dedupes_case_insensitive_preserves_first(self) -> None:
        assert _normalize_keywords(["Auth", "auth", "AUTH", "login"]) == [
            "Auth", "login",
        ]

    def test_drops_stop_words(self) -> None:
        result = _normalize_keywords(
            ["the", "auth", "and", "login", "is", "payment"],
        )
        assert result == ["auth", "login", "payment"]

    def test_drops_overlong_entries(self) -> None:
        long_tok = "x" * 41
        result = _normalize_keywords([long_tok, "auth"])
        assert result == ["auth"]
        # 40 chars exactly is allowed
        assert _normalize_keywords(["x" * 40, "auth"])[0] == "x" * 40

    def test_caps_at_max_keywords(self) -> None:
        many = [f"k{i}" for i in range(20)]
        result = _normalize_keywords(many)
        assert len(result) == 12
        assert result == many[:12]

    def test_accepts_string_input_with_delimiters(self) -> None:
        assert _normalize_keywords("auth, login; payment\nrefund") == [
            "auth", "login", "payment", "refund",
        ]

    def test_non_iterable_input_returns_empty(self) -> None:
        assert _normalize_keywords(123) == []
        assert _normalize_keywords(object()) == []

    def test_non_string_entries_skipped(self) -> None:
        assert _normalize_keywords(["auth", 42, None, "login"]) == ["auth", "login"]


# =============================================================================
# _heuristic_keywords
# =============================================================================


class TestHeuristicKeywords:
    def test_empty_input_returns_empty(self) -> None:
        assert _heuristic_keywords("") == []
        assert _heuristic_keywords("   \n\t  ") == []

    def test_extracts_backtick_terms(self) -> None:
        result = _heuristic_keywords("Refactor the `auth` module to use OAuth")
        assert "auth" in result

    def test_extracts_camelcase(self) -> None:
        result = _heuristic_keywords("Update PaymentModule and UserAuth flow")
        assert "PaymentModule" in result
        assert "UserAuth" in result

    def test_extracts_all_caps(self) -> None:
        result = _heuristic_keywords("Update JWT and API handling")
        assert "JWT" in result
        assert "API" in result

    def test_first_line_used(self) -> None:
        result = _heuristic_keywords("Fix the payment refund bug\nLong details below")
        assert any("payment refund" in k for k in result)

    def test_first_line_capped_at_80(self) -> None:
        long_first = "x" * 200
        result = _heuristic_keywords(long_first)
        joined = " ".join(result)
        # First-line entry should be at most 80 chars
        first_line_entries = [
            k for k in result if k.startswith("x") and len(k) > 20
        ]
        assert all(len(k) <= 80 for k in first_line_entries)

    def test_dedupe_within_message(self) -> None:
        result = _heuristic_keywords("auth auth auth PaymentModule")
        # auth appears once
        assert result.count("auth") == 1
        assert "PaymentModule" in result

    def test_only_stop_words_returns_empty(self) -> None:
        assert _heuristic_keywords("the and or but if when") == []

    def test_caps_at_heuristic_max(self) -> None:
        # 20 backtick terms — should cap at 8
        msg = " ".join(f"`t{i}`" for i in range(20))
        result = _heuristic_keywords(msg)
        assert len(result) <= 8

    def test_high_signal_tokens_from_head(self) -> None:
        # Build a message with no backticks/CamelCase/ALL_CAPS, but with
        # several 4+ char tokens in the first 500 chars
        result = _heuristic_keywords(
            "refactor payment refund flow to handle edge cases gracefully",
        )
        joined = " ".join(result).lower()
        assert "refactor" in joined
        assert "payment" in joined
        assert "refund" in joined

    def test_overlong_backtick_dropped(self) -> None:
        long_inside = "x" * 50
        result = _heuristic_keywords(f"before `{long_inside}` after")
        assert long_inside not in result


# =============================================================================
# _parse_llm_keywords
# =============================================================================


class TestParseLLMKeywords:
    def test_empty_input(self) -> None:
        assert _parse_llm_keywords("") == []
        assert _parse_llm_keywords("   ") == []

    def test_simple_comma_list(self) -> None:
        assert _parse_llm_keywords("auth, payment, refund") == [
            "auth", "payment", "refund",
        ]

    def test_numbered_list_stripped(self) -> None:
        assert _parse_llm_keywords("1. auth\n2. payment\n3. refund") == [
            "auth", "payment", "refund",
        ]

    def test_bulleted_list_stripped(self) -> None:
        assert _parse_llm_keywords("- auth\n- payment\n* refund") == [
            "auth", "payment", "refund",
        ]

    def test_semicolon_delimiter(self) -> None:
        assert _parse_llm_keywords("auth; payment; refund") == [
            "auth", "payment", "refund",
        ]

    def test_prose_intro_ignored(self) -> None:
        text = "Here are the keywords:\nauth, payment, refund\nLet me know."
        assert _parse_llm_keywords(text) == ["auth", "payment", "refund"]

    def test_dedupes(self) -> None:
        assert _parse_llm_keywords("auth, Auth, payment") == ["auth", "payment"]


# =============================================================================
# extract_keywords (LLM path)
# =============================================================================


class _FakeResponse:
    def __init__(self, content: Any) -> None:
        self.content = content


class TestExtractKeywords:
    @pytest.mark.asyncio
    async def test_empty_message_returns_empty(self) -> None:
        assert await extract_keywords("", config=_make_config()) == []
        assert await extract_keywords("   \n  ", config=_make_config()) == []

    @pytest.mark.asyncio
    async def test_successful_string_response(self) -> None:
        with patch(
            "daemon.graph.ThinkingChatOpenAI",
        ) as mock_llm_cls:
            mock_llm = MagicMock()
            mock_llm.invoke = MagicMock(
                return_value=_FakeResponse("auth, payment, refund"),
            )
            mock_llm_cls.return_value = mock_llm
            result = await extract_keywords("Do thing", config=_make_config())
        assert result == ["auth", "payment", "refund"]

    @pytest.mark.asyncio
    async def test_successful_list_content_response(self) -> None:
        with patch(
            "daemon.graph.ThinkingChatOpenAI",
        ) as mock_llm_cls:
            mock_llm = MagicMock()
            mock_llm.invoke = MagicMock(
                return_value=_FakeResponse(
                    [{"text": "auth, payment"}, {"text": ", refund"}],
                ),
            )
            mock_llm_cls.return_value = mock_llm
            result = await extract_keywords("Do thing", config=_make_config())
        assert result == ["auth", "payment", "refund"]

    @pytest.mark.asyncio
    async def test_llm_raises_returns_empty(self) -> None:
        with patch(
            "daemon.graph.ThinkingChatOpenAI",
        ) as mock_llm_cls:
            mock_llm = MagicMock()
            mock_llm.invoke = MagicMock(side_effect=RuntimeError("LLM down"))
            mock_llm_cls.return_value = mock_llm
            result = await extract_keywords("Do thing", config=_make_config())
        assert result == []

    @pytest.mark.asyncio
    async def test_llm_timeout_returns_empty(self) -> None:
        with patch(
            "daemon.graph.ThinkingChatOpenAI",
        ) as mock_llm_cls, patch(
            "daemon.services.keyword_extraction.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            mock_llm = MagicMock()
            mock_llm_cls.return_value = mock_llm
            result = await extract_keywords("Do thing", config=_make_config())
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty(self) -> None:
        with patch(
            "daemon.graph.ThinkingChatOpenAI",
        ) as mock_llm_cls:
            mock_llm = MagicMock()
            mock_llm.invoke = MagicMock(return_value=_FakeResponse(""))
            mock_llm_cls.return_value = mock_llm
            result = await extract_keywords("Do thing", config=_make_config())
        assert result == []

    @pytest.mark.asyncio
    async def test_unparseable_response_returns_empty(self) -> None:
        with patch(
            "daemon.graph.ThinkingChatOpenAI",
        ) as mock_llm_cls:
            mock_llm = MagicMock()
            mock_llm.invoke = MagicMock(
                return_value=_FakeResponse("   \n  "),
            )
            mock_llm_cls.return_value = mock_llm
            result = await extract_keywords("Do thing", config=_make_config())
        assert result == []

    @pytest.mark.asyncio
    async def test_uses_model_keywords_config(self) -> None:
        """When ``model_keywords`` is set, it's used as the LLM model name."""
        with patch(
            "daemon.graph.ThinkingChatOpenAI",
        ) as mock_llm_cls, patch(
            "daemon.graph.clean_llm_config",
            side_effect=lambda c: {k: v for k, v in c.items() if k != "model_vision"},
        ):
            mock_llm = MagicMock()
            mock_llm.invoke = MagicMock(
                return_value=_FakeResponse("auth"),
            )
            mock_llm_cls.return_value = mock_llm
            await extract_keywords("Do thing", config=_make_config(model_keywords="quick"))
            # The constructor was called with model="quick"
            call_kwargs = mock_llm_cls.call_args.kwargs
            assert call_kwargs["model"] == "quick"

    @pytest.mark.asyncio
    async def test_falls_back_to_main_model_when_model_keywords_blank(self) -> None:
        """When ``model_keywords`` is empty/whitespace, main ``model`` is used."""
        with patch(
            "daemon.graph.ThinkingChatOpenAI",
        ) as mock_llm_cls, patch(
            "daemon.graph.clean_llm_config",
            side_effect=lambda c: {k: v for k, v in c.items() if k != "model_vision"},
        ):
            mock_llm = MagicMock()
            mock_llm.invoke = MagicMock(return_value=_FakeResponse("auth"))
            mock_llm_cls.return_value = mock_llm
            await extract_keywords(
                "Do thing", config=_make_config(
                    model_keywords="", model="gpt-4o",
                ),
            )
            call_kwargs = mock_llm_cls.call_args.kwargs
            assert call_kwargs["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_default_timeout_constant_is_40s(self) -> None:
        """Sanity: the default timeout is 40s (not the 3s originally proposed)."""
        assert KEYWORD_EXTRACTION_TIMEOUT_S == 40.0
