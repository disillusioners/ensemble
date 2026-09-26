"""Snapshot v1 — PR2 tests.

Pins the persona-parametrization + SNAPSHOT_MODEL chain invariants:

  * ``_call_summarization_llm``'s new ``system_message`` parameter
    defaults to :data:`daemon.compaction.DEFAULT_SUMMARIZER_PERSONA`
    (byte-identical to the pre-PR2 inlined literal — this is the
    load-bearing byte-identity invariant the compactor regression
    pack pins).
  * :func:`daemon.compaction.call_summarization_llm_for_snapshot`
    pins the snapshot-side call shape against the compactor method.
  * :func:`daemon.config._resolve_snapshot_model` mirrors the
    compaction resolver's empty/whitespace semantics.
  * :func:`daemon.compaction.resolve_snapshot_model` runs the chain
    ``SNAPSHOT_MODEL > COMPACTION_MODEL > session model`` with the
    ``""``-never-leaps-to-session-model invariant documented in
    design-exploration §2.3 item 4.
  * The boot-time installer + cached accessor + test-reset pair
    follows the existing ``_VSCODE_WEBVIEW_CSP_FIX`` /
    ``_install_vscode_webview_csp_fix`` /
    ``_reset_vscode_webview_csp_fix_for_tests`` pattern.

House style mirrors ``tests/unit/test_reasoning_content_*.py``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# =============================================================================
# Persona-parametrization pin
# =============================================================================


PRE_PR2_INLINED_LITERAL = (
    "You are a helpful assistant that summarizes conversations "
    "concisely while preserving all important details."
)


class TestDefaultSummarizerPersona:
    """``DEFAULT_SUMMARIZER_PERSONA`` is byte-identical to the
    pre-PR2 inlined literal — the byte-identity invariant pinned
    by this trio."""

    def test_constant_is_byte_identical_to_pre_pr2_inlined_literal(self):
        from daemon.compaction import DEFAULT_SUMMARIZER_PERSONA

        assert DEFAULT_SUMMARIZER_PERSONA == PRE_PR2_INLINED_LITERAL
        assert len(DEFAULT_SUMMARIZER_PERSONA) == len(PRE_PR2_INLINED_LITERAL)
        # The pre-PR2 site was an explicit two-line string
        # concatenation. The constant must round-trip the same way.
        assert (
            DEFAULT_SUMMARIZER_PERSONA
            == "You are a helpful assistant that summarizes conversations "
            "concisely while preserving all important details."
        )


class TestCallSummarizationLlmDefaults:
    """``_call_summarization_llm`` defaults ``system_message`` to the
    pre-PR2 literal — existing compaction callers stay byte-identical."""

    async def test_default_system_message_when_omitted(self):
        from unittest.mock import MagicMock

        from daemon.compaction import (
            DEFAULT_SUMMARIZER_PERSONA,
            CompactionContext,
            ContextCompactor,
        )

        # Construct a minimal compactor with the surface the method
        # reads. We don't go through the real LLM path — we observe
        # the message list that the method would feed to
        # ``llm_wrapper.invoke`` once we mock-inject a fake wrapper.
        compactor = ContextCompactor.__new__(ContextCompactor)
        compactor.llm_config_with_headers = {"model": "x"}
        config = MagicMock()
        config.context_window_default = 0
        config.context_window_overrides = {}
        config.timeout_base_s = 90.0
        config.timeout_per_100k_tokens_s = 60.0
        config.timeout_cap_s = 300.0
        config.timeout_facade_margin_s = 5.0
        config.target_ratio = 0.4
        ctx = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="m",
            config=config,
            llm_config={"model": "m"},
        )

        # Patch at the lowest possible level so we can capture the
        # message-list argument the method produces WITHOUT exercising
        # the LLM-construction path (which would need a real LLM
        # credential). We patch ``_invoke_summarizer_llm`` (the
        # ``to_thread`` callable) to capture the messages argument.
        captured: list = []

        def _capture(llm_wrapper, messages):
            captured.append(messages)
            raise RuntimeError("stop after first invoke")

        with patch(
            "daemon.compaction._invoke_summarizer_llm",
            side_effect=_capture,
        ):
            with patch(
                "daemon.compaction._summarization_timeout_s",
                return_value=999.0,
            ):
                with patch(
                    "daemon.compaction.resolve_compaction_model",
                    return_value="",
                ):
                    with patch(
                        "daemon.graph.ThinkingChatOpenAI"
                    ) as fake_chat:
                        fake_chat.return_value = MagicMock()
                        with patch(
                            "daemon.services.llm_failover.wrap_langchain_failover"
                        ) as fake_wrap:
                            fake_wrap.return_value = MagicMock()
                            try:
                                await compactor._call_summarization_llm(
                                    "P", ctx
                                )
                            except RuntimeError:
                                pass
        assert captured, "captured_messages empty — wiring did not run"
        messages_arg = captured[0]
        # The first message must be a SystemMessage carrying the
        # default persona — the byte-identity invariant.
        from langchain_core.messages import SystemMessage

        assert isinstance(messages_arg[0], SystemMessage)
        assert messages_arg[0].content == DEFAULT_SUMMARIZER_PERSONA
        assert messages_arg[0].content == PRE_PR2_INLINED_LITERAL

    async def test_custom_system_message_replaces_default(self):
        """``system_message=`` overrides the default persona."""

        from unittest.mock import MagicMock

        from daemon.compaction import (
            CompactionContext,
            ContextCompactor,
        )

        compactor = ContextCompactor.__new__(ContextCompactor)
        compactor.llm_config_with_headers = {"model": "x"}
        config = MagicMock()
        config.context_window_default = 0
        config.context_window_overrides = {}
        config.timeout_base_s = 90.0
        config.timeout_per_100k_tokens_s = 60.0
        config.timeout_cap_s = 300.0
        config.timeout_facade_margin_s = 5.0
        config.target_ratio = 0.4
        ctx = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="m",
            config=config,
            llm_config={"model": "m"},
        )

        custom_persona = (
            "SNAPSHOT R11 STEUERING BLOCK 1/2: capture the "
            "single-instance conversation arc."
        )

        captured: list = []

        def _capture(llm_wrapper, messages):
            captured.append(messages)
            raise RuntimeError("stop after first invoke")

        with patch(
            "daemon.compaction._invoke_summarizer_llm",
            side_effect=_capture,
        ):
            with patch(
                "daemon.compaction._summarization_timeout_s",
                return_value=999.0,
            ):
                with patch(
                    "daemon.compaction.resolve_compaction_model",
                    return_value="",
                ):
                    with patch(
                        "daemon.graph.ThinkingChatOpenAI"
                    ) as fake_chat:
                        fake_chat.return_value = MagicMock()
                        with patch(
                            "daemon.services.llm_failover.wrap_langchain_failover"
                        ) as fake_wrap:
                            fake_wrap.return_value = MagicMock()
                            try:
                                await compactor._call_summarization_llm(
                                    "P",
                                    ctx,
                                    system_message=custom_persona,
                                )
                            except RuntimeError:
                                pass
        assert captured
        messages_arg = captured[0]
        # Custom persona is the FIRST message content; default is not
        # consulted.
        from langchain_core.messages import SystemMessage

        assert isinstance(messages_arg[0], SystemMessage)
        assert messages_arg[0].content == custom_persona


# =============================================================================
# Snapshot-side wrapper pin
# =============================================================================


class TestCallSummarizationLlmForSnapshot:
    """``call_summarization_llm_for_snapshot`` pins the call shape."""

    async def test_wrapper_delegates_with_system_message_kwarg(self):
        from unittest.mock import AsyncMock, MagicMock

        from daemon.compaction import (
            CompactionContext,
            call_summarization_llm_for_snapshot,
        )

        ctx = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="m",
            config=MagicMock(),
            llm_config={"model": "m"},
        )
        fake_compactor = MagicMock()
        fake_compactor._call_summarization_llm = AsyncMock(
            return_value="ok-text"
        )

        persona = "snapshot steering block"
        result = await call_summarization_llm_for_snapshot(
            fake_compactor,
            "PROMPT",
            ctx,
            system_message=persona,
        )

        assert result == "ok-text"
        fake_compactor._call_summarization_llm.assert_awaited_once()
        # Keyword-only signature pin — the wrapper passes the persona
        # as ``system_message=``, NOT as a positional.
        call_args = fake_compactor._call_summarization_llm.await_args
        call_kwargs = call_args.kwargs
        assert call_kwargs.get("system_message") == persona
        # The prompt and context are passed through.
        assert call_args.args[0] == "PROMPT"
        assert call_args.args[1] is ctx


# =============================================================================
# Config-resolver pin
# =============================================================================


class TestResolveSnapshotModelPure:
    """``_resolve_snapshot_model`` mirrors ``_resolve_compaction_model``
    empty/whitespace normalization."""

    def test_unset_returns_empty(self):
        from daemon.config import _resolve_snapshot_model

        assert _resolve_snapshot_model(None) == ""
        assert _resolve_snapshot_model("") == ""
        assert _resolve_snapshot_model("   ") == ""

    def test_non_empty_wins(self):
        from daemon.config import _resolve_snapshot_model

        assert _resolve_snapshot_model("snapshot-qwen") == "snapshot-qwen"


class TestInstallSnapshotModelBoot:
    """Module-cached boot install follows the existing pattern."""

    def test_install_none_resolves_to_empty(self):
        from daemon import config

        config._reset_snapshot_model_resolved_for_tests()
        config._install_snapshot_model_for_boot(None)
        assert config.get_snapshot_model_env_resolved() == ""

    def test_install_blank_resolves_to_empty(self):
        from daemon import config

        config._reset_snapshot_model_resolved_for_tests()
        config._install_snapshot_model_for_boot("")
        assert config.get_snapshot_model_env_resolved() == ""
        config._install_snapshot_model_for_boot("   ")
        assert config.get_snapshot_model_env_resolved() == ""

    def test_install_value_wins(self):
        from daemon import config

        config._reset_snapshot_model_resolved_for_tests()
        config._install_snapshot_model_for_boot("snapshot-fast-model")
        assert (
            config.get_snapshot_model_env_resolved() == "snapshot-fast-model"
        )

    def test_install_overrides_previous(self):
        """A second ``_install_`` call replaces the cached value."""
        from daemon import config

        config._reset_snapshot_model_resolved_for_tests()
        config._install_snapshot_model_for_boot("first-model")
        assert config.get_snapshot_model_env_resolved() == "first-model"
        config._install_snapshot_model_for_boot("second-model")
        assert config.get_snapshot_model_env_resolved() == "second-model"


class TestResolveSnapshotModelChain:
    """``resolve_snapshot_model`` chain semantics."""

    def _cfg(
        self, *, model: str = "", summary_model: str = ""
    ) -> CompactionConfigModel:
        from daemon.config import CompactionConfig as CompactionConfigModel

        return CompactionConfigModel(
            model=model,
            summarization_model=summary_model,
        )

    def test_snapshot_env_wins_over_compaction_chain(self):
        from daemon.compaction import resolve_snapshot_model

        config = self._cfg(model="agentic", summary_model="")
        out = resolve_snapshot_model(
            config, snapshot_env_value="snapshot-cheap"
        )
        assert out == "snapshot-cheap"

    def test_snapshot_env_empty_falls_through_to_compaction_chain(self):
        """Per design §2.3 item 4: empty ``SNAPSHOT_MODEL`` does
        NOT silently make every snapshot a main-model call —
        it falls through to the compaction-model chain (and through
        THAT to the session model only if the chain also returns
        empty). This is THE documented load-bearing invariant for
        cheap-tier operators."""
        from daemon.compaction import resolve_snapshot_model

        config = self._cfg(model="agentic", summary_model="")
        out = resolve_snapshot_model(config, snapshot_env_value="")
        assert out == "agentic"  # inherited from compaction chain

    def test_snapshot_env_whitespace_treated_as_unset(self):
        from daemon.compaction import resolve_snapshot_model

        config = self._cfg(model="agentic", summary_model="")
        out = resolve_snapshot_model(config, snapshot_env_value="   ")
        assert out == "agentic"

    def test_snapshot_env_none_consults_cached_boot_value(self):
        """``snapshot_env_value=None`` (the typical runtime path)
        consults ``config.get_snapshot_model_env_resolved()`` — the
        cached boot-time value — so the snapshot service doesn't
        need to thread the env value through every call."""
        from daemon import config
        from daemon.compaction import resolve_snapshot_model

        cfg = self._cfg(model="agentic", summary_model="")

        config._reset_snapshot_model_resolved_for_tests()
        config._install_snapshot_model_for_boot("snapshot-pinned")
        # snapshot-pinned from the boot cache wins.
        assert (
            resolve_snapshot_model(cfg) == "snapshot-pinned"
        )

        # Now clear the cache to empty — the same call must fall
        # through to the compaction chain.
        config._install_snapshot_model_for_boot(None)
        assert resolve_snapshot_model(cfg) == "agentic"

    def test_no_overrides_returns_empty_session_model_sentinel(self):
        """No compaction override AND no snapshot env → ``""``.
        The caller's downstream code (the future snapshot service
        in Wave 1b) interprets ``""`` as "use session model +
        context_window_overrides" — the pre-existing behavior at
        the LLM-construction site."""
        from daemon.compaction import resolve_snapshot_model

        config = self._cfg()
        out = resolve_snapshot_model(config, snapshot_env_value="")
        assert out == ""

    def test_legacy_summarization_alias_inherited(self):
        """The legacy ``summarization_model`` alias is honored as
        a fallback WITHIN the compaction chain — the snapshot
        service inherits it transparently."""
        from daemon.compaction import resolve_snapshot_model

        config = self._cfg(model="", summary_model="legacy-snap")
        out = resolve_snapshot_model(config, snapshot_env_value="")
        assert out == "legacy-snap"
