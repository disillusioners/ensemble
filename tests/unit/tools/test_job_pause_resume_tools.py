"""Unit tests for the agent-facing ``job_pause`` / ``job_resume`` tools.

Tests the two tools added to ``create_job_tools()`` in
``daemon/tools/job_queue.py`` (job-pause-resume-tools feature, 2026-09-25).
The tools are THIN WRAPPERS over the same service layer the HTTP endpoints
use (``manager.pause_instance_cascade`` / ``manager.resume_instance_cascade``
/ ``manager.resume_processing_job``) — these tests pin the wrapper contract:

  * Work-resolution parity with ``job_cancel`` (``job_service.get_work``,
    ``record.instance_id``).
  * Job-shaped → instance-shaped translation; same facade calls as the
    HTTP ``/instances/{id}/pause`` and ``/instances/{id}/resume`` endpoints.
  * D1 (QUEUED no-instance) — clean actionable error, job state untouched.
  * Terminal job — clean error.
  * Unknown job_id — clean error.
  * D2 wedge-guard note surfaced on pause success.
  * D3 (resume message injection) — mirrors FE plain path: literal
    ``"resume"`` message via ``resume_processing_job(target, message="resume",
    silent=False)``, then cascade flip, then silent child resumes.
  * Question-pack guard on resume (mirrors ``resume_instance`` Defect-1).
  * Project-scoped access control via ``_check_job_access`` (same helper
    as ``job_messages`` / ``job_tree`` / ``job_progress`` / ``job_inject``).
  * Write-paused migration gate (FE parity).
  * Registration surfaces (factory list, category, KNOWN_TOOL_NAMES).

Scenario → test map (dispatch spec a–g):
  a. pause active job                  → TestJobPause::test_pause_active_job_pauses_instance
  b. pause D1 (QUEUED, no instance)    → TestJobPause::test_pause_queued_job_d1_refusal
  c. pause terminal job                → TestJobPause::test_pause_terminal_job_refused
  d. pause unknown job_id              → TestJobPause::test_pause_unknown_job_id
  e. pause access-control denial       → TestJobPauseAccessControl::test_pause_project_mismatch_denied
                                         TestJobPauseAccessControl::test_pause_system_default_caller_global_operator_allowed
  f. resume active job                 → TestJobResume::test_resume_active_job
  g. resume D1 (QUEUED, no instance)   → TestJobResume::test_resume_queued_job_d1_refusal
  h. resume question-pack guard        → TestJobResume::test_resume_question_pack_pending_refused
  i. resume access-control denial      → TestJobResumeAccessControl::test_resume_project_mismatch_denied

The fixture / mock style mirrors ``tests/unit/tools/test_pause_resume_instance_tools.py``
(the sibling template) and ``tests/unit/tools/test_job_continue_task_only_gate.py``
(the closest job-tool twin).

SAFETY FENCE: pure unit tests — mocks only, the daemon is NEVER booted and
no DB is ever touched, so no ambient ``POSTGRES_*`` can leak anywhere.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.tools._tool_registry import CATEGORY_MODULES, KNOWN_TOOL_NAMES


# Mirrors the sibling test files — the deterministic uuid5 the repo-wide
# autouse conftest fixture seeds into ``constants.SYSTEM_DEFAULT_PROJECT_ID``.
TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"

CALLER_ID = "caller-instance"
TARGET_INSTANCE_ID = "target-instance"
CHILD_INSTANCE_ID = "child-instance"
JOB_ID = "job-active-1"


# ── Helpers ──────────────────────────────────────────────────────────────────────


def _make_work_record(
    *,
    work_id: str = JOB_ID,
    kind: str = "job",
    status: str = "processing",
    instance_id: str | None = TARGET_INSTANCE_ID,
    project_id: str | None = "proj-A",
    agent_id: str | None = "developer",
) -> MagicMock:
    """Build a ``WorkRecord``-shaped mock matching the fields the new tools
    read: ``work_id``, ``kind``, ``status``, ``instance_id``, ``project_id``,
    ``agent_id`` (latter for completeness, not strictly read by either tool)."""
    record = MagicMock(name=f"WorkRecord[{work_id}]")
    type(record).work_id = property(lambda self: work_id)
    type(record).kind = property(lambda self: kind)
    type(record).status = property(lambda self: status)
    type(record).instance_id = property(lambda self: instance_id)
    type(record).project_id = property(lambda self: project_id)
    type(record).agent_id = property(lambda self: agent_id)
    return record


def _make_instance_row(instance_id: str, *, project_id: str | None) -> MagicMock:
    """Instance-shaped mock for ``manager._instance_repository.get`` —
    matches the row shape ``_check_job_access`` reads (the helper at
    ``daemon/tools/job_queue.py:910`` inspects ``caller.project_id``)."""
    row = MagicMock(name=f"InstanceRow[{instance_id}]")
    row.instance_id = instance_id
    row.project_id = project_id
    return row


def _wire_caller_project(manager: MagicMock, *, caller_project: str | None) -> None:
    """Wire ``manager._instance_repository.get`` with a caller row only —
    ``_check_job_access`` looks up the caller instance to read its
    project_id; the job's project_id comes off the WorkRecord directly."""
    caller_row = _make_instance_row(CALLER_ID, project_id=caller_project)
    manager._instance_repository.get = MagicMock(
        side_effect=lambda iid: caller_row if iid == CALLER_ID else None
    )


def _make_pause_resume_manager() -> MagicMock:
    """Manager mock wired for the job-shaped pause/resume tools.

    Mirrors the sibling ``_make_pause_resume_manager`` at
    ``test_pause_resume_instance_tools.py:87`` — exposes the three async
    facade methods the tools wrap, plus the question-manager surface the
    ``job_resume`` Defect-1 guard inspects.

    ``is_write_paused`` MUST be an explicit False — a bare MagicMock
    attribute is truthy and would trip the migration gate. The
    ``_question_manager.get_question_pack`` mock returns ``None`` by
    default (no pending question pack) so the resume tool's Defect-1
    guard falls through on the happy-path tests; pending-pack tests
    override the mock on a per-test basis.
    """
    manager = MagicMock(name="MockManager")
    manager.is_write_paused = False
    manager.pause_instance_cascade = AsyncMock(
        return_value={"paused_ids": [TARGET_INSTANCE_ID], "skipped_ids": []}
    )
    manager.resume_instance_cascade = AsyncMock(
        return_value={
            "resumed_ids": [TARGET_INSTANCE_ID],
            "skipped_ids": [],
            "target_id": TARGET_INSTANCE_ID,
        }
    )
    manager.resume_processing_job = AsyncMock(
        return_value={
            "instance_id": TARGET_INSTANCE_ID,
            "job_id": "work-1",
            "message_id": "msg-1",
            "status": "resuming",
        }
    )
    manager._instance_repository = MagicMock()
    manager._question_manager = MagicMock()
    manager._question_manager.get_question_pack = MagicMock(return_value=None)
    return manager


def _build_tools(
    manager: MagicMock,
    job_service: AsyncMock,
) -> list:
    """Build the real ``create_job_tools`` surface so both new tools can
    be extracted by name. No ``create_job_tools_if_available`` patch
    stack is needed — ``create_job_tools`` is a pure factory that
    returns the closure list directly (mirrors the existing
    ``test_job_continue_task_only_gate.py`` fixture)."""
    from daemon.tools.job_queue import create_job_tools

    return create_job_tools(
        job_service=job_service,
        queue_mgmt_service=AsyncMock(),
        dead_letter_service=MagicMock(),
        current_instance_id=CALLER_ID,
        manager=manager,
    )


def _get_tool(tools: list, name: str):
    tool_obj = next((t for t in tools if getattr(t, "name", None) == name), None)
    assert tool_obj is not None, f"{name} tool not found; got {[t.name for t in tools]}"
    return tool_obj


# ── Fixtures ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def manager() -> MagicMock:
    return _make_pause_resume_manager()


@pytest.fixture
def job_service() -> AsyncMock:
    """Async ``JobQueueService`` mock — only ``get_work`` is read by the new
    tools (parity with the sibling ``test_job_continue_task_only_gate``)."""
    svc = AsyncMock()
    return svc


@pytest.fixture
def tools(manager, job_service):
    return _build_tools(manager, job_service)


@pytest.fixture
def pause_tool(tools):
    return _get_tool(tools, "job_pause")


@pytest.fixture
def resume_tool(tools):
    return _get_tool(tools, "job_resume")


# ─────────────────────────────────────────────────────────────────────────────────
# Scenario (a)–(d): job_pause
# ─────────────────────────────────────────────────────────────────────────────────


class TestJobPause:
    """``job_pause`` wraps ``manager.pause_instance_cascade`` with the
    HTTP pause endpoint's default behavior (routers/instances.py:675),
    shaped on a job_id resolved via ``job_service.get_work``."""

    @pytest.mark.asyncio
    async def test_pause_active_job_pauses_instance(self, manager, job_service, pause_tool):
        """(a) Happy path: pause an ACTIVE job (processing + has instance_id) →
        the instance is paused; the cascade facade is called with the
        instance_id extracted from the WorkRecord; the response carries the
        service's structured result (paused_ids + skipped_ids), the
        D2 wedge-guard note, and the original instance_id."""
        job_service.get_work.return_value = _make_work_record(status="processing")
        _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)

        result = await pause_tool.coroutine(JOB_ID)

        assert result["paused"] is True
        assert result["paused_ids"] == [TARGET_INSTANCE_ID]
        assert result["skipped_ids"] == []
        assert result["instance_id"] == TARGET_INSTANCE_ID
        # D2 wedge-guard note is surfaced on the success response.
        assert "_wedge_note" in result
        assert "STUCK_AWAITING_ANSWER" in result["_wedge_note"]
        # Cascade facade called with the instance_id extracted from
        # the WorkRecord — default kwargs (cascade_to_root=True,
        # suspension_reason=None) inherited from the facade.
        manager.pause_instance_cascade.assert_awaited_once_with(TARGET_INSTANCE_ID)

    @pytest.mark.asyncio
    async def test_pause_queued_job_d1_refusal(self, manager, job_service, pause_tool):
        """(b) D1 — QUEUED job (status='pending', instance_id=None) →
        refusal with the actionable ``queue_pause`` suggestion; job
        state untouched (no facade call, no service side-effect)."""
        job_service.get_work.return_value = _make_work_record(
            status="pending", instance_id=None,
        )
        _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)

        result = await pause_tool.coroutine(JOB_ID)

        assert result["paused"] is False
        # Error names the D1 refusal + the suggested alternative tool.
        assert "queue_pause" in result["error"]
        assert "instance" in result["error"]
        # Job state untouched: the cascade facade was NEVER called.
        manager.pause_instance_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pause_terminal_job_refused(self, manager, job_service, pause_tool):
        """(c) Terminal job (status='completed'/'failed'/etc.) →
        clean refusal; facade untouched."""
        for terminal_status in ("completed", "failed", "cancelled", "dead_letter"):
            job_service.get_work.return_value = _make_work_record(status=terminal_status)
            _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)

            result = await pause_tool.coroutine(JOB_ID)

            assert result["paused"] is False, f"status={terminal_status}"
            assert "terminal state" in result["error"], f"status={terminal_status}"
            assert terminal_status in result["error"], f"status={terminal_status}"
            manager.pause_instance_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pause_unknown_job_id(self, manager, job_service, pause_tool):
        """Unknown job_id (``job_service.get_work`` returns None) →
        clean not-found error."""
        job_service.get_work.return_value = None

        result = await pause_tool.coroutine("ghost-job-id")

        assert result["paused"] is False
        assert "not found" in result["error"]
        assert "ghost-job-id" in result["error"]
        manager.pause_instance_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pause_refused_while_write_paused(self, manager, job_service, pause_tool):
        """Migration-gate parity with the HTTP endpoint's 503."""
        manager.is_write_paused = True
        job_service.get_work.return_value = _make_work_record(status="processing")

        result = await pause_tool.coroutine(JOB_ID)

        assert result["paused"] is False
        assert "503" in result["error"]
        assert "WRITE_PAUSED" in result["error"]
        manager.pause_instance_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pause_skips_ids_are_passthrough(self, manager, job_service, pause_tool):
        """Idempotent re-pause: already-paused target comes back in
        ``skipped_ids`` (service classification); tool returns
        ``paused=True`` with ``paused_ids`` empty — no error."""
        job_service.get_work.return_value = _make_work_record(status="processing")
        _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)
        manager.pause_instance_cascade = AsyncMock(
            return_value={"paused_ids": [], "skipped_ids": [TARGET_INSTANCE_ID]}
        )

        result = await pause_tool.coroutine(JOB_ID)

        assert result["paused"] is True
        assert result["paused_ids"] == []
        assert result["skipped_ids"] == [TARGET_INSTANCE_ID]


# ─────────────────────────────────────────────────────────────────────────────────
# Scenario (f)–(h): job_resume
# ─────────────────────────────────────────────────────────────────────────────────


class TestJobResume:
    """``job_resume`` mirrors the FE plain resume path (HTTP endpoint at
    ``daemon/routers/instances.py:823-866``): target continuation job
    with ``message="resume", silent=False`` → cascade flip → silent
    child resumes with ``silent=True``. Same call shape as the
    ``resume_instance`` agent tool (``daemon/tools/instance.py:4662``)."""

    @pytest.mark.asyncio
    async def test_resume_active_job(self, manager, job_service, resume_tool):
        """(f) Happy path: resume an ACTIVE job (paused instance) →
        ``resume_processing_job(target, "resume", silent=False)`` runs
        FIRST, then ``resume_instance_cascade``, then silent child
        resumes for non-target children. Mirror of the HTTP handler's
        call order (FE-identical by construction)."""
        job_service.get_work.return_value = _make_work_record(status="processing")
        _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)
        # Two resumed ids — target + a child — exercises the silent
        # child lane (the brief's "applicable one precisely" — the
        # :845-area task-status transitions / graph re-trigger).
        manager.resume_instance_cascade = AsyncMock(
            return_value={
                "resumed_ids": [TARGET_INSTANCE_ID, CHILD_INSTANCE_ID],
                "skipped_ids": [],
                "target_id": TARGET_INSTANCE_ID,
            }
        )

        result = await resume_tool.coroutine(JOB_ID)

        assert result["resumed"] is True
        assert result["resumed_ids"] == [TARGET_INSTANCE_ID, CHILD_INSTANCE_ID]
        assert result["target_id"] == TARGET_INSTANCE_ID
        assert result["resume_results"][TARGET_INSTANCE_ID]["status"] == "resuming"
        assert result["resume_results"][CHILD_INSTANCE_ID]["status"] == "resuming"

        # Call ORDER and kwarg parity — the brief pins the tool to be
        # FE-identical. ``resume_processing_job`` runs FIRST on the
        # target (silent=False, message="resume" verbatim), the cascade
        # flip runs SECOND, then silent child resumes (silent=True).
        # The target continuation call shape matches the FE handler
        # at routers/instances.py:823-832; the silent child lane
        # matches routers/instances.py:855-866.
        target_call = manager.resume_processing_job.await_args_list[0]
        assert target_call.args == (TARGET_INSTANCE_ID,)
        assert target_call.kwargs == {"message": "resume", "silent": False}
        child_call = manager.resume_processing_job.await_args_list[1]
        assert child_call.args == (CHILD_INSTANCE_ID,)
        assert child_call.kwargs == {"message": "resume", "silent": True}

    @pytest.mark.asyncio
    async def test_resume_queued_job_d1_refusal(self, manager, job_service, resume_tool):
        """(g) D1 — QUEUED job (no instance_id) → clean refusal with
        the D1 actionable wording; cascade + processing-job untouched."""
        job_service.get_work.return_value = _make_work_record(
            status="pending", instance_id=None,
        )

        result = await resume_tool.coroutine(JOB_ID)

        assert result["resumed"] is False
        assert "instance" in result["error"]
        assert "instance_id" in result["error"]
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_question_pack_pending_refused(self, manager, job_service, resume_tool):
        """(h) Defect-1 guard: a pending ``question_pack`` on the target
        instance → clean refusal pointing at ``job_answer``. Mirrors
        the ``resume_instance`` tool's guard at instance.py:4677-4695:
        the standard resume path would route through
        ``answer_gate_existing_turn`` and treat the literal "resume"
        message as answer content."""
        job_service.get_work.return_value = _make_work_record(status="processing")
        pending_pack = MagicMock(name="PendingPack")
        type(pending_pack).status = property(lambda self: "pending")
        manager._question_manager.get_question_pack = MagicMock(return_value=pending_pack)
        _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)

        result = await resume_tool.coroutine(JOB_ID)

        assert result["resumed"] is False
        assert result["instance_id"] == TARGET_INSTANCE_ID
        assert "pending question" in result["error"]
        assert "job_answer" in result["error"]
        # The literal "resume" message MUST NOT have been injected.
        manager.resume_processing_job.assert_not_awaited()
        manager.resume_instance_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_unknown_job_id(self, manager, job_service, resume_tool):
        """Unknown job_id → clean not-found error; cascade + processing
        job untouched."""
        job_service.get_work.return_value = None

        result = await resume_tool.coroutine("ghost-job-id")

        assert result["resumed"] is False
        assert "not found" in result["error"]
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_refused_while_write_paused(self, manager, job_service, resume_tool):
        """Migration-gate parity with the HTTP endpoint's 503."""
        manager.is_write_paused = True

        result = await resume_tool.coroutine(JOB_ID)

        assert result["resumed"] is False
        assert "503" in result["error"]
        assert "WRITE_PAUSED" in result["error"]
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_none_processing_job_status_normalized(
        self, manager, job_service, resume_tool,
    ):
        """When ``resume_processing_job`` returns None (instance was
        IDLE / WAITING_CHILDREN) the tool normalizes to
        ``{"status": "no_active_job"}`` — mirrors the HTTP handler's
        normalization at routers/instances.py:835-840 and the
        ``resume_instance`` tool at instance.py:4721-4732. The log
        line is also pinned so production debugging is consistent
        regardless of which surface (tool vs HTTP) invoked the path."""
        job_service.get_work.return_value = _make_work_record(status="processing")
        manager.resume_processing_job = AsyncMock(return_value=None)
        _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)

        result = await resume_tool.coroutine(JOB_ID)

        assert result["resumed"] is True
        assert result["resume_results"][TARGET_INSTANCE_ID]["status"] == "no_active_job"

    @pytest.mark.asyncio
    async def test_resume_processing_job_exception_caught(
        self, manager, job_service, resume_tool,
    ):
        """Endpoint parity: a raised exception from ``resume_processing_job``
        is caught and surfaced as ``{"status": "error", "error": ...}``
        in ``resume_results[target]`` — does NOT abort the cascade flip
        or the silent child lane. Matches routers/instances.py:829-834."""
        job_service.get_work.return_value = _make_work_record(status="processing")
        manager.resume_processing_job = AsyncMock(side_effect=RuntimeError("boom"))
        _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)

        result = await resume_tool.coroutine(JOB_ID)

        assert result["resumed"] is True
        assert result["resume_results"][TARGET_INSTANCE_ID]["status"] == "error"
        assert "boom" in result["resume_results"][TARGET_INSTANCE_ID]["error"]
        # Cascade still ran — the exception is per-target, not global.
        manager.resume_instance_cascade.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────────
# Scenario (e) + (i): access control — project-scoped (same helper as
# job_messages / job_inject).
# ─────────────────────────────────────────────────────────────────────────────────


class TestJobPauseAccessControl:
    """Project-scoped access control via ``_check_job_access`` — same
    pattern as ``job_messages`` / ``job_inject``. Caller's project_id
    mismatching the job's → DENY; system-default caller → the
    global-operator tier (allow)."""

    @pytest.mark.asyncio
    async def test_pause_project_mismatch_denied(self, manager, job_service, pause_tool):
        """(e) Caller in proj-B, job in proj-A → denied, cascade untouched."""
        job_service.get_work.return_value = _make_work_record(
            status="processing", project_id="proj-A",
        )
        _wire_caller_project(manager, caller_project="proj-B")

        result = await pause_tool.coroutine(JOB_ID)

        assert "error" in result
        assert "Access denied" in result["error"]
        assert result["paused"] is False
        manager.pause_instance_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pause_system_default_caller_global_operator_allowed(
        self, manager, job_service, pause_tool,
    ):
        """Caller in the system-default project (chat-facing tier, e.g.
        Ari/Jober) manages work in ANY project — mirrors the job
        tools' global-operator carve-out at job_queue.py:900."""
        job_service.get_work.return_value = _make_work_record(
            status="processing", project_id="proj-A",
        )
        _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)

        result = await pause_tool.coroutine(JOB_ID)

        assert result["paused"] is True
        manager.pause_instance_cascade.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pause_same_project_allowed(self, manager, job_service, pause_tool):
        """Caller and job in the same project → allowed."""
        job_service.get_work.return_value = _make_work_record(
            status="processing", project_id="proj-A",
        )
        _wire_caller_project(manager, caller_project="proj-A")

        result = await pause_tool.coroutine(JOB_ID)

        assert result["paused"] is True
        manager.pause_instance_cascade.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pause_repo_error_propagates(
        self, manager, job_service, pause_tool,
    ):
        """``_check_job_access`` (job_queue.py:910) does NOT wrap the
        caller-row lookup in a defensive try/except — unlike
        ``_check_instance_project_access`` (instance.py:431) which
        returns a distinct DENY on repo errors. The job-tool pattern
        is to let the exception propagate. The brief says "reuse their
        access-control/guard patterns" — the job-tool pattern is the
        authoritative one for ``job_pause`` / ``job_resume``.

        This test pins that contract: a repo failure during the
        access check surfaces as the raised exception (the cascade
        is untouched), not as a denial dict."""
        job_service.get_work.return_value = _make_work_record(
            status="processing", project_id="proj-A",
        )
        _wire_caller_project(manager, caller_project="proj-A")
        # Override the get side_effect to raise.
        manager._instance_repository.get = MagicMock(
            side_effect=RuntimeError("simulated repo failure")
        )

        with pytest.raises(RuntimeError, match="simulated repo failure"):
            await pause_tool.coroutine(JOB_ID)
        manager.pause_instance_cascade.assert_not_awaited()


class TestJobResumeAccessControl:
    """Same project-scoped rules as ``TestJobPauseAccessControl`` for
    the resume lane."""

    @pytest.mark.asyncio
    async def test_resume_project_mismatch_denied(self, manager, job_service, resume_tool):
        """(i) Caller in proj-B, job in proj-A → denied, both facade
        calls untouched."""
        job_service.get_work.return_value = _make_work_record(
            status="processing", project_id="proj-A",
        )
        _wire_caller_project(manager, caller_project="proj-B")

        result = await resume_tool.coroutine(JOB_ID)

        assert "error" in result
        assert "Access denied" in result["error"]
        assert result["resumed"] is False
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_system_default_caller_global_operator_allowed(
        self, manager, job_service, resume_tool,
    ):
        """System-default caller → the global-operator tier."""
        job_service.get_work.return_value = _make_work_record(
            status="processing", project_id="proj-A",
        )
        _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)

        result = await resume_tool.coroutine(JOB_ID)

        assert result["resumed"] is True
        manager.resume_instance_cascade.assert_awaited_once()
        manager.resume_processing_job.assert_awaited()

    @pytest.mark.asyncio
    async def test_resume_question_pack_guard_runs_before_access_check(
        self, manager, job_service, resume_tool,
    ):
        """The Defect-1 question-pack guard fires BEFORE the access check
        in ``resume_instance`` (defensive order). For ``job_resume`` the
        SAME order applies: pending-pack refusal does NOT leak project
        information via a side channel. (Pin the ordering contract.)"""
        # If the question-pack guard fires first, the access-check mock
        # would NOT be exercised — and the result would carry
        # ``instance_id`` (the pending-pack refusal shape) rather than
        # ``{"paused": False, ...}``.
        job_service.get_work.return_value = _make_work_record(
            status="processing", project_id="proj-A",
        )
        pending_pack = MagicMock(name="PendingPack")
        type(pending_pack).status = property(lambda self: "pending")
        manager._question_manager.get_question_pack = MagicMock(return_value=pending_pack)
        # Caller mismatched project — should NOT matter for the
        # pending-pack refusal (refusal runs first), but the pin is
        # that the access check does NOT execute at all.
        _wire_caller_project(manager, caller_project="proj-B")

        result = await resume_tool.coroutine(JOB_ID)

        # Pending-pack refusal fires first.
        assert "pending question" in result["error"]
        assert "instance_id" in result  # pending-pack refusal shape
        manager.resume_instance_cascade.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────────
# Job state untouched — pinning the cascade-only contract.
# ─────────────────────────────────────────────────────────────────────────────────


class TestJobStateUntouched:
    """Brief: "Job state (admission_state/status) MUST remain UNTOUCHED"
    across job_pause/job_resume. The cascade operates on the instance
    lineage, NOT on the job row. The mocked manager does not touch any
    job-side store, so the assertion below is the contract pin: the
    tools must NEVER call any job-side mutation facade (cancel_job,
    soft_delete_job, restore_job, retry_job) — only the cascade /
    resume_processing_job facades."""

    @pytest.mark.asyncio
    async def test_pause_does_not_call_job_mutators(
        self, manager, job_service, pause_tool,
    ):
        job_service.get_work.return_value = _make_work_record(status="processing")
        _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)

        await pause_tool.coroutine(JOB_ID)

        # The only job-side call is the read (get_work). No mutator
        # ever fires.
        job_service.cancel_job.assert_not_called()
        job_service.soft_delete_job.assert_not_called()
        job_service.restore_job.assert_not_called()
        job_service.retry_job.assert_not_called()
        # And the read was called exactly once.
        assert job_service.get_work.await_count == 1

    @pytest.mark.asyncio
    async def test_resume_does_not_call_job_mutators(
        self, manager, job_service, resume_tool,
    ):
        job_service.get_work.return_value = _make_work_record(status="processing")
        _wire_caller_project(manager, caller_project=TEST_SYSTEM_PROJECT_ID)

        await resume_tool.coroutine(JOB_ID)

        job_service.cancel_job.assert_not_called()
        job_service.soft_delete_job.assert_not_called()
        job_service.restore_job.assert_not_called()
        job_service.retry_job.assert_not_called()
        assert job_service.get_work.await_count == 1


# ─────────────────────────────────────────────────────────────────────────────────
# Registration surfaces.
# ─────────────────────────────────────────────────────────────────────────────────


class TestRegistration:
    def test_tools_in_factory_surface(self, tools):
        """Both tools appear in ``create_job_tools`` output, carry the
        ``job`` category, and ship full docs (tool_help surface)."""
        pause_tool_obj = _get_tool(tools, "job_pause")
        resume_tool_obj = _get_tool(tools, "job_resume")

        for tool_obj in (pause_tool_obj, resume_tool_obj):
            assert tool_obj._tool_category == "job"
            assert getattr(tool_obj, "_full_doc_", ""), (
                f"{tool_obj.name} missing _full_doc_"
            )

    def test_names_registered_in_known_tool_names(self):
        """The static frozen-binary fallback universe knows both names —
        the bidirectional-drift test pins this the other direction."""
        assert "job_pause" in KNOWN_TOOL_NAMES
        assert "job_resume" in KNOWN_TOOL_NAMES

    def test_job_category_module_covers_the_tools(self):
        """The ``job`` category maps at ``daemon.tools.job_queue`` — so
        agents allowing the category (ari/jober) get both tools
        automatically. Leader gets them via explicit allow entries
        (its ``allow`` list does not include the ``job`` category)."""
        assert CATEGORY_MODULES["job"] == "daemon.tools.job_queue"

    def test_full_doc_documents_invariants_and_d2_d3(self):
        """Pinned invariants: the ``_full_doc_`` must surface (D1) the
        QUEUED-no-instance refusal shape, (D2) the STUCK_AWAITING_ANSWER
        wedge-guard note, and (D3) the resume message="resume" injection
        contract. Future readers rely on these phrases to reason about
        the tool's behavior without re-reading the implementation."""
        manager_obj = _make_pause_resume_manager()
        job_service_obj = AsyncMock()
        job_service_obj.get_work.return_value = _make_work_record(
            status="processing",
        )
        _wire_caller_project(manager_obj, caller_project=TEST_SYSTEM_PROJECT_ID)
        tools_obj = _build_tools(manager_obj, job_service_obj)

        pause_full_doc = " ".join(_get_tool(tools_obj, "job_pause")._full_doc_.split())
        for phrase in (
            "queue_pause",
            "instance_id",
            "skipped_ids",
            "stuck_awaiting_answer",
            "wedge",
        ):
            assert phrase in pause_full_doc, (
                f"job_pause._full_doc_ must document {phrase!r}; "
                f"got: {pause_full_doc[:300]!r}"
            )

        resume_full_doc = " ".join(_get_tool(tools_obj, "job_resume")._full_doc_.split())
        for phrase in (
            "FE-identical",
            "resume_processing_job",
            "answer_gate_existing_turn",
            "job_answer",
            "no_active_job",
        ):
            assert phrase in resume_full_doc, (
                f"job_resume._full_doc_ must document {phrase!r}; "
                f"got: {resume_full_doc[:300]!r}"
            )
