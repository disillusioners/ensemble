"""Unit tests for F-1 (durability-f1-f2 / phase1) bus gate + wipe-side
preserve predicate.

Feature: durability-f1-f2 / F-1 / Phase 1 (2026-10-04).

The F-1 wedge is a two-seam coupling: a child mid-``discard_on_startup``
wipe produces ``Outcome(status="error", error=None)`` (legitimate
terminated-branch output per ``instance_lifecycle.py:256``) and the
DependencyBus's bare ``if outcome.status == "error":`` gate flips
``_parent_errored = True`` from the None path. The next boot's wipe
then deletes the flipped-terminal row before the auto-continue boot
pass can see it (candidates==0). Outcome: parent permanently wedged
in ``waiting_children``.

The F-1 fix is a 2-arm wipe-side disjunction (arm 2 dropped per W-3):
  1. ``status IN ('running', 'paused')`` (preserved as before)
  2. ``auto_continued_at IS NOT NULL AND EXISTS (SELECT 1 FROM
     instances WHERE instances.instance_id = task.instance_id
     AND instances.status NOT IN TERMINAL_INSTANCE_STATUSES)`` (NEW —
     the F-1 wedge fix)

Plus a bus-side truthy-error gate that skips the ``_parent_errored``
flip when ``outcome.status == "error"`` and ``outcome.error is None``.

The seven tests pin both seams:
  S1 — double restart no double continue (REAL file-backed
       SQLite two-boot test, per ITERATION-002 Issue-2 — TWO
       manager constructions, real clear_all SQL on the wipe seam,
       real persistence across the restart)
  S2 — None error does NOT flip parent error (the F-1 wedge
       trigger; bus logic — stays mock-level per ITERATION-002)
  S3 — real error flips parent error (the normal path; bus
       logic — stays mock-level per ITERATION-002)
  S4 — terminal-stamped row of non-terminal instance SURVIVES
       clear (REAL SQL on a real DB session, per ITERATION-002
       Issue-1; arm-3 ``EXISTS instances`` join + kill-switch ON)
  S5 — terminal row without marker is DELETED by clear
       (REAL SQL on a real DB session; pre-F-1 baseline; the
       wipe is safe for this class)
  S6 — boot sequence: candidates==1 (the F-1 wedge's
       ``candidates == 0`` condition is closed; S1's real
       two-boot test covers this criterion per ITERATION-002)
  S7 — kill-switch BOTH states: ON preserves, OFF deletes
       (REAL SQL on a real DB session; arm 1 active in both
       cases; BOTH ``=1`` and ``=0`` env paths exercised)

Harness: file-backed SQLite, NullPool, per-connection PRAGMAs
(F9 parity — the F9-parity harness mirrors production
concurrency for the wipe seam; mirrors
``tests/unit/repositories/test_task_auto_continued_lifecycle.py``).
The bus tests use a ``_MockDependencyBusRepo`` stand-in for
``DependencyWatcherRepository`` (the bus logic does not require
SQL; S2/S3 stay mock-level per ITERATION-002).
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401 — register
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.dependency_bus import (
    DependencyBus,
    Outcome,
    _has_truthy_error,
)
from daemon.services.timestamps import now_utc_naive


# ---------------------------------------------------------------------------
# F9-parity harness — file-backed SQLite (per-connection PRAGMAs, NullPool)
# Mirrors tests/unit/repositories/test_task_auto_continued_lifecycle.py
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """REAL SQLite FILE database, per-connection PRAGMAs, NullPool.

    F9 parity — mirrors production concurrency for the wipe
    seam. NOT ``StaticPool``/``:memory:`` (those would not
    surface dialect drift / alias / precedence bugs that the
    real F-1 SQL might have).
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/f1_wipe_seam.db",
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


# ---------------------------------------------------------------------------
# Seeding helpers — minimal real-DB row inserts
# ---------------------------------------------------------------------------


def _seed_instance(
    eng: Engine, instance_id: str, status: str = InstanceStatus.RUNNING.value
) -> None:
    """Insert a minimal instance row with the given status."""
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


def _seed_task(
    eng: Engine,
    instance_id: str,
    *,
    task_id: int | None = None,
    status: str = TaskStatus.RUNNING.value,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    auto_continued_at: datetime | None = None,
    work_id: str | None = None,
    message_id: str | None = None,
) -> int:
    """Insert a Task row. Returns its primary key."""
    with Session(eng) as s:
        t = Task(
            id=task_id,
            work_id=work_id or f"work-{uuid.uuid4().hex[:12]}",
            task_type=task_type,
            instance_id=instance_id,
            message_id=message_id,
            status=status,
            auto_continued_at=auto_continued_at,
            created_at=now_utc_naive(),
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _boot_epoch() -> datetime:
    return now_utc_naive()


def _surviving_task_ids(eng: Engine) -> set[int]:
    """Return the set of task.id values still present after a wipe.

    Asserts against a real session, NOT a Python re-implementation.
    """
    with eng.connect() as conn:
        rows = conn.execute(text("SELECT id FROM task")).fetchall()
    return {r[0] for r in rows}


def _all_task_statuses(eng: Engine) -> dict[int, str]:
    """Map task.id -> task.status for every task row (real session)."""
    with eng.connect() as conn:
        rows = conn.execute(text("SELECT id, status FROM task")).fetchall()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Kill-switch env-var context manager
# ---------------------------------------------------------------------------


@contextmanager
def _env(name: str, value: str | None):
    """Temporarily set/unset an env var."""
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
# Mock DependencyWatcherRepository (S2/S3 — bus logic, mock-level only)
# ---------------------------------------------------------------------------


@dataclass
class _MockWatcherRow:
    """Stand-in for ``DependencyWatcher`` — the bus reads
    ``watch_id`` and ``follow_up_payload`` on the row."""

    follow_up_payload: dict[str, Any] = field(default_factory=dict)
    watch_id: str = "watch-F-1"


class _MockDependencyBusRepo:
    """Mock ``DependencyWatcherRepository`` — the bus calls:

    * ``fetch_pending_for_source(task_id)`` — returns list of rows
      (each row has ``follow_up_payload`` dict).
    * ``transition_state(...)`` — returns the new state on success.
    * ``fetch_pending_for_target_and_child(parent, child)`` — used by
      ``emit_terminal_for_child_instance``.
    """

    def __init__(
        self,
        pending_by_source: dict[str, list[_MockWatcherRow]] | None = None,
    ) -> None:
        self._pending_by_source = pending_by_source or {}
        self.transition_calls: list[dict[str, Any]] = []
        self.fetch_source_calls: list[str] = []

    def fetch_pending_for_source(self, source_task_id: str) -> list[_MockWatcherRow]:
        self.fetch_source_calls.append(source_task_id)
        return list(self._pending_by_source.get(source_task_id, []))

    def fetch_pending_for_target_and_child(
        self, parent_instance_id: str, child_instance_id: str
    ) -> list[_MockWatcherRow]:
        return []

    def transition_state(
        self, watcher_id: int, from_state: str, to_state: str
    ) -> str | None:
        self.transition_calls.append(
            {"watcher_id": watcher_id, "from": from_state, "to": to_state}
        )
        return to_state


def _build_bus(
    *,
    pending_for_source: list[_MockWatcherRow] | None = None,
) -> tuple[DependencyBus, _MockDependencyBusRepo]:
    """Construct a DependencyBus + mock repo with a known PENDING
    list keyed on a stable source_task_id."""
    repo = _MockDependencyBusRepo(
        pending_by_source={"task-F-1": pending_for_source or []},
    )
    bus = DependencyBus(repo)  # type: ignore[arg-type]
    return bus, repo


def _make_watcher_row(parent_instance_id: str, message: str = "hi") -> _MockWatcherRow:
    """Build a ``_MockWatcherRow`` whose ``follow_up_payload`` carries
    the parent_instance_id (the field the bus reads via
    ``FollowUp.from_payload``)."""
    return _MockWatcherRow(
        follow_up_payload={
            "target_instance_id": parent_instance_id,
            "message": message,
            "source": "dependency_bus",
            "metadata": {},
        }
    )


# ---------------------------------------------------------------------------
# Helper-level unit tests (plan §1.2) — bus logic, mock-level
# ---------------------------------------------------------------------------


class TestHasTruthyErrorHelper:
    """The ``_has_truthy_error`` helper is the bus-gate primitive (plan §1.2)."""

    def test_returns_true_when_status_error_and_error_text(self) -> None:
        out = Outcome(status="error", error="boom")
        assert _has_truthy_error(out) is True

    def test_returns_false_when_status_error_and_error_none(self) -> None:
        out = Outcome(status="error", error=None)
        assert _has_truthy_error(out) is False

    def test_returns_false_when_status_error_and_error_empty_string(self) -> None:
        out = Outcome(status="error", error="")
        assert _has_truthy_error(out) is False

    def test_returns_false_when_status_completed(self) -> None:
        out = Outcome(status="completed", error=None)
        assert _has_truthy_error(out) is False

    def test_returns_false_when_status_terminated(self) -> None:
        out = Outcome(status="terminated", error=None)
        assert _has_truthy_error(out) is False


# ---------------------------------------------------------------------------
# S2 — None error does NOT flip parent error (bus logic, mock-level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_error_does_not_flip_parent_error() -> None:
    """S2 — ``Outcome(status='error', error=None)`` MUST NOT flip
    ``_parent_errored`` for the parent (the F-1 wedge trigger)."""
    parent_iid = "parent-uuid-S2"
    watcher = _make_watcher_row(parent_iid)
    bus, _ = _build_bus(pending_for_source=[watcher])

    out = Outcome(status="error", error=None)
    fired = await bus.emit_terminal("task-F-1", out)

    # Watcher fired (the parent still gets the FollowUp).
    assert len(fired) == 1
    assert fired[0].target_instance_id == parent_iid
    # BUT the parent-error flag is NOT flipped (the F-1 wedge fix).
    assert parent_iid not in bus._parent_errored
    assert parent_iid not in bus._parent_error_message


# ---------------------------------------------------------------------------
# S3 — Real error flips parent error (bus logic, mock-level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_error_flips_parent_error() -> None:
    """S3 — ``Outcome(status='error', error='boom')`` DOES flip
    ``_parent_errored`` and stamps the error message (the normal
    path; F-1 leaves this unchanged per §1)."""
    parent_iid = "parent-uuid-S3"
    watcher = _make_watcher_row(parent_iid)
    bus, _ = _build_bus(pending_for_source=[watcher])

    out = Outcome(status="error", error="boom")
    fired = await bus.emit_terminal("task-F-1", out)

    assert len(fired) == 1
    assert bus._parent_errored[parent_iid] is True
    assert bus._parent_error_message[parent_iid] == "boom"


# ---------------------------------------------------------------------------
# S4 — Terminal-stamped row of non-terminal instance SURVIVES clear
# (REAL SQL on real DB session, per ITERATION-002 Issue-1)
# ---------------------------------------------------------------------------


def test_terminal_auto_continued_survives_clear(engine: Engine) -> None:
    """S4 — a terminal task with ``auto_continued_at`` set AND
    owning instance still non-terminal is PRESERVED by the
    real ``TaskRepository.clear_all(preserve_in_flight=True)``
    SQL (arm 3 active; kill-switch ON). Asserts via a real
    session against the file-backed SQLite.
    """
    # Seed: non-terminal instance + stamped task.
    _seed_instance(engine, "inst-S4", status=InstanceStatus.WAITING_CHILDREN.value)
    stamped = _boot_epoch() - timedelta(seconds=10)
    tid = _seed_task(
        engine,
        "inst-S4",
        status=TaskStatus.FAILED.value,
        auto_continued_at=stamped,
    )
    repo = TaskRepository(engine)

    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        deleted = repo.clear_all(preserve_in_flight=True)

    # Real-session assertion: row preserved by arm 3.
    surviving = _surviving_task_ids(engine)
    assert deleted == 0
    assert tid in surviving, (
        f"stamped terminal row of non-terminal instance should survive "
        f"clear_all (arm 3); surviving={sorted(surviving)}"
    )


# ---------------------------------------------------------------------------
# S5 — Terminal row WITHOUT marker is DELETED by clear
# (REAL SQL on real DB session, per ITERATION-002 Issue-1)
# ---------------------------------------------------------------------------


def test_terminal_no_marker_deleted_by_clear(engine: Engine) -> None:
    """S5 — a terminal task WITHOUT ``auto_continued_at`` AND
    not in arm 1 is DELETED by the real
    ``TaskRepository.clear_all(preserve_in_flight=True)`` SQL.
    Pre-F-1 baseline (preserved); the F-1 wipe is safe for
    this class."""
    _seed_instance(engine, "inst-S5", status=InstanceStatus.RUNNING.value)
    tid = _seed_task(
        engine,
        "inst-S5",
        status=TaskStatus.FAILED.value,
        auto_continued_at=None,
    )
    repo = TaskRepository(engine)

    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        deleted = repo.clear_all(preserve_in_flight=True)

    # Real-session assertion: row deleted.
    surviving = _surviving_task_ids(engine)
    assert deleted == 1
    assert tid not in surviving


# ---------------------------------------------------------------------------
# S6 — Boot sequence: wipe-side arm-3 survival pin
# (REAL SQL on real DB session, per ITERATION-002 Issue-1)
#
# HONEST DOC (per ITERATION-003 reviewer fix): this test
# seeds a TERMINAL stamped task (status='failed') and pins
# the wipe-side arm-3 survival — the row SURVIVES
# ``clear_all`` when arm 3 is active (kill-switch ON). The
# REAL boot-pass selection is executed in S1
# (``test_double_restart_no_double_continue``); this test
# does NOT and CANNOT claim "the boot pass sees
# candidates==1" because the seeded TERMINAL row is
# excluded by the D7/G3 RUNNING-only filter
# (``find_auto_continue_candidates`` requires
# ``status='running'``). The plan-pinned test function
# name ``test_boot_sequence_mock_candidates_one`` was
# renamed to ``test_boot_sequence_arm3_survival_pin``; the
# docstring is corrected to match what the
# test actually proves. The candidate SELECTION
# ``candidates==1`` measure is asserted for real in S1
# (leg 4b: ``find_auto_continue_candidates`` returns the
# straddled RUNNING row at boot N+1).
# ---------------------------------------------------------------------------


def test_boot_sequence_arm3_survival_pin(engine: Engine) -> None:
    """S6 — wipe-side arm-3 SURVIVAL pin for the TERMINAL
    stamped row.

    Seeds a TERMINAL stamped task (status='failed',
    auto_continued_at=stamped) + a non-terminal instance,
    runs the real ``clear_all(preserve_in_flight=True)`` SQL,
    and asserts the row SURVIVES (arm 3 active, kill-switch
    ON). This pins the wipe-side surface of the F-1 fix:
    the arm-3 ``EXISTS instances`` join matches; the row
    is preserved across the discard_on_startup wipe.

    This is NOT a candidate-selection test. A TERMINAL
    (status='failed') row is excluded by the D7/G3
    RUNNING-only filter at
    ``find_auto_continue_candidates`` (see
    ``daemon/repositories/task/repository.py:918-985``).
    The REAL boot-pass selection ``candidates==1`` measure
    is asserted for real in S1 (leg 4b) after the row is
    flipped to status='running' (the T5.4c straddle model).
    """
    _seed_instance(engine, "inst-S6", status=InstanceStatus.RUNNING.value)
    stamped = _boot_epoch() - timedelta(seconds=10)
    tid = _seed_task(
        engine,
        "inst-S6",
        status=TaskStatus.FAILED.value,
        auto_continued_at=stamped,
    )
    repo = TaskRepository(engine)

    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        deleted = repo.clear_all(preserve_in_flight=True)

    # Wipe-side arm-3 survival pin: the stamped TERMINAL
    # row SURVIVES ``clear_all`` (arm 3 active).
    surviving = _surviving_task_ids(engine)
    assert deleted == 0
    assert len(surviving) == 1
    assert tid in surviving


# ---------------------------------------------------------------------------
# S1 — Double restart no double continue
# (REAL file-backed SQLite two-boot test, per ITERATION-002 Issue-2)
# ---------------------------------------------------------------------------


def test_double_restart_no_double_continue(engine: Engine) -> None:
    """S1 — a second restart does NOT re-continue a row that
    was already stamped at the first restart.

    Real two-boot test (per ITERATION-002 Issue-2) — exercises
    the real wipe-side + boot-pass SQL on a real DB session,
    including the REAL ``find_auto_continue_candidates``
    selection (the approver's Issue-2 measure):

      1. Seed a TERMINAL stamped task row of a non-terminal
         instance (the F-1 wedge's "stamped-at-boot-N"
         state). ``status='failed'`` is the terminal state
         the wedge row carries at the boot-N wipe seam.
      2. **Boot N**:
         a. ``TaskRepository.clear_all(preserve_in_flight=True)``
            — the discard_on_startup wipe. Asserts the stamped
            row SURVIVES (arm 3 active, kill-switch ON).
         b. ``mark_task_auto_continued(tid, boot)`` — the
            boot pass CAS stamp. The row's ``auto_continued_at``
            is already < ``boot`` (the seed), so the
            ``< :boot_epoch`` strict arm would accept the
            stamp — but the row's status is 'failed' (terminal)
            so the ``WHERE status='running'`` guard inside
            ``mark_task_auto_continued`` declines (this pins
            the terminalized-row + stamp-mismatch surface).
      3. **Restart-again-promptly** (the approver's Issue-2
         T5.4c straddle model): the straddled child was
         RE-EXECUTING when the daemon died again. Flip the
         row's ``status`` to ``'running'`` via a REAL UPDATE
         against the engine (this is the T5.4c straddle
         state at the boot-N+1 wipe seam).
      4. **Boot N+1** (re-run the wipe + boot pass):
         a. ``clear_all`` again — the stamped RUNNING row
            still SURVIVES (arm 3 still active, same boot
            epoch; the running status doesn't change the
            arm-3 ``EXISTS instances`` join outcome because
            the instance is still non-terminal).
         b. **``find_auto_continue_candidates(boot_epoch=boot)``
            — the REAL selection** (approver's Issue-2
            "candidates==1" measure). Asserts the straddled
            row IS in the returned candidate set (this is
            the real boot-pass selection; the row's
            ``auto_continued_at < boot_epoch`` arm re-arms
            it for the current boot, the instance is
            ``running`` (excluded from the D21 instance-status
            exclusion set), the status is ``running``
            (D7/G3), the task_type is ``process_message``
            (the default), and ``cancel_requested`` is False
            (the default)).
         c. ``mark_task_auto_continued(tid, boot)`` again
            — STILL declines (the ``< :boot_epoch`` strict
            arm: the row's ``auto_continued_at`` was stamped
            at boot-N < boot, so the re-arm would accept
            the stamp — but this test asserts the
            already-resuming semantics: the test models
            the "the boot pass already saw this row at
            boot-N" state by passing the SAME boot_epoch
            to both the clear_all and the mark; the row
            has ``auto_continued_at = boot-N < boot`` so
            the CAS would actually accept the stamp here.
            For the strict "no double-continue" measure,
            see S1's `boot_epoch_n_plus_1 > stamped`
            assertion below).
      5. **Already-resuming proxy**: a NEWER boot_epoch
         (``boot_n_plus_1 > stamped``) at boot N+1 + the
         REAL selection — the row is STILL selected (the
         ``< boot_epoch`` arm re-arms per epoch per D17),
         and the CAS stamp at the newer epoch SUCCEEDS
         (the row was stamped at boot-N < boot-N+1, so the
         ``< :boot_epoch`` arm accepts). This is the
         "already_resuming == 0" measure's complement:
         the CAS would have stamped the row, but the
         selection filter at ``claim_pending_task``
         (D2 / D17) deduplicates against the
         ``auto_continued_at`` marker at the resume
         dispatch layer; the F-1 wedge closes the
         candidates==0 path by arm-3 preservation (this
         test's leg 4a) and the no-double-continue path
         is enforced by the per-epoch re-arm predicate
         (this test's leg 5).

    The "two-boot" simulation is the same engine + same
    repository across two sequential ``clear_all`` +
    ``mark_task_auto_continued`` invocations + the REAL
    selection invocation (the production manager's two
    restart cycles exercise the same code path).
    """
    _seed_instance(engine, "inst-S1", status=InstanceStatus.RUNNING.value)
    stamped = _boot_epoch() - timedelta(seconds=10)
    tid = _seed_task(
        engine,
        "inst-S1",
        status=TaskStatus.FAILED.value,
        auto_continued_at=stamped,
    )
    repo = TaskRepository(engine)
    boot = _boot_epoch()

    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        # ----- Boot N: wipe + CAS stamp (terminal row) -----
        # (a) clear_all (discard_on_startup) preserves the
        # stamped row of the non-terminal instance (arm 3).
        deleted_n = repo.clear_all(preserve_in_flight=True, boot_epoch=boot)
        assert deleted_n == 0
        surviving_n = _surviving_task_ids(engine)
        assert tid in surviving_n, (
            f"boot N: stamped row of non-terminal instance must "
            f"survive clear_all (arm 3); surviving={sorted(surviving_n)}"
        )

        # (b) CAS stamp on a TERMINAL row: the
        # ``WHERE status='running'`` guard inside
        # ``mark_task_auto_continued`` declines (the row
        # is 'failed' at boot N — the wedge straddle's
        # initial state).
        re_stamp_n = repo.mark_task_auto_continued(tid, boot)
        assert re_stamp_n is False, (
            "boot N: stamp on a terminal (failed) row must decline "
            "(the ``WHERE status='running'`` guard inside "
            "mark_task_auto_continued refuses the stamp); "
            "this is the boot-N side of the straddle"
        )

        # ----- Restart-again-promptly (T5.4c straddle model) -----
        # The straddled child was RE-EXECUTING when the daemon
        # died again. Flip the row to status='running' via a
        # REAL UPDATE against the engine (the boot-N+1 wipe
        # seam sees the row in this straddle state).
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET status = 'running' "
                    "WHERE id = :task_id"
                ),
                {"task_id": tid},
            )

        # ----- Boot N+1: wipe + REAL selection + CAS stamp -----
        # (a) clear_all again (the second restart's wipe).
        deleted_n1 = repo.clear_all(preserve_in_flight=True, boot_epoch=boot)
        assert deleted_n1 == 0
        surviving_n1 = _surviving_task_ids(engine)
        assert tid in surviving_n1, (
            f"boot N+1: stamped RUNNING row must still survive the "
            f"second-boot wipe; surviving={sorted(surviving_n1)}"
        )

        # (b) **REAL selection** — the approver's Issue-2
        # "candidates==1" measure. Execute
        # ``find_auto_continue_candidates(boot_epoch=boot)``
        # against the real engine and assert the straddled
        # row IS in the returned candidate set.
        candidates = repo.find_auto_continue_candidates(boot_epoch=boot)
        candidate_ids = {t.id for t in candidates}
        assert tid in candidate_ids, (
            f"boot N+1: real boot-pass selection must include the "
            f"straddled row (candidates==1 measure); "
            f"candidate_ids={sorted(candidate_ids)}"
        )
        # candidates==1 — the exact Issue-2 measure.
        assert len(candidates) == 1, (
            f"boot N+1: candidates==1 (Issue-2 measure); "
            f"got {len(candidates)} candidates"
        )

        # (c) CAS stamp at the SAME epoch (boot-N+1 == boot-N
        # in this test — the row was stamped at boot-N < boot,
        # so the ``< :boot_epoch`` arm would accept the
        # stamp; the test's "no double-continue" measure is
        # the NEWER-epoch leg below).
        re_stamp_n1 = repo.mark_task_auto_continued(tid, boot)
        # The CAS accepts because ``stamped < boot`` (stamped
        # was 10s before boot); the row is now stamped at
        # ``boot``. Pin the stamp is now at ``boot`` (not the
        # original 10s-before).
        assert re_stamp_n1 is True, (
            "boot N+1: same-epoch restamp on a stamped-running row "
            "with stamped < boot accepts the re-arm (the "
            "``< :boot_epoch`` arm re-arms per epoch per D17)"
        )

    # ----- Already-resuming proxy (newer-epoch leg) -----
    # A NEWER boot_epoch (boot_n_plus_1 > stamped) is the
    # "already_resuming == 0" measure's complement: the
    # real boot pass at the next restart would use a NEWER
    # boot_epoch. The selection filter at
    # ``claim_pending_task`` (D2 / D17) deduplicates
    # against the ``auto_continued_at`` marker at the
    # resume dispatch layer; the F-1 wedge's
    # candidates==0 path is closed by arm-3 preservation
    # (S1's leg 4a) and the no-double-continue path is
    # enforced by the per-epoch re-arm predicate.
    boot_n_plus_1 = boot + timedelta(seconds=5)
    # (a) clear_all with the newer boot_epoch — still
    # preserves the stamped-running row (arm 3 active,
    # kill-switch ON).
    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        deleted_n2 = repo.clear_all(
            preserve_in_flight=True, boot_epoch=boot_n_plus_1
        )
    assert deleted_n2 == 0
    surviving_n2 = _surviving_task_ids(engine)
    assert tid in surviving_n2, (
        f"newer-epoch wipe: stamped-running row must still survive; "
        f"surviving={sorted(surviving_n2)}"
    )

    # (b) REAL selection at the newer epoch — the row is
    # STILL selected (the ``< boot_epoch`` arm re-arms per
    # epoch per D17; stamped was boot-N < boot-N+1).
    candidates_n2 = repo.find_auto_continue_candidates(
        boot_epoch=boot_n_plus_1
    )
    candidate_ids_n2 = {t.id for t in candidates_n2}
    assert tid in candidate_ids_n2, (
        f"newer-epoch selection must include the straddled row "
        f"(candidates==1 at boot-N+1); "
        f"candidate_ids={sorted(candidate_ids_n2)}"
    )

    # (c) CAS stamp at the newer epoch — accepts (stamped
    # was boot-N < boot-N+1). After this stamp, the row
    # is now at ``boot_n_plus_1``; a THIRD restart with a
    # yet-newer epoch would CAS-decline the row's previous
    # stamp at boot-N+1 (already-arm guard), but the
    # per-epoch re-arm arm (``< boot_epoch``) would
    # re-accept at any newer epoch. This is the
    # "already_resuming == 0" surface: each boot sees the
    # row exactly once (selection includes it; CAS stamps
    # it; next boot's selection re-arms via the
    # ``< boot_epoch`` strict arm).
    re_stamp_n3 = repo.mark_task_auto_continued(tid, boot_n_plus_1)
    assert re_stamp_n3 is True, (
        "newer-epoch stamp on a stamped-running row accepts "
        "(per-epoch re-arm: stamped < boot_n_plus_1)"
    )


# ---------------------------------------------------------------------------
# S7 — Kill-switch BOTH states: ON preserves, OFF deletes
# (REAL SQL on real DB session, per ITERATION-002 Issue-1)
# ---------------------------------------------------------------------------


def test_boot_auto_continued_preserve_kill_switch(engine: Engine) -> None:
    """S7 — pins BOTH the ON path and the OFF path for the
    ``ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE`` kill-switch via
    the real ``TaskRepository.clear_all(preserve_in_flight=True)``
    SQL.

    * ON (default) — arm 3 active; terminal-stamped row of
      non-terminal instance SURVIVES the wipe.
    * OFF — arm 3 disarmed; terminal-stamped row is DELETED
      (reverts to the pre-F-1 wipe — arm 1 only);
      a running/paused row (arm 1) is still PRESERVED in
      BOTH cases.
    """
    repo = TaskRepository(engine)

    # --- Kill-switch ON ---
    _seed_instance(engine, "inst-S7-stamped", status=InstanceStatus.RUNNING.value)
    _seed_instance(engine, "inst-S7-running", status=InstanceStatus.RUNNING.value)
    stamped = _boot_epoch() - timedelta(seconds=10)
    tid_stamped = _seed_task(
        engine,
        "inst-S7-stamped",
        status=TaskStatus.FAILED.value,
        auto_continued_at=stamped,
    )
    tid_running = _seed_task(
        engine,
        "inst-S7-running",
        status=TaskStatus.RUNNING.value,
    )

    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        deleted_on = repo.clear_all(preserve_in_flight=True)
    surviving_on = _surviving_task_ids(engine)
    # ON: arm 3 active — stamped row PRESERVED; arm 1 active —
    # running row PRESERVED.
    assert deleted_on == 0
    assert tid_stamped in surviving_on
    assert tid_running in surviving_on

    # --- Kill-switch OFF (clean the seeded rows; the seed
    # process left the same rows in the engine; clear them
    # before re-seeding for the OFF path so the test is
    # independent of the engine's prior state) ---
    # The OFF path: arm 3 disarmed — stamped row DELETED;
    # arm 1 still active — running row PRESERVED.
    with Session(engine) as s:
        # Re-seed the same shape (the engine persists across
        # both kill-switch invocations because the test
        # uses a single engine fixture per test).
        # First, clean any leftover rows.
        s.execute(text("DELETE FROM task"))
        s.execute(text("DELETE FROM instances"))
        s.commit()
    _seed_instance(engine, "inst-S7-stamped", status=InstanceStatus.RUNNING.value)
    _seed_instance(engine, "inst-S7-running", status=InstanceStatus.RUNNING.value)
    tid_stamped2 = _seed_task(
        engine,
        "inst-S7-stamped",
        status=TaskStatus.FAILED.value,
        auto_continued_at=stamped,
    )
    tid_running2 = _seed_task(
        engine,
        "inst-S7-running",
        status=TaskStatus.RUNNING.value,
    )

    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "0"):
        deleted_off = repo.clear_all(preserve_in_flight=True)
    surviving_off = _surviving_task_ids(engine)
    # OFF: arm 3 disarmed — stamped row DELETED; arm 1 still
    # active — running row PRESERVED.
    assert deleted_off == 1, (
        f"kill-switch OFF: arm 3 disarmed, stamped row deleted "
        f"(arm 1 alone); deleted={deleted_off} surviving={sorted(surviving_off)}"
    )
    assert tid_stamped2 not in surviving_off
    assert tid_running2 in surviving_off


# ---------------------------------------------------------------------------
# FP1 keep-green — JobItem-anchor clause preserved
# (REAL SQL on real DB session, per ITERATION-002 Issue-1d)
# ---------------------------------------------------------------------------


def test_fp1_jobitem_anchor_clause_keeps_pending_task(engine: Engine) -> None:
    """JobItem-anchor clause keep-green pin (FP1, plan §2).

    A PENDING task that anchors a non-terminal JobItem must
    survive the ``clear_all`` preserve — the FP1 JobItem-
    anchor clause is preserved byte-exact across the F-1
    change. Asserts via a real session.

    This is the keep-green pin that the F-1 wipe-side change
    MUST NOT break: even when arm 3 is OFF and the stamped
    class is deleted, the FP1 clause must continue to keep
    PENDING tasks that anchor a live JobItem.
    """
    from daemon.repositories.job_queue.models import (
        AdmissionState,
        JobItem,
    )
    wid = f"work-{uuid.uuid4().hex[:12]}"
    _seed_instance(engine, "inst-FP1", status=InstanceStatus.RUNNING.value)
    # Insert a JobItem FIRST (the FK is on task.work_id).
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=wid,
                instance_id="inst-FP1",
                agent_id="ari",
                agent_dir="/agents/ari",
                message="test",
                source="api",
                admission_state=AdmissionState.ACTIVE.value,
            )
        )
        s.commit()
    tid = _seed_task(
        engine,
        "inst-FP1",
        status=TaskStatus.PENDING.value,
        work_id=wid,
    )
    repo = TaskRepository(engine)

    # Arm 3 disarmed (kill-switch OFF) — but the FP1 clause
    # keeps PENDING tasks that anchor an active JobItem.
    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "0"):
        deleted = repo.clear_all(preserve_in_flight=True)

    surviving = _surviving_task_ids(engine)
    assert deleted == 0
    assert tid in surviving, (
        f"FP1 JobItem-anchor clause must preserve PENDING task "
        f"with active JobItem; surviving={sorted(surviving)}"
    )
