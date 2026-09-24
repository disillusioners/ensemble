"""DEFECT-1 ROUND 3 (2026-09-24) — pins for the MEASURED CAS winner.

Round-3 arbitration (dev daemon, ``ensemble_dev``, instrumented
``[watch-cas]`` log at the ``work_notifier`` claim chokepoint):

  PRE-FIX  work=c383bdbd status=completed
           caller=job_queue_service.py:notify_watchers:378
                  <- child_reports.py:_dispatch_post_commit_side_effects:4338
                  <- child_reports.py:_process_child_completion_and_notify_parent:2185
           kw_result_summary=None resolver_result_summary=None
           effective_result=None matching=1 claimed=1
  [watch-deliver] body_bytes=55
           body='[JOB_EVENT] Job c383bdbd... completed ✓\\n  Agent: worker'

The completed-leg CAS winner is **NOT** ``task_processor.on_success``
(round-2 fix target), **NOT** ``worker_pool._schedule_work_notification``
(timeout-grace only — statically: its only "completed" call site is the
grace window at worker_pool.py:831), and **NOT** the observer outbox or
``_fire_watcher_notify_for_terminal`` (single caller, TERMINATED-only).
The winner is the ROOT-COMPLETION fan-out in
``ChildReportsService._dispatch_post_commit_side_effects``
(child_reports.py:4338 pre-fix) — added in the round-2 "watch-notify
fix" as the root-completion notify and never given the result arm,
while the SAME block threads ``result_summary=last_content`` into the
lifecycle event (the v0.13.9 result-arm). ``resolver_result_summary=None``
on the winning line proves the resolver's ``task.result`` read was
EMPTY at claim time (the pre-commit visibility gap) — so only a
caller-side kwarg can carry content.

Pins (all must FAIL on revert of the child_reports.py fix):

* ``test_measured_winner_claims_row_and_delivers_result_under_race`` —
  the measured reality: the root-completed fan-out fires (and CAS-claims
  the watcher) while the resolver read is still ``None``. The delivered
  body MUST carry ``Result:\\n<last_content>``. Pre-fix this exact
  scenario produced the byte-exact 55-byte Result:-less envelope.

* ``test_late_on_success_loser_cannot_suppress_or_duplicate`` — the
  exactly-once complement: AFTER the winner claimed, a LATE
  ``task_processor.on_success``-shaped notify (with in-hand content,
  the round-2 defense-in-depth leg) loses the CAS cleanly — zero
  duplicate deliveries, the one delivered body keeps the ``Result:``
  line.

* ``test_dispatch_notify_call_sites_carry_result_summary_kwarg`` —
  AST guard on ``_dispatch_post_commit_side_effects``: at least one
  ``notify_watchers`` call carries the ``result_summary=`` keyword
  (fails on revert), and at most ONE call lacks both ``result_summary=``
  and ``error=`` (the intentional failed-arm call — a NEW unthreaded
  success-status call site pushes the count to 2 and fails).
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlmodel import Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_models import (
    JobWatcher,
)
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.child_reports import (
    ChildReportsService,
    _ChildCompletionDbResult,
)
from daemon.services.job_queue_service import JobQueueService
from daemon.services.task_processor import ProcessMessageProcessor
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import WorkRecord, WorkResolverService


CHILD_REPORTS_PATH = (
    Path(__file__).resolve().parents[2] / "daemon" / "services" /
    "child_reports.py"
)

WATCHER_INSTANCE = "watcher-inst-defect1r3-0000-0000000000f3b"
LAST_CONTENT = "ROUND3MARKER_CONTENT"


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
    """``instance_manager.enqueue_message`` double — inspect the body."""
    manager = MagicMock(name="instance_manager")
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-defect1r3")
    )
    return manager


@pytest.fixture(autouse=True)
def _seed_watcher_instance(engine):
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


def _build_service(engine, job_repo, task_repo, instance_repo, enqueue_mock):
    """Real ``JobQueueService`` carrying the dependencies the winning
    call site reads (same wiring shape as the defect1b pin file)."""
    resolver = WorkResolverService(task_repo, job_repo, instance_repo)
    svc = JobQueueService.__new__(JobQueueService)
    svc._repository = job_repo
    svc._watcher_repo = JobWatcherRepository(engine)
    svc._instance_manager = enqueue_mock
    svc._work_resolver = resolver
    return svc


def _seed_executor_instance(engine, instance_id):
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


def _seed_task(engine, work_id, instance_id):
    with Session(engine) as s:
        s.add(Task(
            work_id=work_id,
            task_type="process_message",
            instance_id=instance_id,
            status=TaskStatus.RUNNING.value,
            created_at=datetime.now(timezone.utc),
            is_deferred=False,
        ))
        s.commit()
    return work_id


def _seed_watcher(watcher_repo, work_id):
    watcher_repo.add_watch(work_id, WATCHER_INSTANCE, ["completed"])
    return work_id


def _patch_resolver_race(resolver, *, wid: str):
    """Simulate the MEASURED race shape: the resolver's ``resolve_work``
    returns ``result_summary=None`` (the winning [watch-cas] line carried
    ``resolver_result_summary=None`` — the Task row's commit was not yet
    visible when the fan-out claimed the watcher)."""
    record = WorkRecord(
        work_id=wid, kind="report", status="completed",
        instance_id="inst-test", project_id="p1",
        agent_id="worker", result_summary=None,
        error=None,
        created_at=datetime.now(timezone.utc),
        job_type=None, mission_liveness=None,
    )
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


def _build_child_reports(engine, service, task_repo) -> ChildReportsService:
    """Real ``ChildReportsService`` over a manager double.

    ``_live_hub`` None skips the SSE leg; ``_events_service`` None skips
    the lifecycle publish; the completion gate + terminal-message lookup
    run against the real engine (fail-open, seeded clean); title
    generation is stubbed (orthogonal side effect). ``_task_repo`` is
    the REAL TaskRepository — the root_completed work-scan reads it via
    ``getattr(self._manager, "_task_repo", None).get_by_instance`` to
    build the candidate ``work_ids`` set.
    """
    manager = MagicMock(name="instance_manager")
    manager._job_queue_service = service
    manager._engine = engine
    manager._task_repo = task_repo
    manager._live_hub = None
    svc = ChildReportsService(manager, events_service=None)
    svc._trigger_title_generation = MagicMock(return_value=None)
    return svc


def _delivered_bodies(enqueue_mock) -> list[str]:
    return [
        c.kwargs.get("message", "")
        for c in enqueue_mock.enqueue_message.await_args_list
    ]


def _root_completed_result(instance_id: str) -> _ChildCompletionDbResult:
    return _ChildCompletionDbResult(
        outcome="root_completed",
        instance_id=instance_id,
        agent_id="worker",
        parent_id=None,
    )


# ── Round-3 pins ──────────────────────────────────────────────────────────


class TestMeasuredWinnerDeliversResult:
    """The root-completed fan-out in
    ``ChildReportsService._dispatch_post_commit_side_effects`` is the
    MEASURED CAS winner for root message-task completions. Firing it
    against a resolver that still sees ``None`` MUST deliver a body
    carrying the in-scope ``last_content``.
    """

    @pytest.mark.asyncio
    async def test_measured_winner_claims_row_and_delivers_result_under_race(
        self, engine, task_repo, job_repo, instance_repo, watcher_repo,
        enqueue_mock,
    ):
        """Replay of the measured ordering: the fan-out claims the only
        watcher row while ``resolver_result_summary`` is still ``None``.

        Pre-fix (work c383bdbd, live): body was exactly
        ``[JOB_EVENT] Job <id>... completed ✓\\n  Agent: worker`` —
        55 bytes, no ``Result:`` line.
        Post-fix (work dade61f7, live): body carries
        ``Result:\\n<content>`` (231 bytes live).
        """
        service = _build_service(
            engine, job_repo, task_repo, instance_repo, enqueue_mock,
        )
        executor = _seed_executor_instance(
            engine, f"exec-{uuid4().hex[:8]}"
        )
        work_id = str(uuid4())
        _seed_task(engine, work_id, executor)
        _seed_watcher(watcher_repo, work_id)

        resolver = service._work_resolver
        original = _patch_resolver_race(resolver, wid=work_id)
        try:
            svc = _build_child_reports(engine, service, task_repo)
            await svc._dispatch_post_commit_side_effects(
                _root_completed_result(executor),
                LAST_CONTENT,
                str(uuid4()),  # completed_message_id (title gen stubbed)
            )
        finally:
            resolver.resolve_work = original

        bodies = _delivered_bodies(enqueue_mock)
        assert len(bodies) == 1, (
            f"exactly-once: the winner delivers exactly one envelope; "
            f"got {len(bodies)}: {bodies!r}"
        )
        body = bodies[0]
        assert "[JOB_EVENT]" in body and "completed ✓" in body
        assert f"Result:\n{LAST_CONTENT}" in body, (
            "DEFECT-1 round-3 fix: the MEASURED winner "
            "(child_reports root-completed fan-out) MUST thread "
            f"result_summary=last_content into the body; got {body!r}"
        )
        # The watcher row was CAS-consumed by the winner (exactly-once).
        from sqlmodel import select
        with Session(engine) as s:
            remaining = s.exec(
                select(JobWatcher).where(JobWatcher.job_id == work_id)
            ).all()
        assert remaining == [], (
            "the winning claim must consume the watcher row"
        )

    @pytest.mark.asyncio
    async def test_late_on_success_loser_cannot_suppress_or_duplicate(
        self, engine, task_repo, job_repo, instance_repo, watcher_repo,
        enqueue_mock,
    ):
        """Exactly-once complement under the measured ordering: AFTER the
        winner (child_reports fan-out) claimed and delivered, a LATE
        ``on_success``-shaped notify — the round-2 defense-in-depth leg
        holding in-hand content — must lose the CAS cleanly: no duplicate
        envelope, and the single delivered body keeps the ``Result:``
        line.
        """
        service = _build_service(
            engine, job_repo, task_repo, instance_repo, enqueue_mock,
        )
        executor = _seed_executor_instance(
            engine, f"exec-{uuid4().hex[:8]}"
        )
        work_id = str(uuid4())
        _seed_task(engine, work_id, executor)
        _seed_watcher(watcher_repo, work_id)

        resolver = service._work_resolver
        original = _patch_resolver_race(resolver, wid=work_id)
        try:
            svc = _build_child_reports(engine, service, task_repo)
            # 1. The measured WINNER fires first and claims the row.
            await svc._dispatch_post_commit_side_effects(
                _root_completed_result(executor),
                LAST_CONTENT,
                str(uuid4()),
            )
            # 2. The LATE leg (on_success shape, in-hand content) fires
            #    after the row is gone — must no-op (claimed=0).
            late_notified = await notify_work_watchers(
                work_id=work_id,
                status="completed",
                instance_manager=enqueue_mock,
                work_resolver=resolver,
                watcher_repo=watcher_repo,
                result_summary="LATE_IN_HAND_CONTENT",
            )
        finally:
            resolver.resolve_work = original

        assert late_notified == 0, (
            "the late loser must not deliver (CAS exactly-once)"
        )
        bodies = _delivered_bodies(enqueue_mock)
        assert len(bodies) == 1, (
            f"exactly-once under the measured ordering; got {len(bodies)}"
        )
        assert f"Result:\n{LAST_CONTENT}" in bodies[0], (
            "the delivered body must be the WINNER's content-bearing "
            f"envelope; got {bodies[0]!r}"
        )
        assert "LATE_IN_HAND_CONTENT" not in bodies[0]


class TestDispatchCallSiteGuard:
    """AST guard over ``_dispatch_post_commit_side_effects``."""

    def _notify_calls(self) -> list[ast.Call]:
        tree = ast.parse(CHILD_REPORTS_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and (
                node.name == "_dispatch_post_commit_side_effects"
            ):
                return [
                    n for n in ast.walk(node)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "notify_watchers"
                ]
        raise AssertionError(
            "_dispatch_post_commit_side_effects not found — layout drift"
        )

    def test_dispatch_notify_call_sites_carry_result_summary_kwarg(self):
        """Every ``notify_watchers`` call inside the winning dispatch
        method must thread a content/error slot — EXCEPT the single
        intentional failed-arm call (no error text is in scope there).

        * ≥1 call with ``result_summary=`` — fails on revert of the
          round-3 fix (the revert removes the only result-bearing call).
        * ≤1 call with NEITHER ``result_summary=`` NOR ``error=`` — a
          NEW unthreaded success-status call site pushes this to 2 and
          fails, closing the "new caller repeats the defect" gap.
        """
        calls = self._notify_calls()
        assert calls, "no notify_watchers call sites found — layout drift"

        with_result = [
            c for c in calls
            if any(kw.arg == "result_summary" for kw in c.keywords)
        ]
        naked = [
            c for c in calls
            if not any(
                kw.arg in ("result_summary", "error") for kw in c.keywords
            )
        ]

        assert len(with_result) >= 1, (
            "DEFECT-1 round-3 regression: no notify_watchers call in "
            "_dispatch_post_commit_side_effects threads "
            f"result_summary= — the measured CAS winner delivers a "
            f"Result:-less envelope. Call sites: "
            f"{[ast.dump(c) for c in calls]}"
        )
        assert len(naked) <= 1, (
            "DEFECT-1 round-3 guard: more than one notify_watchers call "
            "in _dispatch_post_commit_side_effects carries NEITHER "
            "result_summary= NOR error= (only the failed-arm call is "
            f"allowed to omit both). Offending sites at lines: "
            f"{[c.lineno for c in naked]}"
        )
