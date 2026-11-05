"""The ``nest-report-read`` read-tool pack (the nest-anomaly lane).

WHAT IT PROVES
--------------
* the read-path PURITY pin (R8 / M-3c): the tools read through the
  ANOMALY SERVICE — never a raw engine handle, never direct repository
  access, never REST-scraping a door;
* the two-layer gate: the ops trio passes, every other agent is
  refused by the BODY gate (the category gate cannot spell the
  audience — a literal tool name in an allow list bypasses it);
* the refusal ladder's four tokens;
* the six-member bound block on both tools;
* the FRESHEST-FIRST order;
* the DEGRADED-read block (the tools stay PRESENT and refuse LOUDLY
  when the substrate is unmounted);
* the output-budget clamp (M-3d): an oversized body is TRIMMED to the
  byte ceiling and the trim is disclosed;
* **AM-12(a)**: the LIST payload carries NO ``excerpt`` key at all
  (M-3i);
* **AM-12(b)**: a member excerpt appears ONLY behind
  ``include_excerpt=true`` (M-3h);
* **AM-1**: the route and the tools call the SAME projector — the
  route module's source references the service's projector methods
  (single-sourcing: the two transports CANNOT drift).

The legs run against a REAL ``AnomalyService`` over a temporary
SQLite store (the house anomaly-pack pattern) with a seeded cluster
and member reports.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.config import NestReportConfig
from daemon.repositories.anomaly.models import AnomalyCluster, AnomalyReport
from daemon.services.anomaly_service import AnomalyService
from daemon.tools.nest_report_read import create_nest_report_read_tools

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The house refusal envelope (the shape every category uses).
REFUSAL_TEMPLATE = "Error: {label} REFUSED — reason={token}: {message}"


async def _passthrough(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return fn(*args, **kwargs)


class _FakeManager:
    """A manager stub that carries ONLY the anomaly service.

    The stub deliberately has NO ``engine`` attribute: a tool body that
    reached for ``manager.engine`` would raise (the M-3c purity
    mutation target).
    """

    def __init__(self, service: Any) -> None:
        self._anomaly_service = service


@pytest.fixture
def store(tmp_path):
    """A real anomaly store (SQLite, the house anomaly-pack pattern)."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'anomaly_read.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
    )
    return engine


@pytest.fixture
def seeded(store):
    """One OPEN cluster with two members + one CLOSED cluster.

    The stamps are RELATIVE to now (the read's default window is one
    hour, so a hardcoded date would fall out of it).
    """
    from datetime import datetime, timedelta, timezone

    from sqlmodel import Session

    now = datetime.now(timezone.utc)
    fresh = now.isoformat(timespec="microseconds")
    stale = (now - timedelta(days=30)).isoformat(timespec="microseconds")

    with Session(store) as session:
        session.add(
            AnomalyCluster(
                id="cl-1",
                cluster_key_hash="k1",
                tree_root_instance_id="tree-1",
                category="tool-failure",
                first_report_at=fresh,
                last_report_at=fresh,
                report_count=2,
                max_severity="critical",
                board_item_id=None,
                created_at=fresh,
            )
        )
        session.add(
            AnomalyCluster(
                id="cl-2",
                cluster_key_hash="k2",
                tree_root_instance_id="tree-1",
                category="llm-provider",
                first_report_at=stale,
                last_report_at=stale,
                report_count=1,
                max_severity="major",
                board_item_id=None,
                created_at=stale,
            )
        )
        for index, (created, severity) in enumerate(
            (
                (fresh, "critical"),
                (
                    (now - timedelta(minutes=30)).isoformat(timespec="microseconds"),
                    "major",
                ),
            )
        ):
            session.add(
                AnomalyReport(
                    id=f"r-{index}",
                    created_at=created,
                    agent_id="tester",
                    instance_id="i-1",
                    tree_root_instance_id="tree-1",
                    parent_chain=[],
                    parent_chain_agents=[],
                    category="tool-failure",
                    category_raw="tool failure",
                    severity=severity,
                    description=f"report {index}",
                    excerpt=None if index else "EXCERPT-TEXT",
                    cluster_id="cl-1",
                    outcome="accepted",
                )
            )
        session.commit()
    return store


@pytest.fixture
def service(store):
    return AnomalyService(
        store,
        config=NestReportConfig(),
        manager=None,
        offload=_passthrough,
    )


def _tools(manager: Any, agent_id: str = "maintenancer") -> list:
    return create_nest_report_read_tools(
        manager,
        current_instance_id="",
        agent_id=agent_id,
        agent_tag=None,
    )


async def _call(tool: Any, **kwargs: Any) -> dict[str, Any]:
    raw = await tool.ainvoke(kwargs)
    return json.loads(raw)


class TestTheReadPathPurity:
    """R8 — the tools read through the SERVICE (the M-3c mutation
    target: read the engine handle directly ⇒ RED)."""

    def test_the_tool_module_never_reaches_for_an_engine(self) -> None:
        source = (
            REPO_ROOT / "daemon" / "tools" / "nest_report_read.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "manager.engine",
            ".engine",
            "AnomalyRepository",
            "session.exec",
            "httpx",
            "requests.get",
            "aiohttp",
            "urlopen",
        ):
            assert forbidden not in source, forbidden

    def test_the_tool_module_imports_no_repository(self) -> None:
        tree = ast.parse(
            (REPO_ROOT / "daemon" / "tools" / "nest_report_read.py").read_text(
                encoding="utf-8"
            )
        )
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.name)
                if node.module:
                    imported.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
        assert not any("repository" in name for name in imported), sorted(imported)
        assert not any("anomaly_service" in name for name in imported), sorted(imported)

    def test_the_tools_call_the_service_projector(self) -> None:
        source = (
            REPO_ROOT / "daemon" / "tools" / "nest_report_read.py"
        ).read_text(encoding="utf-8")
        assert "service.list_clusters(" in source
        assert "service.list_cluster_reports(" in source


class TestTheTwoLayerGate:
    @pytest.mark.parametrize("agent_id", ["maintenancer", "devops", "wanderer"])
    async def test_the_ops_trio_passes_the_body_gate(
        self, seeded, agent_id: str
    ) -> None:
        service = AnomalyService(
            seeded, config=NestReportConfig(), manager=None, offload=_passthrough
        )
        tools = _tools(_FakeManager(service), agent_id)
        payload = await _call(tools[0])
        assert "groups" in payload, payload
        assert "REFUSED" not in json.dumps(payload)

    @pytest.mark.parametrize("agent_id", ["ari", "worker", "coder", "leader"])
    async def test_every_other_agent_is_refused(self, seeded, agent_id: str) -> None:
        service = AnomalyService(
            seeded, config=NestReportConfig(), manager=None, offload=_passthrough
        )
        tools = _tools(_FakeManager(service), agent_id)
        raw = await tools[0].ainvoke({})
        assert raw.startswith("Error: anomaly_clusters REFUSED — reason=not-authorized")

    async def test_a_blank_agent_gets_the_unknown_agent_token(self) -> None:
        tools = _tools(_FakeManager(None), "")
        raw = await tools[0].ainvoke({})
        assert raw.startswith(
            "Error: anomaly_clusters REFUSED — reason=unknown-agent"
        )

    async def test_authorization_precedes_the_substrate_check(self) -> None:
        """An unauthorized caller must never learn whether the substrate
        is mounted (the refusal ladder's ordering)."""
        tools = _tools(_FakeManager(None), "ari")
        raw = await tools[0].ainvoke({})
        assert "anomaly-unavailable" not in raw
        assert "not-authorized" in raw


class TestTheDegradedRead:
    async def test_the_tools_stay_present_and_refuse_loudly(self) -> None:
        """RULING-1 — a degraded boot leaves the tools PRESENT and
        refusing, with a degraded block that states the scope."""
        tools = _tools(_FakeManager(None))
        assert len(tools) == 2
        payload = await _call(tools[0])
        assert "anomaly-unavailable" in payload["refusal"]
        assert payload["degraded_read"]["degraded"] is True
        assert payload["degraded_read"]["scope"]
        assert payload["degraded_read"]["observed_at_utc"]

    async def test_the_detail_tool_degrades_the_same_way(self) -> None:
        tools = _tools(_FakeManager(None))
        payload = await _call(tools[1], cluster_id="cl-1")
        assert "anomaly-unavailable" in payload["refusal"]
        assert payload["degraded_read"]["degraded"] is True


class TestTheProjection:
    async def test_the_groups_are_the_decision_ready_shape(self, seeded) -> None:
        service = AnomalyService(
            seeded, config=NestReportConfig(), manager=None, offload=_passthrough
        )
        tools = _tools(_FakeManager(service))
        payload = await _call(tools[0])
        group = payload["groups"][0]
        assert group["tree_root_instance_id"] == "tree-1"
        assert group["category"] == "tool-failure"
        assert group["severity"] == "critical"
        assert group["severity_label"] == "critical (peak)"
        assert group["severity_rank"] == 3
        assert group["report_count"] == 2
        assert group["occurrence_count"] == 1
        assert group["cluster_ids"] == ["cl-1"]
        assert group["open"] is True
        assert "board_item_ids" not in group

    async def test_the_bound_block_has_its_six_members(self, seeded) -> None:
        service = AnomalyService(
            seeded, config=NestReportConfig(), manager=None, offload=_passthrough
        )
        tools = _tools(_FakeManager(service))
        payload = await _call(tools[0])
        assert set(payload["bound"]) == {
            "matched_count",
            "returned_count",
            "dropped_count",
            "more_available",
            "truncated",
            "narrow_hint",
        }

    async def test_the_freshness_block_is_server_anchored(self, seeded) -> None:
        service = AnomalyService(
            seeded, config=NestReportConfig(), manager=None, offload=_passthrough
        )
        tools = _tools(_FakeManager(service))
        payload = await _call(tools[0])
        assert payload["freshness"]["window_s"] == 3600
        assert payload["freshness"]["queried_at_utc"].endswith("+00:00")
        assert payload["freshness"]["line"]

    async def test_the_detail_members_are_freshest_first(self, seeded) -> None:
        service = AnomalyService(
            seeded, config=NestReportConfig(), manager=None, offload=_passthrough
        )
        tools = _tools(_FakeManager(service))
        payload = await _call(tools[1], cluster_id="cl-1")
        stamps = [member["created_at"] for member in payload["reports"]]
        assert stamps == sorted(stamps, reverse=True)
        assert payload["cluster_id"] == "cl-1"
        assert payload["severity_label"] == "critical (peak)"
        assert payload["severity_rank"] == 3
        assert payload["open"] is True

    async def test_an_unknown_cluster_is_the_invalid_input_token(self, seeded) -> None:
        service = AnomalyService(
            seeded, config=NestReportConfig(), manager=None, offload=_passthrough
        )
        tools = _tools(_FakeManager(service))
        raw = await tools[1].ainvoke({"cluster_id": "no-such-cluster"})
        assert raw.startswith(
            "Error: anomaly_report_detail REFUSED — reason=invalid-input"
        )


class TestAM12TheExcerpt:
    async def test_the_list_payload_has_no_excerpt_key(self, seeded) -> None:
        """AM-12(a) — the key is ABSENT (not empty, not truncated)."""
        service = AnomalyService(
            seeded, config=NestReportConfig(), manager=None, offload=_passthrough
        )
        tools = _tools(_FakeManager(service))
        payload = await _call(tools[0])
        assert "excerpt" not in json.dumps(payload)

    async def test_the_detail_payload_has_no_excerpt_by_default(self, seeded) -> None:
        """AM-12(b) — the default detail payload carries no excerpt."""
        service = AnomalyService(
            seeded, config=NestReportConfig(), manager=None, offload=_passthrough
        )
        tools = _tools(_FakeManager(service))
        payload = await _call(tools[1], cluster_id="cl-1")
        for member in payload["reports"]:
            assert "excerpt" not in member

    async def test_the_excerpt_appears_only_behind_the_flag(self, seeded) -> None:
        service = AnomalyService(
            seeded, config=NestReportConfig(), manager=None, offload=_passthrough
        )
        tools = _tools(_FakeManager(service))
        payload = await _call(
            tools[1], cluster_id="cl-1", include_excerpt=True
        )
        excerpts = [member.get("excerpt") for member in payload["reports"]]
        assert "EXCERPT-TEXT" in excerpts


class TestTheOutputBudget:
    async def test_an_oversized_body_is_trimmed_and_disclosed(self, seeded) -> None:
        """M-3d — the byte ceiling is enforced on the SHIPPED body (the
        trim fires, the payload shrinks, the trim is disclosed)."""
        service = AnomalyService(
            seeded, config=NestReportConfig(), manager=None, offload=_passthrough
        )

        async def _huge(**_kwargs: Any) -> dict[str, Any]:
            return {
                "groups": [
                    {"tree_root_instance_id": "tree-1", "pad": "x" * 400_000}
                    for _ in range(5)
                ],
                "bound": {
                    "matched_count": 5,
                    "returned_count": 5,
                    "dropped_count": 0,
                    "more_available": False,
                    "truncated": False,
                    "narrow_hint": None,
                },
            }

        service.list_clusters = _huge  # type: ignore[method-assign]
        tools = _tools(_FakeManager(service))
        raw = await tools[0].ainvoke({})
        payload = json.loads(raw)
        from daemon.tools import nest_report_read as nrr

        assert nrr._encoded_size(payload) <= nrr.RESPONSE_BYTE_CEILING
        assert len(payload["groups"]) < 5
        assert payload["bound"]["truncated"] is True
        assert payload["bound"]["narrow_hint"]
        assert "byte_ceiling_caveat" in payload

    async def test_the_trim_helper_shrinks_to_the_ceiling(self) -> None:
        from daemon.tools import nest_report_read as nrr

        payload = {"groups": [{"pad": "x" * 400_000} for _ in range(5)]}
        kept = nrr._trim_to_byte_ceiling(
            payload, key="groups", items=payload["groups"]
        )
        assert len(kept) < 5
        assert (
            nrr._encoded_size({**payload, "groups": kept})
            <= nrr.RESPONSE_BYTE_CEILING - nrr._CAVEAT_HEADROOM_BYTES
        )


class TestAM1TheSameProjector:
    def test_the_service_is_the_single_projection_home(self) -> None:
        """AM-1 — the projector lives in the SERVICE, and the tools call
        it (the service is the ONLY place the projection is built)."""
        from daemon.services import anomaly_service

        assert hasattr(anomaly_service.AnomalyService, "list_clusters")
        assert hasattr(anomaly_service.AnomalyService, "list_cluster_reports")
        source = (
            REPO_ROOT / "daemon" / "tools" / "nest_report_read.py"
        ).read_text(encoding="utf-8")
        assert "service.list_clusters(" in source
        assert "service.list_cluster_reports(" in source

    def test_the_route_calls_the_same_service_projector(self) -> None:
        """AM-1 — the route imports the SAME projector the tool calls
        (single-sourcing: the two transports CANNOT drift).

        The route-side half is asserted HERE once the router exists
        (phase 3's C6 commit) and, unconditionally, by the route pack
        (``tests/unit/test_nest_report_rest_route.py``).
        """
        route = REPO_ROOT / "daemon" / "routers" / "nest_reports.py"
        if not route.exists():
            pytest.skip("the nest-reports router lands in phase 3's C6 commit")
        source = route.read_text(encoding="utf-8")
        assert "list_clusters(" in source
        assert "list_cluster_reports(" in source
        # And the ROUTE must not re-implement the projection.
        for forbidden in ("anomaly_clusters", "SELECT ", "anomaly_reports "):
            assert forbidden not in source, forbidden


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
