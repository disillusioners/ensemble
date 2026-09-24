"""DEFECT-5 (P1) — ``M2 mission_terminal`` opt-in gate pin tests.

Live evidence (2026-09-24 E2E, real Ari on real dev daemon at 5f4e35b0):
3 mid-flight emissions (event rows 2412, 2420 with genuine-progress
payloads), 2 subscription kinds (``in_progress`` and ``midflight_report``),
ZERO deliveries to Ari. Tester isolated by behavior to
``daemon/services/work_notifier.py:357-388`` — the M2
``mission_terminal`` opt-in handler runs
``held_for_mission += 1; continue`` for ANY non-terminal status
whenever the watcher includes ``mission_terminal``, irrespective of
explicit non-terminal subscription.

This file pins:

1. ``test_non_terminal_kind_delivers_through_mission_terminal_gate`` —
   watcher subscribed to BOTH a non-terminal kind (``in_progress``)
   AND ``mission_terminal``. A non-terminal ``in_progress`` event
   MUST deliver (NOT held). Pre-fix this held the row silently.

2. ``test_midflight_kind_delivers_through_mission_terminal_gate`` —
   the original 2026-09-24 E2E evidence: watcher subscribed to BOTH
   ``midflight_report`` AND ``mission_terminal``. A
   ``midflight_report`` event MUST deliver. Pre-fix held silently.

3. ``test_pure_mission_terminal_held_non_terminal_survives`` —
   preservation twin: a PURE ``mission_terminal`` subscription
   (no non-terminal kinds) on a NON-terminal status still HOLDS the
   row — the widening must not pass the terminal boundary. Pre-fix
   this passed by accident (the pre-fix code held everything); the
   post-fix must preserve this (don't widen past the dual-subscription
   boundary).

4. ``test_non_terminal_delivery_does_not_consume_multi_kind_row`` —
   the CRITICAL design trap: a non-terminal fire MUST NOT consume
   the row. Two-step invariant — fire ``in_progress`` on a watcher
   with ``[in_progress, mission_terminal]``, then fire
   ``completed``. The terminal fire MUST deliver exactly once
   (the post-N1 CAS guarantee is preserved at the row level — the
   non-terminal fire left the row in place; the terminal fire
   CAS-claims; no row loss across the kind boundary).

5. ``test_midflight_dual_subscription_does_not_consume_terminal_row`` —
   the actual E2E shape (canonical + mission_terminal), end-to-end:
   fire ``midflight_report`` then ``completed``. The terminal fire
   delivers exactly once — the row was held for the terminal event
   BECAUSE the watcher is dual-subscribed but the non-terminal fire
   didn't claim.

6. ``test_dual_terminal_kind_settles_mid_mission_delivers_once_with_claim``
   — A1 closure (reviewer-ratified, 2026-09-24): a dual-subscribed
   row carrying a TERMINAL-kind event (``settled``) +
   ``mission_terminal``. The receipt settles MID-MISSION (work
   record still ``processing``); the watcher MUST deliver ONCE at
   receipt-settle with the CAS claim, and a subsequent
   mission-terminal flip (``completed``) produces ZERO additional
   fires (the row was consumed). Pre-fix this was held silently
   until the mission flip; post-fix the
   ``mission_terminal_opt_in and not standard_match`` gate does not
   hold when an explicit terminal-kind subscription matches the
   firing kind.

The recipe mirrors ``test_work_notifier_n1_pin.py`` (file-backed
SQLite, real ``JobWatcherRepository``, ``WorkResolverService`` patched
to a synthetic ``WorkRecord``, ``AsyncMock`` for the manager's
``enqueue_message``) so the actual SQL atomicity of the CAS path is
exercised. Status display + Result/Progress/Error slot rendering are
asserted at the message envelope level.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import WorkRecord, WorkResolverService


# ── Fixtures + helpers (minimal — same recipe as test_work_notifier_n1_pin) ──


@pytest.fixture
def defect5_engine(tmp_path):
    db_path = tmp_path / "defect5.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng


def _seed_instances(engine, *names: str) -> None:
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        for n in names:
            s.add(Instance(
                instance_id=n, agent_id="worker",
                agent_dir="/tmp/w", agent_name=n,
                project_id="p1", status="running",
                created_at=now_iso, updated_at=now_iso,
                paused_at=None, parent_id=None,
            ))
        s.commit()


def _seed_task(engine, *, work_id: str, instance_id: str, status: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(Task(
            work_id=work_id, task_type="process_report",
            instance_id=instance_id, status=status,
            created_at=now, is_deferred=False,
        ))
        s.commit()


def _add_watch(engine, *, work_id: str, instance_id: str, watch_events: list[str]) -> None:
    with Session(engine) as s:
        s.add(JobWatcher(
            job_id=work_id, instance_id=instance_id,
            watch_events=watch_events,
        ))
        s.commit()


def _patch_running(resolver, *, wid: str, job_type: str = "task"):
    """Synthesize a processing WorkRecord so the M2 mission_live check
    sees non-terminal liveness (processing)."""
    from datetime import datetime, timezone
    record = WorkRecord(
        work_id=wid, kind="job", status="processing",
        instance_id="inst-prod-1", project_id="p1",
        agent_id="worker", result_summary=None,
        error=None, created_at=datetime.now(timezone.utc),
        job_type=job_type, mission_liveness=None,
    )
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


def _patch_complete(resolver, *, wid: str, job_type: str = "task"):
    from datetime import datetime, timezone
    record = WorkRecord(
        work_id=wid, kind="job", status="completed",
        instance_id="inst-prod-1", project_id="p1",
        agent_id="worker", result_summary=None,
        error=None, created_at=datetime.now(timezone.utc),
        job_type=job_type, mission_liveness=None,
    )
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


class _NoOpJobRepo:
    """Minimal stand-in for ``JobRepository`` — explicit allowlist (B2).

    Pre-B2 this class's ``__getattr__`` auto-returned a no-op callable
    for ANY attribute, so a future typo'd repo read silently passed.
    B2 (reviewer-ratified A1+B2 closure, 2026-09-24) converts to an
    explicit allowlist: only ``get`` (used by ``resolve_work``) is
    supported, and any other attribute raises ``AttributeError`` —
    a typo is more useful as an explicit failure than a silent
    ``None`` downstream. ``WorkResolverService.list_work`` accesses
    ``self._job_repo.engine`` on the JobItem SELECT branch, but the
    defect5 pins never call ``list_work`` so ``engine`` is not in
    the allowlist (intentional — if a future pin calls it, the
    resulting ``SQLModelSession(None)`` failure surfaces loudly
    instead of silently passing).
    """

    def get(self, _job_id):
        return None

    def __getattr__(self, name: str):
        raise AttributeError(
            f"_NoOpJobRepo: unknown attribute {name!r} — "
            f"the explicit allowlist is intentional (B2). "
            f"Add the attribute to the class body if it is required."
        )


@pytest.fixture
def defect5_components(defect5_engine):
    watcher_repo = JobWatcherRepository(defect5_engine)
    task_repo = TaskRepository(defect5_engine)
    instance_repo = SQLModelInstanceRepository(defect5_engine)
    resolver = WorkResolverService(task_repo, _NoOpJobRepo(), instance_repo)
    instance_manager = MagicMock()
    instance_manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-defect5")
    )
    return {
        "engine": defect5_engine,
        "watcher_repo": watcher_repo,
        "task_repo": task_repo,
        "resolver": resolver,
        "instance_manager": instance_manager,
    }


# ── DEFECT-5 pins ─────────────────────────────────────────────────────────


class TestM2GateDualSubscriptionDelivers:
    """Watchers subscribed to BOTH a non-terminal kind AND
    ``mission_terminal`` must receive the non-terminal kind — the
    M2 hold gate must NOT trigger when an explicit non-terminal
    subscription matches.
    """

    @pytest.mark.asyncio
    async def test_in_progress_kind_delivers_through_mission_terminal_gate(
        self, defect5_components,
    ):
        """Watcher subscribes to ``[in_progress, mission_terminal]``.
        A non-terminal ``in_progress`` event MUST deliver (one shot).
        The row is NOT consumed (no CAS claim on non-terminal).
        """
        engine = defect5_components["engine"]
        watcher_repo = defect5_components["watcher_repo"]
        resolver = defect5_components["resolver"]
        instance_manager = defect5_components["instance_manager"]
        wid = f"wid-{uuid4().hex[:8]}"
        _seed_instances(engine, "inst-prod-1", "watcher-1")
        _seed_task(engine, work_id=wid, instance_id="inst-prod-1",
                   status=TaskStatus.RUNNING.value)
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["in_progress", "mission_terminal"])

        original = _patch_running(resolver, wid=wid)
        try:
            notified = await notify_work_watchers(
                wid, "in_progress", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
                progress="50%",
            )
        finally:
            resolver.resolve_work = original

        # Fires — pre-fix the M2 gate held silently (notified=0).
        assert notified == 1, (
            "DEFECT-5: dual-subscribed watcher must receive the "
            "non-terminal kind; M2 hold gate was holding any "
            "mission_terminal-tagged watcher regardless of explicit "
            "non-terminal subscription. Got 0 deliveries."
        )
        assert instance_manager.enqueue_message.await_count == 1
        call = instance_manager.enqueue_message.await_args
        msg = call.kwargs["message"]
        assert "[JOB_EVENT]" in msg
        assert "in progress" in msg  # ⟳ glyph variant is fine too
        # Progress: line shown for in_progress.
        assert "Progress:" in msg
        # Row survives the non-terminal fire (no CAS consumed).
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 1, (
            "DEFECT-5 design trap: non-terminal delivery must NOT "
            "consume the multi-kind row; the terminal mission_terminal "
            "fire would lose its row otherwise."
        )

    @pytest.mark.asyncio
    async def test_midflight_report_kind_delivers_through_mission_terminal_gate(
        self, defect5_components,
    ):
        """The original 2026-09-24 E2E evidence shape:
        watcher subscribed to ``[midflight_report, mission_terminal]``.
        A ``midflight_report`` event with a genuine-progress payload
        MUST deliver (event row 2412/2420 emitted; zero delivery
        pre-fix)."""
        engine = defect5_components["engine"]
        watcher_repo = defect5_components["watcher_repo"]
        resolver = defect5_components["resolver"]
        instance_manager = defect5_components["instance_manager"]
        wid = f"wid-{uuid4().hex[:8]}"
        _seed_instances(engine, "inst-prod-1", "watcher-1")
        _seed_task(engine, work_id=wid, instance_id="inst-prod-1",
                   status=TaskStatus.RUNNING.value)
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["midflight_report", "mission_terminal"])

        original = _patch_running(resolver, wid=wid)
        try:
            notified = await notify_work_watchers(
                wid, "midflight_report", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
                # Mid-flight payload (the genuine-progress content the
                # agent emitted at runtime — never reached Ari pre-fix).
                # The production midflight_qa.py code passes BOTH
                # ``progress=summary`` AND ``result_summary=summary``
                # because status="midflight_report" goes through the
                # ELSE branch (not in_progress) where Progress: is not
                # rendered — the Result: line carries the payload.
                progress="CHECKPOINT4C: 60-line file written, commit f27e",
                result_summary="CHECKPOINT4C: 60-line file written, commit f27e",
            )
        finally:
            resolver.resolve_work = original

        assert notified == 1
        assert instance_manager.enqueue_message.await_count == 1
        call = instance_manager.enqueue_message.await_args
        msg = call.kwargs["message"]
        assert "[JOB_EVENT]" in msg
        assert "mid-flight report" in msg
        # Progress: line includes the mid-flight payload.
        assert "CHECKPOINT4C" in msg
        # Row is preserved (no CAS consumed on non-terminal).
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 1

    @pytest.mark.asyncio
    async def test_pure_mission_terminal_subscription_held_on_non_terminal(
        self, defect5_components,
    ):
        """Preservation twin: a PURE ``mission_terminal`` subscription
        (no non-terminal kinds) on a NON-terminal status HOLDS the
        row. The widening (test 1+2 above) must not pass the
        terminal boundary — pure mission_terminal watchers still
        wait for the terminal fire.
        """
        engine = defect5_components["engine"]
        watcher_repo = defect5_components["watcher_repo"]
        resolver = defect5_components["resolver"]
        instance_manager = defect5_components["instance_manager"]
        wid = f"wid-{uuid4().hex[:8]}"
        _seed_instances(engine, "inst-prod-1", "watcher-1")
        _seed_task(engine, work_id=wid, instance_id="inst-prod-1",
                   status=TaskStatus.RUNNING.value)
        # PURE mission_terminal subscription.
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["mission_terminal"])

        original = _patch_running(resolver, wid=wid)
        try:
            notified = await notify_work_watchers(
                wid, "in_progress", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
                progress="50%",
            )
        finally:
            resolver.resolve_work = original

        # Pure mission_terminal HELD on non-terminal — no notify.
        assert notified == 0, (
            "Pure mission_terminal subscription must hold on "
            "non-terminal statuses (preserve the M2 'wait for the "
            "terminal fire' contract). Got non-zero notifications."
        )
        assert instance_manager.enqueue_message.await_count == 0
        # Row survives — terminal fire will claim it.
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 1


class TestM2GateMultiKindRowSurvivesTerminal:
    """Design trap: non-terminal delivery on a multi-kind row must
    NOT consume the row. Terminal fire on the same row must still
    deliver exactly once (the post-N1 exactly-once invariant).
    """

    @pytest.mark.asyncio
    async def test_in_progress_then_completed_multi_kind_row(
        self, defect5_components,
    ):
        """Two-step: fire ``in_progress`` (non-terminal) on a watcher
        with ``[in_progress, mission_terminal]``. Then fire
        ``completed`` (terminal). The non-terminal fire leaves the
        row in place; the terminal fire CAS-claims and delivers
        exactly once. Pre-fix: non-terminal was silently held (0
        deliveries on in_progress); post-fix the design trap is
        satisfied.
        """
        engine = defect5_components["engine"]
        watcher_repo = defect5_components["watcher_repo"]
        resolver = defect5_components["resolver"]
        instance_manager = defect5_components["instance_manager"]
        wid = f"wid-{uuid4().hex[:8]}"
        _seed_instances(engine, "inst-prod-1", "watcher-1")
        _seed_task(engine, work_id=wid, instance_id="inst-prod-1",
                   status=TaskStatus.RUNNING.value)
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["in_progress", "mission_terminal"])

        # Step 1: non-terminal fire.
        original = _patch_running(resolver, wid=wid)
        try:
            n_inprog = await notify_work_watchers(
                wid, "in_progress", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
                progress="50%",
            )
        finally:
            resolver.resolve_work = original
        assert n_inprog == 1
        # Row SURVIVES — terminal event still has its claim.
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 1

        # Step 2: terminal fire on the same row.
        original2 = _patch_complete(resolver, wid=wid)
        try:
            n_term = await notify_work_watchers(
                wid, "completed", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
            )
        finally:
            resolver.resolve_work = original2

        # Terminal fires exactly once — CAS consumed the row.
        assert n_term == 1
        remaining_after = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining_after) == 0, (
            "multi-kind row must be CAS-claimed by the terminal fire "
            "after the non-terminal fire left it in place."
        )
        # Total deliveries: 1 non-terminal + 1 terminal = 2 enqueue.
        assert instance_manager.enqueue_message.await_count == 2

    @pytest.mark.asyncio
    async def test_dual_terminal_kind_settles_mid_mission_delivers_once_with_claim(
        self, defect5_components,
    ):
        """A1 (reviewer-ratified A1+B2 closure, 2026-09-24): a
        dual-subscription row carrying a TERMINAL-kind event
        (``settled``) + ``mission_terminal``. The receipt settles
        MID-MISSION (the work record's status is still
        ``processing``), and the watcher MUST deliver EXACTLY ONCE
        AT RECEIPT-SETTLE with the CAS claim. A subsequent
        mission-terminal flip (the work reaches true terminal
        liveness, ``completed``) produces NO second fire — the row
        was consumed by the receipt-settle CAS.

        Post-1e12944a gate: ``mission_terminal_opt_in and not
        standard_match``. Here ``settled`` IS in ``watch_events``
        (``standard_match=True``), so the M2 hold gate does NOT
        hold — the row falls through to ``matching``, step 3's CAS
        claims it (``_is_terminal("settled")`` is True — ``settled``
        is canonical terminal per the comment at
        ``work_notifier.py:417-419``), and step 4 delivers. The
        pre-fix code held this row silently until the mission
        flip; the post-fix code delivers at receipt-settle because
        the explicit terminal-kind subscription matches, so the
        hold gate does not trigger. Reviewer explicitly approved
        this delivery semantic (same philosophy as DEFECT-5 — an
        explicit subscription delivers).
        """
        engine = defect5_components["engine"]
        watcher_repo = defect5_components["watcher_repo"]
        resolver = defect5_components["resolver"]
        instance_manager = defect5_components["instance_manager"]
        wid = f"wid-{uuid4().hex[:8]}"
        _seed_instances(engine, "inst-prod-1", "watcher-1")
        _seed_task(engine, work_id=wid, instance_id="inst-prod-1",
                   status=TaskStatus.RUNNING.value)
        # Dual subscription: TERMINAL-kind ``settled`` + ``mission_terminal``.
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["settled", "mission_terminal"])

        # Step 1: receipt settles MID-MISSION — work record is still
        # ``processing`` (resolver patch below), so the mission is not
        # yet terminal. The watcher matches on ``settled`` (explicit
        # terminal-kind subscription), the M2 hold gate does not
        # trigger (``standard_match=True``), the CAS claim runs
        # (``_is_terminal("settled")`` is True), and the watcher row
        # is consumed.
        original = _patch_running(resolver, wid=wid)
        try:
            n_settled = await notify_work_watchers(
                wid, "settled", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
            )
        finally:
            resolver.resolve_work = original

        # Delivers EXACTLY ONCE at receipt-settle with the CAS claim.
        assert n_settled == 1, (
            "A1: dual-subscribed [settled, mission_terminal] watcher "
            "must deliver ONCE at receipt-settle even mid-mission — "
            "the M2 hold gate does NOT hold when the firing kind "
            "matches an explicit terminal-kind subscription "
            "(`mission_terminal_opt_in and not standard_match` is "
            "False here because `settled` IS in watch_events). "
            "Pre-fix the row was held until the mission-terminal "
            "flip; the post-fix row delivers at receipt-settle and "
            "the CAS consumes it. Got 0 deliveries."
        )
        assert instance_manager.enqueue_message.await_count == 1
        # CAS consumed the row — no watchers left for the mission flip.
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 0, (
            "A1: receipt-settle CAS must consume the row — the "
            "subsequent mission-terminal flip must find zero "
            "watchers (no second fire)."
        )

        # Step 2: mission-terminal flip — work reaches true terminal
        # liveness, the resolver returns ``completed``. A
        # ``completed`` event would normally match the
        # ``mission_terminal`` subscription on a row that survived;
        # but the row was already consumed by the receipt-settle
        # CAS, so there is nothing left to deliver to.
        original2 = _patch_complete(resolver, wid=wid)
        try:
            n_term = await notify_work_watchers(
                wid, "completed", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
            )
        finally:
            resolver.resolve_work = original2

        # NO second fire — row was consumed by receipt-settle CAS.
        assert n_term == 0, (
            "A1: subsequent mission-terminal flip on a row already "
            "consumed by receipt-settle CAS must produce ZERO "
            "deliveries — get_watchers_for_job returns 0 rows so "
            "the matching bucket is empty. Got non-zero deliveries."
        )
        # Total deliveries: 1 (settled), not 2.
        assert instance_manager.enqueue_message.await_count == 1, (
            "A1: enqueue_message must be called exactly once across "
            "receipt-settle + mission-terminal flip — the row was "
            "CAS-claimed at receipt-settle."
        )

    @pytest.mark.asyncio
    async def test_midflight_then_completed_canonical_subscription(
        self, defect5_components,
    ):
        """The original E2E shape: canonical subscription
        ``[mission_terminal, midflight_report]``. The mission picks
        a canonical midflight checkin; the row is dual-subscribed.
        The non-terminal fire (midflight_report) MUST deliver; the
        row must SURVIVE for the terminal fire. Then the terminal
        fire delivers exactly once. This is the scenario S5-P1 in
        the tester evidence.
        """
        engine = defect5_components["engine"]
        watcher_repo = defect5_components["watcher_repo"]
        resolver = defect5_components["resolver"]
        instance_manager = defect5_components["instance_manager"]
        wid = f"wid-{uuid4().hex[:8]}"
        _seed_instances(engine, "inst-prod-1", "watcher-1")
        _seed_task(engine, work_id=wid, instance_id="inst-prod-1",
                   status=TaskStatus.RUNNING.value)
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["mission_terminal", "midflight_report"])

        # Midflight fire (non-terminal).
        original = _patch_running(resolver, wid=wid)
        try:
            n_mf = await notify_work_watchers(
                wid, "midflight_report", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
                progress="CHECKPOINT5P1: commit f27e, resume advisory.",
                result_summary="CHECKPOINT5P1: commit f27e, resume advisory.",
            )
        finally:
            resolver.resolve_work = original
        assert n_mf == 1, (
            "DEFECT-5: midflight_report MUST deliver on a "
            "canonical+midflight_report watcher. Pre-fix silently "
            "held (the M2 mission_terminal opt-in gate held any "
            "mission_terminal-tagged watcher regardless of explicit "
            "non-terminal subscription)."
        )
        # Row survives for the terminal fire.
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 1

        # Terminal fire — exactly once.
        original2 = _patch_complete(resolver, wid=wid)
        try:
            n_term = await notify_work_watchers(
                wid, "completed", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
            )
        finally:
            resolver.resolve_work = original2
        assert n_term == 1
        assert watcher_repo.get_watchers_for_job(wid) == []
        # Total deliveries: 1 midflight + 1 terminal.
        assert instance_manager.enqueue_message.await_count == 2
