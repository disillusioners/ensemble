"""T7 — per-completion duration observability tests.

Pins the SC6 log contract: ONE ``[LongToolNudge] TOOL_COMPLETED``
INFO line per tool completion (no duplicates, no leak on exception)
carrying instance_id / tool_call_id / tool_name / duration_ms /
threshold_seconds / threshold_crossed.

Runs under the repo-standard real-langgraph evict/restore pattern —
the wrapper delegates to the real ``ToolNode``.
"""

from __future__ import annotations

import logging

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from tests.helpers.long_tool_nudge import FakeClock, lt_real


@tool
def sample_tool(x: str) -> str:
    """Sample tool."""
    return f"ok:{x}"


@tool
def boom_tool(x: str) -> str:
    """Tool that always raises."""
    raise RuntimeError("boom")


class _S(dict):
    pass


async def _run(lt, node, state, config):
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class S(TypedDict, total=False):
        messages: list

    g = StateGraph(S)
    g.add_node("tools", node)
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    return await g.compile().ainvoke(state, config=config)


def _state(name="sample_tool", call_id="call-1"):
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": name,
                        "args": {"x": "y"},
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }


_CONFIG = {"configurable": {"thread_id": "inst-12345678"}}


def _completed_records(caplog):
    return [
        r
        for r in caplog.records
        if "TOOL_COMPLETED" in r.getMessage()
        and r.name == "daemon.services.long_tool_nudge"
    ]


@pytest.mark.asyncio
async def test_log_line_fields_and_marker(lt_real, caplog):
    registry = lt_real.LongToolNudgeRegistry()
    node = lt_real.wrapped_tools_node([sample_tool], registry)
    # Non-crossed completion logs at DEBUG (routine telemetry).
    with caplog.at_level("DEBUG", logger="daemon.services.long_tool_nudge"):
        await _run(lt_real, node, _state(), _CONFIG)
    records = _completed_records(caplog)
    assert len(records) == 1
    # Pin level explicitly: a fast (non-crossed) completion is DEBUG.
    assert records[0].levelno == logging.DEBUG
    message = records[0].getMessage()
    assert "[LongToolNudge] TOOL_COMPLETED" in message
    assert "inst-123" in message  # 8-char truncation per watchdog convention
    assert "call-1" in message
    assert "sample_tool" in message
    assert "duration_ms=" in message
    assert "threshold_seconds=" in message
    # threshold_crossed=False for a fast completion (900 fallback default).
    assert "threshold_crossed=False" in message
    # The registry cleared — no duplicate on the clear path.
    assert await registry.snapshot() == {}


@pytest.mark.asyncio
async def test_threshold_crossed_true_for_long_completion(
    lt_real, caplog, monkeypatch
):
    clock = FakeClock()
    monkeypatch.setattr(lt_real, "time", clock)
    registry = lt_real.LongToolNudgeRegistry()
    node = lt_real.wrapped_tools_node([sample_tool], registry)
    original_record = registry.record_start

    async def aging_record(
        instance_id, tool_call_id, tool_name, parent_id=None
    ):
        await original_record(instance_id, tool_call_id, tool_name, parent_id)
        registry._stamps[instance_id][tool_call_id].started_at = (
            clock.t - 1200
        )

    registry.record_start = aging_record  # type: ignore[method-assign]
    with caplog.at_level("INFO", logger="daemon.services.long_tool_nudge"):
        await _run(lt_real, node, _state(), _CONFIG)
    records = _completed_records(caplog)
    assert len(records) == 1
    # Pin level explicitly: a crossed completion logs at INFO (the signal).
    assert records[0].levelno == logging.INFO
    assert "threshold_crossed=True" in records[0].getMessage()
    assert "duration_ms=1200000" in records[0].getMessage()


@pytest.mark.asyncio
async def test_threshold_crossed_false_at_exact_boundary(
    lt_real, caplog, monkeypatch
):
    """Pin T7: at duration_seconds == threshold, threshold_crossed must be False.

    The per-completion log mirrors the scanner fire boundary (strict `>`,
    see daemon/services/long_tool_nudge.py: ``elapsed <= threshold: continue``),
    so duration exactly equal to the threshold is NOT a crossing. Guards
    against an ``>=`` regression that would re-arm the close-gate and
    re-open the (parent, child) episode on the boundary.
    """
    clock = FakeClock()
    monkeypatch.setattr(lt_real, "time", clock)
    registry = lt_real.LongToolNudgeRegistry()
    node = lt_real.wrapped_tools_node([sample_tool], registry)
    original_record = registry.record_start

    async def boundary_record(
        instance_id, tool_call_id, tool_name, parent_id=None
    ):
        await original_record(instance_id, tool_call_id, tool_name, parent_id)
        # Backdate by exactly the default threshold (900s) so
        # duration_seconds == threshold on completion.
        registry._stamps[instance_id][tool_call_id].started_at = (
            clock.t - 900
        )

    registry.record_start = boundary_record  # type: ignore[method-assign]
    # Boundary == threshold is non-crossed → DEBUG.
    with caplog.at_level("DEBUG", logger="daemon.services.long_tool_nudge"):
        await _run(lt_real, node, _state(), _CONFIG)
    records = _completed_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    message = records[0].getMessage()
    assert "duration_ms=900000" in message
    assert "threshold_seconds=900" in message
    assert "threshold_crossed=False" in message


@pytest.mark.asyncio
async def test_no_leak_on_exception_one_line_still_emitted(
    lt_real, caplog
):
    registry = lt_real.LongToolNudgeRegistry()
    node = lt_real.wrapped_tools_node([boom_tool], registry)
    # handle_tool_errors=True converts the tool's raise into an error
    # ToolMessage (no node-level exception) — the stamp clears
    # normally on the error path and the completion line still emits.
    # Fast completion → DEBUG.
    with caplog.at_level("DEBUG", logger="daemon.services.long_tool_nudge"):
        result = await _run(lt_real, node, _state(name="boom_tool"), _CONFIG)
    tm = result["messages"][-1]
    assert "Error" in tm.content
    records = _completed_records(caplog)
    assert len(records) == 1  # exactly one line — no duplicates on error
    assert records[0].levelno == logging.DEBUG
    assert await registry.snapshot() == {}


@pytest.mark.asyncio
async def test_completed_log_emitted_when_kill_switch_off(
    lt_real, caplog
):
    """Council fix-cycle 1, W4 — SC9 invariant regression pin.

    ``LONG_TOOL_NUDGE_ENABLED=0`` stops the SCANNER loop and (phase 3)
    gates the ``set_instance_tunable`` writes. The wrapper's per-tool
    stamping + the per-completion ``[LongToolNudge] TOOL_COMPLETED``
    log line MUST continue when disabled — they are the duration
    observability surface (SC6), independent of delivery. This test
    pins the invariant: a disabled scanner does NOT silence the
    forensic line that bd4b36ef needed and never had.
    """
    # Simulate the kill-switch OFF state without touching env vars:
    # the wrapper has no reference to the scanner's enabled flag —
    # the invariant holds regardless of whether the scanner is
    # currently running. We assert via the line being emitted
    # under the empty registry + no resolver/lookup attached (the
    # exact shape the disabled lifespan leaves the registry in).
    registry = lt_real.LongToolNudgeRegistry()
    node = lt_real.wrapped_tools_node([sample_tool], registry)
    # No close handler / resolver / lookup attached — the disabled
    # shape. Stamp lifecycle must still complete and emit the log.
    # Non-crossed completion under kill-switch OFF → DEBUG (continuity
    # pin: line still emitted, only the level moves; SC6 observability
    # is independent of delivery).
    with caplog.at_level("DEBUG", logger="daemon.services.long_tool_nudge"):
        await _run(lt_real, node, _state(), _CONFIG)
    records = _completed_records(caplog)
    assert len(records) == 1, (
        "TOOL_COMPLETED line must still emit when the scanner "
        "kill-switch is OFF (SC6 observability is independent "
        "of delivery — W4 council fix-cycle 1 regression pin)"
    )
    # Pin level: non-crossed under kill-switch OFF → DEBUG.
    assert records[0].levelno == logging.DEBUG
    message = records[0].getMessage()
    assert "inst-123" in message
    assert "call-1" in message
    assert "sample_tool" in message
    assert "duration_ms=" in message
    assert "threshold_seconds=" in message
    assert "threshold_crossed=" in message
    # And the stamp cleared — disabled does not leak either.
    assert await registry.snapshot() == {}


@pytest.mark.asyncio
async def test_completed_log_crossed_under_kill_switch_off_logs_info(
    lt_real, caplog, monkeypatch
):
    """Council fix-cycle 1, W4 — SC9 continuity + signal both hold.

    Companion to ``test_completed_log_emitted_when_kill_switch_off``:
    prove that under the kill-switch OFF shape (no resolver / no
    lookup), a CROSSED completion still emits the TOOL_COMPLETED line
    at INFO (the operator signal). Both halves of the W4 invariant
    hold — non-crossed emits at DEBUG (continuity), crossed emits at
    INFO (continuity + signal).
    """
    clock = FakeClock()
    monkeypatch.setattr(lt_real, "time", clock)
    registry = lt_real.LongToolNudgeRegistry()
    node = lt_real.wrapped_tools_node([sample_tool], registry)
    original_record = registry.record_start

    async def aging_record(
        instance_id, tool_call_id, tool_name, parent_id=None
    ):
        await original_record(instance_id, tool_call_id, tool_name, parent_id)
        registry._stamps[instance_id][tool_call_id].started_at = (
            clock.t - 1200
        )

    registry.record_start = aging_record  # type: ignore[method-assign]
    # Disable shape: no resolver/lookup attached. Capture at INFO so a
    # silent DEBUG regression cannot satisfy this pin.
    with caplog.at_level("INFO", logger="daemon.services.long_tool_nudge"):
        await _run(lt_real, node, _state(), _CONFIG)
    records = _completed_records(caplog)
    assert len(records) == 1
    # Crossed completion under kill-switch OFF → INFO (the signal).
    assert records[0].levelno == logging.INFO
    assert "threshold_crossed=True" in records[0].getMessage()
    assert "duration_ms=1200000" in records[0].getMessage()
