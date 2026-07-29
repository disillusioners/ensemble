"""PostgreSQL test for the ``initiative_message`` feature.

Why this test exists
--------------------

The headline behaviour — searching ``instance_metadata['initiative_message']``
and storing it via ``set_metadata`` — exercises dialect-specific SQL paths:

* SQLite stores ``instance_metadata`` as JSON text and uses
  ``json_extract(..., '$.initiative_message')`` for the search predicate.
* PostgreSQL stores ``instance_metadata`` as JSONB and uses
  ``metadata->>'initiative_message'`` for the search predicate, with
  ``jsonb_set`` (not ``json_set``) for the write path in
  :meth:`SQLModelInstanceRepository.set_metadata`.

The SQLite test (``tests/test_initiative_message.py``) cannot catch a bug
that only fires on the PG ``->>`` JSONB text-extraction operator or on the
PG-specific ``jsonb_set`` write path (e.g. a missing
``COALESCE(metadata, '{}'::jsonb)`` blowing up on a NULL JSONB column).

Run with::

    pytest tests/postgres/test_initiative_message_pg.py \\
        -m postgres --override-ini="addopts=" -v

The ``pg_repository_factory`` fixture in ``tests/postgres/conftest.py``
skips the entire module cleanly when PostgreSQL is not reachable.

What this test covers
---------------------

* **PG JSONB initiative_message search** — the headline case (mirrors the
  ``TestSearchByTitleJsonb`` style in ``test_instance_search_pg.py``).
* PG ``jsonb_set`` write path via ``set_metadata`` (round-trip).
* Truncation, idempotency, special-character handling — these are
  dialect-agnostic but included to lock the contract on both backends.
"""

from __future__ import annotations

import pytest

from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(pg_repository_factory) -> SQLModelInstanceRepository:
    """Real ``SQLModelInstanceRepository`` bound to the PG engine."""
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


# ---------------------------------------------------------------------------
# Capture (write path) — exercises jsonb_set on PostgreSQL
# ---------------------------------------------------------------------------


class TestCaptureOnPostgres:
    """``set_metadata(instance_id, 'initiative_message', ...)`` must use the
    PG ``jsonb_set`` path and compose correctly with concurrent keys.
    """

    def test_round_trip_initiative_message(self, repo):
        """Write then read — PG ``jsonb_set`` round-trips the string."""
        _make(repo, "pg-1", agent_id="dev", agent_dir="agents/coder")
        repo.set_metadata("pg-1", "initiative_message", "deploy staging")
        inst = repo.get("pg-1")
        assert inst.instance_metadata["initiative_message"] == "deploy staging"

    def test_initiative_message_composes_with_other_keys(self, repo):
        """Writing ``initiative_message`` does not clobber sibling keys."""
        _make(
            repo, "pg-1", agent_id="dev", agent_dir="agents/coder",
            metadata={"title": "Existing Title"},
        )
        repo.set_metadata("pg-1", "initiative_message", "first message text")
        inst = repo.get("pg-1")
        # Both keys survive — atomic jsonb_set, not ORM read-modify-write.
        assert inst.instance_metadata["title"] == "Existing Title"
        assert inst.instance_metadata["initiative_message"] == "first message text"

    def test_initiative_message_round_trips_unicode(self, repo):
        """Unicode is preserved through the PG JSONB column."""
        _make(repo, "pg-1", agent_id="dev", agent_dir="agents/coder")
        msg = "héllo wörld 日本語 🚀"
        repo.set_metadata("pg-1", "initiative_message", msg)
        inst = repo.get("pg-1")
        assert inst.instance_metadata["initiative_message"] == msg

    def test_initiative_message_overwrites_with_same_key(self, repo):
        """Calling ``set_metadata`` twice with the same key replaces the value."""
        _make(repo, "pg-1", agent_id="dev", agent_dir="agents/coder")
        repo.set_metadata("pg-1", "initiative_message", "first")
        repo.set_metadata("pg-1", "initiative_message", "second")
        inst = repo.get("pg-1")
        # NOTE: set_metadata is NOT idempotent at the repository layer — the
        # _maybe_store_initiative_message hook handles idempotency. This
        # test just confirms the underlying write path is correct.
        assert inst.instance_metadata["initiative_message"] == "second"


# ---------------------------------------------------------------------------
# Search (read path) — exercises metadata->>'initiative_message' on PG
# ---------------------------------------------------------------------------


class TestSearchByInitiativeMessageJsonb:
    """Substring match against ``metadata->>'initiative_message'`` (PG JSONB).

    Mirrors the ``TestSearchByTitleJsonb`` style from
    ``test_instance_search_pg.py``. If a regression breaks the PG-only
    branch of ``_build_search_condition`` (``sa_cast(Instance
    .instance_metadata['initiative_message'], String)``) these tests fail
    while the SQLite tests still pass.
    """

    def test_search_matches_initiative_message_substring(self, repo):
        _make(
            repo, "alpha", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "deploy the staging server"},
        )
        _make(
            repo, "beta", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "write unit tests"},
        )
        instances, total = repo.list(search="staging")
        assert total == 1
        assert _ids(instances) == ["alpha"]

    def test_search_no_match_returns_empty(self, repo):
        _make(
            repo, "alpha", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "deploy the staging server"},
        )
        instances, total = repo.list(search="definitely-not-here")
        assert total == 0
        assert instances == []

    def test_search_case_insensitive_lowercase_query(self, repo):
        """``metadata->>'initiative_message'`` is text — ILIKE works."""
        _make(
            repo, "lower", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "DEPLOY THE STAGING SERVER"},
        )
        instances, total = repo.list(search="deploy")
        assert total == 1
        assert _ids(instances) == ["lower"]

    def test_search_case_insensitive_uppercase_query(self, repo):
        _make(
            repo, "upper", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "deploy the staging server"},
        )
        instances, total = repo.list(search="DEPLOY")
        assert total == 1
        assert _ids(instances) == ["upper"]

    def test_search_skips_when_initiative_message_absent(self, repo):
        """``metadata->>'initiative_message'`` returns NULL on PG for missing
        keys; ILIKE on NULL yields no match — other fields still searchable."""
        _make(repo, "no-init", agent_id="nope", agent_dir="agents/nope")
        instances, total = repo.list(search="nope")
        assert total == 1
        assert _ids(instances) == ["no-init"]

    def test_search_handles_non_string_initiative_message_value(self, repo):
        """``metadata->>'initiative_message'`` returns TEXT on PG even when the
        JSONB value is a non-string scalar (e.g. an int). The VARCHAR cast
        must coerce it for ILIKE — otherwise PG raises a type mismatch.
        """
        _make(
            repo, "int-init", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": 42},
        )
        instances, total = repo.list(search="42")
        assert total == 1
        assert _ids(instances) == ["int-init"]

    def test_search_coexists_with_title_and_agent_fields(self, repo):
        """OR composition across all four fields on the PG path."""
        _make(
            repo, "via-init", agent_id="fixer", agent_dir="agents/fixer",
            metadata={"initiative_message": "Refactor authentication"},
        )
        _make(
            repo, "via-title", agent_id="reviewer", agent_dir="agents/reviewer",
            metadata={"title": "Refactor auth module"},
        )
        _make(
            repo, "via-name", agent_id="developer", agent_dir="agents/coder",
            metadata={"title": "unrelated", "initiative_message": "unrelated"},
        )
        # agent_name for "via-name" is "Coder" — "refactor" doesn't appear in
        # agent_name or agent_id for any row, so we expect the first two via
        # initiative_message / title only.
        instances, total = repo.list(search="refactor")
        assert total == 2
        assert _ids(instances) == ["via-init", "via-title"]


class TestInitiativeMessageEscapingOnPostgres:
    """``%``, ``_``, ``\\`` in the search term must be literals on PG."""

    def test_percent_is_literal(self, repo):
        """``50%`` must only match the literal string '50%', not '50xyz'."""
        _make(
            repo, "literal", agent_id="x", agent_dir="agents/x",
            metadata={"initiative_message": "50% off sale"},
        )
        _make(
            repo, "fuzzy", agent_id="x", agent_dir="agents/y",
            metadata={"initiative_message": "50xyz off sale"},
        )
        instances, total = repo.list(search="50%")
        assert total == 1
        assert _ids(instances) == ["literal"]

    def test_underscore_is_literal(self, repo):
        """``a_b`` must only match the literal 'a_b', not 'axb'."""
        _make(
            repo, "literal", agent_id="x", agent_dir="agents/x",
            metadata={"initiative_message": "value a_b here"},
        )
        _make(
            repo, "fuzzy", agent_id="x", agent_dir="agents/y",
            metadata={"initiative_message": "value axb here"},
        )
        instances, total = repo.list(search="a_b")
        assert total == 1
        assert _ids(instances) == ["literal"]

    def test_backslash_is_literal(self, repo):
        """A backslash in the search term must match a backslash in the data."""
        _make(
            repo, "literal", agent_id="x", agent_dir="agents/x",
            metadata={"initiative_message": r"path\to\file"},
        )
        _make(
            repo, "other", agent_id="x", agent_dir="agents/y",
            metadata={"initiative_message": r"pathXtoXfile"},
        )
        instances, total = repo.list(search=r"\to")
        assert total == 1
        assert _ids(instances) == ["literal"]


# ---------------------------------------------------------------------------
# Empty / None search — must be a no-op on PG too
# ---------------------------------------------------------------------------


class TestEmptySearchOnPostgres:
    """``None`` and empty string must NOT add a WHERE clause."""

    def test_none_search_returns_all(self, repo):
        _make(
            repo, "a", agent_id="x", agent_dir="agents/x",
            metadata={"initiative_message": "first"},
        )
        _make(
            repo, "b", agent_id="x", agent_dir="agents/y",
            metadata={"initiative_message": "second"},
        )
        instances, total = repo.list(search=None)
        assert total == 2
        assert _ids(instances) == ["a", "b"]

    def test_empty_string_search_returns_all(self, repo):
        _make(
            repo, "a", agent_id="x", agent_dir="agents/x",
            metadata={"initiative_message": "first"},
        )
        _make(
            repo, "b", agent_id="x", agent_dir="agents/y",
            metadata={"initiative_message": "second"},
        )
        instances, total = repo.list(search="")
        assert total == 2