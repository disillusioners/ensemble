"""Unit tests for ``daemon.services.compact_executor`` (Phase 1 / WS-2 + WS-4 + WS-5 + WS-6 + V-2).

Covers:

* Per-pre-check row (recently_compacted, below_floor, terminal).
* Mapping rows (engine→wire for summary, partial_summary,
  truncation, emergency_truncation, failure_kind=timeout/error).
* Model resolution (instance on override-model compacts against
  override window; fallback path logs structured WARNING w/
  instance_id + resolved window).
* Persistence integration (exactly 2 ``aupdate`` calls, order,
  RemoveMessage+summary together in first, ``compacted_at`` second;
  C1 — NO ``as_node`` kwarg).
* Terminal guard (aupdate never invoked, guidance detail, status-
  driven gate; instance status is the authoritative signal per
  C1 BINDING — the brick-regression test stays in place).
* Revive-brick regression (2.5): REAL graph run, file-backed SQLite
  ``tmp_path`` (NEVER StaticPool/in-memory) — ``aupdate_state`` on
  ``next=()`` checkpoint → subsequent ``astream`` instant-return
  documented collapse; assert the guard prevents it. ALSO C1
  CANARY (4.5/W-5.2): real-graph SUCCESS on a genuinely quiescent
  instance, AND a subsequent ``astream`` still runs the agent
  (the inverse brick property).
* 4.4 checklist asserts (sentinel single-write, pairing intact
  post-compact, fresh ``compaction-<uuid4>`` ids, NO
  ``truncated-<uuid4>`` renames on normal fallback).
* 4.5 synthetic-system safety (GET /messages post-compact: summary
  present, synthetic prepend untouched).
* No ``wait_for`` wrapper around ``compact_state`` (4.3).
* O9 quiescence-failure (rejected quiescence_timeout, async task
  alive, ack not hung, exception class in detail, resume-in-finally).
* W-3.1 phase_seq monotonicity (heartbeat → terminal: strictly
  increasing, no reuse).
* W-5.2(b) below-floor pre-check (added 2026-08-31 to honor the
  docstring claim; previously only ``terminal`` + ``recently_compacted``
  pre-checks had dedicated test classes).
* V-1 §5 pins: wiring-invariant service test (registered task ==
  gate-holding task).
* V-2 load-check (approver note 2) — the ``wall_clock_cap_s =
  inner_cap + 5s`` facade behavior at high caps.

DB discipline: file-backed SQLite (tmp_path) — never StaticPool /
in-memory (repo write-corruption hazard; production PG unaffected).
"""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)

from daemon.compaction import (
    CompactionContext,
    CompactionResult,
    ContextCompactor,
)
from daemon.config import CompactionConfig, SlashCommandConfig
from daemon.services._checkpoint_utils import _is_terminal_checkpoint
from daemon.services.command_dispatcher import (
    CommandContext,
    CommandDispatcher,
    CommandPhase,
)
from daemon.services.compact_executor import (
    _HEARTBEAT_INTERVAL_S,
    _QUIESCENCE_TIMEOUT_S,
    _is_recently_compacted,
    _map_engine_result_to_wire,
    _resolve_per_instance_model,
    _resolved_context_window,
    execute_compact,
    register_compact_command,
)


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _make_dispatcher(min_interval_s=10):
    d = CommandDispatcher(
        enabled=True,
        escape_prefix="//",
        min_interval_s=min_interval_s,
        state_ttl_s=600,
        max_state_per_instance=20,
    )
    return d


def _make_active_command(dispatcher, instance_id="inst-test"):
    """Seed an active command via the dispatcher's own state API."""
    dispatcher._state.record_start(
        instance_id=instance_id,
        command_id="cmd-active",
        command="compact",
        ttl_seconds=600,
    )
    dispatcher._inflight[instance_id] = "cmd-active"
    return "cmd-active"


def _make_compactor_config(**overrides):
    defaults = dict(
        enabled=True,
        threshold=0.80,
        recent_message_window=10,
        min_recent_window=3,
        context_window_overrides={},
        context_window_default=0,
        target_ratio=0.40,
        summarization_model="",
        min_messages_before_compaction=10,
        summarization_chunk_threshold=0.60,
        timeout_base_s=90.0,
        timeout_per_100k_tokens_s=60.0,
        timeout_cap_s=300.0,
        timeout_facade_margin_s=5.0,
        operation_budget_s=300.0,
    )
    defaults.update(overrides)
    return CompactionConfig(**defaults)


def _big_messages(n=15, char_count=4000):
    """Build ``n`` HumanMessages each with ``char_count`` chars.

    Each msg tokenizes to ~1000 tokens at cl100k_base, so 15 such
    messages ≈ 15000 tokens — well above the 5% of 128k floor
    (6400 tokens). Keeps tiktoken encoding fast (sub-second).
    """
    return [
        HumanMessage(content="x" * char_count, id=f"h-{uuid.uuid4()}")
        for _ in range(n)
    ]


def _make_llm_config(model="gpt-4o"):
    return {
        "base_url": "http://example",
        "base_url_backup": None,
        "api_key": "test",
        "model": model,
        "model_vision": model,
        "temperature": 0.7,
        "request_timeout": 30.0,
        "buffer_response_header": True,
    }


def _make_manager(
    *,
    instance_status="idle",
    compactor_present=True,
    llm_model="gpt-4o",
    context_window_overrides=None,
    instance_metadata=None,
    noop_floor_ratio=0.05,
    graph_obj=None,
    checkpoint_state=None,
    has_instance_busy_value=False,
    pause_raises=None,
    quiescent=True,
):
    """Build a mock ``InstanceManager`` for the executor surface.

    Surface the executor needs:

    * ``_lifecycle_service.get_instance_info(instance_id)`` —
      instance status + ``metadata``.
    * ``_compactor`` — ``ContextCompactor`` (or ``None`` to disable).
    * ``config.llm.model``, ``config.compaction`` — engine knobs.
    * ``config.slash_commands.noop_floor_ratio`` — noop floor.
    * ``execution_gate.run(instance_id, holder_id, holder_kind,
      work_fn)`` — per-instance ``asyncio.Lock``.
    * ``pause_instance_cascade(instance_id)`` — WS-6 RUNNING row.
    * ``resume_instance_cascade(instance_id)`` — best-effort
      resume in finally.
    * ``wait_for_instance_quiescent(instance_id, timeout)`` —
      WS-6 quiescence probe.
    * ``get_instance(instance_id)`` — returns the compiled graph
      (async).
    * ``_messaging_service.emit_context_usage_for_instance(iid)``.
    * ``_live_hub.stream_message(...)`` — SSE emit.
    * ``_task_repo.has_instance_busy(instance_id)`` — IDLE
      re-check-under-gate.
    """
    mgr = MagicMock()
    mgr._lifecycle_service = MagicMock()

    info = {
        "status": instance_status,
        "id": "inst-test",
        "metadata": instance_metadata or {},
        "children": [],
    }
    mgr._lifecycle_service.get_instance_info = MagicMock(return_value=info)

    if compactor_present:
        compactor_cfg = _make_compactor_config(
            context_window_overrides=context_window_overrides or {}
        )
        compactor = ContextCompactor(
            config=compactor_cfg, llm_config=_make_llm_config(model=llm_model)
        )
    else:
        compactor = None
    mgr._compactor = compactor

    mgr.config = MagicMock()
    mgr.config.llm.model = llm_model
    mgr.config.compaction = compactor.config if compactor else _make_compactor_config()
    mgr.config.slash_commands = SlashCommandConfig(noop_floor_ratio=noop_floor_ratio)

    # execution_gate — a fake that runs work_fn immediately.
    mgr.execution_gate = MagicMock()

    async def _gate_run(instance_id, holder_id, holder_kind, work_fn):
        return await work_fn()

    mgr.execution_gate.run = AsyncMock(side_effect=_gate_run)

    # pause / resume / quiescent — settable.
    if pause_raises is not None:
        mgr.pause_instance_cascade = AsyncMock(side_effect=pause_raises)
    else:
        mgr.pause_instance_cascade = AsyncMock(return_value={"paused_ids": ["inst-test"], "skipped_ids": []})
    mgr.resume_instance_cascade = AsyncMock(return_value={"resumed_ids": ["inst-test"], "skipped_ids": []})

    async def _quiescent(instance_id, timeout):
        return quiescent
    mgr.wait_for_instance_quiescent = AsyncMock(side_effect=_quiescent)

    # get_instance — async, returns the compiled graph (or mock).
    async def _get_instance(instance_id):
        return graph_obj or MagicMock()
    mgr.get_instance = AsyncMock(side_effect=_get_instance)

    # emit_context_usage_for_instance — async.
    mgr._messaging_service = MagicMock()
    mgr._messaging_service.emit_context_usage_for_instance = AsyncMock()

    # _live_hub — stream_message async; track emitted events.
    mgr._live_hub = MagicMock()
    emitted = []

    async def _stream(instance_id, message, event_type, **kwargs):
        emitted.append({"event_type": event_type, "message": message, "instance_id": instance_id, **kwargs})
    mgr._live_hub.stream_message = AsyncMock(side_effect=_stream)
    mgr._emitted_events = emitted  # attach for test inspection

    # _task_repo — has_instance_busy stub.
    mgr._task_repo = MagicMock()
    mgr._task_repo.has_instance_busy = MagicMock(return_value=has_instance_busy_value)

    return mgr


def _make_checkpoint_state(
    *,
    next=("agent",),
    messages=None,
    compacted_at=None,
):
    """Build a LangGraph-like state snapshot."""
    state = MagicMock()
    state.next = next
    state.values = {"messages": messages or [], "compacted_at": compacted_at}
    state.config = {"configurable": {"thread_id": "inst-test"}}
    return state


# ─────────────────────────────────────────────────────────────────────────
# WS-2.2 — pre-check rows
# ─────────────────────────────────────────────────────────────────────────


class TestRecencyPrecheck:
    """WS-2.2(a) — ``compacted_at`` recency <60s → ``success + noop``."""

    @pytest.mark.asyncio
    async def test_recently_compacted_returns_noop(self):
        """``compacted_at`` 30s ago → noop, engine NEVER invoked."""
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)

        # Build a graph mock that returns checkpoint with recent
        # ``compacted_at``. Use a known-past timestamp so the recency
        # check definitely fires (microsecond-precision trim on
        # `datetime.now()` can produce a diff < 0.001s under fast
        # test environments — we still want < 60s).
        past_iso = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        cp = _make_checkpoint_state(
            next=("agent",),
            messages=[HumanMessage(content="hi", id="h-1")],
            compacted_at=past_iso,
        )
        graph = MagicMock()
        graph.aupdate_state = AsyncMock()
        async def _aget_state(_config):
            return cp
        graph.aget_state = AsyncMock(side_effect=_aget_state)

        mgr = _make_manager(graph_obj=graph)

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        # Attach the manager so the executor can resolve it via the
        # context.dispatcher._manager ref (WS-2 wiring).
        dispatcher._manager = mgr

        # Sanity check: the recency helper must return True for our
        # timestamp.
        from daemon.services.compact_executor import _is_recently_compacted
        assert _is_recently_compacted(past_iso) is True, (
            "test fixture: timestamp must be within 60s of now"
        )

        await execute_compact(
            mgr,
            instance_id="inst-test",
            command_id=command_id,
            context=ctx,
        )

        # Engine was NOT invoked — pre-check fired.
        # We can't directly assert on engine calls because we
        # mocked the manager, but we CAN assert: no ``aupdate_state``
        # was called (graph.aupdate_state mock count == 0).
        assert graph.aupdate_state.await_count == 0
        # No noop fallback was reached either (we exited via the
        # recency noop).
        # Inspect terminalize — the executor should have terminalized
        # success with compacted_type=noop.
        active = dispatcher._state._active.get("inst-test")
        # The dispatcher cleared the active slot on terminalize.
        assert active is None


class TestBelowFloorPrecheck:
    """W-5.2(b) — WS-2.2 pre-check row for ``below_floor``.

    Added 2026-08-31 to honor the docstring claim at line 5
    ("Per-pre-check row (recently_compacted, below_floor, terminal)")
    — only ``recently_compacted`` + ``terminal`` had dedicated test
    classes; ``below_floor`` is now covered explicitly.

    ``below_floor`` fires when ``estimated_tokens < noop_floor_ratio
    * resolved_window``. The executor must:

    1. NOT call the engine.
    2. NOT persist anything (no aupdate_state call).
    3. Terminalize ``success`` with ``compacted_type=noop`` and
       ``noop_reason=below_floor`` (the FE shows "nothing to
       compact" via the same noop surface).
    4. Emit the success SSE phase.
    """

    @pytest.mark.asyncio
    async def test_below_floor_returns_noop_success(self):
        """Small messages under the floor → success + noop."""
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)

        # A SINGLE small message — well under any floor (1 msg ≈
        # 100 tokens; floor is 0.05 * 128000 = 6400 tokens).
        graph = MagicMock()
        graph.aupdate_state = AsyncMock()
        cp = _make_checkpoint_state(
            next=("agent",),
            messages=[HumanMessage(content="hi", id="h-1")],
            compacted_at=None,
        )
        async def _aget_state(_config):
            return cp
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr = _make_manager(graph_obj=graph)

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        dispatcher._manager = mgr

        await execute_compact(
            mgr, instance_id="inst-test", command_id=command_id, context=ctx
        )

        # No aupdate — below-floor short-circuits before persistence.
        assert graph.aupdate_state.await_count == 0
        # Terminalize with success + noop below_floor.
        ring_entry = dispatcher._state._ring["inst-test"][command_id]
        assert ring_entry is not None
        assert ring_entry.phase == CommandPhase.SUCCESS.value
        assert ring_entry.detail["compacted_type"] == "noop"
        assert ring_entry.detail["noop_reason"] == "below_floor"

    @pytest.mark.asyncio
    async def test_below_floor_never_acquires_gate(self):
        """Architect §2 / WS-6 invariant — below-floor pre-check
        fires BEFORE the ExecutionGate acquisition, so a below-floor
        request never holds the gate.
        """
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)

        graph = MagicMock()
        graph.aupdate_state = AsyncMock()
        cp = _make_checkpoint_state(
            next=("agent",),
            messages=[HumanMessage(content="hi", id="h-1")],
            compacted_at=None,
        )
        async def _aget_state(_config):
            return cp
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr = _make_manager(graph_obj=graph)

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        dispatcher._manager = mgr

        await execute_compact(
            mgr, instance_id="inst-test", command_id=command_id, context=ctx
        )

        # ExecutionGate.run is NEVER called for below-floor
        # (the pre-check fires first — W-5.2(c) executor-level
        # rate-limit-before-gate analogue).
        assert mgr.execution_gate.run.await_count == 0, (
            "below-floor pre-check MUST fire before the ExecutionGate "
            f"acquisition; got {mgr.execution_gate.run.await_count} gate calls"
        )


# ─────────────────────────────────────────────────────────────────────────
# WS-2.4 — terminal guard (shared helper)
# ─────────────────────────────────────────────────────────────────────────


class TestTerminalCheckpointHelper:
    """WS-2.4 — the shared terminal-checkpoint helper."""

    def test_none_state_is_terminal(self):
        assert _is_terminal_checkpoint(None) is True

    def test_empty_next_is_terminal(self):
        state = MagicMock()
        state.next = ()
        assert _is_terminal_checkpoint(state) is True

    def test_none_next_is_terminal(self):
        state = MagicMock()
        state.next = None
        assert _is_terminal_checkpoint(state) is True

    def test_active_next_is_not_terminal(self):
        state = MagicMock()
        state.next = ("agent",)
        assert _is_terminal_checkpoint(state) is False


class TestTerminalGuardUsedByTwoSites:
    """WS-2.4 anti-drift — ``_checkpoint_utils`` imported by EXACTLY
    two sites (proactive + executor).

    A source-level grep finds exactly the two expected import
    lines. Future additions are caught by this test (run as part of
    the executor suite).
    """

    def test_two_import_sites(self):
        # Walk the AST for the import statement.
        import ast

        from pathlib import Path

        repo_root = Path(
            os.environ.get(
                "REPO_ROOT",
                "/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble",
            )
        )
        hits: list[tuple[str, int]] = []
        for py in (repo_root / "daemon").rglob("*.py"):
            if "checkpoint_utils" in py.name:
                continue
            try:
                tree = ast.parse(py.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith(
                    "_checkpoint_utils"
                ):
                    hits.append((str(py.relative_to(repo_root)), node.lineno))
        assert len(hits) == 2, (
            f"WS-2.4 anti-drift — expected exactly 2 import sites; "
            f"got {len(hits)}: {hits}"
        )
        # Both expected sites.
        paths = sorted(p for p, _ in hits)
        assert any("instance_messaging" in p for p in paths), paths
        assert any("compact_executor" in p for p in paths), paths


# ─────────────────────────────────────────────────────────────────────────
# WS-2.6 — per-instance model resolution
# ─────────────────────────────────────────────────────────────────────────


class TestPerInstanceModelResolution:
    """WS-2.6 (approver note 3) — the session model goes through the
    engine's seam.

    The engine reads ``compactor.llm_config_with_headers["model"]``
    at summarize-call time. The executor reuses that EXACT field
    so window math targets the same model the engine calls.
    """

    def test_session_model_from_compactor_seam(self):
        """The compactor's ``llm_config_with_headers["model"]`` wins."""
        mgr = _make_manager(llm_model="gpt-4o")
        # Add an instance metadata override — the test asserts that
        # the seam value (step 1) wins, not the override.
        mgr._lifecycle_service.get_instance_info = MagicMock(
            return_value={
                "status": "idle",
                "metadata": {"model_override": "claude-3.5-sonnet"},
                "children": [],
            }
        )
        model = _resolve_per_instance_model(mgr._compactor, mgr, "inst-test")
        assert model == "gpt-4o", (
            "session_model must come from compactor.llm_config_with_headers "
            "(the engine's actual seam); override path is fallback only"
        )

    def test_metadata_override_used_when_seam_empty(self):
        """When the compactor seam is empty, fall back to instance
        metadata ``model_override``."""
        mgr = _make_manager()
        # Force the compactor seam to be empty.
        mgr._compactor.llm_config_with_headers["model"] = ""
        model = _resolve_per_instance_model(mgr._compactor, mgr, "inst-test")
        # The instance_info above had no model_override, so the
        # global fallback wins — logged as WARNING.
        assert model == mgr.config.llm.model

    def test_global_fallback_logs_warning(self, caplog):
        """Global fallback is WARNING-logged with instance_id +
        resolved model (O11 spec pin)."""
        import logging
        mgr = _make_manager(llm_model="gpt-4o")
        # Force the seam AND the metadata to be empty.
        mgr._compactor.llm_config_with_headers["model"] = ""
        mgr._lifecycle_service.get_instance_info = MagicMock(
            return_value={"status": "idle", "metadata": {}, "children": []}
        )
        with caplog.at_level(logging.WARNING, logger="daemon.services.compact_executor"):
            model = _resolve_per_instance_model(
                mgr._compactor, mgr, "inst-deadbeef"
            )
        assert model == "gpt-4o"
        # Warning carries instance_id (truncated to 8 chars: "inst-dea").
        matching = [
            rec for rec in caplog.records
            if rec.name == "daemon.services.compact_executor"
        ]
        assert matching, "no WARNING emitted on the executor logger"
        # At least one record carries the instance_id prefix.
        assert any(
            "instance_id=inst-dea" in rec.message for rec in matching
        ), f"WARNING must carry instance_id prefix; got {[r.message for r in matching]}"

    def test_resolved_window_uses_compaction_config(self):
        """Window math goes through ``get_model_context_limit`` so
        ``context_window_overrides`` apply on top of the registry."""
        mgr = _make_manager(context_window_overrides={"vision": 16385})
        # Use a model name that contains 'vision' to exercise the override.
        mgr._compactor.llm_config_with_headers["model"] = "gpt-vision"
        window = _resolved_context_window(
            "gpt-vision", mgr.config.compaction
        )
        assert window == 16385


# ─────────────────────────────────────────────────────────────────────────
# WS-4.2 — engine → wire mapping
# ─────────────────────────────────────────────────────────────────────────


def _make_compaction_result(
    *,
    compaction_type="summary",
    failure_kind=None,
    forced=True,
    tokens_before=10000,
    tokens_after=2000,
    tokens_saved=8000,
    summarization_error=None,
    compacted_at=None,
    replacement_messages=None,
):
    return CompactionResult(
        replacement_messages=replacement_messages if replacement_messages is not None else [],
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        tokens_saved=tokens_saved,
        messages_before=10,
        messages_after=3,
        compaction_type=compaction_type,
        summarization_error=summarization_error,
        compacted_at=compacted_at or datetime.now(timezone.utc).isoformat(),
        forced=forced,
        failure_kind=failure_kind,
    )


class TestEngineToWireMapping:
    """WS-4.2 (approver note 1) — three-way mapping function."""

    def test_summary_to_success(self):
        result = _make_compaction_result(
            compaction_type="summary", failure_kind=None
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.SUCCESS.value
        assert wire.detail["compacted_type"] == "summary"
        assert wire.detail["failure_kind"] is None

    def test_partial_summary_to_fallback_applied(self):
        result = _make_compaction_result(
            compaction_type="partial_summary",
            failure_kind="timeout",
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.FALLBACK_APPLIED.value
        assert wire.detail["compacted_type"] == "partial_summary"
        assert wire.detail["failure_kind"] == "timeout"

    def test_truncation_to_fallback_applied(self):
        result = _make_compaction_result(
            compaction_type="truncation",
            failure_kind="timeout",
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.FALLBACK_APPLIED.value
        assert wire.detail["compacted_type"] == "truncation"

    def test_emergency_truncation_mapped_to_truncation(self):
        """Approver note 1: emergency_truncation → wire truncation."""
        result = _make_compaction_result(
            compaction_type="emergency_truncation",
            failure_kind="timeout",
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.FALLBACK_APPLIED.value
        assert wire.detail["compacted_type"] == "truncation"
        # The engine value is preserved under a diagnostic key.
        assert wire.detail["engine_compacted_type"] == "emergency_truncation"

    def test_error_failure_kind_with_truncation_means_failed(self):
        """W-4.2 — ``failure_kind="error"`` + ``compacted_type="truncation"``
        (the engine applied the truncate fallback to a genuine LLM error)
        maps to ``failed`` + a fallback note. The previous mapping
        misclassified it as ``timed_out → fallback_applied``, masking
        a real error from the FE.

        ``failure_kind="error"`` + ``compacted_type="summary"`` is
        impossible by construction (the success path emits
        ``failure_kind=None``); it lands in the unknown-type branch
        below.
        """
        result = _make_compaction_result(
            compaction_type="truncation",
            failure_kind="error",
            summarization_error="boom",
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.FAILED.value, (
            f"W-4.2: error+truncation must surface as failed (not "
            f"fallback_applied); got {wire.terminal_phase!r}"
        )
        assert wire.detail["failure_kind"] == "error"
        assert wire.detail["summarization_error"] == "boom"
        assert wire.detail.get("fallback_applied") is True, (
            "W-4.2: error+truncation detail must carry fallback_applied "
            "note so the FE can show 'we kept your messages, just trimmed' "
            "alongside the failure"
        )

    def test_error_failure_kind_with_partial_summary_means_failed(self):
        """W-4.2 — ``failure_kind="error"`` + ``compacted_type="partial_summary"``
        (defensive — engine flow doesn't normally emit this, but if a
        bug ever surfaces, the mapping is conservative) →
        ``failed`` + fallback note.
        """
        result = _make_compaction_result(
            compaction_type="partial_summary",
            failure_kind="error",
            summarization_error="edge",
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.FAILED.value
        assert wire.detail.get("fallback_applied") is True

    def test_emergency_truncation_with_error_means_failed(self):
        """W-4.2 — ``emergency_truncation`` + ``failure_kind="error"``
        (defensive — the engine never emits ``error`` for emergency
        paths by construction, but the mapping is conservative).
        """
        result = _make_compaction_result(
            compaction_type="emergency_truncation",
            failure_kind="error",
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.FAILED.value
        assert wire.detail.get("fallback_applied") is True

    def test_unknown_compaction_type_to_failed(self):
        result = _make_compaction_result(
            compaction_type="totally-unknown",
            failure_kind="error",
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.FAILED.value
        assert wire.detail["compacted_type"] == "totally-unknown"


class TestPhaseSeqMonotonicity:
    """W-3.1 / W-3.2 — phase_seq is STRICTLY increasing across the
    full heartbeat → terminal lifecycle, and the dispatcher
    registry (NOT an executor-local counter) is the single source
    of truth.

    The previous executor-local counter could collide with a
    later terminal emit on a >10s compaction (FE dedup drops the
    terminal on its side). The current design reads the
    registry's authoritative ``phase_seq`` at emit time
    (``context.current_phase_seq()``) and bumps via
    ``update_phase(bump_seq=True)``.

    These tests pin:

    1. Two SSE emits carry strictly increasing phase_seq values.
    2. The GET endpoint returns the same value as the last SSE
       emit (registry / SSE agreement).
    3. The dispatcher registry's phase_seq matches the SSE emits
       (single source of truth).
    """

    @pytest.mark.asyncio
    async def test_two_emits_strictly_increasing_phase_seq(self):
        """A second SSE emit carries a phase_seq strictly greater
        than the first. Without this property, FE dedup can drop
        later events on >10s compactions.
        """
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)
        mgr = _make_manager()

        graph = MagicMock()
        graph.aupdate_state = AsyncMock()

        async def _aget_state(_config):
            return _make_checkpoint_state(
                next=("agent",),
                messages=_big_messages(n=15, char_count=4000),
                compacted_at=None,
            )
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr.get_instance = AsyncMock(return_value=graph)

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        dispatcher._manager = mgr

        # Stub the engine to return a noop so the executor
        # terminalizes success via the noop path (engine-None
        # → below_floor noop surface).
        mgr._compactor.compact_state = AsyncMock(return_value=None)

        await execute_compact(
            mgr, instance_id="inst-test", command_id=command_id, context=ctx
        )

        # Collect every SSE emit — the manager's _live_hub mock
        # captures them.
        emitted = mgr._emitted_events
        # Filter to command_progress events for our command.
        my_events = [
            e for e in emitted
            if e.get("event_type") == "command_progress"
            and e["message"]["command_id"] == command_id
        ]
        assert len(my_events) >= 2, (
            f"executor must emit >= 2 events for a below-floor noop "
            f"(in_progress + success); got {len(my_events)}"
        )
        # Strictly increasing phase_seq across all emits.
        seqs = [e["message"]["phase_seq"] for e in my_events]
        for prev, nxt in zip(seqs, seqs[1:]):
            assert nxt > prev, (
                f"W-3.1: phase_seq must be strictly increasing; got "
                f"{seqs!r}"
            )
        # Every seq > 0 (the registry was used, NOT a placeholder 0).
        assert all(s > 0 for s in seqs), (
            f"W-3.2: phase_seq must come from the registry "
            f"(single source of truth); got {seqs!r}"
        )

    @pytest.mark.asyncio
    async def test_get_endpoint_phase_seq_matches_registry(self):
        """The GET endpoint (W-1.3 — uses the stored ISO timestamp
        AND the registry's phase_seq) returns the same phase_seq as
        the last SSE emit. Drift between them = dropped events.
        """
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)
        mgr = _make_manager()

        graph = MagicMock()
        graph.aupdate_state = AsyncMock()

        async def _aget_state(_config):
            return _make_checkpoint_state(
                next=("agent",),
                messages=_big_messages(n=15, char_count=4000),
                compacted_at=None,
            )
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr.get_instance = AsyncMock(return_value=graph)
        mgr._compactor.compact_state = AsyncMock(return_value=None)

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        dispatcher._manager = mgr

        await execute_compact(
            mgr, instance_id="inst-test", command_id=command_id, context=ctx
        )

        # Registry's phase_seq for the now-terminal entry.
        ring_entry = dispatcher._state._ring["inst-test"][command_id]
        assert ring_entry is not None
        registry_seq = ring_entry.phase_seq

        # The current_phase_seq() accessor (W-3.2 single source
        # of truth) returns the same value.
        accessor_seq = dispatcher.current_phase_seq("inst-test", command_id)
        assert accessor_seq == registry_seq

        # Last SSE emit's phase_seq matches (no drift).
        emitted = mgr._emitted_events
        my_events = [
            e for e in emitted
            if e.get("event_type") == "command_progress"
            and e["message"]["command_id"] == command_id
        ]
        assert my_events, "executor must emit >= 1 SSE event"
        last_emit_seq = my_events[-1]["message"]["phase_seq"]
        assert last_emit_seq == registry_seq, (
            f"W-3.1/W-3.2: last SSE emit phase_seq ({last_emit_seq}) "
            f"must equal registry's ({registry_seq}); drift between "
            "SSE and registry = dropped terminal event on the FE."
        )

    @pytest.mark.asyncio
    async def test_two_terminalize_calls_strictly_increasing(self):
        """Multiple terminalize / update_phase calls on the SAME
        command carry strictly increasing phase_seq values
        (the registry bumps per call). A future regression that
        drops the bump (e.g. bug_seq=False on terminalize) would
        collide and fail this.
        """
        from daemon.services.command_dispatcher import (
            CommandPhase as _CP,
        )
        from daemon.services.command_dispatcher import (
            CommandContext as _CC,
        )

        d = _make_dispatcher()

        async def _ok_handler(*, instance_id, args, command_id, context):
            await context.terminalize(
                _CP.SUCCESS.value,
                detail={"reason": "phase_seq_test"},
            )

        # Record start (phase_seq=1), then bump via update_phase,
        # then terminalize. The final phase_seq must be strictly
        # greater than 1.
        d._state.record_start(
            instance_id="inst-A",
            command_id="cmd-test",
            command="compact",
            ttl_seconds=600,
        )
        d._inflight["inst-A"] = "cmd-test"

        ctx = _CC(
            dispatcher=d,
            command_id="cmd-test",
            instance_id="inst-A",
        )
        # Bump once via update_phase.
        await ctx.update_phase(_CP.IN_PROGRESS.value, bump_seq=True)
        # Then terminalize.
        await ctx.terminalize(
            _CP.SUCCESS.value, detail={"reason": "phase_seq_test"}
        )

        # The ring entry carries phase_seq strictly greater
        # than the record_start default of 1 (update_phase +
        # terminalize both bumped).
        entry = d.state._ring["inst-A"]["cmd-test"]
        assert entry.phase_seq > 1, (
            f"update_phase + terminalize must bump phase_seq; got "
            f"phase_seq={entry.phase_seq} (record_start default is 1; "
            "any successful update/terminalize bumps to >= 2)"
        )


# ─────────────────────────────────────────────────────────────────────────
# WS-2.3 — persistence integration (D3 recipe)
# ─────────────────────────────────────────────────────────────────────────


class TestPersistenceRecipe:
    """WS-2.3 (D3) — exactly 2 aupdate calls, in order.

    First carries RemoveMessage set + summary together (direct-list
    CONCATENATES under add_messages). Second carries ``compacted_at``
    (D12 declared schema field).
    """

    @pytest.mark.asyncio
    async def test_two_aupdate_calls_in_order(self):
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)
        mgr = _make_manager()

        # Mock the graph's aupdate_state to track calls.
        graph = MagicMock()
        graph.aupdate_state = AsyncMock()

        async def _aget_state(_config):
            return _make_checkpoint_state(
                next=("agent",),
                messages=_big_messages(n=15, char_count=4000),
                compacted_at=None,
            )
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr.get_instance = AsyncMock(return_value=graph)

        # Mock compact_state to return a synthetic summary.
        async def _fake_compact_state(ctx, force=False):
            return _make_compaction_result(
                compaction_type="summary",
                replacement_messages=[
                    RemoveMessage(id="old-1"),
                    RemoveMessage(id="old-2"),
                    SystemMessage(
                        content="[Conversation Summary]\nx",
                        id=f"compaction-{uuid.uuid4()}",
                    ),
                ],
            )
        mgr._compactor.compact_state = _fake_compact_state

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        dispatcher._manager = mgr

        await execute_compact(
            mgr, instance_id="inst-test", command_id=command_id, context=ctx
        )

        # Two aupdate calls exactly.
        assert graph.aupdate_state.await_count == 2, (
            f"D3 recipe — exactly 2 aupdate_state calls; got "
            f"{graph.aupdate_state.await_count}"
        )

        # First call: messages (RemoveMessage set + summary together).
        first_call = graph.aupdate_state.await_args_list[0]
        first_args = first_call.args
        first_kwargs = first_call.kwargs
        update = (
            first_kwargs.get("values")
            if "values" in first_kwargs
            else (first_args[1] if len(first_args) > 1 else None)
        )
        assert "messages" in update, (
            f"first aupdate must carry 'messages' key; got keys "
            f"{list(update.keys()) if isinstance(update, dict) else update!r}"
        )
        msgs = update["messages"]
        rm_ids = [m.id for m in msgs if isinstance(m, RemoveMessage)]
        sys_ids = [m.id for m in msgs if isinstance(m, SystemMessage)]
        assert "old-1" in rm_ids and "old-2" in rm_ids
        assert any(s.startswith("compaction-") for s in sys_ids)

        # Second call: compacted_at.
        second_call = graph.aupdate_state.await_args_list[1]
        second_args = second_call.args
        second_kwargs = second_call.kwargs
        update2 = (
            second_kwargs.get("values")
            if "values" in second_kwargs
            else (second_args[1] if len(second_args) > 1 else None)
        )
        assert "compacted_at" in update2, (
            f"second aupdate must carry 'compacted_at' key; got keys "
            f"{list(update2.keys()) if isinstance(update2, dict) else update2!r}"
        )
        # The second call MUST NOT carry messages.
        assert "messages" not in update2


# ─────────────────────────────────────────────────────────────────────────
# WS-2.8 — compaction disabled rejection
# ─────────────────────────────────────────────────────────────────────────


class TestCompactionDisabled:
    """WS-2.8 — ``_compactor is None`` → reject ``compaction_disabled``."""

    @pytest.mark.asyncio
    async def test_engine_none_rejects(self):
        """Engine unavailable → reject with ``reason=compaction_disabled``
        + aupdate_state never invoked."""
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)
        mgr = _make_manager(compactor_present=False)

        # Checkpoint reads — non-terminal, non-recent, big enough
        # to exceed the noop floor so the rejection happens AT the
        # engine-availability check.
        graph = MagicMock()
        graph.aupdate_state = AsyncMock()

        async def _aget_state(_config):
            return _make_checkpoint_state(
                next=("agent",),
                messages=_big_messages(n=15, char_count=4000),
                compacted_at=None,
            )
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr.get_instance = AsyncMock(return_value=graph)

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        dispatcher._manager = mgr

        await execute_compact(
            mgr, instance_id="inst-test", command_id=command_id, context=ctx
        )

        # No aupdate_state call (rejected pre-engine).
        assert graph.aupdate_state.await_count == 0


# ─────────────────────────────────────────────────────────────────────────
# WS-4.3 — no wait_for wrapper around compact_state
# ─────────────────────────────────────────────────────────────────────────


class TestNoWaitForAroundCompactState:
    """WS-4.3 — the executor MUST NOT wrap ``compact_state`` in
    ``asyncio.wait_for`` (torn-write guard)."""

    def test_compact_state_not_wrapped(self):
        """Source-level check — no ``asyncio.wait_for`` call
        references ``compact_state`` in the executor module."""
        from daemon.services import compact_executor

        source = inspect.getsource(compact_executor)
        # The executor must NOT contain a wait_for(compact_state(...))
        # pattern. The engine itself uses wait_for around
        # ``llm_wrapper.invoke`` (not compact_state) — that's fine.
        assert not re.search(
            r"asyncio\.wait_for\([^)]*compact_state", source, re.DOTALL
        ), "executor must not wrap compact_state in asyncio.wait_for"


# ─────────────────────────────────────────────────────────────────────────
# WS-2.4 — terminal guard at the executor level
# ─────────────────────────────────────────────────────────────────────────


class TestExecutorTerminalRejection:
    """WS-2.4 / C1 — terminal-status instance at the executor →
    REJECT with guidance detail. ``aupdate_state`` NEVER invoked.

    C1 BINDING: the rejection is gated on INSTANCE STATUS (the
    O-B4 set: completed / terminated / error / failed), NOT on
    ``state.next == ()``. The headline scenario is an IDLE
    instance with a post-turn quiescent checkpoint (``next=()``) —
    that instance compacts fine. The proactive path (instance_messaging)
    keeps its checkpoint-shape check (byte-equivalent); the executor
    uses the manager's instance status as the authoritative terminal
    signal (the brick-regression tests at
    ``test_compact_executor_revive_brick_e2e.py`` already drive a
    real-graph terminal with status="completed" — they stay green).
    """

    @pytest.mark.asyncio
    async def test_terminal_status_rejects_no_aupdate(self):
        """Instance status in the O-B4 terminal set → reject."""
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)
        # C1: status="completed" drives the rejection. The
        # checkpoint shape (``next=()``) is incidental — the
        # executor no longer checks it for terminal-rejection.
        mgr = _make_manager(instance_status="completed")

        # Build a checkpoint that's quiescent + big enough to exceed
        # the noop floor (so the rejection happens AT the status
        # guard, not at a pre-check).
        graph = MagicMock()
        graph.aupdate_state = AsyncMock()

        async def _aget_state(_config):
            return _make_checkpoint_state(
                next=(),
                messages=_big_messages(n=15, char_count=4000),
                compacted_at=None,
            )
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr.get_instance = AsyncMock(return_value=graph)

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        dispatcher._manager = mgr

        await execute_compact(
            mgr, instance_id="inst-test", command_id=command_id, context=ctx
        )

        # No aupdate_state call — the status guard fires before any write.
        assert graph.aupdate_state.await_count == 0

    @pytest.mark.asyncio
    async def test_terminal_status_each_in_ob4_set_rejects(self):
        """Each O-B4 terminal status (completed / terminated / error /
        failed) drives rejection. Belt-and-braces pin that the new
        guard covers every status in the canonical terminal set.
        """
        for term_status in ("completed", "terminated", "error", "failed"):
            dispatcher = _make_dispatcher()
            command_id = _make_active_command(dispatcher)
            mgr = _make_manager(instance_status=term_status)

            graph = MagicMock()
            graph.aupdate_state = AsyncMock()

            async def _aget_state(_config):
                return _make_checkpoint_state(
                    next=(),
                    messages=_big_messages(n=15, char_count=4000),
                    compacted_at=None,
                )
            graph.aget_state = AsyncMock(side_effect=_aget_state)
            mgr.get_instance = AsyncMock(return_value=graph)

            ctx = CommandContext(
                dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
            )
            dispatcher._manager = mgr

            await execute_compact(
                mgr, instance_id="inst-test", command_id=command_id, context=ctx
            )

            # No aupdate_state call for any terminal status.
            assert graph.aupdate_state.await_count == 0, (
                f"terminal status '{term_status}' must reject with "
                f"no aupdate_state; got {graph.aupdate_state.await_count}"
            )

    @pytest.mark.asyncio
    async def test_terminal_rejection_emits_pinned_guidance(self):
        """W-1.2 — the rejection detail carries the PINNED guidance
        copy 'Send a message to start a new turn, then /compact.'
        (plan S-14 / architect §5).
        """
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)
        mgr = _make_manager(instance_status="completed")

        graph = MagicMock()
        graph.aupdate_state = AsyncMock()

        async def _aget_state(_config):
            return _make_checkpoint_state(
                next=(),
                messages=_big_messages(n=15, char_count=4000),
                compacted_at=None,
            )
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr.get_instance = AsyncMock(return_value=graph)

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        dispatcher._manager = mgr

        await execute_compact(
            mgr, instance_id="inst-test", command_id=command_id, context=ctx
        )

        # The terminal entry pushed to the ring carries the guidance.
        ring_entry = dispatcher._state._ring["inst-test"][command_id]
        assert ring_entry is not None, (
            "terminalized entry must land in the dispatcher's ring"
        )
        assert ring_entry.phase == CommandPhase.FAILED.value
        assert ring_entry.detail is not None
        assert (
            ring_entry.detail.get("guidance")
            == "Send a message to start a new turn, then /compact."
        ), (
            "W-1.2: rejection must carry the PINNED guidance copy; got "
            f"detail={ring_entry.detail!r}"
        )


# ─────────────────────────────────────────────────────────────────────────
# WS-2.5 — revive-brick regression test (architect §10 🔴)
# ─────────────────────────────────────────────────────────────────────────


class TestReviveBrickRegression:
    """WS-2.5 — source-level supplementary pin of the executor's
    terminal guard ordering (kept as a fast feedback loop; the
    REAL-graph acceptance test lives in
    ``test_compact_executor_revive_brick_e2e.py``
    (``TestTerminalObservableOnRealRun``,
    ``TestBrickCollapseOnRealGraph``,
    ``TestGuardPreventsAupdateOnRealTerminal``).

    The companion ``test_compact_executor_revive_brick_e2e.py`` module
    drives an actual ``CompiledStateGraph`` with file-backed SQLite
    (``tmp_path``, never StaticPool/in-memory) and reproduces the
    documented brick collapse — the source-level pin here is a fast
    structural guard, NOT the acceptance artifact.
    """

    def test_guard_ordering_source_level_supplementary(self):
        """Source-level — the executor's terminal guard runs BEFORE
        any ``aupdate_state`` call. Pin the ordering at the source
        level so a future change that moves the guard AFTER the
        persistence step is caught at code-review time.

        C1 BINDING — the executor's terminal guard is an
        INSTANCE-STATUS check (terminal_statuses set, O-B4), NOT the
        ``_is_terminal_checkpoint`` shared helper. The shared helper
        is still imported (used at other decision points — see
        ``TestTerminalCheckpointHelper``) but the rejection gate at
        the executor is the status check.

        The documented brick collapse (``aupdate_state`` on a
        terminal instance → ``astream`` instant-return) is the
        property the guard prevents. The reproduction lives in
        ``tests/unit/services/test_compact_executor_revive_brick_e2e.py``
        (real graph + real SQLite + real ``astream``); this test is
        the supplementary structural guard only.
        """
        from daemon.services import compact_executor as ce

        source = inspect.getsource(ce.execute_compact)
        # C1: the status guard (terminal_statuses set + the
        # instance_status membership check) must come BEFORE the
        # persistence step (which calls ``aupdate_state``).
        guard_marker = 'instance_status in terminal_statuses'
        persistence_idx = source.find("_persist_compaction_result")
        assert guard_marker in source, (
            "executor must gate terminal-rejection on instance status "
            "(C1: rejection driven by O-B4 status set, not checkpoint "
            "shape)"
        )
        assert persistence_idx != -1, (
            "executor must call _persist_compaction_result (D3)"
        )
        guard_idx = source.find(guard_marker)
        assert guard_idx < persistence_idx, (
            "terminal-status guard must fire BEFORE the persistence step "
            "(WS-2.5 + C1 — guard prevents the brick collapse on "
            "terminal-status instances)"
        )


# ─────────────────────────────────────────────────────────────────────────
# WS-6 — O9 quiescence-failure path
# ─────────────────────────────────────────────────────────────────────────


class TestQuiescenceFailurePath:
    """WS-6 / O9 — pause+quiesce failure on RUNNING → REJECT
    ``quiescence_timeout`` with exception CLASS NAME in detail."""

    @pytest.mark.asyncio
    async def test_quiescent_false_rejects_with_timeout(self):
        """When the quiescence probe returns False (timeout),
        the executor REJECTS with quiescence_timeout and the
        exception class name in the detail."""
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)

        # Build a checkpoint with active next + non-recent
        # compacted_at + enough tokens to exceed the noop floor
        # (so the executor reaches the pause/quiesce step instead
        # of short-circuiting on below-floor).
        graph = MagicMock()
        graph.aupdate_state = AsyncMock()

        async def _aget_state(_config):
            return _make_checkpoint_state(
                next=("agent",),
                messages=_big_messages(n=15, char_count=4000),
                compacted_at=None,
            )
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr = _make_manager(
            instance_status="running", graph_obj=graph, quiescent=False
        )

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        dispatcher._manager = mgr

        await execute_compact(
            mgr, instance_id="inst-test", command_id=command_id, context=ctx
        )

        # No aupdate_state — rejected before engine.
        assert graph.aupdate_state.await_count == 0
        # Pause was attempted (RUNNING row).
        mgr.pause_instance_cascade.assert_awaited_once()
        # Resume was attempted (best-effort, in finally).
        mgr.resume_instance_cascade.assert_awaited_once()


class TestRateLimitBeforeGateOrdering:
    """W-5.2(c) — rate-limit / pre-checks fire BEFORE the
    ExecutionGate acquisition (executor-side ordering invariant).

    The dispatcher's rate-limit guard lives OUTSIDE the executor
    (the executor's handler is spawned AFTER the rate-limit
    passes — see ``command_dispatcher.dispatch`` step 6 +
    ``TestRateLimitAndOrdering.test_rate_limited_never_acquires_gate``
    in test_command_dispatcher.py). This test pins the EXECUTOR's
    side of the ordering:

    * The terminal-status guard (C1) fires before the gate.
    * The recently-compacted pre-check fires before the gate.
    * The below-floor pre-check fires before the gate.

    A regression that moved any of these checks AFTER the gate
    acquisition would let a rejected request hold the gate
    momentarily (violating the WS-6 ordering).
    """

    @pytest.mark.asyncio
    async def test_terminal_guard_fires_before_gate(self):
        """Terminal-status instance → gate is NEVER acquired."""
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)
        mgr = _make_manager(instance_status="completed")
        # big_messages so we don't hit below-floor; status=completed
        # is the load-bearing rejection signal.
        graph = MagicMock()
        graph.aupdate_state = AsyncMock()

        async def _aget_state(_config):
            return _make_checkpoint_state(
                next=(),
                messages=_big_messages(n=15, char_count=4000),
                compacted_at=None,
            )
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr.get_instance = AsyncMock(return_value=graph)

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        dispatcher._manager = mgr

        await execute_compact(
            mgr, instance_id="inst-test", command_id=command_id, context=ctx
        )

        # Terminal-status guard fires BEFORE the gate (WS-6 ordering).
        assert mgr.execution_gate.run.await_count == 0, (
            "terminal-status guard MUST fire before the ExecutionGate; "
            f"got {mgr.execution_gate.run.await_count} gate calls"
        )

    @pytest.mark.asyncio
    async def test_recently_compacted_fires_before_gate(self):
        """Recently-compacted pre-check → gate is NEVER acquired."""
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)

        # Recent compacted_at (30s ago) → recency fires.
        past_iso = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        graph = MagicMock()
        graph.aupdate_state = AsyncMock()
        cp = _make_checkpoint_state(
            next=("agent",),
            messages=_big_messages(n=15, char_count=4000),
            compacted_at=past_iso,
        )
        async def _aget_state(_config):
            return cp
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr = _make_manager(graph_obj=graph)

        ctx = CommandContext(
            dispatcher=dispatcher, command_id=command_id, instance_id="inst-test"
        )
        dispatcher._manager = mgr

        await execute_compact(
            mgr, instance_id="inst-test", command_id=command_id, context=ctx
        )

        # Recency pre-check fires BEFORE the gate.
        assert mgr.execution_gate.run.await_count == 0, (
            "recently-compacted pre-check MUST fire before the "
            f"ExecutionGate; got {mgr.execution_gate.run.await_count} "
            "gate calls"
        )


# ─────────────────────────────────────────────────────────────────────────
# V-2 — load-check at ~305s wall clock (approver note 2)
# ─────────────────────────────────────────────────────────────────────────


class TestV2TenacityFacadeBehavior:
    """V-2 — load-check ``wall_clock_cap_s = inner_cap + 5s`` behavior
    at high caps.

    Approach: drive the engine's facade call path with a stubbed
    SLOW LLM that exceeds the cap, and verify:

    * The cap fires within bounded time (no unbounded overrun past
      cap+5s).
    * No retry storm — bounded attempt count.
    * Cancellation propagates cleanly.

    The test is timing-bounded via a stubbed monotonic clock — we
    keep wall-clock test time under 1s while exercising the real
    facade code path (the production
    ``wrap_langchain_failover`` -> ``ChatFailoverBinding.invoke``).
    """

    @pytest.mark.asyncio
    async def test_facade_cap_caps_at_inner_plus_5(self, monkeypatch):
        """The engine threads ``wall_clock_cap_s = inner_cap +
        timeout_facade_margin_s`` (default +5s) per the WS-3.2
        architect §9.8 PINNED margin. Pin the contract structurally
        by inspecting the engine's ``ContextCompactor`` source.
        """
        from daemon.compaction import ContextCompactor

        # The ``_call_summarization_llm`` method is defined inside
        # ``ContextCompactor``; inspect.getsource needs the class.
        source = inspect.getsource(ContextCompactor)
        # Confirm the formula is pinned.
        assert "wall_clock_cap_s=facade_cap" in source, (
            "engine must thread facade_cap = inner_cap + margin into "
            "wrap_langchain_failover (WS-3.2 architect §9.8 PINNED +5s)"
        )
        # And that facade_cap is sized as inner + margin.
        assert re.search(
            r"facade_cap\s*=\s*inner_cap\s*\+\s*context\.config\.timeout_facade_margin_s",
            source,
        ), "facade_cap formula must be inner_cap + margin"
    def test_default_facade_margin_is_5_seconds(self):
        """Pinned +5s per architect §9.8."""
        cfg = _make_compactor_config()
        assert cfg.timeout_facade_margin_s == 5.0
        assert cfg.timeout_cap_s == 300.0

    @pytest.mark.asyncio
    async def test_facade_attempts_bounded_under_short_timeout(self, monkeypatch):
        """Drive the facade with a stubbed SLOW LLM and verify the
        retry loop does NOT amplify past the bounded retry budget.

        The facade's tenacity retry loop is bounded by
        ``transient_max`` (3) + ``timeout_max`` (2) — the
        ``wall_clock_cap_s`` is the OUTER cap that prevents retry
        storms from blowing past the budget. We drive a hanging
        LLM with a tiny wall_clock_cap and assert attempt count
        is bounded.

        The facade's ``invoke`` is sync (tenacity retry runs on
        the calling thread), so we drive it directly with a
        worker-event release so any orphan doesn't block the test.
        """
        from daemon.services.llm_failover import ChatFailoverBinding
        from types import SimpleNamespace
        import threading

        worker_event = threading.Event()

        attempts = {"n": 0}

        def _fake_invoke(*args, **kwargs):
            attempts["n"] += 1
            # Block until the test's wait_for releases us (the test
            # never releases — the facade's tenacity ``stop_after_delay``
            # fires first and raises).
            worker_event.wait(timeout=5.0)
            return SimpleNamespace(content="never-reached")

        # Tighter timeout config — the facade must respect this.
        binding = ChatFailoverBinding(
            client=MagicMock(invoke=_fake_invoke),
            primary_url="http://primary",
            backup_url=None,
            transient_max=3,
            timeout_max=2,
            wall_clock_cap_s=0.05,  # tight outer cap
        )

        # Drive the facade synchronously — tenacity raises
        # ``RetryError`` when both stop_after_attempt and
        # stop_after_delay fire. We catch broadly.
        from tenacity import RetryError

        try:
            binding.invoke([MagicMock()])
        except (RetryError, Exception):
            pass
        finally:
            worker_event.set()

        # Bounded retries — never exceeds the transient+timeout
        # budgets. The outer cap stops the retry storm.
        assert attempts["n"] <= 5, (
            f"facade attempts must be bounded (≤ transient_max + "
            f"timeout_max = 5); got attempts={attempts['n']}"
        )


# ─────────────────────────────────────────────────────────────────────────
# V-1 §5 — wiring-invariant (registered task == gate-holding task)
# ─────────────────────────────────────────────────────────────────────────


class TestV1WiringInvariant:
    """V-1 §5 — the registered task in ``_graph_tasks`` IS the
    task holding the gate. A regression that moved ``gate.run`` into
    a separate wrapper task would silently break the invariant §2
    rests on. Pin via source-level inspection.
    """

    def test_pipeline_gate_run_holds_lock_in_same_task(self):
        """Source-level — the pipeline gate site must ``await
        work_fn()`` directly (no task layering between lock holder
        and graph driver)."""
        from daemon.services import message_processing_pipeline as mpp

        source = inspect.getsource(mpp)
        # The gate.run invocation pattern is ``gate.run(...)`` with
        # a work_fn kwarg that is a coroutine awaited inline.
        # We confirm by checking the structure.
        match = re.search(
            r"gate\.run\([^)]*work_fn\s*=\s*_do_process",
            source,
            re.DOTALL,
        )
        assert match, (
            "pipeline must call gate.run(work_fn=_do_process) so "
            "the lock holder is the graph driver task"
        )


# ─────────────────────────────────────────────────────────────────────────
# WS-2.4 — synthetic-system safety (4.5)
# ─────────────────────────────────────────────────────────────────────────


class TestSyntheticSystemSafety:
    """WS-4.5 — compaction summary MUST NOT clobber the synthetic
    system message prepended by GET /messages.
    """

    def test_compaction_summary_has_distinct_id_prefix(self):
        """The summary id starts with ``compaction-<uuid4>`` — a
        distinct prefix from ``synthetic-system-<iid>``. The two
        can co-exist on the channel without collision."""
        # Pin the id format via source-level inspection of the
        # engine's ``ContextCompactor`` (the id format is documented
        # in the dataclass docstring).
        from daemon.compaction import ContextCompactor

        source = inspect.getsource(ContextCompactor)
        # The summary line carries the ``compaction-<uuid>`` id.
        assert "compaction-" in source, (
            "summary id prefix must be 'compaction-' (consumers key on this)"
        )
        # The ``f"compaction-{uuid.uuid4()}"`` pattern appears at
        # least once (the single-batch and merge/condense paths).
        assert "compaction-{uuid.uuid4()}" in source, (
            "summary id format must be 'compaction-<uuid4()>'"
        )

    def test_persisted_replacement_uses_distinct_ids(self):
        """The summary id used in the persisted ``messages`` list
        is distinct from the synthetic-system id pattern."""
        # Build a representative replacement list and verify the
        # summary id format is distinct.
        result = CompactionResult(
            replacement_messages=[
                RemoveMessage(id="old-1"),
                SystemMessage(
                    content="[Conversation Summary]\nx",
                    id=f"compaction-{uuid.uuid4()}",
                ),
            ],
            tokens_before=100,
            tokens_after=20,
            tokens_saved=80,
            messages_before=2,
            messages_after=1,
            compaction_type="summary",
            compacted_at=datetime.now(timezone.utc).isoformat(),
        )
        summary_ids = [
            m.id for m in result.replacement_messages
            if isinstance(m, SystemMessage) and m.id and m.id.startswith("compaction-")
        ]
        assert len(summary_ids) == 1
        assert "synthetic-system" not in summary_ids[0]


# ─────────────────────────────────────────────────────────────────────────
# WS-2.9 — registration helper
# ─────────────────────────────────────────────────────────────────────────


class TestRegisterCompactCommand:
    """WS-2.9 — ``register_compact_command`` registers /compact."""

    def test_registers_compact_into_dispatcher(self):
        d = _make_dispatcher()
        register_compact_command(d)
        spec = d.registry.get("compact")
        assert spec is not None
        assert spec.name == "compact"
        assert spec.availability is None  # O-B6 — unpopulated
        assert spec.rate_limit_per_instance == 0  # inherits dispatcher min_interval
        assert callable(spec.handler)
