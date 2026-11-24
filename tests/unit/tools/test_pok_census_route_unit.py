"""POK CENSUS + ROUTING pack (Wave 2 Phase 9) — the census read
surface, the refusal transition map, the destination grant-check and
the zero-silent-drop parking seam.

Legs: the census tool is CURATOR-ONLY and READ-ONLY (T9.1); the
per-lane refusal transition map names a destination for every family
and every content class (T9.2); the rejection envelope carries the
route + the ``destination_available`` grant-check (T9.3); a refused
write whose destination the caller cannot reach is PARKED, not
dropped (T9.4); no other grant pin asserts the grant set (T9.7 — the
grep is the leg); and the exercises never touch a live store (T9.8).

No LLM, no network, no live store — a scratch in-memory SQLite engine
and a deterministic judge stub.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.constants as constants
import daemon.repositories.pok.models  # noqa: F401
from daemon.repositories.pok.models import (
    POKWriteGateLedger,
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.repositories.pok.repository import POKRepository
from daemon.services.pok_gate import GateVerdict
from daemon.tools import pok as pok_tools
from daemon.tools.pok import (
    CONTENT_CLASS_ROUTES,
    PARKING_GATE_ID_PREFIX,
    REFUSAL_FAMILY_CLASSES,
    create_pok_tools,
    destination_available,
    parking_id,
    route_for_family,
)

PROJECT_ID = "p1"


@pytest.fixture()
def tool_env(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
            POKWriteGateLedger.__table__,
        ],
    )

    class FakeProjectRepo:
        def get(self, pid):
            if pid == "default-x":
                return SimpleNamespace(name="__system_default__")
            return SimpleNamespace(name="proj-one")

    def make_repo(project_id, agent_id):
        class FakeRepo:
            def __init__(self, project_repo):
                self._project_repository = project_repo

            def get(self, iid):
                return SimpleNamespace(project_id=project_id, agent_id=agent_id)

        return FakeRepo(FakeProjectRepo())

    saved = constants.SYSTEM_DEFAULT_PROJECT_ID
    yield SimpleNamespace(
        engine=engine,
        make_repo=make_repo,
        project_repo=FakeProjectRepo(),
        saved_default=saved,
    )
    constants.SYSTEM_DEFAULT_PROJECT_ID = saved


def _manager(tool_env, project_id=PROJECT_ID, agent_id="developer"):
    return SimpleNamespace(
        _engine=tool_env.engine,
        _instance_repository=tool_env.make_repo(project_id, agent_id),
        _project_repository=tool_env.project_repo,
    )


def _tools(tool_env, agent_id="developer"):
    return create_pok_tools(
        _manager(tool_env, PROJECT_ID, agent_id),
        current_instance_id="inst-1",
        agent_id=agent_id,
    )


def _census(tool_env):
    return _tools(tool_env, agent_id="leader")[-1]


def _install_judge(monkeypatch, verdict: GateVerdict):
    def _fake(**kwargs):
        return verdict

    monkeypatch.setattr(pok_tools, "judge_pok_write", _fake)


def _enforce(monkeypatch, on: bool = True):
    monkeypatch.setattr(pok_tools, "read_gate_switches", lambda engine: (on, False))


def _ledger(tool_env):
    return POKRepository(tool_env.engine).query_ledger(PROJECT_ID, limit=None)


# ═══════════════════════════════════════════════════════════════════════════
# T9.1 — the census read surface
# ═══════════════════════════════════════════════════════════════════════════


class TestCensusSurface:
    def test_the_census_is_curator_only(self, tool_env):
        tools = _tools(tool_env, agent_id="developer")
        out = tools[-1].invoke({})
        assert out["code"] == "pok_curator_required"

    def test_a_curator_gets_the_census(self, tool_env):
        out = _census(tool_env).invoke({})
        assert "error" not in out
        assert out["active_count"] == 0
        assert out["snapshot_at"]
        assert out["soft_max"] is None
        assert out["tightening"]["tight"] is False
        assert "by_family" in out and "by_author" in out

    def test_the_census_is_read_only(self, tool_env):
        out = _census(tool_env).invoke({})
        assert out["active_count"] == 0
        assert _ledger(tool_env) == []

    def test_the_census_refuses_the_default_project(self, tool_env):
        tools = create_pok_tools(
            _manager(tool_env, "default-x", "leader"),
            current_instance_id="inst-1",
            agent_id="leader",
        )
        out = tools[-1].invoke({})
        assert out["code"] == "pok_default_project"

    def test_the_census_is_registered_under_the_pok_category(self):
        assert pok_tools.CATEGORY_NAME == "pok"
        source = inspect.getsource(pok_tools)
        assert "def pok_census(" in source


# ═══════════════════════════════════════════════════════════════════════════
# T9.2 — the per-lane refusal transition map
# ═══════════════════════════════════════════════════════════════════════════


class TestTransitionMap:
    def test_every_content_class_names_a_destination(self):
        assert set(CONTENT_CLASS_ROUTES) == {
            "knowledge",
            "operational",
            "governance",
            "episode",
        }
        for content_class, route in CONTENT_CLASS_ROUTES.items():
            assert route["route"], content_class
            assert route["destination"], content_class
            assert route["categories"], content_class
            assert route["instruction"], content_class

    def test_the_class_destinations_are_the_documented_ones(self):
        assert CONTENT_CLASS_ROUTES["knowledge"]["categories"] == ("rag",)
        assert CONTENT_CLASS_ROUTES["operational"]["categories"] == ("pok",)
        assert "critical_notes" in CONTENT_CLASS_ROUTES["governance"]["categories"]
        assert "board" in CONTENT_CLASS_ROUTES["governance"]["categories"]
        assert "board" in CONTENT_CLASS_ROUTES["episode"]["categories"]
        assert "shared_meta_kv" in CONTENT_CLASS_ROUTES["episode"]["categories"]

    def test_every_gate_family_routes_to_a_class(self):
        for family in ("boundary", "reason", "durability", "redundancy", "scope"):
            route = route_for_family(family)
            assert route["content_class"] in CONTENT_CLASS_ROUTES
            assert route["destination"]
            assert route["instruction"]
        assert set(REFUSAL_FAMILY_CLASSES) == {
            "boundary",
            "reason",
            "durability",
            "redundancy",
            "scope",
        }

    def test_an_unknown_family_still_routes(self):
        route = route_for_family("not-a-family")
        assert route["content_class"] == "operational"
        assert route["destination"]


# ═══════════════════════════════════════════════════════════════════════════
# T9.3 — the destination grant-check
# ═══════════════════════════════════════════════════════════════════════════


class TestDestinationAvailability:
    def test_an_unrestricted_agent_reaches_everything(self, monkeypatch):
        monkeypatch.setattr(pok_tools, "_agent_tool_grants", lambda agent_id: None)
        assert destination_available("anyone", ("rag",)) is True

    def test_a_granted_destination_is_available(self, monkeypatch):
        monkeypatch.setattr(
            pok_tools, "_agent_tool_grants", lambda agent_id: ["pok", "board"]
        )
        assert destination_available("a", ("pok",)) is True
        assert destination_available("a", ("board", "shared_meta_kv")) is True
        assert destination_available("a", ("rag",)) is False

    def test_a_degraded_grant_read_falls_back_to_available(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("registry down")

        monkeypatch.setattr("daemon.registry.get_registry", _boom)
        # The grant read swallows the failure and returns None
        # (unrestricted) — the envelope never blocks on a degraded read.
        assert pok_tools._agent_tool_grants("a") is None
        assert destination_available("a", ("rag",)) is True


# ═══════════════════════════════════════════════════════════════════════════
# T9.4 — the zero-silent-drop parking seam
# ═══════════════════════════════════════════════════════════════════════════


class TestParkingSeam:
    def test_the_parking_id_is_stable_per_content(self):
        first = parking_id("p1", "slug-a", "body", "reason")
        second = parking_id("p1", "slug-a", "body", "reason")
        other = parking_id("p1", "slug-a", "different body", "reason")
        assert first == second
        assert first != other
        assert first.startswith(PARKING_GATE_ID_PREFIX)

    def test_a_rejected_write_carries_the_route_and_the_availability(
        self, tool_env, monkeypatch
    ):
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch, GateVerdict(verdict="REJECT", family="boundary", gate_id="g-1")
        )
        monkeypatch.setattr(
            pok_tools, "_agent_tool_grants", lambda agent_id: ["pok"]
        )
        out = _tools(tool_env)[0].invoke(
            {"slug": "fact-01", "body": "a general fact", "reason": "because"}
        )
        assert out["code"] == "pok_gate_reject"
        assert out["route"] == "rag"
        assert out["destination"]
        assert out["content_class"] == "knowledge"
        assert out["instruction"]
        assert out["destination_available"] is False
        # The content was PARKED — never dropped.
        assert out["parked"] is True
        assert out["parking_id"].startswith(PARKING_GATE_ID_PREFIX)
        parked = [
            r
            for r in _ledger(tool_env)
            if (r.gate_id or "").startswith(PARKING_GATE_ID_PREFIX)
        ]
        assert len(parked) == 1
        assert "PARKED" in (parked[0].judge_explanation or "")
        assert parked[0].basis == "gate-rejection"
        assert parked[0].executed is False

    def test_a_reachable_destination_is_not_parked(self, tool_env, monkeypatch):
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch, GateVerdict(verdict="REJECT", family="boundary", gate_id="g-2")
        )
        monkeypatch.setattr(
            pok_tools, "_agent_tool_grants", lambda agent_id: ["pok", "rag"]
        )
        out = _tools(tool_env)[0].invoke(
            {"slug": "fact-02", "body": "a general fact", "reason": "because"}
        )
        assert out["destination_available"] is True
        assert out["parked"] is False
        parked = [
            r
            for r in _ledger(tool_env)
            if (r.gate_id or "").startswith(PARKING_GATE_ID_PREFIX)
        ]
        assert parked == []

    def test_a_shadow_verdict_never_parks(self, tool_env, monkeypatch):
        """SHADOW refuses nothing — so nothing can be dropped."""
        _enforce(monkeypatch, on=False)
        # SHADOW: the verdict is ADVISORY (advisory=True) — it refuses
        # no write, so no content can be dropped.
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="REJECT",
                family="boundary",
                gate_id="g-3",
                advisory=True,
            ),
        )
        monkeypatch.setattr(pok_tools, "_agent_tool_grants", lambda agent_id: ["pok"])
        out = _tools(tool_env)[0].invoke(
            {"slug": "fact-03", "body": "a general fact", "reason": "because"}
        )
        assert out["action"] == "created"
        parked = [
            r
            for r in _ledger(tool_env)
            if (r.gate_id or "").startswith(PARKING_GATE_ID_PREFIX)
        ]
        assert parked == []


# ═══════════════════════════════════════════════════════════════════════════
# T9.7 — no other grant pin asserts the grant set
# ═══════════════════════════════════════════════════════════════════════════


class TestGrantPinSurface:
    def test_only_the_grant_pin_file_asserts_the_grant_set(self):
        """The grep leg: ONE file asserts the GRANT SET (the census
        count + the kb-writer ABSENCE pin). No other pin may re-assert
        the grant census.

        The kb-writer RESOLVED-TOOL-SET pin (``tests/unit/
        test_kb_writer_tools.py``) was re-pinned by this lane — it
        still held the pre-rider set (with ``pok``) and was RED after
        the Phase-4 rider removed the grant. It pins the resolved tool
        set, not the grant census.
        """
        import pathlib

        census_pins = []
        this_file = pathlib.Path(__file__).resolve()
        for path in pathlib.Path("tests").rglob("*.py"):
            if path.resolve() == this_file:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            # The POK grant census is the file that declares the
            # 'pok' CATEGORY and walks the agents' allow lists.
            if 'CATEGORY = "pok"' in text:
                census_pins.append(str(path))
        assert census_pins == ["tests/unit/test_pok_grant_pins.py"], census_pins

    def test_the_resolved_tool_set_pin_is_re_pinned_to_the_post_rider_set(self):
        """The stale pin this lane found and fixed (T9.7's finding)."""
        import pathlib

        text = pathlib.Path("tests/unit/test_kb_writer_tools.py").read_text(
            encoding="utf-8"
        )
        # The pre-rider set literal (with the "pok" grant) is gone.
        assert '"report_anomaly", "pok"' not in text
        assert "report_anomaly" in text

    def test_the_census_tool_needs_no_grant_change(self):
        """The census tool lives in the existing 'pok' category — the
        curators already hold it, so NO meta.json changes."""
        import json
        import pathlib

        for agent in ("leader", "project-manager"):
            meta = json.loads(
                pathlib.Path(f"agents/{agent}/meta.json").read_text(encoding="utf-8")
            )
            assert "pok" in meta["tools"]["allow"], agent
