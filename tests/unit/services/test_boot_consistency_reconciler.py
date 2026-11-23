"""Boot-consistency reconciler — behavior pins (fix/post-outage-recovery SCOPE b).

Hermetic SQLite (file-backed, NullPool, REAL SQLModel schema), no live
daemon, no live DB. Mirrors the harness of ``test_boot_orphan_sweep.py``.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.services.boot_consistency_reconciler as lane
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.boot_consistency_reconciler import (
    ReconcileResult,
    reconcile_boot_consistency,
    reset_boot_recovery_state,
)

BOOT_EPOCH = datetime(2026, 11, 23, 18, 0, 0)  # naive UTC


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = create_engine(
        f"sqlite:///{tmp_path}/boot_consistency.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _reset_leg():
    reset_boot_recovery_state()
    yield
    reset_boot_recovery_state()


@pytest.fixture
def repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


def _seed_instance(
    engine: Engine,
    iid: str,
    *,
    status: str = "running",
    parent_id: str | None = None,
    metadata: dict | None = None,
    boot_re_drive_at: datetime | None = None,
    version: int = 1,
) -> None:
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="ari",
                agent_dir="/agents/ari",
                agent_tag="v1",
                status=status,
                parent_id=parent_id,
                instance_metadata=metadata or {},
                boot_re_drive_at=boot_re_drive_at,
                version=version,
            )
        )
        s.commit()


def _seed_event(
    engine: Engine,
    iid: str,
    data: dict | None,
    *,
    kind: str = "message_received",
) -> None:
    with Session(engine) as s:
        s.add(
            Event(
                instance_id=iid,
                message_id=(data or {}).get("message_id"),
                kind=kind,
                data=json.dumps(data) if data is not None else None,
                created_at=BOOT_EPOCH - timedelta(minutes=5),
            )
        )
        s.commit()


def _msg_data(content: str, iid: str = "m-1") -> dict:
    return {
        "message_id": iid,
        "role": "user",
        "content": content,
        "source": "api",
        "created_at": (BOOT_EPOCH - timedelta(minutes=5)).isoformat() + "+00:00",
    }


def _seed_task(
    engine: Engine,
    iid: str,
    status: str,
    *,
    work_id: str | None = None,
    cancel_requested: bool = False,
    completed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> str:
    wid = work_id or f"w-{uuid4().hex[:12]}"
    with Session(engine) as s:
        s.add(
            Task(
                work_id=wid,
                task_type="process_message",
                instance_id=iid,
                status=status,
                cancel_requested=cancel_requested,
                completed_at=completed_at,
                created_at=created_at or (BOOT_EPOCH - timedelta(minutes=4)),
            )
        )
        s.commit()
    return wid


def _seed_mq(engine: Engine, iid: str) -> None:
    with Session(engine) as s:
        s.add(
            MessageQueue(
                message_id=f"mq-{uuid4().hex[:12]}",
                instance_id=iid,
                content="existing",
                enqueued_at=BOOT_EPOCH - timedelta(minutes=5),
            )
        )
        s.commit()


def _get_instance(engine: Engine, iid: str) -> Instance | None:
    with Session(engine) as s:
        return s.get(Instance, iid)


class _MockManager:
    def __init__(
        self,
        repo: Any,
        *,
        checkpoints: dict[str, bool] | None = None,
        resume_status: str = "resuming",
        terminate_ok: bool = True,
        engine: Any | None = None,
    ) -> None:
        self._task_repo = repo
        self._engine = engine
        self._checkpoints = checkpoints or {}
        self.resume_status = resume_status
        self.terminate_ok = terminate_ok
        self.resume_calls: list[dict[str, Any]] = []
        self.terminate_calls: list[tuple[str, str]] = []

    async def _has_checkpoint(self, iid: str) -> bool:
        return bool(self._checkpoints.get(iid, False))

    async def _schedule_explicit_handle_resume(self, **kwargs) -> dict[str, Any] | None:
        self.resume_calls.append(kwargs)
        return {"status": self.resume_status}

    async def terminate_instance(self, iid: str, terminal_reason: str = "aborted") -> bool:
        # UN-MOCKED (review N1): the REAL terminate flips the instance row to
        # a terminal status — the stamp that follows must survive that flip
        # (require_running=False) or the honest bug resurfaces here.
        self.terminate_calls.append((iid, terminal_reason))
        if not self.terminate_ok:
            return False
        with Session(self._engine) as s:
            row = s.get(Instance, iid)
            if row is not None:
                row.status = "terminated"
                s.add(row)
                s.commit()
        return True


class _ConfigurableRepo:
    """Delegates to the real repo; can raise on selection (DB-unreachable pin)."""

    def __init__(self, real: TaskRepository, raise_exc: Exception | None = None) -> None:
        self._real = real
        self._raise = raise_exc
        self.selection_calls = 0

    def find_boot_consistency_candidates(self, boot_epoch):
        self.selection_calls += 1
        if self._raise is not None:
            raise self._raise
        return self._real.find_boot_consistency_candidates(boot_epoch)

    def stamp_boot_re_drive(self, *a, **k):
        return self._real.stamp_boot_re_drive(*a, **k)

    def remint_boot_consistency_work(self, *a, **k):
        return self._real.remint_boot_consistency_work(*a, **k)

    def finalize_boot_consistency_instance(self, *a, **k):
        return self._real.finalize_boot_consistency_instance(*a, **k)


@contextmanager
def _env(name: str, value: str | None):
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


# ---------------------------------------------------------------------------
# 1. MANDATED REPRO — deleted trace re-derived from the Event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mandated_repro_rederives_task_and_mq(engine, repo) -> None:
    iid = "repro-1"
    _seed_instance(engine, iid)
    _seed_event(engine, iid, _msg_data("hello from the dead turn"))
    # No MQ row, no Task row — the post-commit-deletion signature.
    mgr = _MockManager(repo, checkpoints={iid: True}, engine=engine)

    out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert out.candidates == 1
    assert out.dispatched == 1
    assert out.errors == 0
    assert len(mgr.resume_calls) == 1
    assert mgr.resume_calls[0]["silent"] is True
    assert mgr.resume_calls[0]["route_outcome"] == "boot_consistency_deleted_trace"
    with Session(engine) as s:
        mqs = s.query(MessageQueue).filter(MessageQueue.instance_id == iid).all()
        tasks = s.query(Task).filter(Task.instance_id == iid).all()
    assert len(mqs) == 1 and mqs[0].content == "hello from the dead turn"
    assert len(tasks) == 1 and tasks[0].status == TaskStatus.PENDING.value
    assert tasks[0].work_id == mgr.resume_calls[0]["target_work_id"]
    stamped = _get_instance(engine, iid)
    assert stamped is not None and stamped.boot_re_drive_at == BOOT_EPOCH


# ---------------------------------------------------------------------------
# 2. MANDATED PG-UNREACHABLE PIN — fail loudly, never wedge, readyz degraded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pg_unreachable_logs_error_and_degrades_readyz(engine, repo, caplog) -> None:
    import logging

    raising = _ConfigurableRepo(repo, raise_exc=OperationalError("SELECT", {}, Exception("db down")))
    mgr = _MockManager(raising)

    with caplog.at_level(logging.ERROR):
        out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert out.errors >= 1
    assert out.dispatched == 0
    assert any("[BOOT_RECOVERY_DB_ERROR] pass=selection" in r.message for r in caplog.records)
    state = lane.get_boot_recovery_state()
    assert state["status"] == "degraded" and state["ok"] is False


@pytest.mark.asyncio
async def test_healthy_run_leg_healthy(engine, repo) -> None:
    mgr = _MockManager(repo, engine=engine)  # empty DB → zero candidates
    out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)
    assert out.errors == 0
    state = lane.get_boot_recovery_state()
    assert state["status"] == "healthy" and state["ok"] is True


# ---------------------------------------------------------------------------
# 3. MANDATED GHOST PIN — dispatch-capable with content; retire without event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ghost_with_content_dispatched_not_terminated(engine, repo) -> None:
    iid = "ghost-a"
    _seed_instance(engine, iid)
    _seed_event(engine, iid, _msg_data("ghost has content"))
    mgr = _MockManager(repo, checkpoints={iid: False}, engine=engine)  # no checkpoint

    out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert out.dispatched == 1 and out.ghost_dispatched == 1
    assert out.retired == 0
    assert mgr.terminate_calls == []
    assert len(mgr.resume_calls) == 1


@pytest.mark.asyncio
async def test_ghost_without_event_retired_loudly(engine, repo, caplog) -> None:
    import logging

    iid = "ghost-b"
    _seed_instance(
        engine,
        iid,
        metadata={"title": "Lost Mission", "initiative_message": "do the thing"},
    )
    # No event row at all.
    mgr = _MockManager(repo, checkpoints={iid: False}, engine=engine)

    with caplog.at_level(logging.ERROR):
        out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert out.retired == 1
    assert out.dispatched == 0
    assert mgr.terminate_calls == [(iid, "orphan_retired")]
    assert mgr.resume_calls == []
    # N1 (un-mocked): the retire's stamp must have LANDED even though the
    # terminate flipped the row off 'running' — post-flip stamping with
    # require_running=True would return False here and retire would stay 0
    # while skipped_disposition_refused ticked (the honest failure).
    retired_row = _get_instance(engine, iid)
    assert retired_row is not None and retired_row.boot_re_drive_at == BOOT_EPOCH, (
        "the retire disposition must stamp boot_re_drive_at AFTER the "
        "status flip (require_running=False)"
    )
    assert any("Tier-2 RETIRE" in r.message and "Lost Mission" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 4. Blocked-claim latent family — continue-in-place, never force-cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_claim_redriven_without_cancel(engine, repo) -> None:
    iid = "blocked-1"
    _seed_instance(engine, iid)
    running_wid = _seed_task(engine, iid, "running")
    _seed_task(engine, iid, "pending")  # blocked by the RUNNING task
    mgr = _MockManager(repo, checkpoints={iid: True}, engine=engine)

    out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert out.redriven == 1
    assert len(mgr.resume_calls) == 1
    assert mgr.resume_calls[0]["target_work_id"] == running_wid
    assert mgr.resume_calls[0]["route_outcome"] == "boot_consistency_blocked_claim"
    # The stale RUNNING task is NOT force-cancelled by the lane.
    with Session(engine) as s:
        still_running = s.query(Task).filter(Task.work_id == running_wid).one()
    assert still_running.status == TaskStatus.RUNNING.value
    assert mgr.terminate_calls == []


# ---------------------------------------------------------------------------
# 5. Healer deference — completed-task ROOT skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_task_root_deferred_to_healer(engine, repo, caplog) -> None:
    import logging

    iid = "root-done"
    _seed_instance(engine, iid, parent_id=None)
    _seed_task(
        engine, iid, "completed",
        completed_at=BOOT_EPOCH - timedelta(minutes=2),
    )
    mgr = _MockManager(repo, engine=engine)

    with caplog.at_level(logging.INFO):
        out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert out.deferred_to_healer == 1
    assert out.dispatched == out.retired == out.finalized == out.redriven == 0
    assert mgr.resume_calls == []
    assert any("deferred_to_healer" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 6. CAS — pre-stamped and double-run are exactly-once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prestamped_instance_not_selected(engine, repo) -> None:
    iid = "prestamped"
    _seed_instance(engine, iid, boot_re_drive_at=BOOT_EPOCH)
    _seed_event(engine, iid, _msg_data("already handled"))
    mgr = _MockManager(repo, checkpoints={iid: True}, engine=engine)

    out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert out.candidates == 0
    assert out.dispatched == 0 and mgr.resume_calls == []


@pytest.mark.asyncio
async def test_double_run_same_epoch_schedules_once(engine, repo) -> None:
    iid = "double"
    _seed_instance(engine, iid)
    _seed_event(engine, iid, _msg_data("once only"))
    mgr = _MockManager(repo, checkpoints={iid: True}, engine=engine)

    first = await reconcile_boot_consistency(mgr, BOOT_EPOCH)
    second = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert first.dispatched == 1
    assert second.candidates == 0 and second.dispatched == 0
    assert len(mgr.resume_calls) == 1


# ---------------------------------------------------------------------------
# 7. Tier split — empty content retires (Tier 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_event_content_retires(engine, repo) -> None:
    iid = "empty-content"
    _seed_instance(engine, iid)
    _seed_event(engine, iid, _msg_data("   "))  # whitespace-only
    mgr = _MockManager(repo, checkpoints={iid: True}, engine=engine)

    out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert out.retired == 1 and out.dispatched == 0
    assert mgr.terminate_calls == [(iid, "orphan_retired")]


# ---------------------------------------------------------------------------
# 8. Exclusions — WC / PAUSED / cancel_requested / terminal not selected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_excluded_statuses_and_cancel_requested(engine, repo) -> None:
    # WAITING_CHILDREN with a deleted-trace shape
    _seed_instance(engine, "wc", status="waiting_children")
    _seed_event(engine, "wc", _msg_data("wc"))
    # PAUSED with a deleted-trace shape
    _seed_instance(engine, "paused", status="paused")
    _seed_event(engine, "paused", _msg_data("paused"))
    # Terminal
    _seed_instance(engine, "done", status="completed")
    _seed_event(engine, "done", _msg_data("done"))
    # cancel_requested blocked-claim family
    _seed_instance(engine, "cancelled")
    _seed_task(engine, "cancelled", "running", cancel_requested=True)
    _seed_task(engine, "cancelled", "pending")

    mgr = _MockManager(repo, engine=engine)
    out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert out.candidates == 0
    assert mgr.resume_calls == [] and mgr.terminate_calls == []


# ---------------------------------------------------------------------------
# 13. Finalize-gap NON-ROOT — finalized to COMPLETED, no resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_gap_non_root_finalized(engine, repo) -> None:
    iid = "child-done"
    _seed_instance(engine, iid, parent_id="parent-1")
    _seed_task(
        engine, iid, "completed",
        completed_at=BOOT_EPOCH - timedelta(minutes=2),
    )
    mgr = _MockManager(repo, engine=engine)

    out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert out.finalized == 1
    assert out.deferred_to_healer == 0
    assert mgr.resume_calls == []
    final = _get_instance(engine, iid)
    assert final is not None and final.status == "completed"
    assert final.boot_re_drive_at == BOOT_EPOCH


# ---------------------------------------------------------------------------
# 14. N2 — multi-family overlap: ONE disposition per instance per pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_family_overlap_single_disposition(engine, repo) -> None:
    """N2 (review): an instance can satisfy TWO families at once — a stale
    RUNNING task (blocked_claim) AND a terminal latest task
    (completed_task). Pre-fix both rows dispositioned (re-drive + finalize =
    double disposition, finalized miscount). Post-fix the FIRST family that
    achieves a disposition claims the instance; the later family is skipped
    for that instance in this pass."""
    iid = "overlap-1"
    _seed_instance(engine, iid, parent_id="parent-2")
    # The stale RUNNING task + the PENDING task (blocked_claim needs BOTH —
    # stale RUNNING blocker AND a pending claim-blocked task) ...
    _seed_task(engine, iid, "running", work_id="w-stale", cancel_requested=False)
    _seed_task(engine, iid, "pending", work_id="w-pending")
    # ... and a COMPLETED latest task (completed_task family, non-root).
    _seed_task(
        engine, iid, "completed",
        completed_at=BOOT_EPOCH - timedelta(minutes=2),
    )
    mgr = _MockManager(repo, engine=engine)

    out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    # Exactly ONE disposition: the re-drive. NO finalize on top of it.
    assert out.redriven == 1, f"exactly one re-drive expected, got {out!r}"
    assert out.finalized == 0, (
        "a second family must NOT disposition the same instance in one pass"
    )
    assert len(mgr.resume_calls) == 1
    final = _get_instance(engine, iid)
    assert final is not None and final.status == "running", (
        "the re-dispositioned instance must NOT have been finalized"
    )
    assert final.boot_re_drive_at == BOOT_EPOCH


# ---------------------------------------------------------------------------
# 15. N8 — completed_task family excludes cancel_requested
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_task_cancel_requested_excluded(engine, repo) -> None:
    """N8 (review): a deliberate stop is never re-driven — the completed_task
    family must exclude tasks carrying ``cancel_requested`` just like the
    task-based families do."""
    iid = "child-cancelled"
    _seed_instance(engine, iid, parent_id="parent-3")
    _seed_task(
        engine, iid, "completed",
        cancel_requested=True,
        completed_at=BOOT_EPOCH - timedelta(minutes=2),
    )
    mgr = _MockManager(repo, engine=engine)

    out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)

    assert out.candidates == 0, "cancel_requested completed_task must not be selected"
    assert out.finalized == 0 and out.deferred_to_healer == 0
    assert mgr.resume_calls == [] and mgr.terminate_calls == []
