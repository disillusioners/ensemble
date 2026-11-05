"""The nest-anomaly CRITICAL → BOARD sink (R3 + AM-13 + AM-12e + AM-7).

Covers the phase-2 board contract (decisions.md R3 + AM-7 + AM-13,
phase2-plan.md "Critical → Board Seam"):

* exactly ONE board item per cluster — the first critical creates it,
  later criticals on the same cluster APPEND a board note;
* the board write runs SERVER-SIDE via ``BoardService`` (agents never
  see a board tool from this lane) INSIDE the same-key stripe — two
  concurrent criticals on a fresh cluster still yield ONE item;
* ``notify_user`` is NEVER called (import-graph + behavioral);
* the title carries the cluster_id and ``critical (peak)`` (AM-7/13);
* the body's first line is the FIXED prefix
  ``auto-filed — unverified agent prose`` (AM-12e);
* ``top_description`` is deterministic (highest severity, tie
  ``created_at ASC, id ASC`` — AM-12e);
* ``created_by_instance`` is the REPORTING instance; ``project_id`` is
  None (the GLOBAL board namespace);
* AM-13 ceilings — beyond 3/tree/hour or 20/hour global the create is
  SUPPRESSED: a note lands on the tree's most recent anomaly item, the
  suppression counter increments and the cluster points at that item.

Mutation legs (phase6 §(b)):

* **M-2e** — make the board create unconditional → RED (2 items for 2
  criticals on one cluster).
* **M-2f** — call ``notify_user`` from the board path → RED (the
  doctrine violation).
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon.config import NestReportConfig
from daemon.models.board import BoardItem, BoardItemTag, BoardNote
from daemon.repositories.anomaly import models as anomaly_models
from daemon.repositories.anomaly.models import (
    AnomalyCluster,
    AnomalyReport,
)
from daemon.repositories.notify_audit.models import NotifyAudit
from daemon.services import anomaly_service
from daemon.services.anomaly_service import AnomalyService
from daemon.services.board_service import BoardService

SERVICE_SOURCE = Path(inspect.getfile(anomaly_service)).read_text()

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
        board_service: BoardService | None = None,
    ) -> None:
        self.checkpointer = type(
            "_Cp", (), {"raw_saver": None}
        )()
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
        self._board_service = board_service


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
    import daemon.services.board_service as board_service_module

    clock = _Clock()
    monkeypatch.setattr(anomaly_service, "_utc_now_iso", clock.iso)
    monkeypatch.setattr(anomaly_models, "_utc_now_iso", clock.iso)
    # the board's stamps share the same deterministic clock (the AM-13
    # hour windows compare board created_at against the service clock).
    monkeypatch.setattr(board_service_module, "_now_iso", clock.iso)
    return clock


def _engine(tmp_path, name: str = "anomaly_board.db"):
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
            NotifyAudit.__table__,
        ],
    )
    return engine


def _service(
    engine,
    config: NestReportConfig | None = None,
    offload=_passthrough,
) -> AnomalyService:
    manager = _FakeManager(
        {"inst-1": ["root-1"], "inst-2": ["root-1"], "other-1": ["root-2"]},
        board_service=BoardService(engine),
    )
    return AnomalyService(
        engine,
        config=config or NestReportConfig(),
        manager=manager,
        offload=offload,
    )


async def _critical(
    service: AnomalyService,
    *,
    instance_id: str = "inst-1",
    agent_id: str = "worker",
    category: str = "tool-failure",
    description: str = "critical problem",
):
    return await service.accept_report(
        agent_id=agent_id,
        instance_id=instance_id,
        category=category,
        description=description,
        severity="critical",
    )


def _board_items(engine) -> list[BoardItem]:
    with Session(engine) as session:
        rows = session.exec(
            __import__("sqlmodel").select(BoardItem)
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


def _notes(engine, item_id: str) -> list[BoardNote]:
    with Session(engine) as session:
        rows = session.exec(
            __import__("sqlmodel")
            .select(BoardNote)
            .where(BoardNote.item_id == item_id)
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


# ── the pins ──────────────────────────────────────────────────────────────


class TestBoardSingleRowPerCluster:
    async def test_two_criticals_on_one_cluster_yield_one_item_and_one_note(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        first = await _critical(service, agent_id="worker")
        clock.advance(minutes=2)
        second = await _critical(service, agent_id="sketcher")
        assert first.cluster_id == second.cluster_id
        items = _board_items(engine)
        assert len(items) == 1, "ONE board item per cluster"
        notes = _notes(engine, items[0].id)
        assert len(notes) == 1, "the repeat critical APPENDS one note"
        assert first.cluster_id in notes[0].text
        assert "(peak)" in notes[0].text
        assert "cluster total=2" in notes[0].text
        with Session(engine) as session:
            cluster = session.get(AnomalyCluster, first.cluster_id)
            assert cluster is not None
            assert cluster.board_item_id == items[0].id

    async def test_the_concurrent_leg_two_simultaneous_criticals_one_item(
        self, tmp_path, clock
    ) -> None:
        """C2-corrected shape — ``ensure_board_surface`` runs INSIDE the
        same-key stripe, so two concurrent criticals on a FRESH cluster
        cannot both create an item."""
        engine = _engine(tmp_path)

        async def _yielding_offload(fn, *args, **kwargs):
            await asyncio.sleep(0)
            return fn(*args, **kwargs)

        service = _service(engine, offload=_yielding_offload)
        await asyncio.gather(
            _critical(service, instance_id="inst-1", agent_id="worker"),
            _critical(service, instance_id="inst-2", agent_id="sketcher"),
        )
        assert len(_board_items(engine)) == 1


class TestTheItemShape:
    async def test_the_title_carries_the_cluster_id_and_peak_severity(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        result = await _critical(service, category="loop-stall")
        items = _board_items(engine)
        assert len(items) == 1
        assert items[0].title == (
            f"[anomaly][critical (peak)] loop-stall — cluster {result.cluster_id}"
        )

    async def test_the_body_carries_the_fixed_prefix_and_top_description(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        # a minor member FIRST (older) so the cluster pre-exists the
        # critical that creates the board item.
        await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="minor-level grumble",
            severity="minor",
        )
        clock.advance(minutes=1)
        await _critical(service, description="the critical failure text")
        items = _board_items(engine)
        assert len(items) == 1
        body = items[0].body_md
        assert body.splitlines()[0] == "auto-filed — unverified agent prose"
        assert "the critical failure text" in body, (
            "AM-12e — the HIGHEST-severity member's description wins"
        )
        assert "minor-level grumble" not in body

    async def test_top_description_breaks_ties_by_created_then_id(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        await _critical(
            service, description="first critical description"
        )
        clock.advance(minutes=1)
        await _critical(
            service, description="second critical description"
        )
        items = _board_items(engine)
        assert len(items) == 1
        # the FIRST member wins the tie (created_at ASC, id ASC).
        assert "first critical description" in items[0].body_md
        assert "second critical description" not in items[0].body_md

    async def test_created_by_instance_is_the_reporting_instance(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        await _critical(service, instance_id="inst-2", agent_id="sketcher")
        items = _board_items(engine)
        assert items[0].created_by_instance == "inst-2"

    async def test_the_item_lives_in_the_global_namespace(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        await _critical(service)
        assert _board_items(engine)[0].project_id is None

    async def test_the_tags_suggest_category_and_severity(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        await _critical(service, category="wedge-suspected")
        with Session(engine) as session:
            tags = sorted(
                session.exec(
                    __import__("sqlmodel")
                    .select(BoardItemTag.tag)
                    .where(BoardItemTag.item_id == _board_items(engine)[0].id)
                ).all()
            )
        assert tags == ["anomaly", "category:wedge-suspected", "severity:critical"]


class TestNotifyNeverCalled:
    def test_the_service_code_references_no_notify_symbol(self) -> None:
        """The import-graph assertion — the anomaly path cannot voice.

        Walks the module's CODE identifiers (docstrings and comments
        are prose, not imports): no ``notify`` symbol may appear as a
        name, attribute, import or string-literal call site.
        """
        import ast

        tree = ast.parse(SERVICE_SOURCE)
        docstring_nodes: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                ):
                    docstring_nodes.add(id(body[0].value))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and "notify" in node.id.lower():
                offenders.append(node.id)
            elif isinstance(node, ast.Attribute) and "notify" in node.attr.lower():
                offenders.append(node.attr)
            elif isinstance(node, ast.alias) and "notify" in (
                (node.name or "") + (node.asname or "")
            ).lower():
                offenders.append(node.name or "")
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstring_nodes
                and "notify" in node.value.lower()
            ):
                offenders.append(node.value)
        assert offenders == [], (
            "the anomaly service must never reference a notify symbol "
            f"(R3 — the board is the ALWAYS surface; notify NEVER): {offenders}"
        )

    async def test_a_critical_report_produces_zero_notify_audit_rows(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        await _critical(service)
        with Session(engine) as session:
            count = len(session.exec(
                __import__("sqlmodel").select(NotifyAudit)
            ).all())
        assert count == 0

    async def test_a_board_less_boot_degrades_without_failing_the_report(
        self, tmp_path, clock
    ) -> None:
        """A missing board seam is a WARNING, never a refusal — the
        report is already accepted and stays accepted."""
        engine = _engine(tmp_path)
        manager = _FakeManager({"inst-1": ["root-1"]}, board_service=None)
        service = AnomalyService(
            engine, config=NestReportConfig(), manager=manager, offload=_passthrough
        )
        result = await _critical(service)
        assert result.outcome == "accepted"


class TestTheAM13Ceilings:
    async def test_the_tree_ceiling_routes_overflow_to_a_note(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        config = NestReportConfig(
            max_board_items_per_tree_root_per_hour=2,
            critical_max_per_agent=50,
        )
        service = _service(engine, config=config)
        # three DISTINCT clusters of ONE tree (distinct categories ⇒
        # distinct keys) inside the hour — distinct stamps so "the
        # tree's most recent item" is unambiguous.
        await _critical(service, category="tool-failure")
        clock.advance(seconds=10)
        await _critical(service, category="performance")
        clock.advance(seconds=10)
        await _critical(service, category="loop-stall")
        items = _board_items(engine)
        assert len(items) == 2, "the third create is SUPPRESSED"
        assert service.board_items_suppressed_total == 1
        # the suppression landed as a NOTE on the tree's most recent
        # anomaly item, and the third cluster points at that item.
        newest = max(items, key=lambda item: item.created_at)
        notes = _notes(engine, newest.id)
        assert len(notes) == 1
        assert "SUPPRESSED (board ceiling)" in notes[0].text
        with Session(engine) as session:
            clusters = session.exec(
                __import__("sqlmodel").select(AnomalyCluster)
            ).all()
        suppressed = [c for c in clusters if c.category == "loop-stall"]
        assert len(suppressed) == 1
        assert suppressed[0].board_item_id == newest.id

    async def test_the_global_ceiling_routes_overflow_to_a_note(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        config = NestReportConfig(
            max_board_items_per_hour_global=1,
            critical_max_per_agent=50,
        )
        service = _service(engine, config=config)
        await _critical(service, instance_id="inst-1", category="tool-failure")
        await _critical(service, instance_id="other-1", category="loop-stall")
        items = _board_items(engine)
        assert len(items) == 1, "the second create is SUPPRESSED globally"
        assert service.board_items_suppressed_total == 1

    async def test_the_ceiling_window_is_one_hour(
        self, tmp_path, clock
    ) -> None:
        engine = _engine(tmp_path)
        config = NestReportConfig(
            max_board_items_per_tree_root_per_hour=1,
            critical_max_per_agent=50,
        )
        service = _service(engine, config=config)
        await _critical(service, category="tool-failure")
        clock.advance(minutes=61)  # the first item aged out of the window
        clock.advance(hours=2)  # and past the cluster window: a fresh cluster
        await _critical(service, category="loop-stall")
        assert len(_board_items(engine)) == 2, (
            "the ceiling only counts items created within the hour"
        )


class TestAM13ShippedCeilingValues:
    """B3 (functional-gate fix cycle) — the SHIPPED ceiling VALUES, pinned.

    The tester planted the stale ``50`` for ``max_board_items_per_hour_global``
    and 89 nodes stayed GREEN: the ceiling MECHANISM was pinned (MUT-B, 2 RED)
    but the shipped VALUES were not — so a value regression (a revert of the
    W-6 correction, 50 -> 20) would ship unpinned. Authority: decisions.md
    AM-13 (fold round 3) — per-tree ceiling default **3**, global ceiling
    default **20** (20/h = 480/day, matching the architect's stated harm
    bound). Precedent form: the w4 knob pin reading
    ``NestReportConfig().resolve_window_seconds`` at the config seam
    (tests/unit/metrics/test_metrics_family_w4.py).
    """

    def test_per_tree_ceiling_ships_3(self) -> None:
        assert NestReportConfig().max_board_items_per_tree_root_per_hour == 3, (
            "AM-13: the shipped per-tree board ceiling must be 3/tree/hour "
            "(decisions.md AM-13, fold round 2)"
        )

    def test_global_ceiling_ships_20(self) -> None:
        assert NestReportConfig().max_board_items_per_hour_global == 20, (
            "AM-13: the shipped global board ceiling must be 20/hour — the "
            "W-6 correction (decisions.md AM-13, fold round 3: 50/h = "
            "1,200/day EXCEEDED the architect's own ~480/day harm bound)"
        )
