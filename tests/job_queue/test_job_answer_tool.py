"""job_answer tool tests — agent-facing counterpart of POST /api/jobs/{work_id}/answer.

The ``job_answer`` tool in ``daemon/tools/job_queue.py`` is the
agent-facing twin of ``POST /api/jobs/{work_id}/answer``
(``daemon/routers/jobs_management.py``). Both surfaces share the SAME
underlying helper, ``answer_questions_via_instance``
(``daemon/routers/answer_helper.py``) — the tool never re-implements
the answer flow. These tests exercise the tool boundary (work_id →
instance resolution, project-scoped access control, error-string
shaping) by calling ``answer_questions_via_instance`` for real (the
helper path is already covered by ``test_midflight_qa.py`` §9.1/§9.2
and ``test_answer_resume_real_chain.py``).

Harness mirrors ``tests/job_queue/test_midflight_qa.py`` (its T1″
stale-pack test at ~:800 is the closest precedent for the helper
seam) and the tool-invocation pattern from
``tests/unit/tools/test_job_visibility_tools.py`` (``tool.ainvoke({...})``
+ ``create_job_tools``).

Coverage (brief mandate):
  (a) Happy path — pending pack answered → ``answered`` status +
      resume info returned (work_id envelope + the helper body).
  (b) Stale / mismatched ``question_pack_id`` → ``QUESTION_PACK_MISMATCH``
      surfaced in the error string (the agent must read WHY it
      failed; the brief calls this out specifically).
  (c) Wrong state — no pending pack → friendly error string carrying
      ``NO_PENDING_QUESTION`` + the ``job_continue`` hint.
  (d) Access-control mismatch — caller project ≠ job project →
      access denied (the same ``_check_job_access`` helper the four
      visibility tools use).

Plus supplementary smoke tests:
  * Tool registration — ``create_job_tools_if_available`` returns the
    ``job_answer`` tool when the manager's services are wired.
  * Write-paused posture — 503 surfaced in the error string.
  * Frozen tool-name discovery — ``discover_source_only_tool_names()``
    picks up the new tool (drift pin against ``KNOWN_TOOL_NAMES``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Import models so ``SQLModel.metadata.create_all`` builds the full
# schema. Mirrors the recipe in
# ``tests/job_queue/test_answer_resume_real_chain.py:94-102``.
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.models.common import ErrorCodes
from daemon.repositories.event.repository import EventRepository
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.task.models import (
    SuspensionReason,
    Task,
    TaskStatus,
    TaskType,
)
from daemon.repositories.task.repository import TaskRepository
from daemon.services.event_bus import EventBus
from daemon.services.question_manager import QuestionManager
from daemon.services.work_resolver import WorkResolverService
from daemon.tools._tool_registry import (
    KNOWN_TOOL_NAMES,
    discover_source_only_tool_names,
)
from daemon.tools.job_queue import create_job_tools
from daemon.tools.job_queue import _format_answer_http_error

# Deterministic id matching the repo-wide autouse fixture
# (``tests/conftest.py:_ensure_system_default_project_id``); explicit
# here so a foreign conftest layout (or a missing autouse fixture in
# a stripped-down run) does not silently change the
# system-default carve-out behaviour.
TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


# =============================================================================
# Fixtures — real repos + manager mock (mirrors test_midflight_qa.py harness)
# =============================================================================


@pytest.fixture
def engine():
    """In-memory SQLite engine — single connection for cross-thread
    ``asyncio.to_thread`` calls (mirrors the recipe in
    ``test_midflight_qa.py:97-105``)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def harness(engine):
    """Real repos + a manager facade mock with the async seams the
    answer helper needs.

    Mirrors the ``harness`` fixture in
    ``tests/job_queue/test_midflight_qa.py:211-270`` — same
    component set (real repos + ``QuestionManager`` + ``EventBus`` +
    ``WorkResolverService``) wrapped by a ``MagicMock`` manager whose
    async seams are recorded (``resume_processing_job`` /
    ``resume_instance_cascade`` / ``enqueue_message`` / live hub SSE
    methods).
    """
    instance_repo = SQLModelInstanceRepository(engine)
    task_repo = TaskRepository(engine)
    job_repo = JobRepository(engine)
    watcher_repo = JobWatcherRepository(engine)
    event_repo = EventRepository(engine)
    event_bus = EventBus(event_repo=event_repo)
    work_resolver = WorkResolverService(
        task_repo=task_repo, job_repo=job_repo, instance_repo=instance_repo
    )
    qm = QuestionManager()

    manager = MagicMock()
    manager.engine = engine
    manager.is_write_paused = False
    manager._instance_repository = instance_repo
    manager._task_repo = task_repo
    manager._work_resolver = work_resolver
    manager._watcher_repo = watcher_repo
    manager._event_repo = event_repo
    manager._event_bus = event_bus
    manager._question_manager = qm
    manager._live_hub = MagicMock()
    manager._live_hub.stream_question_pack = AsyncMock()
    manager._live_hub.stream_answer_received = AsyncMock()
    manager.enqueue_message = AsyncMock()
    manager.resume_processing_job = AsyncMock(
        return_value={"status": "resumed", "instance_id": "x"}
    )
    manager.resume_instance_cascade = AsyncMock(
        return_value={
            "target_id": "x",
            "resumed_ids": ["x"],
            "skipped_ids": [],
        }
    )

    return SimpleNamespace(
        engine=engine,
        manager=manager,
        instance_repo=instance_repo,
        task_repo=task_repo,
        job_repo=job_repo,
        watcher_repo=watcher_repo,
        event_repo=event_repo,
        event_bus=event_bus,
        work_resolver=work_resolver,
        qm=qm,
    )


# =============================================================================
# Helpers
# =============================================================================


def _seed_instance(
    engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.PAUSED.value,
    project_id: str | None = None,
    parent_id: str | None = None,
    agent_id: str = "leader",
) -> str:
    """Insert an Instance row; returns its ``instance_id``."""
    iid = instance_id or f"inst-{uuid.uuid4()}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id=agent_id,
                agent_dir="/tmp",
                project_id=project_id,
                status=status,
                version=1,
                instance_metadata={},
                parent_id=parent_id,
            )
        )
        session.commit()
    return iid


def _seed_task(
    engine,
    iid: str,
    *,
    work_id: str | None = None,
    status: str = TaskStatus.PAUSED.value,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
) -> str:
    """Insert a Task row that the ``WorkResolver`` can resolve; returns
    its ``work_id``."""
    wid = work_id or str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            Task(
                work_id=wid,
                task_type=task_type,
                instance_id=iid,
                status=status,
                suspension_reason=SuspensionReason.AWAITING_ANSWER.value
                if status == TaskStatus.PAUSED.value
                else None,
                resume_target_turn_id=wid if status == TaskStatus.PAUSED.value else None,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return wid


def _seed_paused_asker_with_pending_pack(harness, *, project_id: str | None = "p"):
    """Seed a PAUSED asker + PAUSED task + a pending QuestionPack.

    Mirrors ``_seed_wedge_asker`` in
    ``tests/job_queue/test_midflight_qa.py:185-208`` (without the
    durable-shadow metadata stamp — the tool's access control uses
    ``record.project_id`` from the resolver, not the metadata).

    Returns ``(asker_id, work_id, pack)``.
    """
    asker = _seed_instance(
        harness.engine,
        status=InstanceStatus.PAUSED.value,
        project_id=project_id,
    )
    work_id = _seed_task(
        harness.engine,
        asker,
        status=TaskStatus.PAUSED.value,
    )
    pack = harness.qm.set_question_pack(
        asker, [{"id": "q1", "text": "Approach A or B?"}]
    )
    return asker, work_id, pack


def _make_job_answer_tool(
    harness, *, current_instance_id: str = ""
):
    """Build the ``job_answer`` tool via ``create_job_tools`` (the
    factory the production wiring uses).

    Order-pin landmine (tidier #2 — Medium, 2026-09-22): NEVER index
    by ``tools[-1]`` directly. The tool name is the only stable
    contract; the append-at-END contract is enforced by the
    ``TestJobAnswerRegistration`` test in this file. Use a
    name-based lookup so a future insertion (which the docstring
    on ``create_job_tools`` explicitly forbids) cannot silently
    re-target this helper at a different tool.
    """
    job_service = AsyncMock()
    job_service.get_work = AsyncMock(return_value=None)
    queue_mgmt_service = AsyncMock()
    dead_letter_service = MagicMock()

    tools = create_job_tools(
        job_service=job_service,
        queue_mgmt_service=queue_mgmt_service,
        dead_letter_service=dead_letter_service,
        current_instance_id=current_instance_id,
        manager=harness.manager,
    )

    return next(t for t in tools if t.name == "job_answer")


# =============================================================================
# (a) Happy path
# =============================================================================


class TestJobAnswerHappyPath:
    """(a) Pending pack answered → ``answered`` status + resume info
    returned with the work_id envelope."""

    @pytest.mark.asyncio
    async def test_pending_pack_answered_returns_envelope_and_resume_info(
        self, harness
    ):
        """Tool resolves work_id → asker, delegates to
        ``answer_questions_via_instance``, and surfaces the work_id
        envelope + the helper's instance-addressed body (``status``,
        ``question_pack``, ``resume_route``, ``resume_info``)."""
        asker, work_id, pack = _seed_paused_asker_with_pending_pack(harness)

        tool = _make_job_answer_tool(harness)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "Approach A"},
                "question_pack_id": pack.id,
            }
        )

        # Job-addressed envelope (§5.2): the work_id the caller used
        # rides alongside the helper body.
        assert result["work_id"] == work_id

        # Helper body — status is "answered" for the first-success
        # CAS winner; ``resume_route`` is the answer-gate existing
        # turn route (the awaiting_answer handle was consumed).
        assert result["status"] == "answered"
        assert result["resume_route"] == "answer_gate_existing_turn"
        assert result["instance_id"] == asker

        # The resume cascade was driven.
        harness.manager.resume_instance_cascade.assert_awaited_once_with(asker)

        # The CAS flipped the RAM pack to ``answered``.
        refreshed = harness.qm.get_question_pack(asker)
        assert refreshed.status == "answered"

    @pytest.mark.asyncio
    async def test_second_answer_is_already_delivered(self, harness):
        """Duplicate answer → CAS loser branch → ``already_delivered``
        (matches the HTTP-route contract; the tool mirrors it)."""
        asker, work_id, pack = _seed_paused_asker_with_pending_pack(harness)

        tool = _make_job_answer_tool(harness)

        first = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "A"},
                "question_pack_id": pack.id,
            }
        )
        assert first["status"] == "answered"

        # Second answer with the same work_id is the CAS-loser branch.
        # The helper's entry CAS arbitrates exactly-once; the loser's
        # short-circuit returns ``already_delivered`` WITHOUT re-running
        # the resume cascade.
        second = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "B"},
                "question_pack_id": pack.id,
            }
        )
        assert second["status"] == "already_delivered"
        assert second["resume_route"] == "already_delivered"
        assert second["work_id"] == work_id

        # The cascade was driven ONCE (the first answer only).
        assert harness.manager.resume_instance_cascade.await_count == 1


# =============================================================================
# (b) Stale / mismatched question_pack_id → QUESTION_PACK_MISMATCH
# =============================================================================


class TestJobAnswerStalePackId:
    """(b) Stale / mismatched ``question_pack_id`` → ``QUESTION_PACK_MISMATCH``
    surfaced in the error string."""

    @pytest.mark.asyncio
    async def test_stale_pack_id_returns_mismatch_error(self, harness):
        """T1″ (mid-flight QA channel, 2026-09-21): body
        ``question_pack_id`` ≠ current pack id → 400 hijack guard.

        The agent must be able to read WHY the answer failed. The
        error string carries the typed code + the helper's message
        verbatim so an agent can branch on the cause."""
        asker, work_id, pack = _seed_paused_asker_with_pending_pack(harness)

        tool = _make_job_answer_tool(harness)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "stale answer"},
                # NOT the pack.id — a stale id from a superseded pack.
                "question_pack_id": str(uuid.uuid4()),
            }
        )

        # The error string carries the typed code the brief requires.
        assert "error" in result
        assert ErrorCodes.QUESTION_PACK_MISMATCH.value in result["error"]
        assert "400" in result["error"]
        # The pack id mismatch detail is preserved (so the agent can
        # identify which pack was stale).
        assert work_id in result["error"]
        assert asker in result["error"]

        # The pack is still pending — the hijack did NOT stamp it.
        assert harness.qm.get_question_pack(asker).status == "pending"

        # The cascade was NOT driven.
        harness.manager.resume_instance_cascade.assert_not_awaited()


# =============================================================================
# (c) Wrong state — no pending pack → friendly error with hint
# =============================================================================


class TestJobAnswerNoPendingPack:
    """(c) Wrong state — no pending pack → friendly error string with
    the ``job_continue`` hint (the brief asks for this specifically)."""

    @pytest.mark.asyncio
    async def test_no_pending_pack_returns_no_pending_question_error(self, harness):
        """An asker with NO pending pack returns a friendly error string
        carrying either ``NO_PENDING_QUESTION`` (404 — durable shadow
        exists but is non-pending) or ``QUESTION_PACK_LOST`` (410 —
        no RAM pack and no durable shadow) AND the ``job_continue``
        hint so the agent doesn't loop on a job whose asker is past
        the answer window.

        The helper distinguishes these two failure shapes
        (see ``_resolve_pack_or_raise`` in ``answer_helper.py``). For
        THIS test the RAM pack and the durable shadow are both
        absent, so the helper raises 410 ``QUESTION_PACK_LOST``. The
        companion case (durable shadow present but ``status !=
        "pending"`` → 404 ``NO_PENDING_QUESTION``) is exercised by
        ``test_durable_shadow_non_pending_returns_no_pending_question``
        below."""
        # Seed an asker + task but NO question pack (RAM empty,
        # metadata empty — the 410 branch).
        asker = _seed_instance(
            harness.engine,
            status=InstanceStatus.PAUSED.value,
            project_id="p",
        )
        work_id = _seed_task(
            harness.engine,
            asker,
            status=TaskStatus.PAUSED.value,
        )
        # qm.get_question_pack(asker) is None — no pack.

        tool = _make_job_answer_tool(harness)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "yes"},
                "question_pack_id": "any-id",
            }
        )

        assert "error" in result
        # The brief asks for the hint in the no-pending/terminal case.
        assert "job_continue" in result["error"]
        assert work_id in result["error"]
        assert asker in result["error"]
        # Specifically the 410 QUESTION_PACK_LOST branch (no RAM, no
        # shadow). The helper's message includes "lost" — pin that to
        # catch silent regressions in the helper route.
        assert ErrorCodes.QUESTION_PACK_LOST.value in result["error"]

    @pytest.mark.asyncio
    async def test_durable_shadow_non_pending_returns_no_pending_question(
        self, harness
    ):
        """Companion case: durable shadow EXISTS but is non-pending
        (e.g. answered before the RAM store was lost). Helper raises
        404 ``NO_PENDING_QUESTION`` — same ``job_continue`` hint."""
        asker = _seed_instance(
            harness.engine,
            status=InstanceStatus.PAUSED.value,
            project_id="p",
        )
        work_id = _seed_task(
            harness.engine,
            asker,
            status=TaskStatus.PAUSED.value,
        )
        # Stamp a non-pending durable shadow — the
        # ``answer_helper._resolve_pack_or_raise`` 404 branch.
        harness.instance_repo.set_metadata(
            asker,
            "question_pack_id",
            "some-pack-id",
        )
        harness.instance_repo.set_metadata(
            asker,
            "question_pack_payload",
            {"status": "answered"},  # non-pending → 404 branch
        )

        tool = _make_job_answer_tool(harness)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "yes"},
                "question_pack_id": "any-id",
            }
        )

        assert "error" in result
        assert ErrorCodes.NO_PENDING_QUESTION.value in result["error"]
        assert "404" in result["error"]
        assert "job_continue" in result["error"]


# =============================================================================
# (d) Access-control mismatch
# =============================================================================


class TestJobAnswerAccessDenied:
    """(d) Access-control mismatch — caller project ≠ job project →
    access denied (same ``_check_job_access`` helper the four visibility
    tools enforce)."""

    @pytest.mark.asyncio
    async def test_project_mismatch_returns_access_denied(self, harness):
        """The tool reuses ``_check_job_access`` — a caller in
        project-A asking about a job in project-B is denied without
        the answer flow ever starting."""
        asker, work_id, pack = _seed_paused_asker_with_pending_pack(
            harness, project_id="proj-JOB"
        )
        # Seed a caller instance in a different (non-system-default)
        # project. The autouse ``_ensure_system_default_project_id``
        # conftest fixture pins the system default to a deterministic
        # uuid, so anything else falls outside the global-operator
        # carve-out and triggers the strict-match refusal.
        caller = _seed_instance(
            harness.engine,
            instance_id="caller-id",
            status=InstanceStatus.RUNNING.value,
            project_id="proj-CALLER",
        )

        tool = _make_job_answer_tool(harness, current_instance_id=caller)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "yes"},
                "question_pack_id": pack.id,
            }
        )

        # The SAME error string the four visibility tools emit on
        # project mismatch — _check_job_access returns the canonical
        # wording, and the tool passes it through verbatim.
        assert result == {
            "error": "Access denied: job does not belong to caller's project"
        }

        # The pack was NOT stamped (the answer flow never ran).
        assert harness.qm.get_question_pack(asker).status == "pending"
        harness.manager.resume_instance_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_same_project_allowed(self, harness):
        """Sibling sanity check: caller project == job project →
        allowed (the strict-match pass-through)."""
        asker, work_id, pack = _seed_paused_asker_with_pending_pack(
            harness, project_id="proj-shared"
        )
        caller = _seed_instance(
            harness.engine,
            instance_id="caller-id",
            status=InstanceStatus.RUNNING.value,
            project_id="proj-shared",
        )

        tool = _make_job_answer_tool(harness, current_instance_id=caller)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "yes"},
                "question_pack_id": pack.id,
            }
        )

        # No access-denied error.
        assert "error" not in result
        assert result["status"] == "answered"

    @pytest.mark.asyncio
    async def test_system_default_caller_allowed_across_projects(self, harness):
        """System-default (global-operator) caller may answer packs in
        any project — mirrors the ``_check_job_access`` carve-out the
        four visibility tools inherit (chat-facing agents run in the
        system-default project)."""
        asker, work_id, pack = _seed_paused_asker_with_pending_pack(
            harness, project_id="proj-X"
        )
        caller = _seed_instance(
            harness.engine,
            instance_id="caller-id",
            status=InstanceStatus.RUNNING.value,
            # The system-default project id from the conftest autouse
            # fixture — this is the global-operator tier.
            project_id=TEST_SYSTEM_PROJECT_ID,
        )

        tool = _make_job_answer_tool(harness, current_instance_id=caller)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "yes"},
                "question_pack_id": pack.id,
            }
        )

        assert "error" not in result
        assert result["status"] == "answered"


# =============================================================================
# Supplementary smoke tests
# =============================================================================


class TestJobAnswerRegistration:
    """``job_answer`` is registered exactly like its siblings — visible
    to ``create_job_tools`` and to the frozen tool-name discovery."""

    def test_create_job_tools_returns_job_answer(self):
        """``create_job_tools`` returns the ``job_answer`` tool as the
        LAST entry (positional-index trap)."""
        job_service = AsyncMock()
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        manager = MagicMock()

        tools = create_job_tools(
            job_service=job_service,
            queue_mgmt_service=queue_mgmt_service,
            dead_letter_service=dead_letter_service,
            manager=manager,
        )

        # Order-pin landmine (tidier #2 — Medium, 2026-09-22): ``job_answer``
        # is the LAST entry in the returned list. Pin that + the three
        # witnesses ([16] = job_inject, [17] = watch_job, [20] =
        # watch_jobs) so a future insertion (the docstring on
        # ``create_job_tools`` explicitly forbids this) cannot silently
        # shift either the new tool or the three witnesses.
        # ``tools[-1]`` is the ONLY place this positional index is
        # acceptable — every other test in this file uses the
        # name-based ``next(t for t in tools if t.name == ...)``
        # lookup so the append-at-END contract does not have to hold
        # for them.
        last = tools[-1]
        assert last.name == "job_answer", (
            f"Last tool in create_job_tools() should be 'job_answer', "
            f"got {last.name!r}"
        )
        # Append-at-END witnesses: index 16/17/20 must NOT shift when
        # ``job_answer`` is appended at index 21.
        assert tools[16].name == "job_inject"
        assert tools[17].name == "watch_job"
        assert tools[20].name == "watch_jobs"

    def test_job_answer_in_known_tool_names(self):
        """Drift pin: ``job_answer`` is in the static fallback universe
        so the frozen-binary validator sees it.

        (Mirrors the ``test_known_tool_names_matches_source_exactly_no_drift``
        pin — adding a tool without updating ``KNOWN_TOOL_NAMES``
        would let a stale agent-config entry pass validation silently.)
        """
        assert "job_answer" in KNOWN_TOOL_NAMES

    def test_discover_source_only_tool_names_includes_job_answer(self):
        """Source-discovery pin: ``discover_source_only_tool_names()``
        picks up the new ``@tool``-decorated ``job_answer`` from
        ``daemon/tools/job_queue.py``."""
        names = discover_source_only_tool_names()
        assert "job_answer" in names

    def test_job_answer_docstring_styled_consistently(self):
        """The tool docstring references ``tool_help("job_answer")``
        like every sibling (the family rendering contract)."""
        job_service = AsyncMock()
        tools = create_job_tools(
            job_service=AsyncMock(),
            queue_mgmt_service=AsyncMock(),
            dead_letter_service=MagicMock(),
            manager=MagicMock(),
        )
        # Name-based lookup (tidier #2 — Medium, 2026-09-22). See
        # ``_make_job_answer_tool`` for the rationale.
        job_answer = next(t for t in tools if t.name == "job_answer")
        docstring = job_answer.description or job_answer.__doc__ or ""
        assert 'tool_help("job_answer")' in docstring


class TestJobAnswerWritePause:
    """Write-pause posture (503) is surfaced in the error string —
    the agent should know the daemon is in migration mode and retry
    later, not treat the refusal as an unknown-job failure."""

    @pytest.mark.asyncio
    async def test_write_paused_returns_503_error(self, harness):
        harness.manager.is_write_paused = True

        tool = _make_job_answer_tool(harness)

        result = await tool.ainvoke(
            {
                "work_id": "any",
                "answers": {"q1": "x"},
                "question_pack_id": "any",
            }
        )

        assert "error" in result
        assert "503" in result["error"]
        assert "WRITE_PAUSED" in result["error"]
        # The migration hint is in the message so the agent knows
        # to retry once the daemon leaves migration mode.
        assert "migration" in result["error"].lower()


class TestJobAnswerUnknownWorkId:
    """Unknown ``work_id`` → 404 ``JOB_NOT_FOUND`` (the route's contract
    carries through to the tool)."""

    @pytest.mark.asyncio
    async def test_unknown_work_id_returns_job_not_found(self, harness):
        tool = _make_job_answer_tool(harness)

        result = await tool.ainvoke(
            {
                "work_id": str(uuid.uuid4()),  # never seeded
                "answers": {"q1": "yes"},
                "question_pack_id": "any",
            }
        )

        assert "error" in result
        assert ErrorCodes.JOB_NOT_FOUND.value in result["error"]
        assert "404" in result["error"]


class TestJobAnswerMissingManager:
    """When the manager isn't wired (test doubles / partial-init), the
    tool returns a clean error rather than raising."""

    @pytest.mark.asyncio
    async def test_no_manager_returns_clean_error(self):
        job_service = AsyncMock()
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()

        tools = create_job_tools(
            job_service=job_service,
            queue_mgmt_service=queue_mgmt_service,
            dead_letter_service=dead_letter_service,
            manager=None,  # the tool's defensive guard
        )
        # Name-based lookup (tidier #2 — Medium, 2026-09-22).
        job_answer = next(t for t in tools if t.name == "job_answer")

        result = await job_answer.ainvoke(
            {
                "work_id": "any",
                "answers": {"q1": "yes"},
                "question_pack_id": "any",
            }
        )

        assert "error" in result
        assert "manager" in result["error"].lower()

class TestJobAnswerLiveHubNone:
    """live_hub=None degrade test (reviewer warning #1, 2026-09-22).

    The harness always wires a mock ``manager._live_hub``, so the
    ``if live_hub is not None`` guard at
    ``answer_helper.py:304`` has no exercise at the tool level. When
    ``live_hub is None`` the helper's SSE emission is a no-op (the
    answer + resume paths still complete). This test exercises the
    happy path with ``live_hub=None`` and asserts the tool completes
    cleanly — no raise, answered status, resume info present.

    Real-world trigger: tests / partial-bootstrap contexts where the
    hub is not yet wired, AND the ``tools[-1]`` self-check that the
    code-review pipeline runs (no hub means no SSE, but the answer
    flow must still work).
    """

    @pytest.mark.asyncio
    async def test_live_hub_none_happy_path_completes(self, harness):
        """With ``manager._live_hub = None``, the tool still answers +
        resumes — the SSE branch is a clean no-op."""
        harness.manager._live_hub = None  # the seam the harness hides

        asker, work_id, pack = _seed_paused_asker_with_pending_pack(harness)

        tool = _make_job_answer_tool(harness)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "Approach A"},
                "question_pack_id": pack.id,
            }
        )

        # No exception escaped the tool — the live_hub=None branch is
        # genuinely a no-op (guarded at answer_helper.py:304), not a
        # raise.
        assert "error" not in result
        assert result["work_id"] == work_id
        assert result["status"] == "answered"
        assert result["resume_route"] == "answer_gate_existing_turn"
        assert result["instance_id"] == asker

        # The resume cascade was still driven — live_hub controls SSE
        # only; the CAS + resume path is independent of the hub.
        harness.manager.resume_instance_cascade.assert_awaited_once_with(asker)

        # The CAS flipped the RAM pack to ``answered``.
        refreshed = harness.qm.get_question_pack(asker)
        assert refreshed.status == "answered"


class TestFormatAnswerHttpError503RaceWindow:
    """503 race-window branch test (tidier paired item, 2026-09-22).

    The plain-string 503 branch of ``_format_answer_http_error`` is
    the one path the helper raises WITHOUT a typed ``ErrorResponse``
    body — just ``HTTPException(503, "Writes are paused for database
    migration")``. The docstring promises:
      * ``"503"`` status prefix in the error string
      * ``"migration"`` substring so an agent can branch on it
      * NO branchable typed code (the tool's own pre-check owns the
        ``WRITE_PAUSED`` token; this branch is the
        "pre-check-cleared-but-helper-re-raised" race window).

    The tool's pre-check path is covered by
    ``test_write_paused_returns_503_error``; this test exercises the
    helper directly with a synthetic HTTPException to pin the
    race-window contract independently of the tool wiring.
    """

    def test_race_window_503_string_branch(self):
        """Construct the plain-string HTTPException(503, ...) directly
        and verify the helper's shaped output matches the
        docstring-promised contract."""
        # The helper raises with this exact phrasing (no typed
        # ``ErrorResponse`` body — answer_helper.py:152-157).
        exc = HTTPException(
            status_code=503,
            detail="Writes are paused for database migration",
        )

        result = _format_answer_http_error(
            exc,
            work_id="job-1",
            instance_id="inst-1",
        )

        # Docstring promise: readable 503 message + no branchable
        # typed code (the typed WRITE_PAUSED token is owned by the
        # tool's own pre-check, not this race-window branch).
        assert "error" in result
        err = result["error"]
        assert "503" in err
        assert "migration" in err.lower()
        assert "WRITE_PAUSED" not in err  # race-window: no typed token
        # The work_id + instance_id are echoed so the agent can
        # correlate (mirrors the typed-code path).
        assert "job-1" in err
        assert "inst-1" in err

