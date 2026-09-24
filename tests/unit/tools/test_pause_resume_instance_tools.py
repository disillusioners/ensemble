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
  f. resume waiting_children       → TestResumeInstance::test_resume_waiting_children_parent_silent_resume
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
    async def test_resume_waiting_children_parent_silent_resume(self, manager, resume_tool):
        """(f) A parent resumed on the silent lane returns the service's
        "silent_resume" status (internal_child_noop §9.3,
        manager.py:10021) and non-target children resume with silent=True.

        Note (B2, 2026-09-11): a WAITING_CHILDREN parent + silent=True with
        the lifecycle service wired enqueues a durable wake and returns
        "wake_enqueued" instead — the tool NEVER re-interprets statuses;
        whatever the service returns lands in resume_results verbatim."""
        manager.resume_processing_job = AsyncMock(
            side_effect=lambda iid, message, silent: {
                "instance_id": iid,
                "job_id": None,
                "message_id": None,
                "status": "silent_resume",
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

        assert result["resume_results"][TARGET_ID]["status"] == "silent_resume"
        assert result["resume_results"][CHILD_ID]["status"] == "silent_resume"
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
