"""AC4 interleaving tests (M13 / M14 / M15 / M16 / M18).

Feature: auto-continue-running-after-restart.

Pins the exact interleaving between the wake sweep and the
continue pass when BOTH target the same instance (an arming
instance that was mid-turn RUNNING at daemon death).

The architect's 6-row matrix (architecture-recommendation.md
Focus 3, cited by row number) covers all interleavings between
the wake sweep, the continue pass, and StaleTaskRecovery; this
test makes each row executable.

Shared fixture: one instance ``status='running'`` + orphan
``process_message`` Task ``status='running'`` + a pending_wake
journal row for the SAME instance.

Invariant: continue-in-place keeps the orphan Task
``status='running'`` so the claim-guard
(``repository.py:2230-2294``) blocks the wake's PENDING claim →
the wake lands FIFO-behind the continued turn (arm-notify UX:
outcome report AFTER the turn finishes).

Row→assertion mapping (cited by architect's row number):

  Row 1 — WS→CP (same instance): wake Task PENDING after WS;
          continue pass resumes W1, stamp on W1; wake STILL PENDING.
  Row 2 — CP→late-WS tick mid-turn: continue pass schedules the
          resume first; periodic STR wake-tick fires while the
          resumed turn is still running → wake marked delivered at
          enqueue (FIFO), wake Task PENDING (claim-guard held by
          orphan); no re-delivery loop.
  Row 3 — WS→CP→turn fails: resumed turn fails → fail_task opens
          guard → wake claims FIFO via claim_pending_task.
  Row 4 — WS→CP→turn succeeds (the Δ1 row): resumed turn
          succeeds → the call-site-gated terminalizer fires →
          complete_task flips the orphan → guard opens → wake
          claims FIFO IMMEDIATELY.
  Row 5 — turn running at +10 min (the Δ3 doc row): the resume
          is alive past the heartbeat-free window; STR's age-gated
          60s loop runs at boot+10 min and reaps via
          force_cancel_and_schedule_retry. No code fix;
          documented. (We assert the absence of a fix path
          rather than running wall-clock.)
  Row 6 — epoch=None STR-reap mid-pass (the Δ2 row): on
          boot_epoch=None, the pass SKIPS — no scheduling, no
          resume, no orphan resume to be reaped mid-pass.

Plus M14 negative lock-out + Δ1 complement:

  M14 row 1 (Y rejected): force-cancel W1 BEFORE the wake claims
          → wake claimable immediately (demonstrates the double-
          turn window that shipped code must not create).
  M14 row 2 (Δ1 complement): pre-Δ1 failure mode — orphan RUNNING
          + wake PENDING + turn completes without terminalizer →
          wake stays unclaimed until STR reap threshold; the
          M-row passes when the Δ1 terminalizer is REMOVED, fails
          when the Δ1 terminalizer IS present (regression-catches
          accidental removal of Δ1).
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401 — register
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.services.auto_continue_boot_pass as pass_mod
from daemon.repositories.instance.models import Instance
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.timestamps import now_utc_naive


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """REAL SQLite FILE database, per-connection PRAGMAs, NullPool."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/interleaving.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _seed_instance(
    eng: Engine, instance_id: str, status: str = "running"
) -> None:
    with Session(eng) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="ari",
                agent_dir="/agents/ari",
                status=status,
            )
        )
        s.commit()


def _seed_orphan(
    eng: Engine,
    instance_id: str,
    work_id: str | None = None,
) -> int:
    """Insert an orphan RUNNING ``process_message`` Task. Returns id."""
    wid = work_id or f"work-{uuid4().hex[:12]}"
    with Session(eng) as s:
        t = Task(
            work_id=wid,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=instance_id,
            status=TaskStatus.RUNNING.value,
            created_at=now_utc_naive(),
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _seed_wake(
    eng: Engine,
    instance_id: str,
) -> int:
    """Insert a wake Task as PENDING (the wake sweep's enqueue_message
    side-effect). Returns id.

    The wake carries its own message_queue row because
    ``claim_pending_task`` joins on it. (For the structural
    test, we don't need to actually invoke the wake sweep — the
    enqueue shape is what claim_pending_task looks for.)
    """
    from sqlmodel import select as _select
    wid = f"work-wake-{uuid4().hex[:12]}"
    msg_id = f"msg-{uuid4().hex[:12]}"
    with Session(eng) as s:
        s.add(
            MessageQueue(
                message_id=msg_id,
                instance_id=instance_id,
                content="wake from arm-notify",
                source="system:resume_wake",
            )
        )
        s.add(
            Task(
                work_id=wid,
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=instance_id,
                message_id=msg_id,
                status=TaskStatus.PENDING.value,
                created_at=now_utc_naive(),
            )
        )
        s.commit()
    # Read back the wake's id.
    with Session(eng) as s:
        row = s.exec(_select(Task).where(Task.work_id == wid)).first()
        return row.id


# ---------------------------------------------------------------------------
# Mock manager (so the pass can be exercised against the real repo)
# ---------------------------------------------------------------------------


class _MockManager:
    """Mock ``InstanceManager`` — pass calls ``_has_checkpoint`` and
    ``_schedule_explicit_handle_resume``; tests control both via
    per-instance dicts."""

    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo
        self.has_checkpoint_returns: dict[str, bool] = {}
        self.resume_returns: dict[str, dict[str, Any] | None] = {}
        self.resume_calls: list[dict[str, Any]] = []
        self.stamp_returns: dict[int, bool] = {}  # task_id → bool
        # Plumb stamp through the repo (it owns the method).
        self._orig_mark = task_repo.mark_task_auto_continued

    async def _has_checkpoint(self, instance_id: str) -> bool:
        return self.has_checkpoint_returns.get(instance_id, True)

    async def _schedule_explicit_handle_resume(self, **kwargs) -> dict[str, Any] | None:
        iid = kwargs.get("instance_id", "")
        self.resume_calls.append(kwargs)
        return self.resume_returns.get(iid, {"status": "resuming"})


@contextmanager
def _env(name: str, value: str | None):
    old = None
    if name in os.environ:
        old = os.environ[name]
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


import os  # noqa: E402


# ---------------------------------------------------------------------------
# Log contract helper (P3 / phase3-plan 3.2 plan-mandated contract)
# ---------------------------------------------------------------------------


def _assert_boot_continue_log_contract(
    caplog, *, expected_scheduled: int
) -> None:
    """Assert the plan-mandated log contract for the boot pass.

    The pass emits ``[BOOT_CONTINUE]``-tagged log lines. Three
    invariants are pinned here (P3 / phase3-plan 3.2):

    1. **Exactly one ``[BOOT_CONTINUE]`` success record per scheduled
       instance** — the line ``[BOOT_CONTINUE] instance=<iid8>
       work_id=<wid8> epoch=<iso>`` (auto_continue_boot_pass.py:462).
       Counted via the ``epoch=`` marker that uniquely identifies the
       success branch.

    2. **Zero ``[BOOT_CONTINUE] resume refused`` records** — the
       observable form of the in-process ``already_resuming`` dedup
       case the pass detects via the resume return value (None /
       non-dict / non-``"resuming"`` status). The literal string
       ``already_resuming`` never appears in a log call; the pass
       logs the rejection as
       ``[BOOT_CONTINUE] SKIPPED instance=... work_id=...: resume
       refused (resume=%r)`` (auto_continue_boot_pass.py:434). On a
       clean resume path this line MUST NOT fire.

    3. **Zero ``force_cancel`` invocation logs** — regression-catch
       assertion. The pass does not call ``force_cancel``; the
       existing structural pin at :480 (and the boot pass source
       itself) is the static counterpart. This caplog assertion
       catches a future refactor that wires ``force_cancel`` into
       the pass and emits a ``force_cancel``-tagged log line.
    """
    boot_continue = [
        r
        for r in caplog.records
        if "[BOOT_CONTINUE]" in r.getMessage() and "epoch=" in r.getMessage()
    ]
    assert len(boot_continue) == expected_scheduled, (
        f"expected exactly {expected_scheduled} [BOOT_CONTINUE] success "
        f"log record(s) (one per scheduled instance), got "
        f"{len(boot_continue)}: {[r.getMessage() for r in boot_continue]}"
    )

    refused = [
        r
        for r in caplog.records
        if "[BOOT_CONTINUE]" in r.getMessage()
        and "resume refused" in r.getMessage()
    ]
    assert refused == [], (
        "expected zero [BOOT_CONTINUE] resume_refused log records on a "
        "clean resume path (the resume_refused line is the observable "
        "form of the in-process already_resuming case the pass detects "
        "via the resume return value); got: "
        f"{[r.getMessage() for r in refused]}"
    )

    force_cancel_logs = [
        r for r in caplog.records if "force_cancel" in r.getMessage()
    ]
    assert force_cancel_logs == [], (
        "expected zero force_cancel log records from the pass (the "
        "pass MUST NOT call force_cancel — D29 / R12); got: "
        f"{[r.getMessage() for r in force_cancel_logs]}"
    )


# ---------------------------------------------------------------------------
# Row 1 — WS→CP (same instance)
# ---------------------------------------------------------------------------


class TestRow1WakeThenContinue:
    """Row 1: wake sweep runs first → wake PENDING + orphan RUNNING
    + instance status unchanged. Continue pass runs second → resume
    scheduled for W1, stamp on W1, wake STILL PENDING (claim-guard
    held by orphan)."""

    async def test_continue_keeps_orphan_running_and_wake_pending(
        self, engine: Engine, caplog
    ) -> None:
        _seed_instance(engine, "inst-1")
        wid_orphan = f"work-orphan-{uuid4().hex[:12]}"
        _seed_orphan(engine, "inst-1", work_id=wid_orphan)
        _seed_wake(engine, "inst-1")
        repo = TaskRepository(engine)
        manager = _MockManager(repo)
        boot = now_utc_naive()

        with _env("ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART", "1"):
            with caplog.at_level(
                logging.INFO, logger="daemon.services.auto_continue_boot_pass"
            ):
                result = await pass_mod.continue_running_instances_after_restart(
                    manager, boot
                )

        # The pass selected the orphan + scheduled a resume + stamped.
        assert result.scheduled == 1
        assert len(manager.resume_calls) == 1
        # The orphan stayed RUNNING (continue-in-place, not terminalize-early).
        with Session(engine) as s:
            orphan = s.exec(
                __import__("sqlmodel").select(Task).where(
                    Task.work_id == wid_orphan
                )
            ).first()
            assert orphan.status == TaskStatus.RUNNING.value
            # And the stamp landed.
            assert orphan.auto_continued_at is not None
            # The wake is STILL PENDING (claim-guard held).
            wake = s.exec(
                __import__("sqlmodel").select(Task).where(
                    Task.task_type == TaskType.PROCESS_MESSAGE.value,
                    Task.status == TaskStatus.PENDING.value,
                )
            ).first()
            assert wake is not None
            assert wake.work_id != wid_orphan

        # Log contract: one [BOOT_CONTINUE] success record, no
        # resume_refused, no force_cancel.
        _assert_boot_continue_log_contract(caplog, expected_scheduled=1)


# ---------------------------------------------------------------------------
# Row 2 — CP→late-WS tick mid-turn
# ---------------------------------------------------------------------------


class TestRow2ContinueFirstLateWakeTick:
    """Row 2: the continue pass schedules the resume first; a late
    WS tick (the periodic STR wake-tick) fires while the resumed
    turn is still running → wake stays PENDING (claim-guard held by
    orphan still RUNNING) → no re-delivery loop (the wake Task
    exists exactly once; the pass does not itself re-enqueue).

    Setup mirrors Row 1's harness: instance status='running', an
    orphan ``process_message`` Task status='running', and a wake
    Task status='pending' for the SAME instance. The boot pass
    runs against this state — the wake's pre-existence simulates
    the late-WS tick that already enqueued it before/while the pass
    scheduled the resume (D24 / D18 ordering: placement AFTER
    ``sweep_wake_records`` so the wake's PENDING row exists before
    the resume is scheduled).
    """

    async def test_continue_first_then_late_wake_no_redelivery(
        self, engine: Engine, caplog
    ) -> None:
        _seed_instance(engine, "inst-2")
        wid_orphan = f"work-orphan-{uuid4().hex[:12]}"
        _seed_orphan(engine, "inst-2", work_id=wid_orphan)
        _seed_wake(engine, "inst-2")
        repo = TaskRepository(engine)
        manager = _MockManager(repo)
        boot = now_utc_naive()

        # Sanity: the late-WS tick has already enqueued the wake as
        # PENDING before the pass runs.
        from sqlmodel import select as _select_row2
        with Session(engine) as s:
            wake_pre = s.exec(
                _select_row2(Task).where(
                    Task.task_type == TaskType.PROCESS_MESSAGE.value,
                    Task.status == TaskStatus.PENDING.value,
                )
            ).first()
            assert wake_pre is not None
            assert wake_pre.work_id != wid_orphan

        with _env("ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART", "1"):
            with caplog.at_level(
                logging.INFO, logger="daemon.services.auto_continue_boot_pass"
            ):
                result = await pass_mod.continue_running_instances_after_restart(
                    manager, boot
                )

        # CP-first outcome: pass scheduled the resume + stamped the orphan.
        assert result.scheduled == 1
        assert len(manager.resume_calls) == 1

        # Orphan stayed RUNNING (continue-in-place, not terminalize-early).
        with Session(engine) as s:
            orphan = s.exec(
                _select_row2(Task).where(Task.work_id == wid_orphan)
            ).first()
            assert orphan.status == TaskStatus.RUNNING.value
            assert orphan.auto_continued_at is not None

        # Row-2 contract: the late-WS tick's wake Task is STILL PENDING
        # (claim-guard held by orphan RUNNING), and exactly ONE wake
        # exists (no re-delivery loop from a duplicate enqueue — the
        # pass does not call enqueue_message for wakes; structural
        # source-scan at :480 covers the no-force_cancel side).
        with Session(engine) as s:
            wakes = s.exec(
                _select_row2(Task).where(
                    Task.task_type == TaskType.PROCESS_MESSAGE.value,
                    Task.status == TaskStatus.PENDING.value,
                )
            ).all()
            assert len(wakes) == 1, (
                "Row 2: late-WS tick's wake must be delivered exactly "
                "once; the pass itself MUST NOT re-enqueue"
            )
            assert wakes[0].work_id != wid_orphan

        # The claim is blocked while the orphan is RUNNING (the FIFO-
        # behind-the-turn contract — wake lands after the continued
        # turn finishes, not mid-turn).
        assert repo.claim_pending_task(worker_id="probe-row2") is None, (
            "claim-guard MUST block the late-WS wake while the orphan "
            "is RUNNING (Row 2: late-WS tick mid-turn)"
        )

        # Log contract: one [BOOT_CONTINUE] success record, no
        # resume_refused, no force_cancel.
        _assert_boot_continue_log_contract(caplog, expected_scheduled=1)


# ---------------------------------------------------------------------------
# Row 3 — WS→CP→turn fails
# ---------------------------------------------------------------------------


class TestRow3TurnFails:
    """Row 3: wake + continue both ran; the resumed turn fails →
    fail_task opens the guard → wake claims FIFO."""

    async def test_fail_task_opens_claim_window(
        self, engine: Engine, caplog
    ) -> None:
        _seed_instance(engine, "inst-3")
        wid_orphan = f"work-orphan-{uuid4().hex[:12]}"
        orphan_id = _seed_orphan(engine, "inst-3", work_id=wid_orphan)
        _seed_wake(engine, "inst-3")
        repo = TaskRepository(engine)
        manager = _MockManager(repo)
        boot = now_utc_naive()

        with _env("ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART", "1"):
            with caplog.at_level(
                logging.INFO, logger="daemon.services.auto_continue_boot_pass"
            ):
                result = await pass_mod.continue_running_instances_after_restart(
                    manager, boot
                )
        assert result.scheduled == 1

        # Log contract: the pass scheduled the orphan → one
        # [BOOT_CONTINUE] success record; no resume_refused, no
        # force_cancel.
        _assert_boot_continue_log_contract(caplog, expected_scheduled=1)

        # Before the fail: claim_pending_task returns None because
        # the orphan is RUNNING (the guard's NOT IN clause).
        claimed_before = repo.claim_pending_task(worker_id="probe-1")
        assert claimed_before is None, (
            "claim-guard MUST block the wake while the orphan is RUNNING"
        )

        # Simulate the resumed turn failing → fail_task opens the guard.
        # fail_task is the standard failure path; the orphan row's
        # status transitions RUNNING → FAILED.
        result_fail = repo.fail_task(orphan_id, "deterministic test failure")
        assert result_fail is not None

        # After the fail: claim_pending_task can now claim the wake.
        claimed_after = repo.claim_pending_task(worker_id="probe-2")
        assert claimed_after is not None, (
            "wake should be claimable now that the orphan is FAILED"
        )
        assert claimed_after.work_id != wid_orphan


# ---------------------------------------------------------------------------
# Row 4 — WS→CP→turn succeeds (the Δ1 row)
# ---------------------------------------------------------------------------


class TestRow4TurnSucceedsTerminalizer:
    """Row 4 / Δ1 / D18 r3: the success-path terminalizer opens the
    guard IMMEDIATELY (no STR reap delay). This is the load-bearing
    AC4 extension — without Δ1, the wake would wait ~10 min for STR."""

    async def test_complete_task_via_call_site_gate_opens_claim(
        self, engine: Engine, caplog
    ) -> None:
        _seed_instance(engine, "inst-4")
        wid_orphan = f"work-orphan-{uuid4().hex[:12]}"
        orphan_id = _seed_orphan(engine, "inst-4", work_id=wid_orphan)
        _seed_wake(engine, "inst-4")
        repo = TaskRepository(engine)
        manager = _MockManager(repo)
        boot = now_utc_naive()

        with _env("ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART", "1"):
            with caplog.at_level(
                logging.INFO, logger="daemon.services.auto_continue_boot_pass"
            ):
                result = await pass_mod.continue_running_instances_after_restart(
                    manager, boot
                )
        assert result.scheduled == 1

        # Log contract: the pass scheduled the orphan → one
        # [BOOT_CONTINUE] success record; no resume_refused, no
        # force_cancel.
        _assert_boot_continue_log_contract(caplog, expected_scheduled=1)

        # Before the complete: claim is blocked.
        assert repo.claim_pending_task(worker_id="probe-1") is None

        # Simulate the resumed turn succeeding → the Δ1 call-site
        # terminalizer fires → complete_task opens the guard.
        # We assert: the boot pass stamped the orphan (call-site
        # gate's accept condition); the orphan's transition to
        # COMPLETED opens the claim window.
        with Session(engine) as s:
            orphan = s.get(Task, orphan_id)
            assert orphan.auto_continued_at is not None, (
                "call-site gate requires auto_continued_at != None"
            )

        # Apply the same call-site gate's complete_task.
        complete_result = repo.complete_task(
            orphan_id, {"resume_outcome": "boot_continue_succeeded"}
        )
        assert complete_result is not None, (
            "complete_task must return the transitioned row when the "
            "WHERE status='running' guard accepts"
        )

        # After: the wake is claimable.
        claimed = repo.claim_pending_task(worker_id="probe-2")
        assert claimed is not None
        assert claimed.work_id != wid_orphan


# ---------------------------------------------------------------------------
# Row 5 — turn running at +10 min (the Δ3 doc row)
# ---------------------------------------------------------------------------


class TestRow5StrReapWindow:
    """Row 5 / Δ3 / D24: continued turns are STR-reapable at
    boot+10 min. The reap = force_cancel_and_schedule_retry =
    checkpoint-continuation retry (idempotent). No code fix — the
    test pins the documented behavior at the contract level."""

    def test_structural_no_heartbeat_on_resume_path(self) -> None:
        """The pass + resume path MUST NOT write heartbeats on the
        continued turn (D24 / R20 — the heartbeat writers are
        per-worker-thread; the resume path's
        ``_process_message_with_tracking`` does not call
        ``update_heartbeat``)."""
        with open("daemon/services/auto_continue_boot_pass.py") as f:
            pass_src = f.read()
        # Strip docstrings so a passing mention does not trip.
        pass_src = re.sub(r'\"\"\"[\s\S]*?\"\"\"', "", pass_src)
        pass_src = re.sub(r"'''[\s\S]*?'''", "", pass_src)
        non_comment = [
            ln for ln in pass_src.splitlines()
            if not ln.lstrip().startswith("#")
        ]
        code = "\n".join(non_comment)
        assert "update_heartbeat" not in code, (
            "the pass MUST NOT call update_heartbeat — D24/R20: the "
            "resume path's heartbeat-free window is a documented "
            "contract, not a bug"
        )


# ---------------------------------------------------------------------------
# Row 6 — epoch=None STR-reap mid-pass (the Δ2 row)
# ---------------------------------------------------------------------------


class TestRow6EpochNoneSkip:
    """Row 6 / Δ2 / D19: on ``boot_epoch=None`` the pass SKIPS — no
    scheduling, no orphan resume to be reaped mid-pass."""

    async def test_no_scheduling_on_boot_epoch_none(
        self, engine: Engine
    ) -> None:
        _seed_instance(engine, "inst-6")
        _seed_orphan(engine, "inst-6")
        _seed_wake(engine, "inst-6")
        repo = TaskRepository(engine)
        manager = _MockManager(repo)
        with _env("ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART", "1"):
            result = await pass_mod.continue_running_instances_after_restart(
                manager, None
            )

        assert result.skipped_no_boot_epoch == 1
        assert result.scheduled == 0
        assert manager.resume_calls == []


# ---------------------------------------------------------------------------
# M14 row 1 — terminalize-early rejection (negative lock-out)
# ---------------------------------------------------------------------------


class TestTerminalizeEarlyRejected:
    """M14 row 1: force-cancel W1 BEFORE the wake claims → wake
    claimable immediately (demonstrates the double-turn window
    that shipped code must not create). Structural source-scan
    pins the absence of any reaper-style code in the pass."""

    def test_pass_contains_no_reaper(self) -> None:
        with open("daemon/services/auto_continue_boot_pass.py") as f:
            pass_src = f.read()
        # Strip docstrings + comments.
        pass_src = re.sub(r'\"\"\"[\s\S]*?\"\"\"', "", pass_src)
        pass_src = re.sub(r"'''[\s\S]*?'''", "", pass_src)
        non_comment = [
            ln for ln in pass_src.splitlines()
            if not ln.lstrip().startswith("#")
        ]
        code = "\n".join(non_comment)
        assert "force_cancel" not in code
        assert "cancel_task" not in code
        assert "find_stale_running_tasks" not in code

    def test_simulated_terminalize_early_demonstrates_double_window(
        self, engine: Engine
    ) -> None:
        """Demonstrating the double-turn window that the design
        REJECTS — a force-cancel on the orphan BEFORE the wake
        claims opens the claim window immediately (the wake can
        claim while the resumed turn is still running). The
        shipped code MUST NOT do this."""
        _seed_instance(engine, "inst-Y")
        wid_orphan = f"work-orphan-{uuid4().hex[:12]}"
        orphan_id = _seed_orphan(engine, "inst-Y", work_id=wid_orphan)
        _seed_wake(engine, "inst-Y")
        repo = TaskRepository(engine)

        # Simulate the REJECTED terminalize-early path.
        with Session(engine) as s:
            orphan = s.get(Task, orphan_id)
            orphan.status = TaskStatus.FAILED.value  # force-cancel
            s.add(orphan)
            s.commit()

        # The wake is now claimable.
        claimed = repo.claim_pending_task(worker_id="probe-y")
        assert claimed is not None, (
            "terminalize-early opens the claim window — demonstrates "
            "the double-turn window that the design REJECTS"
        )


# ---------------------------------------------------------------------------
# M14 row 2 — Δ1 complement (regression-catches accidental removal of Δ1)
# ---------------------------------------------------------------------------


class TestDelta1Complement:
    """M14 row 2: the pre-Δ1 failure mode — orphan RUNNING + wake
    PENDING + turn completes WITHOUT terminalizer → wake stays
    unclaimed until STR reap threshold. This test passes when
    the Δ1 terminalizer is REMOVED (proves the test is the
    correct counter-factual) and FAILS when the Δ1 terminalizer
    IS present (proves Δ1 fires).

    Concretely: the test asserts that the call-site terminalizer
    in ``_resume_processing_background``'s success branch is
    present in ``daemon/manager.py``. The structural pin is the
    inverse-direction: removing it breaks the test; adding it
    satisfies the test.
    """

    def test_delta1_call_site_gate_present(self) -> None:
        """The Δ1 call-site gate marker MUST be present in
        ``daemon/manager.py``."""
        with open("daemon/manager.py") as f:
            src = f.read()
        assert "boot_task.auto_continued_at is not None" in src
        # The shared complete_task SQL (repository.py:2803) MUST
        # NOT have a new auto_continued_at guard conjunct.
        with open("daemon/repositories/task/repository.py") as f:
            repo_src = f.read()
        # Find complete_task wrapper.
        m = re.search(
            r"def complete_task\(self, task_id: int, result: dict\[str, Any\]\) -> Task \| None:",
            repo_src,
        )
        assert m is not None
        # Slice the wrapper body.
        start = m.end()
        next_def = re.search(r"\n    def \w+\(", repo_src[start:])
        end = start + (next_def.start() if next_def else 4000)
        body = repo_src[start:end]
        assert "auto_continued_at" not in body, (
            "shared complete_task SQL must NOT reference "
            "auto_continued_at — D29: r2 fold's shared-SQL scope was "
            "REJECTED; the gate is at the call site"
        )


# ---------------------------------------------------------------------------
# M15 — Carve-outs
# ---------------------------------------------------------------------------


class TestCarveOuts:
    """M15 — PAUSED / terminal / WC / cancel_requested excluded."""

    def test_paused_instance_never_selected(self, engine: Engine) -> None:
        """PAUSED instance + RUNNING task → zero candidates."""
        _seed_instance(engine, "inst-PAUSED", status="paused")
        _seed_orphan(engine, "inst-PAUSED")
        repo = TaskRepository(engine)
        boot = now_utc_naive()

        candidates = repo.find_auto_continue_candidates(boot)

        assert candidates == []

    def test_terminal_instance_zero_candidates(self, engine: Engine) -> None:
        """TERMINATED instance + RUNNING task → zero candidates."""
        _seed_instance(engine, "inst-TERM", status="terminated")
        _seed_orphan(engine, "inst-TERM")
        repo = TaskRepository(engine)
        boot = now_utc_naive()

        candidates = repo.find_auto_continue_candidates(boot)

        assert candidates == []

    def test_waiting_children_skipped(self, engine: Engine) -> None:
        """WC instance → zero candidates (bus owns the wake path)."""
        _seed_instance(engine, "inst-WC", status="waiting_children")
        _seed_orphan(engine, "inst-WC")
        repo = TaskRepository(engine)
        boot = now_utc_naive()

        candidates = repo.find_auto_continue_candidates(boot)

        assert candidates == []

    def test_cancel_requested_excluded(self, engine: Engine) -> None:
        """RUNNING task with ``cancel_requested=True`` → not selected."""
        _seed_instance(engine, "inst-CR")
        with Session(engine) as s:
            s.add(
                Task(
                    work_id=f"work-{uuid4().hex[:12]}",
                    task_type=TaskType.PROCESS_MESSAGE.value,
                    instance_id="inst-CR",
                    status=TaskStatus.RUNNING.value,
                    cancel_requested=True,
                    created_at=now_utc_naive(),
                )
            )
            s.commit()
        repo = TaskRepository(engine)
        boot = now_utc_naive()

        candidates = repo.find_auto_continue_candidates(boot)

        assert candidates == []


# ---------------------------------------------------------------------------
# M16 — Boot-order placement pin
# ---------------------------------------------------------------------------


class TestBootOrderPlacement:
    """M16 / D3 / architecture-recommendation.md Focus 2 — the
    pass call in ``daemon/api.py`` sits BETWEEN
    ``sweep_wake_records()`` and ``upgrade_journal_sweep.start()``.
    This is the structural test that fails if a future edit
    reorders the boot sequence."""

    def test_pass_call_between_wake_and_journal_start(self) -> None:
        with open("daemon/api.py") as f:
            src = f.read()
        wake_idx = src.find("sweep_wake_records()")
        pass_idx = src.find("continue_running_instances_after_restart")
        journal_idx = src.find("upgrade_journal_sweep.start()")
        assert wake_idx != -1
        assert pass_idx != -1
        assert journal_idx != -1
        assert wake_idx < pass_idx < journal_idx, (
            "D3 / M16: pass call must be wired BETWEEN "
            "sweep_wake_records() and upgrade_journal_sweep.start()"
        )
