"""Empty-response-guard Phase 1 — router caps, burn bound, classifier/failover
ladder, telemetry streak, and grep-pins.

Covers the §10 test strategy of
``.agents/shared/planning/empty-response-guard/architecture-recommendation.md``:

* S5 derived caps on the router's degenerate re-invoke branches
  (reasoning-only / <think>-tag-only), with the burn assertion for both
  shapes (no-tool storm ≤ cap+2 LLM calls; with-tool shape ≤ 2×cap —
  the cap fall-through lands on the row-5 nudge recovery rung, then the
  nudge HumanMessage breaks ``_has_recent_tool_result`` → END) vs ~100
  legacy-burn calls, and the ghost-promise UNCAPPED pin.
* The kill-switch (doc §11(b)) disabling the ROUTER half too: with
  ``ENSEMBLE_EMPTY_RESPONSE_GUARD=0`` the cap never fires and
  ``_is_empty_content`` restores the exact legacy semantics, while ON
  keeps the shared-predicate results.
* The grep-pin single-caller invariant for ``_is_empty_content``.
* The S1 raise riding the existing retry → failover ladder: transient
  budget consumption, swap-to-backup at the primary transient cap, and
  loud exhaustion when no backup is configured.
* The manager-scoped RAM-only empty-response streak telemetry (non-gating).
"""

import ast
import pathlib
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from tenacity import Retrying, stop_after_attempt

import daemon.graph as graph_module
from daemon.graph import (
    EMPTY_DEGENERATE_REINVOKE_CAP,
    NUDGE_MESSAGE,
    _count_trailing_degenerate_ai_messages,
    _is_empty_content,
    should_continue,
)
from daemon.llm_error_classifier import (
    LLMResponseValidationError,
    classify_llm_errors,
    make_llm_retry_strategy,
)
from daemon.response_validation import (
    EmptyLLMResponseError,
    _reset_empty_guard_config_for_tests,
    install_empty_guard_config,
)


@pytest.fixture(autouse=True)
def _restore_empty_guard_defaults():
    _reset_empty_guard_config_for_tests()
    yield
    _reset_empty_guard_config_for_tests()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _real_human(text="Do the thing"):
    return HumanMessage(content=text)


def _reasoning_only():
    return AIMessage(content="", additional_kwargs={"reasoning_content": "thinking"})


def _think_only():
    return AIMessage(content="<think>hmm</think>")


def _ghost_promise():
    return AIMessage(content="Now let me write the document:")


def _tool_calling_ai():
    return AIMessage(
        content="",
        tool_calls=[{"name": "test_tool", "args": {}, "id": "call_1"}],
    )


def _tool_result():
    return ToolMessage(content="tool output", tool_call_id="call_1")


# ---------------------------------------------------------------------------
# S5 caps — router rows 2-3 (derived trailing-degenerate count)
# ---------------------------------------------------------------------------


class TestDegenerateReinvokeCap:
    """Cap on the unbounded reasoning-only / think-only re-invoke loops."""

    def _simulate_storm(self, make_degenerate, turns=8):
        """Drive should_continue over an accumulating degenerate tail.

        Mirrors the real graph cycle: the LLM keeps returning degenerate
        AIMessages; each ``"agent"`` route appends one (one LLM call) and
        re-routes. Returns (routes, llm_calls).
        """
        messages = [_real_human(), make_degenerate()]
        routes = []
        llm_calls = 1
        for _ in range(turns):
            route = should_continue({"messages": messages})
            routes.append(route)
            if route != "agent":
                break
            messages.append(make_degenerate())
            llm_calls += 1
        return routes, llm_calls

    def test_reasoning_only_storm_bounded(self):
        routes, llm_calls = self._simulate_storm(_reasoning_only)
        assert "agent" not in routes[EMPTY_DEGENERATE_REINVOKE_CAP - 1:]
        # Burn assertion: ≤ cap+2 LLM calls vs ~100 (GraphRecursionError) today.
        assert llm_calls <= EMPTY_DEGENERATE_REINVOKE_CAP + 2

    def test_think_tag_only_storm_bounded(self):
        routes, llm_calls = self._simulate_storm(_think_only)
        assert "agent" not in routes[EMPTY_DEGENERATE_REINVOKE_CAP - 1:]
        assert llm_calls <= EMPTY_DEGENERATE_REINVOKE_CAP + 2

    def test_mixed_degenerate_storm_bounded(self):
        """Alternating classes cannot evade the cap (mixed tail counting)."""
        makers = [_reasoning_only, _think_only]
        messages = [_real_human(), makers[0]()]
        llm_calls = 1
        for i in range(8):
            route = should_continue({"messages": messages})
            if route != "agent":
                break
            messages.append(makers[(i + 1) % 2]())
            llm_calls += 1
        assert llm_calls <= EMPTY_DEGENERATE_REINVOKE_CAP + 2

    def test_ghost_promise_deliberately_not_capped(self):
        # Doc L4: ghost-promise content is truthy real text under the
        # shared predicate — the S5 cap owns the EMPTY re-invoke classes
        # only. The ghost row keeps its pre-guard behavior.
        routes, _ = self._simulate_storm(_ghost_promise, turns=6)
        assert all(route == "agent" for route in routes)

    def test_cap_counting_helper(self):
        messages = [_real_human(), _reasoning_only(), _think_only(), _reasoning_only()]
        assert _count_trailing_degenerate_ai_messages(messages) == 3
        # A healthy AI message breaks the walk.
        messages2 = [_real_human(), _reasoning_only(), AIMessage(content="done")]
        assert _count_trailing_degenerate_ai_messages(messages2) == 0
        # Tool calls break the walk.
        messages3 = [_real_human(), _reasoning_only(), _tool_calling_ai()]
        assert _count_trailing_degenerate_ai_messages(messages3) == 0

    def test_nudge_flow_still_fires_after_cap_fallthrough(self):
        """Cap fall-through preserves the row-5 nudge (recovery rung)."""
        messages = [
            _real_human(),
            _tool_calling_ai(),
            _tool_result(),
            _reasoning_only(),
            _reasoning_only(),
            _reasoning_only(),  # cap reached
        ]
        route = should_continue({"messages": messages})
        assert route == "nudge"

    def _simulate_with_tool_storm(self, make_degenerate, turns=12):
        """Drive should_continue through the WITH-TOOL degenerate shape.

        Mirrors the real graph cycle after a tool result: degenerate
        responses route ``"agent"`` until the cap; the cap fall-through
        routes ``"nudge"`` (the row-5 recovery rung — nudge_node
        injects the nudge HumanMessage and the graph re-invokes the
        LLM); the post-nudge degenerate cycle re-fills the cap and then
        ends at END because the injected nudge HumanMessage breaks
        ``_has_recent_tool_result``. Returns ``(routes, llm_calls)``.
        """
        messages = [
            _real_human(),
            _tool_calling_ai(),
            _tool_result(),
            make_degenerate(),
        ]
        routes = []
        llm_calls = 1
        for _ in range(turns):
            route = should_continue({"messages": messages})
            routes.append(route)
            if route == "agent":
                messages.append(make_degenerate())
                llm_calls += 1
            elif route == "nudge":
                # nudge_node injects the HumanMessage, then the graph
                # re-invokes the LLM (one more call) before re-routing.
                messages.append(HumanMessage(content=NUDGE_MESSAGE))
                messages.append(make_degenerate())
                llm_calls += 1
            else:  # END
                break
        return routes, llm_calls

    def test_with_tool_degenerate_storm_bounded(self):
        """With-tool shape: the accurate worst case is ≤ 2×cap LLM calls.

        The old "≤ cap+2" claim ignored this shape: for EMPTY-content
        degenerates the cap fall-through routes to the row-5 nudge
        (recovery rung — deliberately kept), producing a second
        degenerate cycle before the nudge HumanMessage breaks
        ``_has_recent_tool_result`` → END (2×cap). Think-only
        degenerates carry non-empty content under the shared predicate,
        so their fall-through hits END directly (≤ cap+1). Bounded
        either way, never the ~100 legacy burn.
        """
        for maker in (_reasoning_only, _think_only):
            routes, llm_calls = self._simulate_with_tool_storm(maker)
            assert llm_calls <= 2 * EMPTY_DEGENERATE_REINVOKE_CAP
            assert routes[-1] != "agent"
            # Only empty-content degenerates reach the row-5 nudge;
            # think-only content fails the nudge gate → straight END.
            assert routes.count("nudge") == (1 if maker is _reasoning_only else 0)

    def test_cap_below_loop_detector_threshold_would_not_bound(self):
        """Sanity: the module default is 3 (LoopDetector threshold parity)."""
        assert EMPTY_DEGENERATE_REINVOKE_CAP == 3


# ---------------------------------------------------------------------------
# Kill-switch disables the ROUTER half too (doc §11(b) — follow-up pins)
# ---------------------------------------------------------------------------


class TestKillSwitchDisablesRouterHalf:
    """``ENSEMBLE_EMPTY_RESPONSE_GUARD=0`` must disable BOTH halves.

    The validator half (S1 Check 3) is pinned in
    ``test_response_validation.py::TestEmptyResponseGuardKillSwitch``;
    these pins close the router half (review follow-up): with OFF the
    S5 cap never fires (legacy unbounded re-invoke, WARN path included)
    and ``_is_empty_content`` restores its EXACT pre-guard legacy truth
    table. ON keeps the shared-predicate results unchanged. Flag
    flipping uses the same boot-installed source of truth via
    ``install_empty_guard_config`` (no second env read in the router).
    """

    def test_guard_off_cap_never_fires_no_tool_storm(self):
        """OFF: degenerate storms proceed UNBOUNDED at the router level."""
        install_empty_guard_config(enabled=False, compaction_skip=False)
        for maker in (_reasoning_only, _think_only):
            routes, _ = TestDegenerateReinvokeCap()._simulate_storm(
                maker, turns=EMPTY_DEGENERATE_REINVOKE_CAP + 3
            )
            assert all(route == "agent" for route in routes), (
                f"cap fired with the kill-switch OFF ({maker.__name__})"
            )

    def test_guard_off_cap_never_fires_with_tool_storm(self):
        """OFF: even the with-tool degenerate shape stays unbounded —
        no cap fall-through, so the row-5 nudge is never reached for
        the degenerate class (exact legacy routing)."""
        install_empty_guard_config(enabled=False, compaction_skip=False)
        for maker in (_reasoning_only, _think_only):
            routes, _ = TestDegenerateReinvokeCap()._simulate_with_tool_storm(
                maker, turns=2 * EMPTY_DEGENERATE_REINVOKE_CAP + 2
            )
            assert all(route == "agent" for route in routes), (
                f"cap/nudge fired with the kill-switch OFF ({maker.__name__})"
            )

    def test_guard_off_is_empty_content_legacy_truth_table(self):
        """OFF: ``_is_empty_content`` returns the pre-guard legacy results."""
        install_empty_guard_config(enabled=False, compaction_skip=False)
        # Legacy table (pre-abc226c7 body, restored byte-identically):
        assert _is_empty_content(None) is True
        assert _is_empty_content("") is True
        assert _is_empty_content("   \n\t ") is True
        assert _is_empty_content("text") is False
        # Previously-flipped pins — legacy: ANY list → False; every
        # other shape → False.
        assert _is_empty_content([]) is False
        assert _is_empty_content({}) is False
        assert _is_empty_content(123) is False
        assert _is_empty_content(
            [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
                {"type": "text", "text": " "},
            ]
        ) is False
        assert _is_empty_content(
            [{"type": "text", "text": "  "}, {"type": "text", "text": "\n"}]
        ) is False

    def test_guard_on_keeps_shared_predicate_results(self):
        """ON (default): the same shapes keep the new predicate results."""
        install_empty_guard_config(enabled=True, compaction_skip=False)
        assert _is_empty_content(None) is True
        assert _is_empty_content("") is True
        assert _is_empty_content("   \n\t ") is True
        assert _is_empty_content("text") is False
        assert _is_empty_content([]) is True
        assert _is_empty_content(
            [{"type": "text", "text": "  "}, {"type": "text", "text": "\n"}]
        ) is True
        assert _is_empty_content(
            [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
                {"type": "text", "text": " "},
            ]
        ) is False
        assert _is_empty_content({}) is False
        assert _is_empty_content(123) is False


# ---------------------------------------------------------------------------
# Grep-pin — single prod caller invariant for _is_empty_content
# ---------------------------------------------------------------------------


class TestIsEmptyContentSingleCallerPin:
    """``_is_empty_content`` must keep exactly ONE prod call site.

    The router's nudge gate is the only consumer; the function is a thin
    delegate to the shared predicate. If a second caller appears it must
    import ``is_empty_llm_content`` directly instead — update this pin
    deliberately, never silently.
    """

    def test_exactly_one_prod_caller_in_daemon_graph_py(self):
        source = pathlib.Path(graph_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        call_sites = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "_is_empty_content":
                    call_sites.append(node.lineno)
        assert len(call_sites) == 1, (
            f"_is_empty_content gained prod callers at lines {call_sites}; "
            "the single-caller contract (empty-response-guard doc §3.1) is broken — "
            "call is_empty_llm_content directly instead"
        )

    def test_delegate_matches_shared_predicate(self):
        from daemon.graph import _is_empty_content
        from daemon.response_validation import is_empty_llm_content

        for shape in (None, "", "  ", "text", [], [{"type": "text", "text": " "}],
                      [{"type": "text", "text": "x"}], {}, 123):
            assert _is_empty_content(shape) == is_empty_llm_content(shape)


# ---------------------------------------------------------------------------
# S1 raise riding the retry → failover ladder
# ---------------------------------------------------------------------------


class TestGuardRidesRetryFailoverLadder:
    """The empty raise inherits the existing transient budget machinery."""

    def test_classifier_catches_and_reraises_empty_error(self):
        """EmptyLLMResponseError from the REAL validator is re-raised by
        the classifier's LLMResponseValidationError handler (→ retry)."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="")
        classified = classify_llm_errors(mock_llm)
        with pytest.raises(EmptyLLMResponseError):
            classified.invoke([_real_human()])

    def test_non_empty_response_passes_classifier(self):
        mock_llm = MagicMock()
        expected = AIMessage(content="a real answer")
        mock_llm.invoke.return_value = expected
        classified = classify_llm_errors(mock_llm)
        assert classified.invoke([_real_human()]) is expected

    def _retry_state(self, exception, attempt_number):
        from tenacity import RetryCallState

        outcome = MagicMock()
        outcome.exception.return_value = exception
        state = MagicMock(spec=RetryCallState)
        state.outcome = outcome
        state.attempt_number = attempt_number
        return state

    def test_empty_error_consumes_transient_budget(self):
        predicate = make_llm_retry_strategy(transient_max=3, timeout_max=2)
        error = EmptyLLMResponseError("empty")
        # Budget parity with any other transient (e.g. APIConnectionError,
        # pinned in TestRetryByCategory): attempts 1-2 retry (count<3);
        # attempt 3 exhausts the primary slice (False — no controller,
        # full_budget == transient_max).
        assert predicate(self._retry_state(error, attempt_number=1)) is True
        assert predicate(self._retry_state(error, attempt_number=2)) is True
        assert predicate(self._retry_state(error, attempt_number=3)) is False
        assert predicate(self._retry_state(error, attempt_number=4)) is False

    def test_swap_to_backup_at_primary_transient_cap(self):
        controller = MagicMock()
        predicate = make_llm_retry_strategy(
            transient_max=3, timeout_max=2, failover_controller=controller
        )
        error = EmptyLLMResponseError("empty")
        assert predicate(self._retry_state(error, attempt_number=1)) is True
        assert predicate(self._retry_state(error, attempt_number=2)) is True
        assert predicate(self._retry_state(error, attempt_number=3)) is True
        # 4th consecutive transient: primary slice exhausted → swap.
        assert predicate(self._retry_state(error, attempt_number=4)) is True
        controller.swap_to_backup.assert_called_once()

    def test_continuous_empty_exhausts_loudly_without_backup(self):
        """No backup configured: retries run out → the empty error
        surfaces verbatim (loud terminal ERROR for the agent path)."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="")
        classified = classify_llm_errors(mock_llm)
        predicate = make_llm_retry_strategy(transient_max=3, timeout_max=2)
        retrying = Retrying(
            stop=stop_after_attempt(3), retry=predicate, reraise=True
        )
        with pytest.raises(EmptyLLMResponseError):
            retrying(classified.invoke, [_real_human()])
        # Bounded: exactly the budgeted attempts, each a real LLM call.
        assert mock_llm.invoke.call_count == 3

    def test_continuous_empty_swaps_then_succeeds_on_backup(self):
        """With a backup configured: 3 primary empties → swap → the
        backup's first healthy response ends the cycle."""
        controller = MagicMock()
        healthy = AIMessage(content="recovered on backup")

        responses = [AIMessage(content="")] * 3 + [healthy]

        def _invoke(messages, **kwargs):
            # Mimic classify+validate: an empty response raises the typed
            # error INSIDE the retry scope, a healthy one returns.
            response = responses.pop(0)
            if not response.content:
                raise EmptyLLMResponseError("empty")
            return response

        predicate = make_llm_retry_strategy(
            transient_max=3, timeout_max=2, failover_controller=controller
        )
        retrying = Retrying(
            stop=stop_after_attempt(6), retry=predicate, reraise=True
        )
        result = retrying(_invoke, [_real_human()])
        assert result is healthy
        controller.swap_to_backup.assert_called_once()


# ---------------------------------------------------------------------------
# Manager-scoped streak telemetry (RAM-only, non-gating)
# ---------------------------------------------------------------------------


def _make_stub_manager(**attrs):
    """Build a bare InstanceManager via __new__ with the RAM dicts set.

    Avoids the full constructor (DB engine etc.) — the streak methods
    and the cleanup sites only touch plain dicts/sets.
    """
    from daemon.manager import InstanceManager

    manager = InstanceManager.__new__(InstanceManager)
    manager._empty_response_streaks = {}
    manager._graph_tasks = {}
    manager._pending_injections = {}
    manager._gii_throttle = {}
    manager._loop_breaker_state = {}
    manager._last_context_usage = {}
    manager._question_pause_requested = {}
    manager._deferred_question_pause = set()
    manager._deferred_watchover_terminate = set()
    manager._question_manager = MagicMock()
    for key, value in attrs.items():
        setattr(manager, key, value)
    return manager


class TestEmptyResponseStreakTelemetry:
    def test_note_increments_and_returns_streak(self, caplog):
        manager = _make_stub_manager()
        assert manager.get_empty_response_streak("inst-1") == 0
        assert manager.note_empty_response("inst-1", "http://p") == 1
        assert manager.note_empty_response("inst-1", "http://p") == 2
        assert manager.get_empty_response_streak("inst-1") == 2
        assert "[LLM-EMPTY]" in caplog.text
        assert "provider=http://p" in caplog.text
        assert "streak=2" in caplog.text

    def test_reset_clears_the_streak(self):
        manager = _make_stub_manager()
        manager.note_empty_response("inst-1")
        manager.reset_empty_response_streak("inst-1")
        assert manager.get_empty_response_streak("inst-1") == 0

    def test_streaks_are_per_instance(self):
        manager = _make_stub_manager()
        manager.note_empty_response("inst-1")
        manager.note_empty_response("inst-2")
        manager.note_empty_response("inst-2")
        assert manager.get_empty_response_streak("inst-1") == 1
        assert manager.get_empty_response_streak("inst-2") == 2

    def test_cleanup_instance_state_drops_the_entry(self):
        manager = _make_stub_manager()
        manager.note_empty_response("inst-1")
        manager._cleanup_instance_state("inst-1")
        assert manager.get_empty_response_streak("inst-1") == 0

    def test_ttl_sweep_drops_the_entry(self):
        manager = _make_stub_manager()
        manager.note_empty_response("stale-inst")
        # One stale injection queue so the sweep has work; the sweep pops
        # every RAM dict keyed by the same instance id. Staleness keys on
        # the head entry's ``timestamp`` field (ttl <= 0 short-circuits,
        # so use a real ancient timestamp with a normal ttl).
        manager._pending_injections["stale-inst"] = [
            {"content": "x", "timestamp": "2000-01-01T00:00:00+00:00"}
        ]
        dropped = manager._cleanup_stale_injections(ttl_seconds=60)
        assert dropped >= 1
        assert manager.get_empty_response_streak("stale-inst") == 0

    def test_dead_task_branch_drops_the_entry(self):
        manager = _make_stub_manager()
        manager.note_empty_response("inst-1")
        # A DONE (dead) graph task routes cancel_graph_task into the
        # dead-task cleanup branch.
        dead_task = MagicMock()
        dead_task.done.return_value = True
        manager._graph_tasks["inst-1"] = dead_task
        manager.cancel_graph_task("inst-1")
        assert "inst-1" not in manager._graph_tasks
        assert manager.get_empty_response_streak("inst-1") == 0
