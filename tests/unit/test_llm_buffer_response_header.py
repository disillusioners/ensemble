"""Unit tests for the X-LLMProxy-Buffer-Response outbound header opt-out.

The header rides alongside the proxy identity headers (``x-proxy-app`` /
``x-proxy-interleaved-thinking``) at the 6 inline ``default_headers``
sites: ``daemon/graph.py`` (``build_instance_graph``),
``daemon/compaction.py`` (``ContextCompactor.__init__``),
``daemon/services/title_generation.py``,
``daemon/services/keyword_extraction.py``, and
``daemon/services/child_reports.py`` ×2 (``_summarize_instance`` +
``_repair_report`` — same pattern, not separately exercised here; see
``tests/unit/test_graph_retry_integration.py::
TestProxyHeaderInjectionOtherSites`` for why the deep sibling sites are
not cheaply reachable).

Contract (``LLMConfig.buffer_response_header`` /
``OPENAI_BUFFER_RESPONSE_HEADER``):

* Default True → header present with the EXACT case-sensitive name
  ``X-LLMProxy-Buffer-Response`` and the EXACT string value ``"true"``
  alongside ``x-proxy-app``.
* Flag False → the key is ABSENT entirely. It must NEVER be sent with
  the literal string ``"false"`` (a present-but-false header may be
  misread by the proxy).
* Config dicts lacking the key → header still present (default-on for
  older configs / hand-built dicts).

Seams mirror the existing proxy-header and streaming suites:

* ``build_instance_graph`` with patched ``ThinkingChatOpenAI`` /
  ``StateGraph`` / ``ToolNode`` — the graph-builder dict site
  (``tests/unit/test_graph_retry_integration.py::
  TestProxyHeaderInjection`` seam).
* ``ContextCompactor.__init__`` — stores its headers-augmented config on
  ``self`` (``TestProxyHeaderInjectionOtherSites`` seam).
* ``TitleGenerationService._generate_and_broadcast_title`` — the
  pydantic-object access pattern (``self._config.llm.<field>``
  representative of the 4 service sites).
* ``LLMConfig(_env_file=None)`` — env-var parsing
  (``tests/unit/test_llm_reasoning_echo_config.py`` seam).

Also pins the ``clean_llm_config`` strip guard: the flag key is consumed
by the header sites and then stripped before the ``ChatOpenAI``
constructor — same consumed-then-stripped pattern as ``base_url_backup``
(whose leak crashes every invoke via the ``model_kwargs`` transfer; see
``clean_llm_config``'s docstring).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from daemon.config import LLMConfig

HEADER_NAME = "X-LLMProxy-Buffer-Response"


# ─── Config field + env parsing ───────────────────────────────────────


class TestLLMConfigBufferResponseHeader:
    """LLMConfig.buffer_response_header defaults, env overrides, coercion."""

    def test_default_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_BUFFER_RESPONSE_HEADER", raising=False)
        cfg = LLMConfig(_env_file=None)
        assert cfg.buffer_response_header is True

    def test_env_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_BUFFER_RESPONSE_HEADER", "false")
        cfg = LLMConfig(_env_file=None)
        assert cfg.buffer_response_header is False

    def test_env_true_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_BUFFER_RESPONSE_HEADER", "true")
        cfg = LLMConfig(_env_file=None)
        assert cfg.buffer_response_header is True

    def test_empty_string_coerces_to_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mirrors _coerce_streaming_empty_to_default: an empty value that
        # pastes through .env interpolation must not crash daemon boot.
        monkeypatch.setenv("OPENAI_BUFFER_RESPONSE_HEADER", "")
        cfg = LLMConfig(_env_file=None)
        assert cfg.buffer_response_header is True

    def test_yaml_null_coerces_to_true(self) -> None:
        cfg = LLMConfig(buffer_response_header=None, _env_file=None)
        assert cfg.buffer_response_header is True


# ─── Graph-builder site (daemon/graph.py build_instance_graph) ────────


def _build_graph_and_capture_headers(llm_config: dict) -> dict:
    """Run build_instance_graph and return ThinkingChatOpenAI's default_headers.

    Same seam as TestProxyHeaderInjection in
    tests/unit/test_graph_retry_integration.py.
    """
    with patch("daemon.graph.ThinkingChatOpenAI") as mock_llm_class:
        mock_llm_instance = MagicMock()
        mock_llm_instance.bind_tools.return_value = MagicMock()
        mock_llm_class.return_value = mock_llm_instance

        with patch("daemon.graph.StateGraph"):
            with patch("daemon.graph.ToolNode"):
                from daemon.graph import build_instance_graph

                build_instance_graph(
                    tools=[],
                    checkpointer=MagicMock(),
                    llm_config=llm_config,
                    system_prompt="You are helpful.",
                )

        call_kwargs = mock_llm_class.call_args[1]
        assert "default_headers" in call_kwargs
        return call_kwargs["default_headers"]


class TestGraphBuilderSite:
    """build_instance_graph must stamp the buffer header conditionally."""

    def test_default_on_when_key_missing(self) -> None:
        # Config dict WITHOUT the key (older configs / hand-built dicts):
        # default must stay ON.
        headers = _build_graph_and_capture_headers(
            {"model": "gpt-4o", "api_key": "test"}
        )
        assert headers["x-proxy-app"] == "ensemble"
        assert headers[HEADER_NAME] == "true"

    def test_explicit_true_sends_header(self) -> None:
        headers = _build_graph_and_capture_headers(
            {"model": "gpt-4o", "api_key": "test", "buffer_response_header": True}
        )
        assert headers[HEADER_NAME] == "true"

    def test_flag_false_omits_header_entirely(self) -> None:
        headers = _build_graph_and_capture_headers(
            {"model": "gpt-4o", "api_key": "test", "buffer_response_header": False}
        )
        assert headers["x-proxy-app"] == "ensemble"
        # ABSENT entirely — never the literal string "false".
        assert HEADER_NAME not in headers
        assert "false" not in headers.values()


# ─── Compactor site (daemon/compaction.py ContextCompactor) ───────────


class TestContextCompactorSite:
    """ContextCompactor must stamp the buffer header conditionally."""

    def test_default_on_when_key_missing(self) -> None:
        from daemon.compaction import ContextCompactor

        compactor = ContextCompactor(
            config=MagicMock(),
            llm_config={"model": "gpt-4o", "api_key": "test"},
        )
        headers = compactor.llm_config_with_headers["default_headers"]
        assert headers["x-proxy-app"] == "ensemble"
        assert headers[HEADER_NAME] == "true"

    def test_flag_false_omits_header_entirely(self) -> None:
        from daemon.compaction import ContextCompactor

        compactor = ContextCompactor(
            config=MagicMock(),
            llm_config={
                "model": "gpt-4o",
                "api_key": "test",
                "buffer_response_header": False,
            },
        )
        headers = compactor.llm_config_with_headers["default_headers"]
        assert headers["x-proxy-app"] == "ensemble"
        assert HEADER_NAME not in headers


# ─── Service site (daemon/services/title_generation.py) ───────────────


def _run_title_generation(buffer_flag: bool) -> dict:
    """Run _generate_and_broadcast_title and return ThinkingChatOpenAI's headers.

    Representative of the 4 service sites that read the flag from the
    LLMConfig pydantic object (``self._config.llm.<field>`` /
    ``config.llm.<field>``): title_generation, keyword_extraction,
    child_reports ×2.
    """
    from daemon.services.title_generation import TitleGenerationService

    manager = MagicMock()
    manager.config.llm.buffer_response_header = buffer_flag
    # No existing title → the method proceeds to LLM construction.
    manager._instance_repository.get.return_value = MagicMock(
        instance_metadata={}
    )
    service = TitleGenerationService(manager=manager, logger=MagicMock())

    with patch("daemon.services.title_generation.ThinkingChatOpenAI") as mock_llm:
        with patch(
            "daemon.services.title_generation.wrap_langchain_failover"
        ) as mock_wrap:
            response = MagicMock()
            response.content = "A Generated Title"
            mock_wrap.return_value.invoke.return_value = response

            asyncio.run(
                service._generate_and_broadcast_title(
                    "instance-1", "please do a thing"
                )
            )

        call_kwargs = mock_llm.call_args[1]
        assert "default_headers" in call_kwargs
        return call_kwargs["default_headers"]


class TestTitleGenerationSite:
    """The pydantic-object service sites must honor the flag."""

    def test_flag_true_sends_header(self) -> None:
        headers = _run_title_generation(buffer_flag=True)
        assert headers["x-proxy-app"] == "ensemble"
        assert headers[HEADER_NAME] == "true"

    def test_flag_false_omits_header_entirely(self) -> None:
        headers = _run_title_generation(buffer_flag=False)
        assert headers["x-proxy-app"] == "ensemble"
        assert HEADER_NAME not in headers


# ─── Chokepoint strip guard (daemon/graph.py clean_llm_config) ────────


class TestCleanLLMConfigStripsFlagKey:
    """The flag key must never leak into the ChatOpenAI constructor kwargs.

    ``build_instance_graph`` spreads the caller's llm_config (which now
    carries ``buffer_response_header`` from ``_build_llm_config``) into
    the constructor-bound dict. On langchain-openai >= 1.x an unknown
    kwarg transfers into ``model_kwargs`` and crashes every invoke with a
    TypeError — the exact ``base_url_backup`` precedent documented in
    ``clean_llm_config``'s docstring.
    """

    def test_flag_key_stripped(self) -> None:
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config(
            {
                "model": "gpt-4o",
                "api_key": "test",
                "buffer_response_header": False,
            }
        )
        assert "buffer_response_header" not in cleaned

    def test_proxy_headers_survive(self) -> None:
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config(
            {
                "model": "gpt-4o",
                "api_key": "test",
                "buffer_response_header": True,
                "default_headers": {
                    "x-proxy-app": "ensemble",
                    "x-proxy-interleaved-thinking": "True",
                    HEADER_NAME: "true",
                },
            }
        )
        assert cleaned["default_headers"][HEADER_NAME] == "true"
        assert cleaned["default_headers"]["x-proxy-app"] == "ensemble"
