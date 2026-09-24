"""DEFECT-1b (2026-09-24) — producer-side ``result_summary`` threading.

Live evidence (2026-09-24 E2E, real Ari on real dev daemon at
``fix/watch-notify-delivery-gaps`` @ ``0d75e068``; tester RESULTS item 2):

  work_id   = 4a7236e2-bb84-47a5-bc76-8a7574761bcb (T1 task-kind)
  enqueue   = 03:46:43.987  (notify_work_watchers enqueued [JOB_EVENT])
  commit    = 03:46:44.047  (Task.completed_at captured at complete_task entry)
  delta     = -60ms  (notify fired BEFORE the Task row's commit was
                       visible to the resolver read)
  body      = 57 bytes — header + Agent line ONLY, NO ``Result:`` line
  Task.result carried ``RESULTWORD7`` (verified in DB; API resolver
  returned the content correctly).

The pre-fix ``ProcessMessageProcessor.on_success`` callback at
``daemon/services/task_processor.py:1010`` called ``notify_work_watchers``
WITHOUT threading ``result_summary=`` — the notify relied on the
resolver's ``task.result`` read, which raced the ``complete_task``
commit. The OBSERVER path (``job_feedback_observer.py:2076``) DID thread
``result_summary=`` correctly but lost the CAS race on the watcher row
(the direct path claimed it first, leaving the watcher row deleted).

This file pins the producer-side fix:

* ``test_on_success_threads_in_hand_result_summary_under_race`` —
  ``ProcessMessageProcessor.on_success(processing_result)`` MUST thread
  ``result_summary=processing_result.result_content`` into
  ``notify_work_watchers`` even when the resolver returns
  ``result_summary=None`` (the precise race the live trace exhibits).
  The pre-fix code did NOT thread it; the post-fix code does. A
  regression to the pre-fix shape (relying on the resolver read) would
  leave the watcher body missing the ``Result:`` line — exactly the
  symptom that surfaced live.

* ``test_on_success_threads_in_hand_overrides_stale_resolver_value`` —
  even when the resolver DOES return a value (but a STALE one — the
  previous turn's content), the call-site's in-hand content wins.
  This is the F2-discipline twin: ``result_summary=`` kwarg overrides
  the resolver fallback in ``work_notifier.effective_result``
  (``work_notifier.py:306``).

* ``test_on_success_failure_leg_unaffected`` — GUARD: the fix only
  touches the producer-side ``on_success`` callback; the
  failure/cancel legs (``worker_pool._handle_task_failure`` and the
  cancellation paths) thread ``error=`` symmetrically and were
  verified not affected by the same race — they use synchronous
  ``fail_task``/``cancel_task`` (worker thread blocks on commit) AND
  thread ``error=`` directly as a kwarg. Pin the symmetry: a future
  refactor that drops the failure-leg ``error=`` kwarg would re-open
  the race on the error side; this test asserts the in-hand error
  string is preserved when the resolver returns a stale/None value.

Recipe: real ``JobWatcherRepository`` + ``TaskRepository`` +
``WorkResolverService`` + ``ProcessMessageProcessor._build_callbacks``
— the conftest in-memory SQLite engine plus the canonical
``ProcessMessageProcessor`` wiring from
``test_event_driven_completion.py``. The resolver is patched to return
the SIMULATED-RACE shape (``result_summary=None`` / stale); the
production ``notify_work_watchers`` runs end-to-end and the message
body is inspected for the in-hand content.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlmodel import Session, select

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem, AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_queue_service import JobQueueService
from daemon.services.task_processor import ProcessMessageProcessor
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import WorkRecord, WorkResolverService


WATCHER_INSTANCE = "watcher-inst-defect1b-0000-0000000000f1b"


# ── Fixtures + helpers ────────────────────────────────────────────────────


@pytest.fixture
def watcher_repo(engine) -> JobWatcherRepository:
    return JobWatcherRepository(engine)


@pytest.fixture
def job_repo(engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def task_repo(engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def instance_repo(engine):
    from daemon.repositories.instance.repository import (
        SQLModelInstanceRepository,
    )
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def enqueue_mock():
    """instance_manager.enqueue_message double — inspect the body."""
    manager = MagicMock(name="instance_manager")
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-defect1b")
    )
    return manager


@pytest.fixture(autouse=True)
def _seed_watcher_instance(engine):
    """The watching instance row (job_watchers.instance_id FK)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(Instance(
            instance_id=WATCHER_INSTANCE,
            agent_id="jober",
            agent_dir="agents/jober",
            project_id="test-project",
            status=InstanceStatus.RUNNING.value,
            created_at=now,
            updated_at=now,
        ))
        s.commit()


def _seed_instance(engine, instance_id):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(Instance(
            instance_id=instance_id,
            agent_id="worker",
            agent_dir="agents/worker",
            project_id="test-project",
            status=InstanceStatus.RUNNING.value,
            created_at=now,
            updated_at=now,
        ))
        s.commit()
    return instance_id


def _seed_task(engine, work_id, instance_id, status=TaskStatus.RUNNING.value):
    from datetime import datetime, timezone
    with Session(engine) as s:
        s.add(Task(
            work_id=work_id,
            task_type="process_message",
            instance_id=instance_id,
            status=status,
            created_at=datetime.now(timezone.utc),
            is_deferred=False,
        ))
        s.commit()
    return work_id


def _seed_mirror_job(
    engine, work_id, instance_id, *, admission_state="active",
    job_type="message",
):
    with Session(engine) as s:
        s.add(JobItem(
            job_id=work_id,
            agent_id="worker",
            agent_dir="agents/worker",
            message="task work",
            source="agent:test",
            instance_id=instance_id,
            admission_state=admission_state,
            job_type=job_type,
        ))
        s.commit()
    return work_id


def _seed_watcher(watcher_repo, work_id, events=None):
    if events is None:
        events = ["completed"]
    watcher_repo.add_watch(work_id, WATCHER_INSTANCE, events)
    return work_id


def _build_service(engine, job_repo, task_repo, instance_repo, enqueue_mock):
    """Real ``JobQueueService`` carrying the dependencies the call site
    reads (``_repository``, ``_watcher_repo``, ``_instance_manager``,
    ``_work_resolver``)."""
    resolver = WorkResolverService(task_repo, job_repo, instance_repo)
    svc = JobQueueService.__new__(JobQueueService)
    svc._repository = job_repo
    svc._watcher_repo = JobWatcherRepository(engine)
    svc._instance_manager = enqueue_mock
    svc._work_resolver = resolver
    return svc


def _make_manager(service, task_repo, instance_repository, enqueue_mock):
    """Manager double carrying the real job-queue service + task repo
    (and the instance repo for the W6 anchor clear). The ``enqueue_mock``
    provides ``enqueue_message`` which the on_success callback's
    ``notify_work_watchers`` call needs (work_notifier.py:534)."""
    manager = MagicMock(name="instance_manager")
    manager._job_queue_service = service
    manager._task_repo = task_repo
    manager._instance_repository = instance_repository
    manager.enqueue_message = enqueue_mock.enqueue_message
    return manager


def _seed_task_kind_job(engine, job_id, instance_id, *, admission_state, terminal_reason):
    """Seed a task-kind JobItem with an explicit terminal disposition."""
    with Session(engine) as s:
        s.add(JobItem(
            job_id=job_id,
            agent_id="developer",
            agent_dir="agents/developer",
            message="task work",
            source="agent:test",
            instance_id=instance_id,
            admission_state=admission_state,
            terminal_reason=terminal_reason,
            job_type="task",
        ))
        s.commit()
    return job_id


def _build_task_processor(task_repo, resolver, watcher_repo, manager):
    """Minimal ``ProcessMessageProcessor`` carrying exactly the
    attributes the ``on_success`` closure reads."""
    tp = ProcessMessageProcessor.__new__(ProcessMessageProcessor)
    tp._task_repo = task_repo
    tp._work_resolver = resolver
    tp._watcher_repo = watcher_repo
    tp._manager = manager
    tp._contention_counts = {}
    tp._last_info_at = {}
    return tp


def _delivered_body(enqueue_mock) -> str:
    """The canonical message body the call-site enqueued (the LAST
    ``[JOB_EVENT]`` body delivered)."""
    if not enqueue_mock.enqueue_message.await_args_list:
        return ""
    call = enqueue_mock.enqueue_message.await_args_list[-1]
    return call.kwargs.get("message", "")


def _patch_resolver_stale(resolver, *, wid: str, result_summary):
    """Synthesize a TASK-side WorkRecord whose ``result_summary``
    simulates the race: either ``None`` (race-lost) or a STALE prior
    turn's content (race-won-with-stale-data). The post-fix call site
    threads its own in-hand value via the ``notify_work_watchers``
    ``result_summary=`` kwarg, overriding the resolver fallback.
    """
    record = WorkRecord(
        work_id=wid, kind="report", status="completed",
        instance_id="inst-test", project_id="p1",
        agent_id="worker", result_summary=result_summary,
        error=None,
        created_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc,
        ),
        job_type=None, mission_liveness=None,
    )
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


# ── DEFECT-1b pins ────────────────────────────────────────────────────────


class TestOnSuccessThreadsInHandResultSummary:
    """The producer-side ``on_success`` callback threads
    ``result_summary=processing_result.result_content`` directly into
    ``notify_work_watchers``, bypassing the resolver read.

    Live evidence (work_id 4a7236e2..., 2026-09-24 E2E): when the
    resolver returned ``None`` (the post-commit visibility gap), the
    delivered [JOB_EVENT] body was 57 bytes (NO ``Result:`` line)
    despite ``Task.result`` being committed. The post-fix call site
    threads the in-memory ``processing_result.result_content`` instead
    of relying on the resolver.
    """

    @pytest.mark.asyncio
    async def test_on_success_threads_in_hand_result_summary_under_race(
        self, engine, task_repo, job_repo, instance_repo, watcher_repo,
        enqueue_mock,
    ):
        """Race simulation: resolver returns ``result_summary=None``
        (the precise post-commit-visibility-gap shape the live trace
        exhibited). The call site MUST thread the in-hand
        ``processing_result.result_content`` so the delivered body
        carries the ``Result:\\n<in-hand content>`` line.

        Pre-fix: the call site did NOT thread ``result_summary=``,
        so the body carried NO ``Result:`` line (the 57-byte live
        evidence shape).
        Post-fix: the call site threads
        ``result_summary=result.result_content`` (the agent's last
        assistant message from the pipeline return value), so the
        body carries ``Result:\\n<that content>`` regardless of the
        resolver's view.
        """
        service = _build_service(
            engine, job_repo, task_repo, instance_repo, enqueue_mock,
        )
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        work_id = str(uuid4())
        _seed_task(engine, work_id, instance_id, TaskStatus.RUNNING.value)
        _seed_watcher(watcher_repo, work_id)

        # Patch the resolver to simulate the race: the resolver's
        # ``resolve_work`` returns a WorkRecord with
        # ``result_summary=None`` — the same shape that left the live
        # 57-byte body without a ``Result:`` line. The post-fix call
        # site MUST NOT trust this value; it threads its own in-hand
        # ``processing_result.result_content`` instead.
        resolver = service._work_resolver
        original = _patch_resolver_stale(
            resolver, wid=work_id, result_summary=None,
        )
        try:
            # The manager needs ``_job_queue_service`` (Fix B hook),
            # ``_task_repo`` (W6 anchor clear), ``_instance_repository``
            # (W6 anchor clear), AND ``enqueue_message`` (the
            # ``notify_work_watchers`` call site at
            # task_processor.py:1010).
            manager = _make_manager(
                service, task_repo, instance_repo, enqueue_mock,
            )
            tp = _build_task_processor(
                task_repo, resolver, watcher_repo, manager,
            )
            callbacks = tp._build_callbacks(
                Session(engine).get(
                    Task,
                    Session(engine).exec(
                        select(Task).where(Task.work_id == work_id)
                    ).first().id,
                )
            )

            # The in-hand ProcessingResult — this is what the pipeline
            # passes into ``on_success`` (the agent's last assistant
            # message from ``gate_outcome.content``).
            in_hand_content = "RESULTWORD7_FROM_PIPELINE"
            from daemon.services.message_processing_pipeline import (
                ProcessingResult,
            )
            processing_result = ProcessingResult(
                success=True, result_content=in_hand_content,
            )
            await callbacks.on_success(processing_result)
        finally:
            resolver.resolve_work = original

        # The resolver was patched to return ``result_summary=None``
        # (the race shape). The fix MUST surface the in-hand content
        # in the watcher body anyway.
        body = _delivered_body(enqueue_mock)
        assert "[JOB_EVENT]" in body, (
            f"on_success must deliver a [JOB_EVENT] watcher body; got {body!r}"
        )
        assert "completed ✓" in body, (
            f"task-kind terminal body must carry the completed glyph; got {body!r}"
        )
        # The pin: the in-hand content from ``processing_result.result_content``
        # threads into the body even when the resolver returned None.
        assert f"Result:\n{in_hand_content}" in body, (
            "DEFECT-1b fix: the on_success callback MUST thread "
            "``result_summary=processing_result.result_content`` "
            "directly so the body carries the in-hand content even "
            "when the resolver's ``resolve_work`` returns "
            "``result_summary=None`` (the post-commit visibility gap "
            "the live trace exhibited). Pre-fix the call site did "
            "NOT thread ``result_summary=`` and the resolver's "
            "``None`` propagated to ``effective_result`` ("
            "work_notifier.py:306), producing the 57-byte watcher "
            "body (no ``Result:`` line) that surfaced live at "
            "work_id 4a7236e2..., 03:46:43.987."
        )
        # And the resolver's None / empty value MUST NOT be present.
        assert "Result:\nNone" not in body, (
            "DEFECT-1b: a regression to the pre-fix shape would slip "
            "the resolver's stale ``None`` (or 'None' literal) into "
            "the body — close that door."
        )

    @pytest.mark.asyncio
    async def test_on_success_threads_in_hand_overrides_stale_resolver_value(
        self, engine, task_repo, job_repo, instance_repo, watcher_repo,
        enqueue_mock,
    ):
        """Stale resolver value: resolver returns ``"STALE_PRIOR_TURN"``
        (a previous turn's content, NOT this turn's). The in-hand
        ``processing_result.result_content`` MUST override it via the
        ``result_summary=`` kwarg — the F2-discipline twin of the
        defect-1b fix.

        Live E2E (work_id 4a7236e2..., 2026-09-24): the resolver
        returned ``None`` (race-lost), not a stale value. The same
        mechanism — ``work_notifier.effective_result`` preferring the
        caller's kwarg over the resolver fallback — closes BOTH the
        ``None`` and the ``stale`` failure shapes. Pin that the
        override survives both.
        """
        service = _build_service(
            engine, job_repo, task_repo, instance_repo, enqueue_mock,
        )
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        work_id = str(uuid4())
        _seed_task(engine, work_id, instance_id, TaskStatus.RUNNING.value)
        _seed_watcher(watcher_repo, work_id)

        resolver = service._work_resolver
        original = _patch_resolver_stale(
            resolver, wid=work_id,
            result_summary="STALE_PRIOR_TURN_CONTENT",
        )
        try:
            manager = _make_manager(
                service, task_repo, instance_repo, enqueue_mock,
            )
            tp = _build_task_processor(
                task_repo, resolver, watcher_repo, manager,
            )
            callbacks = tp._build_callbacks(
                Session(engine).get(
                    Task,
                    Session(engine).exec(
                        select(Task).where(Task.work_id == work_id)
                    ).first().id,
                )
            )

            from daemon.services.message_processing_pipeline import (
                ProcessingResult,
            )
            processing_result = ProcessingResult(
                success=True, result_content="CURRENT_TURN_CONTENT",
            )
            await callbacks.on_success(processing_result)
        finally:
            resolver.resolve_work = original

        body = _delivered_body(enqueue_mock)
        # The kwarg override MUST win — the in-hand content surfaces,
        # NOT the resolver's stale value.
        assert "Result:\nCURRENT_TURN_CONTENT" in body, (
            "DEFECT-1b override pin: even when the resolver returns a "
            "stale value (NOT None — a previous turn's content), the "
            "call site's ``result_summary=`` kwarg must override the "
            "resolver fallback (``work_notifier.effective_result`` at "
            "work_notifier.py:306 — ``result_summary if result_summary "
            "is not None else work_record.result_summary``). A "
            "regression that drops the threading would surface the "
            "stale ``STALE_PRIOR_TURN_CONTENT`` instead of "
            "``CURRENT_TURN_CONTENT``."
        )
        assert "STALE_PRIOR_TURN_CONTENT" not in body, (
            "DEFECT-1b: stale resolver value MUST NOT leak into the "
            "watcher body when the call site has in-hand content."
        )


class TestFailureLegUnaffected:
    """GUARD: the failure/cancel legs already thread ``error=``
    symmetrically and were verified not affected by the same race.

    ``worker_pool._handle_task_failure`` and the cancellation paths
    use SYNCHRONOUS ``fail_task``/``cancel_task`` (the worker thread
    blocks on the DB commit before returning) AND thread ``error=``
    as a kwarg into ``notify_work_watchers`` — so ``effective_error``
    (work_notifier.py:316) carries the in-hand string regardless of
    the resolver's view.

    This test exercises the SAME notify_watchers shape the failure
    leg uses (``error=`` kwarg + resolver with stale ``error=None``)
    and asserts the body carries the in-hand error string — i.e. a
    regression that drops the ``error=`` threading on the failure
    leg would re-open the symmetric race. Pin the asymmetry: failure
    legs thread ``error=``, success legs thread ``result_summary=``.
    """

    @pytest.mark.asyncio
    async def test_failure_leg_error_kwarg_survives_stale_resolver(
        self, engine, task_repo, job_repo, instance_repo, watcher_repo,
        enqueue_mock,
    ):
        """Asymmetry pin: failure legs (worker_pool
        ``_handle_task_failure`` and cancellation paths) thread
        ``error=`` as a kwarg and use SYNCHRONOUS fail_task/cancel_task
        — no race window exists. This test simulates the SAME notify
        shape the failure leg uses (``error=`` in-hand + resolver
        returns ``error=None``) and asserts the in-hand error string
        surfaces in the ``Error:`` slot.

        If a future refactor drops the ``error=`` kwarg on the failure
        leg (relying on the resolver's ``work_record.error`` read
        instead), the body would carry ``Error: None`` or no Error
        line at all. Pin that door closed.
        """
        resolver = WorkResolverService(task_repo, job_repo, instance_repo)
        service = _build_service(
            engine, job_repo, task_repo, instance_repo, enqueue_mock,
        )
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        work_id = str(uuid4())
        _seed_task(engine, work_id, instance_id, TaskStatus.RUNNING.value)
        _seed_watcher(watcher_repo, work_id, events=["failed"])

        # Resolver returns ``error=None`` (the failure-leg race shape
        # — the resolver hasn't seen the just-committed task.error
        # yet, mirroring the live success-leg trace but on the error
        # side).
        original = _patch_resolver_stale(
            resolver, wid=work_id, result_summary=None,
        )
        # Override the patched record's ``error`` to None (the
        # _patch_resolver_stale helper sets error=None by default).
        try:
            # Drive the canonical failure-leg facade:
            # JobQueueService.notify_watchers with ``error=`` kwarg.
            notified = await service.notify_watchers(
                work_id, "failed",
                error="max retries exceeded (failure-leg in-hand)",
            )
        finally:
            resolver.resolve_work = original

        assert notified == 1, (
            f"failure leg notify should fire to the single watcher; got {notified}"
        )
        body = _delivered_body(enqueue_mock)
        assert "[JOB_EVENT]" in body
        assert "failed ✗" in body
        # The in-hand error string surfaces in the ``Error:`` slot
        # — the failure-leg threading discipline is preserved.
        assert "Error: max retries exceeded (failure-leg in-hand)" in body, (
            "Failure-leg GUARD: the worker_pool failure/cancel paths "
            "thread ``error=`` as a kwarg into notify_watchers. "
            "``work_notifier.effective_error`` (work_notifier.py:316) "
            "prefers ``error`` over the resolver's ``work_record.error`` "
            "— so the body MUST surface the in-hand error string "
            "even when the resolver returns ``error=None``. A "
            "regression that drops the ``error=`` threading would "
            "slip the resolver's ``None`` into the body (or omit "
            "the ``Error:`` line entirely)."
        )
        # And the resolver's None MUST NOT be the only Error value.
        assert "Error: None" not in body, (
            "Failure-leg GUARD: a regression that drops the in-hand "
            "``error=`` threading would slip ``None`` into the "
            "rendered Error slot. Pin that door closed."
        )
