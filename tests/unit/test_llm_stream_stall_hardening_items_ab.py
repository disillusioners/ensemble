"""LLM stream-stall hardening — Item A (L1 log-truth) + Item B (L0
request_timeout inject-if-absent) acceptance pins.

Item A (log-truth):
* Mid-stream HTTP timeouts (bare ``httpx.ReadTimeout``) must log via
  the explicit timeout branch ("HTTP timeout (mid-stream, retryable
  via timeout budget)") — never the catch-all's lying "will not
  retry". The classifier RE-RAISES (classification is decided by the
  retry predicate — TIMEOUT_EXCEPTIONS membership — unchanged).
* The ``agent_node`` catch tuple must include ``httpx.TimeoutException``
  so an EXHAUSTED timeout budget routes through the loud-ERROR handler
  instead of "Unexpected error after retries" (source-level pin — the
  tuple is load-bearing text).

Item B (inject-if-absent):
* ``clean_llm_config`` injects ``request_timeout`` strictly-if-absent
  (value from the ``default_request_timeout`` ClassVar); explicit
  values — including explicit ``None`` — are preserved verbatim.
* Startup propagation pins: ``daemon/api.py`` and ``daemon/__main__.py``
  wire ``LLMConfig.request_timeout`` into the ClassVar.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import daemon
import httpx
import openai
import pytest

DAEMON_DIR = Path(daemon.__file__).parent


# ═══════════════════════════════════════════════════════════════════
# Item A.1 — classifier timeout branch (log truth)
# ═══════════════════════════════════════════════════════════════════


class TestClassifierTimeoutBranch:
    """The explicit ``except httpx.TimeoutException`` branch must log
    truthfully and re-raise unchanged."""

    def _classified(self, side_effect):
        from daemon.llm_error_classifier import classify_llm_errors

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = side_effect
        return classify_llm_errors(mock_llm)

    def test_readtimeout_logs_timeout_branch_not_catchall(self, caplog):
        """Bare mid-stream httpx.ReadTimeout → truthful retryable log,
        NOT the catch-all's 'will not retry' lie."""
        with caplog.at_level("WARNING", logger="daemon.llm_error_classifier"):
            with pytest.raises(httpx.ReadTimeout):
                self._classified(
                    httpx.ReadTimeout("read timed out mid-stream")
                ).invoke([])

        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "HTTP timeout (mid-stream, retryable via timeout budget)" in joined, (
            "mid-stream ReadTimeout must log via the explicit timeout branch"
        )
        assert "will not retry" not in joined, (
            "the catch-all's lying 'will not retry' must NOT fire for "
            "timeout-class exceptions"
        )

    def test_readtimeout_is_reraised_not_wrapped(self, caplog):
        """The branch re-raises the SAME exception (the retry predicate
        routes it; the classifier never wraps timeouts)."""
        original = httpx.ReadTimeout("read timed out mid-stream")
        with pytest.raises(httpx.ReadTimeout) as exc_info:
            self._classified(original).invoke([])
        assert exc_info.value is original

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ReadTimeout("mid-stream read deadline"),
            httpx.ConnectTimeout("connect deadline"),
            httpx.PoolTimeout("pool deadline"),
            httpx.WriteTimeout("write deadline"),
        ],
    )
    def test_all_timeout_family_members_take_the_branch(self, exc, caplog):
        """httpx.TimeoutException subclasses (Connect/Pool/Write/Read)
        all land in the truthful branch — none reach the catch-all."""
        with caplog.at_level("WARNING", logger="daemon.llm_error_classifier"):
            with pytest.raises(type(exc)):
                self._classified(exc).invoke([])
        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "HTTP timeout (mid-stream, retryable via timeout budget)" in joined
        assert "will not retry" not in joined

    def test_genuine_unexpected_still_logs_catchall(self, caplog):
        """Control: a non-timeout exception still takes the catch-all
        (the fix must not have swallowed the catch-all's coverage)."""

        class WeirdError(Exception):
            pass

        with caplog.at_level("ERROR", logger="daemon.llm_error_classifier"):
            with pytest.raises(WeirdError):
                self._classified(WeirdError("not a timeout")).invoke([])
        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "Unexpected error (will not retry)" in joined

    def test_branch_precedes_catchall_source_pin(self):
        """Source pin: the ``except httpx.TimeoutException`` branch must
        exist before the catch-all in ``_run_with_classification`` (the
        branch order is load-bearing — Python except clauses match
        first-wins)."""
        from daemon import llm_error_classifier as mod

        src = inspect.getsource(mod)
        timeout_branch = src.index("except httpx.TimeoutException")
        catchall = src.index("except Exception as e:")
        assert timeout_branch < catchall, (
            "the httpx.TimeoutException branch must precede the catch-all"
        )


# ═══════════════════════════════════════════════════════════════════
# Item A.2 — agent_node catch tuple pin
# ═══════════════════════════════════════════════════════════════════


class TestAgentNodeCatchTuple:
    """``httpx.TimeoutException`` must be a member of the agent_node
    exception tuple (exhausted mid-stream timeouts route to the
    loud-ERROR handler, not 'Unexpected error after retries')."""

    def test_agent_node_source_contains_httpx_timeout(self):
        from daemon import graph

        # The agent node is built by the ``create_agent_node`` factory
        # (the catch tuple lives in the returned closure's body).
        src = inspect.getsource(graph.create_agent_node)
        assert "httpx.TimeoutException" in src, (
            "agent_node catch tuple must include httpx.TimeoutException "
            "(L1 log-truth fix)"
        )

    def test_timeout_exception_is_retryable_family_member(self):
        """And it is genuinely in TIMEOUT_EXCEPTIONS — routing through
        the tuple reaches the '(timeout, ...)' loud-ERROR wording."""
        from daemon.llm_error_classifier import TIMEOUT_EXCEPTIONS

        assert issubclass(httpx.TimeoutException, TIMEOUT_EXCEPTIONS)

    def test_exhausted_timeout_would_log_category_timeout(self):
        """Behavioral pin on the handler's category computation: an
        ``httpx.TimeoutException`` matches ``TIMEOUT_EXCEPTIONS`` (the
        '(timeout, ...)' branch), not TRANSIENT / non-retryable."""
        from daemon import graph
        from daemon.llm_error_classifier import (
            TIMEOUT_EXCEPTIONS,
            TRANSIENT_EXCEPTIONS,
        )

        exc = httpx.ReadTimeout("exhausted mid-stream")
        assert isinstance(exc, TIMEOUT_EXCEPTIONS)
        assert not isinstance(exc, TRANSIENT_EXCEPTIONS)
        # And the graph module actually holds both names the handler
        # needs (import-level sanity for the category computation).
        assert graph.TIMEOUT_EXCEPTIONS is TIMEOUT_EXCEPTIONS


# ═══════════════════════════════════════════════════════════════════
# Item B — request_timeout inject-if-absent
# ═══════════════════════════════════════════════════════════════════


class TestRequestTimeoutInjectIfAbsent:
    """``clean_llm_config`` fills the deadline hole for sites that omit
    ``request_timeout`` (title / keyword / child-reports ×2)."""

    def test_injects_default_when_absent(self):
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        cleaned = clean_llm_config(
            {"model": "gpt-4o", "api_key": "test", "base_url": "https://x.test/v1"}
        )
        assert cleaned["request_timeout"] == (
            ThinkingChatOpenAI.default_request_timeout
        )
        assert cleaned["request_timeout"] == 610  # documented default

    def test_preserves_explicit_value(self):
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config(
            {
                "model": "gpt-4o",
                "api_key": "test",
                "base_url": "https://x.test/v1",
                "request_timeout": 123,
            }
        )
        assert cleaned["request_timeout"] == 123, (
            "explicit request_timeout must be preserved verbatim"
        )

    def test_preserves_explicit_none_strict_absence(self):
        """STRICT inject-if-ABSENT: an explicit ``None`` is a caller
        decision and is preserved (key-membership guard, not falsiness)."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config(
            {
                "model": "gpt-4o",
                "api_key": "test",
                "base_url": "https://x.test/v1",
                "request_timeout": None,
            }
        )
        assert cleaned["request_timeout"] is None

    def test_classvar_default_is_610(self):
        from daemon.graph import ThinkingChatOpenAI

        assert ThinkingChatOpenAI.default_request_timeout == 610

    def test_classvar_is_respected_when_overridden(self):
        """The injected value follows the ClassVar (startup wiring
        seam) — mirror of the default_streaming propagation contract."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        original = ThinkingChatOpenAI.default_request_timeout
        ThinkingChatOpenAI.default_request_timeout = 77
        try:
            cleaned = clean_llm_config(
                {"model": "gpt-4o", "api_key": "test", "base_url": "https://x.test/v1"}
            )
            assert cleaned["request_timeout"] == 77
        finally:
            ThinkingChatOpenAI.default_request_timeout = original


class TestRequestTimeoutStartupPropagationPins:
    """Both startup entry points must wire ``LLMConfig.request_timeout``
    into the ClassVar before any LLM is constructed (file-text pins —
    importing api.py would boot the app factory)."""

    def test_api_py_wires_classvar(self):
        src = (DAEMON_DIR / "api.py").read_text()
        assert "ThinkingChatOpenAI.default_request_timeout" in src
        assert "config.llm.request_timeout" in src

    def test_main_py_wires_classvar(self):
        src = (DAEMON_DIR / "__main__.py").read_text()
        assert "ThinkingChatOpenAI.default_request_timeout" in src
        assert "config.llm.request_timeout" in src

    def test_llmconfig_field_exists_with_documented_default(self):
        from daemon.config import LLMConfig

        cfg = LLMConfig(api_key="test", base_url="https://x.test/v1")
        assert cfg.request_timeout == 610
