"""Tool-layer tests for project-name/shortname resolution in the history tools.

The resolution ladder has exactly ONE owner —
``SQLModelProjectRepository.resolve_project_reference`` — and the tool
layer CALLS it (the very same seam ``project_get`` uses). These tests
exercise the four ``project_history_*`` tools against a REAL in-memory
SQLite database (mirroring the fixtures in
``tests/test_project_resolution.py``).

Invariants under test:

* a shortname / an exact name resolves to exactly ONE project on EVERY
  tool (add / list / search / delete);
* the explicit-id path keeps its behaviour and NEVER touches the
  resolver;
* an ambiguous reference is a typed ``AMBIGUOUS_PROJECT`` refusal that
  enumerates the candidates — never a silent pick;
* an unknown reference is a typed ``NOT_FOUND`` refusal;
* no reference at all is refused with the "must provide" message;
* the Blueprint pending-queue hook (C8) is driven by the RESOLVED project.
"""

from unittest.mock import MagicMock

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories import SQLModelProjectRepository as ProjectStore
from daemon.tools.project_history import create_project_history_tools


# =============================================================================
# Fixtures
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
    """Create a REAL project store over the in-memory SQLite engine."""
    return ProjectStore(engine)


@pytest.fixture
def tool_map(store):
    """The four project-history tools, keyed by tool name."""
    tools = create_project_history_tools(
        store,
        current_instance_id="test-instance",
        agent_id="test-agent",
    )
    return {t.name: t for t in tools}


def _make(store, name, shortnames=None):
    """Create a project (optionally with shortnames)."""
    return store.create(name=name, shortnames=shortnames or [])


class _NoResolverStore:
    """Store proxy that FAILS LOUDLY if the resolver is called.

    The id path must NEVER reach the resolution ladder — a plain
    ``store.get`` lookup is enough. This proxy pins that invariant.
    """

    def __init__(self, store):
        self._store = store

    def __getattr__(self, name):
        if name == "resolve_project_reference":
            def _boom(*_args, **_kwargs):
                raise AssertionError(
                    "the explicit-id path must NEVER call the resolver"
                )
            return _boom
        return getattr(self._store, name)


# =============================================================================
# Test Class: TestShortnameResolution
# =============================================================================


class TestShortnameResolution:
    """A shortname resolves to exactly ONE project on every tool."""

    def test_add_resolves_shortname(self, store, tool_map):
        project = _make(store, "agents-ensemble", shortnames=["ensemble", "ens"])

        result = tool_map["project_history_add"].invoke(
            {
                "shortname": "ensemble",
                "entry_type": "milestone",
                "summary": "Shortname add",
            }
        )

        assert "error" not in result
        assert result["project_id"] == project.project_id
        assert result["entry_type"] == "milestone"
        assert result["summary"] == "Shortname add"

    def test_list_resolves_shortname(self, store, tool_map):
        project = _make(store, "agents-ensemble", shortnames=["ensemble"])
        tool_map["project_history_add"].invoke(
            {
                "shortname": "ensemble",
                "entry_type": "note",
                "summary": "Listed via shortname",
            }
        )

        result = tool_map["project_history_list"].invoke({"shortname": "ensemble"})

        assert "error" not in result
        assert result["total"] == 1
        assert result["entries"][0]["project_id"] == project.project_id
        assert result["entries"][0]["summary"] == "Listed via shortname"

    def test_search_resolves_shortname(self, store, tool_map):
        project = _make(store, "agents-ensemble", shortnames=["ensemble"])
        tool_map["project_history_add"].invoke(
            {
                "shortname": "ensemble",
                "entry_type": "note",
                "summary": "Searchable payload",
            }
        )

        result = tool_map["project_history_search"].invoke(
            {"shortname": "ensemble", "query": "Searchable"}
        )

        assert "error" not in result
        assert result["total"] == 1
        assert result["entries"][0]["project_id"] == project.project_id

    def test_delete_resolves_shortname(self, store, tool_map):
        project = _make(store, "agents-ensemble", shortnames=["ensemble"])
        added = tool_map["project_history_add"].invoke(
            {
                "shortname": "ensemble",
                "entry_type": "note",
                "summary": "Delete me",
            }
        )
        entry_id = added["id"]

        result = tool_map["project_history_delete"].invoke(
            {"shortname": "ensemble", "entry_id": entry_id}
        )

        assert result["success"] is True
        assert result["deleted_entry_id"] == entry_id
        # The row is gone — the resolved project is the owner.
        remaining = tool_map["project_history_list"].invoke(
            {"project_id": project.project_id}
        )
        assert remaining["total"] == 0


# =============================================================================
# Test Class: TestExactNameResolution
# =============================================================================


class TestExactNameResolution:
    """An exact project name resolves to exactly ONE project."""

    def test_add_resolves_exact_name(self, store, tool_map):
        project = _make(store, "agents-ensemble", shortnames=["ensemble"])

        result = tool_map["project_history_add"].invoke(
            {
                "name": "agents-ensemble",
                "entry_type": "feature",
                "summary": "Named add",
            }
        )

        assert "error" not in result
        assert result["project_id"] == project.project_id

    def test_list_resolves_exact_name(self, store, tool_map):
        project = _make(store, "agents-ensemble", shortnames=["ensemble"])
        tool_map["project_history_add"].invoke(
            {
                "name": "agents-ensemble",
                "entry_type": "note",
                "summary": "Named entry",
            }
        )

        result = tool_map["project_history_list"].invoke({"name": "agents-ensemble"})

        assert "error" not in result
        assert result["total"] == 1
        assert result["entries"][0]["project_id"] == project.project_id

    def test_search_resolves_exact_name(self, store, tool_map):
        _make(store, "agents-ensemble", shortnames=["ensemble"])
        tool_map["project_history_add"].invoke(
            {
                "name": "agents-ensemble",
                "entry_type": "note",
                "summary": "Named searchable",
            }
        )

        result = tool_map["project_history_search"].invoke(
            {"name": "agents-ensemble", "query": "searchable"}
        )

        assert "error" not in result
        assert result["total"] == 1

    def test_delete_resolves_exact_name(self, store, tool_map):
        _make(store, "agents-ensemble", shortnames=["ensemble"])
        added = tool_map["project_history_add"].invoke(
            {
                "name": "agents-ensemble",
                "entry_type": "note",
                "summary": "Named delete",
            }
        )

        result = tool_map["project_history_delete"].invoke(
            {"name": "agents-ensemble", "entry_id": added["id"]}
        )

        assert result["success"] is True
        assert result["deleted_entry_id"] == added["id"]


# =============================================================================
# Test Class: TestIdPathUnchanged
# =============================================================================


class TestIdPathUnchanged:
    """The explicit-id path keeps its behaviour and never calls the resolver."""

    def test_id_path_never_calls_the_resolver(self, store):
        project = _make(store, "agents-ensemble", shortnames=["ensemble"])
        tools = create_project_history_tools(
            _NoResolverStore(store),
            current_instance_id="test-instance",
            agent_id="test-agent",
        )
        tool_map = {t.name: t for t in tools}

        added = tool_map["project_history_add"].invoke(
            {
                "project_id": project.project_id,
                "entry_type": "milestone",
                "summary": "Id path",
            }
        )

        assert "error" not in added
        assert added["project_id"] == project.project_id

        listed = tool_map["project_history_list"].invoke(
            {"project_id": project.project_id}
        )
        assert listed["total"] == 1

        searched = tool_map["project_history_search"].invoke(
            {"project_id": project.project_id, "query": "Id"}
        )
        assert searched["total"] == 1

        deleted = tool_map["project_history_delete"].invoke(
            {"project_id": project.project_id, "entry_id": added["id"]}
        )
        assert deleted["success"] is True

    def test_id_path_miss_is_typed_not_found(self, tool_map):
        result = tool_map["project_history_add"].invoke(
            {
                "project_id": "no-such-project-id",
                "entry_type": "note",
                "summary": "x",
            }
        )

        assert result["error"] == "Project not found"
        assert result["error_code"] == "NOT_FOUND"
        assert result["query"] == "no-such-project-id"
        assert result["candidates"] == []

    def test_explicit_id_wins_over_name(self, store, tool_map):
        """``project_id`` takes precedence over ``name``/``shortname``."""
        first = _make(store, "agents-ensemble", shortnames=["ensemble"])
        second = _make(store, "Other Project", shortnames=["other"])

        result = tool_map["project_history_add"].invoke(
            {
                "project_id": second.project_id,
                "name": "agents-ensemble",
                "shortname": "ensemble",
                "entry_type": "note",
                "summary": "Id wins",
            }
        )

        assert "error" not in result
        assert result["project_id"] == second.project_id
        assert result["project_id"] != first.project_id


# =============================================================================
# Test Class: TestAmbiguousReference
# =============================================================================


class TestAmbiguousReference:
    """Several candidates produce a TYPED refusal — never a silent pick."""

    @pytest.fixture
    def ambiguous_projects(self, store):
        first = _make(store, "Ambiguous One")
        second = _make(store, "Ambiguous Two")
        return first, second

    def test_add_refuses_ambiguous_reference(self, tool_map, ambiguous_projects):
        result = tool_map["project_history_add"].invoke(
            {
                "name": "Ambiguous",
                "entry_type": "note",
                "summary": "x",
            }
        )

        assert result["error_code"] == "AMBIGUOUS_PROJECT"
        assert result["query"] == "Ambiguous"
        assert result["candidate_count"] == 2
        assert len(result["candidates"]) == 2

    def test_list_refuses_ambiguous_reference(self, tool_map, ambiguous_projects):
        result = tool_map["project_history_list"].invoke({"name": "Ambiguous"})

        assert result["error_code"] == "AMBIGUOUS_PROJECT"
        assert result["query"] == "Ambiguous"
        assert result["candidate_count"] == 2
        assert len(result["candidates"]) == 2

    def test_search_refuses_ambiguous_reference(self, tool_map, ambiguous_projects):
        result = tool_map["project_history_search"].invoke(
            {"name": "Ambiguous", "query": "x"}
        )

        assert result["error_code"] == "AMBIGUOUS_PROJECT"
        assert result["query"] == "Ambiguous"
        assert result["candidate_count"] == 2
        assert len(result["candidates"]) == 2

    def test_delete_refuses_ambiguous_reference(self, tool_map, ambiguous_projects):
        result = tool_map["project_history_delete"].invoke(
            {"name": "Ambiguous", "entry_id": "some-entry-id"}
        )

        assert result["error_code"] == "AMBIGUOUS_PROJECT"
        assert result["query"] == "Ambiguous"
        assert result["candidate_count"] == 2
        assert len(result["candidates"]) == 2

    def test_ambiguous_candidates_name_both_projects(
        self, tool_map, ambiguous_projects
    ):
        first, second = ambiguous_projects

        result = tool_map["project_history_list"].invoke({"name": "Ambiguous"})

        assert {
            candidate["project_id"] for candidate in result["candidates"]
        } == {first.project_id, second.project_id}


# =============================================================================
# Test Class: TestUnknownReference
# =============================================================================


class TestUnknownReference:
    """An unknown reference is a typed NOT_FOUND refusal."""

    def test_add_unknown_reference(self, tool_map):
        result = tool_map["project_history_add"].invoke(
            {
                "name": "no-such-project-xyz",
                "entry_type": "note",
                "summary": "x",
            }
        )

        assert result["error"] == "Project not found"
        assert result["error_code"] == "NOT_FOUND"
        assert result["query"] == "no-such-project-xyz"
        assert result["candidates"] == []

    def test_list_unknown_reference(self, tool_map):
        result = tool_map["project_history_list"].invoke(
            {"shortname": "no-such-shortname-xyz"}
        )

        assert result["error_code"] == "NOT_FOUND"
        assert result["query"] == "no-such-shortname-xyz"
        assert result["candidates"] == []

    def test_search_unknown_reference(self, tool_map):
        result = tool_map["project_history_search"].invoke(
            {"shortname": "no-such-shortname-xyz", "query": "x"}
        )

        assert result["error_code"] == "NOT_FOUND"
        assert result["query"] == "no-such-shortname-xyz"

    def test_delete_unknown_reference(self, tool_map):
        result = tool_map["project_history_delete"].invoke(
            {"shortname": "no-such-shortname-xyz", "entry_id": "some-entry-id"}
        )

        assert result["error_code"] == "NOT_FOUND"
        assert result["query"] == "no-such-shortname-xyz"


# =============================================================================
# Test Class: TestMissingReference
# =============================================================================


class TestMissingReference:
    """No reference at all is refused with the "must provide" message."""

    def test_add_requires_a_reference(self, tool_map):
        result = tool_map["project_history_add"].invoke(
            {"entry_type": "note", "summary": "x"}
        )

        assert result["error"] == "Must provide either project_id, name, or shortname"

    def test_list_requires_a_reference(self, tool_map):
        result = tool_map["project_history_list"].invoke({})

        assert result["error"] == "Must provide either project_id, name, or shortname"

    def test_search_requires_a_reference(self, tool_map):
        result = tool_map["project_history_search"].invoke({"query": "x"})

        assert result["error"] == "Must provide either project_id, name, or shortname"

    def test_delete_requires_a_reference(self, tool_map):
        result = tool_map["project_history_delete"].invoke(
            {"entry_id": "some-entry-id"}
        )

        assert result["error"] == "Must provide either project_id, name, or shortname"


# =============================================================================
# Test Class: TestPendingQueueHookSeam
# =============================================================================


class TestPendingQueueHookSeam:
    """The Blueprint pending-queue hook (C8) follows the RESOLVED project."""

    def test_add_without_manager_writes_the_resolved_project(self, store):
        project = _make(store, "agents-ensemble", shortnames=["ensemble"])
        tools = create_project_history_tools(
            store,
            current_instance_id="test-instance",
            agent_id="test-agent",
            manager=None,
        )
        tool_map = {t.name: t for t in tools}

        result = tool_map["project_history_add"].invoke(
            {
                "shortname": "ensemble",
                "entry_type": "feature",
                "summary": "No manager, no pending queue",
            }
        )

        assert "error" not in result
        assert result["project_id"] == project.project_id

    def test_add_enqueues_pending_for_the_resolved_project(self, store):
        project = _make(store, "agents-ensemble", shortnames=["ensemble"])
        pending_repo = MagicMock()
        manager = MagicMock()
        manager._blueprint_pending_repo = pending_repo
        manager._project_repository.get_metadata = MagicMock(return_value=True)
        tools = create_project_history_tools(
            store,
            current_instance_id="test-instance",
            agent_id="test-agent",
            manager=manager,
        )
        tool_map = {t.name: t for t in tools}

        result = tool_map["project_history_add"].invoke(
            {
                "shortname": "ensemble",
                "entry_type": "feature",
                "summary": "Resolved project drives the queue",
            }
        )

        assert "error" not in result
        assert pending_repo.enqueue.call_count == 1
        call = pending_repo.enqueue.call_args.kwargs
        assert call["project_id"] == project.project_id
        assert call["source_type"] == "history"
        assert call["source_payload"]["entry_type"] == "feature"
