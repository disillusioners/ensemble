"""Tests for the M2 ``watch_job(events='mission_terminal')`` opt-in gating.

Mission-class Milestone M2 (2026-09-02, ``feature/mission-class``) —
contract draft §3.5: ``watch_job(events='mission_terminal')`` fires
ONLY when admission AND mission liveness are BOTH terminal. Default
event behavior stays transport semantics (back-compat).

Two surfaces are exercised:

  1. The tool-level gate in ``watch_job`` / ``watch_jobs`` (the
     immediate-notify branch when the job is already terminal at
     registration time). When ``mission_terminal`` is in the
     watcher's events list AND mission liveness is not yet
     terminal, the watch is registered but the immediate
     notification is suppressed.

  2. The notification-level gate in
     ``daemon/services/work_notifier.py::notify_work_watchers``.
     When ``mission_terminal`` is in a watcher's events list AND
     the linked instance's canonical mission liveness is not
     terminal, the watcher row is held (no notification fires).

Both gates exist so the wrong-predicate trap stays closed: an
agent that opts in via ``mission_terminal`` cannot be misled by a
settled transport receipt that has not yet reached mission
terminality.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.work_notifier import notify_work_watchers
from daemon.tools.job_queue import (
    _record_mission_is_terminal,
    create_job_tools,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def job_service() -> AsyncMock:
    svc = AsyncMock()
    svc.use_virtual_job_resolver = False
    return svc


@pytest.fixture
def queue_mgmt_service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def dead_letter_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def watcher_repo() -> MagicMock:
    """``JobWatcherRepository`` mock — count + add_watch + claim.

    Default ``count`` is 0 (no prior watches) so the
    ``MAX_WATCHES=50`` gate never fires. ``claim_watchers_for_job_for_instances``
    defaults to returning an empty list (the held / not-matching
    case). Tests that need the matching watcher to be claimed set
    the method's ``return_value`` explicitly.
    """
    repo = MagicMock()
    repo.count_watches_for_instance = MagicMock(return_value=0)
    repo.add_watch = MagicMock()
    repo.get_watchers_for_job = MagicMock(return_value=[])
    repo.claim_watchers_for_job_for_instances = MagicMock(return_value=[])
    return repo


@pytest.fixture
def task_repo() -> MagicMock:
    """``TaskRepository`` mock — the ``has_instance_busy`` gate."""
    repo = MagicMock()
    repo.has_instance_busy = MagicMock(return_value=False)
    return repo


@pytest.fixture
def mock_manager(task_repo: MagicMock, watcher_repo: MagicMock) -> MagicMock:
    manager = MagicMock()
    manager._task_repo = task_repo
    manager._watcher_repo = watcher_repo
    manager._instance_repository = MagicMock()
    manager.enqueue_message = AsyncMock()
    return manager


@pytest.fixture
def tools(
    job_service, queue_mgmt_service, dead_letter_service,
    watcher_repo, mock_manager,
):
    return create_job_tools(
        job_service,
        queue_mgmt_service,
        dead_letter_service,
        current_instance_id="watcher-inst",
        agent_id="watcher-agent",
        watcher_repo=watcher_repo,
        manager=mock_manager,
    )


# ─── Helpers ─────────────────────────────────────────────────────────────


def _make_record(*, status: str, mission_liveness: str | None, job_type: str = "message"):
    """Build a ``WorkRecord``-shaped mock with the canonical mission fields.

    ``status`` is the row's canonical work-side status (used by the
    transport ``is_terminal`` check). ``mission_liveness`` is the
    linked instance's canonical mission vocabulary for mirror rows
    (``None`` for task rows).
    """
    record = MagicMock()
    type(record).status = property(lambda self: status)
    type(record).job_type = property(lambda self: job_type)
    type(record).mission_liveness = property(lambda self: mission_liveness)
    type(record).kind = property(lambda self: "job")
    record.instance_id = "inst-1"
    return record


# ─── The M2 watch_job tool-level gate ────────────────────────────────────


class TestWatchJobMissionTerminalToolGate:
    """``watch_job`` tool-level: when ``mission_terminal`` is in
    ``events`` and the mission is not yet terminal, the watch is
    registered but the immediate notification is suppressed.
    """

    @pytest.mark.asyncio
    async def test_mission_terminal_holds_when_mission_not_terminal(
        self, job_service, watcher_repo, mock_manager, tools
    ) -> None:
        """Transport terminal + mission live ⇒ watch registered, no
        immediate notify.

        Contract draft §3.5: when ``events=['mission_terminal']``
        and the mission is not yet terminal, the watcher wants to
        fire ONLY when BOTH admission AND mission liveness are
        terminal. The tool suppresses the immediate-notification
        branch and returns a "held" message.
        """
        record = _make_record(
            status="completed",  # transport is terminal
            mission_liveness="processing",  # mission is NOT terminal
            job_type="message",
        )
        job_service.get_work.return_value = record

        watch_job = tools[17]
        result = await watch_job.ainvoke({
            "job_id": "job-mission-terminal",
            "events": ["mission_terminal"],
        })

        # The watch IS registered (the row stays in place for the
        # future terminal event).
        watcher_repo.add_watch.assert_called_once()
        # But the immediate notify path was NOT triggered —
        # ``notify_watchers`` is the asynchronous transport path.
        job_service.notify_watchers.assert_not_awaited()
        # The result surfaces the held state so the agent knows
        # why no notification fired now.
        assert "mission_terminal" in result or "held" in result or "gating" in result

    @pytest.mark.asyncio
    async def test_mission_terminal_fires_when_both_terminal(
        self, job_service, watcher_repo, mock_manager, tools
    ) -> None:
        """Transport terminal + mission terminal ⇒ immediate
        notification fires."""
        record = _make_record(
            status="completed",
            mission_liveness="completed",  # mission IS terminal
            job_type="message",
        )
        job_service.get_work.return_value = record

        watch_job = tools[17]
        result = await watch_job.ainvoke({
            "job_id": "job-mission-terminal-2",
            "events": ["mission_terminal"],
        })

        # Watch registered (the row is delivered + consumed for the
        # immediate-notify branch).
        watcher_repo.add_watch.assert_called_once()
        # notify_watchers fires (the dual-terminal condition held).
        job_service.notify_watchers.assert_awaited()
        # The result confirms the immediate notification.
        assert "already" in result.lower() or "immediate" in result.lower()

    @pytest.mark.asyncio
    async def test_default_watch_event_set_excludes_mission_terminal(
        self, job_service, watcher_repo, mock_manager, tools
    ) -> None:
        """A watcher that does NOT opt in (``events=None``) sees
        transport-only semantics — back-compat preserved.

        Pre-M2 watchers (which subscribed to the default event set
        ``ALL_WATCHABLE_EVENTS`` = terminal states + ``in_progress``)
        do NOT see ``mission_terminal`` notifications fire. The
        opt-in is explicit; the default does NOT widen.
        """
        # Build a record with mission_liveness=None (degraded). The
        # default watcher fires on transport terminal regardless.
        record = _make_record(
            status="completed",
            mission_liveness=None,
            job_type="message",
        )
        job_service.get_work.return_value = record

        watch_job = tools[17]
        result = await watch_job.ainvoke({"job_id": "job-default"})

        # Default watch: immediate notify fires (the default
        # behavior is preserved).
        job_service.notify_watchers.assert_awaited()
        watcher_repo.add_watch.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_event_name_rejected(
        self, job_service, watcher_repo, mock_manager, tools
    ) -> None:
        """An unknown ``events`` value is rejected with a clear list
        of accepted values.

        Without this, a typo in ``events`` would silently degrade
        to "match nothing" — a fail-open trap. The validation
        mirrors the HTTP surface's "unknown filter ⇒ 400"
        precedent.
        """
        watch_job = tools[17]
        result = await watch_job.ainvoke({
            "job_id": "job-typo",
            "events": ["mission_terminal_typo"],  # not a real event
        })
        # Error wording lists accepted values.
        assert "Error" in result or "error" in result.lower()
        assert "Unknown" in result or "unknown" in result.lower()
        # The watch was NOT registered (validation rejected the
        # call before ``add_watch``).
        watcher_repo.add_watch.assert_not_called()


# ─── The notification-level gate ─────────────────────────────────────────


class TestNotifyWatchersMissionTerminalGate:
    """``notify_work_watchers`` holds ``mission_terminal`` watchers
    when the linked mission liveness is not yet terminal.
    """

    @pytest.mark.asyncio
    async def test_mission_terminal_watcher_held_when_mission_not_terminal(self) -> None:
        """A watcher that opted in via ``mission_terminal`` is held
        back when the linked instance is still in a non-terminal
        liveness. The watcher row stays in place — no
        ``enqueue_message`` call fires, ``claim_watchers_for_job``
        is NOT called (the ``notified == 0`` path keeps the watch
        alive).
        """
        # Mirror row, transport terminal, mission LIVE.
        work_record = _make_record(
            status="completed",
            mission_liveness="processing",  # mission NOT terminal
            job_type="message",
        )
        work_resolver = MagicMock()
        work_resolver.resolve_work = MagicMock(return_value=work_record)
        instance_manager = MagicMock()
        instance_manager.enqueue_message = AsyncMock()

        # Watcher opted in via ``mission_terminal``.
        watcher = MagicMock()
        watcher.instance_id = "watcher-inst"
        watcher.watch_events = ["mission_terminal"]
        watcher_repo = MagicMock()
        watcher_repo.get_watchers_for_job = MagicMock(return_value=[watcher])
        # Claim-first flow: only called for the matching subset;
        # the held-for-mission watcher is excluded so this MUST NOT
        # be called.
        watcher_repo.claim_watchers_for_job_for_instances = MagicMock(
            return_value=[]
        )

        notified = await notify_work_watchers(
            "job-1",
            "completed",  # admission terminal
            error=None,
            instance_manager=instance_manager,
            work_resolver=work_resolver,
            watcher_repo=watcher_repo,
        )

        # No notification fires — the gate held the watcher back.
        assert notified == 0
        instance_manager.enqueue_message.assert_not_awaited()
        # The watcher row is NOT claimed — the held path skips
        # claim entirely (matching set is empty) so the row stays
        # in place for the future terminal event.
        watcher_repo.claim_watchers_for_job_for_instances.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_watcher_fires_on_transport_terminal(self) -> None:
        """A watcher that did NOT opt in via ``mission_terminal``
        still fires on transport terminal — back-compat.

        Default watchers (``watch_events=['completed', 'failed',
        'cancelled', 'dead_letter', 'in_progress']``) see the
        immediate transport-event notification. Only the explicit
        ``mission_terminal`` opt-in sees the dual-terminal gate.
        """
        work_record = _make_record(
            status="completed",
            mission_liveness="processing",  # mission live
            job_type="message",
        )
        work_resolver = MagicMock()
        work_resolver.resolve_work = MagicMock(return_value=work_record)
        instance_manager = MagicMock()
        instance_manager.enqueue_message = AsyncMock()

        watcher = MagicMock()
        watcher.instance_id = "watcher-inst"
        # Default event set — no ``mission_terminal``.
        watcher.watch_events = [
            "completed", "failed", "cancelled", "dead_letter", "in_progress",
        ]
        watcher_repo = MagicMock()
        watcher_repo.get_watchers_for_job = MagicMock(return_value=[watcher])
        # Claim-first flow: the matching watcher wins the CAS and
        # is returned for the notify loop.
        watcher_repo.claim_watchers_for_job_for_instances = MagicMock(
            return_value=[watcher]
        )

        notified = await notify_work_watchers(
            "job-1",
            "completed",
            error=None,
            instance_manager=instance_manager,
            work_resolver=work_resolver,
            watcher_repo=watcher_repo,
        )

        # Default watcher fires on transport terminal.
        assert notified == 1
        instance_manager.enqueue_message.assert_awaited()

    @pytest.mark.asyncio
    async def test_mission_terminal_watcher_fires_when_both_terminal(self) -> None:
        """``mission_terminal`` opt-in fires when BOTH admission AND
        mission liveness are terminal."""
        work_record = _make_record(
            status="completed",
            mission_liveness="completed",  # mission IS terminal
            job_type="message",
        )
        work_resolver = MagicMock()
        work_resolver.resolve_work = MagicMock(return_value=work_record)
        instance_manager = MagicMock()
        instance_manager.enqueue_message = AsyncMock()

        watcher = MagicMock()
        watcher.instance_id = "watcher-inst"
        watcher.watch_events = ["mission_terminal"]
        watcher_repo = MagicMock()
        watcher_repo.get_watchers_for_job = MagicMock(return_value=[watcher])
        # Claim-first flow: matching watcher (mission_terminal opt-in
        # with mission also terminal) wins the CAS.
        watcher_repo.claim_watchers_for_job_for_instances = MagicMock(
            return_value=[watcher]
        )

        notified = await notify_work_watchers(
            "job-1",
            "completed",
            error=None,
            instance_manager=instance_manager,
            work_resolver=work_resolver,
            watcher_repo=watcher_repo,
        )

        assert notified == 1
        instance_manager.enqueue_message.assert_awaited()

    @pytest.mark.asyncio
    async def test_held_mission_terminal_watcher_fires_on_cancelled_mission(
        self,
    ) -> None:
        """A3 pin (mission-class, 2026-09-03) — a held
        ``mission_terminal`` watcher that was held because the
        mission was non-terminal at the prior transport event
        FIRES when the mission reaches ``cancelled`` (the
        ``TERMINATED`` ``InstanceStatus`` canonical mapping).

        Per-kind dispatch (ADR-MISSION-01 §6.6 I3 amendment) — the
        held gate passes when ``mission_liveness in {completed,
        failed, cancelled}``. The pin asserts the watcher sees the
        re-fire on the cancelled mission liveness, not just the
        natural ``completed`` / ``failed`` re-fires.
        """
        # Mirror row, admission terminal + mission CANCELLED.
        work_record = _make_record(
            status="completed",  # transport terminal
            mission_liveness="cancelled",  # mission reached TERMINATED
            job_type="message",
        )
        work_resolver = MagicMock()
        work_resolver.resolve_work = MagicMock(return_value=work_record)
        instance_manager = MagicMock()
        instance_manager.enqueue_message = AsyncMock()

        # Watcher opted in via ``mission_terminal`` — held earlier,
        # now must re-fire on the cancelled-mission liveness.
        watcher = MagicMock()
        watcher.instance_id = "watcher-inst"
        watcher.watch_events = ["mission_terminal"]
        watcher_repo = MagicMock()
        watcher_repo.get_watchers_for_job = MagicMock(return_value=[watcher])
        # Claim-first flow: the watcher now passes the held gate
        # (mission IS terminal) and wins the CAS.
        watcher_repo.claim_watchers_for_job_for_instances = MagicMock(
            return_value=[watcher]
        )

        notified = await notify_work_watchers(
            "job-cancelled-1",
            "cancelled",
            error=None,
            instance_manager=instance_manager,
            work_resolver=work_resolver,
            watcher_repo=watcher_repo,
            result_summary="mission terminated",
        )

        # The held gate must pass — watcher fires on cancelled.
        assert notified == 1, (
            f"held mission_terminal watcher must re-fire when the "
            f"mission reaches 'cancelled' (TERMINATED InstanceStatus "
            f"mapping); got notified={notified}"
        )
        instance_manager.enqueue_message.assert_awaited()
        # The wire text MUST carry the canonical ``cancelled``
        # token — not ``completed`` (which would mis-route the
        # orchestrator's parser contract).
        call_kwargs = instance_manager.enqueue_message.await_args.kwargs
        message = call_kwargs["message"]
        assert "cancelled" in message, (
            f"[JOB_EVENT] text for re-fire on cancelled mission "
            f"must carry 'cancelled'; got: {message!r}"
        )

    @pytest.mark.asyncio
    async def test_settled_mirror_renders_settled_in_job_event_text(self) -> None:
        """A2 pin (mission-class, 2026-09-03) — the ``[JOB_EVENT]`` text
        for a terminal MIRROR row carries the wire-accurate ``settled``
        label, NOT ``completed``.

        Per-kind dispatch (ADR-MISSION-01 §6.6 I3 amendment): the
        transport-receipt terminal ``settled`` is disjoint from the
        work-outcome vocabulary (``completed`` is reserved for task
        rows / the mission-side outcome). The ``[JOB_EVENT]`` text is
        consumed by the orchestrator's parser contract
        (``agents/job-orchestration/skill.md``); a regression that
        collapses mirror rows onto ``completed ✓`` in the wire text
        would break the parser's per-kind branching.
        """
        work_record = _make_record(
            status="settled",  # mirror terminal — the M3 rename
            mission_liveness="completed",  # mission IS terminal too
            job_type="message",
        )
        work_resolver = MagicMock()
        work_resolver.resolve_work = MagicMock(return_value=work_record)
        instance_manager = MagicMock()
        instance_manager.enqueue_message = AsyncMock()

        # Default watcher fires on terminal transport events.
        watcher = MagicMock()
        watcher.instance_id = "watcher-inst"
        watcher.watch_events = [
            "completed", "settled", "failed", "cancelled",
            "dead_letter", "in_progress",
        ]
        watcher_repo = MagicMock()
        watcher_repo.get_watchers_for_job = MagicMock(return_value=[watcher])
        # Claim-first flow: matching watcher wins the CAS for the
        # settled-mirror notification.
        watcher_repo.claim_watchers_for_job_for_instances = MagicMock(
            return_value=[watcher]
        )

        notified = await notify_work_watchers(
            "job-settled-1",
            "settled",
            error=None,
            instance_manager=instance_manager,
            work_resolver=work_resolver,
            watcher_repo=watcher_repo,
            result_summary="mirror settled",
        )

        assert notified == 1
        instance_manager.enqueue_message.assert_awaited()
        # Inspect the message that was enqueued — the wire text MUST
        # carry ``settled ✓`` (the M3 per-kind dispatch), NOT
        # ``completed ✓`` (the legacy mirror-terminal text).
        call_kwargs = instance_manager.enqueue_message.await_args.kwargs
        message = call_kwargs["message"]
        assert "[JOB_EVENT]" in message
        assert "settled ✓" in message, (
            f"[JOB_EVENT] text for terminal mirror row must carry "
            f"'settled ✓' (M3 per-kind dispatch); got: {message!r}"
        )
        # Negative assertion — the legacy ``completed ✓`` text must
        # NOT leak onto a mirror row's notification.
        assert "completed ✓" not in message, (
            f"[JOB_EVENT] text for terminal mirror row must NOT carry "
            f"legacy 'completed ✓' (settled is disjoint from completed); "
            f"got: {message!r}"
        )


# ─── The ``_record_mission_is_terminal`` helper ──────────────────────────


class TestRecordMissionIsTerminalHelper:
    """The companion helper that drives the gate logic."""

    def test_mirror_live_mission_returns_false(self) -> None:
        """A live mission (mirror row) returns ``False``."""
        record = _make_record(
            status="completed",
            mission_liveness="processing",
            job_type="message",
        )
        assert _record_mission_is_terminal(record) is False

    def test_mirror_terminal_mission_returns_true(self) -> None:
        """A terminal mission (mirror row) returns ``True``."""
        record = _make_record(
            status="completed",
            mission_liveness="completed",
            job_type="message",
        )
        assert _record_mission_is_terminal(record) is True

    def test_task_terminal_returns_true(self) -> None:
        """A task row whose ``status`` is terminal returns ``True``
        (the row IS its own mission)."""
        record = _make_record(
            status="completed",
            mission_liveness=None,  # task rows have None here
            job_type="task",
        )
        assert _record_mission_is_terminal(record) is True

    def test_task_live_returns_false(self) -> None:
        """A live task row returns ``False``."""
        record = _make_record(
            status="processing",
            mission_liveness=None,
            job_type="task",
        )
        assert _record_mission_is_terminal(record) is False

    def test_none_record_returns_false(self) -> None:
        """Defensive: ``None`` record ⇒ ``False`` (the gate fails
        closed for the unknown-row case)."""
        assert _record_mission_is_terminal(None) is False
