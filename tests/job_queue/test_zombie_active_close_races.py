"""Pack D — race + ordering (wi-78967dba).

Pins (phase3-plan T3.4): two concurrent actors on one candidate ⇒
exactly ONE finalize; the loser no-ops INCLUDING the lock side
(``race_lost_noop``); the same-transaction finalize→lock-release
ordering leaves no observable "job done ∧ lock present" state for a
close driven through the seam; the guarded predicate loses cleanly
against a pre-flipped row (operator-shape).

Real guarded seams only (repository methods), never mocked guards —
the race proof is about the SQL predicate.
"""

from __future__ import annotations

import asyncio
import threading
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.job_queue.repository import JobRepository
from daemon.services.dependency_bus import set_dependency_bus

from tests.job_queue.zombie_close_helpers import (
    insert_zombie_fixture,
    make_bus_mock,
    make_recovery_service,
    read_job_state,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 4


@pytest.fixture
def rc_engine(tmp_path):
    """FILE-backed SQLite (concurrent writers need real
    cross-connection locking — the StaticPool single-connection
    harness serializes and cannot exercise races)."""
    engine = create_engine(
        f"sqlite:///{tmp_path}/zc-races.db",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def rc_bus():
    bus = make_bus_mock(pending=[])
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


class TestConcurrentActors:
    async def test_two_seam_callers_one_winner(self, rc_engine, rc_bus):
        """Two threads drive ``force_finalize_orphan`` on the same
        job (the h-leg shape vs an f2-shaped actor): exactly ONE
        wins (rowcount guard), the loser returns None and —
        critically — performs NO lock-side action of its own (the
        winner's same-txn DELETE already removed the row; the loser
        cannot delete it again)."""
        ref = insert_zombie_fixture(
            rc_engine, verdict="completed", age_hours=3.0
        )
        repo = JobRepository(rc_engine)
        results: list = []
        barrier = threading.Barrier(2)

        def _actor():
            barrier.wait()  # maximize the race window overlap
            r = repo.force_finalize_orphan(
                ref["job_id"],
                "zombie_active_completed",
                True,
            )
            results.append(r)

        t1 = threading.Thread(target=_actor)
        t2 = threading.Thread(target=_actor)
        t1.start(); t2.start(); t1.join(); t2.join()
        winners = [r for r in results if r is not None]
        assert len(winners) == 1, (
            f"exactly ONE finalize may win; got {len(winners)} "
            f"winners of {len(results)} actors"
        )
        st = read_job_state(rc_engine, ref["job_id"])
        assert st["admission_state"] == "done", st
        assert st["lock_rows"] == 0, st

    async def test_losers_counted_as_race_lost_noop(self, rc_engine, rc_bus):
        """Seam-level contract (tidier fold 2026-10-31, renamed to
        what it pins): a SECOND ``force_finalize_orphan`` caller on
        an already-terminal row no-ops (returns None) with no
        lock-side action. The drift-level COUNTER assertion lives in
        ``test_h_leg_vs_preemptive_finalize_race_lost_noop`` (the
        full reconcile_drift_states drive)."""
        ref = insert_zombie_fixture(
            rc_engine, verdict="completed", age_hours=3.0
        )
        # The "operator" terminalizes first.
        JobRepository(rc_engine).force_finalize_orphan(
            ref["job_id"], "completed", False
        )
        # Re-arm an ACTIVE row so the (h) leg sees a candidate but
        # its UPDATE still loses… the guarded predicate is
        # admission_state='active' — a done row is NOT a candidate.
        # Instead: flip the row ACTIVE, let the (h) leg run while a
        # concurrent finalize flips it mid-chain — simulated by
        # pre-finalizing AFTER candidacy via a probe patch is
        # over-engineering; the direct seam probe below pins the
        # loser-no-op contract on the SECOND caller.
        repo = JobRepository(rc_engine)
        loser = repo.force_finalize_orphan(
            ref["job_id"], "zombie_active_completed", True
        )
        assert loser is None, (
            "a second caller on a terminal row must no-op (None), "
            "performing NO lock-side action"
        )
        st = read_job_state(rc_engine, ref["job_id"])
        assert st["admission_state"] == "done", st

    async def test_h_leg_vs_preemptive_finalize_race_lost_noop(
        self, rc_engine, rc_bus
    ):
        """The full (h) leg runs against a candidate whose row is
        terminalized by a concurrent actor BETWEEN the candidate
        query and the write: the seam's guarded UPDATE loses ⇒ the
        leg records race_lost_noop, emits NO zombie token, leaves
        the other actor's terminal_reason intact."""
        ref = insert_zombie_fixture(
            rc_engine, verdict="completed", age_hours=3.0
        )
        service = make_recovery_service(rc_engine)
        orig = JobRepository.force_finalize_orphan
        repo = JobRepository(rc_engine)

        def _preemptive(self_repo, jid, *args, **kwargs):
            # The "concurrent actor" wins FIRST (plain f2-shaped
            # finalize without recheck), then the (h) call proceeds
            # and must lose the guarded UPDATE. The (h) leg passes
            # (job_id, token, True) positionally — token is args[0].
            if args and str(args[0]).startswith("zombie_active_"):
                repo.force_finalize_orphan(jid, "completed", False)
            return orig(self_repo, jid, *args, **kwargs)

        import unittest.mock as _mock

        with _mock.patch.object(
            JobRepository, "force_finalize_orphan", _preemptive
        ):
            stats = await (
                service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )
            )
        st = read_job_state(rc_engine, ref["job_id"])
        assert st["admission_state"] == "done", st
        assert st["terminal_reason"] == "completed", (
            "the concurrent actor's reason must survive; the (h) "
            f"leg must not overwrite it: {st}"
        )
        zc = stats.get("zombie_close") or {}
        assert zc.get("race_lost_noop", 0) == 1, zc
        assert zc.get("finalized", 0) == 0, zc

    async def test_same_txn_no_done_with_lock_window(self, rc_engine, rc_bus):
        """Ordering pin: after the seam's transaction commits there
        is NO observable state where the job is done AND its lock
        row survives (a crash between finalize and release would
        leave exactly that — impossible by the single-transaction
        pairing; asserted on the committed state from a SECOND
        connection)."""
        ref = insert_zombie_fixture(
            rc_engine, verdict="failed", age_hours=3.0
        )
        JobRepository(rc_engine).force_finalize_orphan(
            ref["job_id"], "zombie_active_failed", True
        )
        with rc_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT j.admission_state, "
                    "(SELECT COUNT(*) FROM job_locks l "
                    " WHERE l.job_id = j.job_id) AS locks "
                    "FROM job_queue_items j WHERE j.job_id = :j"
                ),
                {"j": ref["job_id"]},
            ).first()
        assert row is not None
        assert row[0] == "done"
        assert int(row[1]) == 0, (
            "same-transaction pairing: done ∧ lock-present is "
            "unobservable post-commit"
        )
