"""PostgreSQL test for the ``search`` parameter on SQLModelInstanceRepository.list().

Why this test exists
--------------------

PostgreSQL is the **PRIMARY** production database for the daemon. The
instance search feature (``SQLModelInstanceRepository.list(search=...)``)
ships a dialect-aware title expression at
``daemon/repositories/instance/repository.py:_build_search_condition``:

* SQLite path: ``CAST(json_extract(metadata, '$.title') AS VARCHAR)``
* PostgreSQL path: ``CAST(metadata->>'title' AS VARCHAR)``

Those two expressions emit different SQL and round-trip differently. The
SQLite test (``tests/test_instance_search.py``) covers the SQLite path
against an in-memory database; it cannot catch a bug that only fires on
the PostgreSQL ``->>`` JSONB text-extraction operator (e.g. the cast
returning NULL where SQLite returns 'null', an ILIKE on a non-string
JSONB scalar, or whitespace handling differences between PG's ILIKE
and SQLite's LIKE-with-LOWER).

Run with::

    pytest tests/postgres/test_instance_search_pg.py \\
        -m postgres --override-ini="addopts=" -v

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the
entire module cleanly when PostgreSQL is not reachable.

What this test covers
---------------------

* **PG JSONB title path** (the headline case): substring match against
  ``metadata->>'title'`` cast to VARCHAR.
* Search by ``agent_name`` (PG column).
* Search by ``agent_id`` (PG column).
* Case-insensitivity (PG ILIKE on text columns + cast JSONB text).
* ``%`` / ``_`` / ``\\`` wildcard escaping on PG.
* ``None`` / empty / whitespace search → no-op.
* Combination with ``project_id`` and ``exclude_kb`` filters.
* Pagination with search filter.
* BFS child query applies search (defense-in-depth).

Out of scope (covered in the SQLite test)
-----------------------------------------

* Flat-pagination edge cases that have no dialect-specific code path.
* Whitespace-only search treat-as-truthy behavior — the Python branch
  is identical on both backends.
"""
from __future__ import annotations

import pytest

from daemon.repositories.instance.repository import (
    KB_AGENT_IDS,
    SQLModelInstanceRepository,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(pg_repository_factory) -> SQLModelInstanceRepository:
    """Real ``SQLModelInstanceRepository`` bound to the PG engine.

    Uses the standard ``pg_repository_factory`` from
    ``tests/postgres/conftest.py``. Cleanup between tests is handled by
    the autouse ``_pg_truncate_tables`` fixture in the conftest — no
    per-test teardown needed here.
    """
    return pg_repository_factory(SQLModelInstanceRepository)


def _make(
    repo: SQLModelInstanceRepository,
    instance_id: str,
    agent_id: str,
    agent_dir: str,
    *,
    metadata: dict | None = None,
    parent_id: str | None = None,
    project_id: str | None = None,
    status: str = "idle",
):
    """Insert an instance via the repository (exercises PG JSONB writes).

    Wraps ``repo.create`` so each test stays focused on data shape, not
    on the repository's surface area. ``agent_name`` is derived from
    ``agent_dir`` via ``Path(agent_dir).name.title()`` (mirrors the
    SQLite test seed).
    """
    return repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        metadata=metadata or {},
        parent_id=parent_id,
        project_id=project_id,
        status=status,
    )


def _ids(instances) -> list[str]:
    return sorted(inst.instance_id for inst in instances)


@pytest.fixture
def seeded_repo(repo):
    """Seed instances exercising title / agent_name / agent_id on PG JSONB.

    Layout (identical to the SQLite ``seeded_repo`` fixture so the two
    test files can be compared side-by-side):

      - "alpha"  : title="Alpha Run",   agent_id="developer", agent_dir="agents/coder"
      - "beta"   : title="Beta Run",    agent_id="fixer",      agent_dir="agents/fixer"
      - "gamma"  : title="OTHER",       agent_id="reviewer",   agent_dir="agents/reviewer"
      - "delta"  : title=None,          agent_id="developer",  agent_dir="agents/wanderer"

    agent_name derivation (``Path(agent_dir).name.title()``):
      alpha → "Coder", beta → "Fixer", gamma → "Reviewer", delta → "Wanderer".
    """
    _make(repo, "alpha", agent_id="developer", agent_dir="agents/coder",
          metadata={"title": "Alpha Run"})
    _make(repo, "beta", agent_id="fixer", agent_dir="agents/fixer",
          metadata={"title": "Beta Run"})
    _make(repo, "gamma", agent_id="reviewer", agent_dir="agents/reviewer",
          metadata={"title": "OTHER"})
    _make(repo, "delta", agent_id="developer", agent_dir="agents/wanderer")
    return repo


# ---------------------------------------------------------------------------
# Headline test: PG JSONB title path
# ---------------------------------------------------------------------------


class TestSearchByTitleJsonb:
    """Substring match against ``metadata->>'title'`` (PG JSONB extraction).

    This is the test that justifies this file's existence. SQLite's path
    is ``json_extract(metadata, '$.title')``; PG's path is
    ``metadata->>'title'``. The Python ``search`` term is wrapped in
    ``%...%`` and run through ILIKE on the cast-to-VARCHAR value.

    If a regression breaks the PG-only branch
    (``sa_cast(Instance.instance_metadata['title'], String)`` in
    ``_build_search_condition``) these tests fail while the SQLite tests
    still pass — which is the whole point of having a separate PG test.
    """

    def test_search_matches_title_substring_on_jsonb(self, seeded_repo):
        instances, total = seeded_repo.list(search="alpha")
        assert total == 1
        assert _ids(instances) == ["alpha"]

    def test_search_no_match_returns_empty(self, seeded_repo):
        instances, total = seeded_repo.list(search="nothing-matches-this")
        assert total == 0
        assert instances == []

    def test_search_matches_title_when_title_key_present(self, repo):
        """``metadata->>'title'`` returns the stored string on PG."""
        _make(repo, "only", agent_id="dev", agent_dir="agents/coder",
              metadata={"title": "Unique-Title-Marker"})
        instances, total = repo.list(search="unique-title-marker")
        assert total == 1
        assert _ids(instances) == ["only"]

    def test_search_skips_when_title_absent_in_metadata(self, repo):
        """``metadata->>'title'`` returns NULL on PG for missing key;
        the ILIKE on NULL yields no match, so non-title columns (agent_id,
        agent_name) are the only way to match.
        """
        _make(repo, "no-title", agent_id="nope", agent_dir="agents/nope")
        # "nope" doesn't appear in title (no title key) but does in
        # agent_id and agent_name — assert the row is still found via
        # those columns and that no extra row sneaks in via a stray
        # NULL→'' coercion.
        instances, total = repo.list(search="nope")
        assert total == 1
        assert _ids(instances) == ["no-title"]

    def test_search_handles_non_string_title_value(self, repo):
        """``metadata->>'title'`` returns TEXT on PG even when the JSONB
        value is a non-string scalar (e.g. an int). The repository's
        VARCHAR cast must coerce it to a string for ILIKE — otherwise
        PG raises ``function ilike(text, text) does not exist`` or
        returns a type mismatch.
        """
        _make(repo, "int-title", agent_id="dev", agent_dir="agents/coder",
              metadata={"title": 42})
        instances, total = repo.list(search="42")
        assert total == 1
        assert _ids(instances) == ["int-title"]

    def test_search_title_with_extra_metadata_keys(self, repo):
        """Other keys in the JSONB column must not interfere with title
        extraction. ``metadata->>'title'`` is a single key lookup so
        sibling keys are irrelevant, but a regression that switches to
        ``jsonb_path_query`` or ``metadata::text`` would break here.
        """
        _make(repo, "rich", agent_id="dev", agent_dir="agents/coder",
              metadata={"title": "Findable", "extra": "ignored", "n": 7})
        instances, total = repo.list(search="findable")
        assert total == 1
        assert _ids(instances) == ["rich"]


# ---------------------------------------------------------------------------
# Search by agent_name / agent_id
# ---------------------------------------------------------------------------


class TestSearchByAgentName:
    """``agent_name`` is a plain text column on PG; the search path is
    the same as the SQLite path but exercises PG's ILIKE collation."""

    def test_search_matches_agent_name(self, seeded_repo):
        instances, total = seeded_repo.list(search="reviewer")
        assert total == 1
        assert _ids(instances) == ["gamma"]

    def test_search_matches_agent_name_case_insensitive(self, seeded_repo):
        # agent_name stored as "Coder"; lowercase query matches via ILIKE.
        # "developer" agent_id does NOT contain "coder", so delta is excluded.
        instances, total = seeded_repo.list(search="coder")
        assert total == 1
        assert _ids(instances) == ["alpha"]


class TestSearchByAgentId:
    """``agent_id`` is a plain text column on PG."""

    def test_search_matches_agent_id(self, seeded_repo):
        instances, total = seeded_repo.list(search="fixer")
        assert total == 1
        assert _ids(instances) == ["beta"]

    def test_search_matches_agent_id_case_insensitive(self, seeded_repo):
        instances, total = seeded_repo.list(search="DEVELOPER")
        # alpha + delta both have agent_id="developer"
        assert total == 2
        assert _ids(instances) == ["alpha", "delta"]


# ---------------------------------------------------------------------------
# Case-insensitivity on the PG JSONB title path
# ---------------------------------------------------------------------------


class TestCaseInsensitivity:
    """ILIKE must be case-insensitive on both regular text columns and
    the CAST JSONB→VARCHAR expression. PG's ILIKE is built-in; the
    question is whether the CAST preserves the string semantics."""

    def test_title_match_uppercase_query(self, seeded_repo):
        instances, total = seeded_repo.list(search="ALPHA")
        assert total == 1
        assert _ids(instances) == ["alpha"]

    def test_title_match_mixed_case_query(self, seeded_repo):
        instances, total = seeded_repo.list(search="RuN")
        # alpha + beta both have title containing "Run"
        assert total == 2
        assert _ids(instances) == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Wildcard escaping
# ---------------------------------------------------------------------------


class TestSpecialCharEscaping:
    """``%`` and ``_`` must be escaped to literals; backslash is itself
    escaped. The Python escaping is dialect-independent, but PG's LIKE
    evaluator differs subtly from SQLite's so we confirm both."""

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


# ---------------------------------------------------------------------------
# Empty / None search → no-op
# ---------------------------------------------------------------------------


class TestEmptySearch:
    """``None`` and empty string must NOT add a WHERE clause."""

    def test_none_search_returns_all(self, seeded_repo):
        instances, total = seeded_repo.list(search=None)
        assert total == 4

    def test_empty_string_search_returns_all(self, seeded_repo):
        instances, total = seeded_repo.list(search="")
        assert total == 4


# ---------------------------------------------------------------------------
# Combination with other filters
# ---------------------------------------------------------------------------


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
        # KB-related agent_ids live in KB_AGENT_IDS (experiencer,
        # kb-importer, kb-writer). Title contains the search term on all
        # three KB rows and on the one user row.
        _make(repo, "k1", agent_id="experiencer", agent_dir="agents/kb",
              metadata={"title": "Alpha Memory"})
        _make(repo, "k2", agent_id="kb-importer", agent_dir="agents/kb",
              metadata={"title": "Alpha Importer"})
        _make(repo, "u1", agent_id="developer", agent_dir="agents/coder",
              metadata={"title": "Alpha Run"})

        instances, total = repo.list(search="alpha", exclude_kb=True)
        assert total == 1
        assert _ids(instances) == ["u1"]

    def test_search_includes_kb_agents_when_exclude_kb_false(self, repo):
        """The inverse: with ``exclude_kb=False``, KB rows matching the
        search must come back. Confirms the filter composition order
        (exclude_kb is NOT inlined into the search predicate)."""
        _make(repo, "k1", agent_id="experiencer", agent_dir="agents/kb",
              metadata={"title": "Alpha Memory"})
        _make(repo, "u1", agent_id="developer", agent_dir="agents/coder",
              metadata={"title": "Alpha Run"})

        # Sanity: KB_AGENT_IDS is in sync with the comment in the SQLite test.
        assert "experiencer" in KB_AGENT_IDS

        instances, total = repo.list(search="alpha", exclude_kb=False)
        assert total == 2
        assert _ids(instances) == ["k1", "u1"]


# ---------------------------------------------------------------------------
# Pagination with search
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# include_descendants (BFS path) on PG
# ---------------------------------------------------------------------------


class TestSearchWithIncludeDescendants:
    """``search`` must apply to the BFS child queries too (defense-in-depth).

    On PG this exercises the second WHERE inside the iterative BFS
    loop, which uses the same ``search_cond`` predicate. If the BFS
    child query forgot to apply ``search_cond``, the descendants would
    leak through and the assertion below would fail."""

    def test_search_filters_descendants_on_bfs(self, repo):
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
        # Root count is 1 (only "root" matches the search); descendants
        # are loaded but must also satisfy the search predicate, so
        # "child-hit" appears and "child-miss" does not.
        assert total == 1
        ids = _ids(instances)
        assert "root" in ids
        assert "child-hit" in ids
        assert "child-miss" not in ids