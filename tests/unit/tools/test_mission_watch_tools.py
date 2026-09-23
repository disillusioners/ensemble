"""Tool-layer tests for the mission-watch toolset reshape.

Toolset reshape (2026-09-19, ``feature/mission-watch-toolset``; design:
``.agents/shared/planning/watch-notification-reliability/toolset-reshape-design.md``
§9 test plan). Pins the ``watch_mission`` tool contract:

* **Resolution paths** (§9.1) — mission_id OR job reference (the
  ``job_create`` receipt) resolve to the same registration; unresolvable
  targets are an explicit error with NO row minted.
* **Pre-registration** (§9.2) — a pre-dispatch receipt (``instance_id=None``)
  registers a row on the pre-generated UUID (design §2b: the receipt IS the
  future mission's first task receipt; a mission-keyed row would strand —
  ``watcher_repository.get_watchers_for_job`` is strictly receipt-keyed).
* **Already-terminal** (§9.3) — an already-terminal mission replays
  NOTHING (F1 registration-time terminal filter): settled receipts are
  skipped (no row, no immediate notification) and the tool reply itself
  carries the ``terminal_reason``; a dead_letter-since-revived mission
  (W4 shape) arms its live receipts and waits.
* **Multi-receipt fan-in** (§9.4) — N receipts → N rows with
  ``events=["mission_terminal"]``; each row CAS-claims exactly once via the
  REAL ``JobWatcherRepository`` claim primitive; ``add_watch`` UPSERT keeps
  a re-watch at one row per receipt.
* **Post-mint gap** (§9.5) — a receipt minted after the watch is NOT
  auto-watched; a re-call covers it (documented limitation, prompt-mitigated).
* **Epoch** (§9.6) — claimed rows are gone (revived mission NOT auto-watched);
  a fresh call re-registers.
* **Removal pins** (§9.7) — ari/jober resolved toolsets exclude
  ``watch_job``/``watch_jobs`` AND include ``watch_mission``; the
  ``create_job_tools`` factory return list is UNTOUCHED (indices 17/20);
  the ``job`` category still carries both watch tools for other agents.
* **Cap** (§9.8) — the 50-watch cap counts EVERY minted row.

Harness: real ``MissionResolver`` + real ``JobWatcherRepository`` +
real ``TaskRepository`` against a file-backed SQLite engine;
``job_service`` is an AsyncMock double (same pattern as
``tests/test_job_queue_tools.py``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.mission_resolver import MissionResolver
from daemon.tools.instance import resolve_tool_filter
from daemon.tools.job_queue import create_job_tools, create_mission_watch_tools
from daemon.tools.missions import create_mission_tools
from daemon.tools._tool_registry import list_tools_by_category, scan_tools_for_full_docs

CALLER = "watcher-inst-0000-0000-000000000001"


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    Same conventions recipe as ``tests/unit/tools/test_mission_tools.py``.
    """
    db_path = tmp_path / "mission-watch-tools-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def resolver(instance_repo, job_repo) -> MissionResolver:
    return MissionResolver(instance_repo=instance_repo, job_repo=job_repo)


@pytest.fixture
def watcher_repo(engine: Engine) -> JobWatcherRepository:
    return JobWatcherRepository(engine)


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def job_service() -> AsyncMock:
    """JobQueueService double: resolver-aware ``get_work`` + counting
    ``notify_watchers``. Tests configure ``get_work.side_effect`` /
    ``return_value`` per scenario."""
    svc = AsyncMock(name="JobQueueService")
    svc.get_work = AsyncMock(return_value=None)
    svc.notify_watchers = AsyncMock(return_value=1)
    return svc


@pytest.fixture
def watch_mission(job_service, resolver, task_repo, watcher_repo):
    tools = create_mission_watch_tools(
        job_service=job_service,
        mission_resolver=resolver,
        task_repo=task_repo,
        watcher_repo=watcher_repo,
        current_instance_id=CALLER,
    )
    assert [t.name for t in tools] == ["watch_mission"]
    return tools[0]


@pytest.fixture(autouse=True)
def _seed_caller_instance(engine):
    """``job_watchers.instance_id`` carries a real FK to
    ``instances.instance_id`` and the harness enables FK enforcement —
    seed the watching (caller) instance row up front (in production the
    caller always exists)."""
    _seed_instance(engine, instance_id=CALLER, agent_id="jober")


# ─── Seed helpers ─────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    iid = instance_id or f"inst-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id=agent_id,
                agent_dir=f"agents/{agent_id}",
                project_id="test-project",
                status=status,
                last_activity_at=now,
                created_at=iso_now,
                updated_at=iso_now,
            )
        )
        s.commit()
    return iid


def _seed_task(engine: Engine, *, work_id: str, instance_id: str, status: str = TaskStatus.COMPLETED.value) -> str:
    with Session(engine) as s:
        s.add(Task(work_id=work_id, instance_id=instance_id, status=status))
        s.commit()
    return work_id


def _seed_job_item(
    engine: Engine,
    *,
    instance_id: str,
    job_id: str | None = None,
    admission_state: str = AdmissionState.DONE.value,
    job_type: str = "task",
    terminal_reason: str | None = "completed",
) -> str:
    """Insert a JobItem row (model: ``tests/unit/tools/test_mission_tools.py``)."""
    jid = job_id or str(uuid.uuid4())
    with Session(engine) as s:
        s.add(JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="agents/developer",
            message="seeded",
            source="agent:test",
            instance_id=instance_id,
            admission_state=admission_state,
            job_type=job_type,
            terminal_reason=terminal_reason,
        ))
        s.commit()
    return jid


def _work_record(work_id, kind, status, *, instance_id=None, job_type=None,
                 mission_liveness=None, error=None, result_summary=None):
    """WorkRecord-shaped mock (mirrors ``tests/test_job_queue_tools.py``)."""
    record = MagicMock(name=f"WorkRecord[{work_id[:8]}]")
    record.work_id = work_id
    record.kind = kind
    record.status = status
    record.instance_id = instance_id
    record.job_type = job_type
    record.mission_liveness = mission_liveness
    record.error = error
    record.result_summary = result_summary
    return record


def _watched_job_ids(watcher_repo: JobWatcherRepository) -> set[str]:
    return {w.job_id for w in watcher_repo.get_watches_for_instance(CALLER)}


# ─── §9.1 Resolution paths ────────────────────────────────────────────────


class TestResolutionPaths:
    def test_mission_id_resolves_to_one_row_per_receipt(
        self, engine, watch_mission, watcher_repo
    ):
        """mission_id (live mission, 2 receipts) → 2 rows, both
        ``events=["mission_terminal"]``."""
        mid = _seed_instance(engine)
        receipt_a = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        receipt_b = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert "Mission watch registered" in result
        assert "armed 2 live receipt(s)" in result
        watched = _watched_job_ids(watcher_repo)
        assert watched == {receipt_a, receipt_b}
        rows = watcher_repo.get_watches_for_instance(CALLER)
        for row in rows:
            assert row.watch_events == ["mission_terminal"]

    def test_job_ref_resolves_to_same_registration(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """job reference (a task receipt) resolves via ``get_work`` → its
        instance → the same receipt set as the mission_id form."""
        mid = _seed_instance(engine)
        receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        async def _get_work(work_id):
            return _work_record(work_id, "report", "processing", instance_id=mid)

        job_service.get_work = AsyncMock(side_effect=_get_work)

        result = asyncio.run(watch_mission.ainvoke({"target": receipt}))

        assert "Mission watch registered" in result
        assert _watched_job_ids(watcher_repo) == {receipt}

    def test_unresolvable_target_is_explicit_error_and_mints_nothing(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """An unknown id resolves NEITHER as a mission NOR as work →
        explicit error, zero rows (a synthetic key would strand silently —
        design §2b)."""
        job_service.get_work = AsyncMock(return_value=None)

        result = asyncio.run(watch_mission.ainvoke({"target": str(uuid.uuid4())}))

        assert "Error" in result
        assert "Could not resolve" in result
        assert _watched_job_ids(watcher_repo) == set()


# ─── §9.2 Pre-registration (mission not yet born) ─────────────────────────


class TestPreRegistration:
    def test_pre_dispatch_receipt_registers_on_pre_generated_uuid(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """``job_create`` → ``watch_mission(returned job_id)`` BEFORE
        dispatch: the receipt resolves with ``instance_id=None``; the row
        keys on the pre-generated UUID (its Task lands at dispatch with
        ``work_id = job_id`` — job_processor.py:1024)."""
        receipt = str(uuid.uuid4())  # pre-generated UUID4, as job_create does

        async def _get_work(work_id):
            # Pre-dispatch: JobItem exists (queued), instance NOT minted.
            return _work_record(work_id, "job", "pending", instance_id=None)

        job_service.get_work = AsyncMock(side_effect=_get_work)

        result = asyncio.run(watch_mission.ainvoke({"target": receipt}))

        assert "Watch registered on receipt" in result
        assert "mission not yet dispatched" in result
        assert _watched_job_ids(watcher_repo) == {receipt}
        row = watcher_repo.get_watches_for_instance(CALLER)[0]
        assert row.watch_events == ["mission_terminal"]


# ─── §9.3 Already-terminal missions ───────────────────────────────────────


class TestAlreadyTerminal:
    def test_completed_mission_arms_nothing_skips_settled_no_replay(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """F1 (fix/job-event-watch-replay): a genuinely-terminal mission
        with an already-settled receipt arms NOTHING and replays NOTHING.

        Incident mechanics (RC1, live DB 2026-09-23): the old
        register-then-notify path minted a row for a receipt that had
        settled hours earlier AND fired it immediately — every later
        mission-instance flip re-fired the whole historical set
        (bursts of 9/14 duplicate [JOB_EVENT] rows). The registration-
        time terminal filter skips already-settled receipts entirely:
        no row minted, no notify enqueued."""
        mid = _seed_instance(engine, status=InstanceStatus.COMPLETED.value)
        receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        async def _get_work(work_id):
            return _work_record(work_id, "report", "completed", instance_id=mid,
                                result_summary="done")

        job_service.get_work = AsyncMock(side_effect=_get_work)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert "already terminal (completed)" in result
        assert "1 already-settled receipt(s) skipped" in result
        # NO row minted for the historical receipt, NO notify enqueued.
        assert _watched_job_ids(watcher_repo) == set()
        job_service.notify_watchers.assert_not_awaited()

    def test_failed_mission_terminal_reason_surfaced_no_replay(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """F1: terminal_reason is surfaced AND the failed receipt is
        skipped silently (no row, no immediate notify)."""
        mid = _seed_instance(engine, status=InstanceStatus.ERROR.value)
        receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        async def _get_work(work_id):
            return _work_record(work_id, "report", "failed", instance_id=mid,
                                error="boom")

        job_service.get_work = AsyncMock(side_effect=_get_work)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert "already terminal (failed)" in result
        assert "1 already-settled receipt(s) skipped" in result
        assert _watched_job_ids(watcher_repo) == set()
        job_service.notify_watchers.assert_not_awaited()

    def test_dead_letter_since_revived_registers_normally_not_short_circuit(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """M2 (review council 2026-09-19): a mission whose JobItem carries
        ``terminal_reason="dead_letter"`` but whose liveness has since
        returned to RUNNING must NOT short-circuit to "already terminal" —
        the watch REGISTERS normally (the misleading reply came from the
        resolver's W4 encoding keeping dead_letter on since-revived rows;
        the tool cross-checks liveness)."""
        mid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        _seed_job_item(
            engine, instance_id=mid,
            admission_state=AdmissionState.DEAD.value, terminal_reason=None,
        )

        async def _get_work(work_id):
            # The live receipt resolves non-terminal (instance revived).
            return _work_record(work_id, "report", "processing", instance_id=mid)

        job_service.get_work = AsyncMock(side_effect=_get_work)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        # REGISTERS — no "already terminal" short-circuit, no notify.
        assert "Mission watch registered" in result
        assert "already terminal" not in result
        assert _watched_job_ids(watcher_repo) == {receipt}
        job_service.notify_watchers.assert_not_awaited()

    def test_dead_letter_with_terminal_liveness_still_short_circuits(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """Complementary M2 pin: dead_letter where the instance liveness is
        ALSO terminal (ERROR → failed) is a genuinely-terminal mission —
        the short-circuit applies and the resolver's ``dead_letter``
        terminal_reason is surfaced. F1: the settled receipt is SKIPPED
        (no row, no immediate replay notification)."""
        mid = _seed_instance(engine, status=InstanceStatus.ERROR.value)
        receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        _seed_job_item(
            engine, instance_id=mid,
            admission_state=AdmissionState.DEAD.value, terminal_reason=None,
        )

        async def _get_work(work_id):
            return _work_record(work_id, "report", "failed", instance_id=mid,
                                error="boom")

        job_service.get_work = AsyncMock(side_effect=_get_work)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert "already terminal (dead_letter)" in result
        assert "1 already-settled receipt(s) skipped" in result
        assert _watched_job_ids(watcher_repo) == set()
        job_service.notify_watchers.assert_not_awaited()


# ─── F1: registration-time terminal filter (incident RC1) ─────────────────


class TestRegistrationTerminalFilter:
    """Regression pins for the 2026-09-23 replay-flood incident.

    RC1 mechanics (live ensemble_prod evidence): ``watch_mission``
    minted one held row per Task receipt with NO terminal-state
    filter; the mission-finalize fan-out notified every receipt
    work_id; the hold gate re-read mission liveness FRESH at emit
    time, so every mission-instance terminal flip (chat missions flip
    ``completed`` per turn) released ALL held rows — including rows
    for receipts that had settled hours earlier. Re-calling
    ``watch_mission`` (the documented workflow) UPSERT-recreated the
    rows → the next flip re-fired them → duplicate bursts (9 then 14
    ``[JOB_EVENT]`` rows per flip; burst intersection = exactly the
    user-reported duplicates).

    F1 invariant: ``watch_mission`` arms ONLY currently-live receipts;
    already-settled receipts are skipped (no row, no notify) at
    registration time AND in the already-terminal-mission branch.
    """

    def test_mixed_terminal_and_live_receipts_arms_only_live(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """(a) 3 receipts — two settled, one live → exactly ONE row
        minted (the live one); the settled pair is reported skipped."""
        mid = _seed_instance(engine)
        settled_a = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        settled_b = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        live_c = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        statuses = {settled_a: "completed", settled_b: "settled", live_c: "processing"}

        async def _get_work(work_id):
            return _work_record(work_id, "report", statuses[work_id],
                                instance_id=mid)

        job_service.get_work = AsyncMock(side_effect=_get_work)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert "Mission watch registered" in result
        assert "armed 1 live receipt(s)" in result
        assert "2 already-settled receipt(s) skipped" in result
        assert _watched_job_ids(watcher_repo) == {live_c}
        job_service.notify_watchers.assert_not_awaited()

    def test_terminal_flip_fires_exactly_armed_set_once_each(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """(b) After arming the live set, a mission-instance terminal
        flip delivers exactly the ARMED rows — each exactly once — and
        the skipped settled receipt has NO row to fire (that is the
        duplicate-burst killer: the historical receipts were re-fired
        per flip because their rows kept being recreated)."""
        mid = _seed_instance(engine)
        settled = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        live_a = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        live_b = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        statuses = {settled: "completed", live_a: "processing", live_b: "processing"}

        async def _get_work(work_id):
            return _work_record(work_id, "report", statuses[work_id],
                                instance_id=mid)

        job_service.get_work = AsyncMock(side_effect=_get_work)
        result = asyncio.run(watch_mission.ainvoke({"target": mid}))
        assert _watched_job_ids(watcher_repo) == {live_a, live_b}

        # Mission-instance terminal flip: the engine CAS-claims each
        # armed row (claim → notify → row gone). First claim wins once;
        # the second is empty (exactly-once).
        for receipt in (live_a, live_b):
            first = watcher_repo.claim_watchers_for_job_for_instances(
                receipt, [CALLER]
            )
            second = watcher_repo.claim_watchers_for_job_for_instances(
                receipt, [CALLER]
            )
            assert len(first) == 1
            assert second == []

        # The skipped settled receipt: NO row ever existed to fire —
        # under the old code this claim would have returned a row on
        # the first call (it was registered despite being settled).
        assert watcher_repo.claim_watchers_for_job_for_instances(
            settled, [CALLER]
        ) == []
        assert "1 already-settled receipt(s) skipped" in result

    def test_rewatch_after_flip_mints_no_rows_and_no_second_burst(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """(c) The duplicates case: arm → flip (claim) → re-call
        watch_mission (the documented workflow). The formerly-live
        receipt is now settled → the re-call is a no-op delta-arm: no
        row recreated, nothing to fire on the NEXT flip."""
        mid = _seed_instance(engine)
        receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        statuses = {receipt: "processing"}

        async def _get_work(work_id):
            return _work_record(work_id, "report", statuses[work_id],
                                instance_id=mid)

        job_service.get_work = AsyncMock(side_effect=_get_work)

        # Turn 1: arm the live receipt.
        asyncio.run(watch_mission.ainvoke({"target": mid}))
        assert _watched_job_ids(watcher_repo) == {receipt}

        # The turn ends: receipt settles, the flip claims + fires its row.
        statuses[receipt] = "completed"
        claimed = watcher_repo.claim_watchers_for_job_for_instances(
            receipt, [CALLER]
        )
        assert len(claimed) == 1

        # Turn 2 begins: the documented re-call. Under the old code the
        # UPSERT re-created the row → the NEXT flip re-fired the
        # already-delivered receipt (the duplicate burst).
        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert "armed 0 live receipt(s)" in result
        assert "1 already-settled receipt(s) skipped" in result
        assert _watched_job_ids(watcher_repo) == set()
        job_service.notify_watchers.assert_not_awaited()
        # Nothing can fire on a subsequent flip.
        assert watcher_repo.claim_watchers_for_job_for_instances(
            receipt, [CALLER]
        ) == []

    def test_already_terminal_mission_no_notifications_for_historical_receipts(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """(d) Mission already terminal at call time → the historical
        receipts are neither registered nor enqueued — the second
        immediate-replay vector (job_queue.py register-then-notify §4
        branch) is closed."""
        mid = _seed_instance(engine, status=InstanceStatus.COMPLETED.value)
        settled_a = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        settled_b = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        async def _get_work(work_id):
            return _work_record(work_id, "report", "completed", instance_id=mid,
                                result_summary="done")

        job_service.get_work = AsyncMock(side_effect=_get_work)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert "already terminal (completed)" in result
        assert "Armed 0 live receipt(s)" in result
        assert "2 already-settled receipt(s) skipped" in result
        assert "no historical replay" in result
        assert _watched_job_ids(watcher_repo) == set()
        job_service.notify_watchers.assert_not_awaited()

    def test_dead_letter_since_revived_still_arms_live_receipt(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """F1 composes with the v0.13.12 mission-live guard (9e596604):
        a dead_letter-since-revived mission (stale terminal_reason,
        live liveness) does NOT short-circuit and its LIVE receipt is
        armed normally — the W4 hazard encoding is untouched."""
        mid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        _seed_job_item(
            engine, instance_id=mid,
            admission_state=AdmissionState.DEAD.value, terminal_reason=None,
        )

        async def _get_work(work_id):
            return _work_record(work_id, "report", "processing", instance_id=mid)

        job_service.get_work = AsyncMock(side_effect=_get_work)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert "Mission watch registered" in result
        assert "armed 1 live receipt(s)" in result
        assert "already terminal" not in result
        assert _watched_job_ids(watcher_repo) == {receipt}
        job_service.notify_watchers.assert_not_awaited()


# ─── §9.4 Multi-receipt fan-in + exactly-once claim ───────────────────────


class TestMultiReceiptFanIn:
    def test_both_rows_claim_exactly_once(
        self, engine, watch_mission, watcher_repo
    ):
        """Task + mirror receipts registered → each row CAS-claims
        exactly once via the REAL repo claim primitive (the engine's N1
        exactly-once primitive over tool-minted rows)."""
        mid = _seed_instance(engine)
        task_receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        mirror_receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        asyncio.run(watch_mission.ainvoke({"target": mid}))

        # Two rows, both mission_terminal.
        rows = watcher_repo.get_watches_for_instance(CALLER)
        assert {r.job_id for r in rows} == {task_receipt, mirror_receipt}
        assert all(r.watch_events == ["mission_terminal"] for r in rows)

        # CAS claim: exactly once per receipt (second claim → empty).
        first_a = watcher_repo.claim_watchers_for_job_for_instances(
            task_receipt, [CALLER]
        )
        second_a = watcher_repo.claim_watchers_for_job_for_instances(
            task_receipt, [CALLER]
        )
        assert len(first_a) == 1
        assert second_a == []

        first_b = watcher_repo.claim_watchers_for_job_for_instances(
            mirror_receipt, [CALLER]
        )
        assert len(first_b) == 1
        assert _watched_job_ids(watcher_repo) == set()

    def test_re_watch_upserts_never_duplicates(
        self, engine, watch_mission, watcher_repo
    ):
        """``add_watch`` is an UPSERT on (job_id, instance_id) — calling
        ``watch_mission`` twice keeps ONE row per receipt."""
        mid = _seed_instance(engine)
        _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        asyncio.run(watch_mission.ainvoke({"target": mid}))
        asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert len(watcher_repo.get_watches_for_instance(CALLER)) == 1


# ─── §9.5 Post-mint gap (documented limitation) ───────────────────────────


class TestPostMintGap:
    def test_receipt_minted_after_watch_is_unwatched_until_recall(
        self, engine, watch_mission, watcher_repo
    ):
        mid = _seed_instance(engine)
        receipt_a = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        asyncio.run(watch_mission.ainvoke({"target": mid}))

        # job_continue mints a NEW receipt after the watch returned.
        receipt_b = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        # Documented limitation: the new receipt is NOT auto-watched.
        assert _watched_job_ids(watcher_repo) == {receipt_a}

        # Mitigation (prompt rule): re-call covers it. F1: the re-call
        # is a delta-arm — the new receipt is live, so it gets armed.
        result = asyncio.run(watch_mission.ainvoke({"target": mid}))
        assert "Mission watch registered" in result
        assert "armed 2 live receipt(s)" in result
        assert _watched_job_ids(watcher_repo) == {receipt_a, receipt_b}


# ─── §9.6 Epoch semantics (claim-delete + fresh re-watch) ─────────────────


class TestEpochSemantics:
    def test_claimed_row_is_gone_and_fresh_call_re_registers(
        self, engine, watch_mission, watcher_repo
    ):
        mid = _seed_instance(engine)
        receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        asyncio.run(watch_mission.ainvoke({"target": mid}))
        # Mission-terminal: the engine CAS-claims (deletes) the row.
        watcher_repo.claim_watchers_for_job_for_instances(receipt, [CALLER])
        assert _watched_job_ids(watcher_repo) == set()

        # Revived mission: NOT auto-watched — a FRESH call re-registers.
        result = asyncio.run(watch_mission.ainvoke({"target": mid}))
        assert "Mission watch registered" in result
        assert _watched_job_ids(watcher_repo) == {receipt}


# ─── Hard constraint: never a mission-keyed row ───────────────────────────


class TestNoMissionKeyedRows:
    def test_zero_receipt_mission_mints_nothing(self, engine, watch_mission, watcher_repo):
        """A mission handle with ZERO receipts has nothing valid to key on
        (mission-keyed rows never resolve) — explicit error, no row keyed
        on the mission_id."""
        mid = _seed_instance(engine)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert "Error" in result
        assert "no receipts" in result
        rows = watcher_repo.get_watches_for_instance(CALLER)
        assert rows == []
        assert not any(r.job_id == mid for r in rows)


# ─── §9.8 Cap counts EVERY minted row ─────────────────────────────────────


class TestCap:
    def test_cap_counts_n_rows(self, engine, watch_mission, watcher_repo):
        mid = _seed_instance(engine)
        _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        # Caller already watches 49 receipts.
        for _ in range(49):
            watcher_repo.add_watch(str(uuid.uuid4()), CALLER)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert "Would exceed maximum watch limit (50)" in result
        assert "mission has 2 receipt watch(es)" in result
        # Nothing was minted — all-or-nothing.
        assert len(watcher_repo.get_watches_for_instance(CALLER)) == 49

    def test_cap_counts_only_live_rows_settled_receipts_excluded(
        self, engine, job_service, watch_mission, watcher_repo
    ):
        """F1 terminal filter meets the cap: settled receipts arm NO
        row and count NOTHING against the cap — a mission whose
        receipts are ALL already-settled reports ``0 receipt
        watch(es)`` in the armed wording even with the caller over
        the cap (a broken filter would arm the settled receipts,
        report 2, and overflow)."""
        mid = _seed_instance(engine)
        _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        async def _get_work(work_id):
            return _work_record(work_id, "report", "completed", instance_id=mid)

        job_service.get_work = AsyncMock(side_effect=_get_work)

        # Caller already watches 51 receipts: only a non-zero LIVE
        # count can trip the would-exceed cap shape — a fully-settled
        # mission must stay at "0 receipt watch(es)".
        for _ in range(51):
            watcher_repo.add_watch(str(uuid.uuid4()), CALLER)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))

        assert "Would exceed maximum watch limit (50)" in result
        assert "mission has 0 receipt watch(es)" in result
        # Nothing was minted — settled receipts arm no rows.
        assert len(watcher_repo.get_watches_for_instance(CALLER)) == 51

    def test_cap_boundary_is_allowed(self, engine, watch_mission, watcher_repo):
        mid = _seed_instance(engine)
        _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        for _ in range(48):
            watcher_repo.add_watch(str(uuid.uuid4()), CALLER)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))
        assert "Mission watch registered" in result
        assert len(watcher_repo.get_watches_for_instance(CALLER)) == 50

    def test_pre_dispatch_branch_single_row_cap(self, engine, job_service, watch_mission, watcher_repo):
        receipt = str(uuid.uuid4())

        async def _get_work(work_id):
            return _work_record(work_id, "job", "pending", instance_id=None)

        job_service.get_work = AsyncMock(side_effect=_get_work)
        for _ in range(50):
            watcher_repo.add_watch(str(uuid.uuid4()), CALLER)

        result = asyncio.run(watch_mission.ainvoke({"target": receipt}))

        assert "Maximum watch limit (50) reached" in result
        assert not any(r.job_id == receipt for r in watcher_repo.get_watches_for_instance(CALLER))


# ─── §9.7 Removal pins (resolved toolsets + factory untouched) ────────────


REPO_ROOT = Path(__file__).resolve().parents[3]


def _build_registry_tools():
    """Build the full job+mission+mission-watch tool list exactly as
    ``create_instance_tools`` assembles it, then scan it so
    ``list_tools_by_category()`` carries the categories."""
    tools = []
    tools.extend(create_job_tools(
        job_service=MagicMock(name="job_service"),
        queue_mgmt_service=MagicMock(name="queue_mgmt"),
        dead_letter_service=MagicMock(name="dlq"),
    ))
    tools.extend(create_mission_tools(MagicMock(name="resolver")))
    tools.extend(create_mission_watch_tools(
        job_service=MagicMock(name="job_service"),
        mission_resolver=MagicMock(name="resolver"),
    ))
    scan_tools_for_full_docs(tools)
    return tools


class TestRemovalPins:
    @pytest.mark.parametrize("agent", ["ari", "jober"])
    def test_resolved_toolset_excludes_watch_and_includes_watch_mission(self, agent):
        meta = json.loads((REPO_ROOT / "agents" / agent / "meta.json").read_text())
        _build_registry_tools()  # populates the registry metadata (global scan)
        categories = list_tools_by_category()

        resolved = resolve_tool_filter(
            allow=meta["tools"]["allow"],
            deny=meta["tools"].get("deny"),
            tool_categories=categories,
            all_tool_names=set(),
        )
        assert "watch_job" not in resolved, f"{agent} still resolves watch_job"
        assert "watch_jobs" not in resolved, f"{agent} still resolves watch_jobs"
        assert "watch_mission" in resolved, f"{agent} missing watch_mission"
        # The rest of the job/mission surface is untouched.
        assert "job_create" in resolved
        assert "get_mission" in resolved

    def test_factory_return_list_untouched_index_pins(self):
        """``create_job_tools`` indices are UNTOUCHED — watch_job=17,
        watch_jobs=20 (the pins at tests/test_job_queue_tools.py:2098/
        2155/2203/2241/2283/2333 depend on it)."""
        tools = create_job_tools(
            job_service=MagicMock(name="job_service"),
            queue_mgmt_service=MagicMock(name="queue_mgmt"),
            dead_letter_service=MagicMock(name="dlq"),
        )
        names = [t.name for t in tools]
        assert names[17] == "watch_job"
        assert names[18] == "unwatch_job"
        assert names[19] == "list_watched_jobs"
        assert names[20] == "watch_jobs"

    def test_job_category_still_carries_watch_tools(self):
        """Deny is ari/jober-LOCAL: the ``job`` category (any other agent
        granting it) still lists watch_job/watch_jobs."""
        _build_registry_tools()
        job_category = list_tools_by_category()["job"]
        assert "watch_job" in job_category
        assert "watch_jobs" in job_category

    def test_watch_mission_joins_mission_category(self):
        _build_registry_tools()
        assert "watch_mission" in list_tools_by_category()["mission"]


    def test_watch_mission_in_known_tool_names(self):
        """New tool ⇒ KNOWN_TOOL_NAMES entry (the no-drift detector in
        tests/unit/tools/test_frozen_tool_name_discovery.py enforces
        equality with the source scan)."""
        from daemon.tools._tool_registry import KNOWN_TOOL_NAMES
        assert "watch_mission" in KNOWN_TOOL_NAMES


# ─── job_create response: mission_id when already known (A2) ──────────────


class TestJobCreateMissionIdResponse:
    def _tools(self):
        job_service = AsyncMock(name="job_service")
        job_service.enqueue = AsyncMock()
        tools = create_job_tools(
            job_service=job_service,
            queue_mgmt_service=MagicMock(name="queue_mgmt"),
            dead_letter_service=MagicMock(name="dlq"),
            current_instance_id=CALLER,
        )
        by_name = {t.name: t for t in tools}
        return job_service, by_name["job_create"]

    def test_pre_dispatch_job_has_no_mission_id_key(self):
        """A fresh pre-dispatch task job has ``instance_id=None`` — the
        ``mission_id`` key is ABSENT and the agent uses ``job_id`` as the
        ``watch_mission`` handle (design A2)."""
        job_service, job_create = self._tools()
        job_item = JobItem(
            job_id=str(uuid.uuid4()),
            agent_id="developer",
            message="work",
            source="agent:jober",
            instance_id=None,
        )
        job_service.enqueue.return_value = job_item

        result = asyncio.run(job_create.ainvoke(
            {"agent_id": "developer", "message": "work"}
        ))

        assert "mission_id" not in result
        assert result["job_id"] == job_item.job_id

    def test_post_dispatch_row_surfaces_mission_id(self):
        """Mirror rows / post-dispatch re-reads already know the instance —
        ``mission_id`` is present (= instance_id per M1 identity)."""
        job_service, job_create = self._tools()
        mid = f"inst-{uuid.uuid4().hex[:12]}"
        job_item = JobItem(
            job_id=str(uuid.uuid4()),
            agent_id="developer",
            message="mirror",
            source="agent:jober",
            instance_id=mid,
        )
        job_service.enqueue.return_value = job_item

        result = asyncio.run(job_create.ainvoke(
            {"agent_id": "developer", "message": "mirror"}
        ))

        assert result["mission_id"] == mid


# ─── High#1 companion: resolver-RAISE degraded paths are logged ───────────


class TestResolverRaiseDegraded:
    """One fixture per handler (watch_mission / unwatch_job /
    list_watched_jobs): a resolver RAISE degrades to the receipt/plain
    path AND is logged at warning — the degraded lookups are never
    silent (tidier High #1 + Med #2; house pattern missions.py
    get_mission)."""

    def test_watch_mission_resolver_raise_falls_to_work_side_and_logs(
        self, engine, job_service, resolver, watcher_repo, watch_mission, caplog
    ):
        mid = _seed_instance(engine)
        receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

        def _raise(handle):
            raise RuntimeError("db down")

        resolver.resolve = _raise

        async def _get_work(work_id):
            return _work_record(work_id, "report", "processing", instance_id=mid)

        job_service.get_work = AsyncMock(side_effect=_get_work)

        with caplog.at_level(logging.WARNING, logger="daemon.tools.job_queue"):
            result = asyncio.run(watch_mission.ainvoke({"target": receipt}))

        # Degraded mission side → work side still resolves the receipt.
        assert "Mission watch registered" in result
        assert _watched_job_ids(watcher_repo) == {receipt}
        assert any(
            "watch_mission" in msg and "mission resolver raised" in msg
            for msg in caplog.messages
        ), caplog.messages

    def test_unwatch_job_resolver_raise_falls_to_receipt_path_and_logs(
        self, resolver, watcher_repo, task_repo, caplog
    ):
        watcher_repo2, _job_service, by_name = _build_job_tools_with_repos(watcher_repo, task_repo, resolver)
        receipt = str(uuid.uuid4())
        watcher_repo2.add_watch(receipt, CALLER, ["mission_terminal"])

        def _raise(handle):
            raise RuntimeError("db down")

        resolver.resolve = _raise

        with caplog.at_level(logging.WARNING, logger="daemon.tools.job_queue"):
            result = asyncio.run(by_name["unwatch_job"].ainvoke({"job_id": receipt}))

        # Degraded mission side → receipt path removes the row.
        assert "Stopped watching job" in result
        assert _watched_job_ids(watcher_repo2) == set()
        assert any(
            "unwatch_job" in msg and "mission resolver raised" in msg
            for msg in caplog.messages
        ), caplog.messages

    def test_list_watched_jobs_resolver_raise_labels_plain_and_logs(
        self, engine, resolver, watcher_repo, task_repo, caplog
    ):
        watcher_repo2, _job_service, by_name = _build_job_tools_with_repos(watcher_repo, task_repo, resolver)
        mid = _seed_instance(engine)
        receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        watcher_repo2.add_watch(receipt, CALLER, ["mission_terminal"])

        def _raise(handle):
            raise RuntimeError("db down")

        resolver.resolve = _raise

        with caplog.at_level(logging.WARNING, logger="daemon.tools.job_queue"):
            result = asyncio.run(by_name["list_watched_jobs"].ainvoke({}))

        assert receipt[:8] in result
        assert "receipt of mission" not in result  # plain, unlabeled row
        assert any(
            "list_watched_jobs" in msg and "mission resolver raised" in msg
            for msg in caplog.messages
        ), caplog.messages


# ─── unwatch_job / list_watched_jobs resolver extension ───────────────────


def _build_job_tools_with_repos(watcher_repo, task_repo, resolver):
    """Build the job-tool set over the SHARED fixtures (no local repo
    rebuild — tidier #15); returns (watcher_repo, job_service, by_name).

    One shared harness for ``TestUnwatchAndListResolverExtension`` and
    ``TestResolverRaiseDegraded``."""
    job_service = MagicMock(name="job_service")
    # Default: unresolved work (labels degrade to plain receipts).
    job_service.get_work = AsyncMock(return_value=None)
    manager = MagicMock(name="manager")
    manager._mission_resolver = resolver
    manager._task_repo = task_repo
    tools = create_job_tools(
        job_service=job_service,
        queue_mgmt_service=MagicMock(name="queue_mgmt"),
        dead_letter_service=MagicMock(name="dlq"),
        current_instance_id=CALLER,
        watcher_repo=watcher_repo,
        manager=manager,
    )
    by_name = {t.name: t for t in tools}
    return watcher_repo, job_service, by_name


class TestUnwatchAndListResolverExtension:
    def _harness(self, watcher_repo, task_repo, resolver):
        return _build_job_tools_with_repos(watcher_repo, task_repo, resolver)

    def test_unwatch_job_mission_handle_removes_all_receipt_rows(
        self, engine, resolver, watcher_repo, task_repo
    ):
        watcher_repo, _job_service, by_name = self._harness(watcher_repo, task_repo, resolver)
        mid = _seed_instance(engine)
        receipt_a = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        receipt_b = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        watcher_repo.add_watch(receipt_a, CALLER, ["mission_terminal"])
        watcher_repo.add_watch(receipt_b, CALLER, ["mission_terminal"])

        result = asyncio.run(by_name["unwatch_job"].ainvoke({"job_id": mid}))

        assert "Stopped watching mission" in result
        assert "2 watch row(s) removed" in result
        assert _watched_job_ids(watcher_repo) == set()

    def test_unwatch_job_receipt_handle_still_works(
        self, resolver, watcher_repo, task_repo
    ):
        watcher_repo, _job_service, by_name = self._harness(watcher_repo, task_repo, resolver)
        receipt = str(uuid.uuid4())
        watcher_repo.add_watch(receipt, CALLER, ["mission_terminal"])

        result = asyncio.run(by_name["unwatch_job"].ainvoke({"job_id": receipt}))

        assert "Stopped watching job" in result
        assert _watched_job_ids(watcher_repo) == set()

    def test_list_watched_jobs_labels_receipts_with_mission(
        self, engine, resolver, watcher_repo, task_repo
    ):
        watcher_repo, job_service, by_name = self._harness(watcher_repo, task_repo, resolver)
        mid = _seed_instance(engine)
        receipt = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
        watcher_repo.add_watch(receipt, CALLER, ["mission_terminal"])

        async def _get_work(work_id):
            return _work_record(work_id, "report", "completed", instance_id=mid)

        job_service.get_work = AsyncMock(side_effect=_get_work)

        result = asyncio.run(by_name["list_watched_jobs"].ainvoke({}))

        assert receipt[:8] in result
        assert f"receipt of mission {mid[:8]}..." in result

    def test_list_watched_jobs_unresolvable_receipt_gets_no_label(
        self, resolver, watcher_repo, task_repo
    ):
        watcher_repo, _job_service, by_name = self._harness(watcher_repo, task_repo, resolver)
        receipt = str(uuid.uuid4())
        watcher_repo.add_watch(receipt, CALLER, ["mission_terminal"])

        result = asyncio.run(by_name["list_watched_jobs"].ainvoke({}))

        assert receipt[:8] in result
        assert "receipt of mission" not in result
