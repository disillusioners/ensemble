"""The nest-anomaly CLUSTERING service — write-time assignment (R5).

Covers the phase-2 clustering contract (decisions.md R5 + AM-5a +
AM-14 + D2 + E4, phase2-plan.md "Cluster Service Algorithm"):

* write-time assignment — the stored row is mutated ONCE to carry its
  ``cluster_id``; the cluster key is
  ``(tree_root_instance_id, canonical category)`` and the window is
  SLIDING from the cluster's LAST report (never fixed buckets);
* ``cluster_id`` is assigned ONCE and NEVER re-derived — earlier
  reports never move, later reports never change identity
  (``cluster_id_stability``);
* reports inside sliding 15-min gaps CHAIN into one cluster
  (``sliding_window_chains`` — a fixed bucket would split at the
  boundary);
* N agents × same incident = ONE incident (``n_agents_one_incident``)
  — all descendants of one root resolve to the same ``tree_root``;
* distinct keys never collapse; a re-incident after quiet creates a
  SECOND same-key cluster with NO constraint error (the R5
  unique-rejection proof);
* the AM-14 stripe serializes same-key writes (the concurrent leg);
* ``accepted`` MEANS clustered (AM-5b) — an E4 empty-RETURNING
  assignment failure writes the report ``unclustered``, never
  ``accepted`` + NULL and never a cluster_id pointing at a deleted
  cluster;
* AC1 — the three clustering pins also run on a scratch POSTGRESQL
  database (not just SQLite).

Mutation legs (phase6 §(b)):

* **M-2a** — drop the window term from the candidate seek → RED (a
  report outside the window collapses into the cluster).
* **M-2b** — re-cluster on write (recompute earlier members' cluster
  ids) → RED (identity instability).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon.config import NestReportConfig
from daemon.repositories.anomaly import models as anomaly_models
from daemon.repositories.anomaly.models import (
    OUTCOME_UNCLUSTERED,
    AnomalyCluster,
    AnomalyReport,
)
from daemon.repositories.anomaly import repository as anomaly_repository
from daemon.services import anomaly_service
from daemon.services.anomaly_service import (
    AnomalyRefusalError,
    AnomalyService,
)

# ── the shared fakes (each pack is standalone per house convention) ───────


class _StubInstanceRepo:
    """Ancestor chains the test controls — the tree root is chain[-1]."""

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


class _FakeRawSaver:
    def __init__(self, messages: list[object]) -> None:
        self._messages = messages
        self.calls: list[dict] = []

    async def aget(self, config: dict) -> dict:
        self.calls.append(config)
        return {"channel_values": {"messages": list(self._messages)}}


class _FakeCheckpointer:
    def __init__(self, messages: list[object]) -> None:
        self.raw_saver = _FakeRawSaver(messages)


class _FakeManager:
    def __init__(self, chains: dict[str, list[str]] | None = None) -> None:
        self.checkpointer = _FakeCheckpointer([])
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


async def _passthrough(fn, *args, **kwargs):
    return fn(*args, **kwargs)


class _Clock:
    """A deterministic fixed-width clock — controls the WHOLE service."""

    def __init__(
        self, start: datetime | None = None
    ) -> None:
        self.now = start or datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)

    def iso(self) -> str:
        return self.now.isoformat(timespec="microseconds")


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(anomaly_service, "_utc_now_iso", clock.iso)
    monkeypatch.setattr(anomaly_models, "_utc_now_iso", clock.iso)
    return clock


def _engine(tmp_path, name: str = "anomaly_cluster.db"):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
    )
    return engine


def _service(
    engine,
    chains: dict[str, list[str]] | None = None,
    config: NestReportConfig | None = None,
    offload=_passthrough,
) -> AnomalyService:
    return AnomalyService(
        engine,
        config=config or NestReportConfig(),
        manager=_FakeManager(chains),
        offload=offload,
    )


async def _report(
    service: AnomalyService,
    *,
    instance_id: str = "inst-1",
    category: str = "tool-failure",
    severity: str = "major",
    agent_id: str = "worker",
) -> object:
    return await service.accept_report(
        agent_id=agent_id,
        instance_id=instance_id,
        category=category,
        description=f"{category} boom from {instance_id}",
        severity=severity,
    )


def _clusters(engine) -> list[AnomalyCluster]:
    with Session(engine) as session:
        rows = session.exec(
            __import__("sqlmodel").select(AnomalyCluster)
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


def _cluster_of(engine, report_id: str) -> AnomalyCluster | None:
    with Session(engine) as session:
        report = session.get(AnomalyReport, report_id)
        assert report is not None
        cluster = session.get(AnomalyCluster, report.cluster_id) if report.cluster_id else None
        if cluster is not None:
            session.expunge(cluster)
        session.expunge(report)
        return cluster


# ── the pins ──────────────────────────────────────────────────────────────


class TestWriteTimeAssignment:
    async def test_two_same_key_reports_inside_the_window_collapse(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine, chains={"inst-1": []})
        first = await _report(service)
        clock.advance(minutes=5)
        second = await _report(service)
        assert first.cluster_id == second.cluster_id
        assert second.cluster_report_count == 2
        cluster = _cluster_of(engine, second.report_id)
        assert cluster is not None
        assert cluster.report_count == 2
        assert cluster.last_report_at >= cluster.first_report_at
        assert cluster.category == "tool-failure"

    async def test_distinct_keys_never_collapse(self, tmp_path, clock) -> None:
        engine = _engine(tmp_path)
        service = _service(engine, chains={"inst-1": []})
        a = await _report(service, category="tool-failure")
        b = await _report(service, category="performance")
        assert a.cluster_id != b.cluster_id
        assert len(_clusters(engine)) == 2

    async def test_distinct_trees_never_collapse(self, tmp_path, clock) -> None:
        engine = _engine(tmp_path)
        service = _service(
            engine, chains={"inst-a": ["root-a"], "inst-b": ["root-b"]}
        )
        a = await _report(service, instance_id="inst-a")
        b = await _report(service, instance_id="inst-b")
        assert a.cluster_id != b.cluster_id


class TestClusterIdStability:
    """`cluster_id` is assigned ONCE and never re-derived (R5)."""

    async def test_a_late_report_never_moves_an_earlier_report(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine, chains={"inst-1": []})
        early = await _report(service)
        first_cluster = early.cluster_id
        # quiet PAST the 15-minute window, then a same-key re-incident.
        clock.advance(hours=2)
        late = await _report(service)
        assert late.cluster_id != first_cluster, (
            "a re-incident after quiet creates a SECOND same-key cluster"
        )
        with Session(engine) as session:
            early_row = session.get(AnomalyReport, early.report_id)
            assert early_row is not None
            assert early_row.cluster_id == first_cluster, (
                "an earlier report's cluster_id NEVER moves"
            )
        assert len(_clusters(engine)) == 2

    async def test_the_re_incident_insert_is_not_an_integrity_error(
        self, tmp_path, clock
    ) -> None:
        """The R5 unique-rejection proof — the key is intentionally
        non-unique over time; a UNIQUE(cluster_key_hash) constraint
        would turn this legitimate INSERT into an IntegrityError."""
        engine = _engine(tmp_path)
        service = _service(engine, chains={"inst-1": []})
        await _report(service)
        clock.advance(hours=2)
        second = await _report(service)
        assert second.cluster_id
        keys = {
            cluster.cluster_key_hash for cluster in _clusters(engine)
        }
        assert len(keys) == 1, "both clusters share the same key hash"
        assert len(_clusters(engine)) == 2


class TestSlidingWindowChains:
    async def test_reports_at_t0_5m_12m_22m_form_one_cluster(
        self, tmp_path, clock
    ) -> None:
        """Gaps 5m/7m/10m are all < W=15m — the window slides from the
        LAST report, so the chain never splits (a fixed 15-min bucket
        would have split at t0+15m)."""
        engine = _engine(tmp_path)
        service = _service(engine, chains={"inst-1": []})
        ids = []
        # the GAP from the PREVIOUS report: 0, 5m, 7m, 10m — all < W.
        for gap_minutes in (0, 5, 7, 10):
            if gap_minutes:
                clock.advance(minutes=gap_minutes)
            result = await _report(service)
            ids.append(result.cluster_id)
        assert len(set(ids)) == 1
        with Session(engine) as session:
            cluster = session.get(AnomalyCluster, ids[0])
            assert cluster is not None
            assert cluster.report_count == 4

    async def test_a_report_past_the_window_starts_a_new_cluster(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine, chains={"inst-1": []})
        await _report(service)
        clock.advance(minutes=16)  # > W=15m since the LAST report
        late = await _report(service)
        cluster = _cluster_of(engine, late.report_id)
        assert cluster is not None
        assert cluster.report_count == 1


class TestNAgentsOneIncident:
    async def test_three_siblings_same_root_same_category_one_cluster(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(
            engine,
            chains={
                "child-a": ["root-1"],
                "child-b": ["root-1"],
                "child-c": ["root-1"],
            },
        )
        results = [
            await _report(service, instance_id=instance)
            for instance in ("child-a", "child-b", "child-c")
        ]
        assert len({result.cluster_id for result in results}) == 1
        cluster = _cluster_of(engine, results[0].report_id)
        assert cluster is not None
        assert cluster.report_count == 3
        assert cluster.tree_root_instance_id == "root-1"
        # the parent CHAINS differ (each row keeps its own chain) while
        # the tree ROOT is equal — the N-agents proof counts ROWS.
        with Session(engine) as session:
            rows = session.exec(
                __import__("sqlmodel").select(AnomalyReport)
            ).all()
            chains = sorted(tuple(row.parent_chain) for row in rows)
            roots = {row.tree_root_instance_id for row in rows}
        assert chains == [("root-1",), ("root-1",), ("root-1",)]
        assert roots == {"root-1"}

    async def test_the_severity_rollup_is_the_incremental_max_rank(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine, chains={"inst-1": []})
        await _report(service, severity="minor")
        await _report(service, severity="major")
        await _report(service, severity="critical")
        clusters = _clusters(engine)
        assert len(clusters) == 1
        assert clusters[0].max_severity == "critical"


class TestTheStripe:
    """AM-14 — same-key writes serialize (the concurrent leg)."""

    async def test_concurrent_same_key_reports_join_one_cluster(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine, chains={"inst-1": []})

        async def _yielding_offload(fn, *args, **kwargs):
            await asyncio.sleep(0)
            return fn(*args, **kwargs)

        service = AnomalyService(
            engine,
            config=NestReportConfig(),
            manager=_FakeManager({"inst-1": []}),
            offload=_yielding_offload,
        )
        results = await asyncio.gather(
            _report(service, instance_id="inst-1"),
            _report(service, instance_id="inst-1"),
        )
        assert results[0].cluster_id == results[1].cluster_id
        cluster = _cluster_of(engine, results[0].report_id)
        assert cluster is not None
        assert cluster.report_count == 2

    async def test_the_stripe_array_is_64_slots(self, tmp_path) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        assert len(service._stripe_locks) == 64


class TestAcceptedMeansClustered:
    """AM-5b + E4 — a live assignment failure terminalizes ``unclustered``."""

    async def test_an_empty_returning_is_an_assignment_failure(
        self, tmp_path, clock
    ) -> None:
        """E4 — the cluster is zero-count-DELETED between the SELECT and
        the UPDATE (a concurrent shed): the report row is written
        ``unclustered`` — never a cluster_id pointing at a DELETED
        cluster, never ``accepted`` + NULL."""
        engine = _engine(tmp_path)
        service = _service(engine, chains={"inst-1": []})
        seed = await _report(service)
        cluster_id = seed.cluster_id
        assert cluster_id

        original = anomaly_repository._select_candidate

        def _select_then_kill(session, **kwargs):
            row = original(session, **kwargs)
            if row is not None:
                # The concurrent shed: delete ALL members, then the
                # zero-count cluster — INSIDE the unit's own session so
                # the interleaving is deterministic.
                anomaly_repository.shed_members(
                    session,
                    [
                        str(member_id)
                        for member_id in session.execute(
                            __import__("sqlalchemy").select(
                                AnomalyReport.id
                            ).where(AnomalyReport.cluster_id == row[0])
                        ).scalars().all()
                    ],
                )
            return row

        monkeypatch_holder = pytest.MonkeyPatch()
        monkeypatch_holder.setattr(
            anomaly_repository, "_select_candidate", _select_then_kill
        )
        try:
            result = await _report(service)
        finally:
            monkeypatch_holder.undo()

        assert result.outcome == OUTCOME_UNCLUSTERED
        assert result.cluster_id is None
        with Session(engine) as session:
            row = session.get(AnomalyReport, result.report_id)
            assert row is not None
            assert row.outcome == OUTCOME_UNCLUSTERED
            assert row.cluster_id is None
            # the killed cluster is GONE — nothing points at it.
            assert session.get(AnomalyCluster, cluster_id) is None
        # and no orphan accepted+NULL row exists anywhere.
        with Session(engine) as session:
            orphans = session.exec(
                __import__("sqlmodel")
                .select(AnomalyReport)
                .where(AnomalyReport.outcome == "accepted")
                .where(AnomalyReport.cluster_id.is_(None))  # type: ignore[union-attr]
            ).all()
            assert orphans == []


# ── AC1 — the scratch-PostgreSQL leg ─────────────────────────────────────


class TestTheScratchPostgresLeg:
    """AC1 — the three clustering pins on a scratch PG database.

    The house PG recipe: the coordinates come from the ``PG_TEST_*``
    env vars ONLY, the target is a uniquely-named SANDBOX scratch
    database created over TCP and DROPPED at teardown, and the live
    databases (``ensemble_prod`` / ``ensemble_dev``) are never named.
    """

    def test_same_key_collapse_distinct_keys_and_re_incident(self) -> None:
        import os

        from sqlalchemy import inspect, text

        host = os.environ.get("PG_TEST_HOST", "localhost")
        port = os.environ.get("PG_TEST_PORT", "5432")
        user = os.environ.get("PG_TEST_USER", "ensemble")
        password = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")

        if host not in {"localhost", "127.0.0.1", "::1"}:
            pytest.skip(
                f"the scratch-PG leg only runs against a loopback server "
                f"(PG_TEST_HOST={host!r} is not loopback — it may be live)"
            )

        admin_url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/postgres"
        scratch_name = f"ensemble_anomaly_c3_{uuid.uuid4().hex[:12]}"
        assert scratch_name not in {
            "ensemble_prod",
            "ensemble_dev",
            "ensemble_prod_test",
        }
        admin = create_engine(admin_url, pool_pre_ping=True, future=True)
        try:
            with (
                admin.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as conn
            ):
                conn.execute(text(f'CREATE DATABASE "{scratch_name}"'))
        except Exception as exc:  # noqa: BLE001 — skip-cleanly
            pytest.skip(f"no scratch database could be created: {exc}")

        scratch_url = (
            f"postgresql+psycopg://{user}:{password}@{host}:{port}/{scratch_name}"
        )
        engine = create_engine(scratch_url, pool_pre_ping=True, future=True)
        try:
            SQLModel.metadata.create_all(
                engine,
                tables=[AnomalyReport.__table__, AnomalyCluster.__table__],
            )
            assert "anomaly_reports" in inspect(engine).get_table_names()

            async def _scenario() -> None:
                clock = _Clock()
                original_service_iso = anomaly_service._utc_now_iso
                original_models_iso = anomaly_models._utc_now_iso
                anomaly_service._utc_now_iso = clock.iso
                anomaly_models._utc_now_iso = clock.iso
                try:
                    service = _service(engine, chains={"inst-1": []})
                    # pin 1 — same-key writes collapse.
                    first = await _report(service)
                    clock.advance(minutes=5)
                    second = await _report(service)
                    assert first.cluster_id == second.cluster_id
                    assert second.cluster_report_count == 2
                    # pin 2 — distinct keys never collapse.
                    other = await _report(service, category="performance")
                    assert other.cluster_id != first.cluster_id
                    # pin 3 — a re-incident after quiet creates a second
                    # same-key cluster with NO constraint error.
                    clock.advance(hours=2)
                    late = await _report(service)
                    assert late.cluster_id != first.cluster_id
                    with Session(engine) as session:
                        assert (
                            session.get(AnomalyReport, first.report_id).cluster_id
                            == first.cluster_id
                        )
                        keys = session.exec(
                            __import__("sqlmodel").select(AnomalyCluster.cluster_key_hash)
                        ).all()
                        assert len(keys) == 3
                        assert len(set(keys)) == 2
                finally:
                    anomaly_service._utc_now_iso = original_service_iso
                    anomaly_models._utc_now_iso = original_models_iso

            asyncio.run(_scenario())
        finally:
            engine.dispose()
            with (
                admin.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as conn
            ):
                conn.execute(text(f'DROP DATABASE "{scratch_name}"'))
