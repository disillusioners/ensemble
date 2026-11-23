"""Repository-level tests for the project-reference resolution ladder.

The ladder has exactly ONE owner — ``SQLModelProjectRepository
.resolve_project_reference`` — and the tool layer (``project_get``) CALLS
it. These tests exercise that method against a REAL in-memory SQLite
database (mirroring the fixtures in ``tests/test_project_tools.py``).

Ladder (the FIRST tier that matches wins)::

    L1  exact project NAME       (case-sensitive)
    L2  exact project SHORTNAME  (case-sensitive)
    L3  case-insensitive exact project NAME
    L4  case-insensitive exact project SHORTNAME
    L5  case-insensitive SUBSTRING over NAME + SHORTNAMES ONLY

Invariants under test:

* exactly ONE project resolves — never a silent pick out of several;
* more than one match is a typed ``AMBIGUOUS_PROJECT`` refusal that
  enumerates the candidates;
* the DESCRIPTION is never part of the match surface;
* ``scope="shortname"`` NEVER resolves via the project NAME;
* NO status filter — paused / archived projects stay resolvable.
"""

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories import SQLModelProjectRepository as ProjectStore
from daemon.tools.project import create_project_tools


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


def _make(store, name, shortnames=None, description=None, status=None):
    """Create a project (optionally with shortnames / description / status)."""
    project = store.create(
        name=name,
        shortnames=shortnames or [],
        description=description,
    )
    if status:
        project = store.update_status(project.project_id, status)
    return project


class TestResolveShortname:
    """A human-typed shortname resolves to exactly ONE project."""

    def test_shortname_resolves(self, store):
        project = _make(store, "agents-ensemble", shortnames=["ensemble", "ens"])

        result = store.resolve_project_reference("ensemble", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id
        assert result["matched_by"] == "shortname_exact"

    def test_resolved_project_is_enriched(self, store):
        project = _make(store, "agents-ensemble", shortnames=["ensemble", "ens"])

        result = store.resolve_project_reference("ensemble", scope="name")

        assert set(result["project"].shortnames) == {"ensemble", "ens"}
        assert result["project"].tags == []

    def test_shortname_scope_resolves_shortname(self, store):
        project = _make(store, "agents-ensemble", shortnames=["ens"])

        result = store.resolve_project_reference("ens", scope="shortname")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id
        assert result["matched_by"] == "shortname_exact"


class TestResolveExactName:
    """An exact project name resolves on the FIRST tier."""

    def test_exact_name_resolves(self, store):
        project = _make(store, "Ensemble Core")

        result = store.resolve_project_reference("Ensemble Core", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id
        assert result["matched_by"] == "name_exact"

    def test_exact_name_is_case_sensitive(self, store):
        """L1 is case-sensitive — 'ensemble core' does NOT hit L1."""
        project = _make(store, "Ensemble Core")

        result = store.resolve_project_reference("ensemble core", scope="name")

        assert result["found"] is True
        # L3 (CI exact name) — NOT the case-sensitive L1.
        assert result["matched_by"] == "name_exact_ci"


class TestResolveCaseInsensitive:
    """CI tiers resolve a differently-cased reference."""

    def test_ci_upper_shortname(self, store):
        project = _make(store, "agents-ensemble", shortnames=["ensemble"])

        result = store.resolve_project_reference("ENSEMBLE", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id
        assert result["matched_by"] == "shortname_exact_ci"

    def test_ci_mixed_case_shortname(self, store):
        project = _make(store, "agents-ensemble", shortnames=["ensemble"])

        result = store.resolve_project_reference("Ensemble", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id
        assert result["matched_by"] == "shortname_exact_ci"

    def test_ci_mixed_case_name(self, store):
        project = _make(store, "Ensemble Core")

        result = store.resolve_project_reference("eNSEMBLE cORE", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id
        assert result["matched_by"] == "name_exact_ci"

    def test_ci_shortname_scope(self, store):
        project = _make(store, "agents-ensemble", shortnames=["ens"])

        result = store.resolve_project_reference("ENS", scope="shortname")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id
        assert result["matched_by"] == "shortname_exact_ci"


class TestResolveAmbiguous:
    """Several candidates produce a TYPED refusal — never a silent pick."""

    def test_ambiguous_substring_refuses(self, store):
        alpha_one = _make(store, "Alpha One")
        alpha_two = _make(store, "Alpha Two")

        result = store.resolve_project_reference("alpha", scope="name")

        assert result["found"] is False
        assert result["error_code"] == "AMBIGUOUS_PROJECT"
        assert "project" not in result
        assert result["query"] == "alpha"
        assert result["candidate_count"] == 2
        assert {
            candidate["project_id"]
            for candidate in result["candidates"]
        } == {alpha_one.project_id, alpha_two.project_id}

    def test_ambiguous_message_names_every_candidate(self, store):
        alpha_one = _make(store, "Alpha One", shortnames=["a1"])
        alpha_two = _make(store, "Alpha Two", shortnames=["a2"])

        result = store.resolve_project_reference("alpha", scope="name")

        # The frozen refusal sentence is the message PREFIX.
        assert result["error"].startswith(
            "Project 'alpha' is ambiguous - 2 projects match. "
            "Retry with an explicit project_id."
        )
        # ... and the message NAMES each candidate's name, shortnames and id.
        for candidate in result["candidates"]:
            assert candidate["name"] in result["error"]
            assert candidate["project_id"] in result["error"]
            for shortname in candidate["shortnames"]:
                assert shortname in result["error"]
        assert alpha_one.project_id in result["error"]
        assert alpha_two.project_id in result["error"]

    def test_ambiguous_exact_shortname_refuses(self, store):
        """Two projects sharing a shortname → refusal, not a silent pick."""
        first = _make(store, "First Project", shortnames=["shared"])
        second = _make(store, "Second Project", shortnames=["shared"])

        result = store.resolve_project_reference("shared", scope="name")

        assert result["found"] is False
        assert result["error_code"] == "AMBIGUOUS_PROJECT"
        assert result["candidate_count"] == 2
        assert {
            candidate["project_id"]
            for candidate in result["candidates"]
        } == {first.project_id, second.project_id}

    def test_candidates_capped_at_ten_but_count_is_total(self, store):
        for index in range(12):
            _make(store, f"Cap Project {index:02d}")

        result = store.resolve_project_reference("cap", scope="name")

        assert result["found"] is False
        assert result["error_code"] == "AMBIGUOUS_PROJECT"
        assert result["candidate_count"] == 12
        assert len(result["candidates"]) == 10
        # The message enumerates at most 10 candidates too.
        assert result["error"].count("project_id:") == 10


class TestResolveNotFound:
    """An unknown reference is a typed NOT_FOUND refusal."""

    def test_unknown_reference_not_found(self, store):
        result = store.resolve_project_reference("nope-nothing", scope="name")

        assert result["found"] is False
        assert result["error_code"] == "NOT_FOUND"
        assert "not found" in result["error"].lower()
        assert result["query"] == "nope-nothing"
        assert result["candidates"] == []
        assert "project" not in result

    def test_description_only_text_creates_no_hit(self, store):
        """L5 covers NAME + SHORTNAMES ONLY — never the description."""
        _make(
            store,
            "agents-ensemble",
            description="Persistent multi-agent daemon built with LangGraph",
        )

        result = store.resolve_project_reference("LangGraph", scope="name")

        assert result["found"] is False
        assert result["error_code"] == "NOT_FOUND"
        assert result["candidates"] == []

    def test_description_substring_creates_no_hit(self, store):
        _make(
            store,
            "agents-ensemble",
            description="Persistent multi-agent daemon built with LangGraph",
        )

        result = store.resolve_project_reference("langgraph", scope="name")

        assert result["found"] is False
        assert result["error_code"] == "NOT_FOUND"

    def test_empty_query_not_found(self, store):
        result = store.resolve_project_reference("", scope="name")

        assert result["found"] is False
        assert result["error_code"] == "NOT_FOUND"


class TestResolveSingleSubstring:
    """A substring matching exactly ONE candidate returns that candidate."""

    def test_single_substring_candidate_returns_it(self, store):
        project = _make(store, "My Wonderful Project", shortnames=["mwp"])

        result = store.resolve_project_reference("wonder", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id
        assert result["matched_by"] == "name_substring"

    def test_single_shortname_substring_candidate_returns_it(self, store):
        project = _make(store, "Docs Project", shortnames=["documentation-hub"])

        result = store.resolve_project_reference("hub", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id
        assert result["matched_by"] == "shortname_substring"


class TestResolveScope:
    """scope='shortname' NEVER resolves via the project NAME."""

    def test_shortname_scope_never_matches_name(self, store):
        _make(store, "agents-ensemble", shortnames=["ens"])

        result = store.resolve_project_reference("agents-ensemble", scope="shortname")

        assert result["found"] is False
        assert result["error_code"] == "NOT_FOUND"

    def test_shortname_scope_never_matches_name_substring(self, store):
        _make(store, "agents-ensemble", shortnames=["ens"])

        result = store.resolve_project_reference("ensemble", scope="shortname")

        assert result["found"] is False
        assert result["error_code"] == "NOT_FOUND"

    def test_shortname_scope_never_matches_ci_name(self, store):
        _make(store, "agents-ensemble", shortnames=["ens"])

        result = store.resolve_project_reference("AGENTS-ENSEMBLE", scope="shortname")

        assert result["found"] is False
        assert result["error_code"] == "NOT_FOUND"

    def test_invalid_scope_raises(self, store):
        """Fail closed — an unknown scope must never widen the ladder."""
        with pytest.raises(ValueError):
            store.resolve_project_reference("anything", scope="banana")


class TestResolveStatusIndependence:
    """NO status filter — paused / archived projects stay resolvable."""

    def test_paused_project_stays_resolvable(self, store):
        project = _make(store, "Paused Project", shortnames=["paused-ref"], status="paused")

        result = store.resolve_project_reference("paused-ref", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id
        assert result["project"].status == "paused"

    def test_archived_project_stays_resolvable(self, store):
        project = _make(
            store,
            "Archived Project",
            shortnames=["archived-ref"],
            status="archived",
        )

        result = store.resolve_project_reference("archived-ref", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id
        assert result["project"].status == "archived"

    def test_paused_project_resolves_by_name(self, store):
        project = _make(store, "Paused By Name", status="paused")

        result = store.resolve_project_reference("Paused By Name", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == project.project_id


class TestLadderOrder:
    """The FIRST matching tier wins — the order is part of the contract."""

    def test_exact_name_beats_exact_shortname(self, store):
        by_name = _make(store, "ensemble")
        by_shortname = _make(store, "Other Project", shortnames=["ensemble"])

        result = store.resolve_project_reference("ensemble", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == by_name.project_id
        assert result["matched_by"] == "name_exact"
        assert by_shortname.project_id != result["project"].project_id

    def test_exact_shortname_beats_ci_exact_name(self, store):
        """L2 runs BEFORE L3 — a shared exact shortname wins over a CI name."""
        by_shortname = _make(store, "Beta Project", shortnames=["ensemble"])
        by_ci_name = _make(store, "Ensemble")

        result = store.resolve_project_reference("ensemble", scope="name")

        assert result["found"] is True
        assert result["project"].project_id == by_shortname.project_id
        assert result["matched_by"] == "shortname_exact"
        assert by_ci_name.project_id != result["project"].project_id


class TestProjectGetWiring:
    """project_get CALLS the ladder — it never re-implements it."""

    @pytest.fixture
    def tool_map(self, store):
        tools = create_project_tools(
            store, current_instance_id="test-instance", agent_id="test"
        )
        return {tool.name: tool for tool in tools}

    def test_get_by_shortname(self, tool_map, store):
        created = store.create(name="Wired Project", shortnames=["wired"])

        result = tool_map["project_get"].invoke({"shortname": "wired"})

        assert result["project_id"] == created.project_id
        assert result["name"] == "Wired Project"
        assert "wired" in result["shortnames"]

    def test_get_by_name_still_works(self, tool_map):
        created = tool_map["project_create"].invoke({"name": "Plain Project"})

        result = tool_map["project_get"].invoke({"name": "Plain Project"})

        assert result["project_id"] == created["project_id"]

    def test_get_by_id_unchanged(self, tool_map):
        created = tool_map["project_create"].invoke({"name": "Id Project"})

        result = tool_map["project_get"].invoke(
            {"project_id": created["project_id"]}
        )

        assert result["project_id"] == created["project_id"]

    def test_get_not_found_shape(self, tool_map):
        result = tool_map["project_get"].invoke({"name": "nothing-here"})

        assert "not found" in result["error"].lower()
        assert result["error_code"] == "NOT_FOUND"
        assert result["query"] == "nothing-here"
        assert result["candidates"] == []

    def test_get_ambiguous_shape(self, tool_map):
        tool_map["project_create"].invoke({"name": "Ambiguous One"})
        tool_map["project_create"].invoke({"name": "Ambiguous Two"})

        result = tool_map["project_get"].invoke({"name": "ambiguous"})

        assert result["error_code"] == "AMBIGUOUS_PROJECT"
        assert result["query"] == "ambiguous"
        assert result["candidate_count"] == 2
        assert len(result["candidates"]) == 2
        assert "project_id" not in result
        assert "name" not in result

    def test_get_without_arguments(self, tool_map):
        result = tool_map["project_get"].invoke({})

        assert result == {"error": "Must provide either project_id, name, or shortname"}
