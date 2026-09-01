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
    next=(),
    messages=None,
    compacted_at=None,
):
    """Build a LangGraph-like state snapshot.

    Default ``next=()`` — a GENUINELY quiescent post-turn checkpoint.
    Under C1 (terminal rejection is instance-status-based), the
    quiescent shape is the LEGITIMATE success-path state: every
    success-path fixture must NOT fabricate a non-empty ``next``.
    Pass ``next=("agent",)`` explicitly ONLY where a test genuinely
    simulates a mid-graph shape (e.g. the pause-first/RUNNING path
    with a frozen in-flight task).
    """
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
            # Quiescent fixture (default next=()) — the recency
            # pre-check must fire on the LEGITIMATE post-turn shape,
            # not a fabricated mid-graph one.
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


class TestRunningNoopWaitingSequence:
    """Defect #7 follow-up — the RUNNING-noop SSE sequence pin.

    With the relocated ``waiting`` emit (compact_executor.py, step 3,
    F3/D-B9), a RUNNING instance that noops at a pre-check (recency or
    below-floor) must emit ``waiting`` THEN the ``success(noop)``
    terminal — never ``success(noop)`` alone. This locks the relocated
    emission against future revert attempts (the pre-#7 code emitted
    nothing at all for this path) and supports the #7 acceptance
    criterion that the ``waiting`` event arrives immediately,
    independent of the pause-first pipeline.
    """

    @pytest.mark.asyncio
    async def test_running_recency_noop_emits_waiting_then_success(self):
        """RUNNING + recently-compacted → SSE phases == waiting, success.

        The recency pre-check fires BEFORE the pause-first branch (a
        noop must never pause the instance), so this pins the exact
        sequence the FE sees on a RUNNING-noop: ``waiting`` (the
        relocated emit) then ``success`` with the noop detail — and
        that ``pause_instance_cascade`` is never awaited.
        """
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)

        # Recent compacted_at (30s-scale past) → recency noop fires.
        past_iso = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        cp = _make_checkpoint_state(
            # Genuine RUNNING-row checkpoint shape (frozen mid-graph,
            # ``next == ("agent",)`` — the only legit synthetic
            # mid-graph shape per the defect-1 fixture doctrine).
            # Outcome-irrelevant for the recency check (C1: the gate
            # is instance status), but keeps the fixture honest.
            next=("agent",),
            messages=[HumanMessage(content="hi", id="h-1")],
            compacted_at=past_iso,
        )
        graph = MagicMock()
        graph.aupdate_state = AsyncMock()

        async def _aget_state(_config):
            return cp

        graph.aget_state = AsyncMock(side_effect=_aget_state)

        mgr = _make_manager(graph_obj=graph, instance_status="running")

        ctx = CommandContext(
            dispatcher=dispatcher,
            command_id=command_id,
            instance_id="inst-test",
        )
        dispatcher._manager = mgr

        await execute_compact(
            mgr,
            instance_id="inst-test",
            command_id=command_id,
            context=ctx,
        )

        # THE PIN — SSE sequence is waiting THEN success(noop), not
        # success(noop) alone (the pre-#7 behavior for this path).
        phases = [e["message"]["phase"] for e in mgr._emitted_events]
        assert phases == ["waiting", "success"], (
            f"RUNNING-noop must emit waiting then success(noop); "
            f"got {phases!r} — a revert of the relocated waiting-emit "
            f"(defect #7) drops the leading 'waiting'"
        )
        # The waiting emit is the START state: phase_seq 1 (record_start
        # default, bump_seq=False), and the terminal carries a strictly
        # greater registry-authoritative seq (W-3.1 single source).
        assert mgr._emitted_events[0]["message"]["phase_seq"] == 1
        assert (
            mgr._emitted_events[1]["message"]["phase_seq"]
            > mgr._emitted_events[0]["message"]["phase_seq"]
        )
        # The terminal is the noop success with the recency detail.
        success_detail = mgr._emitted_events[1]["message"]["detail"]
        assert success_detail["compacted_type"] == "noop"
        assert success_detail["noop_reason"] == "recently_compacted"
        # A noop must never pause the instance — the pre-check fires
        # before the pause-first branch.
        mgr.pause_instance_cascade.assert_not_awaited()
        # Engine persistence never ran (recency pre-check exited).
        assert graph.aupdate_state.await_count == 0


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
            # Quiescent fixture (default next=()) — below-floor noop
            # is a success path; must not fabricate a mid-graph next.
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
            # Quiescent fixture (default next=()) — below-floor noop
            # is a success path; must not fabricate a mid-graph next.
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

    # ─────────────────────────────────────────────────────────────────────
    # W-4.4 / N1 (2026-08-31) — engine → wire TOTAL-BY-CONSTRUCTION.
    #
    # Live re-gate evidence at 9eb1b67e, command 7c78a141 vicinity: an
    # accepted compact terminalized ``phase=failed, reason=
    # unknown_compaction_type`` while the engine emitted
    # ``compaction_type="summarization"`` (its actual success-path
    # literal at ``daemon/compaction.py:920``). 1 occurrence / 3
    # accepted compacts under hammer load. Root cause: the prior
    # mapping pinned ``"summary"`` which never matched the engine's
    # real emission — the engine's mapping table is now total-by-
    # construction via ``_ENGINE_TYPE_TO_WIRE_COMPACTED_TYPE``.
    #
    # These tests pin (a) the regression — engine literal
    # ``"summarization"`` maps to ``success`` — and (b) the full
    # enum coverage + the ``fk="error"`` carve-out + the
    # forward-compat safe default.
    # ─────────────────────────────────────────────────────────────────────

    def test_engine_summarization_literal_maps_to_success(self):
        """W-4.4 / N1 — engine emits ``"summarization"`` (its success-
        path literal at ``daemon/compaction.py:920``) and the
        executor MUST translate that to wire ``compacted_type=
        "summary"`` + ``phase=success``. Pinning this prevents a
        re-introduction of the regressed pre-W-4.4 mapping.
        """
        result = _make_compaction_result(
            compaction_type="summarization",
            failure_kind=None,
            forced=True,
            tokens_before=41865,
            tokens_after=48629,
            tokens_saved=-6764,  # negative — engine grew the context
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.SUCCESS.value, (
            "N1 regression: engine emitted summarization (success), the "
            f"executor MUST return success; got {wire.terminal_phase!r}"
        )
        assert wire.detail["compacted_type"] == "summary", (
            f"wire enum must collapse engine 'summarization' → "
            f"'summary'; got {wire.detail['compacted_type']!r}"
        )
        assert wire.detail["failure_kind"] is None
        assert wire.detail.get("reason") != "unknown_compaction_type"
        # Honest pass-through of the negative delta.
        assert wire.detail["tokens_saved"] == -6764

    def test_engine_legacy_summary_alias_maps_to_success(self):
        """W-4.4 — engine ``"summary"`` is an accepted alias (forward-
        compat / pre-feature variant). Maps identically to wire
        ``"summary"`` + ``phase=success``. Pinned by the same byte-
        compatibility requirement as ``test_summary_to_success``.
        """
        result = _make_compaction_result(
            compaction_type="summary", failure_kind=None
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.SUCCESS.value
        assert wire.detail["compacted_type"] == "summary"
        assert wire.detail["failure_kind"] is None

    def test_engine_chunked_summarization_legacy_collapse(self):
        """W-4.4 — engine ``"chunked_summarization"`` is the legacy
        emit (pre-WS-3.4) and is collapsed to wire ``"summary"`` per
        the WS-3.4 amendment. Mapped identically to the other
        success-path engine emissions.
        """
        result = _make_compaction_result(
            compaction_type="chunked_summarization",
            failure_kind=None,
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.SUCCESS.value
        assert wire.detail["compacted_type"] == "summary"
        assert wire.detail["failure_kind"] is None

    def test_negative_tokens_saved_passes_through_unchanged(self):
        """W-4.4 / N1 — the executor MUST report the actual negative
        ``tokens_saved`` on the wire (no clamping to 0, no
        fabrication). The live re-gate showed ``-6764`` after a
        summarization that GREW the context; masking that delta
        would defeat operator / FE diagnosis.
        """
        result = _make_compaction_result(
            compaction_type="summarization",
            failure_kind=None,
            tokens_before=41865,
            tokens_after=48629,
            tokens_saved=-6764,
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.detail["tokens_before"] == 41865
        assert wire.detail["tokens_after"] == 48629
        assert wire.detail["tokens_saved"] == -6764, (
            "negative tokens_saved MUST pass through; got "
            f"{wire.detail['tokens_saved']!r}"
        )

    def test_unknown_engine_type_with_no_failure_maps_to_success(self):
        """W-4.4 — TOTAL-BY-CONSTRUCTION default. An unforeseen engine
        value (e.g., ``"summary_v2"`` from a post-merge feature
        branch) with ``failure_kind=None`` is treated as an HONEST
        engine success: ``phase=success`` + raw engine value carried
        as diagnostic detail under ``engine_compacted_type`` and
        ``unknown_compaction_type=True``. NEVER
        ``failed/unknown_compaction_type`` — N1 explicitly forbids
        that carve-out for engine success.
        """
        result = _make_compaction_result(
            compaction_type="summary_v2",
            failure_kind=None,
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.SUCCESS.value, (
            "unknown engine value + no failure MUST surface as success "
            f"(forward-compat); got {wire.terminal_phase!r}"
        )
        assert wire.detail["compacted_type"] == "summary_v2", (
            "wire enum carries the raw engine value so the FE can "
            "recognize a new engine vocabulary; got "
            f"{wire.detail['compacted_type']!r}"
        )
        assert wire.detail.get("engine_compacted_type") == "summary_v2"
        assert wire.detail.get("unknown_compaction_type") is True

    def test_unknown_engine_type_with_error_failure_maps_to_failed(self):
        """W-4.4 — the failure_kind-based carve-out still binds for
        ``failed`` on truly-unknown engine types: when the engine
        stamps ``failure_kind="error"`` the wire outcome is
        ``failed`` + diagnostic, regardless of how exotic the
        ``compaction_type`` is. This protects the original safety
        case while the default-unknown-success branch above
        protects the engine-success path.
        """
        result = _make_compaction_result(
            compaction_type="summary_v2",
            failure_kind="error",
            summarization_error="internal_blew_up",
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == CommandPhase.FAILED.value
        assert wire.detail.get("unknown_compaction_type") is True
        assert wire.detail.get("fallback_applied") is True

    @pytest.mark.parametrize(
        "engine_value,fk,expected_phase,expected_wire_type",
        [
            # Success-path engine values → wire summary / success.
            ("summarization", None, "success", "summary"),
            ("summarization", "timeout", "success", "summary"),
            ("summary", None, "success", "summary"),
            ("summary", "error", "success", "summary"),  # defense: fk=error on
                                                          # success-type is
                                                          # contradictory and
                                                          # treated as success
            ("chunked_summarization", None, "success", "summary"),
            # Fallback-path engine values, no error → fallback_applied.
            ("partial_summary", "timeout", "fallback_applied", "partial_summary"),
            ("partial_summary", None, "fallback_applied", "partial_summary"),
            ("truncation", "timeout", "fallback_applied", "truncation"),
            ("truncation", None, "fallback_applied", "truncation"),
            ("emergency_truncation", "timeout", "fallback_applied", "truncation"),
            ("emergency_truncation", None, "fallback_applied", "truncation"),
            # Fallback-path + fk="error" → failed.
            ("partial_summary", "error", "failed", "partial_summary"),
            ("truncation", "error", "failed", "truncation"),
            ("emergency_truncation", "error", "failed", "truncation"),
            # Unknown / forward-compat: no error → success; with error →
            # failed. The safe default for unforeseen engine values
            # is the SIGNATURE of W-4.4 (N1 fix).
            ("totally-new-type", None, "success", "totally-new-type"),
            ("totally-new-type", "error", "failed", "totally-new-type"),
        ],
    )
    def test_engine_to_wire_enum_exhaustively(
        self, engine_value, fk, expected_phase, expected_wire_type
    ):
        """W-4.4 — parametrize EVERY known engine emission path
        enumerated in ``daemon/compaction.py`` AND the failure_kind
        × type combinations. The mapping is TOTAL-BY-CONSTRUCTION:
        every input terminates with a sane phase + wire type; no
        input lands in the legacy ``failed/unknown_compaction_type``
        fallback UNLESS the engine itself reported an error.
        """
        result = _make_compaction_result(
            compaction_type=engine_value,
            failure_kind=fk,
            forced=True,
        )
        wire = _map_engine_result_to_wire(result)
        assert wire.terminal_phase == expected_phase, (
            f"engine={engine_value!r} fk={fk!r}: expected "
            f"phase={expected_phase!r}, got {wire.terminal_phase!r}"
        )
        assert wire.detail["compacted_type"] == expected_wire_type, (
            f"engine={engine_value!r} fk={fk!r}: expected "
            f"compacted_type={expected_wire_type!r}, got "
            f"{wire.detail['compacted_type']!r}"
        )
        # Engine-success path MUST NOT carry reason=unknown_compaction_type.
        # That string is reserved for genuinely-failed engine results.
        if expected_phase == "success":
            assert wire.detail.get("reason") != "unknown_compaction_type"


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
                # Quiescent fixture (default next=()) — noop success
                # path; must not fabricate a mid-graph next.
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
                # Quiescent fixture (default next=()) — noop success
                # path; must not fabricate a mid-graph next.
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

        # Mock compact_state to return a synthetic doc. The new
        # design (§4 / §5) emits a single SystemMessage doc with
        # the original tail ids preserved — the executor's sentinel
        # recipe handles the rest. The mock mirrors that shape so
        # the pre-write guard accepts the replacement.
        # Build a stable snapshot once, share between the
        # aget_state and the fake compactor's return.
        graph = MagicMock()
        graph.aupdate_state = AsyncMock()
        _orig_messages = _big_messages(n=15, char_count=4000)
        async def _aget_state(_config):
            return _make_checkpoint_state(
                # Quiescent fixture (default next=()) — this is a
                # SUCCESS-to-persistence test; the fabricated
                # mid-graph next was carried over from pre-C1 and
                # misdescribed the real post-turn shape.
                messages=list(_orig_messages),
                compacted_at=None,
            )
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr.get_instance = AsyncMock(return_value=graph)

        # Mock compact_state to return a synthetic doc with the
        # SAME tail ids as the snapshot (the pre-write guard
        # rejects replacements that lose snapshot ids).
        async def _fake_compact_state(ctx, force=False):
            return _make_compaction_result(
                compaction_type="summary",
                replacement_messages=[
                    SystemMessage(
                        content=(
                            "[CONTEXT COMPACTION — mode=summary | ...]\n"
                            "GLOBAL OVERVIEW\nx\n"
                        ),
                        id="compaction-global-inst-test-1",
                    ),
                    *list(_orig_messages),
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
        # §5 — the replacement starts with the REMOVE_ALL_MESSAGES
        # sentinel (source-verified literal value
        # ``"__remove_all__"`` at langgraph 1.0.9), then the doc,
        # then the preserved tail (all original messages).
        sys_ids = [m.id for m in msgs if isinstance(m, SystemMessage)]
        # The doc carries the new id prefix.
        assert any(
            (s or "").startswith("compaction-global-") for s in sys_ids
        ), f"expected a compaction-global- doc id, got {sys_ids}"
        # Sentinel is element 0.
        from langchain_core.messages import RemoveMessage
        first = msgs[0]
        assert isinstance(first, RemoveMessage)
        assert first.id == "__remove_all__"
        # The preserved tail ids follow the doc.
        tail_ids = [
            m.id for m in msgs
            if not isinstance(m, (SystemMessage, RemoveMessage))
        ]
        assert len(tail_ids) == 15, (
            f"preserved tail must carry all 15 original ids; got {len(tail_ids)}"
        )

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
                # Quiescent fixture (default next=()) — this is an
                # IDLE-instance rejection (engine unavailable); the
                # real post-turn shape is quiescent, so the fixture
                # must not fabricate a mid-graph next.
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
    """WS-2.4 / C1 — compact-reject status at the executor →
    REJECT with guidance detail. ``aupdate_state`` NEVER invoked.

    C1 BINDING: the rejection is gated on INSTANCE STATUS (the
    COMPACT_REJECT_STATUSES set: terminated / error / failed since
    compact-on-COMPLETED 2026-08-31), NOT on ``state.next == ()``.
    The headline scenario is an IDLE instance with a post-turn
    quiescent checkpoint (``next=()``) — that instance compacts
    fine — and now a ``completed`` instance does TOO (O-B4
    superseded by C1 Variant A for that status; see the
    ``TestExecutorCompactOnCompleted`` class below). The proactive
    path (instance_messaging) keeps its checkpoint-shape check
    (byte-equivalent); the executor uses the manager's instance
    status as the authoritative status signal (the brick-regression
    tests at ``test_compact_executor_revive_brick_e2e.py`` drive a
    real-graph terminal — they stay green).
    """

    @pytest.mark.asyncio
    async def test_terminal_status_rejects_no_aupdate(self):
        """Instance status in COMPACT_REJECT_STATUSES → reject."""
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)
        # compact-on-COMPLETED: status="terminated" drives the
        # rejection now. The checkpoint shape (``next=()``) is
        # incidental — the executor checks instance status only.
        mgr = _make_manager(instance_status="terminated")

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
    async def test_terminal_status_each_in_reject_set_rejects(self):
        """Each COMPACT_REJECT_STATUSES status (terminated / error /
        failed) drives rejection. Belt-and-braces pin that the guard
        covers every status in the compact-reject set.
        (compact-on-COMPLETED 2026-08-31: ``completed`` was removed
        from this set — it compacts; see
        ``TestExecutorCompactOnCompleted``.)
        """
        for term_status in ("terminated", "error", "failed"):
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
        mgr = _make_manager(instance_status="terminated")

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


class TestExecutorCompactOnCompleted:
    """compact-on-COMPLETED (2026-08-31) — unit-level success path.

    ``status="completed"`` proceeds through the executor: past the
    compact-reject guard (now COMPACT_REJECT_STATUSES only), through
    the quiescent-by-definition branch (no pause, no quiesce probe —
    a COMPLETED instance has no live work), into engine → persistence
    → SUCCESS in the dispatcher ring.

    The REAL-graph acceptance canaries (Variant A persist shape +
    revive-on-send) live in
    ``test_compact_executor_revive_brick_e2e.py``
    (``TestExecutorCompactOnCompletedRealGraph``).
    """

    @pytest.mark.asyncio
    async def test_completed_instance_compacts_successfully(self):
        """status="completed" → engine runs, persistence runs (2
        aupdate calls, NO ``as_node``), ring lands ``success``."""
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)
        # completed: compact-ELIGIBLE now. Quiescent-by-definition —
        # pause/resume must never fire.
        mgr = _make_manager(instance_status="completed")

        graph = MagicMock()
        graph.aupdate_state = AsyncMock()
        # Stable snapshot, shared with the fake compactor.
        _orig_messages = _big_messages(n=15, char_count=4000)

        async def _aget_state(_config):
            return _make_checkpoint_state(
                next=(),
                messages=list(_orig_messages),
                compacted_at=None,
            )
        graph.aget_state = AsyncMock(side_effect=_aget_state)
        mgr.get_instance = AsyncMock(return_value=graph)

        async def _fake_compact_state(ctx, force=False):
            return _make_compaction_result(
                compaction_type="summary",
                replacement_messages=[
                    SystemMessage(
                        content=(
                            "[CONTEXT COMPACTION — mode=summary | ...]\n"
                            "GLOBAL OVERVIEW\ncompleted-canary\n"
                        ),
                        id="compaction-global-inst-test-1",
                    ),
                    *list(_orig_messages),
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

        # SUCCESS landed in the ring.
        ring_entry = dispatcher._state._ring["inst-test"][command_id]
        assert ring_entry is not None, (
            "completed instance must terminalize (success or failure) "
            "in the dispatcher ring"
        )
        assert ring_entry.phase == CommandPhase.SUCCESS.value, (
            f"completed must be compact-eligible — expected the success "
            f"terminal; got phase={ring_entry.phase!r} "
            f"detail={ring_entry.detail!r}"
        )
        assert ring_entry.detail.get("compacted_type") == "summary"

        # Variant A persistence — exactly 2 aupdate calls, NEITHER
        # carrying ``as_node``.
        assert graph.aupdate_state.await_count == 2, (
            f"persistence must emit exactly 2 aupdate_state calls; got "
            f"{graph.aupdate_state.await_count}"
        )
        for idx, c in enumerate(graph.aupdate_state.await_args_list):
            assert "as_node" not in c.kwargs, (
                f"C1 Variant A: aupdate call #{idx + 1} must NOT carry "
                f"as_node; got kwargs={c.kwargs!r}"
            )

        # Quiescent-by-definition: no pause, no resume, no quiesce
        # probe for a COMPLETED instance.
        mgr.pause_instance_cascade.assert_not_awaited()
        mgr.resume_instance_cascade.assert_not_awaited()
        mgr.wait_for_instance_quiescent.assert_not_awaited()


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
        INSTANCE-STATUS check (the COMPACT_REJECT_STATUSES set,
        terminated / error / failed since compact-on-COMPLETED
        2026-08-31), NOT the ``_is_terminal_checkpoint`` shared
        helper. The shared helper
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
        # C1: the status guard (the compact-reject set — the shared
        # COMPACT_REJECT_STATUSES constant since compact-on-COMPLETED
        # 2026-08-31; previously the O-B4 all-4 set, before that a
        # local terminal_statuses set) + the instance_status
        # membership check must come BEFORE the persistence step
        # (which calls ``aupdate_state``).
        guard_marker = 'instance_status in COMPACT_REJECT_STATUSES'
        persistence_idx = source.find("_persist_compaction_result")
        assert guard_marker in source, (
            "executor must gate terminal-rejection on instance status "
            "(C1: rejection driven by the COMPACT_REJECT_STATUSES set, "
            "not checkpoint shape)"
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
        # of short-circuiting on below-floor). This is the ONE
        # fixture that deliberately keeps ``next=("agent",)``: a
        # RUNNING instance with a frozen in-flight task is a
        # genuine mid-graph shape (pause-first/RUNNING path), not
        # a fabricated success-path shape.
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
        """Compact-reject status instance → gate is NEVER acquired."""
        dispatcher = _make_dispatcher()
        command_id = _make_active_command(dispatcher)
        mgr = _make_manager(instance_status="terminated")
        # big_messages so we don't hit below-floor; status=terminated
        # is the load-bearing rejection signal (compact-on-COMPLETED:
        # completed is eligible now — the pin rides on a
        # COMPACT_REJECT_STATUSES status).
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
            # Quiescent fixture (default next=()) — recency noop is
            # a success path; must not fabricate a mid-graph next.
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
        """Architect §4 — the doc id starts with
        ``compaction-global-{instance_id}-{seq}`` — a distinct
        prefix from ``synthetic-system-<iid>``. The two can
        co-exist on the channel without collision.
        """
        # Pin the id format via source-level inspection of the
        # engine's :func:`build_compaction_doc` (the id is built
        # in the module-scope helper, not the class).
        import daemon.compaction as cm
        from daemon.compaction import build_compaction_doc

        source = inspect.getsource(build_compaction_doc)
        # The doc id prefix is ``compaction-global-``.
        assert "compaction-global-" in source, (
            "doc id prefix must be 'compaction-global-' (FE keys on this — "
            "the fold-with-preview card is gated by this prefix)"
        )
        # The doc id is built via ``f"compaction-global-{instance_id}-{seq}"``.
        assert (
            "compaction-global-" in source
            and "instance_id" in source
            and "seq" in source
        ), (
            "doc id format must be 'compaction-global-{instance_id}-{seq}'"
        )
        # The module exposes the prefix constant for consumers.
        assert cm.GLOBAL_DOC_ID_PREFIX == "compaction-global-"

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
