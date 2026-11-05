"""The nest-anomaly RETENTION sweep + the maintenance registration (R6).

Covers the phase-2 retention contract (decisions.md R6 + AM-6 + AM-5b
+ AM-7 + AM-9/C3 + AM-14 + AM-15, phase2-plan.md "Retention Sweep"):

* the sweep is REGISTERED on the existing ``MaintenanceService`` seam
  (``manager.py`` registration cluster) at a 24h interval (M-2g);
* the age DELETE is BATCHED and bounded per run (M-2h);
* every deleted member DECREMENTS its cluster's ``report_count`` via
  the ONE shared ``shed_members`` helper (AM-6/E5) — the invariant
  ``report_count == COUNT(accepted members)`` holds ABSOLUTELY, both
  directions, AND under the D2 concurrent-shed interleaving;
* a cluster whose count reaches 0 is DELETED in the same unit-of-work
  (R-b) — ``cluster_row_dies_with_members``;
* orphan ``accepted``+NULL rows are TERMINALIZED to ``unclustered``
  (age-gated, bounded) — NEVER reassigned (AM-5b / M-2i);
* resolved clusters' board items reach ``done`` (AM-7 — a BOARD-row
  write only; the cluster row itself is untouched);
* the F-1 global leg — the refusal-ledger INSERT never takes TOTAL
  rows above ``max_rows_global``;
* the ``global_capacity_cap_leg`` four-part observable consequence;
* the per-tree cap SHEDS — one tree can never go dark (C3(ii));
* resolution is DERIVED — no status column, no reopen write (R5);
* an injected sweep exception logs a WARNING and never raises (the
  house best-effort convention).

Mutation legs (phase6 §(b)):

* **M-2g** — unregister the maintenance job → RED.
* **M-2h** — make the delete unbatched → RED.
* **M-2i** — REASSIGN an orphan instead of terminalizing it → RED.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon.config import NestReportConfig
from daemon.models.board import BoardItem, BoardItemTag, BoardNote
from daemon.repositories.anomaly import models as anomaly_models
from daemon.repositories.anomaly import repository as anomaly_repository
from daemon.repositories.anomaly.models import (
    AnomalyCluster,
    AnomalyReport,
)
from daemon.repositories.anomaly.repository import (
    shed_members,
)
from daemon.services import anomaly_service
from daemon.services.anomaly_service import (
    AnomalyRefusalError,
    AnomalyService,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER_SOURCE = (REPO_ROOT / "daemon" / "manager.py").read_text()

# ── the shared fakes (each pack is standalone per house convention) ───────


class _StubInstanceRepo:
    def __init__(self, chains: dict[str, list[str]] | None = None) -> None:
        self._chains = chains or {}

    def get_ancestor_ids(self, instance_id: str) -> list[str]:
        return list(self._chains.get(instance_id, []))

    def get_agent_ids_for(self, ids: list[str]) -> dict[str, str]:
        return {item: "agent" for item in ids}


class _StubTaskRepo:
    def find_running_by_instance(self, instance_id: str):  # noqa: ANN001
        return None


class _StubJobRepo:
    def get_active_by_instance(self, instance_id: str):  # noqa: ANN001
        return None


class _StubMissionRepo:
    def find_outstanding_commission_for_worker(self, instance_id: str):  # noqa: ANN001
        return None


class _StubWedgeRepo:
    def list_recent(self, limit: int):  # noqa: ANN001
        return []


class _FakeManager:
    def __init__(
        self,
        chains: dict[str, list[str]] | None = None,
        board=None,
    ) -> None:
        self.checkpointer = type("_Cp", (), {"raw_saver": None})()
        self.metrics_query_provider = None
        self._instance_repository = _StubInstanceRepo(chains)
        self._task_repo = _StubTaskRepo()
        self._job_queue_service = type(
            "_Jqs", (), {"repository": _StubJobRepo()}
        )()
        self._mission_record_service = type(
            "_Mrs", (), {"commission_repository": lambda self=None: _StubMissionRepo()}
        )()
        self._wedge_capture_repository = _StubWedgeRepo()
        self._board_service = board


async def _passthrough(fn, *args, **kwargs):
    return fn(*args, **kwargs)


class _Clock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)

    def iso(self) -> str:
        return self.now.isoformat(timespec="microseconds")

    def past(self, **kwargs: float) -> str:
        return (self.now - timedelta(**kwargs)).isoformat(timespec="microseconds")


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    import daemon.services.board_service as board_service_module

    clock = _Clock()
    monkeypatch.setattr(anomaly_service, "_utc_now_iso", clock.iso)
    monkeypatch.setattr(anomaly_models, "_utc_now_iso", clock.iso)
    monkeypatch.setattr(board_service_module, "_now_iso", clock.iso)
    return clock


def _engine(tmp_path, name: str = "anomaly_retention.db"):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            AnomalyReport.__table__,
            AnomalyCluster.__table__,
            BoardItem.__table__,
            BoardNote.__table__,
            BoardItemTag.__table__,
        ],
    )
    return engine


def _service(
    engine,
    config: NestReportConfig | None = None,
    board=None,
) -> AnomalyService:
    if board is None:
        from daemon.services.board_service import BoardService

        board = BoardService(engine)
    return AnomalyService(
        engine,
        config=config or NestReportConfig(),
        manager=_FakeManager({"inst-1": ["root-1"]}, board=board),
        offload=_passthrough,
    )


async def _accept(
    service: AnomalyService,
    *,
    agent_id: str = "worker",
    instance_id: str = "inst-1",
    category: str = "tool-failure",
    severity: str = "major",
):
    return await service.accept_report(
        agent_id=agent_id,
        instance_id=instance_id,
        category=category,
        description=f"{severity} problem",
        severity=severity,
    )


def _seed_rows(
    engine,
    *,
    count: int,
    created_at: str,
    cluster: AnomalyCluster | None = None,
    agent_id: str = "worker",
    severity: str = "minor",
    outcome: str = "accepted",
) -> list[str]:
    """Bulk-seed report rows directly (the sweep does not care who wrote
    them). When a cluster is given, the rows become its members and its
    ``report_count`` is set to the total member count.

    ``outcome`` is EXPLICIT so a capacity scenario can make the ACCEPTED
    count and the TOTAL count DIVERGE. With every row accepted the two
    counts are equal, and a TOTAL-row fence is then indistinguishable
    from an ACCEPTED-only fence — the F-1 vacuous-pin trap (mutating
    the fence's ``outcome=None`` to ``outcome='accepted'`` would pass).
    """
    ids = []
    with Session(engine) as session:
        from sqlalchemy import func
        from sqlmodel import select as sm_select

        for index in range(count):
            row = AnomalyReport(
                created_at=created_at,
                agent_id=agent_id,
                instance_id="inst-1",
                tree_root_instance_id=cluster.tree_root_instance_id
                if cluster
                else "root-1",
                category=cluster.category if cluster else "tool-failure",
                category_raw="tool-failure",
                severity=severity,
                description=f"seeded {index}",
                cluster_id=cluster.id if cluster else None,
                outcome=outcome,
            )
            session.add(row)
            ids.append(row.id)
        if cluster is not None:
            session.flush()  # the pending rows become query-visible
            total = int(
                session.exec(
                    sm_select(func.count())
                    .select_from(AnomalyReport)
                    .where(AnomalyReport.cluster_id == cluster.id)
                ).one()
            )
            attached = session.get(AnomalyCluster, cluster.id)
            assert attached is not None
            attached.report_count = total
        session.commit()
    return ids


def _cluster_row(engine, cluster_id: str) -> AnomalyCluster | None:
    with Session(engine) as session:
        cluster = session.get(AnomalyCluster, cluster_id)
        if cluster is not None:
            session.expunge(cluster)
        return cluster


def _members(engine, cluster_id: str) -> int:
    from sqlalchemy import func
    from sqlmodel import select as sm_select

    with Session(engine) as session:
        return int(
            session.exec(
                sm_select(func.count())
                .select_from(AnomalyReport)
                .where(AnomalyReport.cluster_id == cluster_id)
                .where(AnomalyReport.outcome == "accepted")
            ).one()
        )


# ── the registration seam (M-2g's target) ────────────────────────────────


class TestTheRegistrationSeam:
    def test_the_retention_job_is_registered_on_the_maintenance_seam(self) -> None:
        assert 'self._maintenance_service.register(\n            "anomaly_retention",' in (
            MANAGER_SOURCE
        ), "the ONE registration must exist in manager.py's cluster"
        assert "min_interval_hours=24" in MANAGER_SOURCE
        assert (
            "execute_fn=self._anomaly_service.run_retention_sweep"
            in MANAGER_SOURCE
        ), "the job body is the service's retention sweep"

    async def test_the_sweep_never_raises(self, tmp_path, clock, caplog) -> None:
        """Best-effort convention — an injected exception logs WARNING
        and never escapes into the maintenance loop."""
        engine = _engine(tmp_path)
        service = _service(engine)

        async def _boom(**kwargs):
            raise RuntimeError("sweep exploded")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            service._repository, "sweep_reports_older_than", _boom
        )
        try:
            with caplog.at_level("WARNING", logger="daemon.services.anomaly_service"):
                await service.run_retention_sweep()  # must NOT raise
        finally:
            monkeypatch.undo()
        assert any("retention sweep failed" in record.message for record in caplog.records)


# ── the age sweep (R6) ────────────────────────────────────────────────────


class TestTheAgeSweep:
    async def test_thirty_one_day_old_rows_are_swept_and_recent_rows_remain(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        old_ids = _seed_rows(
            engine, count=3, created_at=clock.past(days=31)
        )
        fresh_ids = _seed_rows(
            engine, count=2, created_at=clock.past(hours=1)
        )
        await service.run_retention_sweep()
        with Session(engine) as session:
            surviving = {
                row.id
                for row in session.exec(
                    __import__("sqlmodel").select(AnomalyReport)
                ).all()
            }
        assert surviving == set(fresh_ids), "only the aged rows are gone"

    async def test_the_sweep_is_batched_and_bounded_per_run(
        self, tmp_path, clock
    ) -> None:
        """M-2h's target — 6,000 stale rows, batch 500, per-run cap
        5,000: ONE run deletes exactly 5,000 and leaves 1,000 (a single
        unbounded DELETE would empty the table)."""
        engine = _engine(tmp_path)
        service = _service(engine, config=NestReportConfig(retention_batch_size=500))
        _seed_rows(engine, count=6000, created_at=clock.past(days=40))
        await service.run_retention_sweep()
        assert service.repository.count_reports(outcome=None) == 1000

    async def test_retention_decrements_counts(
        self, tmp_path, clock
    ) -> None:
        """AM-6 — each deleted member decrements exactly its cluster's
        count by 1 (a chronic cluster survives its mid-lifetime sweep
        without over-counting)."""
        engine = _engine(tmp_path)
        service = _service(engine)
        with Session(engine) as session:
            session.add(
                AnomalyCluster(
                    id="chronic",
                    cluster_key_hash="k",
                    tree_root_instance_id="root-1",
                    category="tool-failure",
                    first_report_at=clock.past(days=40),
                    last_report_at=clock.past(minutes=5),
                    report_count=0,
                    max_severity="major",
                    created_at=clock.past(days=40),
                )
            )
            session.commit()
        old_ids = _seed_rows(
            engine,
            count=3,
            created_at=clock.past(days=31),
            cluster=_cluster_row(engine, "chronic"),
        )
        fresh_ids = _seed_rows(
            engine,
            count=2,
            created_at=clock.past(hours=1),
            cluster=_cluster_row(engine, "chronic"),
        )
        cluster = _cluster_row(engine, "chronic")
        assert cluster.report_count == 5
        await service.run_retention_sweep()
        cluster = _cluster_row(engine, "chronic")
        assert cluster is not None
        assert cluster.report_count == 2, "3 deleted members = count -3"
        assert _members(engine, "chronic") == 2
        assert cluster.report_count == _members(engine, "chronic")


class TestReportCountEqualsMemberRows:
    """AM-6 + D2 — the invariant holds ABSOLUTELY."""

    async def test_the_invariant_holds_after_a_sweep(self, tmp_path, clock) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        await _accept(service)
        await _accept(service)
        with Session(engine) as session:
            cluster = session.exec(
                __import__("sqlmodel").select(AnomalyCluster)
            ).first()
        assert cluster is not None
        # age EVERYTHING but one member.
        with Session(engine) as session:
            rows = session.exec(
                __import__("sqlmodel").select(AnomalyReport)
            ).all()
            for row in rows[:-1]:
                row.created_at = clock.past(days=31)
            session.commit()
        await service.run_retention_sweep()
        assert _cluster_row(engine, cluster.id).report_count == 1
        assert _members(engine, cluster.id) == 1

    def test_the_invariant_pin_detects_fabricated_drift(
        self, tmp_path, clock
    ) -> None:
        """The reverse direction — a fabricated drift MUST redden the
        invariant check (the pin is not vacuous)."""
        engine = _engine(tmp_path)
        service = _service(engine)
        result_ids = _seed_rows(
            engine, count=3, created_at=clock.past(hours=1)
        )
        with Session(engine) as session:
            session.add(
                AnomalyCluster(
                    id="drift",
                    cluster_key_hash="k",
                    tree_root_instance_id="root-1",
                    category="tool-failure",
                    first_report_at=clock.past(hours=1),
                    last_report_at=clock.past(hours=1),
                    report_count=8,  # fabricated drift: 8 != 3 members
                    max_severity="major",
                    created_at=clock.past(hours=1),
                )
            )
            for row_id in result_ids:
                row = session.get(AnomalyReport, row_id)
                row.cluster_id = "drift"
            session.commit()
        drifted = _cluster_row(engine, "drift").report_count
        assert drifted != _members(engine, "drift"), (
            "the fabricated drift must be VISIBLE to the invariant read"
        )

    async def test_the_d2_concurrent_shed_leg(
        self, tmp_path, clock
    ) -> None:
        """D2 — a per-tree shed racing an assignment on a cluster of the
        SAME tree under a DIFFERENT stripe slot: the RELATIVE rollup
        composes with the interim decrement (the absolute form would
        clobber it)."""
        engine = _engine(tmp_path)
        service = _service(
            engine, config=NestReportConfig(rate_max_per_agent=50)
        )
        # cluster Y: tree root-1, category performance, 5 members.
        for _ in range(5):
            await _accept(service, category="performance", agent_id="packer")
        with Session(engine) as session:
            cluster = session.exec(
                __import__("sqlmodel")
                .select(AnomalyCluster)
                .where(AnomalyCluster.category == "performance")
            ).first()
            assert cluster is not None
            victim_ids = [
                row.id
                for row in session.exec(
                    __import__("sqlmodel")
                    .select(AnomalyReport)
                    .where(AnomalyReport.cluster_id == cluster.id)
                    .order_by(AnomalyReport.created_at.asc())
                ).all()[:2]
            ]

        original = anomaly_repository._select_candidate

        def _select_then_shed(session, **kwargs):
            row = original(session, **kwargs)
            if row is not None and kwargs.get("category") == "performance":
                # the concurrent per-tree shed lands BETWEEN the
                # assignment's SELECT and its UPDATE (a different
                # stripe slot — not serialized): it deletes 2 of Y's
                # members and decrements the count RELATIVELY.
                with Session(engine) as other:
                    shed_members(other, victim_ids)
                    other.commit()
            return row

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            anomaly_repository, "_select_candidate", _select_then_shed
        )
        try:
            # the racing assignment: same tree, DIFFERENT key ⇒ a
            # different stripe slot.
            result = await _accept(service, category="performance", agent_id="packer")
        finally:
            monkeypatch.undo()

        cluster = _cluster_row(engine, cluster.id)
        assert cluster.report_count == _members(engine, cluster.id), (
            "D2 — the relative rollup composes with the interim "
            f"decrement (count={cluster.report_count}, members="
            f"{_members(engine, cluster.id)})"
        )
        assert cluster.report_count == 4, "5 - 2 shed + 1 assigned"
        assert result.cluster_id == cluster.id


class TestClusterRowDiesWithMembers:
    async def test_a_zero_count_cluster_is_deleted_in_the_same_unit(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        await _accept(service)
        await _accept(service)
        with Session(engine) as session:
            cluster = session.exec(
                __import__("sqlmodel").select(AnomalyCluster)
            ).first()
            member_ids = [
                row.id
                for row in session.exec(
                    __import__("sqlmodel")
                    .select(AnomalyReport)
                    .where(AnomalyReport.cluster_id == cluster.id)
                ).all()
            ]
        with Session(engine) as session:
            shed_members(session, member_ids)
            session.commit()
        assert _cluster_row(engine, cluster.id) is None, (
            "R-b — a cluster whose report_count reaches 0 is DELETED"
        )


class TestOrphanTerminalization:
    """AM-5b — accepted MEANS clustered."""

    async def test_no_orphan_accepted_rows_after_any_accept(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        await _accept(service)
        await _accept(service)
        with Session(engine) as session:
            orphans = session.exec(
                __import__("sqlmodel")
                .select(AnomalyReport)
                .where(AnomalyReport.outcome == "accepted")
                .where(AnomalyReport.cluster_id.is_(None))  # type: ignore[union-attr]
            ).all()
            assert orphans == []

    async def test_a_crash_injected_orphan_is_terminalized_not_reassigned(
        self, tmp_path, clock
    ) -> None:
        """The crash window between INSERT and binding (a live
        assignment can no longer produce it — the unit is ONE
        transaction): the sweep terminalizes the row to
        ``unclustered``, NEVER reassigned."""
        engine = _engine(tmp_path)
        service = _service(engine)
        await _accept(service)  # a live cluster exists to "reassign" to
        with Session(engine) as session:
            orphan = AnomalyReport(
                created_at=clock.past(hours=2),  # past the age gate
                agent_id="worker",
                instance_id="inst-1",
                tree_root_instance_id="root-1",
                category="tool-failure",
                category_raw="tool-failure",
                severity="major",
                description="crash-window orphan",
                cluster_id=None,
                outcome="accepted",
            )
            session.add(orphan)
            session.commit()
            orphan_id = orphan.id
        await service.run_retention_sweep()
        with Session(engine) as session:
            row = session.get(AnomalyReport, orphan_id)
            assert row is not None
            assert row.outcome == "unclustered"
            assert row.cluster_id is None, (
                "NEVER reassigned — post-hoc reassignment is retroactive "
                "identity instability (R5's own rejection)"
            )
            remaining_accepted_orphans = session.exec(
                __import__("sqlmodel")
                .select(AnomalyReport)
                .where(AnomalyReport.outcome == "accepted")
                .where(AnomalyReport.cluster_id.is_(None))  # type: ignore[union-attr]
            ).all()
            assert remaining_accepted_orphans == []

    async def test_a_fresh_orphan_is_left_alone_by_the_age_gate(
        self, tmp_path, clock
    ) -> None:
        """The sweep's age gate (the resolve window) keeps it from
        racing a live in-flight assignment."""
        engine = _engine(tmp_path)
        service = _service(engine)
        with Session(engine) as session:
            orphan = AnomalyReport(
                created_at=clock.past(minutes=5),  # INSIDE the gate
                agent_id="worker",
                instance_id="inst-1",
                category="tool-failure",
                category_raw="tool-failure",
                severity="major",
                description="live-looking orphan",
                cluster_id=None,
                outcome="accepted",
            )
            session.add(orphan)
            session.commit()
            orphan_id = orphan.id
        await service.run_retention_sweep()
        with Session(engine) as session:
            row = session.get(AnomalyReport, orphan_id)
            assert row.outcome == "accepted", "too fresh to terminalize"


class TestBoardItemClosesWhenClusterResolves:
    async def test_a_resolved_clusters_board_item_reaches_done(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        await _accept(service, severity="critical")
        with Session(engine) as session:
            item = session.exec(
                __import__("sqlmodel").select(BoardItem)
            ).first()
            assert item is not None
            assert item.status == "todo"
            cluster = session.exec(
                __import__("sqlmodel").select(AnomalyCluster)
            ).first()
            cluster_before = {
                column: getattr(cluster, column)
                for column in (
                    "id",
                    "report_count",
                    "max_severity",
                    "first_report_at",
                    "last_report_at",
                    "board_item_id",
                    "created_at",
                )
            }
        clock.advance(hours=2)  # past the 1h resolve window
        await service.run_retention_sweep()
        with Session(engine) as session:
            item = session.get(BoardItem, item.id)
            assert item.status == "done", "AM-7 — the board item closes"
            cluster_after = session.get(AnomalyCluster, cluster.id)
            for column, value in cluster_before.items():
                assert getattr(cluster_after, column) == value, (
                    f"R5 — the cluster row is NEVER touched ({column})"
                )


class TestUnboundedRefusalLoopCapped:
    async def test_fifty_refused_calls_persist_at_most_ten_ledger_rows(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        for _ in range(5):
            await _accept(service)
        for _ in range(50):
            with pytest.raises(AnomalyRefusalError) as caught:
                await _accept(service)
            assert caught.value.token == "rate-limited"
        assert (
            service.repository.count_reports(outcome="rate_limited") == 10
        )

    # The F-1 global-ceiling pin (test_the_f1_global_leg…) was MOVED to
    # the RATE pack (council fix cycle ③, 2026-11-05): the ceiling is a
    # RATE-GATE fence — the pin sits where the fence lives, so a future
    # pack reorganization cannot strand it from the fence-shape
    # contract that pins its predicate.


class TestGlobalCapacityCapLeg:
    """C3(iii) — the four-part OBSERVABLE consequence."""

    async def test_the_envelope_the_row_the_counter_and_the_surfacing(
        self, tmp_path, clock, caplog
    ) -> None:
        engine = _engine(tmp_path)
        config = NestReportConfig(max_rows_global=6)
        service = _service(engine, config=config)
        _seed_rows(engine, count=6, created_at=clock.past(minutes=1))
        assert service.capacity_exhausted_total == 0
        with caplog.at_level(
            "WARNING", logger="daemon.services.anomaly_service"
        ):
            with pytest.raises(AnomalyRefusalError) as caught:
                await _accept(service, agent_id="fresh-agent")
        # (a) the envelope token.
        assert caught.value.token == "capacity-exhausted"
        # (b) NO row is written.
        assert service.repository.count_reports(outcome=None) == 6
        # (c) the capacity-exhausted counter INCREMENTS.
        assert service.capacity_exhausted_total == 1
        # (d) the refusal is SURFACED for review (not merely the shape).
        assert any(
            "surfaced for morning review" in record.message
            for record in caplog.records
        )

    async def test_the_pre_insert_leg_refuses_after_harvest(
        self, tmp_path, clock
    ) -> None:
        """The SECOND global check (post-harvest, pre-persist) — the
        rows grew between the fence-2 read and the insert."""
        engine = _engine(tmp_path)
        config = NestReportConfig(max_rows_global=6)
        service = _service(engine, config=config)
        _seed_rows(engine, count=5, created_at=clock.past(minutes=1))
        # 5 < 6 ⇒ the accept path RUNS and persists the 6th row.
        result = await _accept(service, agent_id="fresh-agent")
        assert result.outcome == "accepted"
        assert service.repository.count_reports(outcome=None) == 6
        # the NEXT report — fresh agent, empty budget — hits the WALL.
        with pytest.raises(AnomalyRefusalError) as caught:
            await _accept(service, agent_id="fresh-agent-2")
        assert caught.value.token == "capacity-exhausted"
        assert service.capacity_exhausted_total >= 1


class TestPerTreeTrimKeepsChannelLive:
    async def test_a_tree_at_its_cap_never_goes_dark(
        self, tmp_path, clock
    ) -> None:
        """C3(ii) — the next report is ACCEPTED after an oldest-first
        shed; the invariant stays equal on the affected clusters."""
        engine = _engine(tmp_path)
        config = NestReportConfig(
            max_rows_per_tree_root=5,
            rate_max_per_agent=50,
            retention_batch_size=10,
        )
        service = _service(engine, config=config)
        first = await _accept(service)
        oldest_id = None
        with Session(engine) as session:
            oldest_id = session.exec(
                __import__("sqlmodel")
                .select(AnomalyReport)
                .order_by(AnomalyReport.created_at.asc())
            ).first().id
        for _ in range(4):
            clock.advance(seconds=30)  # distinct stamps ⇒ a real oldest
            await _accept(service)
        assert (
            service.repository.count_reports(
                tree_root_instance_id="root-1", outcome=None
            )
            == 5
        )
        # the 6th report for this tree: SHED-THEN-ACCEPT (never refuse).
        result = await _accept(service)
        assert result.outcome == "accepted"
        tree_rows = service.repository.count_reports(
            tree_root_instance_id="root-1", outcome=None
        )
        assert tree_rows == 5, "the shed made room for the new row"
        with Session(engine) as session:
            assert session.get(AnomalyReport, oldest_id) is None, (
                "the OLDEST row was shed first"
            )
        cluster = _cluster_row(engine, first.cluster_id)
        assert cluster is not None
        assert cluster.report_count == _members(engine, cluster.id)


class TestResolutionDerived:
    async def test_openness_flips_at_the_resolve_window_with_zero_writes(
        self, tmp_path, clock
    ) -> None:
        """R5 — open ⇔ last_report_at >= now − resolve_window; the flip
        involves NO write to any cluster column."""
        engine = _engine(tmp_path)
        service = _service(engine)
        await _accept(service)
        with Session(engine) as session:
            cluster = session.exec(
                __import__("sqlmodel").select(AnomalyCluster)
            ).first()
            snapshot = {
                column: getattr(cluster, column)
                for column in AnomalyCluster.__table__.columns.keys()
            }

        def _is_open() -> bool:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(seconds=3600)
            ).isoformat(timespec="microseconds")
            with Session(engine) as session:
                row = session.exec(
                    __import__("sqlmodel").select(AnomalyCluster)
                ).first()
                return row.last_report_at >= cutoff

        # the derived predicate uses the SERVICE clock (patched) — read
        # through the same fixed-width compare the read surface uses.
        resolve_cutoff = (clock.now - timedelta(seconds=3600)).isoformat(
            timespec="microseconds"
        )
        with Session(engine) as session:
            row = session.exec(
                __import__("sqlmodel").select(AnomalyCluster)
            ).first()
            assert row.last_report_at >= resolve_cutoff, "open at t0"
        clock.advance(hours=2)
        resolve_cutoff = (clock.now - timedelta(seconds=3600)).isoformat(
            timespec="microseconds"
        )
        with Session(engine) as session:
            row = session.exec(
                __import__("sqlmodel").select(AnomalyCluster)
            ).first()
            assert row.last_report_at < resolve_cutoff, (
                "closed at t0+2h WITHOUT any write"
            )
            after = {
                column: getattr(row, column)
                for column in AnomalyCluster.__table__.columns.keys()
            }
        assert after == snapshot, "no cluster column changed"

    def test_there_is_no_status_column_and_no_reopen_write(self) -> None:
        import ast

        columns = set(AnomalyCluster.__table__.columns.keys())
        for forbidden in ("status", "open", "state", "resolved", "closed"):
            assert forbidden not in columns, (
                "R5 — resolution is DERIVED; a status column would "
                "reopen the reopen-race class"
            )
        for module in (anomaly_service, anomaly_repository):
            source = Path(module.__file__).read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and "reopen" in node.id.lower():
                    raise AssertionError(
                        f"{module.__name__} carries a reopen write path"
                    )
                if isinstance(node, ast.Attribute) and "reopen" in node.attr.lower():
                    raise AssertionError(
                        f"{module.__name__} carries a reopen write path"
                    )
