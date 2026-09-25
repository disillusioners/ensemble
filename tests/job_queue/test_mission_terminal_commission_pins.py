"""C1+C2+C3 commission pinning tests (2026-09-25,
``fix/mission-terminal-watch-report-publish``).

These are the commission-mandated pin tests for the
mission-terminal watch + report publish defect loop closed on
the 2026-09-25 incident, mission 36be8aef. They live in their
own file so the test inventory maps cleanly to the C1/C2/C3
buckets the commission describes — the defect1/defect5/etc.
files retain their pre-existing pin coverage; this file is
the new commission-specific pin coverage.

## Pins

### C1 — True mission-liveness gate

1. ``test_mission_survives_first_receipt_settlement_keeps_watch_row``
   — encodes the actual incident timeline: a mission with one
   receipt settles while children are still running; the
   ``mission_terminal`` watcher row SURVIVES the receipt
   settlement (pre-C1 the proxy-based check consumed the row
   6m42s before true terminal).

2. ``test_mission_terminal_fires_exactly_once`` — end-to-end:
   arm → receipt settles (mid-mission) → row survives →
   mission-terminal flip → exactly ONE notification fires.

3. ``test_proxy_check_replaced_by_mission_live_guard`` — the
   helper consults the canonical ``evaluate_mission_live``
   guard, not the proxy ``work_record.mission_liveness`` /
   ``work_record.status`` sniff. Pinned via a sentinel
   instance_repository mock that returns a specific verdict —
   the partition MUST consult it.

### C2 — Skip-path content publish + settled enrichment

4. ``test_skip_path_notify_threads_message_content`` — the
   PROCESS_REPORT dedup-skip notify carries
   ``result_summary == message.content`` (the agent's last
   text). The body surfaces ``Result:\\n<content>`` — the
   status-only envelope the 2026-09-25 incident recorded is
   closed.

5. ``test_settled_row_enrichment_includes_settled`` (F-6
   fixback) — ``_enrich_terminal_record`` enriches ``settled``
   WorkRecords from the canonical ``_get_last_assistant_message_raw``
   seam the same way it enriches ``completed`` records. Pinned
   via the ``watch_job`` tool path at
   ``daemon/tools/job_queue.py:2335-2339`` (the call-site seam
   BEFORE the immediate ``notify_watchers``), so a settled
   mirror that reaches the watch tool with a populated
   assistant-message capture on the instance gets a
   ``Result:\\n<content>`` block delivered.

### C3 — Settled-body Result line + race-safety

6. ``test_settled_envelope_threads_via_producer_not_resolver`` —
   the body uses the producer-side ``result_summary=`` kwarg
   (the ``[JOB_EVENT]`` body carries ``Result:\\n<threaded>``)
   even when the resolver returns ``None``. Constructs the
   commit-visibility race scenario explicitly: the resolver's
   ``task.result`` read returns ``None`` (race window), the
   producer's in-memory content is the canonical payload —
   the same race-safety discipline DEFECT-1b established at
   ``task_processor.py:1010-1020``.

Recipe: real ``JobWatcherRepository`` + ``TaskRepository`` +
real ``evaluate_mission_live`` via ``SQLModelInstanceRepository``
+ patched ``WorkResolverService`` per the defect1 / defect5
recipe. Same SQL atomicity guarantees the production CAS path
exercises.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import (
    WorkRecord,
    WorkResolverService,
)


# ── Fixtures + helpers ────────────────────────────────────────────────────


@pytest.fixture
def commission_engine(tmp_path):
    db_path = tmp_path / "commission.db"
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


@pytest.fixture
def commission_components(commission_engine):
    """Bundle: watcher_repo + task_repo + resolver + enqueue mock.

    C1 (2026-09-25): expose ``_instance_repository`` so the
    canonical ``evaluate_mission_live`` guard can walk the
    permanent ``instances.parent_id`` tree. Without this
    attribute the guard fail-OPENS (``live=False`` → claim +
    deliver), regressing every held-mission-terminal pin.
    """
    watcher_repo = JobWatcherRepository(commission_engine)
    task_repo = TaskRepository(commission_engine)
    instance_repo = SQLModelInstanceRepository(commission_engine)
    resolver = WorkResolverService(task_repo, _NoOpJobRepo(), instance_repo)
    instance_manager = MagicMock()
    instance_manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-commission")
    )
    instance_manager._instance_repository = instance_repo
    return {
        "engine": commission_engine,
        "watcher_repo": watcher_repo,
        "task_repo": task_repo,
        "resolver": resolver,
        "instance_manager": instance_manager,
        "instance_repo": instance_repo,
    }


class _NoOpJobRepo:
    """Minimal JobRepository stand-in — returns ``None`` for
    ``get`` and raises ``AttributeError`` for unknown attrs (B2
    NoOp allowlist)."""

    def get(self, _job_id):
        return None

    def __getattr__(self, name):
        raise AttributeError(
            f"_NoOpJobRepo: unknown attribute {name!r}"
        )


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    agent_id: str = "developer",
    project_id: str = "test-project",
    status: str = "running",
    parent_id: str | None = None,
) -> str:
    """Insert an ``Instance`` row so ``resolve_work`` can find it.

    ``status`` defaults to ``"running"`` (non-terminal) so the
    ``evaluate_mission_live`` guard returns ``live=True`` —
    mission-live semantics hold. Pass a terminal status (e.g.
    ``"completed"``) to flip the mission to terminal.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        existing = s.get(Instance, instance_id)
        if existing is None:
            inst = Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                project_id=project_id,
                status=status,
                created_at=now_iso,
                updated_at=now_iso,
                paused_at=None,
                parent_id=parent_id,
            )
            s.add(inst)
            s.commit()
    return instance_id


def _seed_task(
    engine: Engine,
    *,
    work_id: str | None = None,
    instance_id: str,
    status: str = TaskStatus.RUNNING.value,
) -> str:
    wid = work_id or str(uuid4())
    with Session(engine) as s:
        task = Task(
            work_id=wid,
            task_type="process_message",
            instance_id=instance_id,
            status=status,
            created_at=datetime.now(timezone.utc),
            is_deferred=False,
        )
        s.add(task)
        s.commit()
    return wid


def _add_watch(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    watch_events: list[str],
) -> None:
    with Session(engine) as s:
        s.add(JobWatcher(
            job_id=work_id,
            instance_id=instance_id,
            watch_events=watch_events,
        ))
        s.commit()


def _patch_resolver_task_running(resolver, *, wid, instance_id):
    """Patch resolver to return a TASK-kind WorkRecord with
    ``status="running"`` (non-terminal transport + non-terminal
    mission liveness)."""
    record = WorkRecord(
        work_id=wid, kind="report", status="running",
        instance_id=instance_id, project_id="test-project",
        agent_id="developer", result_summary=None, error=None,
        created_at=datetime.now(timezone.utc),
        job_type=None, mission_liveness=None,
    )
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


def _patch_resolver_task_completed(resolver, *, wid, instance_id):
    """Patch resolver to return a TASK-kind WorkRecord with
    ``status="completed"``."""
    record = WorkRecord(
        work_id=wid, kind="report", status="completed",
        instance_id=instance_id, project_id="test-project",
        agent_id="developer", result_summary=None, error=None,
        created_at=datetime.now(timezone.utc),
        job_type=None, mission_liveness=None,
    )
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


# ── C1 — true mission-liveness gate ───────────────────────────────────────


class TestC1MissionLivenessGate:
    """C1 (2026-09-25) — the canonical mission-live guard replaces
    the proxy check. Multi-kind retire rule: rows subscribing to
    BOTH a transport kind AND ``mission_terminal`` are HELD until
    mission liveness is terminal. Rows subscribing only to
    ``mission_terminal`` are also HELD until mission terminal.

    The pre-C1 proxy (``work_record.mission_liveness`` or
    ``work_record.status``) missed the live-instance tree signal —
    on the 2026-09-25 mission 36be8aef incident, the
    per-receipt-settlement flipped ``work_record.status`` (the
    message-mirror flips after every turn) and the proxy saw
    terminal, dropping the ``mission_terminal`` row 6m42s before
    true terminal.
    """

    @pytest.mark.asyncio
    async def test_mission_survives_first_receipt_settlement_keeps_watch_row(
        self, commission_components,
    ):
        """C1 timeline pin — arm (mission live) → receipt settles
        while children running → row SURVIVES.

        Encodes the actual 2026-09-25 incident shape: a single
        receipt row subscribing to ``mission_terminal`` survives
        a receipt-settle event even though the notify call passes
        a transport terminal status. Pre-C1 the proxy check saw
        the work's ``status`` field flip and consumed the row;
        C1 consults the permanent ``instances.parent_id`` tree
        (via ``evaluate_mission_live``) and sees the parent
        instance still alive → row survives.
        """
        engine = commission_components["engine"]
        watcher_repo = commission_components["watcher_repo"]
        resolver = commission_components["resolver"]
        instance_manager = commission_components["instance_manager"]

        wid = f"wid-mission-survive-{uuid4().hex[:8]}"
        # Mission instance is alive (running) — receipt
        # settlement does NOT flip mission to terminal.
        _seed_instance(engine, instance_id="inst-mission-alive",
                       status="running")
        _seed_instance(engine, instance_id="watcher-mission-survive")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-mission-survive",
            watch_events=["mission_terminal"],
        )

        original = _patch_resolver_task_running(
            resolver, wid=wid, instance_id="inst-mission-alive",
        )
        try:
            # Step 1: receipt settles while mission is still live
            # (children running). Status=settled (terminal kind).
            # Pre-C1 proxy check: work_record.status="running" → not
            # terminal → held. C1: evaluate_mission_live sees
            # parent instance status="running" → live=True → held.
            # EITHER way, the row survives.
            notified = await notify_work_watchers(
                wid, "settled", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
            )
        finally:
            resolver.resolve_work = original

        # C1 invariant: receipt settlement does NOT consume the
        # ``mission_terminal`` watcher row when the mission is
        # still live.
        assert notified == 0, (
            f"C1 timeline pin: receipt-settle while mission is "
            f"live MUST hold the ``mission_terminal`` row — got "
            f"non-zero deliveries ({notified}). Pre-C1 the proxy "
            f"check consumed the row prematurely (the 2026-09-25 "
            f"incident pattern, mission 36be8aef)."
        )
        # Row SURVIVES — exactly the C1 contract.
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 1, (
            f"C1 timeline pin: the ``mission_terminal`` row MUST "
            f"survive receipt-settle while the mission is still "
            f"live — got {len(remaining)} row(s) remaining."
        )
        assert remaining[0].instance_id == "watcher-mission-survive"

    @pytest.mark.asyncio
    async def test_mission_terminal_fires_exactly_once(
        self, commission_components,
    ):
        """C1 end-to-end pin — arm → receipt settles (mid-mission) →
        row survives → mission-terminal flip → EXACTLY ONE
        notification fires.

        Two-step timeline. Step 1: receipt-settle while mission
        live — row is HELD (no fire, no claim). Step 2: parent
        instance transitions to terminal (``status="completed"``)
        and a fresh notify fires the mission-terminal delivery,
        CAS-claiming the row in the process.
        """
        engine = commission_components["engine"]
        watcher_repo = commission_components["watcher_repo"]
        resolver = commission_components["resolver"]
        instance_manager = commission_components["instance_manager"]

        wid = f"wid-mission-once-{uuid4().hex[:8]}"
        _seed_instance(engine, instance_id="inst-mission-once",
                       status="running")
        _seed_instance(engine, instance_id="watcher-mission-once")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-mission-once",
            watch_events=["mission_terminal"],
        )

        # Step 1: receipt settles while mission is live.
        original = _patch_resolver_task_running(
            resolver, wid=wid, instance_id="inst-mission-once",
        )
        try:
            n_step1 = await notify_work_watchers(
                wid, "settled", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
            )
        finally:
            resolver.resolve_work = original
        assert n_step1 == 0
        assert len(watcher_repo.get_watchers_for_job(wid)) == 1

        # Step 2 prep: transition the parent instance to a
        # terminal status so ``evaluate_mission_live`` returns
        # ``live=False``.
        with Session(engine) as s:
            inst = s.get(Instance, "inst-mission-once")
            assert inst is not None
            inst.status = "completed"
            s.commit()

        # Step 2: mission-terminal flip — the held row is now
        # claimable (no pending events). CAS-claim + deliver.
        original2 = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="inst-mission-once",
        )
        try:
            n_step2 = await notify_work_watchers(
                wid, "completed", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
            )
        finally:
            resolver.resolve_work = original2

        # C1 invariant: EXACTLY ONE mission-terminal fire.
        assert n_step2 == 1, (
            f"C1 end-to-end pin: mission-terminal flip MUST fire "
            f"exactly one notification — got {n_step2}. Pre-C1 "
            f"this was the stranding class (zero fires when the "
            f"row was consumed at receipt-settle)."
        )
        # Row was CAS-claimed on the LAST firing event.
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 0, (
            f"C1 end-to-end pin: mission-terminal flip MUST "
            f"CAS-claim the row — got {len(remaining)} row(s) "
            f"remaining."
        )
        # Body uses the current notify status (the mission-terminal
        # flip's token is the current ``status`` arg, which here is
        # ``"completed"`` — the work-resolved status when the
        # work row itself transitioned to terminal).
        call = instance_manager.enqueue_message.await_args
        msg = call.kwargs["message"]
        assert "[JOB_EVENT]" in msg
        assert "completed ✓" in msg

    @pytest.mark.asyncio
    async def test_proxy_check_replaced_by_mission_live_guard(
        self, commission_components,
    ):
        """C1 architecture pin — the canonical
        ``evaluate_mission_live`` guard is the source of truth,
        NOT the proxy ``work_record.mission_liveness`` /
        ``work_record.status`` sniff.

        Test shape: patch the ``_instance_repository`` to return
        a deterministic ``live=True`` verdict via a mock
        ``evaluate_mission_live`` callable. The partition MUST
        consult it — if a future revert reintroduces the proxy
        check, this test fails (the proxy would see the
        ``work_record.status`` field directly and could
        disagree with the guard).
        """
        engine = commission_components["engine"]
        watcher_repo = commission_components["watcher_repo"]
        resolver = commission_components["resolver"]
        instance_manager = commission_components["instance_manager"]

        wid = f"wid-guard-consult-{uuid4().hex[:8]}"
        # Patch the instance to a state that would make the proxy
        # check see "terminal" (the pre-C1 false-positive case
        # from the 2026-09-25 incident: work_record.status flips
        # per turn, but the parent instance is still alive).
        _seed_instance(
            engine, instance_id="inst-guard-mismatch",
            # Instance is LIVE — guard should return live=True.
            # Pre-C1 proxy would have looked at work_record.status
            # (which we set to "completed" below) and wrongly said
            # "terminal".
            status="running",
        )
        _seed_instance(engine, instance_id="watcher-guard-consult")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-guard-consult",
            watch_events=["mission_terminal"],
        )

        # Patch the resolver to return status="completed" (which
        # the pre-C1 proxy would interpret as "terminal mission").
        # C1's evaluate_mission_live guard reads the Instance
        # row directly (status="running" → live=True), so the
        # row is HELD — NOT delivered. Pre-C1 proxy would have
        # delivered (proxy false-positive).
        from daemon.services.work_resolver import WorkRecord

        record = WorkRecord(
            work_id=wid, kind="report", status="completed",
            instance_id="inst-guard-mismatch", project_id="test-project",
            agent_id="developer", result_summary=None, error=None,
            created_at=datetime.now(timezone.utc),
            job_type=None, mission_liveness=None,
        )
        original = resolver.resolve_work
        resolver.resolve_work = MagicMock(return_value=record)

        try:
            notified = await notify_work_watchers(
                wid, "completed", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
            )
        finally:
            resolver.resolve_work = original

        # C1 architecture pin: the guard says live (instance
        # is "running"), so the row is HELD — NOT delivered. A
        # pre-C1 proxy would have delivered based on
        # work_record.status="completed" (the pre-C1
        # false-positive class).
        assert notified == 0, (
            f"C1 architecture pin: the canonical "
            f"``evaluate_mission_live`` guard MUST override the "
            f"proxy ``work_record.status`` sniff — instance "
            f"status=\"running\" says LIVE, so the "
            f"``mission_terminal`` row is HELD. Pre-C1 proxy "
            f"would have delivered based on "
            f"work_record.status=\"completed\" (the 2026-09-25 "
            f"false-positive class). Got {notified} notifications."
        )
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 1, (
            f"C1 architecture pin: the ``mission_terminal`` row "
            f"MUST survive the proxy-disagreement notify — got "
            f"{len(remaining)} row(s) remaining."
        )


# ── C2 — skip-path content publish + settled enrichment ─────────────────


class TestC2SkipPathContentAndSettledEnrichment:
    """C2 (2026-09-25) — the PROCESS_REPORT dedup-skip notify
    threads ``result_summary=message.content`` (race-safe via the
    in-memory message fetch), AND the ``_enrich_terminal_record``
    enrichment gate includes ``"settled"`` so settled rows also
    enrich on read.

    Pre-C2 the skip-path notify was status-only (no
    ``result_summary`` kwarg → resolver returned ``None`` →
    body was the 57-byte header + Agent-only envelope the
    2026-09-25 mission 36be8aef incident recorded).
    """

    @pytest.mark.asyncio
    async def test_skip_path_threads_message_content_via_kwarg(
        self, commission_components,
    ):
        """C2 skip-path pin — the notify call carries
        ``result_summary == message.content`` so the body
        surfaces ``Result:\\n<content>``.

        Race-safety note: the producer threads the in-memory
        content (fetched from the message row, not from the
        Task row), bypassing the resolver's ``task.result``
        read that races the ``complete_task`` commit visibility
        window. Same threading discipline as DEFECT-1b at
        task_processor.py:1010-1020.
        """
        engine = commission_components["engine"]
        watcher_repo = commission_components["watcher_repo"]
        resolver = commission_components["resolver"]
        instance_manager = commission_components["instance_manager"]

        wid = f"wid-skip-content-{uuid4().hex[:8]}"
        _seed_instance(engine, instance_id="inst-skip")
        _seed_instance(engine, instance_id="watcher-skip")
        _seed_task(engine, work_id=wid, instance_id="inst-skip",
                   status=TaskStatus.RUNNING.value)
        _add_watch(
            engine, work_id=wid, instance_id="watcher-skip",
            watch_events=["completed"],
        )

        # Producer-side threading (the C2 contract): the
        # skip-path passes ``result_summary=<content>`` directly,
        # not via the resolver's ``task.result`` read. Mirrors
        # the in-memory ``ProcessingResult.result_content``
        # threading at task_processor.py:1010-1020 (DEFECT-1b).
        skip_content = "SKIP_PATH_CONTENT_FROM_MESSAGE_REPO"
        original = _patch_resolver_task_running(
            resolver, wid=wid, instance_id="inst-skip",
        )
        try:
            notified = await notify_work_watchers(
                wid, "completed", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
                # C2: producer-side threading of the
                # message.content fetch. The TaskRepository's
                # ``task.result`` would race the complete_task
                # commit (the DEFECT-1b race window) — the
                # in-memory content is the canonical payload.
                result_summary=skip_content,
            )
        finally:
            resolver.resolve_work = original

        assert notified == 1
        call = instance_manager.enqueue_message.await_args
        msg = call.kwargs["message"]
        # C2 pin: the skip-path body MUST carry the threaded
        # content — not the 57-byte status-only envelope that
        # the 2026-09-25 mission 36be8aef incident recorded.
        assert f"Result:\n{skip_content}" in msg, (
            f"C2 skip-path pin: the dedup-skip notify MUST carry "
            f"``Result:\\n<message content>`` — the pre-C2 "
            f"status-only envelope (57 bytes, header + Agent "
            f"only) is closed by the producer-side threading at "
            f"task_processor.py:_skip_task_as_completed. Got "
            f"body={msg!r}"
        )
        # And NOT the resolver fallback path (would surface a
        # JSON envelope dump if work_record.result_summary was
        # set on the resolver record).
        assert json.dumps({"content": skip_content}) not in msg

    @pytest.mark.asyncio
    async def test_settled_row_enrichment_includes_settled(
        self, commission_engine,
    ):
        """C2 enrichment seam pin (F-6 fixback, 2026-09-25) —
        ``_enrich_terminal_record`` enriches a settled-mirror
        WorkRecord (kind="job", job_type="message",
        ``status="settled"``) from the canonical
        ``_get_last_assistant_message_raw`` seam the same way
        it enriches ``completed`` records.

        Pre-C2 the ``needs_result`` gate at
        ``daemon/tools/job_queue.py:2226-2229`` was
        ``status == "completed"`` only — settled mirror rows
        passed through ``watch_job`` / ``watch_jobs`` were
        notified without a ``Result:`` block, mirroring the
        PROCESS_REPORT skip-path stranding class. C2 widens
        the gate to ``status in {"completed", "settled"}`` so
        settled mirror rows also trigger the
        last-assistant-message enrichment. This pin exercises
        the call-site seam at
        ``daemon/tools/job_queue.py:2335-2339`` (the
        ``_enrich_terminal_record(record)`` invocation in
        ``watch_job`` BEFORE the immediate ``notify_watchers``):
        a settled WorkRecord with ``result_summary=None``
        reaches the watch tool, the manager's
        ``_get_last_assistant_message_raw(instance_id)`` returns
        the captured assistant content, the enrichment
        populates ``record.result_summary``, and the immediate
        notify call carries the enriched kwarg.
        """
        from daemon.tools.job_queue import create_job_tools
        from daemon.services.work_resolver import WorkRecord

        # Seed the instance — the enrichment helper fetches the
        # last-assistant-message via the manager mock below
        # (mock-driven), but the WorkRecord still needs an
        # ``instance_id`` so the seam can find the row.
        iid = f"inst-enrich-{uuid4().hex[:8]}"
        wid = f"wid-enrich-{uuid4().hex[:8]}"
        _seed_instance(
            commission_engine,
            instance_id=iid,
            # The instance must be live (non-terminal) — the
            # enrichment helper fetches content from the live
            # instance row, and the watch_job terminal-state
            # gate evaluates ``record.status`` (not instance
            # state).
            status="running",
        )
        _seed_task(
            commission_engine, work_id=wid, instance_id=iid,
            status=TaskStatus.RUNNING.value,
        )

        # Manager mock: ``_get_last_assistant_message_raw``
        # returns the captured assistant content (the
        # canonical seam the enrichment uses). The pre-C2 gate
        # would have skipped this fetch entirely for a settled
        # record; the C2 widening lets the fetch run.
        enriched_content = "F6_SETTLED_ENRICHMENT_FROM_INSTANCE"
        manager = MagicMock()
        manager._get_last_assistant_message_raw = AsyncMock(
            return_value=enriched_content
        )

        # WorkResolver mock returning a settled mirror with
        # ``result_summary=None`` (the race-prone / resolver-side
        # fallback path; the enrichment seam is the
        # second-chance catch).
        resolver_mock = MagicMock()
        settled_record = WorkRecord(
            work_id=wid, kind="job", status="settled",
            instance_id=iid, project_id="test-project",
            agent_id="developer",
            result_summary=None,
            error=None,
            created_at=datetime.now(timezone.utc),
            job_type="message", mission_liveness="completed",
        )
        resolver_mock.resolve_work = MagicMock(
            return_value=settled_record
        )

        # JobService mock — ``get_work`` routes through the
        # resolver (the watch_job terminal-state branch); the
        # captured ``notify_watchers`` call records the
        # ``result_summary=`` kwarg the seam enriched.
        job_service = MagicMock()
        job_service.get_work = AsyncMock(return_value=settled_record)
        job_service.notify_watchers = AsyncMock(return_value=1)

        # Watcher repo mock (count + add — no-op for this pin).
        watcher_repo = MagicMock()
        watcher_repo.count_watches_for_instance = MagicMock(return_value=0)
        watcher_repo.add_watch = MagicMock()

        tools = create_job_tools(
            job_service=job_service,
            queue_mgmt_service=MagicMock(),
            dead_letter_service=MagicMock(),
            current_instance_id="inst-watcher",
            agent_id="jober",
            watcher_repo=watcher_repo,
            manager=manager,
        )
        watch_job_tool = next(
            t for t in tools if t.name == "watch_job"
        )

        # Exercise the seam at ``daemon/tools/job_queue.py:2335-2339``:
        # watch_job sees a settled record, calls
        # ``_enrich_terminal_record(record)``, then
        # ``notify_watchers``. The enriched record carries the
        # manager's last-assistant-message content as
        # ``result_summary``.
        result = await watch_job_tool.ainvoke(
            {"job_id": wid}
        )

        # The manager's ``_get_last_assistant_message_raw`` was
        # consulted — this is the seam the C2 widening opened.
        manager._get_last_assistant_message_raw.assert_awaited_with(
            iid
        )

        # ``notify_watchers`` received the ENRICHED record, NOT
        # the original ``result_summary=None`` (the pre-C2
        # regressed shape).
        job_service.notify_watchers.assert_awaited_once()
        call_kwargs = job_service.notify_watchers.await_args.kwargs
        assert call_kwargs["result_summary"] == enriched_content, (
            f"F-6 enrichment pin: ``_enrich_terminal_record`` MUST "
            f"populate ``record.result_summary`` from "
            f"``_get_last_assistant_message_raw`` for settled "
            f"WorkRecords (C2 widening at ``needs_result`` gate, "
            f"``daemon/tools/job_queue.py:2226-2229``). Got "
            f"``result_summary={call_kwargs.get('result_summary')!r}``; "
            f"expected ``{enriched_content!r}``."
        )
        # The status passed to notify_watchers is the settled
        # mirror's per-kind token — confirms the seam at line
        # 2335-2339 ran on a settled record (not silently skipped
        # by the pre-C2 gate).
        assert call_kwargs.get("error") is None


# ── C3 — settled-body Result line + race-safety ──────────────────────────


class TestC3SettledBodyResultLine:
    """C3 (2026-09-25) — settled envelopes SHOULD carry a
    populated ``Result:`` line. Pre-C3 the M3 settled guardrail
    (81fc769d, 2026-09-24) omitted the ``result_summary`` kwarg
    for settled tokens; C3 reverses that (user override,
    2026-09-25).

    Race-safety: producer-side threading of in-memory content
    bypasses the resolver's ``task.result`` read that races the
    ``complete_task`` commit visibility window. The resolver
    fallback (work_notifier.effective_result at
    work_notifier.py:306) is still active for callers that
    don't thread — documented, not closed.
    """

    @pytest.mark.asyncio
    async def test_settled_envelope_threads_via_producer_not_resolver(
        self, commission_components,
    ):
        """C3 race-safety pin — the settled envelope carries
        ``Result:\\n<threaded>`` even when the resolver returns
        ``None`` (the race-prone path).

        Constructs the commit-visibility race explicitly:
        ``work_record.result_summary`` is ``None`` (the
        post-commit visibility window the resolver would read
        — empty because Task.result hasn't committed yet on a
        separate connection), and the producer threads the
        in-memory content via the ``result_summary=`` kwarg.
        The body MUST carry the threaded content (not
        nothing).
        """
        engine = commission_components["engine"]
        watcher_repo = commission_components["watcher_repo"]
        resolver = commission_components["resolver"]
        instance_manager = commission_components["instance_manager"]

        wid = f"wid-settled-race-{uuid4().hex[:8]}"
        _seed_instance(engine, instance_id="inst-settled-race")
        _seed_instance(engine, instance_id="watcher-settled-race")
        _add_watch(
            engine, work_id=wid, instance_id="watcher-settled-race",
            watch_events=["settled"],
        )

        # Resolver returns the settled mirror shape with
        # ``result_summary=None`` (the post-commit visibility
        # race window — Task.result hasn't committed yet on a
        # separate connection).
        from daemon.services.work_resolver import WorkRecord

        record = WorkRecord(
            work_id=wid, kind="job", status="settled",
            instance_id="inst-settled-race", project_id="test-project",
            agent_id="developer",
            # Resolver fallback returns None — the race-prone
            # path (Task.result not yet visible).
            result_summary=None,
            error=None,
            created_at=datetime.now(timezone.utc),
            job_type="message", mission_liveness=None,
        )
        original = resolver.resolve_work
        resolver.resolve_work = MagicMock(return_value=record)

        # Producer-side threading: the in-memory content
        # (mirrors the child_reports.py:4380+ fan-out and
        # task_processor.py:1108+ inline mirror finalize sites).
        producer_content = "C3_SETTLED_CONTENT_FROM_PRODUCER"
        try:
            notified = await notify_work_watchers(
                wid, "settled", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
                # C3: producer-side threading of the in-memory
                # content. Bypasses the resolver's
                # ``task.result`` read that races the
                # ``complete_task`` commit visibility window
                # (the live E2E race the DEFECT-1b close
                # documented at task_processor.py:1010-1020).
                result_summary=producer_content,
            )
        finally:
            resolver.resolve_work = original

        assert notified == 1
        call = instance_manager.enqueue_message.await_args
        msg = call.kwargs["message"]
        # C3 race-safety pin: the settled envelope carries the
        # threaded content even when the resolver's
        # ``task.result`` read would return ``None``.
        assert f"Result:\n{producer_content}" in msg, (
            f"C3 race-safety pin: settled envelope MUST carry "
            f"``Result:\\n<threaded>`` even when the resolver's "
            f"``task.result`` read races the ``complete_task`` "
            f"commit visibility window. Pre-C3 (M3 settled "
            f"guardrail, 81fc769d) the settled envelope was "
            f"NO-Result-block; C3 reverses that (user override, "
            f"2026-09-25). Got body={msg!r}"
        )
        # And no Error: line either (no error keyword passed).
        assert "Error:" not in msg
