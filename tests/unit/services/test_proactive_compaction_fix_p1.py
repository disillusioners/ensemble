"""Phase 1 (proactive-compaction-fix) — unit + AST anchors.

P1 scope (per
``.agents/shared/planning/proactive-compaction-fix/architecture-recommendation.md``
§3.1, §3.3+A.5, §3.5, §3.6, §3.7+A.2):

* **T1** Gate polarity — quiescent-shaped + running/idle/waiting_children
  proceeds; status ∈ reject-set → INFO skip; non-quiescent shape → INFO skip.
* **T3** AST persist-identity pin — shared seam (mid_turn=False) issues
  NO ``as_node=``; sentinel is element 0; two ordered writes, nothing
  between.
* **T4** Numerator/budget + anti-refire acceptance — injected counted
  in numerator AND budget; injections-dominate → skip + single
  rate-limited WARN + ``compacted_at`` stamped; assert dedup engages.
* **T6** Anti-drift — ONE frozenset, THREE importers (command_dispatcher
  gate, proactive gate, tests); canonical ``TERMINAL_INSTANCE_STATUSES``
  tripwire stays green.
* **T7** Observability anchors — INFO skip logs + WARN ≥90%.
* **T8** ``_compute_context_usage`` output unchanged.

Architecture references:

* ``daemon/services/instance_messaging.py`` — proactive gate site.
* ``daemon/services/compact_executor.py`` — on-demand executor.
* ``daemon/services/command_dispatcher.py`` — frozenset source of truth.
* ``daemon/services/_compaction_persist_seam.py`` — shared persist seam.
* ``daemon/compaction.py`` — engine; numerator/budget + anti-refire
  stamp mechanics.
* ``daemon/config.py`` — ``proactive_enabled`` flag (env
  ``ENSEMBLE_PROACTIVE_COMPACTION``, default ON).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import textwrap
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.compaction import (
    CompactionContext,
    CompactionResult,
    ContextCompactor,
    SystemMessage,
    identify_boundary_groups,
    select_compactable_groups,
)
from daemon.config import CompactionConfig as CompactionConfigModel
from daemon.config import load_config
from daemon.loader import estimate_messages_tokens
from daemon.services import _compaction_persist_seam as seam_mod
from daemon.services import compact_executor as ce
from daemon.services import instance_messaging as im
from daemon.services.command_dispatcher import (
    COMPACT_REJECT_STATUSES,
    CommandDispatcher,
)
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.cancellation import CancellationService
from langchain_core.messages import AIMessage, HumanMessage


# =============================================================================
# Helpers
# =============================================================================


def make_compaction_config(**overrides: Any) -> CompactionConfigModel:
    """CompactionConfig with optional overrides (mirror test_compaction.py)."""
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
        "proactive_enabled": True,
    }
    defaults.update(overrides)
    return CompactionConfigModel(**defaults)


def make_messages(n: int, content_prefix: str = "M") -> list:
    """Alternate human/ai messages."""
    out = []
    for i in range(n):
        cls = HumanMessage if i % 2 == 0 else AIMessage
        out.append(cls(content=f"{content_prefix} {i}", id=f"m-{i}"))
    return out


def _make_injected_messages(n: int) -> list:
    """Build HumanMessages flagged with the injected_message kwarg."""
    return [
        HumanMessage(
            content=f"[injected] {i}",
            id=f"inj-{i}",
            additional_kwargs={"injected_message": True},
        )
        for i in range(n)
    ]


def _make_context(
    config: CompactionConfigModel,
    messages: list,
    model_name: str = "gpt-4o",
    last_compacted_at: str | None = None,
) -> CompactionContext:
    return CompactionContext(
        messages=messages,
        system_prompt_tokens=0,
        model_name=model_name,
        config=config,
        llm_config={
            "base_url": "http://localhost:1234/v1",
            "api_key": "k",
            "model": model_name,
            "model_vision": model_name,
            "temperature": 0.7,
            "request_timeout": 30.0,
        },
        last_compacted_at=last_compacted_at,
        instance_id="p1-test-iid",
    )


def _build_service(
    *,
    manager: MagicMock | None = None,
) -> tuple[InstanceMessagingService, MagicMock]:
    """Build a real ``InstanceMessagingService`` against a mocked
    ``InstanceManager`` facade. Returns ``(service, manager)``.
    """
    if manager is None:
        manager = MagicMock()
    # The ``_config`` property reads ``manager.config``; the
    # compaction reads ``manager.config.compaction``. The proactive
    # gate reads ``self._config.compaction.proactive_enabled``.
    manager.config = MagicMock()
    manager.config.compaction = make_compaction_config()
    manager.config.llm.model = "gpt-4o"
    # CancellationService is required at __init__.
    manager_cancellation = MagicMock()
    manager_cancellation.manager = manager
    svc = InstanceMessagingService(
        manager=manager,
        cancellation_service=manager_cancellation,
    )
    return svc, manager


# =============================================================================
# T1 — Gate polarity (status + inverted shape)
# =============================================================================


class TestT1GatePolarity:
    """Phase 1 / T1 — the proactive gate is polarity-correct.

    The OLD code rejected every quiescent checkpoint (terminal-shape
    gate) and therefore never fired. The NEW code:
    * skips at INFO if instance status ∈ COMPACT_REJECT_STATUSES,
    * skips at INFO if checkpoint is NOT quiescent-shaped
      (state.next non-empty),
    * proceeds when both conditions are clear (quiescent checkpoint +
      non-terminal status).
    """

    @pytest.mark.asyncio
    async def test_status_reject_set_skips_at_info(self, caplog):
        """Status ∈ reject-set → INFO skip; engine is NEVER called."""
        mgr = MagicMock()
        mgr._instance_repository.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        mgr._compactor = MagicMock()
        mgr._compactor.compact_state = AsyncMock()
        svc, _ = _build_service(manager=mgr)
        graph = MagicMock()
        # graph.aget_state must be AsyncMock for ``await_count`` to
        # surface a real int — see the test_off_skips_entire_gate
        # commentary.
        graph.aget_state = AsyncMock()
        with caplog.at_level(logging.INFO, logger="daemon.services.instance_messaging"):
            await svc._maybe_compact_context("inst-status-skip", graph, {})
        # Engine is NEVER invoked on status-reject path.
        assert mgr._compactor.compact_state.await_count == 0, (
            "status gate must short-circuit BEFORE the engine call"
        )
        # The graph's aget_state is NEVER consulted on status-reject path.
        assert graph.aget_state.await_count == 0
        # Status-skip log carries the documented INFO string.
        assert any(
            "terminal-status" in r.getMessage()
            and "terminated" in r.getMessage()
            for r in caplog.records
        ), (
            f"expected 'terminal-status' INFO log; got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_running_status_proceeds_to_engine(self):
        """Running status + quiescent checkpoint → engine is invoked."""
        mgr = MagicMock()
        mgr._instance_repository.get = MagicMock(
            return_value=MagicMock(status="running")
        )
        mgr._compactor = MagicMock()
        mgr._compactor._trigger_window = MagicMock(return_value=1_000_000)
        mgr._compactor.compact_state = AsyncMock(return_value=None)
        mgr.message_metadata_repo = None  # tap path skipped
        svc, _ = _build_service(manager=mgr)
        svc._get_system_prompt_tokens = AsyncMock(return_value=0)
        graph = MagicMock()
        graph.aget_state = AsyncMock(
            return_value=MagicMock(
                values={"messages": make_messages(20)},
                next=(),  # quiescent
            )
        )
        await svc._maybe_compact_context("inst-running", graph, {})
        assert mgr._compactor.compact_state.await_count == 1, (
            "running + quiescent: engine must be invoked"
        )

    @pytest.mark.asyncio
    async def test_idle_status_proceeds_to_engine(self):
        """Idle status + quiescent checkpoint → engine is invoked
        (idle is NOT in the reject set).
        """
        mgr = MagicMock()
        mgr._instance_repository.get = MagicMock(
            return_value=MagicMock(status="idle")
        )
        mgr._compactor = MagicMock()
        mgr._compactor._trigger_window = MagicMock(return_value=1_000_000)
        mgr._compactor.compact_state = AsyncMock(return_value=None)
        mgr.message_metadata_repo = None
        svc, _ = _build_service(manager=mgr)
        svc._get_system_prompt_tokens = AsyncMock(return_value=0)
        graph = MagicMock()
        graph.aget_state = AsyncMock(
            return_value=MagicMock(
                values={"messages": make_messages(20)},
                next=(),
            )
        )
        await svc._maybe_compact_context("inst-idle", graph, {})
        assert mgr._compactor.compact_state.await_count == 1

    @pytest.mark.asyncio
    async def test_waiting_children_status_proceeds_to_engine(self):
        """Waiting children status + quiescent → engine invoked."""
        mgr = MagicMock()
        mgr._instance_repository.get = MagicMock(
            return_value=MagicMock(status="waiting_children")
        )
        mgr._compactor = MagicMock()
        mgr._compactor._trigger_window = MagicMock(return_value=1_000_000)
        mgr._compactor.compact_state = AsyncMock(return_value=None)
        mgr.message_metadata_repo = None
        svc, _ = _build_service(manager=mgr)
        svc._get_system_prompt_tokens = AsyncMock(return_value=0)
        graph = MagicMock()
        graph.aget_state = AsyncMock(
            return_value=MagicMock(
                values={"messages": make_messages(20)},
                next=(),
            )
        )
        await svc._maybe_compact_context("inst-wait", graph, {})
        assert mgr._compactor.compact_state.await_count == 1

    @pytest.mark.asyncio
    async def test_completed_status_proceeds_to_engine(self):
        """Completed status + quiescent → engine invoked
        (compact-on-COMPLETED).
        """
        mgr = MagicMock()
        mgr._instance_repository.get = MagicMock(
            return_value=MagicMock(status="completed")
        )
        mgr._compactor = MagicMock()
        mgr._compactor._trigger_window = MagicMock(return_value=1_000_000)
        mgr._compactor.compact_state = AsyncMock(return_value=None)
        mgr.message_metadata_repo = None
        svc, _ = _build_service(manager=mgr)
        svc._get_system_prompt_tokens = AsyncMock(return_value=0)
        graph = MagicMock()
        graph.aget_state = AsyncMock(
            return_value=MagicMock(
                values={"messages": make_messages(20)},
                next=(),
            )
        )
        await svc._maybe_compact_context("inst-completed", graph, {})
        assert mgr._compactor.compact_state.await_count == 1

    @pytest.mark.asyncio
    async def test_non_quiescent_shape_skips_at_info(self, caplog):
        """Non-quiescent shape (state.next != ()) → INFO skip."""
        mgr = MagicMock()
        mgr._instance_repository.get = MagicMock(
            return_value=MagicMock(status="running")
        )
        mgr._compactor = MagicMock()
        mgr._compactor.compact_state = AsyncMock()
        svc, _ = _build_service(manager=mgr)
        graph = MagicMock()
        graph.aget_state = AsyncMock(
            return_value=MagicMock(
                values={"messages": make_messages(5)},
                next=("agent",),  # NOT quiescent
            )
        )
        with caplog.at_level(logging.INFO, logger="daemon.services.instance_messaging"):
            await svc._maybe_compact_context("inst-nonquiescent", graph, {})
        assert mgr._compactor.compact_state.await_count == 0, (
            "non-quiescent shape must skip the engine call"
        )
        assert any(
            "non-quiescent" in r.getMessage() for r in caplog.records
        ), (
            f"expected 'non-quiescent' INFO log; got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_error_status_skips_at_info(self, caplog):
        """Error status → INFO skip."""
        mgr = MagicMock()
        mgr._instance_repository.get = MagicMock(
            return_value=MagicMock(status="error")
        )
        mgr._compactor = MagicMock()
        mgr._compactor.compact_state = AsyncMock()
        svc, _ = _build_service(manager=mgr)
        graph = MagicMock()
        graph.aget_state = AsyncMock()
        with caplog.at_level(logging.INFO, logger="daemon.services.instance_messaging"):
            await svc._maybe_compact_context("inst-error", graph, {})
        assert mgr._compactor.compact_state.await_count == 0
        assert graph.aget_state.await_count == 0
        assert any("error" in r.getMessage() for r in caplog.records)


# =============================================================================
# T3 — AST persist-identity pin (shared seam mid_turn=False)
# =============================================================================


class TestT3ASTPersistIdentityPin:
    """T3 — the shared seam (mid_turn=False) emits Variant A.

    Pinned at the source-AST level so a future regression that adds
    ``as_node=`` to the executor / proactive path is caught at code
    review.
    """

    def test_seam_mid_turn_false_first_aupdate_omits_as_node(self):
        """The mid_turn=False STAMP-ONLY arm omits ``as_node=`` —
        Variant A, byte-equivalent to the executor's pre-P1 recipe.

        P1b note: the stamp-only path now has TWO arms — the
        mid_turn=True arm (which MUST carry ``as_node='agent'``, fixed
        in P1b: the P1 code embedded ``as_node`` inside the STATE dict)
        and the mid_turn=False arm (which must NOT carry it). This test
        pins the mid_turn=False arm; the mid_turn=True stamp arm is
        pinned by ``test_seam_mid_turn_true_stamp_uses_as_node`` (P1b)
        and the normal-path arms by the sibling tests below.
        """
        src = inspect.getsource(seam_mod.persist_compaction_result)
        # The FIRST ``if mid_turn:`` is the stamp-only path's split.
        first_mid_turn_idx = src.find("if mid_turn:")
        assert first_mid_turn_idx >= 0
        # Its ``else:`` (the mid_turn=False stamp-only arm).
        else_idx = src.find("\n            else:", first_mid_turn_idx)
        assert else_idx >= 0, (
            "expected an else branch after the stamp-only if mid_turn:"
        )
        # The arm ends at the normal-path comment/section — slice to
        # the standard-path marker.
        end_marker = src.find("# Standard Variant A / Variant B path.", else_idx)
        arm = src[else_idx:end_marker if end_marker > 0 else len(src)]
        aupdate_indices = [
            i for i in range(len(arm))
            if arm.startswith("await graph.aupdate_state(", i)
        ]
        assert len(aupdate_indices) == 1, (
            f"mid_turn=False stamp-only arm must have exactly 1 "
            f"aupdate_state call; got {len(aupdate_indices)}"
        )
        call = arm[aupdate_indices[0]:]
        call = call[: call.find(")") + 1]
        assert "as_node" not in call, (
            f"shared seam mid_turn=False stamp-only arm must omit "
            f"as_node (C1 Variant A); got: {call!r}"
        )

    def test_seam_mid_turn_true_stamp_uses_as_node(self):
        """P1b: the mid_turn=True STAMP-ONLY arm passes ``as_node`` as
        the langgraph KEYWORD argument (not inside the state dict — the
        P1 bug this fixes; first exercised by the 95% hook)."""
        src = inspect.getsource(seam_mod.persist_compaction_result)
        first_mid_turn_idx = src.find("if mid_turn:")
        assert first_mid_turn_idx >= 0
        else_idx = src.find("\n            else:", first_mid_turn_idx)
        mid_section = src[first_mid_turn_idx:else_idx]
        assert "as_node=\"agent\"" in mid_section, (
            "mid_turn=True stamp-only arm must pass as_node='agent' as "
            "the aupdate_state keyword argument"
        )
        # And it must be a KEYWORD arg, not a state-dict key: the call
        # must NOT embed as_node inside the update dict literal.
        call_idx = mid_section.find("await graph.aupdate_state(")
        call = mid_section[call_idx: mid_section.find(")", call_idx)]
        assert "stamp_update" in call and "as_node" not in call.split(
            "stamp_update"
        )[0], (
            "as_node must be passed as a keyword after the update dict, "
            "not embedded in the state dict"
        )

    def test_seam_emits_two_ordered_writes_messages_then_compacted_at(self):
        """The seam emits exactly TWO ``aupdate_state`` calls in the
        mid_turn=False arm (the second ``else`` branch — the one
        inside the ``if mid_turn: ... else:`` split): messages first,
        compacted_at second. The anti-refire stamp-only path is a
        separate ``else`` earlier (one call, compacted_at only) and
        is pinned separately.
        """
        src = inspect.getsource(seam_mod.persist_compaction_result)
        # Locate the LAST ``if mid_turn:`` (the one inside the
        # normal path, NOT the stamp-only path).
        mid_turn_idx = src.rfind("if mid_turn:")
        assert mid_turn_idx >= 0
        # The ``else`` AFTER the LAST ``if mid_turn:`` is the
        # mid_turn=False arm. Use rfind for robustness.
        else_idx = src.find("\n    else:", mid_turn_idx)
        assert else_idx >= 0, "expected an else branch after if mid_turn:"
        non_mid_section = src[else_idx:]
        aupdate_indices = [
            i for i in range(len(non_mid_section))
            if non_mid_section.startswith("await graph.aupdate_state(", i)
        ]
        assert len(aupdate_indices) == 2, (
            f"expected exactly 2 aupdate_state calls in the mid_turn=False "
            f"arm (messages + compacted_at); got {len(aupdate_indices)}"
        )
        # The first call carries messages; the second carries
        # compacted_at. Pin via a substring check on each window.
        first_call_window = non_mid_section[
            aupdate_indices[0]:aupdate_indices[0] + 200
        ]
        second_call_window = non_mid_section[
            aupdate_indices[1]:aupdate_indices[1] + 200
        ]
        first_call_end = first_call_window.find(")")
        first_call = first_call_window[:first_call_end]
        second_call_end = second_call_window.find(")")
        second_call = second_call_window[:second_call_end]
        assert "'messages'" in first_call or '"messages"' in first_call, (
            f"first aupdate_state must carry messages; got: {first_call!r}"
        )
        assert (
            "'compacted_at'" in second_call or '"compacted_at"' in second_call
        ), (
            f"second aupdate_state must carry compacted_at; got: {second_call!r}"
        )

    def test_seam_mid_turn_true_uses_as_node(self):
        """The mid_turn=True arm carries ``as_node='agent'`` (Variant B
        — for the CLE handler / future 95% hook).

        The seam has TWO ``if mid_turn:`` branches (one in the
        anti-refire stamp-only path, one in the normal path). We pin
        the LATER one — that's where the Variant A/B split lives.
        """
        src = inspect.getsource(seam_mod.persist_compaction_result)
        # rfind to locate the LAST ``if mid_turn:`` (the one inside
        # the normal path, not the stamp-only path).
        mid_turn_idx = src.rfind("if mid_turn:")
        assert mid_turn_idx >= 0
        # The mid_turn=True arm spans from ``if mid_turn:`` to the
        # NEXT ``else:`` keyword AFTER the second ``if mid_turn:``.
        else_idx = src.find("\n    else:", mid_turn_idx)
        assert else_idx >= 0
        mid_section = src[mid_turn_idx:else_idx]
        aupdate_indices = [
            i for i in range(len(mid_section))
            if mid_section.startswith("await graph.aupdate_state(", i)
        ]
        assert len(aupdate_indices) == 2, (
            f"mid_turn=True arm must have 2 aupdate_state calls; "
            f"got {len(aupdate_indices)}"
        )
        first_call_window = mid_section[
            aupdate_indices[0]:aupdate_indices[0] + 200
        ]
        first_call_end = first_call_window.find(")")
        first_call = first_call_window[:first_call_end]
        assert "as_node" in first_call, (
            f"mid_turn=True arm must use as_node='agent' "
            f"(Variant B — mid-superstep); got: {first_call!r}"
        )


# =============================================================================
# T4 — Numerator/budget + anti-refire acceptance
# =============================================================================


class TestT4NumeratorBudgetAntiRefire:
    """T4 — injected counted in numerator AND budget; injections-dominate
    → skip + WARN + ``compacted_at`` stamped; dedup engages.
    """

    @pytest.mark.asyncio
    async def test_numerator_includes_injected_tokens(self):
        """Total token count includes injected tokens.

        We make the regular messages large enough that the regular-
        only token sum does NOT cross the threshold, while the
        total (regular + injected) DOES cross. The OLD pre-P1
        numerator (regular-only) would skip; the NEW numerator
        counts injected and compacts.
        """
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.50,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 500},
        )
        # 4 regular messages, each padded to be substantial but under
        # the threshold (4 * 50 = ~200 tokens; < 250 = 50% of 500).
        regular = [
            HumanMessage(content="x" * 200, id=f"reg-{i}")
            for i in range(4)
        ]
        # 4 injected messages of similar size — push the total above
        # 250 tokens (50% threshold of 500).
        injected = [
            HumanMessage(
                content="y" * 200,
                id=f"inj-{i}",
                additional_kwargs={"injected_message": True},
            )
            for i in range(4)
        ]
        messages = regular + injected

        regular_only_tokens = estimate_messages_tokens(regular)
        injected_only_tokens = estimate_messages_tokens(injected)
        threshold_tokens = 500 * 0.50  # 250

        assert regular_only_tokens < threshold_tokens, (
            "fixture: regular-only must NOT cross threshold (pre-P1 "
            "engine would have skipped this case)"
        )
        assert (
            regular_only_tokens + injected_only_tokens > threshold_tokens
        ), "fixture: regular+injected MUST cross threshold (post-P1 trigger)"

        compactor = ContextCompactor(config, {})

        async def _fake_chunked(compactable, context):
            class _O:
                summaries = [SystemMessage(content="[S]\nall", id="doc")]
                failed_batches = []
                stop_reason = "completed"
            return _O()

        compactor._summarize_chunked = _fake_chunked
        result = await compactor.compact_state(_make_context(config, messages))
        assert result is not None, (
            "engine must compact when total tokens (incl. injected) cross "
            "threshold — the L3 fix is to count ALL tokens, not just regular"
        )
        # tokens_before must exceed regular-only sum (the OLD pre-P1
        # numerator would have under-counted by the injected share).
        assert result.tokens_before > regular_only_tokens, (
            f"tokens_before ({result.tokens_before}) must exceed the "
            f"regular-only sum ({regular_only_tokens}); the gate now "
            f"counts injected tokens"
        )

    def test_selection_budget_includes_injected_tokens(self):
        """``select_compactable_groups`` honors ``injected_tokens`` in
        the budget check: an injection-heavy conversation cannot
        compact into the threshold even with all regular groups
        dropped.

        Pin: with ``injected_tokens = 7000`` (~70% of window) and
        regular groups that together would comfortably fit under
        threshold if injected were ignored, the budget MUST add
        injected_tokens to the comparison so the function cannot
        return "fits" when it would actually exceed.
        """
        # Large regular set so the OLD pre-P1 logic (without injected
        # in budget) would consider the budget trivially satisfied.
        groups = identify_boundary_groups(make_messages(50))
        context_window = 10_000
        system_prompt_tokens = 0
        threshold = 0.80  # 8000 threshold tokens
        injected_tokens = 7_000  # 70% — leaves little room

        # Pre-P1 baseline: WITHOUT injected in the budget, the
        # preserved side at min_window=1 may be small enough to fit
        # without consideration of injected. We assert the new
        # behavior: the function uses injected_tokens in the
        # comparison, so the answer differs.
        compactable_pre, preserved_pre, _ = select_compactable_groups(
            groups,
            recent_window=3,
            min_window=1,
            context_window=context_window,
            system_prompt_tokens=system_prompt_tokens,
            estimate_fn=estimate_messages_tokens,
            config_threshold=threshold,
            injected_tokens=0,  # pre-P1 behavior baseline
        )
        compactable_post, preserved_post, _ = select_compactable_groups(
            groups,
            recent_window=3,
            min_window=1,
            context_window=context_window,
            system_prompt_tokens=system_prompt_tokens,
            estimate_fn=estimate_messages_tokens,
            config_threshold=threshold,
            injected_tokens=injected_tokens,
        )
        # Sanity: both return valid structures.
        assert isinstance(compactable_pre, list)
        assert isinstance(preserved_post, list)

        # Core assertion: the post-P1 preserved_total (preserved_tokens
        # + injected_tokens) must be ≤ threshold_tokens. The function
        # reduces the preserved window until this holds; the only way
        # to violate the contract is if injected is NOT added.
        preserved_tokens_post = estimate_messages_tokens(
            [msg for g in preserved_post for msg in g.messages]
        )
        preserved_total_post = preserved_tokens_post + injected_tokens
        threshold_tokens = int(context_window * threshold)
        assert preserved_total_post <= threshold_tokens + 50, (
            f"post-P1 budget must respect injected: preserved_total="
            f"{preserved_total_post} should be <= threshold_tokens="
            f"{threshold_tokens} (small tolerance for token-count noise)"
        )

        # The OLD (injected_tokens=0) and NEW (injected_tokens=7000)
        # answers MAY legitimately agree (if the regular pool is so
        # small that the new budget still fits) — but with 7000
        # injected and 50 regular messages, the new budget must
        # reduce the preserved window further than the old one.
        preserved_tokens_pre = estimate_messages_tokens(
            [msg for g in preserved_pre for msg in g.messages]
        )
        assert preserved_tokens_post <= preserved_tokens_pre, (
            "post-P1 budget must preserve fewer tokens than pre-P1 "
            f"(injected eats into the budget): pre={preserved_tokens_pre}, "
            f"post={preserved_tokens_post}"
        )

    @pytest.mark.asyncio
    async def test_all_injected_anti_refire_stamps_compacted_at(self):
        """All-injected skip path stamps ``compacted_at`` so the
        60s dedup engages on the next dispatch.
        """
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,  # very low so we'd otherwise trigger
        )
        messages = _make_injected_messages(5)  # all injected
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(_make_context(config, messages))
        assert result is not None, (
            "anti-refire: engine must return a STAMPED no-op, NOT None"
        )
        assert result.compaction_type == "skipped_injections_dominate"
        assert result.replacement_messages == []
        assert result.compacted_at is not None

    @pytest.mark.asyncio
    async def test_min_messages_anti_refire_stamps_compacted_at(self):
        """min_messages skip path stamps ``compacted_at``."""
        config = make_compaction_config(
            min_messages_before_compaction=10,
            threshold=0.01,
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(
            _make_context(config, make_messages(5))
        )
        assert result is not None
        assert result.compaction_type == "skipped_below_min_messages"
        assert result.compacted_at is not None

    @pytest.mark.asyncio
    async def test_dedup_engages_after_anti_refire_stamp(self):
        """A subsequent dispatch carrying the stamped ``compacted_at``
        within 60s hits the dedup and returns None un-stamped. This
        closes the per-dispatch refire loop the doc §3.5 names.
        """
        config = make_compaction_config(
            min_messages_before_compaction=10,
            threshold=0.01,
        )
        compactor = ContextCompactor(config, {})

        msgs_inj = _make_injected_messages(5)
        first_result = await compactor.compact_state(
            _make_context(config, msgs_inj)
        )
        assert first_result is not None
        stamped = first_result.compacted_at
        assert stamped, "anti-refire must stamp compacted_at"

        msgs_regular = make_messages(20)
        second_result = await compactor.compact_state(
            _make_context(
                config,
                msgs_regular,
                last_compacted_at=stamped,
            )
        )
        assert second_result is None, (
            "dedup must engage: the anti-refire stamp from the first "
            "dispatch lands in last_compacted_at and the second "
            "dispatch should be deduped within 60s"
        )


# =============================================================================
# T6 — Anti-drift: ONE frozenset, THREE importers
# =============================================================================


class TestT6AntiDrift:
    """T6 — ``COMPACT_REJECT_STATUSES`` has ONE definition and THREE
    importers (the dispatcher gate, the proactive gate, the tests).
    The canonical ``TERMINAL_INSTANCE_STATUSES`` tripwire stays green.
    """

    def test_one_frozenset_definition(self):
        """The frozenset is defined in EXACTLY ONE place:
        ``daemon/services/command_dispatcher.py``.
        """
        from pathlib import Path

        repo_root = Path(
            os.environ.get(
                "REPO_ROOT",
                "/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble",
            )
        )
        import ast

        definitions: list[tuple[str, int]] = []
        for py in (repo_root / "daemon").rglob("*.py"):
            try:
                tree = ast.parse(py.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.AnnAssign) and isinstance(
                    node.target, ast.Name
                ) and node.target.id == "COMPACT_REJECT_STATUSES":
                    definitions.append(
                        (str(py.relative_to(repo_root)), node.lineno)
                    )
        assert len(definitions) == 1, (
            f"expected ONE COMPACT_REJECT_STATUSES definition; got "
            f"{definitions}"
        )
        assert "command_dispatcher" in definitions[0][0], (
            f"the frozenset must be defined in command_dispatcher.py; "
            f"got {definitions[0][0]}"
        )

    def test_proactive_site_imports_frozenset(self):
        """The proactive site (instance_messaging) imports the
        frozenset from command_dispatcher — not a duplicate.
        """
        from pathlib import Path

        repo_root = Path(
            os.environ.get(
                "REPO_ROOT",
                "/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble",
            )
        )
        import ast

        importers: list[tuple[str, int]] = []
        for py in (repo_root / "daemon" / "services").rglob("*.py"):
            if py.name == "command_dispatcher.py":
                continue
            try:
                tree = ast.parse(py.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and node.module.endswith("command_dispatcher")
                    and any(
                        alias.name == "COMPACT_REJECT_STATUSES"
                        for alias in node.names
                    )
                ):
                    importers.append(
                        (str(py.relative_to(repo_root)), node.lineno)
                    )
        paths = [p for p, _ in importers]
        # The proactive site MUST be an importer (Phase 1 unification).
        assert any("instance_messaging" in p for p in paths), (
            f"proactive site (instance_messaging) must import "
            f"COMPACT_REJECT_STATUSES — Phase 1 T6 anti-drift; "
            f"got importers: {paths}"
        )
        # The compact_executor (defense-in-depth guard) was an
        # importer pre-Phase-1; still imported post-Phase-1.
        assert any("compact_executor" in p for p in paths), (
            f"compact_executor defense-in-depth guard must remain an "
            f"importer; got: {paths}"
        )

    def test_canonical_terminal_instance_statuses_untouched(self):
        """The canonical ``daemon.constants.TERMINAL_INSTANCE_STATUSES``
        set is UNCHANGED (sibling-frozenset convention).
        """
        from daemon.constants import TERMINAL_INSTANCE_STATUSES
        # The canonical set keeps ``completed`` in its membership
        # (compact-on-COMPLETED reversal: completed is NOT terminal for
        # the canonical set, only for the COMPACT_REJECT_STATUSES
        # sibling).
        assert "completed" in TERMINAL_INSTANCE_STATUSES, (
            "TERMINAL_INSTANCE_STATUSES must keep 'completed' in its "
            "membership per the sibling-frozenset convention"
        )
        assert isinstance(TERMINAL_INSTANCE_STATUSES, frozenset)
        assert len(TERMINAL_INSTANCE_STATUSES) >= 4

    def test_compact_reject_statuses_value(self):
        """The frozenset membership is the documented triple:
        terminated / error / failed. ``completed`` is INTENTIONALLY
        excluded (compact-on-COMPLETED).
        """
        assert COMPACT_REJECT_STATUSES == frozenset(
            {"terminated", "error", "failed"}
        )
        assert "completed" not in COMPACT_REJECT_STATUSES


# =============================================================================
# T7 — Observability anchors (INFO skip + WARN ≥90%)
# =============================================================================


class TestT7Observability:
    """T7 — observability strings are greppable + match the doc."""

    def test_status_skip_log_contains_substring(self):
        """The status-skip log line is greppable — ``skipping proactive on
        terminal-status`` (split across the f-string for line-length
        in the source).
        """
        src = inspect.getsource(im.InstanceMessagingService._maybe_compact_context)
        # The f-string is line-wrapped in source; search for the
        # semantic substrings that survive wrapping.
        assert "skipping proactive on terminal" in src, (
            "status-skip log string not found in proactive source"
        )
        assert "status=%s" in src or "status=" in src, (
            "status field placeholder not found in status-skip log"
        )

    def test_shape_skip_log_contains_substring(self):
        """The shape-skip log line is greppable — ``skipping proactive on
        non-quiescent``.
        """
        src = inspect.getsource(im.InstanceMessagingService._maybe_compact_context)
        assert "skipping proactive on non-quiescent" in src, (
            "shape-skip log string not found in proactive source"
        )

    def test_warn_at_90_percent_threshold_substring(self):
        """The ≥90% threshold WARN is present in the proactive site.
        """
        src = inspect.getsource(im.InstanceMessagingService._maybe_compact_context)
        assert "90%" in src or "0.90" in src, (
            "≥90% threshold WARN not present in proactive source"
        )


# =============================================================================
# T8 — ``_compute_context_usage`` output unchanged
# =============================================================================


class TestT8ComputeContextUsage:
    """T8 — FE badge contract unchanged: ``_compute_context_usage``
    counts ALL messages (including injected).
    """

    @pytest.mark.asyncio
    async def test_compute_context_usage_counts_all_messages(self):
        """The badge includes injected messages in the token count."""
        mgr = MagicMock()
        mgr._instance_repository.get = MagicMock(return_value=None)
        svc, _ = _build_service(manager=mgr)
        # System prompt resolver returns 0 — no extra tokens.
        svc._get_system_prompt_tokens = AsyncMock(return_value=0)
        all_msgs = make_messages(20) + _make_injected_messages(5)
        tokens, window, model = await svc._compute_context_usage(
            "inst-badge", all_msgs
        )
        regular_only = estimate_messages_tokens(make_messages(20))
        assert tokens >= regular_only, (
            f"_compute_context_usage tokens ({tokens}) must be >= "
            f"regular-only sum ({regular_only}); injected messages "
            f"must be counted"
        )
        assert model == "gpt-4o"


# =============================================================================
# proactive_enabled flag default + env (T-flag §3.7+A.2)
# =============================================================================


class TestProactiveEnabledFlag:
    """Phase 1 / §3.7+A.2 — ``compaction.proactive_enabled`` flag with
    env ``ENSEMBLE_PROACTIVE_COMPACTION``, default ON.
    """

    def test_default_on(self):
        cfg = CompactionConfigModel()
        assert cfg.proactive_enabled is True, (
            "proactive_enabled must default ON per ADDENDUM §A.2"
        )

    def test_env_zero_disables(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_PROACTIVE_COMPACTION", "0")
        cfg = CompactionConfigModel()
        assert cfg.proactive_enabled is False

    def test_env_false_disables(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_PROACTIVE_COMPACTION", "false")
        cfg = CompactionConfigModel()
        assert cfg.proactive_enabled is False

    def test_env_no_disables(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_PROACTIVE_COMPACTION", "no")
        cfg = CompactionConfigModel()
        assert cfg.proactive_enabled is False

    def test_env_one_enables(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_PROACTIVE_COMPACTION", "0")
        cfg0 = CompactionConfigModel()
        monkeypatch.setenv("ENSEMBLE_PROACTIVE_COMPACTION", "1")
        cfg1 = CompactionConfigModel()
        assert cfg0.proactive_enabled is False
        assert cfg1.proactive_enabled is True

    def test_env_blank_uses_default(self, monkeypatch):
        """Bare ``KEY=`` line in .env → default ON (not False).

        Implementation note: pydantic-settings converts empty env
        strings to ``None`` for non-string types BEFORE the
        ``mode="before"`` field validator runs, so we cannot
        intercept ``""`` for the bool field at the validator level.
        The user-facing behavior is preserved because pydantic's
        default (when the env resolves to ``None``) is the field
        default — which is ``True`` here. Pin the runtime behavior
        via the YAML path (no env, no yaml value → default ON).
        """
        # No env / no yaml → default ON.
        monkeypatch.delenv("ENSEMBLE_PROACTIVE_COMPACTION", raising=False)
        cfg = CompactionConfigModel()
        assert cfg.proactive_enabled is True, (
            "no-env / no-yaml must fall through to the documented "
            "ON default"
        )
        # Sanity: an explicit env override wins regardless of empty
        # values cleared earlier.
        monkeypatch.setenv("ENSEMBLE_PROACTIVE_COMPACTION", "0")
        cfg_off = CompactionConfigModel()
        assert cfg_off.proactive_enabled is False

    def test_yaml_can_set_explicitly(self, tmp_path, monkeypatch):
        """``compaction.proactive_enabled: false`` in yaml → False."""
        monkeypatch.delenv("ENSEMBLE_PROACTIVE_COMPACTION", raising=False)
        text = textwrap.dedent("""
            llm:
              base_url: "https://api.openai.com/v1"
              api_key: "k"
              model: "gpt-4"
            persistence:
              db_path: "./data/instances.db"
            compaction:
              proactive_enabled: false
        """).strip()
        path = tmp_path / "config.yaml"
        path.write_text(text)
        cfg = load_config(config_path=str(path))
        assert cfg.compaction.proactive_enabled is False


class TestProactiveGateKillSwitch:
    """When ``proactive_enabled=False``, the proactive site short-
    circuits BEFORE the status gate / shape gate / engine call.
    """

    @pytest.mark.asyncio
    async def test_off_skips_entire_gate(self):
        """OFF: compactor / status lookup / graph are NEVER touched."""
        mgr = MagicMock()
        mgr._instance_repository.get = MagicMock()
        mgr._compactor = MagicMock()
        mgr._compactor.compact_state = AsyncMock()
        svc, _ = _build_service(manager=mgr)
        # Flip the flag OFF.
        svc._config.compaction.proactive_enabled = False
        # Sanity: read it back.
        assert svc._config.compaction.proactive_enabled is False
        graph = MagicMock()
        # graph.aget_state must be AsyncMock for ``await_count`` to
        # surface a real int — a regular MagicMock attribute is
        # itself a MagicMock (not 0), which trips the assertion.
        graph.aget_state = AsyncMock()
        await svc._maybe_compact_context("inst-off", graph, {})
        assert mgr._compactor.compact_state.await_count == 0
        assert mgr._instance_repository.get.call_count == 0
        assert graph.aget_state.await_count == 0


# =============================================================================
# Shared seam: anti-refire stamp-only path
# =============================================================================


class TestSharedSeamStampOnlyPath:
    """When ``result.replacement_messages`` is empty (engine stamped a
    no-op), the seam writes ONLY the ``compacted_at`` stamp and does
    NOT touch the messages channel.
    """

    @pytest.mark.asyncio
    async def test_stamp_only_no_messages_aupdate(self):
        mgr = MagicMock()
        graph = MagicMock()
        mgr.get_instance = AsyncMock(return_value=graph)
        aupdate = AsyncMock()
        graph.aupdate_state = aupdate
        # Engine stamps a no-op (anti-refire):
        result = CompactionResult(
            replacement_messages=[],
            tokens_before=0,
            tokens_after=0,
            tokens_saved=0,
            messages_before=0,
            messages_after=0,
            compaction_type="skipped_injections_dominate",
            compacted_at="2026-09-04T00:00:00+00:00",
        )
        await seam_mod.persist_compaction_result(
            mgr,
            instance_id="inst-stamp-only",
            result=result,
            mid_turn=False,
        )
        # Exactly ONE aupdate_state call: the stamp.
        assert aupdate.await_count == 1
        # Find the call. AsyncMock positional args: (config, kwargs_dict)
        first_call_args, first_call_kwargs = aupdate.call_args
        # The aupdate_state call is graph.aupdate_state(config, {kwargs})
        # — config is the first positional arg, the dict is the second.
        # We assert the second positional arg carries compacted_at.
        assert len(first_call_args) >= 2
        kwargs_dict = first_call_args[1]
        assert "compacted_at" in kwargs_dict, (
            f"stamp-only path must carry compacted_at; got: {kwargs_dict!r}"
        )
        # No messages touched.
        assert "messages" not in kwargs_dict

    @pytest.mark.asyncio
    async def test_normal_path_emits_two_aupdates(self):
        """Normal Variant A path: two aupdate_state calls — messages,
        then compacted_at. NO as_node= (mid_turn=False).
        """
        mgr = MagicMock()
        graph = MagicMock()
        mgr.get_instance = AsyncMock(return_value=graph)
        # Pre-state: snapshot has 2 human messages (will be replaced).
        pre_state = MagicMock()
        pre_state.values = {
            "messages": [
                HumanMessage(content="a", id="ha"),
                HumanMessage(content="b", id="hb"),
            ]
        }
        graph.aget_state = AsyncMock(return_value=pre_state)
        aupdate = AsyncMock()
        graph.aupdate_state = aupdate
        result = CompactionResult(
            replacement_messages=[
                SystemMessage(id="compaction-doc-1", content="doc"),
                HumanMessage(content="b", id="hb"),  # tail id
            ],
            tokens_before=100,
            tokens_after=50,
            tokens_saved=50,
            messages_before=2,
            messages_after=2,
            compaction_type="summary",
            compacted_at="2026-09-04T00:00:00+00:00",
            compacted_ids=frozenset({"ha"}),
        )
        await seam_mod.persist_compaction_result(
            mgr,
            instance_id="inst-normal",
            result=result,
            mid_turn=False,
        )
        assert aupdate.await_count == 2
        first_call_args, _ = aupdate.call_args_list[0]
        second_call_args, _ = aupdate.call_args_list[1]
        # First call: messages (no as_node).
        first_kwargs = first_call_args[1]
        assert "messages" in first_kwargs
        assert "as_node" not in first_kwargs
        # Second call: compacted_at (no as_node).
        second_kwargs = second_call_args[1]
        assert "compacted_at" in second_kwargs
        assert "as_node" not in second_kwargs


# =============================================================================
# T2 — Revive canary (proactive entry)
# =============================================================================


class TestT2ReviveCanaryProactiveEntry:
    """T2 — proactive path uses Variant A persist (no ``as_node``).

    The proactive path consumes the shared seam with
    ``mid_turn=False``. The seam's mid_turn=False arm issues two
    ``aupdate_state`` calls WITHOUT ``as_node=``. The brick collapse
    (``as_node='agent'`` on a quiescent checkpoint clearing
    ``state.next``) is the documented hazard the executor already
    pinned via ``test_compact_executor_revive_brick_e2e.py``. This
    test pins the SAME structural immunity for the proactive path —
    the seam call site passes ``mid_turn=False`` AND the persisted
    state remains quiescent for subsequent ``astream``.
    """

    @pytest.mark.asyncio
    async def test_seam_mid_turn_false_does_not_set_as_node(self):
        """The seam's mid_turn=False path NEVER sets ``as_node``.

        Both calls in the mid_turn=False arm are pure external
        writes — exactly the brick-interaction window the canary in
        ``test_compact_executor_revive_brick_e2e.py::TestBrickCollapseOnRealGraph``
        documents. Pin here so the seam cannot regress.
        """
        mgr = MagicMock()
        graph = MagicMock()
        mgr.get_instance = AsyncMock(return_value=graph)
        pre_state = MagicMock()
        pre_state.values = {
            "messages": [
                HumanMessage(content="a", id="ha"),
                HumanMessage(content="b", id="hb"),
            ]
        }
        graph.aget_state = AsyncMock(return_value=pre_state)
        aupdate = AsyncMock()
        graph.aupdate_state = aupdate
        result = CompactionResult(
            replacement_messages=[
                SystemMessage(id="doc-1", content="doc"),
                HumanMessage(content="b", id="hb"),
            ],
            tokens_before=100, tokens_after=50, tokens_saved=50,
            messages_before=2, messages_after=2,
            compaction_type="summary",
            compacted_at="2026-09-04T00:00:00+00:00",
            compacted_ids=frozenset({"ha"}),
        )
        await seam_mod.persist_compaction_result(
            mgr,
            instance_id="inst-t2",
            result=result,
            mid_turn=False,
        )
        # Both calls must omit as_node= (Variant A).
        for call in aupdate.call_args_list:
            args, _ = call
            # aupdate_state(config, dict). The dict must not carry
            # ``as_node`` (mid_turn=False — we don't even pass it).
            kwargs_dict = args[1]
            assert "as_node" not in kwargs_dict, (
                f"mid_turn=False seam must NOT pass as_node=; got "
                f"call kwargs={kwargs_dict!r}"
            )

    @pytest.mark.asyncio
    async def test_seam_mid_turn_false_passes_kwargs_positionally(self):
        """The seam's mid_turn=False path does NOT pass as_node
        as a keyword arg at all (we only pass positional
        ``config`` + ``values`` dict).

        Belt-and-braces: even a future refactor that adds
        ``as_node=None`` to the dict would be caught by the
        position-args check above; this test pins the positional
        count = 2 (config + values dict).
        """
        mgr = MagicMock()
        graph = MagicMock()
        mgr.get_instance = AsyncMock(return_value=graph)
        pre_state = MagicMock()
        pre_state.values = {"messages": []}
        graph.aget_state = AsyncMock(return_value=pre_state)
        aupdate = AsyncMock()
        graph.aupdate_state = aupdate
        result = CompactionResult(
            replacement_messages=[
                SystemMessage(id="doc-1", content="d"),
            ],
            tokens_before=10, tokens_after=5, tokens_saved=5,
            messages_before=1, messages_after=1,
            compaction_type="summary",
            compacted_at="2026-09-04T00:00:00+00:00",
        )
        await seam_mod.persist_compaction_result(
            mgr,
            instance_id="inst-t2-kwargs",
            result=result,
            mid_turn=False,
        )
        # Each call: positional args = (config, dict).
        for call in aupdate.call_args_list:
            args, kwargs = call
            assert kwargs == {}, (
                f"mid_turn=False seam must not pass kwargs (we want "
                f"positional config + dict only); got kwargs={kwargs!r}"
            )
            assert len(args) == 2, (
                f"mid_turn=False seam must pass exactly 2 positional "
                f"args (config, values dict); got args={args!r}"
            )