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
  success-status call site pushes the count to 2 and fails). The
  ROUND-3 REVIEW NARROW assertion extends this same test method:
  each ``result_summary=`` call must be reachable ONLY under the
  BODY of ``if _token == "completed":`` — AST parent-map walk via
  ``_result_summary_call_under_completed_arm``. Fails the moment
  anyone re-widens the threading to a non-completed token (the
  post-5292eb99 gap that produced settled envelopes WITH a
  ``Result:`` line).

* ``test_settled_message_kind_dispatch_envelope_has_no_result_block`` —
  ROUND-3 REVIEW M3 GUARD: a message-kind mirror WorkRecord
  (``status="settled"``, ``job_type="message"``) routed through
  the root-completion ``_dispatch_post_commit_side_effects``
  fan-out delivers an envelope carrying the header + Agent line
  and ``settled ✓`` glyph — and NOTHING ELSE. The resolver returns
  no content for settled mirrors; the narrowed branch must not
  pre-thread ``result_summary=last_content`` into a settled call
  site (the pre-narrow else-branch did so for ``settled`` tokens
  too, which violates the M3 mission-class by-design NO-Result
  shape).
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
def enqueue_mock(instance_repo):
    """``instance_manager.enqueue_message`` double — inspect the body.

    C1 (2026-09-25): the ``_instance_repository`` attribute is wired
    so the canonical ``evaluate_mission_live`` guard can walk the
    ``instances.parent_id`` tree. Without it the guard fail-OPENS
    (``live=False`` → claim + deliver) and the held-mission-terminal
    semantics break.
    """
    manager = MagicMock(name="instance_manager")
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-defect1r3")
    )
    manager._instance_repository = instance_repo
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


def _patch_resolver_settled_mirror(resolver, *, wid: str):
    """Synthesize a message-kind mirror WorkRecord — ``kind="job"``,
    ``job_type="message"`` — so ``per_kind_status_for(wid)`` returns
    ``"settled"`` (M3 mission-class rename: terminal Reason='completed'
    on a mirror JobItem flips the canonical status to ``settled``).

    The settled mirror carries NO ``result_summary`` and NO ``error``
    (mirror JobItem rows have no paired Task row to surface content
    from), so the notifier's ``effective_result`` resolver-side read is
    ``None`` and the body must render without a ``Result:`` block.

    This is the SHAPE that the root-completion
    ``_dispatch_post_commit_side_effects`` fan-out scans when a
    message-kind JobItem mirror for the root is still in the
    ``find_jobs_by_instance`` bucket at completion time. Pre-narrow,
    the else-branch threaded ``result_summary=last_content`` here too
    and the delivered envelope rendered WITH a ``Result:`` line,
    breaking the M3 by-design NO-Result-block shape.
    """
    record = WorkRecord(
        work_id=wid, kind="job", status="settled",
        instance_id="inst-test", project_id="p1",
        agent_id="worker", result_summary=None,
        error=None,
        created_at=datetime.now(timezone.utc),
        job_type="message", mission_liveness=None,
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


class TestSettledMessageKindDispatchEnvelope:
    """C3 (2026-09-25, ``fix/mission-terminal-watch-report-publish``) —
    settled envelope through ``_dispatch_post_commit_side_effects``
    MUST carry a populated ``Result:`` line. The pre-C3 M3 narrow
    (81fc769d, 2026-09-24) restricted ``result_summary=last_content``
    threading to the ``_token == "completed"`` arm only; a settled
    mirror (kind='job', job_type='message') flowing through the same
    fan-out fell to the else-branch and got NO content kwarg. C3
    reverses that — settled envelopes SHOULD carry the Result line
    (user-ratified, 2026-09-25).

    Race-safety: ``last_content`` is the in-memory agent's last
    assistant message, fetched BEFORE the fan-out runs. Threading it
    as a ``result_summary`` kwarg bypasses the resolver's
    ``task.result`` read that races the ``complete_task`` commit
    visibility window (the live E2E race the DEFECT-1b close
    documented at task_processor.py:1010-1020). Same race-safety
    discipline DEFECT-1b established for the completed arm.
    """

    @pytest.mark.asyncio
    async def test_settled_message_kind_dispatch_envelope_carries_result_block(
        self, engine, task_repo, job_repo, instance_repo, watcher_repo,
        enqueue_mock,
    ):
        """Real CAS path — real ``ChildReportsService`` → real
        ``JobQueueService`` → real ``notify_work_watchers``. A
        message-kind mirror WorkRecord (per_kind_status_for →
        'settled') flowing through
        ``_dispatch_post_commit_side_effects`` delivers ONE
        envelope carrying the header + Agent + ``settled ✓`` glyph
        AND a populated ``Result:\\n<last_content>`` line.

        Pre-C3 (M3 narrow at 81fc769d, 2026-09-24) this scenario
        delivered a body WITHOUT a ``Result:`` block — the
        by-design M3 NO-Result-block shape. C3 reverses that
        (user override, 2026-09-25): settled envelopes SHOULD
        carry the Result line. The fan-out threading is broadened
        at child_reports.py:4380+ so EVERY non-failed terminal
        token (completed / settled / cancelled / dead_letter)
        threads ``result_summary=last_content``; the ``failed``
        token stays on the no-content branch because it has its
        own error-lane emission in ``error_reporting.py``.
        """
        service = _build_service(
            engine, job_repo, task_repo, instance_repo, enqueue_mock,
        )
        executor = _seed_executor_instance(
            engine, f"exec-{uuid4().hex[:8]}"
        )
        work_id = str(uuid4())
        # The settled mirror's work_id still surfaces via the
        # Task-side work-scan (``get_by_instance``). Seed a Task
        # row carrying it — its canonical status is irrelevant
        # because the resolver is patched below to return the
        # mirror-shape WorkRecord.
        _seed_task(engine, work_id, executor)
        # A watcher must exist for ``notify_work_watchers`` to
        # actually deliver. C3: the watcher subscribes to the
        # ``settled`` event explicitly (the per-kind dispatch
        # surfaces ``settled`` as the canonical status on a
        # mirror row).
        watcher_repo.add_watch(
            work_id, WATCHER_INSTANCE, ["settled"]
        )

        resolver = service._work_resolver
        original = _patch_resolver_settled_mirror(resolver, wid=work_id)
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
            f"the settled-mirror fan-out must deliver exactly one "
            f"envelope; got {len(bodies)}: {bodies!r}"
        )
        body = bodies[0]
        assert "[JOB_EVENT]" in body
        assert "settled ✓" in body, (
            "settled-mirror C3 envelope must carry the "
            f"``settled ✓`` glyph; got {body!r}"
        )
        assert f"Agent: worker" in body
        # ── C3 BROADEN HOLDS HERE ──
        # The fan-out's non-failed branch (every terminal token
        # EXCEPT failed, which has its own error lane) now threads
        # ``result_summary=last_content``. A settled mirror
        # flowing through the same fan-out lands on the
        # non-failed branch and gets the content kwarg — the
        # body carries the ``Result:\\n<last_content>`` line.
        assert f"Result:\n{LAST_CONTENT}" in body, (
            "C3 settled GUARD: a settled mirror envelope through "
            "_dispatch_post_commit_side_effects MUST carry a "
            "Result: line — the C3 broaden at child_reports.py:4380+ "
            "threads ``result_summary=last_content`` for every "
            "non-failed terminal token. Pre-C3 (M3 narrow at "
            "81fc769d, 2026-09-24) settled envelopes were "
            "NO-Result-block; C3 reverses that (user override, "
            "2026-09-25). Race-safe via in-memory ``last_content`` "
            f"(no DB read of ``task.result`` required). Got body={body!r}"
        )
        # No error slot either (we passed status='settled', not
        # 'failed', and no error kwarg upstream).
        assert "Error:" not in body
        # The watcher row was CAS-consumed by the fan-out
        # (the same exactly-once invariant the winner pin proves).
        from sqlmodel import select
        with Session(engine) as s:
            remaining = s.exec(
                select(JobWatcher).where(JobWatcher.job_id == work_id)
            ).all()
        assert remaining == [], (
            "the settled-mirror fan-out must consume the watcher "
            "row via the same CAS the winner takes"
        )


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
        method must thread a content/error slot — EXCEPT the
        non-result-summary branches (the F-5 narrowed shape: the
        ``cancelled`` / ``dead_letter`` / ``failed`` tokens all
        revert to the pre-C3 NO-Result shape).

        * ≥1 call with ``result_summary=`` — fails on revert of the
          round-3 fix (the revert removes the only result-bearing call).
        * ≤3 calls with NEITHER ``result_summary=`` NOR ``error=`` —
          the F-5 narrowed dispatch has 3 non-content branches
          (``failed`` / ``cancelled`` / ``dead_letter``), each
          without a ``result_summary=`` kwarg. Pre-F-5 the C3
          broaden had only 1 (the failed-arm call); F-5 widens
          the allowance to 3.

        F-5 NARROW (2026-09-25, ``fix/mission-terminal-watch-report-publish``):
        the C3 broaden threaded ``result_summary=last_content``
        for every non-failed terminal token (completed /
        settled / cancelled / dead_letter). F-5 NARROWS the C3
        scope back to ``{completed, settled}`` — stale
        mid-flight ``last_content`` captured while the mission
        was alive would render under ``Result:`` for
        ``cancelled`` and ``dead_letter`` envelopes (the F-5
        stale-content stranding class). The dispatch arm flips
        again to ``if _token in {"completed", "settled"}:`` —
        the SET of tokens whose envelope SHOULD carry the
        ``Result:`` line. The AST guard therefore asserts
        ``result_summary=`` calls MUST live in the BODY of
        ``if _token in {"completed", "settled"}:`` (the
        narrowed allow-list). Fails the moment anyone
        re-broadens to the C3 every-non-failed arm (stale
        mid-flight content under cancelled / dead_letter) OR
        re-narrows to the pre-C3 completed-only arm (settled
        envelopes WITHOUT a ``Result:`` line).
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
        assert len(naked) <= 3, (
            "F-5 narrowed guard: more than 3 notify_watchers calls "
            "in _dispatch_post_commit_side_effects carry NEITHER "
            "result_summary= NOR error= — the F-5 narrowed dispatch "
            "has 3 non-content branches (failed / cancelled / "
            "dead_letter), each without a ``result_summary=`` kwarg. "
            "A re-broaden to the C3 every-non-failed scope collapses "
            "this allowance back to 1 (only failed-arm call); an "
            "off-broaden pushes it past 3 and fails. Offending sites "
            f"at lines: {[c.lineno for c in naked]}"
        )

        # F-5 NARROW (2026-09-25): walk the AST and confirm every
        # ``notify_watchers(...)`` call carrying ``result_summary=``
        # is reachable ONLY under the BODY of
        # ``if _token in {"completed", "settled"}:``. Pre-F-5 (C3
        # broaden) this assertion required the call to live in
        # the ELSE branch of ``if _token == "failed":`` — every
        # non-failed terminal token was on the threading branch.
        # F-5 narrows that back to the canonical
        # ``{completed, settled}`` set; ``cancelled`` and
        # ``dead_letter`` revert to the pre-C3 NO-Result shape.
        # Re-broadening to the C3 every-non-failed scope triggers
        # this assertion (offending call sites are reported with
        # their arm status so the bug pattern is unambiguous from
        # the failure message).
        #
        # Implementation note: the calls returned by ``_notify_calls``
        # are derived from a FRESH ``ast.parse`` of the file. The
        # parent map MUST be built from the SAME parse so the
        # ``id()`` keys line up with the call nodes (each parse
        # produces a fresh node object with a fresh id).
        tree = ast.parse(CHILD_REPORTS_PATH.read_text(encoding="utf-8"))
        # Find the dispatch function in the SAME tree so the calls
        # returned by ``_notify_calls`` and the parent map share
        # node identity.
        dispatch_fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.AsyncFunctionDef)
             and n.name == "_dispatch_post_commit_side_effects"),
            None,
        )
        assert dispatch_fn is not None, (
            "_dispatch_post_commit_side_effects not found — layout drift"
        )
        # Re-derive the calls list from the same dispatch_fn (no
        # helper indirection — guarantees identity match).
        live_calls = [
            n for n in ast.walk(dispatch_fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "notify_watchers"
        ]
        live_with_result = [
            c for c in live_calls
            if any(kw.arg == "result_summary" for kw in c.keywords)
        ]
        parents_map = _build_parent_map(tree)
        offenders: list[tuple[int, str]] = []
        for call in live_with_result:
            status = _result_summary_call_under_completed_settled_arm(
                call, parents_map,
            )
            if status != "completed-settled-body":
                offenders.append((call.lineno, status))
        assert not offenders, (
            "F-5 NARROW guard violation: every "
            "notify_watchers(...result_summary=...) call inside "
            "_dispatch_post_commit_side_effects must be reachable "
            "ONLY under the BODY of `if _token in {\"completed\", "
            "\"settled\"}:` (the F-5 narrowed allow-list). "
            "Offending call sites (lineno, status): "
            f"{offenders}. A re-broaden to the C3 every-non-failed "
            "arm (re-introducing the pre-F-5 stale-mid-flight-content "
            "stranding under cancelled / dead_letter) OR a "
            "re-narrow to the pre-C3 completed-only arm "
            "(re-introducing the M3 settled guardrail) flips this "
            "guard cleanly with a shape-error message."
        )


# ── AST helpers (parent map + arm walker) ────────────────────────────────


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Map ``id(child_node) → parent_node`` for the entire module."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            # ``ast.walk`` deduplicates children that appear in
            # multiple lists (e.g. ``If.orelse`` may contain an
            # ``ast.If`` that ``ast.iter_child_nodes`` traverses
            # separately on the outer node). Use the FIRST parent
            # encountered; ``ast.walk`` yields parents before
            # children, so this is the syntactically correct parent.
            parents.setdefault(id(child), parent)
    return parents


def _is_token_in_completed_settled(test: ast.AST) -> bool:
    """Return True iff ``test`` is the membership expression
    ``_token in {"completed", "settled"}`` (F-5 NARROWED arm
    shape, 2026-09-25).

    C3 (2026-09-25) broadened the dispatch arm from
    ``if _token == "completed":`` to
    ``if _token == "failed":`` (every non-failed token threads).
    F-5 narrows the C3 scope back to ``{completed, settled}`` —
    stale mid-flight ``last_content`` captured while the mission
    was alive would render under ``Result:`` for ``cancelled``
    and ``dead_letter`` envelopes (the F-5 stale-content
    stranding class). The dispatch arm flips again to
    ``if _token in {"completed", "settled"}:`` — the SET of
    tokens whose envelope SHOULD carry the ``Result:`` line.

    Tolerates ``ast.Compare`` wrapping with a single ``In`` op
    and an ``ast.Set`` RHS whose elts are exactly the two
    ``ast.Constant`` strings ``"completed"`` and ``"settled"``
    (order-insensitive). Rejects ``==``, ``!=``, ``not in``,
    lists, tuples, and non-string RHS — so a future reviewer
    who accidentally broadens back to ``_token in {...all...}``
    OR narrows back to ``_token == "completed":`` flips this
    guard cleanly (FAIL with a clear shape-error message).
    """
    if not isinstance(test, ast.Compare):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.In):
        return False
    if len(test.comparators) != 1:
        return False
    left = test.left
    rhs = test.comparators[0]
    if not isinstance(left, ast.Name) or left.id != "_token":
        return False
    if not isinstance(rhs, ast.Set):
        return False
    expected = {"completed", "settled"}
    actual: set[str] = set()
    for elt in rhs.elts:
        if not isinstance(elt, ast.Constant):
            return False
        if not isinstance(elt.value, str):
            return False
        actual.add(elt.value)
    return actual == expected


def _result_summary_call_under_completed_settled_arm(
    call: ast.Call, parents_map: dict[int, ast.AST],
) -> str:
    """Return one of:
    * ``"completed-settled-body"`` — the call sits in the BODY
      of an ancestor ``If`` whose test is
      ``_token in {"completed", "settled"}``. PASS
      (F-5 narrowed scope: completed / settled tokens
      thread ``result_summary=last_content``).
    * ``"completed-settled-orelse"`` — the call sits in the
      ORELSE of the membership arm. FAIL.
    * ``"no-completed-settled-arm"`` — no ancestor ``If``
      matches the membership test. FAIL (re-narrow to
      ``if _token == "completed":`` — the pre-C3 narrow the
      F-5 narrowing reverses; or re-broaden to
      ``if _token == "failed":`` — the C3 broaden F-5 reverses).

    Note: this inverts the C3 ``_result_summary_call_under_non_failed_arm``
    guard. C3 broadened threading to every non-failed terminal
    token; F-5 narrows the threading back to
    ``{completed, settled}`` — the canonical tokens whose
    ``Result:`` line is content-meaningful. ``cancelled`` and
    ``dead_letter`` revert to the pre-C3 NO-Result shape
    (stale mid-flight ``last_content`` would mislead under
    those tokens).
    """
    cur = call
    parent = parents_map.get(id(cur))
    while parent is not None:
        if isinstance(parent, ast.If):
            if _is_token_in_completed_settled(parent.test):
                # Find which side of the If this subtree is on.
                in_body = any(
                    id(sibling) == id(cur) for sibling in parent.body
                )
                return (
                    "completed-settled-body" if in_body
                    else "completed-settled-orelse"
                )
        cur = parent
        parent = parents_map.get(id(cur))

    return "no-completed-settled-arm"


def _is_token_eq_failed(test: ast.AST) -> bool:
    """Return True iff ``test`` is the expression ``_token == "failed"``.

    Deprecated (F-5, 2026-09-25): the C3 (2026-09-25) dispatch
    arm was ``if _token == "failed":`` (every non-failed
    terminal token threads). F-5 narrows the C3 scope back to
    ``{completed, settled}`` — the dispatch arm flips again to
    ``if _token in {"completed", "settled"}:``. This predicate
    is retained for backwards-compat with any test fixture that
    still references it but the production AST guard uses
    :func:`_is_token_in_completed_settled` /
    :func:`_result_summary_call_under_completed_settled_arm`
    instead.

    Tolerates ast.Compare wrapping; rejects ``!=``, ``in``,
    ``is``, and any non-string RHS.
    """
    if not isinstance(test, ast.Compare):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    if len(test.comparators) != 1:
        return False
    left = test.left
    cmp = test.comparators[0]
    if not isinstance(left, ast.Name) or left.id != "_token":
        return False
    if not isinstance(cmp, ast.Constant) or cmp.value != "failed":
        return False
    return True


def _result_summary_call_under_non_failed_arm(
    call: ast.Call, parents_map: dict[int, ast.AST],
) -> str:
    """Deprecated (F-5, 2026-09-25): the C3 (2026-09-25) AST
    guard. The C3 broadened dispatch arm was
    ``if _token == "failed":`` (every non-failed terminal token
    threads ``result_summary=``). F-5 narrows the C3 scope back
    to ``{completed, settled}``; use
    :func:`_result_summary_call_under_completed_settled_arm`
    for the F-5 narrowed guard.

    Return shape (unchanged from C3):
    * ``"non-failed-body"`` — call in the ORELSE of
      ``if _token == "failed":``. PASS (C3 broaden).
    * ``"failed-body"`` — call in the BODY. FAIL.
    * ``"no-failed-arm"`` — no ancestor ``If`` matches. FAIL.
    """
    cur = call
    parent = parents_map.get(id(cur))
    while parent is not None:
        if isinstance(parent, ast.If):
            if _is_token_eq_failed(parent.test):
                # Find which side of the If this subtree is on.
                in_body = any(
                    id(sibling) == id(cur) for sibling in parent.body
                )
                return (
                    "non-failed-body" if not in_body else "failed-body"
                )
        cur = parent
        parent = parents_map.get(id(cur))

    return "no-failed-arm"


def _is_token_eq_completed(test: ast.AST) -> bool:
    """Return True iff ``test`` is the expression ``_token == "completed"``.

    Deprecated (C3, 2026-09-25): the pre-C3 ROUND-3 REVIEW NARROW
    used this predicate to verify the ``result_summary=`` call lived
    inside the BODY of ``if _token == "completed":``. C3 broadens
    the threading to every non-failed terminal token, so the
    relevant arm boundary is now ``_token == "failed":`` (the
    EXCLUDED branch). This predicate is retained for backwards-
    compat with any test fixture that still references it but the
    production AST guard uses ``_result_summary_call_under_non_failed_arm``
    instead.
    """
    if not isinstance(test, ast.Compare):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    if len(test.comparators) != 1:
        return False
    left = test.left
    cmp = test.comparators[0]
    if not isinstance(left, ast.Name) or left.id != "_token":
        return False
    if not isinstance(cmp, ast.Constant) or cmp.value != "completed":
        return False
    return True


def _result_summary_call_under_completed_arm(
    call: ast.Call, parents_map: dict[int, ast.AST],
) -> str:
    """Deprecated (C3, 2026-09-25): the pre-C3 ROUND-3 REVIEW NARROW
    guard. The pre-C3 narrow required the ``result_summary=`` call
    to live inside the BODY of ``if _token == "completed":``; C3
    broadens the threading so EVERY non-failed terminal token threads
    the kwarg. Use :func:`_result_summary_call_under_non_failed_arm`
    for the C3 broaden guard. This function is retained so the AST
    module still resolves any fixture that references it.

    Return shape (unchanged from pre-C3):
    * ``"completed-body"`` — call in the BODY of ``if _token == "completed":``. PASS (pre-C3).
    * ``"completed-orelse"`` — call in the ORELSE. FAIL (pre-C3 regression).
    * ``"no-completed-arm"`` — no ancestor ``If`` matches. FAIL.
    """
    cur = call
    parent = parents_map.get(id(cur))
    while parent is not None:
        if isinstance(parent, ast.If):
            if _is_token_eq_completed(parent.test):
                # Find which side of the If this subtree is on.
                in_body = any(
                    id(sibling) == id(cur) for sibling in parent.body
                )
                return "completed-body" if in_body else "completed-orelse"
        cur = parent
        parent = parents_map.get(id(cur))
    return "no-completed-arm"
