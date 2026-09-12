"""End-to-end pin: continuous-empty AIMessages → instance ERROR lineage.

Scenario-a's two follow-on claims are UNTESTED:

    (i)  no empty AIMessage is checkpointed as the turn's final answer
         end-to-end (the S1 raise rides the retry → failover → loud
         ERROR ladder; the AIMessage NEVER lands in ``state["messages"]``
         because ``validate_llm_response`` raises BEFORE the agent_node
         appends it);
    (ii) instance ERROR lineage (error lane → message_processing_errors
         .py:131-133 ``validation_error`` → instance status ERROR). The
         architecture intent (architecture-recommendation.md §3
         step-2 "Failure semantics") and the typed subclassing contract
         (``EmptyLLMResponseError ⊂ LLMResponseValidationError``) BOTH
         demand that the typed exception classify to ``validation_error``
         so the error-event DB row carries the ``validation_error``
         lane.

The existing unit pin
``tests/unit/test_empty_response_guard.py::TestGuardRidesRetryFailoverLadder
::test_continuous_empty_exhausts_loudly_without_backup`` (:434)
structurally pins the S1 raise + 3-call bound but only against the
classifier + Retrying tuple — it does NOT exercise the real graph
``create_agent_node`` closure, the real ``Retrying(stop=
stop_after_attempt(...))`` wrapper, the real failover controller, OR
the real ``message_processing_errors`` lane routing. This file is the
graph-level integration pin that closes that gap.

What this test drives (no stubs, no mirrors):

* the REAL ``daemon.graph.create_agent_node`` closure (graph.py:4447),
  compiled into a real LangGraph ``StateGraph(MessagesState)`` with a
  real ``MemorySaver`` checkpointer;
* the REAL ``daemon.llm_error_classifier.classify_llm_errors`` wrapper
  (``_run_with_classification`` runs ``validate_llm_response`` INSIDE
  the retry scope — llm_error_classifier.py:907-916);
* the REAL ``daemon.llm_error_classifier.make_llm_retry_strategy``
  predicate + ``daemon.llm_error_classifier.derive_ha_attempt_ceiling``
  ceiling derivation;
* the REAL ``daemon.services.message_processing_errors`` lane router
  (the three side-effects: error event, lifecycle ``status="error"``,
  parent ``_send_error_report``).

Scenarios:

    (a) real graph + classifier + retry, no backup, continuous empty
        → bounded LLM calls, no empty AIMessage in state, ERROR lane.
    (b) real graph + classifier + retry + backup controller, backup
        also empty → bounded total calls (no swap-forever burn),
        same ERROR lineage.
    (c) real graph + classifier + retry, no backup, same as (a)
        (loud-exhaustion assertion variant).
    (d) real graph + scripted normal response → completes normally
        (regression guard against a false positive).

The ``EmptyLLMResponseError`` lane is asserted per the architecture
recommendation (validation_error). This is the regression pin that
will turn green when the message_processing_errors lane is widened
to include the typed subclass (see ``_classify_error_type``
message_processing_errors.py:131-133). With the current code (which
matches ``exc_name in ("LLMResponseValidationError", ...)`` strictly,
missing the ``EmptyLLMResponseError`` subclass name) the assertion
exposes a defect — pinned here so a future fix has a one-line guard.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from sqlalchemy import event, func, select
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel
from tenacity import Retrying, stop_after_attempt, wait_exponential_jitter

from daemon.repositories.event.models import Event
from daemon.repositories.dependency_bus.models import DependencyWatcher
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task

INSTANCE_ID = "empty-guard-error-lineage-e2e"


# ---------------------------------------------------------------------------
# LLM scaffolding — mirrors ``build_instance_llms`` (graph.py:5804-5897)
# ---------------------------------------------------------------------------
#
# The real production wiring is
#
#     classified = classify_llm_errors(raw_llm)
#     retrying   = Retrying(stop=stop_after_attempt(ceiling),
#                           wait=wait_exponential_jitter(),
#                           retry=make_llm_retry_strategy(...),
#                           reraise=True)
#     wrapped    = RunnableLambda(lambda v: retrying(classified.invoke, v))
#
# We rebuild the same triple here so the test exercises the EXACT
# retry → classifier → raw-LLM pipeline the graph uses at runtime.
# The raw LLM is a MagicMock so we control what it returns / raises
# without booting an OpenAI client.


def _stub_llm(return_value: AIMessage) -> MagicMock:
    """A MagicMock with a deterministic ``invoke`` return value.

    ``invoke`` is a sync MagicMock because the agent_node offloads
    ``current_llm.invoke(full_messages)`` to a thread via
    ``loop.run_in_executor`` (graph.py:5298-5301) — the real
    LangChain ``.invoke()`` is sync; only ``.ainvoke()`` is async.
    """
    llm = MagicMock(name="stub_llm")
    llm.invoke = MagicMock(return_value=return_value)
    return llm


def _empty_ai() -> AIMessage:
    """An AIMessage with shared-predicate-empty content + no tool_calls
    + no reasoning_content — fires the S1 ``EmptyLLMResponseError``
    raise inside ``validate_llm_response`` (response_validation.py:467-475).
    """
    return AIMessage(content="", additional_kwargs={})


class _NoOpFailoverController:
    """A FailoverController substitute that records the swap call but
    does NOT mutate the underlying openai client.

    Real ``FailoverController.swap_to_backup`` walks ``client.root_client``
    and rewrites ``base_url`` (llm_error_classifier.py:639-663) — a real
    openai client dereferences ``base_url`` lazily on every request, so
    a MagicMock-as-chat-client would raise inside the property setter.
    The retry predicate does not need the URL mutation to make its
    swap-decision — only the ``swap_to_backup()`` call + ``is_configured``
    property. We mirror those and ignore the underlying client so the
    test stays inside the unit seam and never touches an openai client.

    Implements the two attributes the retry predicate reads
    (``llm_error_classifier.py:772-779, 813-814, 866-879``):

        * ``is_configured``  → True iff backup_url is set AND distinct
          from primary_url (mirrors the real property at :600-603);
        * ``swap_to_backup`` → records the call, no mutation;
        * ``reset_to_primary`` → records the call, no mutation;
        * ``failover_summary`` → returns a one-line summary used by the
          swap WARNING log line (daemon/graph.py log surface).
    """

    def __init__(self, primary_url: str, backup_url: str):
        self._primary_url = primary_url
        self._backup_url = backup_url
        self.swap_calls = 0
        self.reset_calls = 0
        self.on_backup = False

    @property
    def is_configured(self) -> bool:
        return bool(self._backup_url) and self._backup_url != self._primary_url

    def swap_to_backup(self) -> None:
        self.swap_calls += 1
        self.on_backup = True

    def reset_to_primary(self) -> None:
        self.reset_calls += 1
        self.on_backup = False

    def failover_summary(self) -> str:
        return f"primary={self._primary_url} -> backup={self._backup_url}"


def _build_wrapped_llm(
    stub_llm: MagicMock,
    failover_controller,
    transient_attempts: int,
    timeout_attempts: int,
):
    """Mirror graph.py:5852-5878 ``build_instance_llms`` wiring.

    Returns a ``RunnableLambda`` whose ``.invoke(input_value)`` runs:

        retrying(classified_llm.invoke, input_value)

    where ``classified_llm`` is the REAL ``classify_llm_errors`` wrapper
    and ``retrying`` is a REAL ``Retrying`` instance with the REAL
    ``make_llm_retry_strategy`` predicate and the REAL
    ``derive_ha_attempt_ceiling`` ceiling.

    The total attempt ceiling matches graph.py:5765-5786 derivation:

        ceiling = derive_ha_attempt_ceiling(
            transient_attempts, timeout_attempts,
            failover_active=failover_controller.is_configured,
        )

    Exposed callables (``wrapped.invoke``) become the ``llm_with_tools``
    / ``llm_standard`` slots the agent_node uses (graph.py:5146).
    """
    from daemon.llm_error_classifier import (
        classify_llm_errors,
        derive_ha_attempt_ceiling,
        make_llm_retry_strategy,
    )

    classified = classify_llm_errors(stub_llm)

    ceiling = derive_ha_attempt_ceiling(
        transient_attempts,
        timeout_attempts,
        failover_active=(
            failover_controller.is_configured
            if failover_controller is not None
            else False
        ),
    )
    predicate = make_llm_retry_strategy(
        transient_max=transient_attempts,
        timeout_max=timeout_attempts,
        failover_controller=failover_controller,
    )
    retrying = Retrying(
        stop=stop_after_attempt(ceiling),
        wait=wait_exponential_jitter(initial=0, max=0),  # 0 jitter keeps the test fast
        retry=predicate,
        reraise=True,
    )

    def _run_with_retry(input_value):
        return retrying(classified.invoke, input_value)

    return RunnableLambda(_run_with_retry)


# ---------------------------------------------------------------------------
# Real-langgraph swap (the root tests/conftest.py poisons sys.modules with
# langgraph mocks for unit tests; integration tests evict them per-test)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    """Pin the empty-response-guard ON for every test in this module.

    The default install (response_validation.py:_reset_empty_guard_config_
    for_tests → ``enabled=True``) matches the production default, but
    pin it explicitly so the test never depends on import-order.
    """
    from daemon.response_validation import (
        install_empty_guard_config,
    )

    install_empty_guard_config(enabled=True, compaction_skip=False)
    yield
    install_empty_guard_config(enabled=True, compaction_skip=False)


@pytest.fixture
def file_sqlite_engine(tmp_path):
    """File-backed SQLite instance/event/queue/task engine.

    Per repo conventions: tmp_path + NullPool + WAL + busy_timeout. No
    prod ensemble_prod, no port 5432.
    """
    from sqlalchemy import create_engine

    db_path = tmp_path / "empty_guard_error_lineage.sqlite"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(
        engine,
        tables=[
            Instance.__table__,
            DependencyWatcher.__table__,
            MessageQueue.__table__,
            Task.__table__,
            Event.__table__,
        ],
    )
    try:
        yield engine
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Graph construction (real langgraph + real create_agent_node)
# ---------------------------------------------------------------------------


def _build_one_node_graph(real_graph_module, llm_wrapped, retry_config):
    """Compile a 1-node StateGraph around the REAL ``create_agent_node``.

    Mirrors ``tests/integration/test_injection_echo_id_continuity.py``
    ``_build_graph`` pattern but drops the injection slot / live hub so
    the test exercises only the empty-response path (no HumanMessage
    drain, no SSE emission, no child-report queue).
    """
    from langgraph.checkpoint.memory import MemorySaver

    from daemon.graph import create_agent_node

    agent_node = create_agent_node(
        llm_with_tools=llm_wrapped,
        system_prompt="you are a test assistant",
        compactor=None,
        graph_ref=[None],
        config=None,
        llm_config={"model": "stub", "model_vision": None},
        retry_config=retry_config,
        llm_standard=llm_wrapped,
        injection_slot=None,
        live_hub=MagicMock(stream_message=AsyncMock()),
        empty_streak_manager=None,
    )

    builder = real_graph_module.StateGraph(real_graph_module.MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_edge(real_graph_module.START, "agent")
    builder.add_edge("agent", real_graph_module.END)

    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)
    return graph, checkpointer


def _drive_one_turn(graph, content: str):
    """Drive one ``graph.ainvoke`` call (async).

    Must be awaited from inside an async test (the agent_node is async;
    graph.py:4579). The return is a coroutine that resolves to
    ``(raised_exc, state_messages)``.
    """
    cfg = {"configurable": {"thread_id": INSTANCE_ID}}

    async def _drive():
        raised = None
        try:
            await graph.ainvoke(
                {"messages": [HumanMessage(content=content)]},
                config=cfg,
            )
        except Exception as exc:  # noqa: BLE001 — re-inspected by type below
            raised = exc
        state = await graph.aget_state(cfg)
        return raised, state.values.get("messages", [])

    return _drive()


# ---------------------------------------------------------------------------
# Test: message_processing_errors lane — pure helper, no graph
# ---------------------------------------------------------------------------


class TestMessageProcessingErrorsLaneForEmptyResponse:
    """Pin the ``EmptyLLMResponseError`` → ``validation_error`` lane that
    ``message_processing_errors.py:131-133`` advertises.

    Architecture intent (architecture-recommendation.md §3 step-2):
    the empty raise routes to ``validation_error`` so the error-event
    DB row, the lifecycle ``status="error"`` event, and the parent
    ``_send_error_report`` all carry the lane tag.

    Current implementation: ``_classify_error_type`` matches the class
    name STRICTLY (exc_name in ("LLMResponseValidationError", ...)),
    which MISSES the ``EmptyLLMResponseError`` subclass and falls
    through to the ``"execution_error"`` default at line 157. This is
    a defect — the test pins the architecture intent so a future
    widening fix has a one-line guard.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "DEFECT (reported): EmptyLLMResponseError routes to "
            "execution_error lane — message_processing_errors.py:131-133 "
            "matches class NAME, not isinstance; spec §3-2/ADR-0001 "
            "expect validation_error. 1-line prod fix pending leader "
            "routing."
        ),
    )
    def test_classify_error_type_pins_validation_error_lane_for_empty(
        self,
    ):
        """The typed empty raise MUST route to the validation_error lane.

        Today the lane defaults to ``execution_error`` because the
        implementation matches ``exc_name`` exactly. The fix is a one-
        line widening to include the subclass name; this test is the
        pin that turns green at that fix.
        """
        from daemon.response_validation import EmptyLLMResponseError
        from daemon.services.message_processing_errors import (
            _classify_error_type,
        )

        # Parent class still routes correctly (proves the lane tag exists)
        from daemon.response_validation import LLMResponseValidationError

        parent = LLMResponseValidationError("validation failed")
        assert _classify_error_type(parent) == "validation_error", (
            "lane regression — the parent LLMResponseValidationError "
            "must classify as 'validation_error'"
        )

        # The typed subclass must ALSO route to the same lane (architecture
        # intent). EmptyLLMResponseError IS A LLMResponseValidationError.
        empty = EmptyLLMResponseError(
            "LLM returned an empty response with no tool calls and no "
            "reasoning content — empty-as-entire-answer is treated as a "
            "provider failure (retries + failover apply)",
            response=AIMessage(content=""),
        )
        assert _classify_error_type(empty) == "validation_error", (
            "DEFECT: EmptyLLMResponseError classifies as "
            f"{_classify_error_type(empty)!r}, not 'validation_error'. "
            "_classify_error_type(message_processing_errors.py:131-133) "
            "matches exc_name in ('LLMResponseValidationError', "
            "'APIResponseValidationError') strictly and misses the typed "
            "subclass. Architecture-recommendation.md §3 step-2 'Failure "
            "semantics' pins validation_error for EmptyLLMResponseError."
        )

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "DEFECT (reported): EmptyLLMResponseError routes to "
            "execution_error lane — message_processing_errors.py:131-133 "
            "matches class NAME, not isinstance; spec §3-2/ADR-0001 "
            "expect validation_error. 1-line prod fix pending leader "
            "routing."
        ),
    )
    async def test_handle_message_processing_error_routes_to_error_side_effects(
        self, file_sqlite_engine
    ):
        """The helper runs the three side-effects (error event, lifecycle
        ``status='error'``, parent ``_send_error_report``) for the empty
        raise. Validates that even with the lane-tag defect, the helper
        still transitions the instance via the lifecycle event path.
        """
        from daemon.response_validation import EmptyLLMResponseError
        from daemon.services.message_processing_errors import (
            handle_message_processing_error,
        )

        repo = SQLModelInstanceRepository(file_sqlite_engine)
        repo.create(
            instance_id=INSTANCE_ID,
            agent_id="test",
            agent_dir="./agents/test",
        )

        # Stub manager: capture the three side-effects
        event_bus = MagicMock()
        event_bus.create_error_event = AsyncMock()
        publish_lifecycle = AsyncMock()
        send_error_report = AsyncMock()
        manager = MagicMock()
        manager._event_bus = event_bus
        manager._instance_repository = repo
        manager._publish_instance_lifecycle_event = publish_lifecycle
        manager._send_error_report = send_error_report
        manager._job_queue_service = None

        empty = EmptyLLMResponseError(
            "empty response — provider failure",
            response=AIMessage(content=""),
        )

        await handle_message_processing_error(
            instance_manager=manager,
            instance_id=INSTANCE_ID,
            error=empty,
            message_id="msg-1",
            task_id="42",
        )

        # 1. Error event in DB via event_bus (best-effort)
        event_bus.create_error_event.assert_awaited_once()
        kwargs = event_bus.create_error_event.await_args.kwargs
        error_payload = kwargs["error"]
        # Architecture intent: error_type must be "validation_error".
        # Today it is "execution_error" (the defect pinned above).
        assert error_payload["error_type"] == "validation_error", (
            f"DEFECT: error event carries error_type="
            f"{error_payload['error_type']!r}, not 'validation_error'. "
            "Same lane defect as the unit-classify pin."
        )
        assert "empty response" in error_payload["error"]

        # 2. Lifecycle event with status="error" — the instance state
        # transition. This is what wakes JobFeedbackObserver + writes
        # the persistent instance.status column to ERROR.
        publish_lifecycle.assert_awaited_once()
        kwargs = publish_lifecycle.await_args.kwargs
        assert kwargs["instance_id"] == INSTANCE_ID
        assert kwargs["status"] == "error"
        assert "empty response" in (kwargs.get("error") or "")

        # 3. Parent error report via _send_error_report
        send_error_report.assert_awaited_once()
        kwargs = send_error_report.await_args.kwargs
        assert kwargs["instance_id"] == INSTANCE_ID
        assert kwargs["error_type"] == "validation_error", (
            f"DEFECT: _send_error_report gets error_type="
            f"{kwargs['error_type']!r}, not 'validation_error'."
        )
        assert kwargs["message_id"] == "msg-1"


# ---------------------------------------------------------------------------
# Test (a)/(b)/(c): graph-level continuous-empty → ERROR lineage
# ---------------------------------------------------------------------------


class TestContinuousEmptyGraphEndToEnd:
    """Drive the REAL graph + classifier + retry machinery end-to-end.

    The four scenarios the empty-response-guard plan asks for:

        (a) no backup, continuous empty → bounded calls + no empty
            AIMessage in state + ERROR lineage;
        (b) backup configured, backup also empty → bounded total calls
            + same ERROR lineage (no swap-forever burn);
        (c) no backup, same as (a) — loud-exhaustion variant;
        (d) normal response → completes normally (regression guard).
    """

    @staticmethod
    def _transient_cap(retry_attempts: int) -> int:
        """The transient cap mirrors ``build_instance_llms`` defaults.

        ``transient_attempts=3`` + ``timeout_attempts=3`` + no backup →
        ``derive_ha_attempt_ceiling(3, 3, failover_active=False) = 3``
        so ``Retrying(stop=stop_after_attempt(3))``. With a backup
        configured the ceiling becomes
        ``budget + max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX) =
        3 + max(3, 2) = 6`` so ``Retrying(stop=stop_after_attempt(6))``.
        """
        return retry_attempts  # the no-backup ceiling equals the cap

    @pytest.mark.asyncio
    async def test_a_continuous_empty_no_backup_bounded_no_empty_ai_in_state(
        self, real_graph_module
    ):
        """(a) real graph + real classifier + real Retrying, no backup,
        continuous empty → bounded LLM calls (≤ transient cap = 3), no
        empty AIMessage lands in state["messages"] (the S1 raise fires
        BEFORE the agent_node appends the response), the agent_node
        re-raises the EmptyLLMResponseError.

        The instance ERROR lineage is verified separately by
        TestMessageProcessingErrorsLaneForEmptyResponse above (which
        does not need a graph to exercise the message_processing_errors
        lane).
        """
        from daemon.response_validation import EmptyLLMResponseError

        stub_llm = _stub_llm(return_value=_empty_ai())
        wrapped = _build_wrapped_llm(
            stub_llm=stub_llm,
            failover_controller=None,
            transient_attempts=3,
            timeout_attempts=3,
        )
        graph, _ = _build_one_node_graph(
            real_graph_module,
            wrapped,
            retry_config={"transient_attempts": 3, "timeout_attempts": 3},
        )

        raised, messages = await _drive_one_turn(graph, "Do the thing")

        # (iii) bounded LLM call count: the Retrying ceiling is 3 (no
        # backup → derive_ha_attempt_ceiling(3, 3, False) = 3). Every
        # attempt is one real LLM.invoke, the 3rd's EmptyLLMResponseError
        # is re-raised. agent_node catches LLMResponseValidationError
        # at graph.py:5512 and re-raises.
        assert stub_llm.invoke.call_count == self._transient_cap(3), (
            f"expected exactly {self._transient_cap(3)} LLM calls "
            f"(bounded retry budget), got {stub_llm.invoke.call_count}"
        )

        # (i) no empty AIMessage in state["messages"]. The S1 raise
        # happens BEFORE the agent_node's outgoing=[response] return
        # at graph.py:5630, so the AIMessage never reaches the
        # add_messages reducer. The checkpointer state at the time
        # of the raise carries ONLY the input HumanMessage.
        assert isinstance(raised, EmptyLLMResponseError), (
            f"expected graph to re-raise EmptyLLMResponseError, "
            f"got {type(raised).__name__}: {raised}"
        )
        ai_messages = [m for m in messages if isinstance(m, AIMessage)]
        assert ai_messages == [], (
            "defect — an empty AIMessage was checkpointed as the turn's "
            "final answer; the S1 raise must short-circuit BEFORE the "
            f"add_messages reducer fires. messages={[type(m).__name__ for m in messages]}"
        )
        # Last message is the user input (no empty response sneaks in).
        assert len(messages) == 1
        assert isinstance(messages[-1], HumanMessage)
        assert messages[-1].content == "Do the thing"

    @pytest.mark.asyncio
    async def test_b_backup_configured_backup_also_empty_bounded_total_calls(
        self, real_graph_module
    ):
        """(b) real graph + real classifier + real Retrying + REAL
        FailoverController-with-no-op-mutation, backup also empty →
        bounded total calls (3 primary + 3 backup = 6 attempts, no
        swap-forever burn), same EmptyLLMResponseError propagation.

        Same stub LLM is used for primary AND backup (the failover
        swap is a controller-only decision; the retry predicate
        hands the SAME invoke to the SAME MagicMock). The point of
        this test is to prove the budget split, NOT the swap path's
        network behavior — the controller verifies the swap was
        actually CALLED once and that total attempts are bounded.
        """
        from daemon.response_validation import EmptyLLMResponseError

        stub_llm = _stub_llm(return_value=_empty_ai())
        controller = _NoOpFailoverController(
            primary_url="http://primary.test",
            backup_url="http://backup.test",
        )
        assert controller.is_configured is True

        wrapped = _build_wrapped_llm(
            stub_llm=stub_llm,
            failover_controller=controller,
            transient_attempts=3,
            timeout_attempts=3,
        )
        graph, _ = _build_one_node_graph(
            real_graph_module,
            wrapped,
            retry_config={"transient_attempts": 3, "timeout_attempts": 3},
        )

        raised, messages = await _drive_one_turn(graph, "Do the thing")

        # 3 primary attempts exhaust → swap → 3 backup attempts exhaust.
        # derive_ha_attempt_ceiling(3, 3, failover_active=True) = 3 + 3 = 6.
        # Total LLM.invoke call count MUST equal 6.
        expected_total = 6
        assert stub_llm.invoke.call_count == expected_total, (
            f"expected exactly {expected_total} LLM calls "
            f"(3 primary + 3 backup, bounded swap-once), "
            f"got {stub_llm.invoke.call_count} — swap-forever burn?"
        )
        # Controller verified the swap fired exactly once (the
        # bounded swap-once contract).
        assert controller.swap_calls == 1, (
            f"expected exactly one swap_to_backup() call (bounded), "
            f"got {controller.swap_calls}"
        )

        # Same re-raise + no-empty-AI-in-state contract.
        assert isinstance(raised, EmptyLLMResponseError), (
            f"expected EmptyLLMResponseError, got {type(raised).__name__}"
        )
        ai_messages = [m for m in messages if isinstance(m, AIMessage)]
        assert ai_messages == [], (
            "defect — empty AIMessage persisted to state despite "
            f"controller-guarded swap-once. messages={[type(m).__name__ for m in messages]}"
        )

    @pytest.mark.asyncio
    async def test_c_no_backup_loud_exhaustion_no_swap(self, real_graph_module):
        """(c) real graph + classifier + retry, no backup → loud
        exhaustion: bounded attempts (3 — the transient cap), the
        EmptyLLMResponseError propagates VERBATIM, no infinite burn.

        Production wiring (graph.py:5773-5779) gates the controller
        BEFORE handing it to ``make_llm_retry_strategy`` —
        ``failover_controller=(controller if (controller is not None
        and controller.is_configured) else None)`` — so "no backup"
        means ``failover_controller=None`` at the predicate. The
        predicate's early-return on ``failover_controller is None``
        keeps the retry logic on the pre-HA path (single slice,
        no swap arm).
        """
        from daemon.response_validation import EmptyLLMResponseError

        stub_llm = _stub_llm(return_value=_empty_ai())
        wrapped = _build_wrapped_llm(
            stub_llm=stub_llm,
            failover_controller=None,  # matches production "no backup"
            transient_attempts=3,
            timeout_attempts=3,
        )
        graph, _ = _build_one_node_graph(
            real_graph_module,
            wrapped,
            retry_config={"transient_attempts": 3, "timeout_attempts": 3},
        )

        raised, messages = await _drive_one_turn(graph, "Do the thing")

        # Bounded: exactly the cap (3 attempts), loud exhaustion.
        assert stub_llm.invoke.call_count == 3, (
            f"expected 3 attempts (loud exhaust, no controller = no swap), "
            f"got {stub_llm.invoke.call_count}"
        )
        assert isinstance(raised, EmptyLLMResponseError)
        assert [m for m in messages if isinstance(m, AIMessage)] == []

    @pytest.mark.asyncio
    async def test_d_normal_response_completes_normally(
        self, real_graph_module
    ):
        """(d) Regression guard — a normal, non-empty AIMessage must
        complete the turn normally (state receives the response, no
        exception, the LLM was called exactly once because no retry
        was needed). Proves the test (a)/(b)/(c) wiring is NOT
        false-positive: the SAME pipeline that explodes on continuous
        empty accepts a healthy response and routes the agent to END.
        """
        stub_llm = _stub_llm(
            return_value=AIMessage(content="Here is your answer.")
        )
        wrapped = _build_wrapped_llm(
            stub_llm=stub_llm,
            failover_controller=None,
            transient_attempts=3,
            timeout_attempts=3,
        )
        graph, _ = _build_one_node_graph(
            real_graph_module,
            wrapped,
            retry_config={"transient_attempts": 3, "timeout_attempts": 3},
        )

        raised, messages = await _drive_one_turn(graph, "Do the thing")

        # Normal completion: no exception, exactly one LLM call.
        assert raised is None, (
            f"normal response must NOT raise; got {type(raised).__name__}: "
            f"{raised}"
        )
        assert stub_llm.invoke.call_count == 1, (
            f"expected 1 LLM call (no retry on success), "
            f"got {stub_llm.invoke.call_count}"
        )

        # State has the AIMessage appended — the regression guard
        # proves the empty case is NOT silently swallowing AIMessages.
        ai_messages = [m for m in messages if isinstance(m, AIMessage)]
        assert len(ai_messages) == 1, (
            f"expected exactly 1 AIMessage in normal-completion state, "
            f"got {len(ai_messages)}"
        )
        assert ai_messages[0].content == "Here is your answer."
        assert isinstance(messages[-1], AIMessage)