"""POK tools unit pack (Phase 1 T4 — the tool surface).

Legs: the three tools exist and register under the 'pok' category;
SERVER-SIDE project scoping (no project_id tool argument — the
calling instance's row resolves it; an unlinked instance refuses);
the two-layer gate (category grant + in-body factory-captured
agent_id role gates); AC10 at the tool level (default-project typed
refusals ×3); the typed echo taxonomy (POKError codes surface as
``code`` keys, never raw tracebacks); the write path round-trip
through the real service (fence/budget echo).

The engine is a StaticPool in-memory SQLite (the seam's to_thread
discipline — same connection across threads).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.engine import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.constants as constants
import daemon.repositories.pok.models  # noqa: F401
from daemon.repositories.pok.models import (
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.tools._tool_registry import CATEGORY_MODULES
from daemon.tools.pok import CATEGORY_NAME, create_pok_tools


@pytest.fixture()
def tool_env(monkeypatch):
    from sqlalchemy.pool import StaticPool as SP  # noqa: F401 (re-import guard)

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
        ],
    )

    class FakeProjectRepo:
        def get(self, pid):
            if pid == "default-x":
                return SimpleNamespace(name="__system_default__")
            return SimpleNamespace(name="proj-one")

    def make_repo(project_id, agent_id):
        class FakeRepo:
            def get(self, iid):
                return SimpleNamespace(project_id=project_id, agent_id=agent_id)

        return FakeRepo()

    saved = constants.SYSTEM_DEFAULT_PROJECT_ID
    state = SimpleNamespace(
        engine=engine,
        make_repo=make_repo,
        project_repo=FakeProjectRepo(),
        saved_default=saved,
    )
    yield state
    constants.SYSTEM_DEFAULT_PROJECT_ID = saved


def _manager(tool_env, project_id, agent_id):
    return SimpleNamespace(
        _engine=tool_env.engine,
        _instance_repository=tool_env.make_repo(project_id, agent_id),
        _project_repository=tool_env.project_repo,
    )


class TestRegistrationSurface:
    def test_three_tools_created(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, "p1", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        assert [t.name for t in tools] == ["pok_upsert", "pok_query", "pok_curate"]

    def test_category_registered_in_modules(self) -> None:
        assert CATEGORY_NAME == "pok"
        assert CATEGORY_MODULES.get("pok") == "daemon.tools.pok"
        assert "pok" not in (
            __import__("daemon.tools._tool_registry", fromlist=["PRIVILEGED_TOOL_CATEGORIES"]).PRIVILEGED_TOOL_CATEGORIES
        )


class TestServerSideScoping:
    def test_upsert_resolves_the_callers_project(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, "p1", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        out = tools[0].invoke({"slug": "sc-01-a", "body": "scoped fact"})
        assert out["action"] == "created"
        # the row landed on the CALLER's project (no project_id arg exists)
        from daemon.services.pok_service import POKService

        rows = POKService(tool_env.engine)._repo.list_entries(
            "p1", statuses=("active",)
        )
        assert [r.slug for r in rows] == ["sc-01-a"]

    def test_unlinked_instance_refused(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, None, "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        for tool in tools:
            kwargs = {"slug": "x"} if tool.name != "pok_query" else {}
            if tool.name == "pok_query":
                out = tool.invoke({})
            else:
                body = {"body": "x"} if tool.name == "pok_upsert" else {}
                out = tool.invoke({"slug": "x", **body})
            assert out.get("code") == "pok_no_project", (
                f"{tool.name}: {out}"
            )


class TestAc10DefaultProjectToolLevel:
    @pytest.mark.parametrize("tool_idx", [0, 1, 2])
    def test_default_project_typed_refusals(self, tool_env, tool_idx) -> None:
        constants.SYSTEM_DEFAULT_PROJECT_ID = "default-x"
        tools = create_pok_tools(
            _manager(tool_env, "default-x", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        tool = tools[tool_idx]
        if tool.name == "pok_upsert":
            out = tool.invoke({"slug": "x", "body": "fact"})
        elif tool.name == "pok_query":
            out = tool.invoke({})
        else:
            out = tool.invoke({"slug": "x"})
        assert out.get("code") == "pok_default_project"
        assert "error" in out


class TestTwoLayerGate:
    def test_any_agent_writes(self, tool_env) -> None:
        for agent in ("developer", "worker", "tester", "ari"):
            tools = create_pok_tools(
                _manager(tool_env, "p1", agent),
                current_instance_id="inst-1",
                agent_id=agent,
            )
            out = tools[0].invoke(
                {"slug": f"w-{agent}", "body": f"written by {agent}"}
            )
            assert out["action"] == "created", out

    def test_query_is_universal(self, tool_env) -> None:
        """AC23 tool-level: every agent's pok_query sees every entry —
        the audience dimension never filters a read."""
        w = create_pok_tools(
            _manager(tool_env, "p1", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        w[0].invoke({"slug": "uni-op-a", "body": "operation fact"})
        for agent in ("worker", "developer", "ari"):
            tools = create_pok_tools(
                _manager(tool_env, "p1", agent),
                current_instance_id="inst-1",
                agent_id=agent,
            )
            out = tools[1].invoke({})
            slugs = {e["slug"] for e in out["entries"]}
            assert "uni-op-a" in slugs, f"{agent} cannot read: {slugs}"

    def test_curate_refused_for_non_curators(self, tool_env) -> None:
        for agent in ("developer", "worker", "ari"):
            tools = create_pok_tools(
                _manager(tool_env, "p1", agent),
                current_instance_id="inst-1",
                agent_id=agent,
            )
            out = tools[2].invoke({"slug": "x", "pinned": True})
            assert out.get("code") == "pok_curator_required"

    def test_curate_allowed_for_leader_and_pm(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, "p1", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        tools[0].invoke({"slug": "cur-a", "body": "curate me"})
        for agent in ("leader", "project-manager"):
            tools = create_pok_tools(
                _manager(tool_env, "p1", agent),
                current_instance_id="inst-1",
                agent_id=agent,
            )
            out = tools[2].invoke({"slug": "cur-a", "pinned": True})
            assert out.get("pinned") is True, out

    def test_attribution_is_the_factory_captured_id(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, "p1", "worker"),
            current_instance_id="inst-1",
            agent_id="worker",
        )
        out = tools[0].invoke({"slug": "attr-a", "body": "who wrote this"})
        assert out["created_by_agent"] == "worker"


class TestTypedEchoTaxonomy:
    def test_fence_refusal_surfaces_as_code(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, "p1", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        out = tools[0].invoke({"slug": "fence-a", "body": "two\nlines"})
        assert out.get("code") == "pok_body_structural_token"
        assert "error" in out

    def test_budget_refusal_surfaces_as_code(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, "p1", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        out = tools[0].invoke({"slug": "fence-b", "body": ">" * 200})
        assert out.get("code") == "pok_body_oversize"

    def test_store_unavailable_refuses_never_raises(self, tool_env) -> None:
        """A degraded boot (no engine handle) yields typed refusals —
        the tools are PRESENT and refusing, never silently vanished."""
        mgr = SimpleNamespace(_engine=None, _instance_repository=None)
        tools = create_pok_tools(mgr, current_instance_id="inst-1", agent_id="d")
        assert tools[0].invoke({"slug": "x", "body": "y"}).get(
            "code"
        ) == "pok_store_unavailable"
        assert tools[1].invoke({}).get("code") == "pok_store_unavailable"
        assert tools[2].invoke({"slug": "x"}).get("code") == "pok_store_unavailable"

    def test_family_full_and_pin_cap_codes(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, "p1", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        for i in (1, 2, 3):
            tools[0].invoke(
                {"slug": f"fam-{i:02d}-m", "body": f"member {i}"}
            )
        out = tools[0].invoke({"slug": "fam-04-m", "body": "the 4th"})
        assert out.get("code") == "pok_family_full"
        for slug in ("pa", "pb"):
            tools[0].invoke({"slug": slug, "body": f"pin {slug}"})
        lead = create_pok_tools(
            _manager(tool_env, "p1", "leader"),
            current_instance_id="inst-2",
            agent_id="leader",
        )
        for slug in ("pa", "pb"):
            assert lead[2].invoke({"slug": slug, "pinned": True}).get(
                "pinned"
            ) is True
        dev = create_pok_tools(
            _manager(tool_env, "p1", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        dev[0].invoke({"slug": "pc", "body": "third pin"})
        out = lead[2].invoke({"slug": "pc", "pinned": True})
        assert out.get("code") == "pok_pin_cap"

    def test_query_result_shape(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, "p1", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        tools[0].invoke({"slug": "shape-a", "body": "the shape body"})
        out = tools[1].invoke({"query": "shape"})
        assert out["count"] == len(out["entries"]) >= 1
        assert isinstance(out["bm25_only"], bool)
        entry = out["entries"][0]
        for key in (
            "id", "slug", "domain", "audience", "body", "status",
            "disputed", "pinned", "provenance", "created_by_agent",
            "verify_count", "access_count",
        ):
            assert key in entry, key

    def test_supersede_echo_carries_the_prior_id(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, "p1", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        v1 = tools[0].invoke({"slug": "sup-a", "body": "v1"})
        v2 = tools[0].invoke({"slug": "sup-a", "body": "v2 differs"})
        assert v2["action"] == "superseded"
        assert v2["superseded_id"] == v1["id"]

    def test_noop_echo_note(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, "p1", "developer"),
            current_instance_id="inst-1",
            agent_id="developer",
        )
        tools[0].invoke({"slug": "noop-a", "body": "same"})
        out = tools[0].invoke({"slug": "noop-a", "body": "same"})
        assert out["action"] == "no-op"
        assert "note" in out
