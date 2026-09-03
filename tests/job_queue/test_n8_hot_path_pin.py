"""N8 (mission-class, 2026-09-03, ``feature/mission-class``) — hot-path
pin for the per-kind dispatch in the observer's post-commit outbox.

Bug: a mirror JobItem (job_type=message) settling via the observer's
PRIMARY event path (the ``_process_event`` → ``_finalize_job`` →
post-commit outbox flow) was rendered as ``completed ✓`` — the WRONG
speaker for a mirror row under the per-kind vocabulary
(ADR-MISSION-01 §6.6 I3 amendment). The mirror's transport-receipt
terminal is ``settled``, NOT ``completed``; collapsing the two broke
the orchestrator's per-kind parser contract (the
``agents/job-orchestration/skill.md`` parser branches on the wire
token).

This file pins the FIX: the post-commit outbox must derive the
per-kind status via ``WorkResolverService.per_kind_status_for`` so the
token the watcher receives matches the row's kind (mirror → settled,
task → completed).

Differential proof (project convention, see required):

  Copy ONLY this file into a ``git worktree`` at ``68202403`` (the
  pre-fix base). Run the pin there — it FAILS with ``TypeError`` /
  ``AssertionError`` because the pre-fix observer hardcodes
  ``status="completed"`` for every candidate work_id (mirror collapses
  onto task-outcome vocabulary). At HEAD, the pin PASSES (mirror →
  settled via the resolver; task → completed unchanged).

The previous synthetic pin at
``tests/unit/tools/test_watch_job_mission_terminal.py:448`` only
exercised a direct ``notify_work_watchers(...)`` call with a hand-set
status; this new pin drives the REAL handler (the observer's
``_process_event`` → ``_finalize_job`` → post-commit outbox flow) and
inspects the per-work_id ``status`` argument the observer passes into
``notify_watchers`` — closing the "synthetic-only" gap the reviewer
flagged.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import JobItem
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _FinalizeJobResult,
    _ProcessingJobContext,
)
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.work_resolver import WorkRecord, WorkResolverService


# Map legacy status → admission_state (Phase 4: status is frozen,
# admission_state is the sole authority). Mirrors the in-test map
# used by ``tests/job_queue/test_job_feedback_observer.py`` so the
# MagicMock JobItem surfaces the right admission value to the
# production code's admission-aware branches.
_STATUS_TO_ADMISSION = {
    "pending": "queued",
    "processing": "active",
    "paused": "active",
    "completed": "done",
    "failed": "done",
    "cancelled": "done",
    "dead_letter": "dead",
}


@pytest.fixture(autouse=True)
def _reset_bus():
    """Tear down the DependencyBus singleton after the test so a
    failing assertion doesn't leak the mock into the next test."""
    yield
    set_dependency_bus(None)


def _build_observer_for_hot_path(
    *,
    instance_id: str,
    mirror_work_id: str | None,
    task_work_ids: list[str] | None,
    job_id_for_instance: str | None,
) -> tuple[JobFeedbackObserver, MagicMock, WorkResolverService]:
    """Build a ``JobFeedbackObserver`` configured for the hot-path pin.

    The post-commit outbox (at the bottom of ``_finalize_job``) reads
    ``getattr(self._job_queue_service, "_work_resolver", None)`` and
    calls ``per_kind_status_for(work_id, default=...)`` per
    candidate work_id. We need:

    1. A REAL ``WorkResolverService`` wired into the mock job_queue_service
       so the per-kind dispatch returns ``settled`` for mirror rows.
    2. A REAL ``_finalize_job`` flow — only the sync DB write
       (``_finalize_job_db_sync``) is mocked (since it would try to
       open a Session against the MagicMock instance_manager's
       engine). The rest of the production code runs.
    3. A wired DependencyBus so the bus lock + pending-count gate
       pass. The mock bus returns ``0`` / ``False`` so a COMPLETED
       event falls through to the post-commit outbox.
    """
    # ── Mocks for the JobQueueService surface the observer calls ──
    mock_job = MagicMock(spec=JobItem)
    mock_job.job_id = job_id_for_instance or "job-instance-context"
    mock_job.admission_state = _STATUS_TO_ADMISSION["processing"]
    mock_job.instance_id = instance_id

    mock_job_queue_service = MagicMock()
    mock_job_queue_service.get_job_by_instance = AsyncMock(
        return_value=mock_job
    )
    notify_mock = AsyncMock(return_value=0)
    mock_job_queue_service.notify_watchers = notify_mock
    # Sanity-check: verify the attribute assignment works (no MagicMock auto-viv).
    assert mock_job_queue_service.notify_watchers is notify_mock, (
        "mock_job_queue_service.notify_watchers assignment didn't take — "
        "MagicMock spec/protocol interference suspected"
    )

    # ── Real resolver wired into the mock service ──
    # N8 pin design: mock ``resolve_work`` directly to return the
    # right WorkRecord per work_id (Task rows → completed, mirror
    # JobItem rows → settled). This avoids the deep ``_job_to_record``
    # chain which would otherwise access many SQLModel attributes
    # that a MagicMock doesn't expose — the pin focuses on the OBSERVER's
    # call-site behaviour, not the resolver's full SQL pipeline
    # (the existing ``tests/integration/test_m3_per_kind_dispatch_pin.py``
    # already covers the resolver's full chain with real DB writes).
    task_repo_mock = MagicMock()
    task_repo_mock.get_by_work_id = MagicMock(return_value=None)
    job_repo_mock = MagicMock(spec=JobRepository)
    job_repo_mock.get = MagicMock(return_value=None)
    instance_repo_mock = MagicMock()

    real_resolver = WorkResolverService(
        task_repo_mock,
        job_repo_mock,
        instance_repo_mock,
    )

    def _fake_resolve(work_id: str):
        # Mirror row: per-kind dispatch flips completed → settled.
        if work_id == mirror_work_id:
            return WorkRecord(
                work_id=work_id,
                kind="job",
                status="settled",
                instance_id=instance_id,
                project_id="test-project",
                agent_id="developer",
                result_summary=None,
                error=None,
                created_at=None,
                job_type="message",
            )
        # Task row: status is the Task's terminal — completed for a
        # successful task. Per-kind dispatch does NOT apply.
        if work_id in (task_work_ids or []):
            return WorkRecord(
                work_id=work_id,
                kind="report",
                status="completed",
                instance_id=instance_id,
                project_id="test-project",
                agent_id="developer",
                result_summary=None,
                error=None,
                created_at=None,
                job_type=None,
            )
        return None

    real_resolver.resolve_work = MagicMock(side_effect=_fake_resolve)
    mock_job_queue_service._work_resolver = real_resolver

    # ── Bus singleton — no-op mock with the right surface ──
    bus_mock = MagicMock()
    bus_mock.count_pending_for_target = AsyncMock(return_value=0)
    bus_mock.count_pending_for_target_sync = lambda iid: 0
    bus_mock.had_parent_error = MagicMock(return_value=False)
    # ``_finalize_job`` acquires ``bus._get_parent_lock(parent_id)``
    # via ``async with await bus._get_parent_lock(parent_id):`` — a
    # MagicMock default returns a non-awaitable. Use a real asyncio.Lock
    # wrapped in AsyncMock so the ``await`` and ``async with`` both work.
    bus_mock._get_parent_lock = AsyncMock(
        side_effect=lambda parent_id: asyncio.Lock()
    )
    bus_mock.get_generation = MagicMock(return_value=0)
    set_dependency_bus(bus_mock)

    # ── Mock the InstanceManager's LLM-fetch so the pre-fetch works ──
    mock_instance_manager = MagicMock()
    mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
        return_value="hot-path pin assistant message"
    )

    mock_job_repo = MagicMock(spec=JobRepository)
    mock_lock_repo = MagicMock(spec=LockRepository)
    mock_lock_repo.release_by_instance = MagicMock(return_value=1)

    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=mock_job_queue_service,
        job_repo=mock_job_repo,
        lock_repo=mock_lock_repo,
        project_repo=MagicMock(),
        instance_manager=mock_instance_manager,
    )

    # ── Mock the sync DB write so it returns a proper result without ──
    # needing a real engine. The post-commit outbox at lines 1779+
    # still executes because we only bypass the sync helper, not the
    # full ``_finalize_job`` flow.
    def _fake_sync(
        job_id,
        instance_id,
        terminal_status,
        result_summary,
        error_message,
    ):
        return _FinalizeJobResult(
            skip=False,
            terminal_status=terminal_status,
            job_id=job_id,
            instance_id=instance_id,
            parent_id=None,
            agent_id="developer",
            result_summary=result_summary,
            error_message=error_message,
            locks_released=1,
            instance_was_terminal=False,
        )

    observer._finalize_job_db_sync = MagicMock(side_effect=_fake_sync)

    # The post-commit outbox pre-fetches terminal_watchers via
    # ``watcher_repo.get_watchers_for_job`` (the JobQueueService holds
    # the watcher repo). The observer reads it via
    # ``getattr(self._job_queue_service, "_watcher_repo", None)``.
    # Default to an empty list so the B4 pre-fetch returns no rows;
    # the candidate_work_ids set is populated from ``ctx.job_id``
    # (the JobItem returned by ``get_job_by_instance``) plus any
    # Task work_ids from ``inst_tasks`` (which we mock to return
    # ``task_work_ids``).

    def _get_by_instance(instance_id_arg):
        # Return an empty list for the Task-side fetch — the post-commit
        # outbox iterates ``candidate_work_ids`` only via this fetch
        # (besides ``ctx.job_id``).
        return []

    task_repo_for_inst = MagicMock()
    task_repo_for_inst.get_by_instance = MagicMock(side_effect=_get_by_instance)

    # Attach the task_repo to the instance_manager mock so the
    # post-commit outbox's ``getattr(self._instance_manager,
    # "_task_repo", None)`` returns our mock.
    mock_instance_manager._task_repo = task_repo_for_inst

    watcher_repo_mock = MagicMock()
    watcher_repo_mock.get_watchers_for_job = MagicMock(return_value=[])
    mock_job_queue_service._watcher_repo = watcher_repo_mock

    return observer, mock_job_queue_service, real_resolver


# ─── The N8 hot-path pin ──────────────────────────────────────────────────


class TestN8HotPathPin:
    """N8 (mission-class, 2026-09-03, ``feature/mission-class``) — the
    hot event path must render ``settled ✓`` for a mirror work_id and
    ``completed ✓`` for a task work_id.

    Pre-fix, the observer's post-commit outbox hardcoded
    ``status="completed"`` for the COMPLETED event branch (the
    early-return TERMINATED branch and the held-watcher re-fire
    re-derived notify_status from the caller, both kind-agnostic).
    The hardcoded literal collapsed mirror rows onto the task-side
    ``completed ✓`` glyph and broke the orchestrator's per-kind
    parser contract.

    Post-fix, the observer derives the per-kind status via
    ``WorkResolverService.per_kind_status_for(work_id,
    default="completed")`` so the wire text matches the row's kind:

    * mirror JobItem (job_type=message, terminal_reason=completed)
      → ``settled`` (per-kind dispatch in ``_job_to_record``)
    * task-backed WorkRecord (kind="report", status=completed)
      → ``completed`` (no per-kind dispatch)
    """

    @pytest.mark.asyncio
    async def test_mirror_work_id_renders_settled_via_primary_event_path(
        self,
    ) -> None:
        """A mirror JobItem (job_type=message, terminal_reason=completed)
        settling via the observer's PRIMARY event path fires
        ``notify_watchers`` with ``status="settled"`` (the
        per-kind-token), NOT ``status="completed"``.

        Failure mode the pin catches (the pre-fix bug): the
        observer's post-commit outbox at the COMPLETED branch
        hardcoded ``"completed"`` — the mirror's wire text said
        ``completed ✓`` (the WRONG speaker under the per-kind
        vocabulary; the mirror's transport-receipt terminal is
        ``settled``, disjoint from work-outcome ``completed``).
        """
        instance_id = f"inst-n8-{uuid.uuid4().hex[:8]}"
        mirror_jid = f"job-n8-mirror-{uuid.uuid4().hex[:8]}"

        observer, mock_jqs, _ = _build_observer_for_hot_path(
            instance_id=instance_id,
            mirror_work_id=mirror_jid,
            task_work_ids=[],
            job_id_for_instance=mirror_jid,
        )

        # Drive the REAL handler: ``_process_event`` for an
        # instance_lifecycle COMPLETED event. The flow goes
        # ``_process_event`` → ``_finalize_job`` → post-commit
        # outbox → ``notify_watchers``. The sync DB write is the
        # only step bypassed (mocked to return a fake result so the
        # rest of the production code runs).
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": instance_id,
                "status": "completed",
                "error": None,
            },
        }
        await observer._process_event(event)

        # The post-commit outbox must have fired notify_watchers for
        # the mirror work_id. Inspect the call args — the per-kind
        # token is the second positional arg.
        notify_calls = mock_jqs.notify_watchers.await_args_list
        assert len(notify_calls) >= 1, (
            "post-commit outbox must fire notify_watchers for the "
            "mirror work_id; got zero calls. The event flow "
            "(_process_event -> _finalize_job -> post-commit outbox) "
            "is the PRIMARY event path; a zero-call here means the "
            "flow didn't reach the outbox (mock surface miss)."
        )

        # Find the call whose first arg matches the mirror work_id.
        mirror_calls = [
            call for call in notify_calls if call.args[0] == mirror_jid
        ]
        assert len(mirror_calls) >= 1, (
            f"notify_watchers must have fired for mirror work_id="
            f"{mirror_jid[:8]}...; calls seen: "
            f"{[c.args[0][:8] for c in notify_calls]}"
        )

        # THE PIN: mirror work_id → ``settled`` (per-kind dispatch),
        # NOT ``completed`` (the legacy hardcoded text).
        mirror_status = mirror_calls[0].args[1]
        assert mirror_status == "settled", (
            f"N8: mirror work_id settling via the PRIMARY event path "
            f"must render the per-kind 'settled' token (M3 mirror-"
            f"receipt vocabulary); got {mirror_status!r}. The "
            f"pre-fix bug hardcoded 'completed' for every candidate "
            f"work_id, collapsing mirror rows onto the task-side "
            f"glyph and breaking the per-kind parser contract."
        )

    @pytest.mark.asyncio
    async def test_task_work_id_renders_completed_via_resolver_helper(
        self,
    ) -> None:
        """A Task-backed work_id resolves to ``status="completed"``
        via ``WorkResolverService.per_kind_status_for`` — the
        work-outcome vocabulary is unchanged for task rows (a task
        job IS its own mission).

        Negative control for the per-kind dispatch's specificity:
        the resolver must NOT introduce a new per-kind split on
        task rows; ``completed`` stays ``completed``. Protects
        against future regressions that would silently rename
        ``completed`` on the task side and break the orchestrator's
        parser contract on task rows.

        Driving the full post-commit outbox with a Task-only
        candidate set requires mocking ``inst_tasks`` correctly —
        simpler to drive the resolver helper directly and assert
        the Task row's derived status. The existing
        ``tests/unit/services/test_work_resolver.py:1784`` pin
        covers the wire-text assertion on the Task side; this pin
        covers the helper's contract on Task rows.
        """
        task_wid = f"task-n8-{uuid.uuid4().hex[:8]}"

        # Build a fresh one with the same Task-side mock.
        task_repo_mock = MagicMock()
        task_mock = MagicMock()
        task_mock.work_id = task_wid
        task_mock.status = "completed"
        task_mock.instance_id = "inst-x"
        task_mock.error = None
        task_mock.result = None
        task_repo_mock.get_by_work_id = MagicMock(return_value=task_mock)

        resolver = WorkResolverService(
            task_repo_mock,
            MagicMock(spec=JobRepository),
            MagicMock(),
        )
        derived = resolver.per_kind_status_for(
            task_wid, default="completed"
        )
        assert derived == "completed", (
            f"N8 negative control: Task-backed work_id must resolve "
            f"to 'completed' (no per-kind split on task rows — a "
            f"task job IS its own mission); got {derived!r}"
        )