"""Commit B: optional compaction model via env ``COMPACTION_MODEL`` + yaml ``compaction.model``.

Covers the documented precedence chain and its consumption:

  1. ``_resolve_compaction_model`` (daemon/config.py) — pure resolver:
     env ``COMPACTION_MODEL`` > yaml ``compaction.model`` > unset (``""``).
  2. ``load_config`` end-to-end — precedence resolved EXPLICITLY at the
     resolution site (NOT by pydantic layering: a plain yaml passthrough
     would make init kwargs silently beat env vars).
  3. ``resolve_compaction_model`` (daemon/compaction.py) — engine-side
     effective override: canonical ``model`` > legacy
     ``summarization_model`` alias > ``""`` (session-model behavior).
  4. Override honored in ``_call_summarization_llm`` client construction,
     including consistent resolution across the Commit-A parallel pool.
  5. Never-silent fallback: override construction failure → WARN + session
     model; construction failure WITHOUT override → still raises.
  6. Window math follows the compaction model's context window, with
     ``context_window_overrides`` matched against the override name.

Mirrors fixture mechanics from ``tests/unit/test_compaction.py``
(Commit-A parallel-pool tests) and the pure-resolver style of
``tests/unit/test_llm_allowed_models_precedence.py``.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from daemon.compaction import (
    ChunkedOutcome,
    CompactionContext,
    ContextCompactor,
    get_model_context_limit,
    identify_boundary_groups,
    resolve_compaction_model,
    select_compactable_groups,
)
from daemon.compaction import SystemMessage
from daemon.config import CompactionConfig as CompactionConfigModel
from daemon.config import _resolve_compaction_model, load_config
from daemon.loader import estimate_messages_tokens


# =============================================================================
# Helpers
# =============================================================================

def make_compaction_config(**overrides: Any) -> CompactionConfigModel:
    """CompactionConfig with optional overrides (mirrors test_compaction.py)."""
    defaults: dict[str, Any] = {
        "enabled": True,
        "threshold": 0.80,
        "recent_message_window": 10,
        "min_recent_window": 3,
        "context_window_overrides": {},
        "context_window_default": 0,
        "target_ratio": 0.40,
        "model": "",
        "summarization_model": "",
        "min_messages_before_compaction": 10,
        "summarization_chunk_threshold": 0.60,
        "timeout_base_s": 90.0,
        "timeout_per_100k_tokens_s": 60.0,
        "timeout_cap_s": 300.0,
        "timeout_facade_margin_s": 5.0,
        "operation_budget_s": 300.0,
        "chunk_concurrency": 3,
    }
    defaults.update(overrides)
    return CompactionConfigModel(**defaults)


def make_messages(count: int, content_prefix: str = "Message") -> list:
    """Alternate human/ai messages (mirrors test_compaction.py)."""
    from langchain_core.messages import AIMessage, HumanMessage

    messages = []
    for i in range(count):
        cls = HumanMessage if i % 2 == 0 else AIMessage
        messages.append(cls(content=f"{content_prefix} {i}", id=f"msg-{i}"))
    return messages


def _make_context(
    config: CompactionConfigModel,
    messages: list,
    model_name: str = "gpt-4o",
) -> CompactionContext:
    return CompactionContext(
        messages=messages,
        system_prompt_tokens=0,
        model_name=model_name,
        config=config,
        llm_config={
            "base_url": "http://localhost:1234/v1",
            "api_key": "test-key",
            "model": model_name,
            "temperature": 0.7,
        },
    )


def _write_yaml(tmp_path, compaction_block: str | None) -> str:
    """Minimal loadable config.yaml with an optional compaction section."""
    text = """
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "test-key"
  model: "gpt-4"

persistence:
  db_path: "./data/instances.db"
"""
    if compaction_block is not None:
        text += f"\ncompaction:\n{compaction_block}\n"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(text)
    return str(config_file)


# =============================================================================
# Pure resolver: _resolve_compaction_model (daemon/config.py)
# =============================================================================

class TestResolveCompactionModelPure:
    """env COMPACTION_MODEL > yaml compaction.model > unset ("")."""

    def test_env_wins_over_yaml(self) -> None:
        assert _resolve_compaction_model(
            "yaml-model", env_value="env-model"
        ) == "env-model"

    def test_yaml_honored_when_env_unset(self) -> None:
        assert _resolve_compaction_model("yaml-model", env_value=None) == "yaml-model"

    def test_blank_env_treated_as_unset_yaml_wins(self) -> None:
        """Empty/whitespace env values are UNSET (launcher.sh exports bare
        ``KEY=`` lines verbatim) — the yaml value must not be shadowed."""
        assert _resolve_compaction_model("yaml-model", env_value="") == "yaml-model"
        assert _resolve_compaction_model("yaml-model", env_value="   ") == "yaml-model"

    def test_yaml_none_normalizes_to_empty(self) -> None:
        """``compaction.model: null`` must not crash the str field."""
        assert _resolve_compaction_model(None, env_value=None) == ""

    def test_yaml_blank_normalizes_to_empty(self) -> None:
        assert _resolve_compaction_model("", env_value=None) == ""
        assert _resolve_compaction_model("   ", env_value=None) == ""

    def test_neither_set_yields_empty(self) -> None:
        assert _resolve_compaction_model("", env_value=None) == ""


# =============================================================================
# load_config end-to-end precedence
# =============================================================================

class TestLoadConfigCompactionModelPrecedence:
    """Precedence resolved EXPLICITLY in load_config, not pydantic layering."""

    def test_env_beats_yaml(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("COMPACTION_MODEL", "env-wins-model")
        path = _write_yaml(tmp_path, "  model: yaml-loses-model")
        config = load_config(config_path=path)
        assert config.compaction.model == "env-wins-model"

    def test_yaml_honored_when_env_unset(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("COMPACTION_MODEL", raising=False)
        path = _write_yaml(tmp_path, "  model: yaml-only-model")
        config = load_config(config_path=path)
        assert config.compaction.model == "yaml-only-model"

    def test_env_only_yaml_section_absent(self, tmp_path, monkeypatch) -> None:
        """No ``compaction:`` key in yaml at all → env still lands. The
        section is ALWAYS resolved now (the old passthrough only fired
        when the yaml key existed)."""
        monkeypatch.setenv("COMPACTION_MODEL", "env-only-model")
        path = _write_yaml(tmp_path, None)
        config = load_config(config_path=path)
        assert config.compaction.model == "env-only-model"

    def test_neither_set_defaults_to_empty(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("COMPACTION_MODEL", raising=False)
        path = _write_yaml(tmp_path, None)
        config = load_config(config_path=path)
        assert config.compaction.model == ""

    def test_yaml_null_model_normalizes_to_empty(self, tmp_path, monkeypatch) -> None:
        """``model: null`` in yaml → "" (unset), never a str-field crash."""
        monkeypatch.delenv("COMPACTION_MODEL", raising=False)
        path = _write_yaml(tmp_path, "  model: null")
        config = load_config(config_path=path)
        assert config.compaction.model == ""

    def test_blank_env_falls_to_yaml(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("COMPACTION_MODEL", "   ")
        path = _write_yaml(tmp_path, "  model: yaml-blank-env")
        config = load_config(config_path=path)
        assert config.compaction.model == "yaml-blank-env"

    def test_other_compaction_yaml_keys_untouched(self, tmp_path, monkeypatch) -> None:
        """Resolution is scoped to ``model``; sibling keys keep the exact
        pre-existing passthrough semantics."""
        monkeypatch.delenv("COMPACTION_MODEL", raising=False)
        block = (
            "  model: m1\n"
            "  threshold: 0.55\n"
            "  summarization_model: legacy-m\n"
            "  context_window_overrides:\n"
            '    "vision": 120000\n'
        )
        config = load_config(config_path=_write_yaml(tmp_path, block))
        assert config.compaction.model == "m1"
        assert config.compaction.threshold == 0.55
        assert config.compaction.summarization_model == "legacy-m"
        assert config.compaction.context_window_overrides == {"vision": 120000}


# =============================================================================
# Engine-side effective resolution (canonical > legacy alias > unset)
# =============================================================================

class TestResolveEngineModel:
    def test_canonical_model_wins_over_legacy_alias(self) -> None:
        config = make_compaction_config(model="new-model", summarization_model="old-model")
        assert resolve_compaction_model(config) == "new-model"

    def test_legacy_alias_honored_when_canonical_unset(self) -> None:
        config = make_compaction_config(summarization_model="old-model")
        assert resolve_compaction_model(config) == "old-model"

    def test_both_unset_means_no_override(self) -> None:
        assert resolve_compaction_model(make_compaction_config()) == ""


# =============================================================================
# Engine call site: override reaches client construction
# =============================================================================

class TestCallSiteOverride:
    @pytest.mark.asyncio
    async def test_canonical_model_reaches_client_construction(self) -> None:
        """The canonical override model is what the summarization client
        is constructed with (mirror of the legacy-alias vision-strip test
        in test_compaction.py)."""
        from unittest.mock import MagicMock, patch

        from langchain_core.messages import AIMessage

        config = make_compaction_config(model="gpt-4o-mini")
        llm_config = {
            "base_url": "http://localhost:1234/v1",
            "api_key": "test-key",
            "model": "gpt-4o",
            "temperature": 0.7,
        }
        compactor = ContextCompactor(config, llm_config)
        mock_response = AIMessage(content="Summary.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)
        context = _make_context(config, [], model_name="gpt-4o")

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=mock_llm_instance,
            create=True,
        ) as mock_cls:
            await compactor._call_summarization_llm("Summarize this.", context)

        assert mock_cls.call_args.kwargs.get("model") == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_parallel_pool_resolves_override_consistently(self) -> None:
        """Commit-A composition: EVERY concurrent batch call resolves the
        SAME override — captured per batch task from the shared config
        through the REAL ``_summarize_chunked`` pool (no pool stub)."""
        config = make_compaction_config(
            model="pool-override-model",
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            summarization_chunk_threshold=0.01,  # force chunking
        )
        messages = make_messages(120)
        compactor = ContextCompactor(config, {})

        captured: list[str] = []

        async def _stub_single_batch(batch_groups, context):
            captured.append(resolve_compaction_model(context.config))
            return SystemMessage(
                content="[Conversation Summary]\nbatch summary",
                id=f"compaction-{len(captured) - 1}",
            )

        compactor._summarize_single_batch = _stub_single_batch

        groups = identify_boundary_groups(messages)
        compactable, _preserved, _w = select_compactable_groups(
            groups,
            config.recent_message_window,
            config.min_recent_window,
            1000,
            0,
            estimate_messages_tokens,
            config_threshold=config.threshold,
        )
        assert len(compactable) > 20  # multiple batches under the real pool

        outcome = await compactor._summarize_chunked(
            compactable, _make_context(config, messages)
        )
        assert outcome.stop_reason == "completed"
        assert len(captured) == len(outcome.summaries)
        assert set(captured) == {"pool-override-model"}


# =============================================================================
# Never-silent fallback
# =============================================================================

class TestNeverSilentFallback:
    @pytest.mark.asyncio
    async def test_construction_failure_warns_and_falls_back(
        self, caplog,
    ) -> None:
        """Override client construction failure → WARN (with model name)
        + rebuild from the session-model config; call still succeeds."""
        from unittest.mock import MagicMock, patch

        from langchain_core.messages import AIMessage

        config = make_compaction_config(model="ghost-model")
        llm_config = {
            "base_url": "http://localhost:1234/v1",
            "api_key": "test-key",
            "model": "gpt-4o",
            "temperature": 0.7,
        }
        compactor = ContextCompactor(config, llm_config)
        context = _make_context(config, [], model_name="gpt-4o")

        mock_response = AIMessage(content="session-model summary", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)

        constructions: list[str | None] = []

        def _ctor(**kwargs):
            constructions.append(kwargs.get("model"))
            if kwargs.get("model") == "ghost-model":
                raise ValueError("unknown model: ghost-model")
            return mock_llm_instance

        with patch(
            "daemon.graph.ThinkingChatOpenAI", side_effect=_ctor, create=True
        ), caplog.at_level(logging.WARNING, logger="daemon.compaction"):
            content = await compactor._call_summarization_llm("Summarize.", context)

        assert content == "session-model summary"
        # First construction used the override, the rebuild used the
        # session model — never silent in between.
        assert constructions == ["ghost-model", "gpt-4o"]
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "ghost-model" in r.getMessage()
        ]
        assert warnings, "expected a WARN log naming the failed override model"
        assert any("falling back" in r.getMessage() for r in warnings)

    @pytest.mark.asyncio
    async def test_construction_failure_without_override_still_raises(
        self, caplog,
    ) -> None:
        """No override set + construction failure → nothing to fall back
        TO; the error propagates (existing outer truncate-fallback path
        handles it) — no silent swallowing, no bogus fallback warning."""
        from unittest.mock import MagicMock, patch

        config = make_compaction_config()  # model="" → session behavior
        compactor = ContextCompactor(config, {})
        context = _make_context(config, [], model_name="gpt-4o")

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            side_effect=RuntimeError("boom"),
            create=True,
        ), caplog.at_level(logging.WARNING, logger="daemon.compaction"):
            with pytest.raises(RuntimeError, match="boom"):
                await compactor._call_summarization_llm("Summarize.", context)

        assert not [
            r for r in caplog.records if "falling back" in r.getMessage()
        ]


# =============================================================================
# Window math follows the compaction model
# =============================================================================

class TestWindowMathFollowsCompactionModel:
    def test_effective_model_name_prefers_override(self) -> None:
        config = make_compaction_config(model="sum-model")
        compactor = ContextCompactor(config, {})
        context = _make_context(config, [], model_name="gpt-4o")
        assert compactor._effective_model_name(context) == "sum-model"

    def test_effective_model_name_falls_back_to_session_model(self) -> None:
        config = make_compaction_config()
        compactor = ContextCompactor(config, {})
        context = _make_context(config, [], model_name="gpt-4o")
        assert compactor._effective_model_name(context) == "gpt-4o"

    def test_effective_model_name_honors_legacy_alias(self) -> None:
        config = make_compaction_config(summarization_model="legacy-sum-model")
        compactor = ContextCompactor(config, {})
        context = _make_context(config, [], model_name="gpt-4o")
        assert compactor._effective_model_name(context) == "legacy-sum-model"

    def test_context_window_overrides_match_override_name(self) -> None:
        """context_window_overrides interact with the SETTING: substring
        matching runs against the compaction model's name, not the
        session model's."""
        config = make_compaction_config(
            model="sum-model",
            context_window_overrides={"sum-model": 12345},
        )
        compactor = ContextCompactor(config, {})
        context = _make_context(config, [], model_name="gpt-4o")
        effective = compactor._effective_model_name(context)
        assert effective == "sum-model"
        assert get_model_context_limit(effective, config) == 12345
        # Session-model window is untouched for comparison (128k registry).
        assert get_model_context_limit("gpt-4o", config) == 128000

    @pytest.mark.asyncio
    async def test_threshold_math_uses_compaction_model_window(self) -> None:
        """End-to-end differential: the SAME message set triggers
        compaction under a small override window and is skipped under the
        session model's window — the threshold math followed the
        setting. No LLM is invoked (chunked summarizer stubbed)."""
        config = make_compaction_config(
            model="sum-model",
            context_window_overrides={"sum-model": 100},
            min_messages_before_compaction=2,
            recent_message_window=2,
            min_recent_window=1,
        )
        messages = make_messages(30)
        total_tokens = estimate_messages_tokens(messages)
        assert total_tokens > 100 * 0.80, "fixture must exceed the override threshold"
        assert total_tokens <= 128000 * 0.80, "fixture must sit below the session threshold"

        compactor = ContextCompactor(config, {})

        async def _fake_chunked(compactable, context):
            return ChunkedOutcome(
                summaries=[
                    SystemMessage(
                        content="[Conversation Summary]\nall groups",
                        id="compaction-0",
                    )
                ],
                failed_batches=[],
                stop_reason="completed",
            )

        compactor._summarize_chunked = _fake_chunked
        result = await compactor.compact_state(
            _make_context(config, messages, model_name="gpt-4o")
        )
        assert result is not None, (
            "compaction must trigger under the override model's 100-token window"
        )

        # Identical messages, override unset → session-model window → skip.
        config.model = ""
        result_no_override = await compactor.compact_state(
            _make_context(config, messages, model_name="gpt-4o")
        )
        assert result_no_override is None, (
            "same messages must NOT trigger under the session model's window"
        )


# =============================================================================
# W1: auto-path threshold gated at min(session_window, override_window)
# =============================================================================

class TestWindowGatedAtSessionWindow:
    """W1 (review fix): when a compaction-model override is active, the
    AUTO-path threshold gate is sized at ``min(session_window,
    override_window)`` — NOT at the override window alone — so a LARGER
    override window cannot push proactive compaction past session
    capacity. The internal sizing math (chunk batching, merge, condense)
    still follows the OVERRIDE window; this is the TRIGGER side only.

    Failure mode the gate prevents: with override > session, the OLD
    code let the session model overflow before proactive compaction
    triggered. The reactive CLE path (force=False) returns None on
    context-length error, so auto-recovery was defeated. /compact
    (force=True) still recovered — that path was never broken. Once
    the gate is correct, proactive compaction fires at the SESSION
    threshold, so the session model never overflows.
    """

    def test_override_greater_than_session_gates_at_session(self) -> None:
        """(a) override window > session window → gate at SESSION."""
        config = make_compaction_config(
            model="big-override",
            context_window_overrides={
                "big-override": 200_000,
                "session-model": 200,
            },
        )
        compactor = ContextCompactor(config, {})
        context = _make_context(config, [], model_name="session-model")
        assert compactor._trigger_window(context) == 200

    def test_override_smaller_than_session_gates_at_override(self) -> None:
        """(b) override window < session window → gate at OVERRIDE
        (current behavior, preserved)."""
        config = make_compaction_config(
            model="small-override",
            context_window_overrides={
                "small-override": 100,
                "session-model": 200_000,
            },
        )
        compactor = ContextCompactor(config, {})
        context = _make_context(config, [], model_name="session-model")
        assert compactor._trigger_window(context) == 100

    def test_no_override_uses_session_model_window(self) -> None:
        """No override set → session-model window, byte-identical with
        pre-setting behavior (S-7 anti-drift: callers that never set
        the override must see no change)."""
        config = make_compaction_config()  # model="" → no override
        compactor = ContextCompactor(config, {})
        context = _make_context(config, [], model_name="gpt-4o")
        # gpt-4o is in the MODEL_CONTEXT_LIMITS registry at 128000.
        assert compactor._trigger_window(context) == 128000

    def test_warn_emitted_once_when_override_greater_than_session(
        self, caplog,
    ) -> None:
        """(c) WARN emitted when override window > session window,
        exactly ONCE per compactor instance (no per-batch spam)."""
        config = make_compaction_config(
            model="big-override",
            context_window_overrides={
                "big-override": 200_000,
                "session-model": 200,
            },
        )
        compactor = ContextCompactor(config, {})
        context = _make_context(config, [], model_name="session-model")
        with caplog.at_level(logging.WARNING, logger="daemon.compaction"):
            compactor._trigger_window(context)
            compactor._trigger_window(context)  # second call: must NOT re-warn
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "gated at" in r.getMessage()
        ]
        assert len(warnings) == 1, (
            f"expected exactly ONE WARN per compactor instance, "
            f"got {len(warnings)}"
        )
        msg = warnings[0].getMessage()
        # Message names BOTH windows so operators can act on it.
        assert "big-override" in msg
        assert "session-model" in msg
        assert "200000" in msg  # override window
        assert "200" in msg  # session window

    def test_no_warn_when_override_smaller_than_session(self, caplog) -> None:
        """(c) WARN NOT emitted when override window <= session window
        — the original asymmetric W1 condition is the only WARN trigger."""
        config = make_compaction_config(
            model="small-override",
            context_window_overrides={
                "small-override": 100,
                "session-model": 200_000,
            },
        )
        compactor = ContextCompactor(config, {})
        context = _make_context(config, [], model_name="session-model")
        with caplog.at_level(logging.WARNING, logger="daemon.compaction"):
            compactor._trigger_window(context)
        assert not [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "gated at" in r.getMessage()
        ]

    def test_no_warn_when_no_override(self, caplog) -> None:
        """(c) WARN NOT emitted when no override is set — the gate
        falls through to session-only and there is nothing to warn
        about."""
        config = make_compaction_config()  # model=""
        compactor = ContextCompactor(config, {})
        context = _make_context(config, [], model_name="gpt-4o")
        with caplog.at_level(logging.WARNING, logger="daemon.compaction"):
            compactor._trigger_window(context)
        assert not [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "gated at" in r.getMessage()
        ]
