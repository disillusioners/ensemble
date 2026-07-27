"""Tests for the ``search`` parameter on SQLModelInstanceRepository.list().

Covers the dialect-aware substring filter that matches ``search`` against
``instance_metadata.title``, ``agent_name``, and ``agent_id``. Includes
wildcard escaping, case-insensitivity, combination with other filters
(``project_id``, ``exclude_kb``, ``include_descendants``, pagination), and
the no-op behaviour when ``search`` is empty/None.

These tests run against the in-memory SQLite path used by ``test_instance_title``.
The PostgreSQL-specific JSONB title path (``metadata->>'title'`` cast to VARCHAR,
JSONB scalar coercion, etc.) is exercised separately in
``tests/postgres/test_instance_search_pg.py`` and only runs under
``pytest -m postgres``.
"""

from __future__ import annotations

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories.instance import SQLModelInstanceRepository


# ----- fixtures --------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    """In-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repo(engine):
    return SQLModelInstanceRepository(engine)


def _make(repo, instance_id, agent_id, agent_dir, *, metadata=None,
          parent_id=None, project_id=None, status="idle"):
    """Create an instance with the given parameters."""
    return repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        metadata=metadata or {},
        parent_id=parent_id,
        project_id=project_id,
        status=status,
    )


# ----- test data --------------------------------------------------------


@pytest.fixture
def seeded_repo(repo):
    """Seed a handful of instances exercising title / agent_name / agent_id.

    Layout:
      - "alpha"  : title="Alpha Run",   agent_id="developer", agent_dir="agents/coder"
      - "beta"   : title="Beta Run",    agent_id="fixer",      agent_dir="agents/fixer"
      - "gamma"  : title="OTHER",       agent_id="reviewer",   agent_dir="agents/reviewer"
      - "delta"  : title=None,          agent_id="developer",  agent_dir="agents/wanderer"

    agent_name is derived from agent_dir (Title Case of the dir name):
      alpha → "Coder", beta → "Fixer", gamma → "Reviewer", delta → "Wanderer".

    The search behaviour we want to exercise:
      - "alpha"     matches only alpha (title)
      - "coder"     matches only alpha (agent_name=Coder; "developer" doesn't
                    contain "coder" so delta is NOT a hit)
      - "DEVELOPER" matches alpha + delta (case-insensitive agent_id)
      - "run"       matches alpha + beta (title substring, case-insensitive)
      - "reviewer"  matches only gamma (agent_name + agent_id)
    """
    _make(repo, "alpha", agent_id="developer", agent_dir="agents/coder",
          metadata={"title": "Alpha Run"})
    _make(repo, "beta", agent_id="fixer", agent_dir="agents/fixer",
          metadata={"title": "Beta Run"})
    _make(repo, "gamma", agent_id="reviewer", agent_dir="agents/reviewer",
          metadata={"title": "OTHER"})
    _make(repo, "delta", agent_id="developer", agent_dir="agents/wanderer")
    return repo


def _ids(instances):
    return sorted(inst.instance_id for inst in instances)


# ----- search by field --------------------------------------------------


class TestSearchByTitle:
    """Title matches against ``instance_metadata.title``."""

    def test_search_matches_title_substring(self, seeded_repo):
        instances, total = seeded_repo.list(search="alpha")
        assert total == 1
        assert _ids(instances) == ["alpha"]

    def test_search_no_match_returns_empty(self, seeded_repo):
        instances, total = seeded_repo.list(search="nothing-matches-this")
        assert total == 0
        assert instances == []

    def test_search_matches_title_with_title_set_to_none(self, repo):
        # title key absent in metadata => no title match, only name/id match
        _make(repo, "x", agent_id="nope", agent_dir="agents/nope")
        instances, total = repo.list(search="nope")
        # 'nope' matches both agent_id "nope" and agent_name "Nope"
        assert total == 1


class TestSearchByAgentName:
    """agent_name is auto-derived from agent_dir (Title Case)."""

    def test_search_matches_agent_name(self, seeded_repo):
        instances, total = seeded_repo.list(search="reviewer")
        assert total == 1
        assert _ids(instances) == ["gamma"]

    def test_search_matches_agent_name_case_insensitive(self, seeded_repo):
        # agent_name stored as "Coder"; lowercase query matches via ILIKE
        instances, total = seeded_repo.list(search="coder")
        # "coder" matches alpha (agent_name=Coder) only — note "developer"
        # agent_id does NOT contain "coder" as substring, so delta doesn't match.
        assert total == 1
        assert _ids(instances) == ["alpha"]


class TestSearchByAgentId:
    """agent_id is the literal string column."""

    def test_search_matches_agent_id(self, seeded_repo):
        instances, total = seeded_repo.list(search="fixer")
        assert total == 1
        assert _ids(instances) == ["beta"]

    def test_search_matches_agent_id_case_insensitive(self, seeded_repo):
        instances, total = seeded_repo.list(search="DEVELOPER")
        # alpha + delta both have agent_id="developer"
        assert total == 2
        assert _ids(instances) == ["alpha", "delta"]


class TestCaseInsensitivity:
    """ILIKE must be case-insensitive on both field types."""

    def test_title_match_uppercase_query(self, seeded_repo):
        instances, total = seeded_repo.list(search="ALPHA")
        assert total == 1
        assert _ids(instances) == ["alpha"]

    def test_title_match_mixed_case_query(self, seeded_repo):
        instances, total = seeded_repo.list(search="RuN")
        # alpha + beta both have title containing "Run"
        assert total == 2
        assert _ids(instances) == ["alpha", "beta"]


# ----- special character escaping ---------------------------------------


class TestSpecialCharEscaping:
    """``%`` and ``_`` must be escaped to literals; backslash is itself escaped."""

    def test_percent_is_treated_as_literal(self, repo):
        """``50%`` must only match the literal string '50%', not '50xyz'."""
        _make(repo, "literal", agent_id="x", agent_dir="agents/x",
              metadata={"title": "50% off sale"})
        _make(repo, "fuzzy", agent_id="x", agent_dir="agents/y",
              metadata={"title": "50xyz off sale"})
        instances, total = repo.list(search="50%")
        assert total == 1
        assert _ids(instances) == ["literal"]

    def test_underscore_is_treated_as_literal(self, repo):
        """``a_b`` must only match the literal 'a_b', not 'axb'."""
        _make(repo, "literal", agent_id="x", agent_dir="agents/x",
              metadata={"title": "value a_b here"})
        _make(repo, "fuzzy", agent_id="x", agent_dir="agents/y",
              metadata={"title": "value axb here"})
        instances, total = repo.list(search="a_b")
        assert total == 1
        assert _ids(instances) == ["literal"]

    def test_backslash_is_treated_as_literal(self, repo):
        """A backslash in the search term must match a backslash in the data."""
        _make(repo, "literal", agent_id="x", agent_dir="agents/x",
              metadata={"title": r"path\to\file"})
        _make(repo, "other", agent_id="x", agent_dir="agents/y",
              metadata={"title": r"pathXtoXfile"})
        instances, total = repo.list(search=r"\to")
        assert total == 1
        assert _ids(instances) == ["literal"]


# ----- empty / None search → no-op ------------------------------------


class TestEmptySearch:
    """``None`` and empty string must NOT filter anything."""

    def test_none_search_returns_all(self, seeded_repo):
        instances, total = seeded_repo.list(search=None)
        assert total == 4

    def test_empty_string_search_returns_all(self, seeded_repo):
        instances, total = seeded_repo.list(search="")
        assert total == 4

    def test_whitespace_only_search_treated_as_truthy(self, seeded_repo):
        """Whitespace is truthy and gets wrapped in ``% %`` — matches any
        title/name/id containing a space (alpha + beta titles contain spaces).
        """
        instances, total = seeded_repo.list(search=" ")
        # alpha + beta titles both contain " " (e.g. "Alpha Run").
        assert total == 2
        assert _ids(instances) == ["alpha", "beta"]


# ----- combination with other filters -----------------------------------


class TestSearchCombinedWithProjectId:
    """``search`` AND ``project_id`` must both apply."""

    def test_search_with_project_id_filter(self, repo):
        _make(repo, "p1-alpha", agent_id="dev", agent_dir="agents/coder",
              metadata={"title": "Alpha Run"}, project_id="proj-1")
        _make(repo, "p2-alpha", agent_id="dev", agent_dir="agents/coder",
              metadata={"title": "Alpha Run"}, project_id="proj-2")
        instances, total = repo.list(search="alpha", project_id="proj-1")
        assert total == 1
        assert _ids(instances) == ["p1-alpha"]


class TestSearchCombinedWithExcludeKb:
    """``search`` AND ``exclude_kb`` must both apply — KB agents excluded."""

    def test_search_excludes_kb_agents(self, repo):
        # KB-related agent_ids live in KB_AGENT_IDS (experiencer, kb-importer,
        # kb-writer). Title contains the search term on all three.
        _make(repo, "k1", agent_id="experiencer", agent_dir="agents/kb",
              metadata={"title": "Alpha Memory"})
        _make(repo, "k2", agent_id="kb-importer", agent_dir="agents/kb",
              metadata={"title": "Alpha Importer"})
        _make(repo, "u1", agent_id="developer", agent_dir="agents/coder",
              metadata={"title": "Alpha Run"})

        instances, total = repo.list(search="alpha", exclude_kb=True)
        assert total == 1
        assert _ids(instances) == ["u1"]


class TestSearchWithPagination:
    """Pagination over the filtered set must work (limit + offset on search)."""

    def test_pagination_applies_after_search_filter(self, repo):
        # Seed 5 matching + 2 non-matching instances.
        for i in range(5):
            _make(repo, f"hit-{i}", agent_id="dev", agent_dir="agents/coder",
                  metadata={"title": f"Hit {i}"})
        _make(repo, "miss-1", agent_id="dev", agent_dir="agents/coder",
              metadata={"title": "Other 1"})
        _make(repo, "miss-2", agent_id="dev", agent_dir="agents/coder",
              metadata={"title": "Other 2"})

        # Page 1
        page1, total = repo.list(search="hit", limit=2, offset=0)
        assert total == 5
        assert len(page1) == 2

        # Page 2
        page2, _ = repo.list(search="hit", limit=2, offset=2)
        assert len(page2) == 2

        # Page 3 (only 1 remaining)
        page3, _ = repo.list(search="hit", limit=2, offset=4)
        assert len(page3) == 1

        # All page results are distinct and from the matching set.
        all_paged = (
            [i.instance_id for i in page1]
            + [i.instance_id for i in page2]
            + [i.instance_id for i in page3]
        )
        assert sorted(all_paged) == sorted(f"hit-{i}" for i in range(5))


class TestSearchWithIncludeDescendants:
    """``search`` must apply to the BFS child queries too (defense-in-depth)."""

    def test_search_filters_descendants(self, repo):
        _make(repo, "root", agent_id="dev", agent_dir="agents/coder",
              metadata={"title": "Root Hit"}, project_id="proj-x")
        _make(repo, "child-hit", agent_id="dev", agent_dir="agents/coder",
              parent_id="root", project_id="proj-x",
              metadata={"title": "Child Hit"})
        _make(repo, "child-miss", agent_id="dev", agent_dir="agents/coder",
              parent_id="root", project_id="proj-x",
              metadata={"title": "Child Miss"})

        instances, total = repo.list(
            search="hit", include_descendants=True, project_id="proj-x"
        )
        # root count + descendants matching "hit"
        assert total == 1  # only root matches search (children don't enter count)
        ids = _ids(instances)
        assert "root" in ids
        assert "child-hit" in ids
        assert "child-miss" not in ids

    def test_search_with_empty_string_is_noop_with_descendants(self, seeded_repo):
        # Seed a child for root "alpha"
        _make(seeded_repo, "alpha-child", agent_id="fixer",
              agent_dir="agents/fixer", parent_id="alpha",
              metadata={"title": "Alpha Child"})

        instances, total = seeded_repo.list(
            search="", include_descendants=True
        )
        # No filter: 4 roots + 1 child = 5 instances.
        assert total == 4
        assert len(instances) == 5