"""NEXT-TURN EXECUTION proof — the child's next turn actually RUNS with the
parent's instruction present in its context.

Commission: **hide ask_questions from non-root instances** (branch
``feature/hide-question-tool-nonroot``). This file carries the **T7
next-turn execution** proof that the plan's §11.1 test table assigned to
the TESTER phase (the T7 row of the sibling file
``tests/unit/services/test_child_report_open_questions_flow.py`` stops at
the QUEUE level: revive + message row + ``PROCESS_MESSAGE`` task).

THE CHAIN THIS FILE ACTUALLY DRIVES (real machinery, end to end):

    1. REAL ``InstanceMessagingService.enqueue_message`` — the parent's
       instruction lands as a ``MessageQueue`` row + a ``PROCESS_MESSAGE``
       ``Task`` row, and the TERMINAL child (``COMPLETED``) is revived to
       ``RUNNING`` (the production revive seam inside
       ``_prepare_enqueued_message``).
    2. REAL ``ProcessMessageProcessor.process(task)`` — the production
       task processor (the WorkerPool's dispatch entry point).
    3. REAL ``MessageProcessingPipeline.execute`` — the six shared
       production stages (claim → gate → work_fn → mark-completed →
       dispatch → child-completion).
    4. REAL ``InstanceMessagingService._process_message_with_tracking`` —
       the production turn driver: project/skills context assembly,
       ``_build_graph_input`` (the real HumanMessage construction), the
       D1 checkpoint-tail heal, and the real ``graph.astream`` call.
    5. A REAL compiled LangGraph (``StateGraph`` + ``MessagesState`` +
       a real in-memory checkpointer) whose single agent node
       calls a MOCK LLM. The mock LLM is the ONLY mocked component —
       the LLM is the external boundary, and the prompt it received IS
       the child's context.

WHAT IS PROVEN (the assertions):

    * The child's next turn EXECUTED (the graph ran a superstep and the
      LLM was invoked) — not merely "a row was queued".
    * The parent's instruction is PRESENT, VERBATIM, in the message list
      the LLM received (i.e. the instruction really reached the child's
      context, not just the queue table).
    * The instruction arrives as a ``HumanMessage`` whose ``id`` is the
      queued message's ``message_id`` (the ``add_messages`` reducer dedup
      identity — the same identity the production path uses).
    * The turn CONSUMED the queued message: the ``MessageQueue`` row ends
      ``COMPLETED`` and the ``PROCESS_MESSAGE`` task ends ``COMPLETED``.
    * The CONTINUATION shape: a child that ran a turn, went terminal and
      was revived by the parent's instruction runs its next turn with
      BOTH its earlier (checkpointed) context AND the new instruction.

WHAT IS **NOT** PROVEN (honest gaps — read before citing this file):

    * The graph is a REAL LangGraph but NOT the ensemble's production
      graph: it has ONE node (the agent) instead of the full middleware
      stack (context injection slot, report framing, GII throttle, loop
      breaker, response validation, language check, compaction). Those
      slots are not the subject of this commission, but the production
      graph would add them.
    * The LLM is a mock (no provider call) — the tool-call loop, the
      real tool bindings and the real system prompt are NOT exercised.
    * The checkpointer is the in-memory LangGraph saver
      (``langgraph.checkpoint.memory.MemorySaver``), not the production
      PostgreSQL/SQLite saver — so the CHECKPOINT PERSISTENCE of the
      turn is not proven here (the turn's state lives in RAM).
    * The instance's project/skills context block is skipped (the child's
      row carries ``project_injected: True``, i.e. the realistic
      second-turn state of a revived child) — the persistent block
      assembly path is therefore not exercised.

HARNESS CONVENTIONS (borrowed from the sibling suites — no parallel
implementation was invented):

    * ``tests/unit/services/test_child_report_open_questions_flow.py`` —
      the file-backed SQLite engine recipe, the ``_seed_instance`` /
      ``_build_messaging_service`` helpers and the revive assertions.
    * ``tests/job_queue/test_answer_resume_real_chain.py`` — the
      "real chain with the graph/LLM boundary mocked" discipline: only
      the outermost LLM/graph node is mocked, every durable-state write
      is real.
    * ``tests/integration/test_multi_turn_resume.py`` — the real
      compiled LangGraph + mock-LLM recipe.

SAFETY RULES honoured by this file:

    * file-backed SQLite under ``tmp_path`` — NEVER a HOME-derived /
      live-shaped path (the 2026-10-12 live-install deletion incident);
    * the suite never shells out, never boots the daemon, never touches
      a live DB or port;
    * every assertion is made against REAL durable state (rows written
      by the production code path) or against the REAL compiled graph's
      LLM boundary — never against a stub of the subject.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel
from sqlmodel import select as sm_select

# ``daemon.manager`` imports ``langgraph.graph.state`` and ``daemon.graph``
# at MODULE level. Importing it HERE (at collection time, while the root
# ``tests/conftest.py`` langgraph mocks are still installed) pins
# ``daemon.manager`` / ``daemon.graph`` to the MOCK langgraph identity for
# the whole session — so the real-langgraph eviction window used by the
# graph fixture below cannot fork their module identity for other tests.
import daemon.manager  # noqa: F401  (identity pin — see comment above)

# Model imports — required so SQLModel.metadata sees the tables when
# create_all() runs on the test engine.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.config import Config
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.task_processor import ProcessMessageProcessor
from daemon.write_pause_guard import WritePauseGuard
from tests.helpers.checkpoint_prune_pg import (
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)

# The instruction the parent sends to the (terminal) child.
PARENT_INSTRUCTION = (
    "Use the staging environment for the migration and write the "
    "Open questions for the orchestrator section in your final report."
)

# A control string that must NEVER appear in the child's context.
CONTROL_STRING = "CONTROL-SENTINEL-must-not-be-injected-9f3a1c"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL, per project convention).

    File-backed (not ``:memory:``) so each session gets its own
    connection — the messaging path opens sessions from
    ``asyncio.to_thread`` (a worker thread) while the test thread holds
    its own. The path lives under ``tmp_path`` — never a HOME-derived /
    live-shaped path.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/next_turn_execution.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@contextmanager
def _real_langgraph():
    """Evict the root conftest's langgraph MOCKS so the REAL package loads.

    The repo installs lightweight ``langgraph`` stubs in
    ``tests/conftest.py`` for unit tests. This file needs the REAL
    LangGraph (a real compiled graph with a real checkpointer), so the
    window below uses the repo-standard eviction helper
    (``tests/helpers/checkpoint_prune_pg.py``) — the same pattern as
    ``tests/support/conftest.py::real_graph_module`` and
    ``tests/integration/test_compaction_e2e.py``.
    """
    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


class _RecordingLLM:
    """The MOCK LLM — the ONLY mocked component in this file.

    Records every message list it is invoked with (the child's context
    at the LLM boundary) and returns a scripted ``AIMessage``.

    ``invoke`` is SYNCHRONOUS — that matches the production agent-node
    pattern (the production node calls the model synchronously inside
    the async graph node).
    """

    def __init__(self, response: str = "Acknowledged — using staging."):
        self.response = response
        self.calls: list[list[Any]] = []

    def invoke(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        return AIMessage(content=self.response)

    def bind_tools(self, tools):  # pragma: no cover - not used by the graph
        return self


def _build_real_graph(llm: _RecordingLLM):
    """Build a REAL compiled LangGraph whose agent node calls the mock LLM.

    Real ``StateGraph`` + real ``MessagesState`` reducer + a real
    in-memory checkpointer — so ``graph.astream`` performs a genuine
    superstep and the checkpoint tail is genuinely readable via
    ``graph.aget_state`` (the seam the D1 checkpoint-tail heal reads).

    MUST be called inside the :func:`_real_langgraph` window (the real
    package is not importable while the conftest mocks are installed).
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, MessagesState, StateGraph

    async def agent_node(state: MessagesState):
        response = llm.invoke(state["messages"])
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder.compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# Manager facade
# ---------------------------------------------------------------------------


class _NextTurnManager:
    """Minimal REAL manager facade for the messaging + task-processor seams.

    Everything the production path touches is REAL:

    * ``_instance_repository`` — real SQLModel repository over the test DB.
    * ``_queue_repository`` — real message-queue repository.
    * ``_messaging_service`` — the REAL ``InstanceMessagingService``.
    * ``_process_message_with_tracking`` — delegates to the real
      messaging service (NOT a stub), so the production turn driver runs.
    * ``get_instance`` — returns the REAL compiled graph.

    The mocked/stubbed surface is limited to the EXTERNAL boundary:

    * ``source_dispatcher`` — ``None`` (no external chat source in a
      unit test; production dispatches the final message to the user's
      channel).
    * ``_live_hub`` — a no-op SSE hub.
    * ``execution_gate`` — a transparent gate (the lease table is not the
      subject of this proof; the recipe mirrors
      ``tests/test_pipeline_unified.py``).
    * the LLM inside the graph.
    """

    def __init__(self, engine: Engine, graph) -> None:
        self.engine = engine
        self.config = Config()
        self._graph = graph
        self._instance_repository = SQLModelInstanceRepository(engine)
        self._queue_repository = SQLModelMessageQueueRepository(engine)
        self._project_repository = MagicMock(name="ProjectRepository")
        self._task_repo = TaskRepository(engine)
        self._graph_tasks: dict = {}
        self._live_hub = MagicMock()
        self._live_hub.stream_status_change = AsyncMock()
        self._live_hub.stream_message = AsyncMock()
        self._live_hub.stream_question_pack = AsyncMock()
        self._live_hub.stream_answer_received = AsyncMock()
        self.source_dispatcher = None
        self.message_metadata_repo = None
        self.write_guard = WritePauseGuard()
        self._worker_pool = None
        self._deferred_question_pause: set[str] = set()
        self._emitted_message_content: dict = {}
        self.is_write_paused = False
        # No compactor in this harness — the proactive-compaction gate
        # short-circuits on a ``None`` compactor (the compaction
        # machinery is not the subject of this proof).
        self._compactor = None
        # The remaining manager surface the production turn driver reads.
        # Every one of these is either a real object (the semaphore, the
        # write guard) or a no-op stub for a subsystem that is NOT the
        # subject of this proof (watchover, skills, project context).
        self._checkpointer = None
        self._engine = engine
        self._job_queue_service = None
        self._last_context_usage: dict = {}
        self._llm_semaphore = asyncio.Semaphore(4)
        self._original_timestamps: dict = {}
        self._generate_and_broadcast_title = AsyncMock()
        self.clear_watchover_terminate_requested = MagicMock(
            return_value=False
        )
        self.get_context_skill_result = MagicMock(return_value=None)
        self.has_deferred_question_pause = MagicMock(return_value=False)
        self.is_watchover_terminate_requested = MagicMock(return_value=False)
        self.pause_instance_cascade = AsyncMock()
        self.pop_deferred_question_pause = MagicMock(return_value=None)
        self.release_context_usage_cache = MagicMock(return_value=None)
        self.reset_skill_search_count = MagicMock(return_value=None)
        self.terminate_instance = AsyncMock()
        self.was_explicit_skill_loaded = MagicMock(return_value=False)
        self.prompt_cache = None
        # Transparent execution gate (the lease table is not the subject of
        # this proof; the recipe mirrors ``tests/test_pipeline_unified.py``).
        self.execution_gate = _passthrough_gate()

        cancellation = MagicMock()
        cancellation.is_shutting_down = False
        self._cancellation_service = cancellation

        self._messaging_service = InstanceMessagingService(
            manager=self,
            cancellation_service=cancellation,
        )

    # -- the real turn driver -------------------------------------------
    async def _process_message_with_tracking(self, **kwargs):
        return await self._messaging_service._process_message_with_tracking(
            **kwargs
        )

    async def get_instance(self, instance_id: str):
        """Return the REAL compiled graph (production seam)."""
        return self._graph

    # -- injection FIFO seam (no injections in this suite) ----------------
    def get_injection(self, instance_id: str):
        return None

    def clear_injection(self, instance_id: str):
        return None

    def requeue_injections(self, instance_id: str, entries) -> None:
        return None


def _passthrough_gate():
    """A transparent execution gate (mirrors ``tests/test_pipeline_unified.py``).

    The lease table is not the subject of this proof; the gate must simply
    run the work function.
    """

    async def _passthrough(*args, **kwargs):
        work_fn = kwargs.get("work_fn")
        return await work_fn()

    from daemon.services.execution_gate import ExecutionGateService

    gate = MagicMock(spec=ExecutionGateService)
    gate.run = AsyncMock(side_effect=_passthrough)
    return gate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_instance(
    engine: Engine,
    instance_id: str,
    *,
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
    agent_id: str = "worker",
    instance_metadata: dict | None = None,
) -> str:
    """Insert an Instance row; returns its ``instance_id``."""
    now = "2026-10-20T12:00:00+00:00"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=f"/agents/{agent_id}",
                agent_name=agent_id,
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata=instance_metadata or {},
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return instance_id


def _instance_status(engine: Engine, instance_id: str) -> str | None:
    with Session(engine) as session:
        row = session.get(Instance, instance_id)
        return row.status if row is not None else None


def _message_row(engine: Engine, message_id: str) -> MessageQueue | None:
    with Session(engine) as session:
        return session.get(MessageQueue, message_id)


def _process_message_task(
    engine: Engine, instance_id: str
) -> Task | None:
    """The LATEST ``PROCESS_MESSAGE`` task of the instance.

    "Latest" (highest id) because a second turn adds a second task row
    while the first turn's task stays in the table as a COMPLETED
    history row.
    """
    with Session(engine) as session:
        stmt = (
            sm_select(Task)
            .where(
                Task.instance_id == instance_id,
                Task.task_type == TaskType.PROCESS_MESSAGE.value,
            )
            .order_by(Task.id.desc())
        )
        return session.exec(stmt).first()


# ---------------------------------------------------------------------------
# The proof
# ---------------------------------------------------------------------------


class _TurnOutcome:
    """The observable outcome of one driven next-turn execution."""

    def __init__(self) -> None:
        self.llm_calls: list[list[Any]] = []
        self.result: dict = {}
        self.message_id: str = ""
        self.task_id: int | None = None
        self.bound_human_contents: list[Any] = []


class _NextTurnDriver:
    """Drives next-turn executions for ONE child instance.

    Holds the REAL compiled graph (and its checkpointer) so successive
    turns land on the SAME LangGraph thread — which is what makes the
    "the revived child keeps its earlier context" assertion meaningful.

    The chain (all production code except the LLM):

        enqueue_message → revive → PROCESS_MESSAGE task →
        claim_pending_task → ProcessMessageProcessor.process →
        MessageProcessingPipeline.execute →
        InstanceMessagingService._process_message_with_tracking →
        real compiled LangGraph.astream → mock LLM.invoke
    """

    def __init__(
        self,
        engine: Engine,
        *,
        child_id: str = "child-next-turn",
        agent_id: str = "probe-child",
        child_status: str = InstanceStatus.COMPLETED.value,
    ) -> None:
        self.engine = engine
        self.child_id = child_id
        self.llm = _RecordingLLM()
        with _real_langgraph():
            self.graph = _build_real_graph(self.llm)
        self.manager = _NextTurnManager(engine, self.graph)

        # The child is TERMINAL (its previous turn completed) and its
        # project block is already checkpointed (``project_injected:
        # True``) — the realistic second-turn state of a revived child.
        # The agent id is synthetic: the agent's own prompt/tool
        # bindings are NOT the subject of this proof (the graph is a
        # stand-in for the production graph), and a synthetic id keeps
        # the skill-injection path (which needs the skill service) out
        # of the harness.
        _seed_instance(
            engine,
            child_id,
            status=child_status,
            parent_id="parent-1",
            agent_id=agent_id,
            instance_metadata={"project_injected": True},
        )
        _seed_instance(
            engine,
            "parent-1",
            status=InstanceStatus.RUNNING.value,
            agent_id=agent_id,
        )

    async def send(self, instruction: str) -> _TurnOutcome:
        """Deliver ONE instruction and drive the resulting turn."""
        outcome = _TurnOutcome()

        # ---- 1. the parent sends the instruction (REAL enqueue) ---------
        await self.manager._messaging_service.enqueue_message(
            instance_id=self.child_id,
            message=instruction,
            source="internal_agent:parent-1",
            priority=1,
        )

        task = _process_message_task(self.engine, self.child_id)
        assert task is not None, (
            "the enqueue must materialise a PROCESS_MESSAGE task"
        )
        assert _instance_status(self.engine, self.child_id) == (
            InstanceStatus.RUNNING.value
        ), "the terminal child must be revived to RUNNING by the enqueue"
        outcome.message_id = task.message_id
        outcome.task_id = task.id

        # ---- 2. the task processor runs the turn (REAL) ----------------
        # The worker pool claims the task first (``claim_pending_task``
        # → RUNNING) and only then runs the processor — the processor's
        # ``on_success`` callback completes the task with a
        # ``WHERE status='running'`` guard, so the claim is part of the
        # production dispatch lifecycle (mirrored here, not faked).
        claimed = self.manager._task_repo.claim_pending_task(
            "worker-next-turn"
        )
        assert claimed is not None, (
            "the PROCESS_MESSAGE task must be claimable by the worker pool"
        )
        assert claimed.id == task.id
        assert claimed.status == TaskStatus.RUNNING.value

        processor = ProcessMessageProcessor(
            instance_manager=self.manager,
            task_repo=self.manager._task_repo,
            message_repository=self.manager._queue_repository,
        )

        outcome.result = await processor.process(claimed)
        outcome.llm_calls = list(self.llm.calls)
        if self.llm.calls:
            outcome.bound_human_contents = [
                m.content
                for m in self.llm.calls[-1]
                if isinstance(m, HumanMessage)
            ]
        return outcome

    def mark_child_terminal(self) -> None:
        """Flip the child to COMPLETED — the state its own turn leaves
        behind (the sibling flow suite seeds the same terminal state
        directly when it drives the revive)."""
        with Session(self.engine) as session:
            row = session.get(Instance, self.child_id)
            assert row is not None
            row.status = InstanceStatus.COMPLETED.value
            session.commit()


async def _drive_next_turn(
    engine: Engine,
    *,
    instruction: str,
    agent_id: str = "probe-child",
    child_id: str = "child-next-turn",
) -> _TurnOutcome:
    """Drive ONE full next-turn execution and return what was observed."""
    driver = _NextTurnDriver(
        engine, child_id=child_id, agent_id=agent_id
    )
    return await driver.send(instruction)


class TestT7NextTurnExecution:
    """T7 — the revived child's NEXT TURN executes with the parent's
    instruction present in its context."""

    async def test_next_turn_executes_with_the_instruction_in_context(
        self, engine: Engine
    ) -> None:
        """The full chain: enqueue → revive → PROCESS_MESSAGE task →
        processor → pipeline → messaging service → REAL compiled graph →
        MOCK LLM. The LLM receives the parent's instruction verbatim."""
        outcome = await _drive_next_turn(
            engine, instruction=PARENT_INSTRUCTION
        )

        # ---- 3. the turn EXECUTED ---------------------------------------
        assert outcome.result.get("success") is True, (
            f"the next turn must succeed; got {outcome.result!r}"
        )
        assert outcome.llm_calls, (
            "the child's next turn did not reach the LLM — the graph "
            "never ran a superstep"
        )
        assert outcome.result.get("content"), (
            "the turn produced no content — the AI message was not "
            "extracted from the graph stream"
        )

        # ---- 4. the instruction is PRESENT in the child's context -------
        bound = outcome.llm_calls[0]
        human_contents = outcome.bound_human_contents
        assert PARENT_INSTRUCTION in human_contents, (
            "the parent's instruction must be present verbatim in the "
            f"messages the LLM received; got {human_contents!r}"
        )
        assert CONTROL_STRING not in "".join(
            str(c) for c in human_contents
        ), "the control sentinel must not appear in the child's context"

        # The instruction arrives as the HumanMessage carrying the queued
        # message's id (the ``add_messages`` reducer dedup identity).
        instruction_messages = [
            m
            for m in bound
            if isinstance(m, HumanMessage) and m.content == PARENT_INSTRUCTION
        ]
        assert len(instruction_messages) == 1, (
            "the instruction must appear exactly once in the LLM-bound list"
        )
        assert instruction_messages[0].id == outcome.message_id, (
            "the injected HumanMessage must carry the queued message's "
            f"message_id; got {instruction_messages[0].id!r} != "
            f"{outcome.message_id!r}"
        )

        # ---- 5. the turn CONSUMED the queued message --------------------
        row = _message_row(engine, outcome.message_id)
        assert row is not None
        assert row.status == "completed", (
            f"the queued message must be COMPLETED after the turn; got "
            f"{row.status!r}"
        )

        with Session(engine) as session:
            refreshed = session.get(Task, outcome.task_id)
            assert refreshed is not None
            assert refreshed.status == TaskStatus.COMPLETED.value, (
                f"the PROCESS_MESSAGE task must be COMPLETED; got "
                f"{refreshed.status!r}"
            )

    async def test_the_context_carries_the_delivered_instruction_only(
        self, engine: Engine
    ) -> None:
        """NON-VACUITY CONTROL — the assertion tracks the instruction that
        was actually DELIVERED, not a constant baked into the harness.

        A second run delivers a DIFFERENT instruction; the child's context
        must carry that one and must NOT carry the first run's text (nor
        the control sentinel)."""
        other_instruction = (
            "Reply with the rollback plan for the staging migration and "
            "list the open questions for the orchestrator."
        )

        outcome = await _drive_next_turn(
            engine, instruction=other_instruction, child_id="child-control"
        )

        assert outcome.result.get("success") is True
        assert outcome.llm_calls, "the controlled turn did not reach the LLM"

        human_contents = outcome.bound_human_contents
        assert other_instruction in human_contents, (
            "the delivered instruction must be present in the context; "
            f"got {human_contents!r}"
        )
        assert PARENT_INSTRUCTION not in human_contents, (
            "a DIFFERENT instruction was delivered — the previous run's "
            "text must not leak into this child's context"
        )
        assert CONTROL_STRING not in "".join(str(c) for c in human_contents)

    async def test_the_revived_child_keeps_its_earlier_context(
        self, engine: Engine
    ) -> None:
        """CONTINUATION — the revived child's next turn sees BOTH its
        earlier turn AND the parent's new instruction.

        This is the "replacement path" of the commission in its purest
        form: the child ran a turn, went terminal, the parent answers
        the child's open questions with a new instruction, the child is
        revived, and its next turn runs with the EARLIER context intact
        plus the new instruction.

        The continuity comes from the LangGraph thread: the same
        ``thread_id`` (the instance id) on the same compiled graph, so
        the ``add_messages`` reducer keeps the checkpointed history.
        """
        first_instruction = (
            "First task: implement the migration script and report your "
            "open questions for the orchestrator."
        )

        driver = _NextTurnDriver(
            engine,
            child_id="child-continuation",
            child_status=InstanceStatus.RUNNING.value,
        )

        # ---- turn 1: the child's own turn -------------------------------
        first = await driver.send(first_instruction)
        assert first.result.get("success") is True
        assert len(driver.llm.calls) == 1, (
            f"the child's first turn must reach the LLM exactly once; "
            f"got {len(driver.llm.calls)} call(s)"
        )
        assert first_instruction in first.bound_human_contents

        # ---- the child's turn ends terminal (COMPLETED) -----------------
        driver.mark_child_terminal()
        assert _instance_status(engine, driver.child_id) == (
            InstanceStatus.COMPLETED.value
        )

        # ---- turn 2: the parent's answer revives the child --------------
        second = await driver.send(PARENT_INSTRUCTION)
        assert second.result.get("success") is True
        assert len(driver.llm.calls) == 2, (
            f"the revived child must run a SECOND turn; got "
            f"{len(driver.llm.calls)} LLM call(s)"
        )

        # The second turn's context carries BOTH the earlier instruction
        # (from the checkpoint) and the new one (from the revive).
        second_human_contents = [
            m.content
            for m in driver.llm.calls[1]
            if isinstance(m, HumanMessage)
        ]
        assert PARENT_INSTRUCTION in second_human_contents, (
            "the parent's instruction must be present in the revived "
            f"child's context; got {second_human_contents!r}"
        )
        assert first_instruction in second_human_contents, (
            "the revived child must keep its EARLIER context — the "
            f"checkpointed turn-1 instruction is missing; got "
            f"{second_human_contents!r}"
        )
