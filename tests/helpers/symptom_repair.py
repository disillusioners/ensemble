"""Shared test helpers for the hallucination-recovery ladder tests.

Collects the duplicated loop-unit factory, ok-summarizer stub,
``REMOVE_ALL_MESSAGES`` import shim, ladder-OFF autouse fixture
body, and ``_RealLangGraph`` context manager that the ladder /
canary / PG / loop-breaker / partition / engine / doc tests
each carried their own copy of. The test bodies stay byte-identical
to the pre-refactor copies — only the source-of-truth moves here.

Test count and assertion shapes are unchanged; this module is a
pure refactor (see APPLY pass notes).
"""
from __future__ import annotations

import sys

from langchain_core.messages import AIMessage, ToolMessage


# ---------------------------------------------------------------------------
# Loop-unit factory (six pre-refactor copies, identical bodies)
# ---------------------------------------------------------------------------

def loop_units(count: int, tool: str = "bash", args: dict | None = None):
    """``count`` consecutive identical AI+Tool units (oldest first).

    Mirrors the pre-refactor copy in:
      - tests/unit/test_symptom_repair_engine.py
      - tests/unit/test_symptom_repair_ladder.py
      - tests/unit/test_symptom_repair_doc.py
      - tests/unit/test_symptom_repair_partition.py
      - tests/unit/test_symptom_repair_mid_superstep_canary.py
      - tests/postgres/test_symptom_repair_ladder_pg.py
    """
    args = args or {"cmd": "ls"}
    out = []
    for i in range(count):
        tc_id = f"tc-{i}"
        out.append(
            AIMessage(
                content="",
                tool_calls=[{"id": tc_id, "name": tool, "args": args}],
                id=f"ai-{i}",
            )
        )
        out.append(
            ToolMessage(
                content=f"res-{i}",
                tool_call_id=tc_id,
                name=tool,
                id=f"tm-{i}",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Ok-summarizer stub (the pinned string matches the ladder pin)
# ---------------------------------------------------------------------------

async def ok_summarizer(context, symptom_class):
    """Async stub for ``SymptomRepairEngine._summarize`` returning a
    fixed summary string.

    The return string ``"LLM summary of the loop."`` is pinned by
    ``tests/unit/test_symptom_repair_ladder.py::test_durable_repair_calls_engine``
    (``slot.record_calls == [("iid-on", "LLM summary of the loop.")]``).
    The pre-refactor engine/canary/pg/integration copies each returned a
    different string, but no assertion pinned those strings, so unifying
    on the pinned ladder string is safe.
    """
    return "LLM summary of the loop."


# ---------------------------------------------------------------------------
# REMOVE_ALL_MESSAGES import shim (five pre-refactor copies, identical)
# ---------------------------------------------------------------------------

try:
    from langgraph.graph.message import REMOVE_ALL_MESSAGES  # noqa: F401
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    REMOVE_ALL_MESSAGES = "__remove_all__"


# ---------------------------------------------------------------------------
# Ladder-OFF autouse-fixture body (two byte-identical pre-refactor copies)
# ---------------------------------------------------------------------------

def ladder_off_setup(monkeypatch, *, _reset=None):
    """Pin BOTH ladder kill-switches OFF for the duration of a block.

    Used by the byte-identical autouse fixtures in:
      - tests/test_loop_breaker_integration.py:_ladder_off
      - tests/unit/test_loop_repairer_regression.py:_ladder_off

    Each test file keeps its own ``@pytest.fixture(autouse=True) def
    _ladder_off(monkeypatch)`` wrapper that delegates the body here so
    the per-file docstrings and the autouse wiring stay at the call
    site. ``_reset`` is injected lazily from
    ``daemon.config._reset_symptom_repair_ladder_for_tests`` to avoid
    an import-time daemon.config dependency.

    Returns the teardown callable; callers should invoke it after
    their yield (mirrors the pre-refactor fixture's setup/yield/teardown
    shape).
    """
    if _reset is None:
        from daemon.config import _reset_symptom_repair_ladder_for_tests
        _reset = _reset_symptom_repair_ladder_for_tests
    monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
    monkeypatch.setenv("ENSEMBLE_REPAIR_LOOP_DURABLE", "0")
    _reset()
    return _reset


# ---------------------------------------------------------------------------
# _RealLangGraph context manager (three pre-refactor copies, identical)
# ---------------------------------------------------------------------------

_MOCKED_LANGGRAPH_KEYS = (
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
)


class _RealLangGraph:
    """Swap the conftest's mocked langgraph modules for the real ones
    around a block of test code, then restore.

    Three pre-refactor copies (canary, integration, pg) had identical
    bodies modulo docstring; the docstring above is the canary
    original. The integration copy's docstring was shorter
    ('Swap the conftest's mocked langgraph modules for the real ones.');
    the pg copy's was bare. All three behave byte-identically.
    """

    def __enter__(self):
        self._original_modules = {
            k: sys.modules[k]
            for k in _MOCKED_LANGGRAPH_KEYS
            if k in sys.modules
        }
        for key in _MOCKED_LANGGRAPH_KEYS:
            if key in sys.modules:
                del sys.modules[key]
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        return self

    def __exit__(self, exc_type, exc, tb):
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        for key, mod in self._original_modules.items():
            sys.modules[key] = mod
        return False


__all__ = [
    "loop_units",
    "ok_summarizer",
    "REMOVE_ALL_MESSAGES",
    "ladder_off_setup",
    "_RealLangGraph",
    "_MOCKED_LANGGRAPH_KEYS",
]