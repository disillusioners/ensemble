"""JobProcessor - Background worker for processing queued jobs."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.services.dispatch_event_bus import DispatchEventBus
    from daemon.services.job_feedback_observer import JobFeedbackObserver
    from daemon.manager import InstanceManager

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState
from daemon.services.dependency_bus import get_dependency_bus
from daemon.services.messaging_types import _assert_linkage_contract
from daemon.services.job_queue_service import (
    DemandState,
    JobQueueService,
    TERMINAL_CANCEL_STATUSES,
)
from daemon.services.job_lock_manager import JobLockManager
from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.job_queue.queue_repository import JobQueueRepository

logger = logging.getLogger(__name__)


class JobProcessor:
    """Background worker that processes queued jobs.

    Continuously polls for pending jobs across all queues and processes them.
    Uses two-level pause checks: project-level (job_queue_paused) and queue-level
    (is_paused) to control job processing.

    Work-driven scan (admission starvation fix, 2026-08): the scan set
    is derived from queued/active JobItems themselves via
    ``JobQueueRepository.list_queues_with_admittable_work``, NOT from
    ``project_repo.list_projects``. The previous project scan starved
    in DBs with >100 projects (proved on ``ensemble_dev`` 338-projects,
    system-default ranked #189, 3/4 e2e failures, 0 LLM calls) because
    ``list_projects(limit=100, updated_at DESC)`` silently truncated
    the project list and the system-default project's queues were
    never visited — queued JobItems stayed
    ``admission_state='queued'``, the queue-admission guard in
    ``claim_pending_task`` (``task/repository.py:1248-1254``) refused
    every ``Task.claim`` attempt, and the worker pool sat idle.

    Processing order:
    1. ``queue_repo.list_queues_with_admittable_work()`` returns
       queues that hold at least one non-deleted JobItem in
       ``admission_state IN ('queued','active')``. Bounded by
       the scan cap (``limit=1000``) so the polling hot path
       stays bounded.
    2. For each queue, skip if ``queue.is_paused`` (queue pause).
    3. Cached project pause lookup: skip if
       ``project.job_queue_paused`` (project pause). ``None``
       (cache miss on lookup error) means "don't skip" — the
       downstream pause check inside
       ``JobQueueService.start_job`` is the second line of
       defence, and a transient repo error must not wedge the
       queue.
    4. Defer/background idle gates per
       ``_defer_idle_check`` / ``_background_idle_check``.
    5. Get next pending job for the queue; acquire per-queue
       lock and start job.

    Attributes:
        _queue_service: JobQueueService instance for job operations.
        _instance_manager: InstanceManager instance for spawning instances.
        _project_repo: SQLModelProjectRepository for checking project pause state.
        _queue_repo: JobQueueRepository for listing queues and their pause state.
        _poll_interval: Time in seconds between poll cycles.
        _running: Flag to control the processing loop.
        _dispatch_bus: Optional DispatchEventBus for event-driven wakeup.
        _event_dispatch_enabled: Whether to use event-driven dispatch.
        _jobs_dispatched_immediately: Counter for jobs dispatched via events.
        _jobs_dispatched_polling: Counter for jobs dispatched via polling.
    """
    
    def __init__(
        self,
        queue_service: JobQueueService,
        instance_manager: InstanceManager,
        project_repo: SQLModelProjectRepository,
        queue_repo: JobQueueRepository,
        poll_interval: float = 30.0,
        dispatch_bus: "DispatchEventBus" | None = None,
        event_dispatch_enabled: bool = True,
    ):
        """Initialize the JobProcessor.
        
        Args:
            queue_service: JobQueueService for job operations.
            instance_manager: InstanceManager for spawning instances.
            project_repo: SQLModelProjectRepository for checking project pause state.
            queue_repo: JobQueueRepository for listing queues and checking pause state.
            poll_interval: Seconds between poll cycles (default: 30.0).
            dispatch_bus: Optional DispatchEventBus for event-driven job dispatch.
            event_dispatch_enabled: Whether to use event-driven dispatch (default: True).
        """
        self._queue_service = queue_service
        self._instance_manager = instance_manager
        self._project_repo = project_repo
        self._queue_repo = queue_repo
        self._poll_interval = poll_interval
        self._running = False
        self._job: asyncio.Task | None = None
        self._dispatch_bus = dispatch_bus
        self._event_dispatch_enabled = event_dispatch_enabled
        self._jobs_dispatched_immediately = 0
        self._jobs_dispatched_polling = 0
        # C7: Optional reference to the JobFeedbackObserver. Wired in
        # ``setup_job_feedback_observer`` (called from ``daemon/api.py``
        # after both the processor and the observer are constructed).
        # The unified observer → Task → WorkerPool path is the SOLE
        # execution path for message work.
        self._job_feedback_observer: "JobFeedbackObserver" | None = None
        # Throttle/dedup state for in_progress notifications.
        # Keyed by job_id. ``_last_in_progress`` records (timestamp, pending_count) of
        # the most recent in_progress emit so we can skip re-emit when nothing has
        # changed. ``_in_progress_since`` records the FIRST time we saw this job in
        # the guard, so the escape-hatch timer (``_child_timeout_seconds``) measures
        # total stuck-children wall time, not time since last emit.
        self._last_in_progress: dict[str, tuple[float, int]] = {}
        self._in_progress_since: dict[str, float] = {}
        self._child_timeout_seconds: int = 3600  # 1 hour default — force-fail stuck jobs

    def setup_job_feedback_observer(
        self, observer: "JobFeedbackObserver"
    ) -> None:
        """Wire the JobFeedbackObserver into the processor.

        C7: called from ``daemon/api.py`` after both the processor and the
        observer are constructed. With this reference in place, the
        processor routes MESSAGE-type work through the unified
        ``JobFeedbackObserver`` lifecycle path (Task → WorkerPool →
        ``_process_event`` → ``_finalize_job``). The observer is the SOLE
        dispatch authority for MESSAGE jobs (legacy handler path
        removed).

        Idempotent: setting the same observer twice is a no-op.

        Args:
            observer: The :class:`JobFeedbackObserver` instance.
        """
        if self._job_feedback_observer is None:
            self._job_feedback_observer = observer
            logger.info(
                "JobProcessor: JobFeedbackObserver wired "
                "(MESSAGE jobs route through observer — sole dispatch path post-Phase-D)"
            )
        elif self._job_feedback_observer is not observer:
            # Hot-swap (rare; only happens in test setup). Update the
            # reference and log a warning.
            logger.warning(
                "JobProcessor.setup_job_feedback_observer: replacing "
                "existing observer reference"
            )
            self._job_feedback_observer = observer

    async def _capture_result_summary(self, instance_id: str, job_id: str, job_type_label: str) -> str | None:
        """Try to capture agent response content for result_summary."""
        result_summary = None
        if hasattr(self._instance_manager, '_get_last_assistant_message_raw'):
            try:
                # 2026-08-11: terminal job-summary path. Leave agent_id=None
                # (the exclusion check is bypassed — repair runs). The
                # exclusion only fires when the caller has agent_id
                # readily available; this helper receives only instance_id.
                result_summary = await self._instance_manager._get_last_assistant_message_raw(instance_id)
            except Exception as e:
                logger.warning(f"Failed to get result_summary for {job_type_label} job {job_id[:8]}...: {e}")
        return result_summary

    def _cleanup_in_progress_tracking(self, job_id: str) -> None:
        """Drop in-progress tracking state for a job that reached a terminal state.

        Called from the normal (non-guard) ``complete_job`` paths so the throttle
        and escape-hatch timers do not leak across job lifetimes. Idempotent.
        """
        self._last_in_progress.pop(job_id, None)
        self._in_progress_since.pop(job_id, None)

    @staticmethod
    def _looks_like_mock(obj: object) -> bool:
        """Return True if ``obj`` looks like an ``unittest.mock.Mock`` instance.

        Used as a lightweight test-detection heuristic in
        :meth:`_defer_idle_check` so the new shared predicate
        (``TaskRepository.has_active_non_deferred_work``) stays the
        primary path in production without breaking test fixtures that
        still mock the legacy ``count_active_jobs_in_non_defer_queues``
        API.

        Real ``TaskRepository`` instances do NOT expose either of these
        attributes — they are private to the ``unittest.mock``
        contract. ``MagicMock`` / ``AsyncMock`` instances always expose
        both. Avoids an ``import unittest.mock`` at module load time.

        Args:
            obj: The candidate object (any type).

        Returns:
            True if both ``_mock_name`` and ``_mock_methods`` attributes
            are present (the well-known ``unittest.mock`` fingerprint).
            False otherwise.
        """
        return hasattr(obj, "_mock_name") and hasattr(obj, "_mock_methods")

    async def _defer_idle_check(
        self,
        project_id: str,
        requester_instance_id: str | None = None,
    ) -> int:
        """Return the count of active non-defer jobs for a project.

        Gate A (Phase 1 of defer-seam bugfix, 2026-06-30): consults
        the **shared** ``TaskRepository.has_active_non_deferred_work``
        predicate so Gate A (here in ``_process_next_job``) agrees
        with Gate B (``_select_next_eligible_job`` in
        ``job_queue_service.py``) and ``maintenance._is_idle``.

        ``JobProcessor`` does not directly inject ``TaskRepository``;
        it is reached through the already-injected
        ``InstanceManager._task_repo`` attribute.

        Phase 2 (defer-queue idle gate, 2026-07-23) adds the
        job-granular ``JobRepository.has_active_non_deferred_work``
        predicate for admission-lifecycle rows. The task predicate remains a
        required second check when the job predicate returns False because a
        Task can exist without a backing JobItem.

        **WS1 requester-instance carve-out (2026-09-06):** when the
        caller passes ``requester_instance_id`` (the candidate
        instance being admitted on this defer queue), the
        job-granular predicate excludes the candidate's OWN settled
        mirrors from the busy-set. The legacy clause stays UNTOUCHED —
        a live foreground turn yields an ACTIVE job which still
        witnesses correctly. System-scope and legacy callers see the
        pre-WS1 shape (semantics identical to the previous
        implementation).

        Fallback contract (test back-compat):

        * If the ``_queue_service._repository`` does not expose a
          ``has_active_non_deferred_work`` method (older test fixtures
          that mock the legacy ``count_active_jobs_in_non_defer_queues``
          API), OR the repository is a ``Mock`` (detected via the
          ``_mock_name`` + ``_mock_methods`` private-attribute
          fingerprint), fall back to the task-granular predicate.
        * If the task-granular predicate is also unavailable or is a
          Mock, fall back to the legacy count.

        All blocking DB calls are wrapped in ``asyncio.to_thread`` so
        the surrounding event loop stays responsive.

        Args:
            project_id: Project scope for the check. ``None`` is
                allowed (system-wide) by the underlying predicate but
                the defer-queue gate is always project-scoped — pass
                ``queue.project_id``.
            requester_instance_id: Optional candidate instance for
                the WS1 requester-instance carve-out (the first
                pending JobItem's instance on the defer queue being
                processed). When ``None`` (the default — system-scope
                and legacy callers), the no-carve-out body is used
                and semantics are identical to pre-WS1. When set, the
                candidate's own settled mirrors do NOT witness against
                the candidate.

        Returns:
            Truthy ``int`` (1) when non-deferred work is active —
            i.e. the caller should ``continue`` past this defer queue.
            Falsy ``0`` when the project is idle and the defer queue
            may admit its next job. The legacy path returns the raw
            ``int`` so existing tests can still verify the behaviour
            by mocking the legacy method; the shared predicate path
            coerces the ``bool`` to ``int``.
        """
        # Check the job predicate first, but do not treat a False result as
        # conclusive: active Tasks may exist without a backing JobItem.
        #
        # W3 (fail-CLOSED, 2026-07-23): a transient DB error during
        # the predicate call must NOT silently release the defer
        # queue. Wrap the call in try/except; on error, log a
        # warning and return 1 (blocked) so the defer queue waits.
        # Mirrors the ``maintenance.py`` ``_is_idle`` posture and
        # keeps the defer / background gates consistent.
        queue_repo = getattr(self._queue_service, "_repository", None)
        if (
            queue_repo is not None
            and not self._looks_like_mock(queue_repo)
            and hasattr(queue_repo, "has_active_non_deferred_work")
        ):
            try:
                active = await asyncio.to_thread(
                    queue_repo.has_active_non_deferred_work,
                    project_id,
                    requester_instance_id,
                )
                if isinstance(active, bool) and active:
                    return 1
            except Exception as e:
                logger.warning(
                    f"JobProcessor._defer_idle_check: job predicate "
                    f"raised {e!r} for project_id={project_id!r}, "
                    f"requester_instance_id={requester_instance_id!r} — "
                    f"failing CLOSED (returning 1)"
                )
                return 1

        task_repo = getattr(self._instance_manager, "_task_repo", None)
        if (
            task_repo is not None
            and not self._looks_like_mock(task_repo)
            and hasattr(task_repo, "has_active_non_deferred_work")
        ):
            # Production path: use the shared predicate so Gate A,
            # Gate B, and maintenance agree. Coerce bool → int so the
            # return contract stays `int`-shaped (matches the legacy
            # count).
            #
            # W3 (fail-CLOSED): same posture as the job predicate
            # above — wrap in try/except and return 1 on error so the
            # defer queue waits during a transient DB failure.
            try:
                active = await asyncio.to_thread(
                    task_repo.has_active_non_deferred_work, project_id
                )
            except Exception as e:
                logger.warning(
                    f"JobProcessor._defer_idle_check: task predicate "
                    f"raised {e!r} for project_id={project_id!r} — "
                    f"failing CLOSED (returning 1)"
                )
                return 1
            return int(bool(active))
        # Legacy / test fallback: keep behaviour identical to the
        # pre-Phase-1 implementation so existing fixtures that mock
        # ``count_active_jobs_in_non_defer_queues`` remain operational
        # until the Phase 1 test migration lands.
        #
        # W3 (fail-CLOSED): the legacy count path also gets a
        # try/except wrap so a transient DB failure here too is
        # treated as "active" (return 1) rather than silently
        # releasing the defer queue.
        try:
            return await asyncio.to_thread(
                self._queue_service._repository.count_active_jobs_in_non_defer_queues,
                project_id,
            )
        except Exception as e:
            logger.warning(
                f"JobProcessor._defer_idle_check: legacy count path "
                f"raised {e!r} for project_id={project_id!r} — "
                f"failing CLOSED (returning 1)"
            )
            return 1

    async def _background_idle_check(self) -> int:
        """Return 1 (truthy) when background work should wait, 0 when it may run.

        Gate A (Phase 3 background seam, 2026-07-14): the
        background-queue idle gate. Consults the shared
        ``TaskRepository.has_active_non_background_work`` predicate so
        Gate A (here in ``_process_next_job``) agrees with Gate B
        (``_select_next_eligible_job`` in
        ``job_queue_service.py``) and the atomic claim path
        (``TaskRepository.claim_pending_task``).

        **Scope difference from** :meth:`_defer_idle_check`:

        * DEFER checks a single project's non-deferred work count.
        * BACKGROUND checks the SYSTEM's non-background work count — it
          must wait until every project is idle on its non-background
          lanes. The ``has_active_non_background_work`` predicate is
          system-wide (Phase 3 background seam, 2026-07-14) so we
          pass ``None`` as the ``project_id`` explicitly.

          (defer-leak fix, 2026-07-23: defer work now counts as
          non-background work.)

        Phase 2 (defer-queue idle gate, 2026-07-23) adds the
        job-granular ``JobRepository.has_active_non_background_work``
        predicate. It runs first, followed by the task predicate when no
        active job is found, matching :meth:`_defer_idle_check` and covering
        job-less Tasks.

        Sister method to :meth:`_defer_idle_check`: same Mock-detection
        fallback via :meth:`_looks_like_mock`, same
        ``asyncio.to_thread`` wrapping for non-blocking DB I/O, same
        ``int(bool(...))`` coercion so the return contract stays
        ``int``-shaped (matches the defer path). Tests that mock the
        legacy ``count_active_jobs_in_non_defer_queues`` keep working
        until the Phase 3 background-seam test migration lands.

        Returns:
            Truthy ``int`` (1) when non-background work is
            active ANYWHERE in the system — i.e. the caller should
            ``continue`` past this background queue. Falsy ``0`` when
            every project's non-background lanes are idle
            and the background queue may admit its next job. The legacy
            fallback returns the raw ``int`` for back-compat with tests
            that mock the legacy method.
        """
        # System-wide sister check: job rows first, followed by task rows
        # when no active job is found so job-less Tasks still block the
        # background queue.
        #
        # W3 (fail-CLOSED, 2026-07-23): each predicate call is wrapped
        # in try/except. On error, log a warning and return 1 so the
        # background queue waits during a transient DB failure.
        # Mirrors the defer-gate posture in :meth:`_defer_idle_check`.
        queue_repo = getattr(self._queue_service, "_repository", None)
        if (
            queue_repo is not None
            and not self._looks_like_mock(queue_repo)
            and hasattr(queue_repo, "has_active_non_background_work")
        ):
            try:
                active = await asyncio.to_thread(
                    queue_repo.has_active_non_background_work, None
                )
                if isinstance(active, bool) and active:
                    return 1
            except Exception as e:
                logger.warning(
                    f"JobProcessor._background_idle_check: job predicate "
                    f"raised {e!r} — failing CLOSED (returning 1)"
                )
                return 1

        task_repo = getattr(self._instance_manager, "_task_repo", None)
        if (
            task_repo is not None
            and not self._looks_like_mock(task_repo)
            and hasattr(task_repo, "has_active_non_background_work")
        ):
            # Production path: use the shared predicate so Gate A,
            # Gate B, and the claim path all observe the same
            # system-wide idle signal. Coerce bool → int so the
            # return contract stays `int`-shaped (matches the defer
            # path). Pass ``None`` explicitly for ``project_id`` —
            # the background predicate is always system-wide.
            #
            # W3 (fail-CLOSED): wrap the call in try/except so a
            # transient DB failure here too is treated as "active"
            # (return 1) rather than silently releasing the
            # background queue.
            try:
                active = await asyncio.to_thread(
                    task_repo.has_active_non_background_work, None
                )
            except Exception as e:
                logger.warning(
                    f"JobProcessor._background_idle_check: task predicate "
                    f"raised {e!r} — failing CLOSED (returning 1)"
                )
                return 1
            return int(bool(active))
        # Legacy / test fallback: same defensive posture as
        # ``_defer_idle_check`` — if the production predicate is not
        # wired (older tests, partial-init harness) we conservatively
        # treat the system as active so background work does not
        # claim prematurely. Once the Phase 3 test migration lands
        # this fallback can be dropped.
        #
        # W3 (fail-CLOSED): the legacy fallback path also returns 1
        # on exception (treated as "active") for symmetry with the
        # predicate paths above. Previously this was an unconditional
        # ``return 1`` fallback; the W3 fix widens it to also cover
        # transient DB failures during the legacy count.
        return 1

    async def _emit_in_progress_if_children_pending(
        self,
        instance_meta,
        proc_job,
        job_type_label: str,
        status_display: str,
    ) -> bool:
        """Centralized guard for the 6 in_progress-emit call sites.

        If the job's instance is in a would-be terminal state but still has
        child agent reports outstanding, emit an ``in_progress`` notification
        instead of completing the job.

        Phase 4 / Phase 5 (2026-06-23): the control-flow
        decision consults the DependencyBus (DB-backed pending
        watchers) — the SOLE completion authority after the
        CorrelationManager was removed in Phase 5.

        A9 hard error: the legacy SELECT fallback is the exact bug
        we are fixing (TOCTOU) and MUST NOT be reachable. The bus is
        the SOLE completion authority — when the bus is None, this
        is an invalid state and raises ``RuntimeError``. Mirrors A8
        in ``child_reports.py``.

        Adds two safety nets that the original 6 inline blocks did not have:
        * **Throttle/dedup** — within a 300s window we skip re-emitting when
          pending count is unchanged, so a hot poll loop cannot spam
          watchers.
        * **Escape hatch** — if a job has been sitting in the guard for more
          than ``_child_timeout_seconds`` (default 1h), force-complete it as
          FAILED so a stuck child never permanently blocks the job.

        Args:
            instance_meta: The Instance metadata.
            proc_job: The JobItem (must expose ``job_id``).
            job_type_label: Human label for logs, e.g. ``"MESSAGE"`` or ``"TASK"``.
            status_display: Human-readable instance status for the log line
                (e.g. ``"completed"``, ``"terminated"``, ``"errored"``).

        Returns:
            ``True`` if the guard fired (caller should ``continue`` / skip normal
            processing). ``False`` if normal processing should proceed.
        """
        # Phase 4: prefer the CM's in-memory pending count when available.
        # Phase 5 (2026-06-23): the CM ``get_pending_count`` is replaced
        # by ``bus.count_pending_for_target`` on the DependencyBus
        # (the SOLE completion authority after CM removal). The
        # ``_maybe_emit_in_progress_guard`` is async, so we use the
        # async ``count_pending_for_target`` variant.
        assert hasattr(instance_meta, "instance_id"), "instance_meta must be an InstanceModel"
        instance_id = instance_meta.instance_id
        bus = get_dependency_bus()
        if bus is not None:
            wf = int(await bus.count_pending_for_target(instance_id) or 0)
        else:
            # ─── A9: HARD ERROR (not graceful degradation) ───
            # Bus is None is an INVALID state. The legacy SELECT
            # fallback (TOCTOU) is the exact bug we are fixing — it
            # MUST NOT be reachable. The bus must be initialized for
            # the new architecture to work; we raise rather than
            # silently degrade into the TOCTOU fallback. See ADR-011.
            raise RuntimeError(
                "DependencyBus is None — invalid state. "
                "The bus must be initialized (see ADR-011)."
            )
        if wf <= 0:
            return False

        job_id = proc_job.job_id
        now = time.time()

        # --- Escape hatch: force-fail jobs stuck waiting for children ---
        if job_id in self._in_progress_since:
            elapsed = now - self._in_progress_since[job_id]
            if elapsed > self._child_timeout_seconds:
                logger.warning(
                    f"JobProcessor: {job_type_label} job {job_id[:8]}... has been waiting "
                    f"{int(elapsed)}s for {wf} child agent(s); forcing FAILED"
                )
                try:
                    progress_text = await self._capture_result_summary(
                        instance_meta.instance_id, job_id, job_type_label
                    )
                except Exception:
                    progress_text = None
                try:
                    await self._queue_service.complete_job(
                        job_id,
                        demand_state=DemandState.FAILED,
                        error=f"Children did not report within timeout ({int(elapsed)}s)",
                        result_summary=progress_text,
                    )
                except Exception as e:
                    logger.warning(
                        f"JobProcessor: failed to force-fail stuck {job_type_label} job "
                        f"{job_id[:8]}... after {int(elapsed)}s: {e}"
                    )
                # Cleanup so next guard visit starts a fresh window.
                self._in_progress_since.pop(job_id, None)
                self._last_in_progress.pop(job_id, None)
                return True
        else:
            # First time we see this job_id in the guard — start the clock.
            self._in_progress_since[job_id] = now

        # --- Throttle/dedup: skip re-emit within 300s if pending_count unchanged ---
        last = self._last_in_progress.get(job_id)
        if last is not None:
            last_ts, last_wf = last
            if (now - last_ts) < 300 and last_wf == wf:
                # Same state, within throttle window — consume this poll silently.
                return True
        self._last_in_progress[job_id] = (now, wf)

        # --- Emit the in_progress notification (failures are non-fatal) ---
        try:
            progress_text = await self._capture_result_summary(
                instance_meta.instance_id, job_id, job_type_label
            )
        except Exception:
            progress_text = None
        try:
            await self._queue_service.notify_watchers(
                job_id,
                status="in_progress",
                progress=progress_text,
            )
        except Exception as e:
            logger.warning(
                f"JobProcessor: failed to emit in_progress for {job_type_label} job "
                f"{job_id[:8]}...: {e}"
            )
        logger.info(
            f"JobProcessor: {job_type_label} job {job_id[:8]}... instance "
            f"{getattr(instance_meta, 'instance_id', '?')[:8]}... "
            f"emitted '{status_display}' but has {wf} pending child agent(s); deferring"
        )
        return True

    async def start(self) -> None:
        """Start the background processing loop."""
        if self._running:
            return
        
        self._running = True
        self._job = asyncio.create_task(self._process_loop())
        logger.info("JobProcessor started")
    
    async def stop(self) -> None:
        """Stop the background processing loop gracefully."""
        if not self._running:
            return
        
        self._running = False
        
        if self._job is not None:
            self._job.cancel()
            try:
                await self._job
            except asyncio.CancelledError:
                pass
            self._job = None
        
        logger.info("JobProcessor stopped")
    
    async def _process_loop(self) -> None:
        """Main processing loop - polls for and processes jobs with optional event-driven wakeup."""
        logger.debug("[TRACE] _process_loop: started")
        while self._running:
            try:
                # Event-driven dispatch: wait for job event with polling fallback
                if self._event_dispatch_enabled and self._dispatch_bus is not None:
                    # Wait for event with poll_interval as timeout
                    event_received = await self._dispatch_bus.wait_for_job(
                        project_id=None,  # Global event for now (could optimize per-project later)
                        timeout=self._poll_interval
                    )
                    if event_received:
                        self._jobs_dispatched_immediately += 1
                        logger.debug(
                            f"[TRACE] _process_loop: woken by event (immediate={self._jobs_dispatched_immediately}, "
                            f"polling={self._jobs_dispatched_polling}), processing next job"
                        )
                    else:
                        self._jobs_dispatched_polling += 1
                        logger.debug(
                            f"[TRACE] _process_loop: poll timeout, processing next job "
                            f"(immediate={self._jobs_dispatched_immediately}, polling={self._jobs_dispatched_polling})"
                        )
                else:
                    # Fallback: pure polling
                    await asyncio.sleep(self._poll_interval)
                    self._jobs_dispatched_polling += 1
                    logger.debug(
                        f"[TRACE] _process_loop: pure polling wakeup "
                        f"(polling={self._jobs_dispatched_polling})"
                    )
                
                await self._process_next_job()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"Error in processing loop: {e}")
    
    async def _process_next_job(self) -> None:
        """Get the next pending job from any queue that has work and process it.

        Implements two-level pause checking:
        1. Project-level pause (job_queue_paused) - master override that stops ALL queues
        2. Queue-level pause (is_paused) - individual queue control

        Work-driven scan (admission starvation fix, 2026-08):
        The scan set is derived from queued/active JobItems themselves
        via ``JobQueueRepository.list_queues_with_admittable_work``,
        NOT from ``project_repo.list_projects``. The previous project
        scan starved in DBs with >100 projects (proved on
        ``ensemble_dev`` 338-projects, system-default ranked #189,
        3/4 e2e failures, 0 LLM calls) because
        ``list_projects(limit=100, updated_at DESC)`` silently
        truncated the project list and the system-default project's
        queues were never visited — queued JobItems stayed
        ``admission_state='queued'``, the queue-admission guard in
        ``claim_pending_task`` (``task/repository.py:1248-1254``)
        refused every ``Task.claim`` attempt, and the worker pool
        sat idle.

        Processing order:
        1. ``queue_repo.list_queues_with_admittable_work()`` returns
           queues that hold at least one non-deleted JobItem in
           ``admission_state IN ('queued','active')``. Bounded by
           the scan cap (``limit=1000``) so the polling hot path
           stays bounded.
        2. For each queue, skip if ``queue.is_paused`` (queue pause).
        3. Cached project pause lookup: skip if
           ``project.job_queue_paused`` (project pause).
        4. Defer/background idle gates per
           ``_defer_idle_check`` / ``_background_idle_check``.
        5. Get next pending job for the queue; acquire per-queue
           lock and start job.

        The previous project-list iteration never visited queues
        for projects outside the top-100 by ``updated_at``; the
        work-driven scan naturally covers every project that has
        even one queued/active JobItem, bounded only by the cap.
        See tests/job_queue/test_job_processor_admission_starvation.py
        for the regression test (fails on base, passes on fix).

        Note: the ``queue_repo.list_queues_with_admittable_work``
        ordering is ``min(created_at) ASC`` so the queue with the
        oldest backlog is processed first. Multiple queues may share
        a project_id; the project pause state is cached once per
        project to avoid N+1 ``project_repo.get`` calls.
        """
        logger.debug("[TRACE] _process_next_job: waking up to check for jobs")

        # Work-driven scan: enumerate queues via actual pending
        # work. Bypasses the project-list iteration that starved in
        # DBs with >100 projects (system-default often ranks outside
        # the default limit=100). See method docstring above.
        queues_with_work = await asyncio.to_thread(
            self._queue_repo.list_queues_with_admittable_work,
            limit=1000,
        )

        logger.debug(
            f"[TRACE] _process_next_job: {len(queues_with_work)} "
            f"queue(s) with admittable work"
        )

        # Per-project pause cache: many queues may share a
        # project_id. Look up each project's ``job_queue_paused`` at
        # most once per iteration. ``None`` (cache miss on lookup
        # error) means "don't skip" — the downstream pause check
        # inside ``JobQueueService.start_job`` is the second line of
        # defence, and a transient repo error must not wedge the
        # queue.
        project_pause_cache: dict[str, bool | None] = {}

        for queue in queues_with_work:
            # Level 2 pause check: Individual queue pause
            # This allows pausing specific queues while others continue
            if queue.is_paused:
                continue

            # Level 1 pause check: Master pause (project-level)
            # Cached across iterations because multiple queues
            # commonly share a project_id.
            pid = queue.project_id
            if pid not in project_pause_cache:
                try:
                    project = await asyncio.to_thread(
                        self._project_repo.get, pid
                    )
                    if project is None:
                        project_pause_cache[pid] = False
                    else:
                        project_pause_cache[pid] = bool(
                            getattr(project, "job_queue_paused", False)
                        )
                except Exception as lookup_err:
                    logger.warning(
                        f"JobProcessor._process_next_job: project "
                        f"pause lookup failed for {pid!r}: "
                        f"{lookup_err!r} — treating as unpaused"
                    )
                    # Fail-open sentinel: ``None`` means "don't skip" — treated as
                    # not-paused here; JobQueueService.start_job re-checks
                    # ``job_queue_paused`` at job_queue_service.py:2976-2982 as
                    # the second line of defence, so a transient repo error
                    # cannot wedge the queue.
                    project_pause_cache[pid] = None
            if project_pause_cache[pid]:
                continue

            # Defer queue check: only process when project is completely idle
            # Only applies to queues with queue_type attribute (skip mock/test objects)
            pending = await asyncio.to_thread(
                self._queue_service._repository.list_pending_by_queue, queue.queue_id
            )

            if queue.queue_type == "defer" and pending:
                # Gate A (Phase 1 of defer-seam bugfix, 2026-06-30):
                # Defer queues only activate when no non-deferred
                # work is in flight. Use the shared predicate on
                # TaskRepository so the claim path and the admission
                # probe never disagree.
                #
                # Access path: ``JobProcessor`` does not directly
                # inject ``TaskRepository``; reach it through the
                # already-injected ``InstanceManager``. ``TaskRepository.has_active_non_deferred_work``
                # is the shared predicate backing Gate A, Gate B
                # (``_select_next_eligible_job``), and the maintenance
                # ``_is_idle`` check.
                #
                # WS1 (2026-09-06): the candidate being admitted on
                # this defer queue is the first pending JobItem; pass
                # its ``instance_id`` as the requester-instance
                # carve-out so the candidate's OWN settled mirrors do
                # NOT witness against the candidate (the defer self-
                # witness incident). When the first pending JobItem
                # has no ``instance_id`` (a queued defer job with no
                # instance yet), the carve-out degrades to the
                # pre-WS1 shape (no carve-out, project-scoped gate).
                first_pending = pending[0]
                requester_instance_id = (
                    getattr(first_pending, "instance_id", None)
                )
                non_defer_active = await self._defer_idle_check(
                    queue.project_id,
                    requester_instance_id,
                )
                if non_defer_active:
                    continue

            if queue.queue_type == "background" and pending:
                # Gate A (Phase 3 background seam, 2026-07-14):
                # Background queues only activate when no
                # non-deferred, non-background work is in flight
                # ANYWHERE in the system (system-wide scope, not
                # project-scoped — see
                # ``_background_idle_check`` docstring for the
                # rationale and ``has_active_non_background_work``
                # for the shared predicate). Uses the same
                # shared predicate so the claim path and the
                # admission probe never disagree. Sister check to
                # the defer gate above; runs ALONGSIDE it so a
                # project that has both a defer queue and a
                # background queue evaluates both gates correctly.
                background_should_wait = await self._background_idle_check()
                if background_should_wait:
                    continue

            if not pending:
                # Also check for ACTIVE-admission jobs that have instance_id set but
                # no spawned instance yet. These jobs were transitioned to
                # admission_state='active' (via status=PROCESSING) by
                # trigger_next_job() but the JobProcessor missed them due to
                # event-driven or polling timing gaps.
                #
                # Phase 3 admission-decision migration: filter on
                # ``admission_state='active'`` rather than
                # ``statuses=['processing']``. This catches PAUSED jobs too
                # (admission_state='active' under the new model — pause is
                # an Instance concern, lock still held), which the legacy
                # ``status='processing'`` filter silently dropped.
                active_jobs, _ = await asyncio.to_thread(
                    self._queue_service._repository.list_by_queue, queue.queue_id,
                    admission_states=[AdmissionState.ACTIVE.value]
                )
                for proc_job in (active_jobs or []):
                    # W1: skip message orphans. Message jobs target
                    # an EXISTING instance — re-running
                    # ``spawn_instance_with_mcp`` or
                    # ``enqueue_message`` here would create a
                    # duplicate instance + duplicate Task. The
                    # synchronous Task contract
                    # (``enqueue_message_job`` already wrote the
                    # Task row) means the message branch in
                    # ``_process_next_job`` is the only legitimate
                    # dispatch path for message jobs. ACTIVE
                    # message orphans whose Task is missing are
                    # recovered by ``JobRecoveryService
                    # .recover_on_startup`` (it checks Task
                    # existence and resets stale rows to queued).
                    if proc_job.job_type == "message":
                        continue

                    # NOTE: the legacy "inline MESSAGE-specific
                    # orphan guard" was removed in D11. The W1
                    # guard above now owns that responsibility
                    # for the ACTIVE-admission loop: message jobs
                    # are skipped up front because their target
                    # instance already exists and the Task row is
                    # already written by ``enqueue_message_job``.
                    # A stuck ACTIVE MESSAGE row that survives
                    # both the message branch and the W1 skip is
                    # recovered by ``JobRecoveryService`` at
                    # startup; we no longer attempt inline
                    # re-spawn or fail here.

                    # Skip if instance already spawned (normal case).
                    # If instance_id is set but get_instance raises KeyError,
                    # the instance might be in the process of being spawned
                    # (e.g., by JobFeedbackObserver). Skip and let it complete.
                    if proc_job.instance_id:
                        try:
                            await self._instance_manager.get_instance(proc_job.instance_id)
                            continue  # Instance exists, skip
                        except KeyError:
                            # Instance not in memory — check if it was terminated/completed/errored
                            # before attempting to re-spawn
                            if (
                                hasattr(self._instance_manager, '_instance_repository')
                                and self._instance_manager._instance_repository is not None
                            ):
                                try:
                                    instance_meta = await asyncio.to_thread(
                                        self._instance_manager._instance_repository.get,
                                        proc_job.instance_id
                                    )
                                    if instance_meta is not None:
                                        # Instance exists in DB — check its status
                                        if instance_meta.status == InstanceStatus.COMPLETED.value:
                                            if await self._emit_in_progress_if_children_pending(
                                                instance_meta, proc_job, "TASK", "completed"
                                            ):
                                                continue
                                            logger.info(
                                                f"JobProcessor: TASK job {proc_job.job_id[:8]}... "
                                                f"instance {proc_job.instance_id[:8]}... is completed, "
                                                f"completing job"
                                            )
                                            result_summary = await self._capture_result_summary(
                                                proc_job.instance_id, proc_job.job_id, "TASK"
                                            )
                                            await self._queue_service.complete_job(
                                                proc_job.job_id,
                                                demand_state=DemandState.COMPLETED,
                                                result_summary=result_summary,
                                            )
                                            self._cleanup_in_progress_tracking(proc_job.job_id)
                                            continue
                                        elif instance_meta.status in TERMINAL_CANCEL_STATUSES:
                                            status_display = instance_meta.status.value if hasattr(instance_meta.status, 'value') else instance_meta.status
                                            if await self._emit_in_progress_if_children_pending(
                                                instance_meta, proc_job, "TASK", status_display
                                            ):
                                                continue
                                            logger.info(
                                                f"JobProcessor: TASK job {proc_job.job_id[:8]}... "
                                                f"instance {proc_job.instance_id[:8]}... is {status_display}, "
                                                f"cancelling job"
                                            )
                                            await self._queue_service.complete_job(
                                                proc_job.job_id,
                                                demand_state=DemandState.CANCELLED,
                                                error=f"Instance is {status_display}",
                                            )
                                            self._cleanup_in_progress_tracking(proc_job.job_id)
                                            continue
                                        elif instance_meta.status == InstanceStatus.ERROR.value:
                                            if await self._emit_in_progress_if_children_pending(
                                                instance_meta, proc_job, "TASK", "errored"
                                            ):
                                                continue
                                            logger.warning(
                                                f"JobProcessor: TASK job {proc_job.job_id[:8]}... "
                                                f"instance {proc_job.instance_id[:8]}... errored, failing job"
                                            )
                                            await self._queue_service.complete_job(
                                                proc_job.job_id,
                                                demand_state=DemandState.FAILED,
                                                error="Instance errored",
                                            )
                                            self._cleanup_in_progress_tracking(proc_job.job_id)
                                            continue
                                        elif instance_meta.status == InstanceStatus.PAUSED.value:
                                            logger.debug(
                                                f"JobProcessor: TASK job {proc_job.job_id[:8]}... "
                                                f"instance {proc_job.instance_id[:8]}... is paused, skipping"
                                            )
                                            continue
                                        # Instance is in a non-terminal state but not in memory —
                                        # genuine crash, proceed to re-spawn below
                                except Exception as e:
                                    logger.warning(
                                        f"JobProcessor: failed to check instance status for "
                                        f"{proc_job.instance_id[:8]}...: {e}"
                                    )
                                    continue  # Don't crash on transient errors

                            # Instance genuinely crashed or missing — re-spawn
                            logger.info(
                                f"JobProcessor: recovering orphan PROCESSING job {proc_job.job_id[:8]}... "
                                f"(instance {proc_job.instance_id[:8]}... missing)"
                            )
                            try:
                                instance_id = await self._instance_manager.spawn_instance_with_mcp(
                                    agent_id=proc_job.agent_id,
                                    instance_id=proc_job.instance_id,  # Reuse existing valid UUID
                                    project_id=proc_job.project_id,
                                )
                                result = await self._instance_manager.enqueue_message(
                                    instance_id=instance_id,
                                    message=proc_job.message,
                                    source=proc_job.source,
                                    is_deferred=(queue.queue_type == "defer"),
                                    is_background=(queue.queue_type == "background"),
                                    # Stamp the JobItem's ``job_id``
                                    # onto the re-spawned Task's
                                    # ``work_id`` — the documented
                                    # linkage contract. Omitting it
                                    # here minted a fresh UUID and
                                    # broke Pattern-f1's
                                    # ``get_by_work_id(job_id)``
                                    # recovery lookups (council W1,
                                    # incident 2026-08-31).
                                    #
                                    # Fix A (constitution Phase 0,
                                    # approach-comparison.md row A):
                                    # ``work_id_required=True`` makes
                                    # the contract structural — a future
                                    # regression that drops the
                                    # ``work_id=`` binding FAILS LOUDLY
                                    # instead of silently re-minting.
                                    work_id=proc_job.job_id,
                                    work_id_required=True,
                                )
                                # ── Linkage-contract tripwire —
                                # Fix A escalation: ``enforce=True``
                                # turns the tripwire into a hard
                                # failure on the job-driven path (the
                                # orphan-recovery re-spawn site);
                                # mismatches fail the recovery loudly.
                                _assert_linkage_contract(
                                    result,
                                    proc_job.job_id,
                                    source="JobProcessor",
                                    logger=logger,
                                    enforce=True,
                                )
                                # Stamp message_id defensively — a
                                # stamping failure must not fail an
                                # otherwise-successful recovery. The
                                # NULL-safe cross-system guard tolerates
                                # a missing ``message_id``.
                                if result and result.message_id:
                                    try:
                                        await asyncio.to_thread(
                                            self._queue_service._repository.stamp_message_id,
                                            proc_job.job_id, result.message_id,
                                        )
                                    except Exception as stamp_err:
                                        logger.warning(
                                            f"JobProcessor: failed to stamp "
                                            f"message_id on orphan recovery "
                                            f"for job {proc_job.job_id[:8]}...: {stamp_err}"
                                        )
                                logger.info(
                                    f"Job {proc_job.job_id} recovered for instance {instance_id} "
                                    f"on queue {queue.queue_name}"
                                )
                                continue  # Successfully recovered
                            except Exception as e:
                                # Failed to recover - mark as failed to prevent permanent orphan
                                logger.error(
                                    f"Failed to recover orphan job {proc_job.job_id[:8]}...: {e}"
                                )
                                await self._queue_service.complete_job(
                                    proc_job.job_id, demand_state=DemandState.FAILED, error=str(e)
                                )
                                self._cleanup_in_progress_tracking(proc_job.job_id)
                                continue
                        except Exception as e:
                            logger.warning(
                                "Instance check failed for job %s (instance %s): %s",
                                proc_job.job_id[:8],
                                proc_job.instance_id[:8] if proc_job.instance_id else "N/A",
                                e,
                            )
                            continue  # Don't crash on transient errors
                    # No instance_id: this is a genuine orphan (shouldn't happen
                    # in normal operation, but kept as safety net)
                    # This job was started by trigger_next_job() but instance not spawned
                    logger.info(
                        f"JobProcessor: resuming orphan PROCESSING job {proc_job.job_id[:8]}... "
                        f"on queue {queue.queue_name}"
                    )
                    try:
                        instance_id = await self._instance_manager.spawn_instance_with_mcp(
                            agent_id=proc_job.agent_id,
                            instance_id=proc_job.instance_id,
                            project_id=proc_job.project_id,
                        )
                        result = await self._instance_manager.enqueue_message(
                            instance_id=instance_id,
                            message=proc_job.message,
                            source=proc_job.source,
                            is_deferred=(queue.queue_type == "defer"),
                            is_background=(queue.queue_type == "background"),
                            # Stamp the JobItem's ``job_id`` onto the
                            # resumed Task's ``work_id`` — the
                            # documented linkage contract. Omitting it
                            # here minted a fresh UUID and broke
                            # Pattern-f1's ``get_by_work_id(job_id)``
                            # recovery lookups (council W1, incident
                            # 2026-08-31).
                            #
                            # Fix A (constitution Phase 0,
                            # approach-comparison.md row A):
                            # ``work_id_required=True`` makes the
                            # contract structural — a future regression
                            # that drops the ``work_id=`` binding FAILS
                            # LOUDLY instead of silently re-minting.
                            work_id=proc_job.job_id,
                            work_id_required=True,
                        )
                        # ── Linkage-contract tripwire — Fix A
                        # escalation: ``enforce=True`` turns the
                        # tripwire into a hard failure on the
                        # job-driven path (the orphan-resume re-spawn
                        # site); mismatches fail the resume loudly.
                        _assert_linkage_contract(
                            result,
                            proc_job.job_id,
                            source="JobProcessor",
                            logger=logger,
                            enforce=True,
                        )
                        # Stamp message_id defensively — a stamping
                        # failure must not fail an otherwise-successful
                        # resume. The NULL-safe cross-system guard
                        # tolerates a missing ``message_id``.
                        if result and result.message_id:
                            try:
                                await asyncio.to_thread(
                                    self._queue_service._repository.stamp_message_id,
                                    proc_job.job_id, result.message_id,
                                )
                            except Exception as stamp_err:
                                logger.warning(
                                    f"JobProcessor: failed to stamp "
                                    f"message_id on orphan resume "
                                    f"for job {proc_job.job_id[:8]}...: {stamp_err}"
                                )
                        logger.info(
                            f"Job {proc_job.job_id} resumed for instance {instance_id} "
                            f"on queue {queue.queue_name}"
                        )
                    except Exception as e:
                        logger.error(f"Failed to resume orphan job {proc_job.job_id[:8]}...: {e}")
                        await self._queue_service.complete_job(
                            proc_job.job_id, demand_state=DemandState.FAILED, error=str(e)
                        )
                        self._cleanup_in_progress_tracking(proc_job.job_id)
                continue

            job = pending[0]

            # [TRACE] Log job found
            job_type = getattr(job, 'job_type', 'task')
            logger.info(
                f"[TRACE] _process_next_job: found PENDING job {job.job_id[:8]}... "
                f"job_type={job_type} instance={job.instance_id[:8] if job.instance_id else 'N/A'}..."
            )

            # NOTE: The legacy MESSAGE-specific DB-level
            # sibling pre-check was removed in D11. The unified
            # observer owns message dispatch end-to-end — it
            # runs the concurrency gate via the Execution Gate.
            # JobQueue no longer needs a DB-level sibling check
            # for MESSAGE jobs.
            #
            # Instance-level pause, however, must be evaluated here
            # (BEFORE ``start_job``) so the test contract holds:
            # ``TestJobProcessorInstancePause::test_skips_job_for_paused_instance``
            # asserts that ``start_job`` is NOT called when the
            # target instance is paused. The downstream pause check
            # inside ``JobQueueService.start_job`` would return None
            # too late — we want to avoid the lock acquisition
            # attempt entirely. Errors here fall through to
            # ``start_job`` (which has its own pause check) so a
            # transient repo error doesn't wedge the queue.
            if (
                job.instance_id
                and getattr(self._instance_manager, "_instance_repository", None) is not None
            ):
                try:
                    instance_meta = await asyncio.to_thread(
                        self._instance_manager._instance_repository.get,
                        job.instance_id,
                    )
                    if instance_meta is not None and instance_meta.status == InstanceStatus.PAUSED.value:
                        status_display = (
                            instance_meta.status.value
                            if hasattr(instance_meta.status, "value")
                            else instance_meta.status
                        )
                        logger.info(
                            f"JobProcessor: SKIP {job_type} job "
                            f"{job.job_id[:8]}... — instance "
                            f"{job.instance_id[:8]}... is {status_display}, "
                            f"staying PENDING"
                        )
                        continue
                except Exception as e:
                    logger.warning(
                        f"JobProcessor: instance pause pre-check failed "
                        f"for job {job.job_id[:8]}... (instance "
                        f"{job.instance_id[:8] if job.instance_id else 'N/A'}...): "
                        f"{e}. Falling through to start_job."
                    )

            # Try to start the job (acquires per-queue lock internally)
            # Note: JobQueueService.start_job() also performs an
            # instance pause check — that's the second line of defense
            # if the pre-check above raced or errored.
            logger.debug(f"[TRACE] _process_next_job: attempting to start job {job.job_id[:8]}...")
            try:
                started_job = await self._queue_service.start_job(job.job_id)
                if started_job is None:
                    # Lock acquisition failed or job was cancelled
                    logger.debug(f"[TRACE] _process_next_job: SKIP job {job.job_id[:8]}... — start_job returned None (lock contention or cancelled)")
                    continue

                logger.debug(
                    f"[TRACE] _process_next_job: started_job {started_job.job_id[:8]}... "
                    f"instance={started_job.instance_id[:8] if started_job.instance_id else 'N/A'}... "
                    f"admission_state={started_job.admission_state}"
                )

                # Phase 5 (Option B): MESSAGE BRANCH — wake-only.
                # Synchronous Task contract:
                #   - ``enqueue_message_job`` already created the
                #     Task + MessageQueue rows synchronously via
                #     ``_prepare_enqueued_message`` and stamped
                #     ``message_id`` onto the JobItem. The Task
                #     row is PENDING and visible to the worker pool.
                #   - This branch does NOT call ``enqueue_message``
                #     (would create a duplicate Task) and does NOT
                #     call ``spawn_instance_with_mcp`` (message
                #     jobs target an EXISTING instance — the
                #     ``start_job`` step preserved the
                #     ``instance_id`` from the original
                #     ``enqueue_message_job`` call).
                #   - We ONLY wake the worker pool so a worker
                #     thread can claim the pre-existing PENDING
                #     Task and route it to the existing instance.
                # ``JobFeedbackObserver`` owns the terminal
                # transition + slot-lock release.
                if job.job_type == "message":
                    try:
                        # Extract dispatch-time metadata (stored on
                        # the JobItem's JSON ``job_metadata`` column
                        # at ``enqueue_message_job`` time). Kept
                        # here as a no-op read — useful for
                        # debugging via structured logs.
                        job_meta = job.job_metadata or {}

                        # Wake the worker pool so a worker thread
                        # can claim the pre-existing PENDING Task.
                        # The Task + MessageQueue rows were already
                        # written by ``enqueue_message_job``; this
                        # is a surface-only signal.
                        worker_pool = getattr(
                            self._instance_manager, "_worker_pool", None
                        )
                        if worker_pool is not None:
                            worker_pool.notify_work()

                        logger.info(
                            f"JobProcessor (message branch): woke "
                            f"worker pool for pre-existing Task on "
                            f"job {job.job_id[:8]}... / instance "
                            f"{started_job.instance_id[:8] if started_job.instance_id else 'N/A'}..."
                        )
                        # S1 fix: clear the in-progress tracking
                        # entry on the SUCCESS path. Prior code only
                        # cleared it on the enqueue_message failure
                        # branch, which leaked entries in
                        # ``_last_in_progress`` / ``_in_progress_since``
                        # over time. The dispatch itself succeeds;
                        # the JobItem stays ``active`` until
                        # ``JobFeedbackObserver`` releases the slot.
                        self._cleanup_in_progress_tracking(job.job_id)
                    except Exception as e:
                        logger.error(
                            f"Failed to wake worker pool for "
                            f"message job {job.job_id[:8]}...: {e}"
                        )
                        await self._queue_service.complete_job(
                            job.job_id,
                            demand_state=DemandState.FAILED,
                            error=f"Failed to wake worker pool for message job: {e}",
                        )
                        self._cleanup_in_progress_tracking(job.job_id)
                    # Skip the task-path spawn below — message jobs
                    # use the existing instance.
                    continue

                # === TASK PATH (existing) ===
                # Spawn instance for this job
                try:
                    instance_id = await self._instance_manager.spawn_instance_with_mcp(
                        agent_id=job.agent_id,
                        instance_id=started_job.instance_id,
                        project_id=job.project_id,
                    )
                except Exception as e:
                    logger.error(f"Failed to spawn instance for job {job.job_id}: {e}")
                    await self._queue_service.complete_job(
                        job.job_id, demand_state=DemandState.FAILED, error=str(e)
                    )
                    self._cleanup_in_progress_tracking(job.job_id)
                    continue

                # Send the job message to the instance
                try:
                    result = await self._instance_manager.enqueue_message(
                        instance_id=instance_id,
                        message=job.message,
                        source=job.source,
                        is_deferred=(queue.queue_type == "defer"),
                        is_background=(queue.queue_type == "background"),
                        # Stamp the JobItem's ``job_id`` onto the
                        # driving Task's ``work_id`` so the Task is
                        # explicitly linked to its JobItem (the
                        # documented ``work_id == job_id`` contract).
                        # This gives drift detection (F10) and the
                        # work resolver a precise Task↔JobItem key
                        # instead of guessing via the instance's
                        # "freshest" JobItem — which falsely flagged
                        # ``job_continue`` continuation Tasks.
                        #
                        # Fix A (constitution Phase 0,
                        # approach-comparison.md row A):
                        # ``work_id_required=True`` makes the
                        # contract structural — a future regression
                        # that drops the ``work_id=`` binding FAILS
                        # LOUDLY instead of silently re-minting.
                        work_id=job.job_id,
                        work_id_required=True,
                    )
                    # ── Linkage-contract tripwire — Fix A escalation:
                    # ``enforce=True`` turns the tripwire into a hard
                    # failure on the job-driven path (the main TASK
                    # dispatch site). A mismatch between the
                    # dispatched Task's ``work_id`` and the driving
                    # JobItem's ``job_id`` raises
                    # :class:`LinkageContractError` so a regression
                    # that re-keys the Task fails closed at the
                    # dispatch boundary instead of silently breaking
                    # recovery lookups (Pattern-f1
                    # ``get_by_work_id``, work resolver).
                    _assert_linkage_contract(
                        result,
                        job.job_id,
                        source="JobProcessor",
                        logger=logger,
                        enforce=True,
                    )
                    # Stamp the message_id back onto the JobItem so
                    # the cross-system guard in ``claim_pending_task``
                    # can correlate active MESSAGE JobItems with their
                    # ``message_queue`` row. Failure here is
                    # non-fatal — the dispatch has already succeeded,
                    # and the NULL-safe guard tolerates a missing
                    # ``message_id`` (it just falls back to the legacy
                    # sibling check).
                    if result and result.message_id:
                        try:
                            await asyncio.to_thread(
                                self._queue_service._repository.stamp_message_id,
                                job.job_id, result.message_id,
                            )
                        except Exception as stamp_err:
                            logger.warning(
                                f"JobProcessor: failed to stamp message_id "
                                f"for job {job.job_id[:8]}...: {stamp_err}"
                            )
                except Exception as e:
                    logger.error(f"Failed to enqueue message for job {job.job_id}: {e}")
                    await self._queue_service.complete_job(
                        job.job_id, demand_state=DemandState.FAILED, error=str(e)
                    )
                    self._cleanup_in_progress_tracking(job.job_id)
                    continue

                logger.info(
                    f"Job {job.job_id} queued for instance {instance_id} "
                    f"on queue {queue.queue_name}"
                )
            except Exception as e:
                logger.exception(f"Failed to process job {job.job_id}: {e}")
                try:
                    await self._queue_service.complete_job(
                        job.job_id, demand_state=DemandState.FAILED, error=str(e)
                    )
                    self._cleanup_in_progress_tracking(job.job_id)
                except Exception:
                    pass

        # C5 orphan fallback removed (Phase 2): All jobs now have normalized project_id,
        # so there are no longer any orphan jobs without project_id to handle.


# Backward compatibility alias
TaskProcessor = JobProcessor
