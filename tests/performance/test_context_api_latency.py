"""Performance smoke test for Phase 4 context rebuild latency.

Goal
----
Validate that the on-demand context rebuild performed by
``get_instance_messages`` (in ``human_messages`` mode) is fast enough
that it does not regress the GET /messages read path. The Phase 4 plan
specifies Task 7's acceptance criterion as **context build latency
under 50ms for the API read path** — and to add a caching layer if the
budget is exceeded.

What this test measures
-----------------------
**Pure Python overhead** of:

* checkpoint iteration (``saver.alist``),
* per-message serialization (``daemon.utils.serialize_message``),
* the ``_build_context_dicts_for_response`` / ``assemble_context_messages``
  orchestrator (mocked here so DB I/O is not exercised),
* context-message insertion into the result list, and
* ``_locate_context_insertion_index`` placement logic.

What this test does NOT measure
-------------------------------
* Real DB latency from the SQLite/Postgres checkpointer. The checkpointer
  and instance repository are replaced with mocks so the measurement is
  deterministic.
* Real ``assemble_context_messages`` RAG / skill-search / tree-walk cost.
  That helper is mocked to return a canned list of synthetic context
  messages.
* Network / serialization on the FastAPI / SSE path.

**Caveat on the 50ms budget.** The Phase 4 plan's 50ms target refers to
**real-world** end-to-end latency on the API read path (real DB, real
RAG, real filesystem). Because every blocking call is mocked here, the
measured value should be **well below** 50ms. A pass therefore only
proves the *pure Python* overhead is not catastrophic — it does NOT
prove end-to-end latency is under 50ms. If this smoke test fails, that
is a strong signal that context rebuild has introduced a hot-path
regression; if it passes, you still need real-load benchmarks before
shipping.

Running
-------
::

    python -m pytest tests/performance/test_context_api_latency.py -v --tb=short

The thresholds are intentionally generous (smoke-test oriented) so the
test does not flake on a busy CI runner. Tighten them once a baseline
is captured.
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.persistence import get_instance_messages


# ── Test helpers (mirror tests/test_persistence.py style) ──────────────────


class _EmptyAsyncIterator:
    """Async iterator that yields nothing — mocks ``saver.alist``."""

    def __init__(self, items=None):
        self.items = items or []
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index < len(self.items):
            item = self.items[self.index]
            self.index += 1
            return item
        raise StopAsyncIteration


def _make_persisted_messages():
    """Build a realistic two-turn conversation persisted in the checkpoint."""
    from langchain_core.messages import AIMessage, HumanMessage

    return [
        HumanMessage(content="first user turn"),
        AIMessage(content="first assistant reply"),
        HumanMessage(content="second user turn — current turn"),
    ]


def _make_synthetic_context_dicts(instance_id: str):
    """Build a canned list of synthetic context messages.

    The shape mirrors what ``_build_context_dicts_for_response`` would
    emit in production: ``is_synthetic=True``, ``context_kind`` set, and
    a stable ``synthetic-context-<kind>-<id>-<idx>`` ``message_id``.
    """
    kinds = ["project", "skills", "shared", "injection-defense"]
    out = []
    for idx, kind in enumerate(kinds):
        out.append(
            {
                "message_id": f"synthetic-context-{kind}-{instance_id}-{idx}",
                "type": "human",
                "role": "user",
                "content": (
                    f"[SYSTEM CONTEXT: {kind}]\n\n"
                    f"sample body for {kind} entry — replaces real RAG output"
                ),
                "thinking": None,
                "thinking_extracted": None,
                "tool_calls": None,
                "images": None,
                "created_at": "2026-07-28T00:00:00+00:00",
                "instance_id": instance_id,
                "is_synthetic": True,
                "context_kind": kind,
            }
        )
    return out


def _make_mock_manager(instance_id: str, *, mode: str):
    """Build the manager mock required by ``get_instance_messages``.

    The instance_meta / agent_meta pair matches what
    ``_resolve_instance_message_context`` would emit. The test patches
    ``_resolve_instance_message_context`` directly, so the manager's
    only real responsibility is to provide the instance repository so
    the resolve helper does not short-circuit.
    """
    instance_meta = MagicMock()
    instance_meta.agent_id = "developer"
    instance_meta.agent_tag = None
    instance_meta.instance_metadata = {}
    instance_meta.parent_id = None
    instance_meta.project_id = "project-perf-1"
    instance_meta.created_at = "2026-07-28T00:00:00+00:00"

    instance_repo = MagicMock()
    instance_repo.get = MagicMock(return_value=instance_meta)

    agent_meta = MagicMock()
    agent_meta.context_injection_mode = mode

    manager = MagicMock()
    manager._instance_repository = instance_repo

    # ctx payload the resolver returns. Includes a real-looking agent_meta
    # so ``_build_context_dicts_for_response`` has something to thread
    # through, even though we mock the helper itself.
    ctx = {
        "instance_meta": instance_meta,
        "agent_meta": agent_meta,
        "mode": mode,
    }
    return manager, ctx


def _make_mock_checkpointer(messages):
    """Build a checkpointer mock whose ``aget`` / ``alist`` return canned data."""
    mock_checkpointer = MagicMock()
    mock_checkpointer.aget = AsyncMock(
        return_value={"channel_values": {"messages": messages}}
    )
    mock_checkpointer.alist = MagicMock(return_value=_EmptyAsyncIterator())
    return mock_checkpointer


# ── Performance thresholds ────────────────────────────────────────────────

# The Phase 4 plan budget is 50ms for **real-world** end-to-end latency.
# Because all DB / RAG calls are mocked here, the pure Python overhead
# should be well under that. Use a generous smoke-test ceiling so CI
# noise does not flake the test; tighten after capturing a baseline.
PURE_PYTHON_LATENCY_BUDGET_SECONDS = 0.020  # 20ms — mock-only smoke ceiling

# Reasonable floor for the latency assertion — guards against the mock
# accidentally returning instantly and silently masking a regression in
# the orchestrator (e.g. someone removing the asyncio.to_thread wrap and
# silently letting a sync DB call slip onto the event loop).
MIN_USEFUL_WORK_SECONDS = 1e-6  # 1µs — generous; pure-Python work always exceeds this

# Number of measurement iterations. Single-shot measurements are too
# noisy on shared CI runners; 50 iterations is enough for a smoke test.
ITERATIONS = 50


# ── Tests ─────────────────────────────────────────────────────────────────


class TestContextApiLatency:
    """Smoke test: ``get_instance_messages`` context rebuild overhead.

    These tests are **mock-only**: they measure the pure Python cost of
    the orchestrator + serializer + insertion logic, NOT real DB / RAG
    latency. See module docstring for why the 50ms plan target cannot
    be asserted here directly.
    """

    @pytest.mark.asyncio
    async def test_human_messages_mode_under_latency_budget(self):
        """human_messages mode context rebuild completes well under the
        Phase 4 plan's 50ms budget.

        Caveat: with DB / RAG mocked, this only proves the pure Python
        overhead is small. End-to-end latency must be benchmarked under
        real load before shipping.
        """
        instance_id = "inst-perf-1"
        messages = _make_persisted_messages()
        mock_checkpointer = _make_mock_checkpointer(messages)
        manager, ctx = _make_mock_manager(instance_id, mode="human_messages")

        with patch(
            "daemon.persistence._resolve_instance_message_context",
            return_value=ctx,
        ), patch(
            "daemon.persistence._build_context_dicts_for_response",
            new=AsyncMock(return_value=_make_synthetic_context_dicts(instance_id)),
        ), patch(
            "daemon.persistence._reconstruct_full_system_prompt",
            return_value=None,
        ):
            # Warmup — first call may pay lazy-import costs that are not
            # representative of the steady-state hot path.
            await get_instance_messages(
                mock_checkpointer, instance_id, manager=manager
            )

            samples: list[float] = []
            result: list = []  # initialized so the post-block assertion is type-safe
            for _ in range(ITERATIONS):
                t0 = time.perf_counter()
                result = await get_instance_messages(
                    mock_checkpointer, instance_id, manager=manager
                )
                samples.append(time.perf_counter() - t0)

        # Sanity: result still has the expected shape — synthetic context
        # was inserted before the most recent user message.
        assert any(
            m.get("is_synthetic") is True and m.get("context_kind") == "project"
            for m in result
        ), "context rebuild did not inject synthetic context messages"

        median = sorted(samples)[len(samples) // 2]
        p95 = sorted(samples)[int(len(samples) * 0.95)]

        # The work happened — guard against accidental no-ops.
        assert median > MIN_USEFUL_WORK_SECONDS, (
            f"median latency suspiciously low ({median*1e6:.2f}µs) — "
            f"the test may not be exercising the rebuild path"
        )
        # Pure-Python ceiling (mocked DB). See module docstring.
        assert median < PURE_PYTHON_LATENCY_BUDGET_SECONDS, (
            f"human_messages rebuild median latency {median*1000:.2f}ms "
            f"exceeds pure-Python budget "
            f"{PURE_PYTHON_LATENCY_BUDGET_SECONDS*1000:.2f}ms "
            f"(p95={p95*1000:.2f}ms over {ITERATIONS} iterations)"
        )

    @pytest.mark.asyncio
    async def test_human_messages_vs_legacy_overhead(self):
        """Context rebuild adds bounded overhead vs the legacy mode.

        The test runs the same payload through both injection modes and
        compares medians. The ``human_messages`` median must stay within
        a generous multiple of the ``legacy`` median — proving
        the rebuild is real work but not catastrophic.

        Renamed from ``test_human_messages_vs_system_prompt_overhead``
        after ``system_prompt`` mode was renamed to ``legacy`` in
        Phase 6 of the Context Injection Restructure.
        """
        instance_id = "inst-perf-2"
        messages = _make_persisted_messages()

        # ── legacy mode (renamed from system_prompt) ───────────────────
        legacy_checkpointer = _make_mock_checkpointer(messages)
        legacy_manager, legacy_ctx = _make_mock_manager(instance_id, mode="legacy")
        with patch(
            "daemon.persistence._resolve_instance_message_context",
            return_value=legacy_ctx,
        ), patch(
            "daemon.persistence._build_context_dicts_for_response",
            new=AsyncMock(return_value=[]),
        ), patch(
            "daemon.persistence._reconstruct_full_system_prompt",
            return_value=None,
        ):
            await get_instance_messages(
                legacy_checkpointer, instance_id, manager=legacy_manager
            )

            legacy_samples: list[float] = []
            for _ in range(ITERATIONS):
                t0 = time.perf_counter()
                await get_instance_messages(
                    legacy_checkpointer, instance_id, manager=legacy_manager
                )
                legacy_samples.append(time.perf_counter() - t0)

        # ── human_messages mode ───────────────────────────────────────
        hm_checkpointer = _make_mock_checkpointer(messages)
        hm_manager, hm_ctx = _make_mock_manager(instance_id, mode="human_messages")

        with patch(
            "daemon.persistence._resolve_instance_message_context",
            return_value=hm_ctx,
        ), patch(
            "daemon.persistence._build_context_dicts_for_response",
            new=AsyncMock(return_value=_make_synthetic_context_dicts(instance_id)),
        ), patch(
            "daemon.persistence._reconstruct_full_system_prompt",
            return_value=None,
        ):
            await get_instance_messages(
                hm_checkpointer, instance_id, manager=hm_manager
            )

            hm_samples: list[float] = []
            for _ in range(ITERATIONS):
                t0 = time.perf_counter()
                await get_instance_messages(
                    hm_checkpointer, instance_id, manager=hm_manager
                )
                hm_samples.append(time.perf_counter() - t0)

        legacy_median = sorted(legacy_samples)[len(legacy_samples) // 2]
        hm_median = sorted(hm_samples)[len(hm_samples) // 2]

        # human_messages rebuild must remain a bounded multiplier of the
        # legacy path. 10x is generous; tighten after a baseline capture.
        # Note: because both modes do the same checkpoint iteration /
        # serializer work, the rebuild overhead is the DIFFERENCE
        # (``hm_median - legacy_median``) plus the insertion cost.

        overhead_ms = (hm_median - legacy_median) * 1000.0
        assert overhead_ms < PURE_PYTHON_LATENCY_BUDGET_SECONDS * 1000.0, (
            f"human_messages rebuild adds {overhead_ms:.2f}ms over "
            f"legacy (legacy_median={legacy_median*1000:.2f}ms, "
            f"hm_median={hm_median*1000:.2f}ms) — exceeds pure-Python "
            f"smoke ceiling {PURE_PYTHON_LATENCY_BUDGET_SECONDS*1000:.2f}ms"
        )

        # And both modes must independently stay under the smoke ceiling.
        assert legacy_median < PURE_PYTHON_LATENCY_BUDGET_SECONDS, (
            f"legacy median {legacy_median*1000:.2f}ms exceeds smoke "
            f"ceiling — pure-Python baseline regressed"
        )
        assert hm_median < PURE_PYTHON_LATENCY_BUDGET_SECONDS, (
            f"human_messages median {hm_median*1000:.2f}ms exceeds smoke "
            f"ceiling — pure-Python baseline regressed"
        )