"""Unit tests for the agent-facing ``pause_instance`` / ``resume_instance`` tools.

Tests the two tools added to ``create_instance_tools()`` in
``daemon/tools/instance.py`` (agent-pause-resume-tools feature). The tools
are THIN WRAPPERS over the same service layer the HTTP endpoints use
(``manager.pause_instance_cascade`` / ``manager.resume_instance_cascade`` /
``manager.resume_processing_job``) — these tests pin the wrapper contract:

  * Result passthrough of the service's structured shapes
    (``paused_ids`` / ``resumed_ids`` / ``skipped_ids`` + statuses).
  * Default-kwargs parity with the HTTP handlers (no invented flags;
    ``suspension_reason`` threaded on pause).
  * Resume call-shape parity with POST /resume: target continuation job
    (silent=False) → cascade flip → silent child resumes (silent=True).
  * Project-scoped access control (mismatch denies; unscoped allows;
    system-default caller is the global-operator tier).
  * Registration surfaces (factory list, category, KNOWN_TOOL_NAMES).

Scenario → test map (dispatch spec a–g):
  a. pause running instance        → TestPauseInstance::test_pause_running_instance
  b. pause waiting parent (whole   → TestPauseInstance::test_pause_waiting_parent_pauses_whole_lineage
     lineage)
  c. cascade to children/          → TestPauseInstance::test_pause_cascade_to_descendants
     descendants
  d. idempotent re-pause           → TestPauseInstance::test_repause_already_paused_is_skipped
  e. resume running-turn           → TestResumeInstance::test_resume_running_turn_spins_job_resuming
  f. resume silent lane            → TestResumeInstance::test_resume_silent_lane_status_passthrough
  g. access-control denial         → TestPauseResumeAccessControl::test_pause_project_mismatch_denied
                                    TestPauseResumeAccessControl::test_resume_project_mismatch_denied
                                    TestPauseResumeAccessControl::test_unscoped_*_allowed

The fixture / mock style mirrors ``tests/unit/tools/test_job_visibility_tools.py``
and reuses the shared factory-helper patch stack from
``tests.helpers.send_message_fixtures``.

SAFETY FENCE: pure unit tests — mocks only, the daemon is NEVER booted and
no DB is ever touched, so no ambient ``POSTGRES_*`` can leak anywhere.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.helpers.send_message_fixtures import (
    patch_heavy_helpers as _patch_heavy_helpers,
    make_send_message_manager,
)

from daemon.tools._tool_registry import CATEGORY_MODULES, KNOWN_TOOL_NAMES


# Mirrors tests/unit/tools/test_job_visibility_tools.py — the deterministic
# uuid5 the repo-wide autouse conftest fixture seeds into
# ``constants.SYSTEM_DEFAULT_PROJECT_ID`` for every test.
TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"

CALLER_ID = "caller-id"
TARGET_ID = "target-instance"
CHILD_ID = "child-instance"


# ── Helpers ──────────────────────────────────────────────────────────────────────


def _make_instance_row(instance_id: str, *, project_id: str | None = None) -> MagicMock:
    """Instance-shaped mock for ``manager._instance_repository.get`` (the
    row shape ``_get_instance_project_id`` reads ``.project_id`` from)."""
    row = MagicMock(name=f"InstanceRow[{instance_id}]")
    row.instance_id = instance_id
    row.project_id = project_id
    row.status = "running"
    return row


def _wire_projects(manager: MagicMock, *, caller_project: str | None, target_project: str | None) -> None:
    """Wire ``manager._instance_repository.get`` with caller/target rows."""
    rows = {
        CALLER_ID: _make_instance_row(CALLER_ID, project_id=caller_project),
        TARGET_ID: _make_instance_row(TARGET_ID, project_id=target_project),
        CHILD_ID: _make_instance_row(CHILD_ID, project_id=target_project),
    }
    manager._instance_repository.get = MagicMock(side_effect=lambda iid: rows.get(iid))


def _make_pause_resume_manager() -> MagicMock:
    """Manager mock wired for the pause/resume tool closures.

    Extends the shared ``make_send_message_manager`` baseline (existence
    check via ``get_instance``) with the three async facade methods the
    tools wrap. ``is_write_paused`` MUST be an explicit False — a bare
    MagicMock attribute is truthy and would trip the migration gate.
    ``_question_manager.get_question_pack`` is wired to return ``None``
    by default (no pending question pack) so the resume tool's Defect-1
    guard falls through on the existing happy-path tests; pending-pack
    tests override the mock on a per-test basis.
    """
    manager = make_send_message_manager(status="running")
    manager.is_write_paused = False
    manager.pause_instance_cascade = AsyncMock(
        return_value={"paused_ids": [TARGET_ID], "skipped_ids": []}
    )
    manager.resume_instance_cascade = AsyncMock(
        return_value={
            "resumed_ids": [TARGET_ID],
            "skipped_ids": [],
            "target_id": TARGET_ID,
        }
    )
    manager.resume_processing_job = AsyncMock(
        return_value={
            "instance_id": TARGET_ID,
            "job_id": "work-1",
            "message_id": "msg-1",
            "status": "resuming",
        }
    )
    manager._question_manager = MagicMock()
    manager._question_manager.get_question_pack = MagicMock(return_value=None)
    return manager


def _build_tools(manager: MagicMock, *, current_instance_id: str = CALLER_ID) -> list:
    """Build the real instance-tool surface with heavy helpers patched out.

    Same pattern as ``tests.helpers.send_message_fixtures.get_send_message_tool``
    but returns the whole list so both new tools can be extracted by name.
    """
    from daemon.tools.instance import create_instance_tools

    patches = _patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        return create_instance_tools(manager, current_instance_id, "developer")
    finally:
        for p in reversed(patches):
            p.stop()


def _get_tool(tools: list, name: str):
    tool = next((t for t in tools if getattr(t, "name", None) == name), None)
    assert tool is not None, f"{name} tool not found; got {[t.name for t in tools]}"
    return tool


@pytest.fixture
def manager():
    return _make_pause_resume_manager()


@pytest.fixture
def tools(manager):
    return _build_tools(manager)


@pytest.fixture
def pause_tool(tools):
    return _get_tool(tools, "pause_instance")


@pytest.fixture
def resume_tool(tools):
    return _get_tool(tools, "resume_instance")


# ─────────────────────────────────────────────────────────────────────────────────
# Scenario (a)–(d): pause_instance
# ─────────────────────────────────────────────────────────────────────────────────


class TestPauseInstance:
    """``pause_instance`` wraps ``manager.pause_instance_cascade`` with the
    HTTP handler's default behavior (routers/instances.py:653)."""

    @pytest.mark.asyncio
    async def test_pause_running_instance(self, manager, pause_tool):
        """(a) Pausing a running instance pauses it and returns the service's
        structured result (paused_ids + skipped_ids), reason omitted →
        ``suspension_reason=None`` (the service's paused_external default)."""
        result = await pause_tool.coroutine(TARGET_ID)

        assert result == {"paused": True, "paused_ids": [TARGET_ID], "skipped_ids": []}
        manager.pause_instance_cascade.assert_awaited_once_with(
            TARGET_ID, suspension_reason=None
        )

    @pytest.mark.asyncio
    async def test_pause_waiting_parent_pauses_whole_lineage(self, manager, pause_tool):
        """(b) Pausing a child whose parent is waiting pauses the WHOLE tree:
        the service walks up to the root (cascade_to_root=True default) and
        the tool passes the full lineage through verbatim. The tool must NOT
        override the cascade default — kwargs parity with POST /pause."""
        lineage = ["root-instance", "waiting-parent", TARGET_ID]
        manager.pause_instance_cascade = AsyncMock(
            return_value={"paused_ids": lineage, "skipped_ids": []}
        )

        result = await pause_tool.coroutine(TARGET_ID, reason="pre-restart choreography")

        assert result["paused"] is True
        assert result["paused_ids"] == lineage  # ancestors included, order preserved
        # reason threads through to the service as suspension_reason —
        # the ONLY extra kwarg the tool ever passes.
        manager.pause_instance_cascade.assert_awaited_once_with(
            TARGET_ID, suspension_reason="pre-restart choreography"
        )

    @pytest.mark.asyncio
    async def test_pause_cascade_to_descendants(self, manager, pause_tool):
        """(c) Cascade covers descendants: every paused descendant ID from
        the service lands in the tool result unchanged."""
        manager.pause_instance_cascade = AsyncMock(
            return_value={
                "paused_ids": [TARGET_ID, CHILD_ID, "grandchild-instance"],
                "skipped_ids": [],
            }
        )

        result = await pause_tool.coroutine(TARGET_ID)

        assert result["paused_ids"] == [TARGET_ID, CHILD_ID, "grandchild-instance"]
        manager.resume_instance_cascade.assert_not_awaited()  # pause never resumes

    @pytest.mark.asyncio
    async def test_repause_already_paused_is_skipped(self, manager, pause_tool):
        """(d) Idempotent re-pause: an already-paused target comes back in
        skipped_ids (service classification), paused_ids empty — no error."""
        manager.pause_instance_cascade = AsyncMock(
            return_value={"paused_ids": [], "skipped_ids": [TARGET_ID]}
        )

        result = await pause_tool.coroutine(TARGET_ID)

        assert result == {"paused": True, "paused_ids": [], "skipped_ids": [TARGET_ID]}

    @pytest.mark.asyncio
    async def test_pause_unknown_instance_returns_error(self, manager, pause_tool):
        """Existence parity with the endpoint's 404: KeyError → clean error,
        service never called."""
        async def _raise_key_error(instance_id):
            raise KeyError(instance_id)

        manager.get_instance = _raise_key_error

        result = await pause_tool.coroutine("ghost-instance")

        assert result["paused"] is False
        assert result["error"] == "Instance not found: ghost-instance"
        manager.pause_instance_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pause_refused_while_write_paused(self, manager, pause_tool):
        """Migration-gate parity with the endpoint's 503."""
        manager.is_write_paused = True

        result = await pause_tool.coroutine(TARGET_ID)

        assert result["paused"] is False
        assert "database migration" in result["error"]
        manager.pause_instance_cascade.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────────
# Scenario (e)–(f): resume_instance
# ─────────────────────────────────────────────────────────────────────────────────


class TestResumeInstance:
    """``resume_instance`` mirrors the POST /resume call shape
    (routers/instances.py:684): target continuation job (silent=False) →
    cascade flip → silent child resumes (silent=True)."""

    @pytest.mark.asyncio
    async def test_resume_running_turn_spins_job_resuming(self, manager, resume_tool):
        """(e) Resume of a running-turn instance spins a continuation message
        job — status "resuming" passed through verbatim in resume_results."""
        manager.resume_instance_cascade = AsyncMock(
            return_value={"resumed_ids": [TARGET_ID], "skipped_ids": [], "target_id": TARGET_ID}
        )

        result = await resume_tool.coroutine(TARGET_ID)

        assert result["resumed"] is True
        assert result["resumed_ids"] == [TARGET_ID]
        assert result["target_id"] == TARGET_ID
        assert result["resume_results"][TARGET_ID]["status"] == "resuming"
        assert result["resume_results"][TARGET_ID]["job_id"] == "work-1"
        # Target continuation: the endpoint's exact kwargs (default
        # "resume" message, NOT silent).
        manager.resume_processing_job.assert_awaited_once_with(
            TARGET_ID, message="resume", silent=False
        )
        manager.resume_instance_cascade.assert_awaited_once_with(TARGET_ID)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "parent_lane_status",
        [
            "silent_resume",  # non-WC parent — silent checkpoint continuation
            "wake_enqueued",  # WAITING_CHILDREN parent — durable wake turn (B2)
        ],
    )
    async def test_resume_silent_lane_status_passthrough(
        self, manager, resume_tool, parent_lane_status
    ):
        """(f) A parent resumed on the silent lane returns the service's
        status VERBATIM (internal_child_noop §9.3, manager.py:10021) and
        non-target children resume with silent=True.

        Parametrized over both silent-lane parent statuses (B2,
        2026-09-11): "silent_resume" for the non-WC parent checkpoint
        continuation, "wake_enqueued" for a WAITING_CHILDREN parent whose
        silent wake was enqueued durably — the tool NEVER re-interprets
        statuses; whatever the service returns lands in resume_results
        verbatim."""
        manager.resume_processing_job = AsyncMock(
            side_effect=lambda iid, message, silent: {
                "instance_id": iid,
                "job_id": None,
                "message_id": None,
                "status": parent_lane_status,
            }
        )
        manager.resume_instance_cascade = AsyncMock(
            return_value={
                "resumed_ids": [TARGET_ID, CHILD_ID],
                "skipped_ids": [],
                "target_id": TARGET_ID,
            }
        )

        result = await resume_tool.coroutine(TARGET_ID, reason="daemon is back")

        assert result["resume_results"][TARGET_ID]["status"] == parent_lane_status
        assert result["resume_results"][CHILD_ID]["status"] == parent_lane_status
        # Children resume silently from checkpoint — no injected message.
        manager.resume_processing_job.assert_any_await(
            CHILD_ID, message="resume", silent=True
        )
        # Audit reason echoed in the result.
        assert result["reason"] == "daemon is back"

    @pytest.mark.asyncio
    async def test_resume_target_without_handle_reports_no_active_job(self, manager, resume_tool):
        """``resume_processing_job`` returning None (invalid_or_missing_handle)
        surfaces as {"status": "no_active_job"} — endpoint parity."""
        manager.resume_processing_job = AsyncMock(return_value=None)

        result = await resume_tool.coroutine(TARGET_ID)

        assert result["resume_results"][TARGET_ID] == {"status": "no_active_job"}
        assert result["resumed"] is True  # the DB-only cascade still flipped

    @pytest.mark.asyncio
    async def test_resume_target_continuation_failure_degrades_to_error_status(self, manager, resume_tool):
        """Endpoint parity: a failing target continuation is caught and
        degraded to {"status": "error", ...} — the cascade still runs."""
        manager.resume_processing_job = AsyncMock(
            side_effect=RuntimeError("graph task boom")
        )

        result = await resume_tool.coroutine(TARGET_ID)

        assert result["resume_results"][TARGET_ID]["status"] == "error"
        assert "graph task boom" in result["resume_results"][TARGET_ID]["error"]
        manager.resume_instance_cascade.assert_awaited_once_with(TARGET_ID)

    @pytest.mark.asyncio
    async def test_resume_unknown_instance_returns_error(self, manager, resume_tool):
        async def _raise_key_error(instance_id):
            raise KeyError(instance_id)

        manager.get_instance = _raise_key_error

        result = await resume_tool.coroutine("ghost-instance")

        assert result["resumed"] is False
        assert result["error"] == "Instance not found: ghost-instance"
        manager.resume_instance_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_refused_while_write_paused(self, manager, resume_tool):
        manager.is_write_paused = True

        result = await resume_tool.coroutine(TARGET_ID)

        assert result["resumed"] is False
        assert "database migration" in result["error"]
        manager.resume_instance_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_refused_when_question_pack_pending(
        self, manager, resume_tool
    ):
        """Review finding #4 (Defect-1 guard): when a question pack is
        still pending for the target, resume_instance refuses BEFORE any
        service call — ``resume_processing_job`` and
        ``resume_instance_cascade`` must NEVER be invoked, otherwise the
        literal "resume" message would route through
        ``answer_gate_existing_turn`` as answer content (Defect-1).

        Mirrors the HTTP endpoint's gate-supersession check shape
        (routers/instances.py:733): pending pack + status == "pending" →
        error dict returned, no service work scheduled.
        """
        pending_pack = MagicMock(name="QuestionPack[pending]")
        pending_pack.status = "pending"
        manager._question_manager.get_question_pack = MagicMock(
            return_value=pending_pack
        )

        result = await resume_tool.coroutine(TARGET_ID)

        assert result == {
            "error": (
                "instance has a pending question; answer it via "
                "job_answer(work_id) instead of resume_instance"
            ),
            "resumed": False,
            "instance_id": TARGET_ID,
        }
        # CRITICAL: nothing downstream was called — guard fires BEFORE
        # any service. This is what prevents Defect-1 (the "resume"
        # message reaching answer_gate_existing_turn).
        manager.resume_processing_job.assert_not_awaited()
        manager.resume_instance_cascade.assert_not_awaited()
        # And the question-pack check itself was made with the right id.
        manager._question_manager.get_question_pack.assert_called_once_with(
            TARGET_ID
        )

    @pytest.mark.asyncio
    async def test_resume_proceeds_when_no_pending_question(
        self, manager, resume_tool
    ):
        """Happy-path companion to the guard test: explicit
        ``get_question_pack`` returning ``None`` (no pack at all) lets the
        tool fall through to the normal resume flow.
        """
        # Factory default already returns None, but set it explicitly so
        # the test pins the contract independently of the factory.
        manager._question_manager.get_question_pack = MagicMock(return_value=None)

        result = await resume_tool.coroutine(TARGET_ID)

        assert result["resumed"] is True
        assert result["resume_results"][TARGET_ID]["status"] == "resuming"
        manager._question_manager.get_question_pack.assert_called_once_with(
            TARGET_ID
        )
        manager.resume_processing_job.assert_awaited_once_with(
            TARGET_ID, message="resume", silent=False
        )
        manager.resume_instance_cascade.assert_awaited_once_with(TARGET_ID)

    @pytest.mark.asyncio
    async def test_resume_guard_fails_open_on_introspection_error(
        self, manager, resume_tool, caplog
    ):
        """Fail-open contract (review finding #4): if the question-pack
        introspection itself raises, the guard must NOT block resume — a
        broken introspection surface must not wedge restart recovery. A
        WARNING is logged so the failure is observable.
        """
        manager._question_manager.get_question_pack = MagicMock(
            side_effect=RuntimeError("question-manager surface broken")
        )

        # caplog captures WARNING+ on the root logger by default; we just
        # assert the warning landed (the level config is irrelevant for
        # the contract — we want a WARNING-level log, not a CRITICAL).
        result = await resume_tool.coroutine(TARGET_ID)

        # Normal resume flow ran despite the introspection error.
        assert result["resumed"] is True
        assert result["resume_results"][TARGET_ID]["status"] == "resuming"
        manager.resume_processing_job.assert_awaited_once_with(
            TARGET_ID, message="resume", silent=False
        )
        manager.resume_instance_cascade.assert_awaited_once_with(TARGET_ID)
        # Warning was emitted (observability for the fail-open).
        assert any(
            "question-pack introspection failed" in rec.message
            for rec in caplog.records
        ), f"expected warn log; got {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "passthrough_status, payload_field",
        [
            ("wake_enqueued", "job_id"),
            ("wake_failed", "refusal_kind"),
            ("already_resuming", "job_id"),
            ("deferred_report_recovery", "recovery_count"),
        ],
    )
    async def test_resume_status_passthrough_verbatim(
        self, manager, resume_tool, passthrough_status, payload_field
    ):
        """S3 FIX: pin VERBATIM status pass-through for every status the
        ``resume_processing_job`` service can return, including the three
        confirmed by S1 (manager.py:9984 wake_enqueued / :10001
        wake_failed / :10122 already_resuming / :9917
        deferred_report_recovery). The tool MUST NOT re-interpret,
        re-map, or fall-through-to-default — whatever status string the
        service returns lands in ``resume_results[<id>]["status"]``
        byte-identical. Failure mode being pinned: a future refactor
        that "normalizes" the dict or coalesces an unknown status to
        ``no_active_job`` would silently break the FE / agents that
        branch on these strings (notably ``wake_enqueued`` and
        ``wake_failed`` — incident 84563a03 lineage).

        Each param uses a different payload shape mirroring the real
        service:
          * ``wake_enqueued`` / ``already_resuming`` — service carries
            ``job_id`` (the in-flight / dedup'd handle).
          * ``wake_failed`` — service carries ``error`` + ``refusal_kind``
            (the loud-refusal contract).
          * ``deferred_report_recovery`` — service carries
            ``recovery_count`` (the router-recovered row count).
        The test only asserts the status string and presence of the
        service-specific field; it does NOT assert equality of the
        whole dict (verbatim means the *status* is unchanged — the
        other fields legitimately differ per status).
        """
        # Build a status-specific payload mirroring what
        # ``resume_processing_job`` actually returns for each branch.
        if passthrough_status == "wake_enqueued":
            service_payload = {
                "instance_id": TARGET_ID,
                "job_id": "wake-job-1",
                "message_id": "wake-msg-1",
                "status": "wake_enqueued",
            }
        elif passthrough_status == "wake_failed":
            service_payload = {
                "instance_id": TARGET_ID,
                "job_id": None,
                "message_id": None,
                "status": "wake_failed",
                "error": "WC parent — wake enqueue refused",
                "refusal_kind": "wc_wake_failed",
            }
        elif passthrough_status == "already_resuming":
            service_payload = {
                "instance_id": TARGET_ID,
                "job_id": "in-flight-job-1",
                "message_id": None,
                "status": "already_resuming",
            }
        elif passthrough_status == "deferred_report_recovery":
            service_payload = {
                "instance_id": TARGET_ID,
                "job_id": None,
                "message_id": None,
                "status": "deferred_report_recovery",
                "recovery_count": 2,
            }
        else:  # pragma: no cover — unreachable, parametrize exhausts the list
            raise AssertionError(f"unhandled status {passthrough_status}")

        manager.resume_processing_job = AsyncMock(return_value=service_payload)
        manager.resume_instance_cascade = AsyncMock(
            return_value={
                "resumed_ids": [TARGET_ID],
                "skipped_ids": [],
                "target_id": TARGET_ID,
            }
        )

        result = await resume_tool.coroutine(TARGET_ID)

        # The verbatim pass-through contract: the status string the
        # service returned is exactly the string in resume_results —
        # no reinterpretation, no coalesce-to-default.
        assert result["resume_results"][TARGET_ID]["status"] == passthrough_status
        # Cascade still ran (DB-only flip is independent of the
        # continuation branch the service classified into).
        assert result["resumed"] is True
        assert result["target_id"] == TARGET_ID
        # The service-specific field landed too — pin the payload wasn't
        # dropped or filtered (distinct from the silent-lane test, which
        # pins the parent/child call shape).
        assert payload_field in result["resume_results"][TARGET_ID], (
            f"service-specific field {payload_field!r} missing from "
            f"resume_results[{TARGET_ID!r}] for status {passthrough_status!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────────
# Scenario (g): access control — project-scoped (same pattern as job_messages /
# job_inject; helper mirrors _check_job_access at job_queue.py:884).
# ─────────────────────────────────────────────────────────────────────────────────


class TestPauseResumeAccessControl:
    """Target instance's project_id mismatching the caller's → DENY;
    unscoped (either side None) → allow; system-default caller → the
    global-operator tier (allow)."""

    @pytest.mark.asyncio
    async def test_pause_project_mismatch_denied(self, manager, pause_tool):
        """(g) Caller in proj-B, target in proj-A → denied, service untouched."""
        _wire_projects(manager, caller_project="proj-B", target_project="proj-A")

        result = await pause_tool.coroutine(TARGET_ID)

        assert result == {
            "error": "Access denied: instance does not belong to caller's project"
        }
        manager.pause_instance_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_project_mismatch_denied(self, manager, resume_tool):
        _wire_projects(manager, caller_project="proj-B", target_project="proj-A")

        result = await resume_tool.coroutine(TARGET_ID)

        assert result == {
            "error": "Access denied: instance does not belong to caller's project"
        }
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unscoped_target_allowed(self, manager, pause_tool):
        """Legacy/unscoped target row (project_id None) → allowed (job-tool
        backward-compat parity)."""
        _wire_projects(manager, caller_project="proj-B", target_project=None)

        result = await pause_tool.coroutine(TARGET_ID)

        assert result["paused"] is True
        manager.pause_instance_cascade.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unscoped_caller_allowed(self, manager, resume_tool):
        """Caller with no project context → allowed."""
        _wire_projects(manager, caller_project=None, target_project="proj-A")

        result = await resume_tool.coroutine(TARGET_ID)

        assert result["resumed"] is True
        manager.resume_instance_cascade.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_same_project_allowed(self, manager, pause_tool):
        _wire_projects(manager, caller_project="proj-A", target_project="proj-A")

        result = await pause_tool.coroutine(TARGET_ID)

        assert result["paused"] is True

    @pytest.mark.asyncio
    async def test_system_default_caller_global_operator_allowed(self, manager, resume_tool):
        """Caller in the system-default project (chat-facing tier, e.g. Ari)
        manages work in ANY project — mirrors the job tools' global-operator
        carve-out (job_queue.py:900)."""
        _wire_projects(manager, caller_project=TEST_SYSTEM_PROJECT_ID, target_project="proj-A")

        result = await resume_tool.coroutine(TARGET_ID)

        assert result["resumed"] is True
        manager.resume_instance_cascade.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_anonymous_caller_allowed(self, manager, pause_tool):
        """No current_instance_id (anonymous closure) → allowed (fail-open
        parity with _check_job_access's first guard)."""
        tools = _build_tools(manager, current_instance_id="")
        pause_tool = _get_tool(tools, "pause_instance")

        result = await pause_tool.coroutine(TARGET_ID)

        assert result["paused"] is True

    @pytest.mark.asyncio
    async def test_access_check_denies_on_repo_error(self, manager, pause_tool):
        """W1 FIX: an unexpected exception from ``_instance_repository.get``
        MUST flip the access check to DENY — a transient repo error must
        never silently grant access on the authz path. The denial message
        is distinct from the project-mismatch denial so the cause is
        observable in production logs.

        Pins the fail-closed contract: even with project_ids that would
        normally ALLOW (same project), a raised exception in the project
        lookup produces a denial dict and the service never runs.
        """
        _wire_projects(manager, caller_project="proj-A", target_project="proj-A")

        # Force the project lookup to raise (simulates a transient DB /
        # repository surface error). Both the caller and target lookups
        # raise — the wrapper catches the FIRST one and returns DENY.
        manager._instance_repository.get = MagicMock(
            side_effect=RuntimeError("simulated repo failure")
        )

        result = await pause_tool.coroutine(TARGET_ID)

        # Distinct-from-mismatch message so production logs can tell the
        # two denial classes apart.
        assert result == {
            "error": "Access denied: access check failed (project lookup error)"
        }
        # The service was NEVER called — the DENY short-circuits before
        # any work is scheduled.
        manager.pause_instance_cascade.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────────
# Registration surfaces — mirrors test_job_visibility_tools.py's smoke tests.
# ─────────────────────────────────────────────────────────────────────────────────


class TestRegistration:
    def test_tools_in_factory_surface(self, tools):
        """Both tools appear in ``create_instance_tools`` output, carry the
        ``instance`` category, and ship full docs (tool_help surface)."""
        pause_tool = _get_tool(tools, "pause_instance")
        resume_tool = _get_tool(tools, "resume_instance")

        for tool in (pause_tool, resume_tool):
            assert tool._tool_category == "instance"
            assert getattr(tool, "_full_doc_", ""), f"{tool.name} missing _full_doc_"

    def test_names_registered_in_known_tool_names(self):
        """The static frozen-binary fallback universe knows both names —
        the bidirectional-drift test pins this the other direction."""
        assert "pause_instance" in KNOWN_TOOL_NAMES
        assert "resume_instance" in KNOWN_TOOL_NAMES

    def test_instance_category_module_covers_the_tools(self):
        """The ``instance`` category maps at daemon.tools.instance, so
        agents allowing the category (leader/developer) get both tools."""
        assert CATEGORY_MODULES["instance"][0] == "daemon.tools.instance"

    def test_full_doc_documents_invariants_and_service_ownership(self):
        """The deliverable's invariant-comment requirement: the _full_doc_
        must tell future readers the semantics live in the service and
        cover the four pinned behaviors (cascade lineage, idempotent
        skip, "resuming" job spin, verbatim statuses)."""
        manager = _make_pause_resume_manager()
        tools = _build_tools(manager)

        resume_full_doc = " ".join(_get_tool(tools, "resume_instance")._full_doc_.split())
        for phrase in (
            "resuming",
            "silent_resume",
            "no_active_job",
            "VERBATIM",
            "skipped",
        ):
            assert phrase in resume_full_doc, (
                f"resume_instance._full_doc_ must document {phrase!r}; "
                f"got: {resume_full_doc[:300]!r}"
            )

        pause_full_doc = " ".join(_get_tool(tools, "pause_instance")._full_doc_.split())
        for phrase in (
            "lineage",
            "checkpointed",
            "skipped_ids",
            "resume_instance",
        ):
            assert phrase in pause_full_doc, (
                f"pause_instance._full_doc_ must document {phrase!r}; "
                f"got: {pause_full_doc[:300]!r}"
            )
