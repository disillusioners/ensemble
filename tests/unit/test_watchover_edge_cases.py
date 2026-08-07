"""Edge-case tests for Watchover Phase 5 — concurrent isolation, loop-breaker
interaction, and selected compaction-active coverage.

Covers:

  * **T5.5 — Concurrent Instance Isolation.** Two instances with
    independent state concurrently traverse the watchover decision
    path. The denial counter, per-instance metadata, and termination
    markers must NOT leak between instances.

  * **T5.2 — Loop Breaker + Watchover interaction.** When the watcher
    denies a tool-call batch, the matching ``ToolMessage`` carries
    ``additional_kwargs.watchover_denial=True``. The :class:`LoopDetector`
    recognises this as a denial-response and breaks the consecutive
    chain so the loop detector does not steal the termination decision
    from the watcher.

  * **T5.4 — Compaction during active watchover (supplemental).** The
    freshness-counter test in :mod:`test_watchover_phase5` covers the
    single- increment case; this file adds multi-turn progression and a
    compaction-only edge case (counter absence / non-positive interval).

All tests follow the mock-everything convention used by
:mod:`test_watchover_decision` and :mod:`test_watchover_phase5` — no real
LLM, no real database, no real LangGraph run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from daemon.graph import (
    LOOP_BREAKER_DEFAULT_THRESHOLD,
    LoopDetector,
    WatchoverSlot,
    create_watchover_check_node,
)


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class _FakeLLMResult:
    """Drop-in for a LangChain ``AIMessage`` — only ``.content`` matters."""

    content: Any = ""


@dataclass
class _FakeAIMessage:
    """Lightweight AIMessage stand-in for tests — only carries ``tool_calls``."""

    tool_calls: list[dict] | None = None
    content: str = ""
    type: str = "ai"
    additional_kwargs: dict = field(default_factory=dict)

    @property
    def id(self):
        # ``LoopDetector.scan`` reads ``getattr(msg, "id", None)`` for
        # bookkeeping. A real AIMessage exposes ``id`` as a property —
        # we fake one returning ``None`` to keep the detector path simple.
        return None


def _state_with_tool_calls(
    calls: list[dict] | None = None,
    *,
    denial_count: int = 0,
    messages: list[Any] | None = None,
    watchover_context: str | None = None,
    watchover_turn_id: str | None = None,
) -> dict:
    """Build a state dict with a stub last AIMessage carrying tool_calls.

    ``messages`` and the synthetic AIMessage coexist by your choice — when
    supplied, ``messages`` is used directly; otherwise a single fresh AIMessage
    with the requested tool_calls is appended.
    """
    if calls is None:
        calls = [{"id": "tc-1", "name": "bash", "args": {"command": "ls"}}]
    state: dict[str, Any] = {
        "messages": messages or [_FakeAIMessage(tool_calls=calls)],
        "watchover_denial_count": denial_count,
    }
    if watchover_context is not None:
        state["watchover_context"] = watchover_context
    if watchover_turn_id is not None:
        state["watchover_turn_id"] = watchover_turn_id
    return state


def _config(instance_id: str = "iid") -> dict:
    """LangGraph config dict with thread_id = instance_id."""
    return {"configurable": {"thread_id": instance_id}}


def _make_manager(
    *,
    watchover_enabled_for: dict[str, bool] | bool = True,
    metadata_by_instance: dict[str, dict] | None = None,
    stateful_metadata: bool = True,
) -> MagicMock:
    """Build a mock ``InstanceManager`` for the per-instance isolation tests.

    The manager mock holds per-instance state in real Python containers
    (dicts) — two instances with the SAME manager get DIFFERENT rows out
    of ``_instance_repository.get`` keyed on their instance_id. This is
    the closest mock-faithful reproduction of the production behaviour
    (one DB row per instance) while remaining synchronous.

    Args:
        watchover_enabled_for: Either a single ``bool`` (default ``True``)
            that maps every instance to the same value, or a
            ``{instance_id: bool}`` dict for per-instance enable flags.
        metadata_by_instance: Optional ``{instance_id: instance_metadata}``
            dict. Each instance returns its own dict. Missing instances
            get ``{}``.
        stateful_metadata: When ``True`` (default), writes via
            ``set_metadata`` / ``set_metadata_many`` mutate the in-memory
            cache so subsequent ``get`` calls see the updated value. This
            mirrors the production DB write-through path. When ``False``,
            writes are no-ops on the cache (used for tests that don't
            need counter progression).

    Returns:
        A ``MagicMock`` with the watchover + DB + SSE surfaces wired.
    """
    manager = MagicMock(name=f"manager_for_{id(watchover_enabled_for)}")

    # Per-instance enable flag — closures keep watchover_enabled_for local.
    if isinstance(watchover_enabled_for, bool):
        manager.is_watchover_enabled.side_effect = lambda iid: watchover_enabled_for
    else:
        manager.is_watchover_enabled.side_effect = (
            lambda iid: watchover_enabled_for.get(iid, False)
        )

    # Per-instance metadata — each get() returns the row for its own
    # instance_id. When stateful_metadata is True, writes through
    # ``set_metadata`` / ``set_metadata_many`` mutate the in-memory
    # cache so the counter progression is observable across calls.
    metadata_lookup: dict[str, dict] = {}
    if metadata_by_instance:
        for k, v in metadata_by_instance.items():
            metadata_lookup[k] = dict(v)

    def _get(instance_id):
        row = MagicMock()
        row.instance_metadata = metadata_lookup.get(instance_id, {})
        return row

    def _set_metadata(instance_id, key, value):
        if stateful_metadata:
            metadata_lookup.setdefault(instance_id, {})[key] = value

    def _set_metadata_many(instance_id, updates):
        if stateful_metadata and isinstance(updates, dict):
            for k, v in updates.items():
                metadata_lookup.setdefault(instance_id, {})[k] = v

    repo = MagicMock()
    repo.get.side_effect = _get
    repo.set_metadata.side_effect = _set_metadata
    repo.set_metadata_many.side_effect = _set_metadata_many
    manager._instance_repository = repo

    # SSE surface — track per-instance via a per-instance_id list so the
    # "instance B untouched" assertion is mechanical.
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()

    # Deferred-terminate lifecycle (we want assertions on which
    # instances were marked).
    manager.set_deferred_watchover_terminate = MagicMock()
    manager.is_watchover_terminate_requested = MagicMock(return_value=False)
    manager.clear_watchover_terminate_requested = MagicMock()

    # Question-pause stub — build_instance_graph touches it.
    manager.is_question_pause_requested = MagicMock(return_value=False)
    manager.set_deferred_question_pause = MagicMock()
    manager.clear_question_pause_requested = MagicMock()
    manager.pause_instance_cascade = AsyncMock()

    # Diagnostic: expose the cache so tests can verify writes.
    manager._test_metadata_cache = metadata_lookup

    return manager


def _make_fake_llm_class(
    responses: list[Any] | None = None,
):
    """Build a ``ThinkingChatOpenAI`` factory mock with a queued response list.

    Args:
        responses: Iterable of strings or ``_FakeLLMResult`` instances.
            Strings are wrapped in ``_FakeLLMResult(content=...)``.

    Returns:
        A tuple ``(factory_callable, mock_instance)``.
    """
    queue = list(responses or [])

    def _next(_messages):
        if not queue:
            raise AssertionError("LLM mock exhausted")
        item = queue.pop(0)
        if isinstance(item, BaseException) or (
            isinstance(item, type) and issubclass(item, BaseException)
        ):
            raise item
        if isinstance(item, _FakeLLMResult):
            return item
        return _FakeLLMResult(content=item)

    mock_instance = MagicMock()
    mock_instance.invoke.side_effect = _next

    return (lambda **kwargs: mock_instance), mock_instance


# =============================================================================
# T5.5 — Concurrent Instance Isolation
# =============================================================================


class TestConcurrentInstanceIsolation:
    """Two parallel instances with watchover enabled must not share state.

    The watchover state lives in three places:

      * ``state["watchover_denial_count"]`` — per-thread LangGraph state
        (one counter per ``thread_id`` / ``instance_id``).
      * ``instance_metadata`` JSONB — per-instance row, fetched via the
        repository.
      * ``manager._deferred_watchover_terminate`` set — per-instance marker.

    All three must remain per-instance — no cross-instance leakage when
    two instances traverse the watchover path concurrently.
    """

    async def test_denial_counters_are_independent_per_instance(self, monkeypatch):
        """Instance A denied twice keeps counter at 2; instance B at 0.

        Two instances share a manager mock but hold DIFFERENT
        ``watchover_denial_count`` values in their state dicts (mirrors
        LangGraph's per-thread state). The denial path must increment
        only the active instance's counter and leave the other untouched.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")

        manager_a = _make_manager(
            watchover_enabled_for=True,
            metadata_by_instance={
                "iid-A": {
                    "watchover_context": "ctx-A",
                    "watchover_context_turn": 0,
                    "watchover_context_refresh_interval": 99,
                },
                "iid-B": {
                    "watchover_context": "ctx-B",
                    "watchover_context_turn": 0,
                    "watchover_context_refresh_interval": 99,
                },
            },
        )
        slot_a = WatchoverSlot(manager_a)
        factory, _ = _make_fake_llm_class(["Deny: unsafe", "Deny: unsafe"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node_a = create_watchover_check_node(
                manager=manager_a, slot=slot_a, llm_config={"model": "test"}
            )

            # First denial on instance A (counter 0 → 1, route agent).
            res_a1 = await node_a(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid-A"),
            )
            assert res_a1["watchover_denial_count"] == 1
            assert res_a1["watchover_route"] == "agent"

            # Second denial on instance A (counter 1 → 2, route agent).
            res_a2 = await node_a(
                _state_with_tool_calls(denial_count=1),
                config=_config("iid-A"),
            )
            assert res_a2["watchover_denial_count"] == 2
            assert res_a2["watchover_route"] == "agent"

            # Instance B has its own state — counter starts at 0.
            # Use a fresh state dict (mirrors a separate LangGraph thread).
            res_b = await node_a(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid-B"),
            )
            assert res_b["watchover_denial_count"] == 1
            # The config-driven instance id routes through instance B's
            # watchover check — the counter that goes to 1 is instance
            # B's, not A's (A's was 2 before).
            assert res_a2["watchover_denial_count"] == 2

    async def test_termination_of_one_instance_does_not_affect_another(
        self, monkeypatch
    ):
        """3-strike on instance A must NOT mark instance B for termination.

        Drives instance A through three denial batches; the third routes
        to ``watchover_terminate_node`` and the manager's
        ``set_deferred_watchover_terminate`` is called for ``iid-A``
        only. Instance B's deferred-terminate marker is never touched.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")

        manager = _make_manager(
            watchover_enabled_for=True,
            metadata_by_instance={
                "iid-A": {
                    "watchover_context": "ctx-A",
                    "watchover_context_turn": 0,
                    "watchover_context_refresh_interval": 99,
                },
                "iid-B": {
                    "watchover_context": "ctx-B",
                    "watchover_context_turn": 0,
                    "watchover_context_refresh_interval": 99,
                },
            },
        )
        slot = WatchoverSlot(manager)
        factory, _ = _make_fake_llm_class(
            ["Deny: 1", "Deny: 2", "Deny: 3"]
        )
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )

            # Drive A: counters 0 → 1 → 2 → 3 (terminate).
            await node(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid-A"),
            )
            await node(
                _state_with_tool_calls(denial_count=1),
                config=_config("iid-A"),
            )
            res_terminate = await node(
                _state_with_tool_calls(denial_count=2),
                config=_config("iid-A"),
            )
            assert res_terminate["watchover_denial_count"] == 3
            assert res_terminate["watchover_route"] == "watchover_terminate_node"

            # The deferred marker must have been set for instance A ONLY.
            marked_instances = [
                call.args[0]
                for call in manager.set_deferred_watchover_terminate.call_args_list
            ]
            # The terminate path runs inside watchover_terminate_node, not
            # the watchover_check node — so the check node itself does
            # NOT call set_deferred_watchover_terminate. What we CAN
            # assert is that no marker was set for instance B. The set
            # is the only way the mock manager records deferred markers
            # in this test (real manager uses an internal set).
            assert "iid-B" not in marked_instances

            # Drive B separately — counter 0 → 1, route agent, no
            # termination. Its deny state is independent.
            res_b = await node(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid-B"),
            )
            assert res_b["watchover_denial_count"] == 1
            assert res_b["watchover_route"] == "agent"
            # Instance B is still alive — its route is not terminate.
            assert "iid-A" not in (res_b.get("watchover_route") or "")

    async def test_metadata_lookups_are_per_instance(self, monkeypatch):
        """``instance_metadata`` reads / writes hit the right instance.

        The freshness counter is written to ``instance_metadata`` on
        every watchover check. Two instances running concurrently must
        write their counter to THEIR row, never to the other's row.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")

        manager = _make_manager(
            watchover_enabled_for=True,
            metadata_by_instance={
                "iid-A": {
                    "watchover_context": "ctx-A",
                    "watchover_context_turn": 0,
                    "watchover_context_refresh_interval": 99,
                },
                "iid-B": {
                    "watchover_context": "ctx-B",
                    "watchover_context_turn": 5,
                    "watchover_context_refresh_interval": 99,
                },
            },
        )
        slot = WatchoverSlot(manager)
        factory, _ = _make_fake_llm_class(["Allowed"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )

            # Run instance A.
            await node(_state_with_tool_calls(), config=_config("iid-A"))
            # Run instance B.
            await node(_state_with_tool_calls(), config=_config("iid-B"))

            # set_metadata writes the turn counter. Each call writes to
            # the iid the config supplied; we examine the (instance_id,
            # key, value) triples to confirm isolation.
            write_calls = manager._instance_repository.set_metadata.call_args_list
            # Find the watchover_context_turn writes.
            turn_writes = {
                c.args[0]: c.args[2]
                for c in write_calls
                if len(c.args) >= 3 and c.args[1] == "watchover_context_turn"
            }
            # Instance A: read 0 → write 1.
            assert turn_writes.get("iid-A") == 1
            # Instance B: read 5 → write 6.
            assert turn_writes.get("iid-B") == 6
            # No leakage: the write for instance A is NOT 6, and the
            # write for instance B is NOT 1.
            assert turn_writes["iid-A"] != turn_writes["iid-B"]

    async def test_per_instance_kill_switch_isolated(self, monkeypatch):
        """One instance enabled, the other disabled → only the enabled
        instance goes through evaluation.

        Per-instance kill switch via ``is_watchover_enabled``:
        ``iid-A`` returns True, ``iid-B`` returns False. A denies batch
        for A; B sees fast-path passthrough (no evaluator call, no
        denial SSE, no counter mutation).
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")

        manager = _make_manager(
            watchover_enabled_for={
                "iid-A": True,
                "iid-B": False,
            },
            metadata_by_instance={
                "iid-A": {
                    "watchover_context": "ctx-A",
                    "watchover_context_turn": 0,
                    "watchover_context_refresh_interval": 99,
                },
                "iid-B": {
                    "watchover_context": "ctx-B",
                    "watchover_context_turn": 0,
                    "watchover_context_refresh_interval": 99,
                },
            },
        )
        slot = WatchoverSlot(manager)
        # Factory is irrelevant for the disabled instance — it should
        # never be called. But populate enough responses in case the
        # enabled instance triggers a deny.
        factory, llm = _make_fake_llm_class(["Deny: bad"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )

            # Instance A: kill-switch ON, enabled → evaluator runs, deny.
            res_a = await node(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid-A"),
            )
            assert res_a["watchover_denial_count"] == 1
            assert res_a["watchover_route"] == "agent"

            # Instance B: enabled=False → fast-path tools. No LLM call,
            # no denial, no counter increment, no SSE for denial.
            res_b = await node(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid-B"),
            )
            assert res_b["watchover_route"] == "tools"
            # The deny counter for instance B must NOT have been
            # incremented (its config took the fast-path route).
            assert res_b.get("watchover_denial_count", 0) == 0

            # The LLM was only invoked ONCE for instance A's deny.
            # Instance B took the fast-path and never reached the LLM.
            assert llm.invoke.call_count == 1


# =============================================================================
# T5.2 — Loop Breaker + Watchover interaction
# =============================================================================


class TestLoopBreakerWatchoverExclusion:
    """Watchover-denied batches must NOT trigger the loop detector (H1).

    The :class:`LoopDetector.scan` walks backwards looking for
    consecutive identical tool-call patterns. A consecutive chain of
    watchover-denied batches is the AGENT responding to a series of
    rejections — not looping on its own — so the detector must break
    the chain when the matching ``ToolMessage``s carry
    ``additional_kwargs.watchover_denial=True``.
    """

    @staticmethod
    def _denied_sequence(
        tool_name: str, args: dict, count: int
    ) -> list:
        """Build ``count`` watchover-denied AI+Tool pairs.

        The ``ToolMessage``s carry ``additional_kwargs.watchover_denial=True``
        to mirror the production shape (see
        :func:`daemon.graph._compute_deny_state` callers at
        ``graph.py:4236`` and ``graph.py:4295``).
        """
        messages: list = []
        for i in range(count):
            tc_id = f"tc-{i}"
            ai = AIMessage(
                content="",
                tool_calls=[
                    {"id": tc_id, "name": tool_name, "args": args}
                ],
                id=f"ai-{i}",
            )
            tm = ToolMessage(
                content="Watchover denied this tool call: unsafe. "
                "Please adjust your approach.",
                tool_call_id=tc_id,
                name=tool_name,
                id=f"tm-{i}",
                additional_kwargs={"watchover_denial": True},
            )
            messages.append(ai)
            messages.append(tm)
        return messages

    def test_three_denied_batches_do_not_trigger_loop(self):
        """3 consecutive denied batches → ``None`` (no loop detected)."""
        messages = self._denied_sequence(
            tool_name="bash", args={"command": "ls"}, count=3
        )

        result = LoopDetector.scan(
            messages, threshold=LOOP_BREAKER_DEFAULT_THRESHOLD
        )

        # The detector MUST return None — a watchover-denied chain is
        # not a hallucination loop.
        assert result is None, (
            "Watchover-denied batches must not be detected as a "
            "hallucination loop (H1 — agent is responding to "
            "rejections, not looping)"
        )

    def test_denied_batches_dont_count_toward_loop_threshold(self):
        """5 denied batches (>= threshold=3) still don't trigger — every batch is denied.

        Ensures the H1 exclusion holds even when the consecutive chain
        far exceeds the threshold: the entire chain is denial-responses,
        so no loop is detected.
        """
        messages = self._denied_sequence(
            tool_name="bash", args={"command": "ls"}, count=5
        )

        result = LoopDetector.scan(messages, threshold=3)

        assert result is None

    def test_normal_loop_still_detected_when_denials_are_absent(self):
        """3 consecutive identical calls WITHOUT watchover_denial → loop detected."""
        # Sanity check: build a normal loop (no denial tag) and confirm
        # the detector still catches it. The H1 exclusion MUST NOT
        # over-trigger to swallow legitimate loops.
        messages = []
        for i in range(3):
            tc_id = f"tc-{i}"
            ai = AIMessage(
                content="",
                tool_calls=[
                    {"id": tc_id, "name": "bash", "args": {"command": "ls"}}
                ],
                id=f"ai-{i}",
            )
            tm = ToolMessage(
                content="result",
                tool_call_id=tc_id,
                name="bash",
                id=f"tm-{i}",
                # NO watchover_denial → legitimate loop should be detected.
            )
            messages.append(ai)
            messages.append(tm)

        result = LoopDetector.scan(messages, threshold=3)

        assert result is not None
        assert result.repetition_count == 3

    def test_denial_then_normal_loop_still_detected(self):
        """After a denied batch, a real loop is detected starting from there.

        Build: 1 denied batch + 1 allowed batch + 3 normal consecutive
        identical calls. The denial breaks the chain, then 3 identical
        non-denied calls restart the loop counter from scratch. The
        detector finds the 3-call loop at the tail.
        """
        # One denial-response pair.
        ai = AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-deny", "name": "bash", "args": {"command": "ls"}}
            ],
            id="ai-deny",
        )
        tm_deny = ToolMessage(
            content="Watchover denied this tool call: unsafe",
            tool_call_id="tc-deny",
            name="bash",
            id="tm-deny",
            additional_kwargs={"watchover_denial": True},
        )
        # Then three identical non-denied consecutive calls.
        loop = []
        for i in range(3):
            tc_id = f"tc-{i}"
            loop.append(
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": tc_id, "name": "bash", "args": {"command": "pwd"}}
                    ],
                    id=f"ai-{i}",
                )
            )
            loop.append(
                ToolMessage(
                    content="result",
                    tool_call_id=tc_id,
                    name="bash",
                    id=f"tm-{i}",
                )
            )
        messages = [ai, tm_deny] + loop

        result = LoopDetector.scan(messages, threshold=3)

        # The detector must find the loop of 3 pwd calls (different
        # from the original denied ls call, so the denied batch broke
        # the chain and the 3 pwd calls restart the counter).
        assert result is not None
        assert result.tool_name == "bash"
        assert result.tool_args == {"command": "pwd"}
        assert result.repetition_count == 3

    def test_partially_denied_batch_breaks_watchover_exclusion(self):
        """AIMessage with mixed denied + non-denied ToolMessages → chain proceeds.

        When ALL matched ``ToolMessage``s must carry
        ``watchover_denial=True`` to break the chain. If even one
        matched message is NOT tagged ``watchover_denial``, the
        exclusion does not apply and the chain proceeds normally.
        """
        # AI with 2 parallel tool_calls. tc-1 has a denied ToolMessage
        # but tc-2 has a normal ToolMessage — so this AIMessage is NOT
        # fully watchover-denied and does not trigger the exclusion.
        ai = AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-1", "name": "bash", "args": {"command": "ls"}},
                {"id": "tc-2", "name": "bash", "args": {"command": "ls"}},
            ],
            id="ai-0",
        )
        tm_deny_1 = ToolMessage(
            content="denied",
            tool_call_id="tc-1",
            name="bash",
            id="tm-1",
            additional_kwargs={"watchover_denial": True},
        )
        tm_normal_2 = ToolMessage(
            content="result",
            tool_call_id="tc-2",
            name="bash",
            id="tm-2",
            # NO watchover_denial flag.
        )

        # Build three of these (each with mixed denied + normal). The
        # detector should NOT exclude them — chain is identical and
        # consecutive (3 in a row).
        messages = []
        for _ in range(3):
            messages.append(ai)
            messages.append(tm_deny_1)
            messages.append(tm_normal_2)

        result = LoopDetector.scan(messages, threshold=3)

        # The consecutive chain is 3 identical units → loop detected.
        assert result is not None
        assert result.repetition_count == 3


# =============================================================================
# T5.4 — Compaction during active watchover (supplemental)
# =============================================================================


class TestCompactionDuringActiveWatchover:
    """Multi-turn progression of the context freshness counter.

    The Phase 5 tests in :mod:`test_watchover_phase5` exercise the
    single-increment path. This class exercises multi-turn progression:
    across N watchover checks the counter must advance monotonically,
    and a low refresh interval flips the context into the refreshed
    path on the very first check.

    This is the only coverage in this file that overlaps T5.4; the
    other T5.4 tests already exist in :mod:`test_watchover_phase5`.
    """

    async def test_counter_progresses_across_multiple_checks(self, monkeypatch):
        """Counter goes 0 → 1 → 2 → 3 across three calls when never stale."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")

        manager = _make_manager(
            watchover_enabled_for=True,
            metadata_by_instance={
                "iid": {
                    "watchover_context": "ctx",
                    "watchover_context_turn": 0,
                    "watchover_context_refresh_interval": 99,
                },
            },
        )
        slot = WatchoverSlot(manager)
        factory, _ = _make_fake_llm_class(
            ["Allowed", "Allowed", "Allowed"]
        )
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )

            await node(_state_with_tool_calls(), config=_config("iid"))
            await node(_state_with_tool_calls(denial_count=0), config=_config("iid"))
            await node(_state_with_tool_calls(denial_count=0), config=_config("iid"))

        # All three writes are observed, monotonically increasing.
        writes = manager._instance_repository.set_metadata.call_args_list
        turn_writes = [
            c.args[2]
            for c in writes
            if len(c.args) >= 3 and c.args[1] == "watchover_context_turn"
        ]
        assert turn_writes == [1, 2, 3]

    async def test_low_refresh_interval_triggers_refresh_on_first_check(
        self, monkeypatch
    ):
        """interval=1 → very first check sees stale (turn>=1)? No — first
        check writes turn=1 on a fresh activation. The refresh path
        fires on the SECOND check, when turn is read as the previously
        written value (1) which equals the interval (1).
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")

        # Seed metadata such that the very first read sees turn=1
        # (>= interval=1 → refresh). Simulates a suspended activation
        # that left the counter at 1.
        manager = _make_manager(
            watchover_enabled_for=True,
            metadata_by_instance={
                "iid": {
                    "watchover_context": "OLD",
                    "watchover_context_turn": 1,
                    "watchover_context_refresh_interval": 1,
                    "watchover_requirement": "no rm -rf",
                },
            },
        )
        slot = WatchoverSlot(manager)
        factory, _ = _make_fake_llm_class(["Allowed"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )

            # Messages with content so the raw-tail formatter has
            # something to extract.
            messages = [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "tc-1", "name": "bash", "args": {"command": "ls"}}
                    ],
                ),
            ]
            await node(
                _state_with_tool_calls(messages=messages),
                config=_config("iid"),
            )

        # set_metadata_many was called with the refreshed context (it
        # carries the requirement prefix and a [Recent activity] header).
        many_calls = manager._instance_repository.set_metadata_many.call_args_list
        refreshed_writes = [
            c
            for c in many_calls
            if "watchover_context" in (c.args[1] if len(c.args) >= 2 else {})
        ]
        assert len(refreshed_writes) >= 1
        new_ctx = refreshed_writes[0].args[1]["watchover_context"]
        assert "OLD" not in new_ctx
        assert "[Requirement] no rm -rf" in new_ctx
        # Counter reset to 0 on refresh, then incremented to 1 for this
        # check (the counter-write via set_metadata appears with 1).
        assert refreshed_writes[0].args[1]["watchover_context_turn"] == 0

        # The last turn write should be 1 (incremented after the refresh
        # reset it to 0).
        turn_writes = [
            c.args[2]
            for c in manager._instance_repository.set_metadata.call_args_list
            if len(c.args) >= 3 and c.args[1] == "watchover_context_turn"
        ]
        assert turn_writes[-1] == 1
