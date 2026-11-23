"""Tool-layer tests for the project_future project-reference triad.

The three wishlist tools accept the project as a REFERENCE — ``project_id``,
OR ``name``, OR ``shortname`` — and resolve it through the SAME repository
seam ``project_get`` uses (``SQLModelProjectRepository
.resolve_project_reference``). These tests drive the REAL tools against a
REAL ``SQLModelProjectRepository`` over in-memory SQLite (the fixtures of
``tests/test_project_resolution.py``), so the ladder is exercised end-to-end
and is NOT re-implemented in the tool layer.

Invariants under test:

* a shortname / exact name resolves to exactly ONE project on EVERY verb
  (add / list / remove) — the resolved project id is the one threaded into
  the store calls;
* the id path is UNCHANGED — it resolves through ``store.get`` alone and
  never reaches the resolver;
* an ambiguous reference is a typed ``AMBIGUOUS_PROJECT`` refusal that
  enumerates the candidates — never a silent pick;
* an unknown reference is a typed ``NOT_FOUND`` refusal carrying the query;
* no reference at all is the must-provide refusal.
"""

from unittest import mock

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories import SQLModelProjectRepository as ProjectStore
from daemon.tools.project_future import create_project_future_tools


# =============================================================================
# Fixtures — the house idiom of tests/test_project_resolution.py
# =============================================================================


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def store(engine):
    """Create ProjectStore instance with SQLModel engine."""
    return ProjectStore(engine)


@pytest.fixture
def tool_map(store):
    """The three project_future tools, keyed by tool name."""
    tools = create_project_future_tools(store, "inst-123", "coder-agent")
    return {t.name: t for t in tools}


def _make(store, name, shortnames=None):
    """Create a project (optionally with shortnames)."""
    return store.create(name=name, shortnames=shortnames or [])


# =============================================================================
# shortname → the project "agents-ensemble" — on EVERY verb
# =============================================================================


class TestShortnameResolution:
    """shortname="ensemble" resolves to the project named "agents-ensemble"."""

    @pytest.fixture
    def project(self, store):
        return _make(store, "agents-ensemble", shortnames=["ensemble", "ens"])

    def test_add_resolves_shortname(self, tool_map, project):
        """add: the shortname resolves and the entry lands under the RESOLVED id."""
        result = tool_map["project_future_add"].invoke(
            {"shortname": "ensemble", "summary": "Ship the streaming retry path"}
        )
        assert "error" not in result
        assert result["project_id"] == project.project_id
        assert result["evicted"] == []

    def test_list_resolves_shortname(self, tool_map, project):
        """list: the shortname resolves and the seeded entry is visible."""
        tool_map["project_future_add"].invoke(
            {"shortname": "ensemble", "summary": "Ship the streaming retry path"}
        )
        result = tool_map["project_future_list"].invoke({"shortname": "ensemble"})
        assert result["total"] == 1
        assert result["entries"][0]["summary"] == "Ship the streaming retry path"
        assert result["cap"] == 32

    def test_remove_resolves_shortname(self, tool_map, project):
        """remove: the shortname resolves and retires the entry."""
        added = tool_map["project_future_add"].invoke(
            {"shortname": "ensemble", "summary": "Obsolete idea"}
        )
        result = tool_map["project_future_remove"].invoke(
            {"shortname": "ensemble", "entry_id": added["id"]}
        )
        assert result["success"] is True
        assert result["removed"]["id"] == added["id"]
        assert result["removed"]["project_id"] == project.project_id

    def test_remove_is_scoped_to_the_resolved_project(self, tool_map, store, project):
        """The RESOLVED id scopes the removal — another project's entry survives."""
        other = _make(store, "Other Project")
        added = tool_map["project_future_add"].invoke(
            {"project_id": other.project_id, "summary": "Belongs elsewhere"}
        )

        result = tool_map["project_future_remove"].invoke(
            {"shortname": "ensemble", "entry_id": added["id"]}
        )

        assert result == {
            "error": f"Entry '{added['id']}' not found in project '{project.project_id}'"
        }
        survivor = tool_map["project_future_list"].invoke(
            {"project_id": other.project_id}
        )
        assert survivor["total"] == 1

    def test_name_path_also_resolves_a_shortname(self, tool_map, project):
        """The name path runs the FULL ladder — a shortname resolves there too."""
        result = tool_map["project_future_add"].invoke(
            {"name": "ensemble", "summary": "Ship the streaming retry path"}
        )
        assert "error" not in result
        assert result["project_id"] == project.project_id


# =============================================================================
# Exact name → the project — on EVERY verb
# =============================================================================


class TestExactNameResolution:
    """name="agents-ensemble" resolves on the FIRST ladder tier."""

    @pytest.fixture
    def project(self, store):
        return _make(store, "agents-ensemble", shortnames=["ensemble", "ens"])

    def test_add_resolves_exact_name(self, tool_map, project):
        result = tool_map["project_future_add"].invoke(
            {"name": "agents-ensemble", "summary": "Ship the streaming retry path"}
        )
        assert "error" not in result
        assert result["project_id"] == project.project_id

    def test_list_resolves_exact_name(self, tool_map, project):
        tool_map["project_future_add"].invoke(
            {"name": "agents-ensemble", "summary": "Ship the streaming retry path"}
        )
        result = tool_map["project_future_list"].invoke({"name": "agents-ensemble"})
        assert result["total"] == 1
        assert result["cap"] == 32

    def test_remove_resolves_exact_name(self, tool_map, project):
        added = tool_map["project_future_add"].invoke(
            {"name": "agents-ensemble", "summary": "Obsolete idea"}
        )
        result = tool_map["project_future_remove"].invoke(
            {"name": "agents-ensemble", "entry_id": added["id"]}
        )
        assert result["success"] is True
        assert result["removed"]["id"] == added["id"]


# =============================================================================
# The id path is UNCHANGED
# =============================================================================


class TestIdPathUnchanged:
    """project_id resolves through store.get() alone — the ladder is NOT used."""

    @pytest.fixture
    def project(self, store):
        return _make(store, "agents-ensemble", shortnames=["ensemble", "ens"])

    def test_id_path_add(self, tool_map, project):
        result = tool_map["project_future_add"].invoke(
            {"project_id": project.project_id, "summary": "Ship the streaming retry path"}
        )
        assert "error" not in result
        assert result["project_id"] == project.project_id

    def test_id_path_list(self, tool_map, project):
        tool_map["project_future_add"].invoke(
            {"project_id": project.project_id, "summary": "idea"}
        )
        result = tool_map["project_future_list"].invoke(
            {"project_id": project.project_id}
        )
        assert result["total"] == 1
        assert result["cap"] == 32

    def test_id_path_remove(self, tool_map, project):
        added = tool_map["project_future_add"].invoke(
            {"project_id": project.project_id, "summary": "Obsolete idea"}
        )
        result = tool_map["project_future_remove"].invoke(
            {"project_id": project.project_id, "entry_id": added["id"]}
        )
        assert result["success"] is True
        assert result["removed"]["id"] == added["id"]

    def test_id_path_never_calls_the_resolver(self, tool_map, store, project):
        """The resolver is NOT on the id path (byte-compat + mock-store compat)."""
        with mock.patch.object(
            store,
            "resolve_project_reference",
            wraps=store.resolve_project_reference,
        ) as spy:
            result = tool_map["project_future_list"].invoke(
                {"project_id": project.project_id}
            )
        assert result["total"] == 0
        spy.assert_not_called()


# =============================================================================
# Ambiguity — a typed refusal, never a silent pick
# =============================================================================


class TestAmbiguousReference:
    """Two "Ambiguous*" projects make the reference ambiguous."""

    @pytest.fixture
    def ambiguous(self, store):
        first = _make(store, "Ambiguous One", shortnames=["amb"])
        second = _make(store, "Ambiguous Two", shortnames=["amb"])
        return first, second

    def test_add_refuses_ambiguous_name(self, tool_map, ambiguous):
        result = tool_map["project_future_add"].invoke(
            {"name": "Ambiguous", "summary": "idea"}
        )
        assert result["error_code"] == "AMBIGUOUS_PROJECT"
        assert result["query"] == "Ambiguous"
        assert result["candidate_count"] == 2
        assert len(result["candidates"]) == 2
        assert {row["project_id"] for row in result["candidates"]} == {
            p.project_id for p in ambiguous
        }
        assert "error" in result

    def test_list_refuses_ambiguous_name(self, tool_map, ambiguous):
        result = tool_map["project_future_list"].invoke({"name": "Ambiguous"})
        assert result["error_code"] == "AMBIGUOUS_PROJECT"
        assert result["query"] == "Ambiguous"
        assert result["candidate_count"] == 2

    def test_remove_refuses_ambiguous_shortname(self, tool_map, ambiguous):
        result = tool_map["project_future_remove"].invoke(
            {"shortname": "amb", "entry_id": "future-1"}
        )
        assert result["error_code"] == "AMBIGUOUS_PROJECT"
        assert result["query"] == "amb"
        assert result["candidate_count"] == 2
        assert {row["project_id"] for row in result["candidates"]} == {
            p.project_id for p in ambiguous
        }


# =============================================================================
# Unknown reference → typed NOT_FOUND
# =============================================================================


class TestUnknownReference:
    """An unknown reference never reaches the store."""

    def test_add_unknown_name(self, tool_map):
        result = tool_map["project_future_add"].invoke(
            {"name": "no-such-project", "summary": "idea"}
        )
        assert result == {
            "error": "Project not found",
            "error_code": "NOT_FOUND",
            "query": "no-such-project",
            "candidates": [],
        }

    def test_list_unknown_shortname(self, tool_map):
        result = tool_map["project_future_list"].invoke({"shortname": "no-such-shortname"})
        assert result == {
            "error": "Project not found",
            "error_code": "NOT_FOUND",
            "query": "no-such-shortname",
            "candidates": [],
        }

    def test_remove_unknown_name(self, tool_map):
        result = tool_map["project_future_remove"].invoke(
            {"name": "no-such-project", "entry_id": "future-1"}
        )
        assert result == {
            "error": "Project not found",
            "error_code": "NOT_FOUND",
            "query": "no-such-project",
            "candidates": [],
        }


# =============================================================================
# No reference at all → the must-provide refusal
# =============================================================================


class TestNoReference:
    """No reference at all is refused BEFORE any store call."""

    def test_add_without_reference(self, tool_map):
        result = tool_map["project_future_add"].invoke({"summary": "idea"})
        assert result == {"error": "Must provide either project_id, name, or shortname"}

    def test_list_without_reference(self, tool_map):
        result = tool_map["project_future_list"].invoke({})
        assert result == {"error": "Must provide either project_id, name, or shortname"}

    def test_remove_without_reference(self, tool_map):
        result = tool_map["project_future_remove"].invoke({"entry_id": "future-1"})
        assert result == {"error": "Must provide either project_id, name, or shortname"}
