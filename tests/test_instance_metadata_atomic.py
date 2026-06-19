"""Tests for atomic instance_metadata operations on the instance repository.

Regression coverage for Fix C7 — the read-modify-write race in the original
``update_title`` / ``set_metadata`` / ``delete_metadata`` methods, which used
``flag_modified`` after in-place Python ``dict`` mutation. Concurrent calls
targeting different keys silently overwrote each other because the ORM
replaced the entire JSON column on commit.

The fixed implementation uses dialect-aware single-statement UPDATEs:

* PostgreSQL: ``jsonb_set`` (set) / ``metadata - key`` (delete)
* SQLite:     ``json_set`` (set) / ``json_remove`` (delete)

with ``COALESCE(metadata, '{}' ...)`` so it works when the column is NULL.

Run with::

    python -m pytest tests/test_instance_metadata_atomic.py -x -q
"""

from __future__ import annotations

import os
import tempfile
import threading

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories.instance import SQLModelInstanceRepository


@pytest.fixture
def engine():
    """File-backed SQLite engine (in-memory doesn't share across threads)."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = create_engine(f"sqlite:///{db_path}", echo=False)
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()
    os.unlink(db_path)


@pytest.fixture
def repo(engine):
    """Repository bound to the shared engine."""
    return SQLModelInstanceRepository(engine)


def _create(repo: SQLModelInstanceRepository, instance_id: str = "test-instance") -> None:
    repo.create(instance_id=instance_id, agent_id="test", agent_dir="agents/test")


# =============================================================================
# Functional correctness
# =============================================================================


class TestSetMetadataFunctional:
    """Basic functional checks for ``set_metadata``."""

    def test_set_first_key(self, repo):
        _create(repo)
        result = repo.set_metadata("test-instance", "foo", "bar")
        assert result is not None
        assert result.instance_metadata.get("foo") == "bar"

    def test_set_overwrites_existing_key(self, repo):
        _create(repo)
        repo.set_metadata("test-instance", "foo", "first")
        repo.set_metadata("test-instance", "foo", "second")
        result = repo.set_metadata("test-instance", "foo", "third")
        assert result.instance_metadata["foo"] == "third"

    def test_set_preserves_sibling_keys(self, repo):
        """Setting one key must not clobber another (was the bug)."""
        _create(repo)
        repo.set_metadata("test-instance", "alpha", 1)
        repo.set_metadata("test-instance", "beta", 2)
        result = repo.set_metadata("test-instance", "gamma", 3)
        assert result.instance_metadata == {"alpha": 1, "beta": 2, "gamma": 3}

    def test_set_preserves_complex_value_types(self, repo):
        """Strings, ints, lists, dicts, bools, None round-trip cleanly."""
        _create(repo)
        repo.set_metadata("test-instance", "s", "hello")
        repo.set_metadata("test-instance", "n", 42)
        repo.set_metadata("test-instance", "lst", [1, 2, 3])
        repo.set_metadata("test-instance", "d", {"x": 1, "y": "z"})
        repo.set_metadata("test-instance", "b", True)
        repo.set_metadata("test-instance", "null", None)

        result = repo.get("test-instance")
        assert result.instance_metadata["s"] == "hello"
        assert result.instance_metadata["n"] == 42
        assert result.instance_metadata["lst"] == [1, 2, 3]
        assert result.instance_metadata["d"] == {"x": 1, "y": "z"}
        assert result.instance_metadata["b"] is True
        assert result.instance_metadata["null"] is None

    def test_set_returns_enriched_instance(self, repo):
        """Returned instance should have children list populated."""
        repo.create(instance_id="i1", agent_id="test", agent_dir="agents/test")
        repo.create(
            instance_id="i2",
            agent_id="test",
            agent_dir="agents/test",
            parent_id="i1",
        )

        result = repo.set_metadata("i1", "foo", "bar")
        assert result is not None
        assert "i2" in result.children

    def test_set_returns_none_for_missing_instance(self, repo):
        assert repo.set_metadata("missing", "k", "v") is None


class TestUpdateTitleFunctional:
    """``update_title`` must keep its public contract."""

    def test_update_title_for_existing_instance(self, repo):
        _create(repo)
        result = repo.update_title("test-instance", "My Title")
        assert result is not None
        assert result.instance_metadata.get("title") == "My Title"

    def test_update_title_overwrites_previous(self, repo):
        _create(repo)
        repo.update_title("test-instance", "Old")
        result = repo.update_title("test-instance", "New")
        assert result.instance_metadata.get("title") == "New"

    def test_update_title_returns_none_for_missing(self, repo):
        assert repo.update_title("missing", "Title") is None


class TestDeleteMetadataFunctional:
    """Basic functional checks for ``delete_metadata``."""

    def test_delete_existing_key(self, repo):
        _create(repo)
        repo.set_metadata("test-instance", "foo", "bar")
        repo.set_metadata("test-instance", "keep", "yes")
        result = repo.delete_metadata("test-instance", "foo")
        assert result is not None
        assert "foo" not in result.instance_metadata
        assert result.instance_metadata.get("keep") == "yes"

    def test_delete_missing_key_is_noop(self, repo):
        """Deleting a key that doesn't exist must not error."""
        _create(repo)
        repo.set_metadata("test-instance", "foo", "bar")
        result = repo.delete_metadata("test-instance", "never-set")
        assert result is not None
        assert result.instance_metadata.get("foo") == "bar"

    def test_delete_returns_none_for_missing_instance(self, repo):
        assert repo.delete_metadata("missing", "k") is None


# =============================================================================
# Atomicity under concurrency — the actual Fix C7 regression tests
# =============================================================================


class TestConcurrentMetadataWrites:
    """The RMW race that this fix closes: concurrent writes to different keys.

    Before the fix, two threads would each read the full metadata dict,
    mutate one key in their local copy, and write it back. Whichever thread
    committed last would overwrite the other's change. With atomic
    ``jsonb_set`` / ``json_set`` the UPDATE composes correctly per-key.
    """

    def test_concurrent_writes_to_different_keys_all_persist(self, repo):
        """Spawn N threads each writing a unique key. All N must survive."""
        _create(repo)
        n = 16
        threads = [
            threading.Thread(
                target=repo.set_metadata,
                args=("test-instance", f"key_{i}", f"value_{i}"),
            )
            for i in range(n)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = repo.get("test-instance")
        assert final is not None
        expected = {f"key_{i}": f"value_{i}" for i in range(n)}
        assert final.instance_metadata == expected, (
            f"Lost-update race: got {sorted(final.instance_metadata)}, "
            f"expected all {n} keys"
        )

    def test_concurrent_set_and_delete_interleave_safely(self, repo):
        """Concurrent set on one key + delete on another must compose."""
        _create(repo)
        # Seed both keys.
        repo.set_metadata("test-instance", "delete_me", "x")
        repo.set_metadata("test-instance", "keep_me", "y")

        n_set = 8
        n_del = 8
        barrier = threading.Barrier(n_set + n_del)

        def do_set(i: int) -> None:
            barrier.wait()
            repo.set_metadata("test-instance", f"new_{i}", f"v_{i}")

        def do_delete(i: int) -> None:
            barrier.wait()
            repo.delete_metadata("test-instance", f"delete_{i}")

        # Pre-create delete targets.
        for i in range(n_del):
            repo.set_metadata("test-instance", f"delete_{i}", f"old_{i}")

        threads = []
        threads += [threading.Thread(target=do_set, args=(i,)) for i in range(n_set)]
        threads += [threading.Thread(target=do_delete, args=(i,)) for i in range(n_del)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = repo.get("test-instance")
        # All new_* keys must be present.
        for i in range(n_set):
            assert final.instance_metadata.get(f"new_{i}") == f"v_{i}", (
                f"set_metadata lost update for new_{i}"
            )
        # All delete_* keys must be gone.
        for i in range(n_del):
            assert f"delete_{i}" not in final.instance_metadata, (
                f"delete_metadata failed to remove delete_{i}"
            )
        # The pre-existing keep_me must still be there.
        assert final.instance_metadata.get("keep_me") == "y"

    def test_concurrent_title_and_metadata_do_not_clobber(self, repo):
        """Background title generation + metadata writes must compose.

        This mirrors the original bug scenario: a title-generation background
        task fires ``update_title`` while user-driven code calls
        ``set_metadata`` for unrelated keys.
        """
        _create(repo)
        n = 12
        barrier = threading.Barrier(n + 1)

        def title_writer() -> None:
            barrier.wait()
            repo.update_title("test-instance", "Auto Title")

        def meta_writer(i: int) -> None:
            barrier.wait()
            repo.set_metadata("test-instance", f"k_{i}", i)

        threads = [threading.Thread(target=title_writer)]
        threads += [threading.Thread(target=meta_writer, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = repo.get("test-instance")
        # All keys present.
        for i in range(n):
            assert final.instance_metadata.get(f"k_{i}") == i
        # Title present.
        assert final.instance_metadata.get("title") == "Auto Title"
