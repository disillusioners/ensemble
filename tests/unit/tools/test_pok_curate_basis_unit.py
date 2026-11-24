"""POK ``pok_curate`` RULED ARCHIVE BASIS — the mandatory basis enum.

Covers pok-write-gate Phase 1 T1.6: an archive through ``pok_curate``
is permitted ONLY under a ruled basis — the basis is REQUIRED and it
must be a member of the enum. Consolidation happens ONLY under a ruled
basis (covered-by-surviving-row / relocated-to-routed-home / NOW-FALSE)
— NEVER on age, NEVER on access counts alone (design-amendments-v2
§9). The curator may also cite a gate rejection as the basis
(design-amendments-v2 §1).

Fence discipline: in-memory SQLite engine only (StaticPool, the seam's
to_thread discipline). No daemon boot, no InstanceManager, no live
store, no network.
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
    POK_ARCHIVE_BASES,
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.tools.pok import create_pok_tools


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


def _write(tool_env, slug: str, agent_id: str = "worker") -> None:
    tools = create_pok_tools(
        _manager(tool_env, "p1", agent_id),
        current_instance_id="inst-1",
        agent_id=agent_id,
    )
    out = tools[0].invoke({"slug": slug, "body": "an operational rule"})
    assert "error" not in out, out


def _curate(tool_env, payload: dict, agent_id: str = "leader") -> dict:
    tools = create_pok_tools(
        _manager(tool_env, "p1", agent_id),
        current_instance_id="inst-1",
        agent_id=agent_id,
    )
    return tools[2].invoke(payload)


class TestArchiveBasisRequired:
    def test_archive_without_a_basis_is_refused(self, tool_env) -> None:
        _write(tool_env, "demo-01-a")
        out = _curate(tool_env, {"slug": "demo-01-a", "archived": True})
        assert out.get("code") == "pok_curate_basis_required", out
        assert "basis" in out["error"]

    def test_the_refused_archive_did_not_archive(self, tool_env) -> None:
        _write(tool_env, "demo-01-a")
        _curate(tool_env, {"slug": "demo-01-a", "archived": True})
        tools = create_pok_tools(
            _manager(tool_env, "p1", "leader"),
            current_instance_id="inst-1",
            agent_id="leader",
        )
        listed = tools[1].invoke({})
        slugs = [e.get("slug") for e in listed.get("entries", [])]
        assert "demo-01-a" in slugs

    def test_an_empty_basis_is_refused(self, tool_env) -> None:
        _write(tool_env, "demo-01-a")
        out = _curate(
            tool_env, {"slug": "demo-01-a", "archived": True, "basis": "   "}
        )
        assert out.get("code") == "pok_curate_basis_required", out

    def test_an_unknown_basis_is_refused(self, tool_env) -> None:
        _write(tool_env, "demo-01-a")
        out = _curate(
            tool_env,
            {"slug": "demo-01-a", "archived": True, "basis": "because-i-said-so"},
        )
        assert out.get("code") == "pok_curate_basis_invalid", out
        assert "covered-by-surviving-row" in out["error"]

    def test_the_refusal_lists_the_allowed_bases(self, tool_env) -> None:
        _write(tool_env, "demo-01-a")
        out = _curate(tool_env, {"slug": "demo-01-a", "archived": True})
        assert out.get("allowed") == list(POK_ARCHIVE_BASES)


class TestArchiveBasisAccepted:
    @pytest.mark.parametrize("basis", list(POK_ARCHIVE_BASES))
    def test_every_ruled_basis_archives(self, tool_env, basis: str) -> None:
        _write(tool_env, "demo-01-a")
        out = _curate(
            tool_env, {"slug": "demo-01-a", "archived": True, "basis": basis}
        )
        assert "error" not in out, out
        assert out["status"] == "archived"
        assert out["basis"] == basis

    def test_a_non_archive_flip_needs_no_basis(self, tool_env) -> None:
        """Only the ARCHIVE requires a basis — pinning/disputing/
        reclassifying are not archives."""
        _write(tool_env, "demo-01-a")
        out = _curate(tool_env, {"slug": "demo-01-a", "pinned": True})
        assert "error" not in out, out
        assert out["pinned"] is True
        assert out["basis"] is None

    def test_a_basis_without_an_archive_is_ignored(self, tool_env) -> None:
        """A basis alone never archives anything."""
        _write(tool_env, "demo-01-a")
        out = _curate(
            tool_env,
            {"slug": "demo-01-a", "basis": "covered-by-surviving-row"},
        )
        assert "error" not in out, out
        assert out["status"] == "active"


class TestBasisEnumVocabulary:
    def test_the_three_ruled_bases_are_present_verbatim(self) -> None:
        assert "covered-by-surviving-row" in POK_ARCHIVE_BASES
        assert "relocated-to-routed-home" in POK_ARCHIVE_BASES
        assert "NOW-FALSE" in POK_ARCHIVE_BASES

    def test_the_gate_rejection_citation_is_a_basis(self) -> None:
        """design-amendments-v2 §1 — the curator may cite a gate
        rejection as archive basis."""
        assert "gate-rejection" in POK_ARCHIVE_BASES

    def test_no_age_or_access_basis_exists(self) -> None:
        """NEVER age, NEVER access_count alone."""
        assert not any("age" in b for b in POK_ARCHIVE_BASES)
        assert not any("access" in b for b in POK_ARCHIVE_BASES)

    def test_the_basis_is_typed_on_the_tool(self, tool_env) -> None:
        tools = create_pok_tools(
            _manager(tool_env, "p1", "leader"),
            current_instance_id="inst-1",
            agent_id="leader",
        )
        schema = tools[2].args
        assert "basis" in schema
