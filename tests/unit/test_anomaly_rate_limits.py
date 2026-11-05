"""The nest-anomaly RATE LIMITS — pre-harvest fences (R5b + AM-9).

Covers the phase-2 rate-limit contract (decisions.md R5b + AM-9 +
AM-10 + N3 + N4, phase2-plan.md "Rate Limits"):

* the 5-per-10-min minor+major cap per agent TYPE;
* critical is EXEMPT from the 5/10min but capped at 20/hour;
* the refusal is TYPED (``rate-limited`` via ``AnomalyRefusalError``)
  and maps onto the C2 tool's ``_service_token`` contract;
* the LEDGERED refusal row carries the authored content with ALL
  harvest-derived columns NULL, ``cluster_id`` NULL;
* a refused report harvests NOTHING — no transcript read, no ancestor
  walk (the harvest seams are recorded and asserted untouched);
* refusals NEVER consume budget (``budget-counts-accepted-only``);
* FENCE 1 — the per-agent refusal-ledger cap (beyond it the SAME
  envelope returns with NO ledger row);
* FENCE 3 — the pre-harvest global accepted/hour brake;
* ``unclustered`` rows are EXCLUDED from ``accepted``-filtered counts
  (the AM-5 outcome-token contract, asserted explicitly).

Mutation legs (phase6 §(b)):

* **M-2c** — remove the pre-harvest gate (harvest before the rate
  check) → RED (the harvest seams run on a refused call).
* **M-2d** — drop the critical exemption → RED (the 6th critical is
  refused; the cap no longer matches).
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon.config import NestReportConfig
from daemon.repositories.anomaly import models as anomaly_models
from daemon.repositories.anomaly.models import (
    AnomalyCluster,
    AnomalyReport,
)
from daemon.services import anomaly_service
from daemon.services.anomaly_service import (
    AnomalyRefusalError,
    AnomalyService,
)

# ── the shared fakes (each pack is standalone per house convention) ───────


class _StubInstanceRepo:
    def __init__(self, chains: dict[str, list[str]] | None = None) -> None:
        self._chains = chains or {}
        self.calls: list[str] = []

    def get_ancestor_ids(self, instance_id: str) -> list[str]:
        self.calls.append(f"ancestor:{instance_id}")
        return list(self._chains.get(instance_id, []))

    def get_agent_ids_for(self, ids: list[str]) -> dict[str, str]:
        return {item: "agent" for item in ids}


class _StubTaskRepo:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def find_running_by_instance(self, instance_id: str):  # noqa: ANN001
        self.calls.append(f"task:{instance_id}")
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


class _RecordingRawSaver:
    def __init__(self, messages: list[object]) -> None:
        self._messages = messages
        self.calls: list[dict] = []

    async def aget(self, config: dict) -> dict:
        self.calls.append(config)
        return {"channel_values": {"messages": list(self._messages)}}


class _FakeManager:
    def __init__(self, chains: dict[str, list[str]] | None = None) -> None:
        self.checkpointer = type(
            "_Cp",
            (),
            {"raw_saver": _RecordingRawSaver([])},
        )()
        self.metrics_query_provider = None
        self.instance_repo = _StubInstanceRepo(chains)
        self._instance_repository = self.instance_repo
        self.task_repo = _StubTaskRepo()
        self._task_repo = self.task_repo
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
    def __init__(self, start: datetime | None = None) -> None:
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


def _engine(tmp_path, name: str = "anomaly_rate.db"):
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
    manager: _FakeManager | None = None,
    config: NestReportConfig | None = None,
) -> AnomalyService:
    return AnomalyService(
        engine,
        config=config or NestReportConfig(),
        manager=manager or _FakeManager({"inst-1": ["root-1"]}),
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


def _count(engine, **filters) -> int:
    from sqlmodel import select as sm_select
    from sqlalchemy import func

    with Session(engine) as session:
        stmt = sm_select(func.count()).select_from(AnomalyReport)
        column_map = {
            "agent_id": AnomalyReport.agent_id,
            "outcome": AnomalyReport.outcome,
            "severity": AnomalyReport.severity,
        }
        for key, value in filters.items():
            stmt = stmt.where(column_map[key] == value)
        return int(session.exec(stmt).one())


# ── the pins ──────────────────────────────────────────────────────────────


class TestRateLimitRefusal:
    async def test_the_sixth_minor_within_ten_minutes_is_refused(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        for _ in range(5):
            await _accept(service, severity="minor")
        with pytest.raises(AnomalyRefusalError) as caught:
            await _accept(service, severity="minor")
        assert caught.value.token == "rate-limited"

    async def test_the_refusal_row_is_ledgered_with_harvest_columns_null(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        for _ in range(5):
            await _accept(service)
        with pytest.raises(AnomalyRefusalError):
            await _accept(service)
        with Session(engine) as session:
            refused = session.exec(
                __import__("sqlmodel")
                .select(AnomalyReport)
                .where(AnomalyReport.outcome == "rate_limited")
            ).first()
            assert refused is not None
            # authored content IS stored (the spam signal).
            assert refused.agent_id == "worker"
            assert refused.instance_id == "inst-1"
            assert refused.category == "tool-failure"
            assert refused.severity == "major"
            assert refused.description
            assert refused.refusal_reason.startswith("rate-limited: ")
            # ALL harvest-derived columns are NULL — a refused report
            # harvests NOTHING.
            assert refused.tree_root_instance_id is None
            assert refused.parent_chain == []
            assert refused.parent_chain_agents == []
            assert refused.excerpt is None
            assert refused.excerpt_sha256 is None
            assert refused.excerpt_length is None
            assert refused.job_id is None
            assert refused.mission_id is None
            assert refused.dispatch_chain is None
            assert refused.boot_epoch is None
            assert refused.registry_summary_sha256 is None
            assert refused.wedge_capture_ids is None
            assert refused.cluster_id is None

    async def test_a_refused_report_harvests_nothing(
        self, tmp_path, clock
    ) -> None:
        """R5b — no transcript read, no ancestor walk, no task-row
        lookup on the refused call (the M-2c pin)."""
        engine = _engine(tmp_path)
        manager = _FakeManager({"inst-1": ["root-1"]})
        service = _service(engine, manager)
        for _ in range(5):
            await _accept(service)
        raw_saver = manager.checkpointer.raw_saver
        instance_repo = manager.instance_repo
        task_repo = manager.task_repo
        transcript_calls = len(raw_saver.calls)
        ancestor_calls = len(instance_repo.calls)
        task_calls = len(task_repo.calls)
        with pytest.raises(AnomalyRefusalError):
            await _accept(service)
        assert len(raw_saver.calls) == transcript_calls, (
            "a refused report reads NO transcript"
        )
        assert len(instance_repo.calls) == ancestor_calls, (
            "a refused report runs NO ancestor walk"
        )
        assert len(task_repo.calls) == task_calls, (
            "a refused report runs NO task-row lookup"
        )

    async def test_cluster_counts_are_unchanged_on_refusal(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        for _ in range(5):
            await _accept(service)
        before = _count(engine, outcome="accepted")
        clusters_before = len(
            Session(engine)
            .exec(__import__("sqlmodel").select(AnomalyCluster))
            .all()
        )
        for _ in range(3):
            with pytest.raises(AnomalyRefusalError):
                await _accept(service)
        assert _count(engine, outcome="accepted") == before
        with Session(engine) as session:
            after = session.exec(
                __import__("sqlmodel").select(AnomalyCluster)
            ).all()
        assert len(after) == clusters_before
        for cluster in after:
            assert cluster.report_count == 5

    async def test_the_refusal_maps_onto_the_c2_service_token_contract(
        self, tmp_path, clock
    ) -> None:
        """The C2 forward contract: an exception carrying ``.token`` in
        the closed pair maps to the ladder rungs."""
        from daemon.tools.nest_report import _service_token

        assert _service_token(AnomalyRefusalError("rate-limited", "x")) == "rate-limited"
        assert (
            _service_token(
                AnomalyRefusalError("capacity-exhausted", "x")
            )
            == "capacity-exhausted"
        )
        assert _service_token(RuntimeError("plain")) is None


class TestCriticalExemption:
    async def test_criticals_bypass_the_five_per_ten_minutes(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        # 6 criticals INSIDE 10 minutes — all ACCEPTED (the exemption).
        clock.advance(minutes=1)
        for index in range(6):
            result = await _accept(service, severity="critical")
            assert result.outcome == "accepted"
            clock.advance(seconds=30)
        assert _count(engine, outcome="accepted", severity="critical") == 6

    async def test_the_twenty_first_critical_in_the_hour_is_refused(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        for index in range(20):
            await _accept(service, severity="critical", agent_id="critic")
            clock.advance(seconds=30)  # 20 x 30s — inside one hour
        with pytest.raises(AnomalyRefusalError) as caught:
            await _accept(service, severity="critical", agent_id="critic")
        assert caught.value.token == "rate-limited"
        assert "20/20" in (caught.value.detail or "")

    async def test_mixed_severities_share_the_minor_major_budget(
        self, tmp_path, clock
    ) -> None:
        """The minor/major counting query counts EVERY accepted row in
        the window (no severity filter) — 3 minors + 2 criticals spend
        the whole 5/10min budget for this agent type."""
        engine = _engine(tmp_path)
        service = _service(engine)
        for severity in ("minor", "minor", "minor", "critical", "critical"):
            await _accept(service, severity=severity)
        with pytest.raises(AnomalyRefusalError):
            await _accept(service, severity="minor")


class TestBudgetCountsAcceptedOnly:
    async def test_a_refusal_never_extends_the_window(
        self, tmp_path, clock
    ) -> None:
        """Refused-then-accepted inside ONE window: the refusal did not
        consume budget (5 accepted + 1 refused + 1 accepted = 6 accepted
        rows would be over the cap if refusals counted)."""
        engine = _engine(tmp_path)
        service = _service(engine)
        for _ in range(5):
            await _accept(service)
        with pytest.raises(AnomalyRefusalError):
            await _accept(service)
        assert _count(engine, outcome="rate_limited") == 1
        # advance PAST the refusal but stay inside nothing — the next
        # accept proves the budget is still 5-accepted (the ledger row
        # did not extend it): 5 accepted already ⇒ still refused.
        clock.advance(minutes=1)
        with pytest.raises(AnomalyRefusalError):
            await _accept(service)
        # after the WINDOW passes, the budget resets.
        clock.advance(minutes=10)
        result = await _accept(service)
        assert result.outcome == "accepted"

    async def test_unclustered_rows_are_excluded_from_accepted_counts(
        self, tmp_path, clock
    ) -> None:
        """AM-5's outcome-token contract: ``unclustered`` is a DISTINCT
        outcome — an ``accepted``-filtered count never sees it."""
        engine = _engine(tmp_path)
        service = _service(engine)
        # fabricate an unclustered row directly (the terminal state).
        with Session(engine) as session:
            session.add(
                AnomalyReport(
                    agent_id="worker",
                    instance_id="inst-1",
                    category="tool-failure",
                    category_raw="tool-failure",
                    severity="minor",
                    description="orphaned live-failure row",
                    outcome="unclustered",
                    cluster_id=None,
                )
            )
            session.commit()
        config = NestReportConfig(rate_max_per_agent=2)
        service = _service(engine, config=config)
        for _ in range(2):
            await _accept(service, severity="minor")
        # the unclustered row did NOT consume budget: a third minor is
        # refused at 2/2 (not 3/2).
        with pytest.raises(AnomalyRefusalError) as caught:
            await _accept(service, severity="minor")
        assert "2/2" in (caught.value.detail or "")


class TestTheRefusalLedgerCap:
    async def test_beyond_the_cap_the_same_envelope_returns_with_no_row(
        self, tmp_path, clock
    ) -> None:
        """FENCE 1 — at most ``refusal_ledger_max_per_agent`` (10)
        ledger rows per agent per window; every call still refuses."""
        engine = _engine(tmp_path)
        service = _service(engine)
        for _ in range(5):
            await _accept(service)
        for _ in range(50):
            with pytest.raises(AnomalyRefusalError) as caught:
                await _accept(service)
            assert caught.value.token == "rate-limited"
        assert _count(engine, outcome="rate_limited") == 10


class TestFenceThreeGlobalHourBrake:
    async def test_the_global_accepted_per_hour_brake_fires_pre_harvest(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        config = NestReportConfig(
            max_accepted_per_hour_global=3,
            rate_max_per_agent=50,
            critical_max_per_agent=50,
        )
        service = _service(engine, config=config)
        for _ in range(3):
            await _accept(service, agent_id=f"agent-{_}")
        with pytest.raises(AnomalyRefusalError) as caught:
            await _accept(service, agent_id="agent-new")
        assert caught.value.token == "capacity-exhausted"
        assert service.capacity_exhausted_total == 1
        assert _count(engine) == 3, "no row is written on the brake"


# ── the fence-shape contract (F-4) ─────────────────────────────────────────
#
# The F-4 sweep classified EVERY capacity/rate fence in the anomaly
# service + repository as either a TOTAL-row count (accepted + refused)
# or an ACCEPTED-only count (refusals never consume budget).  Each one
# matches its design intent:
#
#   FENCE 1 (refusal-ledger cap)      -> ACCEPTED-only? NO — refusal-only
#   FENCE 2 (global row ceiling)       -> TOTAL   (the F-1 correction)
#   FENCE 3 (global accepted/hour)     -> ACCEPTED-only (an accepted brake)
#   the per-agent budgets (5/10min, 20/hour) -> ACCEPTED-only (by design)
#   the per-tree cap (a SHED, never a refusal) -> TOTAL
#   the pre-insert global ceiling      -> TOTAL
#
# The trap this pin closes: ``count_reports`` DEFAULTS to
# ``outcome='accepted'``, so a call site that forgets the keyword
# silently becomes an ACCEPTED-only count — the exact shape the sweep
# hunted for (a TOTAL fence written as an ACCEPTED-only one).  Every
# call site must therefore STATE its predicate.


class TestTheFenceShapeContract:
    """F-4 — no capacity/rate count may INHERIT its predicate."""

    _SOURCES = (
        Path(__file__).resolve().parents[2]
        / "daemon"
        / "services"
        / "anomaly_service.py",
        Path(__file__).resolve().parents[2]
        / "daemon"
        / "repositories"
        / "anomaly"
        / "repository.py",
    )

    def test_every_count_reports_call_site_states_its_outcome_predicate(
        self,
    ) -> None:
        sites: list[str] = []
        for path in self._SOURCES:
            assert path.exists(), path
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                # (a) a DIRECT call — self.count_reports(...)
                if isinstance(func, ast.Attribute) and func.attr == "count_reports":
                    sites.append(self._assert_outcome(path, node))
                    continue
                # (b) an OFFLOADED reference — the fence's count is
                #     handed to the offload seam, so the predicate
                #     keyword rides the SAME call.
                for arg in node.args:
                    if (
                        isinstance(arg, ast.Attribute)
                        and arg.attr == "count_reports"
                    ):
                        sites.append(self._assert_outcome(path, node))
        assert len(sites) >= 9, (
            f"the fence census found only {len(sites)} count_reports call "
            f"sites ({sites}) — a fence site lost its coverage"
        )

    @staticmethod
    def _assert_outcome(path: Path, call: ast.Call) -> str:
        keywords = {kw.arg for kw in call.keywords if kw.arg}
        assert "outcome" in keywords, (
            f"{path.name}:{call.lineno} — count_reports without an "
            "explicit outcome= inherits the ACCEPTED-only default; a "
            "capacity fence must STATE whether it counts TOTAL rows or "
            "ACCEPTED rows only"
        )
        return f"{path.name}:{call.lineno}"
