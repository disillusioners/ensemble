"""Tests for outbound LLM streaming activation (Cloudflare ~125s 524 fix).

Background
----------
Ensemble's LLM calls go out through a Cloudflare-proxied base_url
(llm.ensem.dev / llm.daoduc.org, CF anycast). CF drops connections
with zero response bytes for ~100-125s and returns 524 on every
generation longer than that window. The fix is to send
``stream: True`` on the wire so chunked bytes flow back through CF
before its read timeout can kill the connection. LightRAG survives the
same CF wall via the same mechanism.

This file verifies the activation is wired end-to-end:

* ``daemon.graph.clean_llm_config`` — the single choke point for every
  LangChain ``ThinkingChatOpenAI`` construction site — injects the
  streaming default (CF-125s fix).
* The injected flag reaches the wire payload (BaseChatOpenAI
  serializes ``"stream": self.streaming``).
* The HA facade (``wrap_langchain_failover``) still composes correctly
  on top of a streaming-enabled client.
* The proxy headers (``default_headers``) survive ``clean_llm_config``
  unchanged at all construction sites.
* ``LLMConfig.streaming`` is exposed with the right default and env
  hook (precedent: ``OPENAI_REASONING_ECHO_DISABLED_MODELS``).

Why this matters
----------------
The non-streaming path sends a single silent POST that produces zero
response bytes until the model finishes generating. Any generation
longer than ~125s gets killed by Cloudflare's anycast read timeout with
a 524. Streaming keeps bytes flowing — every token is a heartbeat —
so the connection survives arbitrary generation lengths. LangChain's
``invoke()`` aggregates the chunks back into the same ``AIMessage``
(content / tool_calls / usage / reasoning_content all preserved), so
callers see identical final results.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage


# ---------------------------------------------------------------------------
# clean_llm_config — streaming default injection
# ---------------------------------------------------------------------------


class TestCleanLlmConfigStreamingDefault:
    """``clean_llm_config`` is the single choke point for every LangChain
    ``ThinkingChatOpenAI`` construction site. The CF-125s fix lives here:
    callers that don't opt in/out get ``streaming=True`` automatically."""

    def test_injects_streaming_true_when_absent(self):
        """Default path: caller did not specify streaming — the helper
        injects True so the wire sees ``stream: True``."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config({"model": "gpt-4o", "api_key": "k"})
        assert cleaned["streaming"] is True, (
            "clean_llm_config must inject streaming=True when absent "
            "(Cloudflare ~125s 524 fix)"
        )

    def test_preserves_explicit_streaming_false(self):
        """Sites that genuinely need non-streaming (debugging, exotic
        backend) opt out by setting ``streaming=False`` explicitly — the
        helper must NOT silently override them."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config(
            {"model": "gpt-4o", "api_key": "k", "streaming": False}
        )
        assert cleaned["streaming"] is False, (
            "explicit streaming=False must survive clean_llm_config "
            "unchanged — do not silently coerce non-streaming callers"
        )

    def test_preserves_explicit_streaming_true(self):
        """Idempotent: explicit streaming=True passes through unchanged."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config(
            {"model": "gpt-4o", "api_key": "k", "streaming": True}
        )
        assert cleaned["streaming"] is True

    def test_still_strips_base_url_backup(self):
        """F1 lesson regression guard: ``base_url_backup`` MUST NOT leak
        into ChatOpenAI(**cfg) — clean_llm_config must continue to
        strip it (otherwise it crashes ``Completions.create()`` with an
        unexpected-kwarg TypeError)."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config(
            {"model": "g", "base_url_backup": "https://backup.example.com"}
        )
        assert "base_url_backup" not in cleaned

    def test_still_strips_model_vision(self):
        """``model_vision`` is a daemon-internal routing hint — must not
        leak into ChatOpenAI(**cfg)."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config({"model": "g", "model_vision": "gpt-4o"})
        assert "model_vision" not in cleaned

    def test_preserves_default_headers(self):
        """The hardcoded proxy header pair MUST survive clean_llm_config
        at every LangChain construction site — see
        graph.py:5191-5194 / compaction.py:593-595 / title_generation.py:96-99
        / keyword_extraction.py:369-371 / child_reports.py:758-761,1383-1386.
        Without these headers, the proxy rejects the request with 403.
        """
        from daemon.graph import clean_llm_config

        headers = {
            "x-proxy-app": "ensemble",
            "x-proxy-interleaved-thinking": "True",
        }
        cleaned = clean_llm_config(
            {"model": "g", "default_headers": headers}
        )
        assert cleaned["default_headers"] == headers, (
            "proxy headers must survive clean_llm_config unchanged "
            "(Cloudflare proxy auth relies on these)"
        )


# ---------------------------------------------------------------------------
# ThinkingChatOpenAI / wire payload — stream:true reaches the wire
# ---------------------------------------------------------------------------


class TestWirePayloadStreamFlag:
    """The wire payload (the JSON body LangChain sends to
    ``chat.completions``) must carry ``stream: True`` when the helper
    injects the streaming default. This is the actual CF-125s fix —
    without ``stream: True`` on the wire, Cloudflare kills the
    connection at ~125s."""

    def test_default_cleaned_config_serializes_stream_true(self):
        """A ThinkingChatOpenAI built from a default-cleaned config
        serializes ``stream: True`` on the wire."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        cfg = clean_llm_config({"model": "test-model", "api_key": "k"})
        llm = ThinkingChatOpenAI(**cfg)

        # _get_request_payload is the parent-class method that builds the
        # wire body. It sets ``"stream": self.streaming`` at the wire
        # boundary (langchain-openai >=1.x, BaseChatOpenAI).
        payload = llm._get_request_payload([HumanMessage(content="hi")])
        assert payload["stream"] is True, (
            "wire payload must carry stream: True to survive the "
            "Cloudflare ~125s read timeout (524)"
        )

    def test_explicit_streaming_false_serializes_stream_false(self):
        """Opt-out path: an explicit streaming=False site still gets
        stream: False on the wire (verify the helper did NOT override
        the caller's intent)."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        cfg = clean_llm_config(
            {"model": "test-model", "api_key": "k", "streaming": False}
        )
        llm = ThinkingChatOpenAI(**cfg)
        payload = llm._get_request_payload([HumanMessage(content="hi")])
        assert payload["stream"] is False

    def test_thinking_chat_openai_instance_has_streaming_true(self):
        """The instance-level flag — verified directly. This is what
        LangChain's ``_should_stream`` consults at ``invoke()`` time to
        decide whether to use the streaming API path internally."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        cfg = clean_llm_config({"model": "test-model", "api_key": "k"})
        llm = ThinkingChatOpenAI(**cfg)
        assert llm.streaming is True


# ---------------------------------------------------------------------------
# LLMConfig — operator-tunable streaming knob
# ---------------------------------------------------------------------------


class TestLLMConfigStreamingField:
    """The operator-facing knob (env var + YAML) that the helper pulls
    from. Mirrors the ``OPENAI_REASONING_ECHO_DISABLED_MODELS``
    denylist-style precedent documented at the field."""

    def test_default_is_true(self):
        """Default streaming on (CF-125s fix): operators must explicitly
        opt out, not opt in."""
        os.environ.pop("OPENAI_STREAMING", None)

        from daemon.config import LLMConfig

        cfg = LLMConfig()
        assert cfg.streaming is True

    def test_env_var_false_overrides(self):
        """OPENAI_STREAMING=false flips it off (debugging / exotic
        backend). The base-url prefix scheme matches the env-prefix on
        LLMConfig (``SettingsConfigDict(env_prefix="OPENAI_")``)."""
        os.environ["OPENAI_STREAMING"] = "false"
        try:
            from daemon.config import LLMConfig

            cfg = LLMConfig()
            assert cfg.streaming is False
        finally:
            os.environ.pop("OPENAI_STREAMING", None)


# ---------------------------------------------------------------------------
# End-to-end wiring — class var propagates to clean_llm_config + wire payload
# ---------------------------------------------------------------------------


class TestStreamingDefaultClassVarPropagation:
    """The operator-facing knob (LLMConfig.streaming → OPENAI_STREAMING env
    var) MUST reach the wire payload end-to-end through the class-var
    propagation that startup sites use (mirrors the
    ``reasoning_echo_disabled_models`` pattern). This is the wiring
    proof — without it, ``OPENAI_STREAMING=false`` would be a dead knob
    (the chokepoint would still hardcode ``True``)."""

    def test_class_var_false_injects_streaming_false_in_chokepoint(self):
        """Set the class var to False (the post-startup state when
        ``OPENAI_STREAMING=false`` was applied) — verify
        ``clean_llm_config`` injects False, not True."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        original_value = ThinkingChatOpenAI.default_streaming
        ThinkingChatOpenAI.default_streaming = False
        try:
            cleaned = clean_llm_config({"model": "gpt-4o", "api_key": "k"})
            assert cleaned["streaming"] is False, (
                "clean_llm_config must inject ThinkingChatOpenAI.default_streaming, "
                "not a hardcoded True — OPENAI_STREAMING=false would otherwise "
                "be a dead knob"
            )
        finally:
            ThinkingChatOpenAI.default_streaming = original_value

    def test_class_var_false_reaches_wire_payload(self):
        """Wire-payload proof: the injected False must propagate to the
        JSON body LangChain sends to ``chat.completions``. Without
        ``stream: False`` on the wire, the backend would still get
        ``stream: True`` (default) and the CF-125s fix would not be
        disengageable."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        original_value = ThinkingChatOpenAI.default_streaming
        ThinkingChatOpenAI.default_streaming = False
        try:
            cfg = clean_llm_config({"model": "test-model", "api_key": "k"})
            llm = ThinkingChatOpenAI(**cfg)
            payload = llm._get_request_payload([HumanMessage(content="hi")])
            assert payload["stream"] is False, (
                "wire payload must reflect ThinkingChatOpenAI.default_streaming "
                "so OPENAI_STREAMING=false reaches the backend"
            )
        finally:
            ThinkingChatOpenAI.default_streaming = original_value


# ---------------------------------------------------------------------------
# HA facade composition — wrap_langchain_failover still works
# ---------------------------------------------------------------------------


class TestFacadeWithStreamingClient:
    """The LangChain HA facade (``wrap_langchain_failover``) wraps
    ``invoke()`` with retry. It must compose correctly on top of a
    streaming-enabled client — the streaming happens INSIDE
    LangChain's ``invoke()`` (which routes via ``_stream`` + chunk
    aggregation), and the facade wraps that single ``invoke`` call as
    a unit. This test verifies the wrapper still builds and exposes
    ``invoke`` on a streaming client."""

    def test_wrap_langchain_failover_accepts_streaming_client(self):
        """A ChatOpenAI constructed with streaming=True must be
        acceptable to ``wrap_langchain_failover`` — the facade must
        NOT require a non-streaming client."""
        from langchain_openai import ChatOpenAI

        from daemon.services.llm_failover import (
            ChatFailoverBinding,
            wrap_langchain_failover,
        )

        # Use a clean streaming-enabled client — the production path
        # after the CF-125s fix is exactly this shape.
        llm = ChatOpenAI(
            api_key="test",
            base_url="https://primary.example/v1",
            model="gpt-4o",
            max_retries=0,
            streaming=True,
        )
        binding = wrap_langchain_failover(
            llm,
            {
                "base_url": "https://primary.example/v1",
                "base_url_backup": "https://backup.example/v1",
                "api_key": "test",
                "model": "gpt-4o",
            },
        )
        assert isinstance(binding, ChatFailoverBinding)
        # The facade surfaces the same HA knob surface regardless of
        # streaming — verify the streaming flag is still on the
        # underlying client (the facade does not proxy it).
        assert llm.streaming is True
        # invoke is exposed; calling it is the caller's job (the
        # wrapper retries on top of invoke, the streaming happens
        # inside invoke's _stream path).
        assert hasattr(binding, "invoke")

    def test_wrap_langchain_failover_invoke_routes_to_classified_client(self):
        """Smoke: binding.invoke() must delegate to the underlying
        client's invoke (LangChain's invoke aggregates chunks when
        streaming=True). Verify with a mock client that records the
        call."""
        from daemon.services.llm_failover import wrap_langchain_failover

        # Mock client with streaming=True flag (LangChain's _should_stream
        # would consult this in production; the wrapper doesn't care).
        mock_client = MagicMock()
        mock_client.streaming = True
        mock_client.invoke = MagicMock(
            return_value=AIMessage(content="aggregated")
        )

        binding = wrap_langchain_failover(
            mock_client,
            {
                "base_url": "https://primary.example/v1",
                "api_key": "test",
                "model": "gpt-4o",
            },
        )
        result = binding.invoke([HumanMessage(content="hi")])

        # The wrapper's invoke delegates to the client's invoke, which
        # in production returns the aggregated AIMessage (chunks joined
        # inside LangChain's invoke). Mock returns the same shape.
        assert result.content == "aggregated"
        mock_client.invoke.assert_called_once()