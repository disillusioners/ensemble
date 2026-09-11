"""C1 RESOLVED (2026-09-11) — Error lane corrective multi-turn emit.

The pre-C1 path on the child-ERROR lane fired ONLY the
task-keyed bus emit:

    await _child_reports_svc._emit_terminal_via_bus(
        task_id=<child_task_id>,
        status="error",
        ...
    )

This is correct for SINGLE-turn children (one task in, one terminal
emit) but INCORRECT for MULTI-turn children (Wanderer-class — the
parent registered its watcher on the child's FIRST ``process_message``
task but the child reaches its terminal graph turn on a LATER
``PROCESS_REPORT`` task). The task-keyed emit is a no-op for
multi-turn children — the parent's PENDING watcher is stranded and the
parent stays in ``waiting_children`` forever.

C1 closes that gap by ALSO calling the corrective
``_emit_terminal_for_child_instance_via_bus`` helper, which fires
watchers by ``(target_instance_id, metadata.child_id)`` instance pair
(matching the watcher the parent's ``send_message`` registered,
regardless of which task the child is on at the terminal turn). The
exact same corrective emit the ``regular_child_completed`` and
``child_still_running_defer`` outcomes in
``child_reports._dispatch_post_commit_side_effects`` use (lines ~3711
and ~3933).

Exactly-once is preserved by the bus's underlying
``transition_state`` guarded ``WHERE state = 'PENDING'`` Core UPDATE —
the single-turn case (where the task-keyed emit already fired the
watcher) returns ``rowcount == 0`` here, so the corrective emit is a
safe no-op. No new env flag (HARD POLICY: bugfixes are NOT
user-togglable).

Covered:
    1. The corrective instance-pair emit fires AFTER the task-keyed
       emit on the error lane — both calls land on the
       ``_child_reports_service``.
    2. The corrective emit passes the right ``parent_instance_id`` /
       ``child_instance_id`` / ``status="error"`` triple.
    3. The defensive-fallback path (no ``_child_reports_service``
       wired) ALSO fires the corrective emit, calling the bus's
       ``emit_terminal_for_child_instance`` primitive directly.
    4. The task-keyed emit failure does NOT prevent the corrective
       emit from running (defensive ordering — each call wrapped in
       its own try/except).
    5. The corrective emit failure does NOT propagate as an
       exception — the error lane is best-effort after the DB-sync
       helper has committed the parent status.

Census stays at 23/1/0 — C1 calls an existing primitive
(``_emit_terminal_for_child_instance_via_bus`` /
``bus.emit_terminal_for_child_instance``). No new
admission_state_writer / JobItem creator / work_id mint site.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.repositories.instance.models import InstanceStatus
from daemon.services.error_reporting import ErrorReportingService


CHILD_ID = "child-c1-001"
PARENT_ID = "parent-c1-001"
MESSAGE_ID = "msg-c1-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stub_session() -> MagicMock:
    """Build the mock session yielded by the patched WriteGuardSession.

    The session's ``get`` returns the child row (with parent_id set)
    and the parent row (still RUNNING; bus reports zero pending so
    the cascade is skipped and the flow reaches the bus hook + enqueue).
    """
    child = MagicMock(name="child_instance")
    child.instance_id = CHILD_ID
    child.agent_id = "tester"
    child.parent_id = PARENT_ID
    child.status = InstanceStatus.RUNNING.value
    child.instance_metadata = {}

    parent = MagicMock(name="parent_instance")
    parent.instance_id = PARENT_ID
    parent.agent_id = "leader"
    parent.parent_id = None
    parent.status = InstanceStatus.RUNNING.value
    parent.version = 1

    session = MagicMock(name="session")
    session.get = MagicMock(
        side_effect=lambda cls, iid: {
            CHILD_ID: child,
            PARENT_ID: parent,
        }.get(iid)
    )
    session.execute = MagicMock(return_value=MagicMock(name="exec_result"))
    session.expire = MagicMock()
    session.commit = MagicMock()
    session.add = MagicMock()
    return session


def _make_manager(
    *,
    with_child_reports_service: bool = True,
) -> MagicMock:
    """Build a manager mock wired for the bus hook path.

    When ``with_child_reports_service`` is True, exposes an
    ``_child_reports_service`` attribute with both
    ``_emit_terminal_via_bus`` and
    ``_emit_terminal_for_child_instance_via_bus`` as AsyncMocks — the
    C1 fix path. When False, leaves the attribute missing — the
    defensive-fallback path that calls the bus primitives directly.
    """
    manager = MagicMock(name="InstanceManager")

    child_meta = MagicMock(name="child_meta")
    child_meta.parent_id = PARENT_ID
    child_meta.agent_name = "tester"
    child_meta.agent_dir = "/tmp/agents/tester"
    manager._instance_repository.get = MagicMock(return_value=child_meta)
    manager._queue_repository.list = MagicMock(return_value=[])

    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="report-msg-0001")
    )
    manager._live_hub = None
    manager._events_service = None

    # _task_repo with get_by_message returning a real-looking task row
    # (so the error lane's _child_task_err lookup succeeds and the
    # task-keyed emit fires with a non-None task id).
    child_task = MagicMock(name="child_task")
    child_task.id = 4242
    manager._task_repo = MagicMock(name="_task_repo")
    manager._task_repo.get_by_message = MagicMock(return_value=child_task)

    if with_child_reports_service:
        svc = MagicMock(name="_child_reports_service")
        svc._emit_terminal_via_bus = AsyncMock(return_value=[])
        svc._emit_terminal_for_child_instance_via_bus = AsyncMock(
            return_value=[]
        )
        manager._child_reports_service = svc
    else:
        # No child_reports_service — defensive-fallback path.
        manager._child_reports_service = None

    return manager


def _make_stub_bus() -> MagicMock:
    """Stub DependencyBus — only the two emit primitives C1 uses."""
    bus = MagicMock(name="DependencyBus")
    bus.count_pending_for_target_sync = MagicMock(return_value=0)
    bus.emit_terminal = AsyncMock(return_value=[])
    bus.emit_terminal_for_child_instance = AsyncMock(return_value=[])
    return bus


async def _drive_send_error_report(
    *,
    with_child_reports_service: bool = True,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Drive the real ``_send_error_report`` and return the mocks.

    Returns:
        (manager, stub_bus, ...) where ``manager`` is the InstanceManager
        mock and ``stub_bus`` is the patched DependencyBus. The third
        return slot is the bus singleton (used in fallback-path
        assertions — same object as ``stub_bus``).
    """
    manager = _make_manager(
        with_child_reports_service=with_child_reports_service,
    )
    service = ErrorReportingService(
        manager=manager, events_service=None
    )

    stub_session = _make_stub_session()
    wgs = MagicMock(name="WriteGuardSession")
    wgs.__enter__ = MagicMock(return_value=stub_session)
    wgs.__exit__ = MagicMock(return_value=False)

    stub_bus = _make_stub_bus()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "daemon.services.dependency_bus.get_dependency_bus",
            lambda: stub_bus,
        )
        mp.setattr(
            "daemon.services.error_reporting.WriteGuardSession",
            lambda *a, **kw: wgs,
        )
        mp.setattr(
            "daemon.services.error_reporting.Session",
            lambda *a, **kw: MagicMock(name="raw_session"),
        )
        mp.setattr(
            "daemon.services.completion_registry.get_completion_registry",
            lambda: MagicMock(name="CompletionRegistry"),
        )
        await service._send_error_report(
            instance_id=CHILD_ID,
            error="LLM call failed with status 400",
            error_type="execution_error",
            message_id=MESSAGE_ID,
        )

    return manager, stub_bus, service


# ---------------------------------------------------------------------------
# 1. Happy path — both emits fire on the happy-path branch
# ---------------------------------------------------------------------------


class TestC1HappyPath:
    """C1 (2026-09-11): corrective (parent, child) instance-pair emit
    fires on the error lane's happy path (when ``_child_reports_service``
    is wired).
    """

    @pytest.mark.asyncio
    async def test_corrective_emit_fires_after_task_keyed_emit(self):
        """The task-keyed emit AND the corrective instance-pair emit
        both fire — order does not matter for correctness (both calls
        route through the bus's ``transition_state`` guarded UPDATE
        and exactly-once is preserved), but both must land on the
        ``_child_reports_service`` for the multi-turn child class.
        """
        manager, _, _ = await _drive_send_error_report()
        svc = manager._child_reports_service

        # The task-keyed emit was called (single-turn baseline).
        svc._emit_terminal_via_bus.assert_awaited_once()
        task_keyed_kwargs = svc._emit_terminal_via_bus.await_args.kwargs
        assert task_keyed_kwargs["status"] == "error"

        # C1 — the corrective (parent, child) instance-pair emit
        # fires too. This is the gap C1 closes.
        svc._emit_terminal_for_child_instance_via_bus.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_corrective_emit_passes_instance_pair(self):
        """The corrective emit's parent/child ids match the failing
        child's actual parent and the child's own instance id.
        """
        manager, _, _ = await _drive_send_error_report()
        svc = manager._child_reports_service

        kwargs = (
            svc._emit_terminal_for_child_instance_via_bus.await_args.kwargs
        )
        assert kwargs["parent_instance_id"] == PARENT_ID
        assert kwargs["child_instance_id"] == CHILD_ID
        assert kwargs["status"] == "error"
        # The summary names the corrective path so operators can
        # distinguish multi-turn emits in the log.
        summary = kwargs["summary"]
        assert "corrective multi-turn" in summary

    @pytest.mark.asyncio
    async def test_corrective_emit_passes_error_message(self):
        """The error string is threaded into the corrective emit's
        Outcome so the bus's per-parent ``_parent_errored`` /
        ``_parent_error_message`` state is set correctly.
        """
        manager, _, _ = await _drive_send_error_report()
        svc = manager._child_reports_service

        kwargs = (
            svc._emit_terminal_for_child_instance_via_bus.await_args.kwargs
        )
        assert kwargs["error"] == "LLM call failed with status 400"


# ---------------------------------------------------------------------------
# 2. Resilience — one emit failing does not block the other
# ---------------------------------------------------------------------------


class TestC1Resilience:
    """C1 (2026-09-11): the two emits are wrapped in independent
    try/except blocks so one failure does not skip the other. The
    error lane is best-effort after the DB-sync helper has committed
    the parent status — a swallowed exception here does not unwind
    the cascade.
    """

    @pytest.mark.asyncio
    async def test_task_keyed_emit_failure_does_not_skip_corrective(self):
        """If ``_emit_terminal_via_bus`` raises, the corrective
        ``_emit_terminal_for_child_instance_via_bus`` STILL fires —
        the parent's PENDING watcher must be released even when the
        task-keyed path is broken (the multi-turn child class is
        exactly the case where the task-keyed emit cannot help, so
        skipping the corrective when the task-keyed raises would
        re-introduce the silent park bug for any future bug that
        breaks the task-keyed emit).
        """
        manager = _make_manager()
        svc = manager._child_reports_service
        # Task-keyed emit raises (simulates a transient bus hook fault).
        svc._emit_terminal_via_bus = AsyncMock(
            side_effect=RuntimeError("simulated task-keyed emit failure")
        )
        # Corrective emit still works.
        svc._emit_terminal_for_child_instance_via_bus = AsyncMock(
            return_value=[]
        )

        service = ErrorReportingService(
            manager=manager, events_service=None
        )
        stub_session = _make_stub_session()
        wgs = MagicMock(name="WriteGuardSession")
        wgs.__enter__ = MagicMock(return_value=stub_session)
        wgs.__exit__ = MagicMock(return_value=False)
        stub_bus = _make_stub_bus()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "daemon.services.dependency_bus.get_dependency_bus",
                lambda: stub_bus,
            )
            mp.setattr(
                "daemon.services.error_reporting.WriteGuardSession",
                lambda *a, **kw: wgs,
            )
            mp.setattr(
                "daemon.services.error_reporting.Session",
                lambda *a, **kw: MagicMock(name="raw_session"),
            )
            mp.setattr(
                "daemon.services.completion_registry.get_completion_registry",
                lambda: MagicMock(name="CompletionRegistry"),
            )
            # Must NOT raise — the DB-sync helper has already
            # committed the parent status, so a bus-hook fault
            # is best-effort.
            await service._send_error_report(
                instance_id=CHILD_ID,
                error="transient",
                error_type="execution_error",
                message_id=MESSAGE_ID,
            )

        # Corrective emit still fired despite the task-keyed raise.
        svc._emit_terminal_for_child_instance_via_bus.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_corrective_emit_failure_does_not_propagate(self):
        """If the corrective ``_emit_terminal_for_child_instance_via_bus``
        raises, the error lane MUST NOT propagate the exception — the
        error report has already been enqueued to the parent (the
        parent's LLM has the recovery hint) and any bus-hook fault is
        operational metadata that the operator logs separately.
        """
        manager = _make_manager()
        svc = manager._child_reports_service
        svc._emit_terminal_via_bus = AsyncMock(return_value=[])
        svc._emit_terminal_for_child_instance_via_bus = AsyncMock(
            side_effect=RuntimeError(
                "simulated corrective emit failure"
            )
        )

        service = ErrorReportingService(
            manager=manager, events_service=None
        )
        stub_session = _make_stub_session()
        wgs = MagicMock(name="WriteGuardSession")
        wgs.__enter__ = MagicMock(return_value=stub_session)
        wgs.__exit__ = MagicMock(return_value=False)
        stub_bus = _make_stub_bus()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "daemon.services.dependency_bus.get_dependency_bus",
                lambda: stub_bus,
            )
            mp.setattr(
                "daemon.services.error_reporting.WriteGuardSession",
                lambda *a, **kw: wgs,
            )
            mp.setattr(
                "daemon.services.error_reporting.Session",
                lambda *a, **kw: MagicMock(name="raw_session"),
            )
            mp.setattr(
                "daemon.services.completion_registry.get_completion_registry",
                lambda: MagicMock(name="CompletionRegistry"),
            )
            # The call MUST NOT raise — both emits are best-effort
            # after the DB-sync commit.
            await service._send_error_report(
                instance_id=CHILD_ID,
                error="transient",
                error_type="execution_error",
                message_id=MESSAGE_ID,
            )


# ---------------------------------------------------------------------------
# 3. Defensive-fallback path — emits the corrective call directly
# ---------------------------------------------------------------------------


class TestC1DefensiveFallback:
    """C1 (2026-09-11): when ``_child_reports_service`` is NOT wired
    (legacy test fixture / partial init during early daemon startup),
    the defensive-fallback path also fires the corrective instance-
    pair emit — directly on the bus's
    ``emit_terminal_for_child_instance`` primitive. Same multi-turn
    coverage, kept self-contained on the rare fallback path.
    """

    @pytest.mark.asyncio
    async def test_fallback_path_emits_corrective_on_bus(self):
        """With no ``_child_reports_service`` wired, the bus's
        ``emit_terminal_for_child_instance`` fires with the right
        ``(parent_instance_id, child_instance_id)`` pair.
        """
        _, stub_bus, _ = await _drive_send_error_report(
            with_child_reports_service=False,
        )

        # Task-keyed emit on the bus — single-turn baseline.
        stub_bus.emit_terminal.assert_awaited_once()
        # C1 — corrective instance-pair emit on the bus too.
        stub_bus.emit_terminal_for_child_instance.assert_awaited_once()

        kwargs = (
            stub_bus.emit_terminal_for_child_instance.await_args.kwargs
        )
        assert kwargs["parent_instance_id"] == PARENT_ID
        assert kwargs["child_instance_id"] == CHILD_ID
        # The outcome is the same Outcome object the task-keyed emit
        # received (built once at the top of the try block and
        # reused — no double allocation).
        outcome = kwargs["outcome"]
        assert outcome.status == "error"

    @pytest.mark.asyncio
    async def test_fallback_path_emits_both_even_when_first_raises(self):
        """With no ``_child_reports_service`` wired and the task-keyed
        bus emit raising, the corrective instance-pair emit STILL
        fires (independent try/except wrapping).
        """
        manager = _make_manager(
            with_child_reports_service=False,
        )
        service = ErrorReportingService(
            manager=manager, events_service=None
        )
        stub_session = _make_stub_session()
        wgs = MagicMock(name="WriteGuardSession")
        wgs.__enter__ = MagicMock(return_value=stub_session)
        wgs.__exit__ = MagicMock(return_value=False)

        stub_bus = _make_stub_bus()
        # Task-keyed emit on the bus raises.
        stub_bus.emit_terminal = AsyncMock(
            side_effect=RuntimeError("simulated bus emit failure")
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "daemon.services.dependency_bus.get_dependency_bus",
                lambda: stub_bus,
            )
            mp.setattr(
                "daemon.services.error_reporting.WriteGuardSession",
                lambda *a, **kw: wgs,
            )
            mp.setattr(
                "daemon.services.error_reporting.Session",
                lambda *a, **kw: MagicMock(name="raw_session"),
            )
            mp.setattr(
                "daemon.services.completion_registry.get_completion_registry",
                lambda: MagicMock(name="CompletionRegistry"),
            )
            # Must NOT raise — the fallback path is best-effort.
            await service._send_error_report(
                instance_id=CHILD_ID,
                error="transient",
                error_type="execution_error",
                message_id=MESSAGE_ID,
            )

        stub_bus.emit_terminal_for_child_instance.assert_awaited_once()


# ---------------------------------------------------------------------------
# 4. Default values — no new env flag, no new tunables, no new writer
# ---------------------------------------------------------------------------


class TestC1Defaults:
    """C1 (2026-09-11): no new env flags, no new tunables, no new
    admission_state_writer / JobItem creator / work_id mint site.

    The fix reuses existing primitives — the corrective
    ``_emit_terminal_for_child_instance_via_bus`` helper (which the
    ``regular_child_completed`` and ``child_still_running_defer``
    outcomes in ``child_reports.py`` already use) and the bus's
    ``emit_terminal_for_child_instance`` primitive. Both pre-existed
    before C1; C1 just routes the ERROR lane through them in addition
    to the task-keyed emit.

    The census therefore stays at 23/1/0 — these primitives are
    repository-method-only (no ``SET admission_state`` / ORM
    ``JobItem(...)`` / work_id mint).
    """

    def test_no_new_env_flag(self):
        """C1 introduces no new env flag — the corrective emit is a
        code-level invariant on every child error.
        """
        import inspect

        src = inspect.getsource(ErrorReportingService._send_error_report)
        # Look at the C1 block specifically — it must not read env vars.
        c1_marker = "C1 (Batch C, 2026-09-11)"
        assert c1_marker in src
        # Take the slice after the first C1 marker to the end of the
        # function body — every new emit after the task-keyed one is
        # C1's contribution.
        post_c1 = src.split(c1_marker, 1)[1]
        # No env-flag reading.
        assert "os.environ.get" not in post_c1
        assert "ENSEMBLE_" not in post_c1

    def test_no_new_repo_writer(self):
        """C1 routes through existing facade methods — no direct repo
        writes that would add a new admission_state_writer site.
        """
        import inspect

        src = inspect.getsource(ErrorReportingService._send_error_report)
        c1_marker = "C1 (Batch C, 2026-09-11)"
        post_c1 = src.split(c1_marker, 1)[1]
        # No raw repository.create / INSERT / session.add of watcher
        # rows — C1 goes through the facade (``_emit_terminal_*_via_bus``
        # / ``bus.emit_terminal_for_child_instance``).
        assert "INSERT INTO dependency_watchers" not in post_c1
        assert "self._repo.create" not in post_c1

    def test_corrective_emit_uses_existing_primitive(self):
        """The corrective emit on the happy path uses the existing
        ``_emit_terminal_for_child_instance_via_bus`` primitive that
        ``child_reports.py:_dispatch_post_commit_side_effects`` already
        uses at the ``regular_child_completed`` and
        ``child_still_running_defer`` outcomes (~lines 3711 / 3933).
        """
        import inspect

        src = inspect.getsource(ErrorReportingService._send_error_report)
        assert (
            "_emit_terminal_for_child_instance_via_bus" in src
        ), (
            "C1 must call the existing corrective multi-turn emit "
            "primitive on the _child_reports_service — no new "
            "writer / facade path."
        )