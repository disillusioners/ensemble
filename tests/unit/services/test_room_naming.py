"""Behavior matrix for the group-chat P2 naming service (D-S3 cascade).

The quick-model lane is mocked DETERMINISTICALLY as a fail/timeout/
success TRIPLE — NO live LLM in tests. Coverage:

* tier 1 — parent-provided name passes through (sanitized), never
  touching the model lane;
* tier 2 — quick-model SUCCESS (bounded): the 3.0s ``wait_for`` bound
  asserted by CAPTURE (the timeout kwarg the service passes), the
  purpose-bound ``model_quick`` field used, the ``allowed_models``
  allowlist never consulted, ``<think>`` blocks stripped, list-content
  handled;
* tier 2 failure → tier 3 deterministic fallback (agent id base),
  instant (wall-clock bounded in-test), no retry (exactly ONE invoke);
* cascade orchestration labels (parent_provided / quick_model /
  agent_fallback) — the spawn result's tier provenance.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from daemon.services.room_naming import (
    ROOM_NAME_MODEL_TIMEOUT_S,
    RoomNamingService,
)


# ── harness ────────────────────────────────────────────────────────────────


def _manager(model_quick: str | None = "quick-model") -> MagicMock:
    manager = MagicMock()
    manager.config.llm.model_quick = model_quick
    manager.config.llm.model = "fallback-model"
    manager.config.llm.base_url = "https://llm.example/v1"
    manager.config.llm.base_url_backup = None
    manager.config.llm.api_key = "test-key"
    manager.config.llm.buffer_response_header = False
    return manager


class _Capture:
    """Captures the constructor config + the wait_for timeout kwarg."""

    def __init__(self, invoke_result=None, invoke_exc: Exception | None = None):
        self.construct_kwargs: dict = {}
        self.raw_llm_config: dict = {}
        self.timeout_captured: float | None = None
        self.invoke_calls: int = 0
        self._invoke_result = invoke_result
        self._invoke_exc = invoke_exc

    def fake_llm_cls(self, **kwargs):
        self.construct_kwargs = kwargs
        return SimpleNamespace()

    def fake_failover(self, _llm, raw_config):
        self.raw_llm_config = raw_config

        def invoke(_messages):
            self.invoke_calls += 1
            if self._invoke_exc is not None:
                raise self._invoke_exc
            return self._invoke_result

        return SimpleNamespace(invoke=invoke)

    async def fake_wait_for(self, coro, timeout=None):
        self.timeout_captured = timeout
        return await coro


def _content(value: str):
    return SimpleNamespace(content=value)


# ── tier 2 — the bounded SUCCESS path ──────────────────────────────────────


class TestTier2Success:
    @pytest.mark.asyncio
    async def test_success_returns_sanitized_name(self):
        mgr = _manager()
        cap = _Capture(invoke_result=_content("Capturer One!"))
        svc = RoomNamingService(mgr)
        with patch(
            "daemon.services.room_naming.ThinkingChatOpenAI",
            cap.fake_llm_cls,
        ), patch(
            "daemon.services.room_naming.wrap_langchain_failover",
            cap.fake_failover,
        ), patch(
            "daemon.services.room_naming.asyncio.wait_for",
            cap.fake_wait_for,
        ):
            out = await svc.generate_member_name(agent_id="capture")
        assert out == "capturer-one"

    @pytest.mark.asyncio
    async def test_three_second_bound_asserted_by_capture(self):
        """The D-S3 tier-2 bound: the service passes
        ``ROOM_NAME_MODEL_TIMEOUT_S = 3.0`` as the wait_for timeout —
        the ONE sanctioned spawn-blocking naming wait."""
        mgr = _manager()
        cap = _Capture(invoke_result=_content("scout"))
        svc = RoomNamingService(mgr)
        with patch(
            "daemon.services.room_naming.ThinkingChatOpenAI",
            cap.fake_llm_cls,
        ), patch(
            "daemon.services.room_naming.wrap_langchain_failover",
            cap.fake_failover,
        ), patch(
            "daemon.services.room_naming.asyncio.wait_for",
            cap.fake_wait_for,
        ):
            out = await svc.generate_member_name(agent_id="capture")
        assert out == "scout"
        assert cap.timeout_captured == pytest.approx(3.0)
        assert ROOM_NAME_MODEL_TIMEOUT_S == 3.0

    @pytest.mark.asyncio
    async def test_purpose_bound_model_quick_used_not_global(self):
        mgr = _manager(model_quick="quick-model")
        cap = _Capture(invoke_result=_content("scout"))
        svc = RoomNamingService(mgr)
        with patch(
            "daemon.services.room_naming.ThinkingChatOpenAI",
            cap.fake_llm_cls,
        ), patch(
            "daemon.services.room_naming.wrap_langchain_failover",
            cap.fake_failover,
        ), patch(
            "daemon.services.room_naming.asyncio.wait_for",
            cap.fake_wait_for,
        ):
            await svc.generate_member_name(agent_id="capture")
        # The purpose-bound QUICK model rides the raw config (F1: the
        # facade needs the RAW dict); constructor receives cleaned.
        assert cap.raw_llm_config["model"] == "quick-model"
        assert cap.construct_kwargs["model"] == "quick-model"

    @pytest.mark.asyncio
    async def test_never_consults_allowed_models(self):
        """Purpose-bound fields bypass the allowlist (config.py SCOPE
        note) — the service never reads ``allowed_models``."""
        mgr = _manager(model_quick="quick-model")
        mgr.config.llm.allowed_models = ["totally-different"]
        cap = _Capture(invoke_result=_content("scout"))
        svc = RoomNamingService(mgr)
        with patch(
            "daemon.services.room_naming.ThinkingChatOpenAI",
            cap.fake_llm_cls,
        ), patch(
            "daemon.services.room_naming.wrap_langchain_failover",
            cap.fake_failover,
        ), patch(
            "daemon.services.room_naming.asyncio.wait_for",
            cap.fake_wait_for,
        ):
            out = await svc.generate_member_name(agent_id="capture")
        assert out == "scout"  # quick-model NOT in allowed_models — used anyway
        assert cap.raw_llm_config["model"] == "quick-model"  # decisive: the quick lane was actually used

    @pytest.mark.asyncio
    async def test_think_block_stripped_and_list_content(self):
        mgr = _manager()
        cap = _Capture(
            invoke_result=_content(
                "<think>reasoning here</think>log-analyzer"
            )
        )
        svc = RoomNamingService(mgr)
        with patch(
            "daemon.services.room_naming.ThinkingChatOpenAI",
            cap.fake_llm_cls,
        ), patch(
            "daemon.services.room_naming.wrap_langchain_failover",
            cap.fake_failover,
        ), patch(
            "daemon.services.room_naming.asyncio.wait_for",
            cap.fake_wait_for,
        ):
            out = await svc.generate_member_name(agent_id="log")
        assert out == "log-analyzer"

    @pytest.mark.asyncio
    async def test_think_only_output_falls_to_none(self):
        mgr = _manager()
        cap = _Capture(
            invoke_result=_content("<think>only reasoning</think>")
        )
        svc = RoomNamingService(mgr)
        with patch(
            "daemon.services.room_naming.ThinkingChatOpenAI",
            cap.fake_llm_cls,
        ), patch(
            "daemon.services.room_naming.wrap_langchain_failover",
            cap.fake_failover,
        ), patch(
            "daemon.services.room_naming.asyncio.wait_for",
            cap.fake_wait_for,
        ):
            out = await svc.generate_member_name(agent_id="log")
        assert out is None  # unusable ⇒ tier 3

    @pytest.mark.asyncio
    async def test_cleared_model_quick_falls_back_to_model(self):
        """The ``model_title`` lane precedent: a cleared purpose field
        falls back to the global default model."""
        mgr = _manager(model_quick=None)
        cap = _Capture(invoke_result=_content("scout"))
        svc = RoomNamingService(mgr)
        with patch(
            "daemon.services.room_naming.ThinkingChatOpenAI",
            cap.fake_llm_cls,
        ), patch(
            "daemon.services.room_naming.wrap_langchain_failover",
            cap.fake_failover,
        ), patch(
            "daemon.services.room_naming.asyncio.wait_for",
            cap.fake_wait_for,
        ):
            await svc.generate_member_name(agent_id="capture")
        assert cap.raw_llm_config["model"] == "fallback-model"


# ── the failure TRIPLE — error / timeout / unavailable ─────────────────────


class TestTier2FailureTriple:
    @pytest.mark.asyncio
    async def test_failure_exception_returns_none_instant(self):
        """TRIPLE leg 1 — invoke RAISES: returns None; exactly ONE
        invoke (no retry-loop); wall-clock instant."""
        mgr = _manager()
        cap = _Capture(invoke_exc=RuntimeError("boom"))
        svc = RoomNamingService(mgr)
        with patch(
            "daemon.services.room_naming.ThinkingChatOpenAI",
            cap.fake_llm_cls,
        ), patch(
            "daemon.services.room_naming.wrap_langchain_failover",
            cap.fake_failover,
        ), patch(
            "daemon.services.room_naming.asyncio.wait_for",
            cap.fake_wait_for,
        ):
            started = time.monotonic()
            out = await svc.generate_member_name(agent_id="capture")
            elapsed = time.monotonic() - started
        assert out is None
        assert cap.invoke_calls == 1  # no retry-loop
        assert elapsed < 1.0  # instant — never blocks spawn

    @pytest.mark.asyncio
    async def test_failure_timeout_returns_none(self):
        """TRIPLE leg 2 — invoke HANGS: the 3.0s bound cancels it
        (real asyncio.wait_for over a sleeping invoke); None out."""
        mgr = _manager()

        cap = _Capture()
        cap.timeout_captured = None

        class _Hanging:
            def __init__(self, **kwargs):
                pass

        def fake_failover(_llm, raw_config):
            def invoke(_messages):
                cap.invoke_calls += 1
                time.sleep(10.0)  # hangs past the bound
                return _content("late")

            return SimpleNamespace(invoke=invoke)

        svc = RoomNamingService(mgr)
        with patch(
            "daemon.services.room_naming.ThinkingChatOpenAI",
            _Hanging,
        ), patch(
            "daemon.services.room_naming.wrap_langchain_failover",
            fake_failover,
        ):  # REAL asyncio.wait_for — the bound must actually fire
            started = time.monotonic()
            out = await svc.generate_member_name(agent_id="capture")
            elapsed = time.monotonic() - started
        assert out is None
        assert 2.9 <= elapsed <= 6.0  # bounded ≈3.0s, not 10s

    @pytest.mark.asyncio
    async def test_unavailable_config_returns_none_instant(self):
        """TRIPLE leg 3 — the quick lane UNAVAILABLE by configuration
        (no model_quick AND no model): instant None, ZERO invokes."""
        mgr = _manager(model_quick=None)
        mgr.config.llm.model = ""
        cap = _Capture(invoke_result=_content("scout"))
        svc = RoomNamingService(mgr)
        with patch(
            "daemon.services.room_naming.ThinkingChatOpenAI",
            cap.fake_llm_cls,
        ), patch(
            "daemon.services.room_naming.wrap_langchain_failover",
            cap.fake_failover,
        ):
            started = time.monotonic()
            out = await svc.generate_member_name(agent_id="capture")
            elapsed = time.monotonic() - started
        assert out is None
        assert cap.invoke_calls == 0
        assert elapsed < 0.5


# ── the full cascade orchestration ─────────────────────────────────────────


class TestCascade:
    @pytest.mark.asyncio
    async def test_tier1_provided_never_touches_model(self):
        svc = RoomNamingService(_manager())
        with patch.object(
            svc, "generate_member_name", side_effect=AssertionError(
                "tier 1 must not call the model lane"
            )
        ):
            name, tier = await svc.resolve_base_name(
                provided="Lead Capture", agent_id="capture"
            )
        assert name == "lead-capture"
        assert tier == "parent_provided"

    @pytest.mark.asyncio
    async def test_tier2_quick_model_label(self):
        svc = RoomNamingService(_manager())
        with patch.object(
            svc, "generate_member_name", return_value="sampler"
        ):
            name, tier = await svc.resolve_base_name(
                provided=None, agent_id="capture"
            )
        assert name == "sampler"
        assert tier == "quick_model"

    @pytest.mark.asyncio
    async def test_tier3_agent_fallback_instant(self):
        """TRIPLE-composed: tier-2 failure ⇒ deterministic agent-id
        base, instant, labeled agent_fallback."""
        mgr = _manager()
        cap = _Capture(invoke_exc=RuntimeError("down"))
        svc = RoomNamingService(mgr)
        with patch(
            "daemon.services.room_naming.ThinkingChatOpenAI",
            cap.fake_llm_cls,
        ), patch(
            "daemon.services.room_naming.wrap_langchain_failover",
            cap.fake_failover,
        ), patch(
            "daemon.services.room_naming.asyncio.wait_for",
            cap.fake_wait_for,
        ):
            started = time.monotonic()
            name, tier = await svc.resolve_base_name(
                provided=None, agent_id="ui_capture"
            )
            elapsed = time.monotonic() - started
        assert name == "ui-capture"
        assert tier == "agent_fallback"
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_dirty_provided_falls_through_to_tier2(self):
        """An UNUSABLE provided name (!! whitespace-only) is tier-1
        failure → the cascade continues to tier 2 (never invents)."""
        svc = RoomNamingService(_manager())
        with patch.object(svc, "generate_member_name", return_value=None):
            name, tier = await svc.resolve_base_name(
                provided="!!!", agent_id="capture"
            )
        assert name == "capture"
        assert tier == "agent_fallback"

    def test_fallback_member_name_sanitizes_agent_id(self):
        svc = RoomNamingService(_manager())
        assert svc.fallback_member_name("capture") == "capture"
        assert svc.fallback_member_name("UI_Capture") == "ui-capture"
        assert svc.fallback_member_name("!!!") == "member"