"""B1 — the concurrent DOUBLE-SHED race, pinned on real PostgreSQL.

The tester's G5 finding (functional gate 2026-11-05, blocker B1): the
per-tree shed decrements ``cluster.report_count`` from a PRE-DELETE
read while the ``DELETE`` can affect 0 rows (a concurrent shed already
removed the victims), drifting the count by −1 per racing shed —
measured 40/40 rounds on PG 16.15 (count 3 vs 4 surviving members),
with the no-shed control exact (20/20).

This pack pins the fixed invariant in the tester's 40-round harness
shape:

* **the race leg (40 rounds)** — a concurrent DOUBLE shed racing an
  assignment on a cluster owned by a DIFFERENT key: transaction S1
  sheds the victim batch UNCOMMITTED (holding the row locks), the
  racing ``assign_report_unit`` (a different cluster key, carrying the
  SAME ``shed_report_ids``) blocks on the victim rows, S1 commits, the
  racer's ``DELETE ... RETURNING`` observes 0 deleted rows and must
  decrement NOTHING. After every round:

  - ``report_count == COUNT(members WHERE outcome='accepted')`` for
    EVERY cluster (the lane's own pinned invariant),
  - no negative ``report_count``,
  - no dangling ``cluster_id`` (every accepted report points at a
    live cluster row),
  - no silent loss (the racing report row IS durable — accepted or
    unclustered, never vanished).

  The interleave is DETERMINISTIC (rendezvous via ``pg_stat_activity``
  wait-event polling), so the pre-fix code form fails every round —
  its pre-read count (2, read under MVCC while S1 is uncommitted)
  decrements rows S1 already shed: count 4−2−2 = 0 vs 2 members.

* **the control leg** — 20 concurrent same-key accepts with NO shed
  stay exact (the tester's control proof): every cluster's count
  equals its member count, sum == 20.

Scratch-DB contract: attach via ``PG_TEST_DB`` to a sanctioned scratch
database (``tests/postgres/conftest.py`` owns the fence; NEVER
``ensemble_dev``/``ensemble_prod``). PG-only by design — the race
needs real row-lock blocking under READ COMMITTED, which SQLite
cannot produce; when PG is unreachable the pack skips loudly via the
conftest's session-scoped probe.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text
from sqlmodel import Session

from daemon.repositories.anomaly.models import (  # noqa: F401 — metadata
    OUTCOME_ACCEPTED,
    AnomalyCluster,
    AnomalyReport,
)
from daemon.repositories.anomaly.repository import (
    AnomalyRepository,
    shed_members,
)

ROUNDS = 40
CONTROL_THREADS = 20


def _now(i: int) -> str:
    """A deterministic fixed-width ISO stamp (unique per call)."""
    base = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=i)).isoformat(timespec="microseconds")


def _report(seed: int, *, tree_root: str) -> AnomalyReport:
    return AnomalyReport(
        agent_id="worker",
        instance_id=f"inst-{seed}",
        tree_root_instance_id=tree_root,
        category="tool-failure",
        category_raw="tool-failure",
        severity="major",
        description=f"pin report {seed}",
        created_at=_now(seed),
    )


def _seed_cluster(
    repo: AnomalyRepository, tree_root: str, *, seed: int, members: int
) -> tuple[str, list[str]]:
    """One cluster with ``members`` accepted reports; returns
    (cluster_id, member_ids oldest-first)."""
    member_ids: list[str] = []
    cluster_id: str | None = None
    for i in range(members):
        assignment = repo.assign_report_unit(
            _report(seed + i, tree_root=tree_root),
            tree_root_instance_id=tree_root,
            category="performance",
            severity="major",
            window_cutoff=_now(0),
            now=_now(seed + i),
        )
        assert assignment.assigned
        member_ids.append(assignment.report_id)
        cluster_id = assignment.cluster_id
    assert cluster_id is not None
    return cluster_id, member_ids


def _victim_ids(repo: AnomalyRepository, tree_root: str, take: int) -> list[str]:
    """The oldest-first victim batch (the trims' own selection order)."""
    return repo.select_oldest_report_ids(
        tree_root_instance_id=tree_root, limit=take
    )


def _assert_invariants(engine) -> None:
    """The lane's pinned invariant set — exact counts, no negatives,
    no dangling cluster_id."""
    with Session(engine) as session:
        dangling = session.execute(
            select(func.count())
            .select_from(AnomalyReport)
            .where(AnomalyReport.outcome == OUTCOME_ACCEPTED)
            .where(AnomalyReport.cluster_id.is_not(None))
            .where(
                ~AnomalyReport.cluster_id.in_(
                    select(AnomalyCluster.id)
                )
            )
        ).scalar_one()
        assert dangling == 0, "no accepted report may point at a dead cluster"
        member_counts: dict[str, int] = {
            str(cluster_id): int(count)
            for cluster_id, count in session.execute(
                select(AnomalyReport.cluster_id, func.count())
                .where(AnomalyReport.outcome == OUTCOME_ACCEPTED)
                .where(AnomalyReport.cluster_id.is_not(None))
                .group_by(AnomalyReport.cluster_id)
            ).all()
        }
        clusters = session.execute(select(AnomalyCluster)).scalars().all()
        for cluster in clusters:
            count = int(cluster.report_count)
            members = member_counts.get(str(cluster.id), 0)
            assert count >= 0, f"negative report_count on {cluster.id}"
            assert count == members, (
                f"B1 — report_count {count} != {members} surviving accepted "
                f"members on cluster {cluster.id}"
            )


def _await_blocked_writer(engine, *, timeout_s: float = 20.0) -> None:
    """Block until a backend on THIS database waits on a row lock.

    The rendezvous that makes the race deterministic: the racing
    transaction's ``DELETE`` is blocked by S1's uncommitted victim-row
    locks — poll ``pg_stat_activity`` until that wait is observable,
    then (and only then) let S1 commit.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with engine.connect() as conn:
            waiting = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND state = 'active' "
                    "AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
        if int(waiting) > 0:
            return
        time.sleep(0.02)
    raise AssertionError(
        "rendezvous failure: no blocked writer observed within "
        f"{timeout_s}s — the race interleave could not be established"
    )


class TestDoubleShedRacingAssignment:
    def test_report_count_exact_40_rounds(self, pg_engine) -> None:
        """The tester's G5 harness shape, pinned: a concurrent double
        shed racing an assignment on a different-key cluster keeps
        ``report_count == COUNT(accepted members)`` — 40/40 rounds."""
        repo = AnomalyRepository(pg_engine)
        for round_no in range(ROUNDS):
            tree_root = f"tree-b1-{round_no}"
            cluster_id, member_ids = _seed_cluster(
                repo, tree_root, seed=round_no * 100, members=4
            )
            assert len(member_ids) == 4
            victims = _victim_ids(repo, tree_root, 2)
            assert len(victims) == 2
            assert set(victims) < set(member_ids)

            outcome: dict[str, object] = {}

            def _racer() -> None:
                try:
                    assignment = repo.assign_report_unit(
                        _report(round_no * 100 + 90, tree_root=tree_root),
                        tree_root_instance_id=tree_root,
                        category="correctness",  # a DIFFERENT key
                        severity="major",
                        window_cutoff=_now(0),
                        now=_now(round_no * 100 + 90),
                        shed_report_ids=victims,
                    )
                    outcome["assignment"] = assignment
                except BaseException as exc:  # noqa: BLE001 — re-raised
                    outcome["error"] = exc

            # S1 sheds the victims UNCOMMITTED — the row locks are held.
            s1 = Session(pg_engine)
            try:
                shed_members(s1, victims)
                racer = threading.Thread(target=_racer)
                racer.start()
                _await_blocked_writer(pg_engine)
                s1.commit()
            finally:
                s1.close()
            racer.join(timeout=60)
            assert not racer.is_alive(), "the racer thread did not finish"

            assert "error" not in outcome, repr(outcome.get("error"))
            assignment = outcome["assignment"]
            # No silent loss: the racing report IS durable and clustered.
            assert assignment.assigned
            with Session(pg_engine) as session:
                row = session.get(AnomalyReport, assignment.report_id)
                assert row is not None
                assert row.outcome == OUTCOME_ACCEPTED
                assert row.cluster_id == assignment.cluster_id

            # The victim cluster: 4 members − 2 shed = 2, count exact.
            with Session(pg_engine) as session:
                cluster = session.get(AnomalyCluster, cluster_id)
                assert cluster is not None, "R-b must not fire — members remain"
                assert cluster.report_count == 2, (
                    f"round {round_no}: victim cluster count "
                    f"{cluster.report_count} != 2"
                )
            _assert_invariants(pg_engine)


class TestControlNoShed:
    def test_concurrent_same_key_accepts_stay_exact(self, pg_engine) -> None:
        """The control proof: relative +1 rollups compose exactly when
        no shed races them (the tester measured 20/20)."""
        repo = AnomalyRepository(pg_engine)
        barrier = threading.Barrier(CONTROL_THREADS)
        errors: list[BaseException] = []

        def _accept(i: int) -> None:
            try:
                barrier.wait(timeout=60)
                repo.assign_report_unit(
                    _report(1000 + i, tree_root="tree-b1-control"),
                    tree_root_instance_id="tree-b1-control",
                    category="performance",
                    severity="major",
                    window_cutoff=_now(0),
                    now=_now(1000 + i),
                )
            except BaseException as exc:  # noqa: BLE001 — collected
                errors.append(exc)

        threads = [
            threading.Thread(target=_accept, args=(i,))
            for i in range(CONTROL_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not any(t.is_alive() for t in threads)
        assert not errors, repr(errors[:3])

        with Session(pg_engine) as session:
            total = session.execute(
                select(func.count())
                .select_from(AnomalyReport)
                .where(AnomalyReport.outcome == OUTCOME_ACCEPTED)
            ).scalar_one()
            assert total == CONTROL_THREADS
        _assert_invariants(pg_engine)
