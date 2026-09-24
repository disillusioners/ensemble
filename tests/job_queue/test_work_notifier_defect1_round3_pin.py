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
    """ROUND-3 REVIEW M3 GUARD — settled envelope through
    ``_dispatch_post_commit_side_effects`` must NOT carry a ``Result:``
    line. The narrowing of the round-3 fix restricts
    ``result_summary=last_content`` to the ``_token == "completed"``
    arm only; a settled mirror (kind='job', job_type='message') flowing
    through the same fan-out falls to the else-branch and gets NO
    content kwarg — and the resolver returns no content for mirror
    rows either — so the delivered body is the by-design
    NO-Result-block shape (M3 mission-class contract).
    """

    @pytest.mark.asyncio
    async def test_settled_message_kind_dispatch_envelope_has_no_result_block(
        self, engine, task_repo, job_repo, instance_repo, watcher_repo,
        enqueue_mock,
    ):
        """Real CAS path — real ``ChildReportsService`` → real
        ``JobQueueService`` → real ``notify_work_watchers``. A
        message-kind mirror WorkRecord (per_kind_status_for →
        'settled') flowing through
        ``_dispatch_post_commit_side_effects`` delivers ONE
        envelope carrying the header + Agent + ``settled ✓`` line
        and NO ``Result:`` block.

        Pre-narrow (else-branch threading every non-failed token),
        this exact scenario delivered a body carrying
        ``Result:\\n<last_content>`` — which violates the M3
        mission-class by-design NO-Result-block shape and creates
        same-token envelope inconsistency between the notifier
        direct call path and the child-reports fan-out (the notifier
        path's guard, ``test_settled_message_kind_no_result_block``
        in ``test_work_notifier_defect1_pins.py``, fires through
        the same notifier but WITHOUT the result-threading else).
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
        # actually deliver. The M3 settled-mirror contract: the
        # watcher subscribes to the ``settled`` event explicitly
        # (the per-kind dispatch surfaces ``settled`` as the
        # canonical status on a mirror row). Default
        # ``watch_events`` shape is the same literal
        # ``["completed"]`` the winner pin seeds — that filter
        # excludes ``settled`` terminal fires (status must be in
        # ``watch_events`` for ``standard_match`` to fire), which
        # is why a defaulted watch would yield zero deliveries on
        # this dispatch.
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
            "settled-mirror M3 envelope must carry the "
            f"``settled ✓`` glyph; got {body!r}"
        )
        assert f"Agent: worker" in body
        # ── THE NARROW HOLDS HERE ──
        # The dispatch's else-branch (every non-completed token)
        # now passes NO ``result_summary`` kwarg; the resolver
        # returns ``result_summary=None`` for mirror rows; so
        # the notifier's ``effective_result`` is ``None`` and the
        # body has no ``Result:`` line.
        assert "Result:" not in body, (
            "ROUND-3 M3 GUARD: a settled mirror envelope through "
            "_dispatch_post_commit_side_effects MUST NOT carry a "
            "Result: line — the narrowed else-branch must not "
            "pre-thread result_summary=last_content into a "
            "settled-status call. Pre-narrow regression: the "
            "else-branch fed last_content into the settled call "
            f"too. Got body={body!r}"
        )
        # No error slot either (we passed status='settled', not
        # 'failed', and no error kwarg upstream).
        assert "Error:" not in body
        # And ``LAST_CONTENT`` must not leak under any prefix —
        # the in-scope assistant reply is supposed to ride the
        # completed-arm delivery, not the settled envelope.
        assert LAST_CONTENT not in body, (
            "ROUND-3 GUARD: LAST_CONTENT must not appear in a "
            "settled envelope — content is only valid for the "
            "``completed`` arm. Got body={body!r}"
        )
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
        method must thread a content/error slot — EXCEPT the single
        intentional failed-arm call (no error text is in scope there).

        * ≥1 call with ``result_summary=`` — fails on revert of the
          round-3 fix (the revert removes the only result-bearing call).
        * ≤1 call with NEITHER ``result_summary=`` NOR ``error=`` — a
          NEW unthreaded success-status call site pushes this to 2 and
          fails, closing the "new caller repeats the defect" gap.

        ROUND-3 REVIEW NARROW assertion (2026-09-24): a
        ``notify_watchers`` call carrying ``result_summary=`` must
        live inside the BODY of an ``If _token == "completed":`` arm —
        NOT in the orelse (which carries settled / cancelled /
        dead_letter tokens) and NOT at the function's top level. Fails
        the moment anyone re-widens the threading to a non-completed
        token (the pre-narrow else-branch gap that produced settled
        envelopes WITH a ``Result:`` line).
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

        # ROUND-3 REVIEW NARROW (2026-09-24): walk the AST and confirm
        # every ``notify_watchers(...)`` call carrying
        # ``result_summary=`` is reachable ONLY under the
        # ``if _token == "completed":`` body. Pre-narrow (commit
        # 5292eb99) threaded ``result_summary=last_content`` in the
        # else-arm, which carried settled / cancelled / dead_letter
        # tokens too — settled envelopes rendered WITH a ``Result:``
        # line, breaking the M3 mission-class by-design NO-Result-block
        # shape. Re-widening triggers this assertion (offending call
        # sites are reported with their arm status so the bug pattern
        # is unambiguous from the failure message).
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
            status = _result_summary_call_under_completed_arm(
                call, parents_map,
            )
            if status != "completed-body":
                offenders.append((call.lineno, status))
        assert not offenders, (
            "ROUND-3 REVIEW NARROW guard violation: every "
            "notify_watchers(...result_summary=...) call inside "
            "_dispatch_post_commit_side_effects must be reachable "
            "ONLY under the `if _token == \"completed\":` body. "
            "Offending call sites (lineno, status): "
            f"{offenders}. A `settled` / `cancelled` / `dead_letter` "
            "envelope reaching result_summary= would render WITH a "
            "Result: line and break the M3 mission-class by-design "
            "NO-Result-block shape."
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


def _is_token_eq_completed(test: ast.AST) -> bool:
    """Return True iff ``test`` is the expression ``_token == "completed"``.

    Tolerates ast.Compare wrapping (the test may be the only operand
    of an outer ``If.test``); rejects ``!=``, ``in``, ``is``, and
    any non-string RHS — so a future reviewer who accidentally
    widens to ``if _token in {"completed", "settled"}:`` flips this
    guard cleanly (FAIL with a clear shape-error message).
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
    """Return one of:
    * ``"completed-body"`` — the call sits in the BODY of an ancestor
      ``If`` whose test is ``_token == "completed"``. PASS.
    * ``"completed-orelse"`` — an ancestor ``If`` has the completed
      test but the call is in ORELSE (FAIL). Pre-narrow regression
      pattern.
    * ``"no-completed-arm"`` — no ancestor ``If`` matches the
      completed test (FAIL). Wider re-threading or top-level leak.
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
