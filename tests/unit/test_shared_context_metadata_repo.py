"""Unit tests for ``SharedContextMetadataRepository``.

Mirrors the engine-setup pattern used in ``tests/repositories/conftest.py``
and ``tests/repositories/infra/conftest.py``: an in-memory SQLite engine via
``StaticPool`` so the database survives across threads, with the
``shared_context_metadata`` table created on fixture setup.

The tests target the CRUD surface required by the Shared Context Metadata
KV system (Phase 1) — the same operations the tool layer and the system
prompt injection layer rely on.
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.repositories.shared_context.models import SharedContextMetadata
from daemon.repositories.shared_context.repository import (
    SharedContextMetadataRepository,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine with the ``shared_context_metadata`` table.

    Uses ``StaticPool`` (per the project's standard pattern) so the
    in-memory database is shared across threads. ``SQLModel.metadata.create_all``
    creates every table currently registered on the global SQLModel
    metadata; importing :class:`SharedContextMetadata` registers it.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Reference the model so static analyzers don't flag it as unused —
    # the import already registers the table on SQLModel.metadata, but
    # the explicit reference documents the dependency for readers.
    _ = SharedContextMetadata
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repo(engine):
    """A :class:`SharedContextMetadataRepository` bound to the test engine."""
    return SharedContextMetadataRepository(engine)


@pytest.fixture
def context_key() -> str:
    """Default ``context_key`` used by most tests."""
    return "ctx-default"


# ─── CREATE / UPSERT ──────────────────────────────────────────────────────────


class TestSetMany:
    """Tests for :meth:`SharedContextMetadataRepository.set_many`."""

    def test_set_many_creates_new(self, repo, context_key):
        """Upserting three new keys inserts three rows and round-trips via ``get_all``."""
        repo.set_many(
            context_key,
            {
                "project_scope": "LARGE",
                "priority": 1,
                "topic": "decisions",
            },
        )

        rows = repo.get_all(context_key)
        assert len(rows) == 3
        keys = {r.meta_key for r in rows}
        assert keys == {"project_scope", "priority", "topic"}

    def test_set_many_updates_existing(self, repo, context_key):
        """Upserting an existing key updates its value (does not duplicate)."""
        repo.set_many(context_key, {"priority": 1})
        # Re-upsert with a new value for the same key.
        repo.set_many(context_key, {"priority": 99})

        rows = repo.get_all(context_key)
        assert len(rows) == 1
        assert rows[0].meta_key == "priority"
        assert rows[0].meta_value == 99

        # ``get_all_as_dict`` reflects the new value too.
        assert repo.get_all_as_dict(context_key) == {"priority": 99}

    def test_set_many_persists_complex_values(self, repo, context_key):
        """JSONB-typed values (dict / list / None) round-trip through the table."""
        repo.set_many(
            context_key,
            {
                "struct": {"nested": [1, 2, 3], "flag": True},
                "items": ["a", "b", "c"],
                "absent": None,
            },
        )

        snapshot = repo.get_all_as_dict(context_key)
        assert snapshot == {
            "struct": {"nested": [1, 2, 3], "flag": True},
            "items": ["a", "b", "c"],
            "absent": None,
        }

    def test_set_many_empty_dict_is_noop(self, repo, context_key):
        """``set_many`` with an empty dict inserts nothing and returns ``[]``."""
        result = repo.set_many(context_key, {})
        assert result == []
        assert repo.get_all(context_key) == []


# ─── READ ──────────────────────────────────────────────────────────────────────


class TestRead:
    """Tests for the read-side helpers."""

    def test_get_all_as_dict(self, repo, context_key):
        """``get_all_as_dict`` returns ``{meta_key: meta_value}`` for the context."""
        repo.set_many(
            context_key,
            {
                "k1": "v1",
                "k2": 42,
                "k3": {"nested": True},
            },
        )

        snapshot = repo.get_all_as_dict(context_key)
        assert snapshot == {
            "k1": "v1",
            "k2": 42,
            "k3": {"nested": True},
        }

    def test_get_many(self, repo, context_key):
        """``get_many`` returns only the requested keys, not the rest."""
        repo.set_many(
            context_key,
            {"a": 1, "b": 2, "c": 3},
        )

        rows = repo.get_many(context_key, ["a", "c"])
        keys = {r.meta_key for r in rows}
        values = {r.meta_key: r.meta_value for r in rows}
        assert keys == {"a", "c"}
        assert values == {"a": 1, "c": 3}

    def test_get_many_empty_keys_returns_empty(self, repo, context_key):
        """``get_many`` with an empty ``keys`` list short-circuits to ``[]``."""
        repo.set_many(context_key, {"a": 1})
        assert repo.get_many(context_key, []) == []

    def test_get_many_unknown_keys_returns_empty(self, repo, context_key):
        """``get_many`` with no matching keys returns ``[]`` (no error)."""
        repo.set_many(context_key, {"a": 1})
        assert repo.get_many(context_key, ["nope"]) == []


# ─── DELETE ────────────────────────────────────────────────────────────────────


class TestDelete:
    """Tests for the delete helpers."""

    def test_delete_many(self, repo, context_key):
        """``delete_many`` removes the requested keys and returns the count."""
        repo.set_many(context_key, {"a": 1, "b": 2, "c": 3})

        deleted = repo.delete_many(context_key, ["a", "c"])
        assert deleted == 2

        remaining = repo.get_all_as_dict(context_key)
        assert remaining == {"b": 2}

    def test_delete_many_empty_keys_returns_zero(self, repo, context_key):
        """``delete_many`` with an empty ``keys`` list is a no-op."""
        repo.set_many(context_key, {"a": 1})
        assert repo.delete_many(context_key, []) == 0
        assert repo.get_all_as_dict(context_key) == {"a": 1}

    def test_delete_many_unknown_keys_returns_zero(self, repo, context_key):
        """``delete_many`` with no matching keys returns ``0`` (no error)."""
        repo.set_many(context_key, {"a": 1})
        assert repo.delete_many(context_key, ["nope"]) == 0
        assert repo.get_all_as_dict(context_key) == {"a": 1}

    def test_delete_all(self, repo, context_key):
        """``delete_all`` removes every row for ``context_key`` and returns the count."""
        repo.set_many(context_key, {"a": 1, "b": 2, "c": 3})

        deleted = repo.delete_all(context_key)
        assert deleted == 3
        assert repo.get_all(context_key) == []
        assert repo.get_all_as_dict(context_key) == {}

    def test_delete_all_empty_context_returns_zero(self, repo, context_key):
        """``delete_all`` on a context with no rows returns ``0``."""
        assert repo.delete_all(context_key) == 0


# ─── ISOLATION ─────────────────────────────────────────────────────────────────


class TestIsolation:
    """Tests that ``context_key`` partitions the table correctly."""

    def test_empty_context_key_returns_empty_dict(self, repo):
        """``get_all_as_dict`` on a non-existent ``context_key`` returns ``{}``."""
        assert repo.get_all_as_dict("never-existed") == {}

    def test_multiple_context_keys_isolated(self, repo):
        """Rows under two different ``context_key``s do not bleed across partitions."""
        repo.set_many("ctx-A", {"shared": "A-value", "only-A": 1})
        repo.set_many("ctx-B", {"shared": "B-value", "only-B": 2})

        assert repo.get_all_as_dict("ctx-A") == {
            "shared": "A-value",
            "only-A": 1,
        }
        assert repo.get_all_as_dict("ctx-B") == {
            "shared": "B-value",
            "only-B": 2,
        }

    def test_delete_all_is_scoped_to_context_key(self, repo):
        """``delete_all`` only removes rows for the given ``context_key``."""
        repo.set_many("ctx-A", {"a": 1})
        repo.set_many("ctx-B", {"b": 2})

        deleted = repo.delete_all("ctx-A")
        assert deleted == 1

        # ctx-A is wiped, ctx-B is untouched.
        assert repo.get_all_as_dict("ctx-A") == {}
        assert repo.get_all_as_dict("ctx-B") == {"b": 2}