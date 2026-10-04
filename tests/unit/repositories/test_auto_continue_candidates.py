"""Unit tests for the boot auto-continue candidate selection + CAS stamp.

Feature: auto-continue-running-after-restart. Phase 1 (P1 / D2 / D17 / D21).

These tests cover:

  M1 — CAS column schema (M-row 1): the ``task.auto_continued_at``
        column exists on the SQLModel, defaults to ``None``, and does
        not affect existing tests.
  M2 — Candidate selection (M-row 2): ``find_auto_continue_candidates``
        matches dormant RUNNING ``process_message`` / ``process_report``;
        excludes ``auto_continued_at >= boot_epoch``; excludes
        PAUSED/TERMINATED/COMPLETED/ERROR/FAILED instance rows (one
        test per excluded status, per D21); excludes WAITING_CHILDREN
        instance rows; excludes other task_types; deterministic
        ``created_at ASC`` order; read-only (no mutation).
  M3 — CAS stamp (M-row 3): ``mark_task_auto_continued`` first stamp
        ``True``; same-epoch restamp ``False``; older-epoch after
        newer ``False``; stamps nothing when row left running-set
        (``rowcount==1`` contract).
  M17 — PG/SQLite predicate parity (M-row 17): the comparison
        semantics are identical for in-memory SQLite; the
        ``< :boot_epoch`` re-arm predicate is exercised.

The repo method is read-only. The S3 PAUSED-doesn't-block invariant
on the claim-guard is pinned INDIRECTLY by the read-only
``find_auto_continue_candidates`` test (no row mutation = no widening
of the guard).

Harness: REAL file-backed SQLite (``tmp_path`` + ``NullPool`` —
deliberately NOT StaticPool/:memory:, mirrors production concurrency).
Pattern mirrors ``tests/unit/test_repository_claim_lane.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

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
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.timestamps import now_utc_naive


# ---------------------------------------------------------------------------
# Fixtures — file-backed SQLite (F9 parity pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """REAL SQLite FILE database, per-connection PRAGMAs, NullPool."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/auto_continue.db",
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
# Seeding helpers
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
    cancel_requested: bool = False,
    auto_continued_at: datetime | None = None,
    created_at: datetime | None = None,
    work_id: str | None = None,
) -> int:
    """Insert a Task row. Returns its primary key."""
    import uuid
    with Session(eng) as s:
        t = Task(
            id=task_id,
            work_id=work_id or f"work-{uuid.uuid4().hex[:12]}",
            task_type=task_type,
            instance_id=instance_id,
            status=status,
            cancel_requested=cancel_requested,
            auto_continued_at=auto_continued_at,
            created_at=created_at or now_utc_naive(),
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


# ---------------------------------------------------------------------------
# M1 — CAS column schema
# ---------------------------------------------------------------------------


class TestAutoContinuedAtColumn:
    """M1 — column presence, default, no impact on existing rows."""

    def test_field_present_on_task_model(self) -> None:
        """The SQLModel exposes ``auto_continued_at``."""
        assert "auto_continued_at" in Task.model_fields

    def test_field_default_is_none(self) -> None:
        """Default is ``None`` (never auto-continued)."""
        af = Task.model_fields["auto_continued_at"]
        assert af.default is None

    def test_field_nullable(self) -> None:
        """Annotation is ``datetime | None`` — NULL is allowed."""
        ann = Task.model_fields["auto_continued_at"].annotation
        # The annotation may be stringified under from __future__ import
        # annotations; we just need to confirm None is among the args.
        ann_str = str(ann)
        assert "None" in ann_str
        assert "datetime" in ann_str.lower()


# ---------------------------------------------------------------------------
# M2 — Candidate selection
# ---------------------------------------------------------------------------


def _boot_epoch() -> datetime:
    return now_utc_naive()


class TestFindAutoContinueCandidates:
    """M2 — selection predicate, full instance-status exclusion set, D21."""

    def test_matches_dormant_running_process_message(
        self, engine: Engine
    ) -> None:
        """A dormant RUNNING ``process_message`` Task is selected."""
        _seed_instance(engine, "inst-A")
        _seed_task(engine, "inst-A")
        repo = TaskRepository(engine)

        candidates = repo.find_auto_continue_candidates(_boot_epoch())

        assert len(candidates) == 1
        assert candidates[0].instance_id == "inst-A"
        assert candidates[0].task_type == TaskType.PROCESS_MESSAGE.value
        assert candidates[0].status == TaskStatus.RUNNING.value

    def test_matches_running_process_report(self, engine: Engine) -> None:
        """A RUNNING ``process_report`` Task is also selected (D7 sibling)."""
        _seed_instance(engine, "inst-PR")
        _seed_task(engine, "inst-PR", task_type=TaskType.PROCESS_REPORT.value)
        repo = TaskRepository(engine)

        candidates = repo.find_auto_continue_candidates(_boot_epoch())

        assert len(candidates) == 1
        assert candidates[0].task_type == TaskType.PROCESS_REPORT.value

    @pytest.mark.parametrize(
        "excluded_status",
        [
            InstanceStatus.PAUSED.value,
            InstanceStatus.TERMINATED.value,
            InstanceStatus.COMPLETED.value,
            InstanceStatus.ERROR.value,
            InstanceStatus.FAILED.value,
            InstanceStatus.WAITING_CHILDREN.value,
        ],
    )
    def test_excludes_tasks_for_excluded_instance_status(
        self, engine: Engine, excluded_status: str
    ) -> None:
        """One test per excluded instance status (D21 full exclusion set).

        PAUSED = resume_instance_cascade lane; terminal / WC = bus-owned.
        """
        _seed_instance(engine, "inst-X", status=excluded_status)
        _seed_task(engine, "inst-X")
        repo = TaskRepository(engine)

        candidates = repo.find_auto_continue_candidates(_boot_epoch())

        assert candidates == [], (
            f"Tasks for {excluded_status} instance must not be selected"
        )

    def test_excludes_other_task_types(self, engine: Engine) -> None:
        """``SEND_REPORT`` / ``CLEANUP`` are not active graph turns."""
        _seed_instance(engine, "inst-O")
        _seed_task(
            engine, "inst-O", task_type=TaskType.SEND_REPORT.value
        )
        _seed_task(
            engine, "inst-O", task_type=TaskType.CLEANUP.value
        )
        repo = TaskRepository(engine)

        candidates = repo.find_auto_continue_candidates(_boot_epoch())

        assert candidates == []

    def test_excludes_non_running_task_statuses(self, engine: Engine) -> None:
        """D7 / G3 — strict ``status='running'`` task predicate."""
        _seed_instance(engine, "inst-P")
        _seed_task(engine, "inst-P", status=TaskStatus.PENDING.value)
        _seed_task(engine, "inst-P", status=TaskStatus.PAUSED.value)
        _seed_task(engine, "inst-P", status=TaskStatus.COMPLETED.value)
        _seed_task(engine, "inst-P", status=TaskStatus.FAILED.value)
        _seed_task(engine, "inst-P", status=TaskStatus.CANCELLED.value)
        # And one RUNNING that should match.
        _seed_task(engine, "inst-P", status=TaskStatus.RUNNING.value)
        repo = TaskRepository(engine)

        candidates = repo.find_auto_continue_candidates(_boot_epoch())

        assert len(candidates) == 1
        assert candidates[0].status == TaskStatus.RUNNING.value

    def test_excludes_cancel_requested_tasks(self, engine: Engine) -> None:
        """D21 — ``cancel_requested=True`` excluded (mirror find_cancellable_tasks)."""
        _seed_instance(engine, "inst-CR")
        _seed_task(engine, "inst-CR", cancel_requested=True)
        # And one not cancel-requested that should match.
        _seed_task(engine, "inst-CR", cancel_requested=False)
        repo = TaskRepository(engine)

        candidates = repo.find_auto_continue_candidates(_boot_epoch())

        assert len(candidates) == 1
        assert candidates[0].cancel_requested is False

    def test_excludes_recently_stamped_orphan(self, engine: Engine) -> None:
        """An orphan stamped with ``auto_continued_at >= boot_epoch``
        is NOT re-selected (D17). The per-epoch re-arm only fires
        when the stamp is OLDER than the boot epoch."""
        # Boot epoch = "now"; stamp is in the past (< boot_epoch),
        # so this row IS still selected (re-arm: yes).
        _seed_instance(engine, "inst-OLD")
        past = now_utc_naive() - timedelta(minutes=30)
        _seed_task(engine, "inst-OLD", auto_continued_at=past)
        repo = TaskRepository(engine)
        boot = _boot_epoch()

        candidates = repo.find_auto_continue_candidates(boot)

        assert len(candidates) == 1
        # Now the stamp is at/after boot_epoch → excluded.
        future = boot + timedelta(seconds=1)
        _seed_task(
            engine, "inst-OLD2", auto_continued_at=future
        )
        candidates = repo.find_auto_continue_candidates(boot)
        assert all(
            c.auto_continued_at != future for c in candidates
        ), "Stamped-after-boot_epoch row must be excluded (D17 strict <)"

    def test_includes_never_stamped_orphan(self, engine: Engine) -> None:
        """``auto_continued_at IS NULL`` is selected (the D17 OR-arm)."""
        _seed_instance(engine, "inst-NS")
        _seed_task(engine, "inst-NS", auto_continued_at=None)
        repo = TaskRepository(engine)

        candidates = repo.find_auto_continue_candidates(_boot_epoch())

        assert len(candidates) == 1

    def test_deterministic_oldest_first_ordering(self, engine: Engine) -> None:
        """Returned in ``created_at ASC, id ASC`` order — oldest first."""
        _seed_instance(engine, "inst-MULTI")
        base = now_utc_naive() - timedelta(hours=1)
        # Insert with non-monotonic created_at to force a real order.
        for offset_minutes in [10, 5, 0]:  # intentionally out of order
            _seed_task(
                engine,
                "inst-MULTI",
                task_id=10 + offset_minutes,  # stable
                created_at=base + timedelta(minutes=offset_minutes),
            )

        repo = TaskRepository(engine)
        candidates = repo.find_auto_continue_candidates(_boot_epoch())

        assert len(candidates) == 3
        # Each candidate's created_at is >= the previous (oldest first).
        for prev, cur in zip(candidates, candidates[1:]):
            assert prev.created_at <= cur.created_at
        # And within the same created_at, id ASC is the tie-break.
        sorted_by = sorted(
            candidates, key=lambda t: (t.created_at, t.id)
        )
        assert [c.id for c in candidates] == [c.id for c in sorted_by]

    def test_read_only_no_row_mutation(self, engine: Engine) -> None:
        """The method is pure read — no row mutation, no instance status writes."""
        _seed_instance(engine, "inst-RO")
        _seed_task(engine, "inst-RO")
        repo = TaskRepository(engine)
        before_tasks = list(repo.find_auto_continue_candidates(_boot_epoch()))

        # Call twice; nothing should change.
        _ = repo.find_auto_continue_candidates(_boot_epoch())
        after_tasks = list(repo.find_auto_continue_candidates(_boot_epoch()))

        # The selection set is identical (no row added or removed).
        assert {t.id for t in before_tasks} == {t.id for t in after_tasks}
        # And no task was stamped by selection (auto_continued_at unchanged).
        assert all(
            t.auto_continued_at is None for t in after_tasks
        )

    def test_returns_empty_when_boot_epoch_is_none(self, engine: Engine) -> None:
        """Defensive — the boot pass SKIPs on ``boot_epoch=None``; the
        repo method, called outside the pass (e.g. test), returns empty."""
        _seed_instance(engine, "inst-NA")
        _seed_task(engine, "inst-NA")
        repo = TaskRepository(engine)

        candidates = repo.find_auto_continue_candidates(None)  # type: ignore[arg-type]

        assert candidates == []


# ---------------------------------------------------------------------------
# M3 — CAS stamp
# ---------------------------------------------------------------------------


class TestMarkTaskAutoContinued:
    """M3 — single-statement CAS, ``rowcount == 1`` contract, idempotency."""

    def test_first_stamp_returns_true(self, engine: Engine) -> None:
        """Stamping a never-stamped RUNNING row returns True."""
        _seed_instance(engine, "inst-FS")
        tid = _seed_task(engine, "inst-FS")
        repo = TaskRepository(engine)
        boot = _boot_epoch()

        ok = repo.mark_task_auto_continued(tid, boot)

        assert ok is True
        with Session(engine) as s:
            row = s.get(Task, tid)
            assert row.auto_continued_at == boot

    def test_same_epoch_restamp_returns_false(self, engine: Engine) -> None:
        """Stamping again with the same boot_epoch returns False
        (the ``< :boot_epoch`` strict arm declines)."""
        _seed_instance(engine, "inst-SR")
        tid = _seed_task(engine, "inst-FS")
        repo = TaskRepository(engine)
        boot = _boot_epoch()
        assert repo.mark_task_auto_continued(tid, boot) is True

        again = repo.mark_task_auto_continued(tid, boot)

        assert again is False

    def test_older_epoch_after_newer_returns_false(self, engine: Engine) -> None:
        """A re-stamp with an OLDER epoch after a newer stamp is False.

        The strict ``< :boot_epoch`` is the per-epoch re-arm: a NEWER
        boot's stamp wins, and a subsequent older-epoch stamp cannot
        overwrite. Stale-clock / backward-DB-step is fail-closed
        (architecture-recommendation.md Focus 1).
        """
        _seed_instance(engine, "inst-OR")
        tid = _seed_task(engine, "inst-OR")
        repo = TaskRepository(engine)
        newer = _boot_epoch()
        older = newer - timedelta(minutes=1)
        assert repo.mark_task_auto_continued(tid, newer) is True

        # Re-stamp with the OLDER epoch — must not overwrite.
        result = repo.mark_task_auto_continued(tid, older)

        assert result is False
        with Session(engine) as s:
            row = s.get(Task, tid)
            assert row.auto_continued_at == newer  # unchanged

    def test_newer_epoch_after_older_stamps(self, engine: Engine) -> None:
        """A re-stamp with a NEWER epoch is accepted (D17 re-arm)."""
        _seed_instance(engine, "inst-NR")
        tid = _seed_task(engine, "inst-NR")
        repo = TaskRepository(engine)
        older = _boot_epoch() - timedelta(hours=1)
        newer = _boot_epoch()
        assert repo.mark_task_auto_continued(tid, older) is True

        # Re-stamp with the NEWER epoch — accepted.
        result = repo.mark_task_auto_continued(tid, newer)

        assert result is True
        with Session(engine) as s:
            row = s.get(Task, tid)
            assert row.auto_continued_at == newer

    def test_does_not_stamp_when_status_left_running(
        self, engine: Engine
    ) -> None:
        """If the row was COMPLETED between schedule and stamp, the
        CAS ``WHERE status='running'`` guard declines — False,
        no mutation. (The 'mark task completed between schedule and
        stamp' race in the a′ semantics residual gap.)"""
        _seed_instance(engine, "inst-CMP")
        tid = _seed_task(engine, "inst-CMP", status=TaskStatus.RUNNING.value)
        repo = TaskRepository(engine)
        boot = _boot_epoch()

        # Externally transition the row to COMPLETED (simulating a
        # worker-pool completion that beat the stamp).
        with Session(engine) as s:
            row = s.get(Task, tid)
            row.status = TaskStatus.COMPLETED.value
            s.add(row)
            s.commit()

        ok = repo.mark_task_auto_continued(tid, boot)

        assert ok is False
        with Session(engine) as s:
            row = s.get(Task, tid)
            # Auto_continued_at is still NULL — the CAS declined.
            assert row.auto_continued_at is None

    def test_does_not_stamp_when_task_id_missing(self, engine: Engine) -> None:
        """Stamping a non-existent task_id returns False (rowcount=0)."""
        repo = TaskRepository(engine)

        ok = repo.mark_task_auto_continued(999_999, _boot_epoch())

        assert ok is False

    def test_does_not_stamp_with_none_boot_epoch(self, engine: Engine) -> None:
        """Defensive — ``boot_epoch=None`` returns False without mutation.

        The boot pass already SKIPS on ``boot_epoch=None``; the repo
        method is belt-and-suspenders.
        """
        _seed_instance(engine, "inst-NE")
        tid = _seed_task(engine, "inst-NE")
        repo = TaskRepository(engine)

        ok = repo.mark_task_auto_continued(tid, None)  # type: ignore[arg-type]

        assert ok is False
        with Session(engine) as s:
            row = s.get(Task, tid)
            assert row.auto_continued_at is None
