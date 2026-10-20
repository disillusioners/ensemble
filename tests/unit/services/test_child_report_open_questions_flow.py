"""Flow-proof tests — "Open questions for the orchestrator" report contract.

Commission: **hide ask_questions from non-root instances** (branch
``feature/hide-question-tool-nonroot``). This file carries the three
FLOW-PROOF tests of the plan's §11.1 test table:

    * **T6** — delivery: a child's completion report whose text contains
      an "Open questions for the orchestrator" section REACHES the parent
      (completion-report row → ``PROCESS_REPORT`` task → ``ReportInjection``
      row → drainable by the parent).
    * **T7** — continuation: the parent instructs the (now terminal) child
      — the send revives it (``status -> RUNNING``) and the instruction is
      delivered into the child's next turn.
    * **T8** — defense in depth: a legacy question pack created BEFORE the
      gate is still answerable by a root that does NOT hold
      ``ask_questions`` — the answer path (shadow resolution + answer CAS)
      never consults the tool registry and never requires the tool to be
      registered.

T6/T7/T8 are the **PRESERVATION** half of the plan's proof sets (GREEN
pre-change). They are NOT gate proof — the gate-proof set (T1/T3/T4b/T5/T9)
lives in ``tests/unit/tools/test_question_tool_root_gate.py`` and is never
reported as part of this file.

HARNESS CONVENTIONS (deliberately borrowed from the existing sibling
suites — no parallel implementation was invented):

    * ``tests/unit/services/test_child_reports.py`` — the child-completion
      report service harness (real ``ChildReportsService`` over a real
      in-memory DB, ``MagicMock`` manager facade, real ``DependencyBus``
      wired because ``child_reports`` treats a missing bus as a HARD ERROR
      at the child-completion seam — A8).
    * ``tests/unit/services/test_a1_revival_carve_out.py`` — the
      ``InstanceMessagingService`` harness (real service, real DB rows,
      ``MagicMock`` manager facade).
    * ``tests/unit/test_question_pause_resume_asymmetry.py`` — the
      answer-path harness (real ``QuestionManager``, real durable shadow,
      real shared answer helper, stubbed graph driver).
    * ``tests/helpers/send_message_fixtures.py`` — the shared agent-tool
      ``send_message`` factory (used for the tool-level routing proof of
      T7).

SAFETY RULES honoured by this file:

    * file-backed SQLite under ``tmp_path`` — NEVER a HOME-derived /
      live-shaped path (the 2026-10-12 live-install deletion incident);
    * the suite never shells out, never touches a live DB or port;
    * every assertion is made against REAL durable state (rows written by
      the production code path), never against a stub of the subject.

HONESTY NOTES (what is NOT proven here):

    * T6 proves the delivery ARTIFACTS (rows) and that the parent can
      drain the open questions out of the injection row alone. It does not
      run a full LangGraph turn for the parent (the graph is not the
      subject of this commission) — the drain is the production
      ``claim_for_injection`` path the parent's live agent-node uses.
    * T7 proves the revive transition + the durable enqueue of the
      instruction (message row + ``PROCESS_MESSAGE`` task) — i.e. the
      carrier of the child's next turn. Executing that turn needs a full
      graph/worker-pool harness and is out of scope here.
    * T8 drives the SHARED answer helper
      (``daemon/routers/answer_helper.py::answer_questions_via_instance``)
      — the single implementation behind BOTH the instance-addressed
      surface and the agent-facing ``job_answer`` tool. The ``job_answer``
      wrapper itself (work-id → asker resolution + job access check) is
      not exercised here; the plan assigns that extension to
      ``tests/job_queue/test_job_answer_tool.py``.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel
from sqlmodel import select as sm_select

# Model imports — required so SQLModel.metadata sees the tables when
# create_all() runs on the test engine.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.repository import DependencyWatcherRepository
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.report_injection.repository import ReportInjectionRepository
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.question_manager import QuestionManager
from daemon.write_pause_guard import WritePauseGuard

# Repo root: tests/unit/services/<file>.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]

# The canonical section title of the commission's report contract
# (D2 — "Open questions for the orchestrator", user verbatim).
OPEN_QUESTIONS_HEADING = "Open questions for the orchestrator"

# The open-questions block as a child would write it in its final report.
OPEN_QUESTIONS_BLOCK = (
    f"{OPEN_QUESTIONS_HEADING}\n"
    "1. Which environment should the migration target — staging or "
    "production?\n"
)

# A realistic child report that CARRIES the open-questions section.
CHILD_REPORT_TEXT = (
    "## Report\n"
    "Implemented the migration script and verified it against a scratch "
    "database.\n"
    "\n"
    f"{OPEN_QUESTIONS_BLOCK}"
)

# A control report that does NOT carry the section (proves the T6
# assertions are not vacuous — the section is read from the payload, not
# assumed).
CONTROL_REPORT_TEXT = (
    "## Report\n"
    "Implemented the migration script and verified it against a scratch "
    "database.\n"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL, per project convention).

    File-backed (not ``:memory:``) so each session gets its own
    connection — the answer path and the child-completion path both open
    their own sessions. The path lives under ``tmp_path`` — never a
    HOME-derived / live-shaped path.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/child_report_open_questions.db",
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


@pytest.fixture(autouse=True)
def bus(engine: Engine):
    """Wire a REAL ``DependencyBus`` bound to the test engine (autouse).

    ``child_reports._process_child_completion_db_sync`` treats a missing
    bus as a HARD ERROR (A8) on the child-completion seam, and it is the
    SOLE completion authority for the parent's pending-children count.
    The bus is constructed over the REAL watcher repository; only the
    background cache-warm loop is skipped (the seam under test consults
    the repository through ``count_pending_for_target_sync``).
    """
    b = DependencyBus(DependencyWatcherRepository(engine))
    set_dependency_bus(b)
    try:
        yield b
    finally:
        set_dependency_bus(None)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

NOW_ISO = "2026-10-20T09:00:00+00:00"


def _mk_instance(
    instance_id: str,
    *,
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
    agent_id: str = "worker",
) -> Instance:
    return Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"/agents/{agent_id}",
        agent_name=agent_id,
        parent_id=parent_id,
        status=status,
        version=1,
        instance_metadata={},
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def _seed_instance(
    engine: Engine,
    instance_id: str,
    *,
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
    agent_id: str = "worker",
) -> str:
    with Session(engine) as session:
        session.add(
            _mk_instance(
                instance_id,
                status=status,
                parent_id=parent_id,
                agent_id=agent_id,
            )
        )
        session.commit()
    return instance_id


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Build a real ``ChildReportsService`` with a ``MagicMock`` manager.

    Mirrors ``tests/unit/services/test_child_reports.py`` (and the
    ``test_revive_non_replay.py`` variant): the manager facade is a mock,
    the durable state and the production insert path are real.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None  # SSE no-op (guarded on truthiness)
    manager._queue_repository = MagicMock()
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._task_repo = None
    manager._deferred_question_pause = set()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None  # lifecycle event publish is guarded
    return service


def _report_message_row(
    engine: Engine, parent_id: str
) -> MessageQueue | None:
    with Session(engine) as session:
        stmt = sm_select(MessageQueue).where(
            MessageQueue.instance_id == parent_id
        )
        rows = list(session.exec(stmt).all())
        reports = [
            r
            for r in rows
            if r.type == MessageType.COMPLETION_REPORT.value
        ]
        return reports[0] if reports else None


def _report_task_row(engine: Engine, parent_id: str) -> Task | None:
    with Session(engine) as session:
        stmt = sm_select(Task).where(
            Task.instance_id == parent_id,
            Task.task_type == TaskType.PROCESS_REPORT.value,
        )
        rows = list(session.exec(stmt).all())
        return rows[0] if rows else None


def _injection_row(
    engine: Engine, parent_id: str
) -> ReportInjection | None:
    with Session(engine) as session:
        stmt = sm_select(ReportInjection).where(
            ReportInjection.parent_instance_id == parent_id
        )
        rows = list(session.exec(stmt).all())
        return rows[0] if rows else None


# ---------------------------------------------------------------------------
# T6 — the open-questions section reaches the parent
# ---------------------------------------------------------------------------


class TestT6OpenQuestionsReachParent:
    """T6 — a child's completion report carrying the "Open questions for
    the orchestrator" section reaches the parent through the REAL
    child-completion path (``ChildReportsService.
    _process_child_completion_db_sync``).

    The three durable artifacts the parent depends on are asserted
    individually, plus the drain (the production
    ``ReportInjectionRepository.claim_for_injection`` path the parent's
    live agent-node uses) — proving the parent can read the open
    questions out of the injection row ALONE.
    """

    def _drive(
        self, engine: Engine, report_text: str
    ) -> tuple[str, str, str]:
        """Run the real child-completion path; return (parent, child, msg)."""
        parent_id = _seed_instance(
            engine, "parent-1", agent_id="leader"
        )
        child_id = _seed_instance(
            engine, "child-1", parent_id=parent_id
        )
        completed_message_id = f"msg-{uuid.uuid4().hex[:8]}"

        service = _build_child_reports_service(engine)
        result = service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id=completed_message_id,
            last_content=report_text,
        )
        assert result.outcome == "regular_child_completed", (
            f"child-completion path did not take the regular-child "
            f"branch; got {result.outcome!r}"
        )
        assert result.parent_id == parent_id
        return parent_id, child_id, completed_message_id

    def test_t6_completion_report_row_is_addressed_to_the_parent(
        self, engine: Engine
    ) -> None:
        """(a) The completion report row exists, typed COMPLETION_REPORT,
        sourced ``internal_report:<child>:<msg_id>``, addressed to the
        child's ``parent_id``."""
        parent_id, child_id, completed_message_id = self._drive(
            engine, CHILD_REPORT_TEXT
        )

        row = _report_message_row(engine, parent_id)
        assert row is not None, (
            "no COMPLETION_REPORT message row was created for the parent"
        )
        assert row.instance_id == parent_id, (
            "the completion report must be addressed to the child's "
            "parent_id"
        )
        assert row.type == MessageType.COMPLETION_REPORT.value
        assert row.source == (
            f"internal_report:{child_id}:{completed_message_id}"
        ), (
            "the completion report row must carry the per-message "
            "idempotency source internal_report:<child>:<msg_id>"
        )
        assert row.content == CHILD_REPORT_TEXT, (
            "the completion report body must be the child's report "
            "verbatim"
        )
        assert OPEN_QUESTIONS_HEADING in row.content

    def test_t6_process_report_task_is_created_for_the_parent(
        self, engine: Engine
    ) -> None:
        """(b) A ``PROCESS_REPORT`` task is created for the parent and
        points at the completion-report message."""
        parent_id, _child_id, _completed_message_id = self._drive(
            engine, CHILD_REPORT_TEXT
        )

        message_row = _report_message_row(engine, parent_id)
        assert message_row is not None

        task_row = _report_task_row(engine, parent_id)
        assert task_row is not None, (
            "no PROCESS_REPORT task was created for the parent"
        )
        assert task_row.instance_id == parent_id
        assert task_row.task_type == TaskType.PROCESS_REPORT.value
        assert task_row.status == TaskStatus.PENDING.value
        assert task_row.message_id == message_row.message_id, (
            "the PROCESS_REPORT task must point at the completion-report "
            "message row"
        )

    def test_t6_report_injection_row_keeps_the_section_verbatim(
        self, engine: Engine
    ) -> None:
        """(c) A ``ReportInjection`` row is created for the parent and its
        ``content`` carries the open-questions section INTACT."""
        parent_id, child_id, completed_message_id = self._drive(
            engine, CHILD_REPORT_TEXT
        )

        row = _injection_row(engine, parent_id)
        assert row is not None, (
            "no ReportInjection row was created for the parent"
        )
        assert row.parent_instance_id == parent_id
        assert row.child_instance_id == child_id
        assert row.child_message_id == completed_message_id
        assert row.state == ReportInjectionState.PENDING.value
        assert row.content is not None
        assert OPEN_QUESTIONS_BLOCK in row.content, (
            "the ReportInjection content must carry the open-questions "
            "section verbatim — the injection is the parent's ONLY "
            "self-contained copy of the child's report"
        )
        assert row.content == CHILD_REPORT_TEXT

    def test_t6_parent_drains_the_open_questions_from_the_injection_alone(
        self, engine: Engine
    ) -> None:
        """The parent can read the open questions out of the injection row
        ALONE — the production drain (``claim_for_injection``) returns the
        verbatim content, exactly once."""
        parent_id, _child_id, _completed_message_id = self._drive(
            engine, CHILD_REPORT_TEXT
        )

        repo = ReportInjectionRepository(engine)
        drained = repo.claim_for_injection(parent_id)
        assert len(drained) == 1, (
            f"expected exactly one drained report, got {len(drained)}"
        )
        assert OPEN_QUESTIONS_BLOCK in drained[0]["content"], (
            "the drained report must still carry the open-questions "
            "section — the parent reads them from the injection alone"
        )
        assert drained[0]["content"] == CHILD_REPORT_TEXT

        # Exactly-once: the claim is terminal, a second drain yields
        # nothing (the guard is the ``WHERE state='PENDING'`` UPDATE).
        assert repo.claim_for_injection(parent_id) == []

        row = _injection_row(engine, parent_id)
        assert row is not None
        assert row.state == ReportInjectionState.INJECTED.value

    def test_t6_control_report_without_section_carries_no_section(
        self, engine: Engine
    ) -> None:
        """NON-VACUITY CONTROL: a report WITHOUT the section produces an
        injection row WITHOUT the section — the T6 assertions read the
        payload, they do not assume it."""
        parent_id, _child_id, _completed_message_id = self._drive(
            engine, CONTROL_REPORT_TEXT
        )

        row = _injection_row(engine, parent_id)
        assert row is not None
        assert OPEN_QUESTIONS_HEADING not in (row.content or "")
        assert row.content == CONTROL_REPORT_TEXT


# ---------------------------------------------------------------------------
# T7 — the parent's instruction revives the terminal child
# ---------------------------------------------------------------------------


def _build_messaging_service(engine: Engine):
    """Build a real ``InstanceMessagingService`` over the test engine.

    Mirrors ``tests/unit/services/test_a1_revival_carve_out.py``: the
    manager facade is a ``MagicMock``; the durable state and the
    production insert path are real.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._graph_tasks = {}
    manager._deferred_question_pause = set()
    manager._worker_pool = None
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._generate_and_broadcast_title = AsyncMock()
    manager._job_queue_service = None
    manager._instance_repository = SQLModelInstanceRepository(engine)

    cancellation = MagicMock()
    cancellation.is_shutting_down = False

    return InstanceMessagingService(
        manager=manager, cancellation_service=cancellation
    ), manager


def _instance_status(engine: Engine, instance_id: str) -> str | None:
    with Session(engine) as session:
        row = session.get(Instance, instance_id)
        return row.status if row is not None else None


def _message_rows(engine: Engine, instance_id: str) -> list[MessageQueue]:
    with Session(engine) as session:
        stmt = sm_select(MessageQueue).where(
            MessageQueue.instance_id == instance_id
        )
        return list(session.exec(stmt).all())


def _process_message_tasks(
    engine: Engine, instance_id: str
) -> list[Task]:
    with Session(engine) as session:
        stmt = sm_select(Task).where(
            Task.instance_id == instance_id,
            Task.task_type == TaskType.PROCESS_MESSAGE.value,
        )
        return list(session.exec(stmt).all())


class TestT7ParentInstructionRevivesTerminalChild:
    """T7 — the parent instructs the child; a TERMINAL child is revived
    (``status -> RUNNING``) and the instruction is delivered into the
    child's next turn.

    The revive semantics live in
    ``daemon/services/instance_messaging.py::_prepare_enqueued_message``
    (the shared prelude every send goes through): COMPLETED / TERMINATED
    / ERROR / FAILED all revive; PAUSED does not.
    """

    @pytest.mark.parametrize(
        "terminal_status",
        [
            InstanceStatus.COMPLETED.value,
            InstanceStatus.TERMINATED.value,
            InstanceStatus.ERROR.value,
            InstanceStatus.FAILED.value,
        ],
    )
    async def test_t7_terminal_child_is_revived_and_the_instruction_queued(
        self, engine: Engine, terminal_status: str
    ) -> None:
        """A send to a terminal child revives it and materialises the
        instruction as a message row + a PENDING ``PROCESS_MESSAGE``
        task — the carrier of the child's next turn."""
        child_id = _seed_instance(
            engine, "child-1", status=terminal_status
        )
        service, _manager = _build_messaging_service(engine)

        result = await service.enqueue_message(
            instance_id=child_id,
            message="Answer the open question: staging environment please.",
            source="internal_agent:parent-1",
            priority=1,
        )
        assert result.status == "queued"

        assert _instance_status(engine, child_id) == (
            InstanceStatus.RUNNING.value
        ), (
            f"a {terminal_status!r} child must be revived to RUNNING by a "
            f"new message; got "
            f"{_instance_status(engine, child_id)!r}"
        )

        messages = _message_rows(engine, child_id)
        assert len(messages) == 1, (
            f"expected exactly one queued message for the child, got "
            f"{len(messages)}"
        )
        assert (
            "Answer the open question: staging environment please."
            in messages[0].content
        ), "the instruction must be queued verbatim for the child"

        tasks = _process_message_tasks(engine, child_id)
        assert len(tasks) == 1, (
            f"expected exactly one PROCESS_MESSAGE task for the child, "
            f"got {len(tasks)}"
        )
        assert tasks[0].status == TaskStatus.PENDING.value
        assert tasks[0].message_id == messages[0].message_id, (
            "the PROCESS_MESSAGE task must point at the queued message"
        )

    async def test_t7_paused_child_is_not_revived(
        self, engine: Engine
    ) -> None:
        """CONTROL: a PAUSED child is NOT revived by a send — the pause
        owns the resume. The instruction is still queued (it is delivered
        on resume), and the status stays PAUSED."""
        child_id = _seed_instance(
            engine, "child-1", status=InstanceStatus.PAUSED.value
        )
        service, _manager = _build_messaging_service(engine)

        await service.enqueue_message(
            instance_id=child_id,
            message="Answer the open question: staging environment please.",
            source="internal_agent:parent-1",
            priority=1,
        )

        assert _instance_status(engine, child_id) == (
            InstanceStatus.PAUSED.value
        ), "a send must never flip a PAUSED instance to RUNNING"

        messages = _message_rows(engine, child_id)
        assert len(messages) == 1
        assert "Answer the open question" in messages[0].content

        tasks = _process_message_tasks(engine, child_id)
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.PENDING.value

    async def test_t7_tool_send_message_routes_terminal_target_to_revive(
        self,
    ) -> None:
        """T7 (tool level): the agent-facing ``send_message`` tool routes
        a terminal target through the enqueue-revive branch (the shared
        ``_prepare_enqueued_message`` path) and reports the revive in its
        result text.

        Harness: the shared factory from
        ``tests/helpers/send_message_fixtures.py`` (same stack the sibling
        ``tests/unit/tools/test_instance_tools.py`` uses)."""
        from tests.helpers.send_message_fixtures import (
            get_send_message_tool,
            make_send_message_manager,
        )

        manager = make_send_message_manager(status="completed")
        manager.config.llm.allowed_models = []
        send_message = get_send_message_tool(manager)

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            result = await send_message.coroutine(
                "child-1", "Answer the open question: staging please."
            )

        assert "revived and message dispatched" in result, (
            f"the terminal-revive branch must report the revive; got "
            f"{result!r}"
        )
        assert manager.enqueue_message.await_count == 1, (
            "the terminal target must be dispatched through the durable "
            "enqueue path (the shared _prepare_enqueued_message revive)"
        )
        enqueue_kwargs = manager.enqueue_message.await_args.kwargs
        assert enqueue_kwargs["instance_id"] == "child-1"
        assert (
            "Answer the open question: staging please."
            in enqueue_kwargs["message"]
        )
        manager.set_injection.assert_not_called()

    async def test_t7_tool_send_message_rejects_a_paused_target(self) -> None:
        """T7 (tool level, control): a PAUSED target is REJECTED by the
        agent-tool path — no revive, no dispatch."""
        from tests.helpers.send_message_fixtures import (
            get_send_message_tool,
            make_send_message_manager,
        )

        manager = make_send_message_manager(status="paused")
        manager.config.llm.allowed_models = []
        send_message = get_send_message_tool(manager)

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            result = await send_message.coroutine(
                "child-1", "Answer the open question: staging please."
            )

        assert "PAUSED" in result
        manager.enqueue_message.assert_not_called()
        manager.set_injection.assert_not_called()


class _ToolRegistrySpy:
    """Records every tool-registry consultation; the context manager
    makes each recorded call FAIL LOUD so a dependency on the registry
    cannot pass silently."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _make(self, name: str):
        def _spy(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(
                f"the answer path consulted the tool registry via {name!r} "
                f"— the answer path must stay independent of the tool "
                f"registration machinery"
            )

        return _spy

    def __enter__(self) -> "_ToolRegistrySpy":
        from daemon.tools import _tool_registry

        self._patches = [
            patch.object(
                _tool_registry, "list_tools", side_effect=self._make("list_tools")
            ),
            patch.object(
                _tool_registry,
                "list_tools_by_category",
                side_effect=self._make("list_tools_by_category"),
            ),
            patch.object(
                _tool_registry,
                "get_tool_categories",
                side_effect=self._make("get_tool_categories"),
            ),
            patch.object(
                _tool_registry,
                "discover_all_tool_names",
                side_effect=self._make("discover_all_tool_names"),
            ),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for p in reversed(self._patches):
            p.stop()


def _tool_registry_spy() -> _ToolRegistrySpy:
    return _ToolRegistrySpy()


# ---------------------------------------------------------------------------
# T8 — a legacy pack stays answerable by a root WITHOUT the tool
# ---------------------------------------------------------------------------


class _AnswerManager:
    """Minimal manager surface for the shared answer helper.

    Copied in spirit from ``tests/unit/test_question_pause_resume_asymmetry.py``:
    REAL instance repository, REAL ``QuestionManager``, REAL durable
    shadow, REAL lifecycle cascade; the graph driver
    (``resume_processing_job``) is stubbed — the graph is not the subject
    of T8.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.write_guard = WritePauseGuard()
        self._instance_repository = SQLModelInstanceRepository(engine)
        self._task_repo = TaskRepository(engine)
        self._question_manager = QuestionManager()
        self._request_registry = MagicMock()
        self._request_registry.cancel_by_instance = MagicMock(return_value=0)
        self._live_hub = MagicMock()
        self._live_hub.stream_status_change = AsyncMock()
        self._live_hub.stream_message = AsyncMock()
        self._live_hub.stream_question_pack = AsyncMock()
        self._live_hub.stream_answer_received = AsyncMock()
        self._event_bus = None
        self._work_resolver = None
        self._graph_tasks: dict = {}
        self._gii_throttle: dict = {}
        self._loop_breaker_state: dict = {}
        self._deferred_question_pause: set[str] = set()
        self.is_write_paused = False
        self.resume_calls: list[dict] = []
        self.enqueued: list[dict] = []
        self.release_context_usage_cache = MagicMock(return_value=None)
        self.clear_injection = MagicMock(return_value=None)

        # The REAL lifecycle cascade (the answer path's banner-9 seam).
        self._lifecycle = InstanceLifecycleService.__new__(
            InstanceLifecycleService
        )
        self._lifecycle._manager = self

    async def resume_instance_cascade(self, instance_id: str) -> dict:
        """REAL cascade resume (the same facade the sibling suite uses)."""
        return await self._lifecycle.resume_instance_cascade(instance_id)

    async def resume_processing_job(
        self,
        instance_id: str,
        message: str | None = None,
        silent: bool = False,
        **kwargs,
    ) -> dict:
        """Stub of the graph driver — records the delivered answer."""
        self.resume_calls.append(
            {"instance_id": instance_id, "message": message, "silent": silent}
        )
        return {"instance_id": instance_id, "status": "resumed"}

    async def enqueue_message(
        self, instance_id: str, message: str, source: str | None = None, **kwargs
    ):
        self.enqueued.append(
            {"instance_id": instance_id, "message": message, "source": source}
        )
        return MagicMock(message_id="msg-enqueued", job_id="job-enqueued")


def _stage_agent_without_question_category(
    tmp_path: Path, agent_id: str
) -> Path:
    """Stage a synthetic agent whose allow-list does NOT grant the
    "question" category (so its instances hold no ``ask_questions``
    tool), based on the real ``watcher`` meta shape (the convention used
    by ``tests/unit/tools/test_question_tool_root_gate.py``)."""
    real_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    real_meta.pop("watchover", None)
    real_meta["id"] = agent_id
    real_meta["name"] = agent_id.title()
    real_meta["team_members"] = []
    real_meta["innate_skills"] = []
    real_meta["tools"] = {"allow": ["read_file"], "deny": []}
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "meta.json").write_text(
        json.dumps(real_meta), encoding="utf-8"
    )
    return agents_dir


class TestT8LegacyPackStaysAnswerableWithoutTheQuestionTool:
    """T8 — defense in depth: a question pack created BEFORE the gate
    (by an instance that no longer holds ``ask_questions``) is still
    answered, by a root that does NOT hold the tool either.

    The answer path (durable-shadow resolution + rehydration + answer CAS)
    is SEPARATE machinery from the tool registration — this suite proves
    the separation instead of asserting it.
    """

    async def test_t8_legacy_pack_is_answered_after_the_ram_store_is_gone(
        self, engine: Engine
    ) -> None:
        """The legacy-pack scenario: the pack was created before the gate
        (RAM pack + durable shadow); the RAM copy is then GONE (the daemon
        restarted / the pack was only ever durable) — the answer path
        rehydrates from the durable shadow and the CAS fires exactly once.

        Driven through the REAL shared answer helper
        (``answer_questions_via_instance``) — the single implementation
        behind both the instance-addressed surface and ``job_answer``.
        """
        from daemon.routers.answer_helper import answer_questions_via_instance
        from daemon.services.midflight_qa import (
            QUESTION_PACK_PAYLOAD_METADATA_KEY,
            stamp_question_pack_metadata,
        )

        manager = _AnswerManager(engine)
        # The ASKER is a CHILD of a root: post-gate a non-root holds NO
        # ask_questions tool at all, so this is the "legacy pack created
        # by a now-tool-less instance" shape.
        _seed_instance(engine, "root-1", agent_id="leader")
        asker_id = _seed_instance(
            engine, "asker-1", parent_id="root-1", agent_id="worker"
        )

        # ── Pre-gate ask: the pack is created and shadowed durably.
        pack = manager._question_manager.set_question_pack(
            asker_id, [{"id": "q1", "text": "Which environment should I use?"}]
        )
        assert pack is not None
        stamp_question_pack_metadata(manager, asker_id, pack)

        # The durable shadow exists (this is what survives the gate).
        shadow = manager._instance_repository.get_metadata_value(
            asker_id, QUESTION_PACK_PAYLOAD_METADATA_KEY
        )
        assert isinstance(shadow, dict) and shadow.get("status") == "pending"

        # ── The RAM copy is GONE (restart shape) — only the shadow is left.
        manager._question_manager.clear_question_pack(asker_id)
        assert manager._question_manager.get_question_pack(asker_id) is None

        # ── The answer arrives (post-gate). The tool registry is spied to
        # EXPLODE if the answer path ever consults it.
        with _tool_registry_spy() as spy:
            response = await answer_questions_via_instance(
                manager,
                asker_id,
                {"q1": "staging"},
                None,
                None,
            )

        assert spy.calls == [], (
            f"the answer path must not consult the tool registry; it "
            f"called {spy.calls!r}"
        )
        assert response["status"] == "answered", (
            f"the legacy pack was not answered; response={response!r}"
        )
        assert response["resume_route"] == "answer_gate_existing_turn"

        # The CAS flipped the pack to answered and stored the answers.
        answered_pack = manager._question_manager.get_question_pack(asker_id)
        assert answered_pack is not None
        assert answered_pack.status == "answered"
        assert answered_pack.answers == {"q1": "staging"}
        assert answered_pack.id == pack.id, (
            "the rehydrated pack must keep the ORIGINAL pack id"
        )

        # The answer was actually delivered into the asker's turn.
        assert len(manager.resume_calls) == 1
        assert manager.resume_calls[0]["instance_id"] == asker_id
        assert "staging" in (manager.resume_calls[0]["message"] or "")

        # The durable shadow is cleared at answer consumption.
        assert (
            manager._instance_repository.get_metadata_value(
                asker_id, QUESTION_PACK_PAYLOAD_METADATA_KEY
            )
            is None
        )

    async def test_t8_duplicate_answer_is_a_cas_loser(
        self, engine: Engine
    ) -> None:
        """The answer CAS fires exactly once: a second answer is a LOSER
        (already-delivered no-op) and does not re-deliver."""
        from daemon.routers.answer_helper import answer_questions_via_instance

        manager = _AnswerManager(engine)
        asker_id = _seed_instance(engine, "asker-1", agent_id="worker")
        assert (
            manager._question_manager.set_question_pack(
                asker_id, [{"id": "q1", "text": "Which environment?"}]
            )
            is not None
        )

        first = await answer_questions_via_instance(
            manager, asker_id, {"q1": "staging"}, None, None
        )
        second = await answer_questions_via_instance(
            manager, asker_id, {"q1": "production"}, None, None
        )

        assert first["status"] == "answered"
        assert second["status"] == "already_delivered"
        assert second["resume_route"] == "already_delivered"
        assert len(manager.resume_calls) == 1, (
            "the CAS loser must not re-deliver the answer"
        )
        answered_pack = manager._question_manager.get_question_pack(asker_id)
        assert answered_pack is not None
        assert answered_pack.answers == {"q1": "staging"}, (
            "the CAS winner's answers are authoritative"
        )

    def test_t8_answer_path_modules_never_reference_the_tool_registry(
        self,
    ) -> None:
        """STATIC half of the separation proof: the answer-path modules
        neither IMPORT the tools package nor NAME the question tool /
        the tool registry in CODE.

        The check is AST-based on purpose: a prose mention in a docstring
        (e.g. "the ``ask_questions`` tool boundary normalizes ...") is not
        a dependency, but an import or an attribute access IS.
        """
        import ast

        answer_path_modules = [
            REPO_ROOT / "daemon" / "routers" / "answer_helper.py",
            REPO_ROOT / "daemon" / "services" / "question_manager.py",
            REPO_ROOT / "daemon" / "services" / "midflight_qa.py",
        ]
        forbidden_names = {
            "create_question_tools",
            "question_tools",
            "_tool_registry",
            "ask_questions",
        }
        for path in answer_path_modules:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: set[str] = set()
            referenced: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    imported.add(module)
                    imported.update(
                        f"{module}.{alias.name}" for alias in node.names
                    )
                elif isinstance(node, ast.Name):
                    referenced.add(node.id)
                elif isinstance(node, ast.Attribute):
                    referenced.add(node.attr)

            tools_imports = sorted(
                m for m in imported if m.startswith("daemon.tools")
            )
            assert tools_imports == [], (
                f"{path.name} imports the tools package {tools_imports!r} — "
                f"the answer path must stay independent of the tool "
                f"registration machinery"
            )
            hits = sorted(forbidden_names & referenced)
            assert hits == [], (
                f"{path.name} references {hits!r} in code — the answer path "
                f"must stay independent of the tool registration machinery"
            )

    def test_t8_a_root_without_the_question_category_holds_no_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The premise of T8: a USER-FACING ROOT whose agent does not
        grant the "question" category holds no ``ask_questions`` tool at
        all — so an answer that succeeds for such a root could not have
        gone through the tool.

        The toolset is built with the REAL factory
        (``daemon.tools.instance.create_instance_tools``) — nothing about
        the built toolset is stubbed.
        """
        import daemon.registry as dr
        from daemon.registry import AgentRegistry
        from daemon.tools.instance import create_instance_tools

        agent_id = "syn-root-no-question"
        agents_dir = _stage_agent_without_question_category(
            tmp_path, agent_id
        )
        registry = AgentRegistry(agents_dir)
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)

        manager = MagicMock(name="InstanceManager")
        manager.config.daemon.port = 0
        manager.config.llm.allowed_models = []
        tools = create_instance_tools(
            manager,
            f"inst-{agent_id}",
            agent_id,
            parent_id=None,
            source_type="discord",
        )
        tool_names = {getattr(t, "name", "?") for t in tools}

        assert "ask_questions" not in tool_names, (
            "a root whose agent does not grant the 'question' category "
            "must hold no ask_questions tool — otherwise T8's premise "
            "(a root WITHOUT the tool answers) would be vacuous"
        )
