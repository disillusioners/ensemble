"""Tests for atomic project repository operations.

Regression coverage for Fixes C8 + H11 + H12 + H14 — the
read-modify-write races in the project repository's tag, shortname,
related-directory, relationship, and create/update-by-name operations.

The fixed implementation uses dialect-aware atomic SQL:

* C8 (relationships): ``jsonb_set`` (PostgreSQL) / ``json_set`` +
  ``json_insert`` with ``$[#]`` (SQLite) for array append; ``jsonb_agg``
  / ``json_group_array`` for value-based array element removal.
* H11 (related_directories): same pattern as C8 but on a single JSON
  array rather than a dict-of-arrays.
* H12 (tags / shortnames): dialect-aware ``INSERT ... ON CONFLICT DO
  NOTHING`` and single-statement ``DELETE`` on the junction table.
* H14 (Project.name): ``UNIQUE`` constraint enforced by the database;
  ``IntegrityError`` translated into a clean ``ValueError``.

Run with::

    python -m pytest tests/test_project_repository_atomic.py -x -q
"""

from __future__ import annotations

import os
import tempfile
import threading

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories import SQLModelProjectRepository


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
    return SQLModelProjectRepository(engine)


def _create_project(repo: SQLModelProjectRepository, name: str = "Test Project") -> str:
    """Create a project and return its project_id."""
    p = repo.create(name=name)
    return p.project_id


# =============================================================================
# C8: relationships — atomic JSON array operations
# =============================================================================


class TestAddRelationshipFunctional:
    """Basic functional checks for ``add_relationship``."""

    def test_add_first_relationship_creates_key(self, repo):
        pid = _create_project(repo)
        result = repo.add_relationship(pid, "instances", "instance-1")
        assert result.relationships == {"instances": ["instance-1"]}

    def test_add_second_relationship_appends(self, repo):
        pid = _create_project(repo)
        repo.add_relationship(pid, "instances", "instance-1")
        result = repo.add_relationship(pid, "instances", "instance-2")
        assert sorted(result.relationships["instances"]) == [
            "instance-1",
            "instance-2",
        ]

    def test_add_duplicate_relationship_is_idempotent(self, repo):
        pid = _create_project(repo)
        repo.add_relationship(pid, "instances", "instance-1")
        result = repo.add_relationship(pid, "instances", "instance-1")
        assert result.relationships["instances"] == ["instance-1"]

    def test_add_relationship_different_keys(self, repo):
        pid = _create_project(repo)
        repo.add_relationship(pid, "instances", "instance-1")
        result = repo.add_relationship(pid, "agents", "agent-1")
        assert result.relationships == {
            "instances": ["instance-1"],
            "agents": ["agent-1"],
        }

    def test_add_relationship_returns_none_for_missing_project(self, repo):
        assert repo.add_relationship("missing-id", "instances", "x") is None


class TestRemoveRelationshipFunctional:
    """Basic functional checks for ``remove_relationship``."""

    def test_remove_existing_relationship(self, repo):
        pid = _create_project(repo)
        repo.add_relationship(pid, "instances", "instance-1")
        repo.add_relationship(pid, "instances", "instance-2")
        result = repo.remove_relationship(pid, "instances", "instance-1")
        assert result.relationships["instances"] == ["instance-2"]

    def test_remove_relationship_no_op_for_missing_entity(self, repo):
        pid = _create_project(repo)
        repo.add_relationship(pid, "instances", "instance-1")
        result = repo.remove_relationship(pid, "instances", "never-set")
        assert result.relationships["instances"] == ["instance-1"]

    def test_remove_relationship_no_op_for_missing_key(self, repo):
        pid = _create_project(repo)
        repo.add_relationship(pid, "instances", "instance-1")
        # Removing from a key that doesn't exist must not error and must not
        # create a phantom key.
        result = repo.remove_relationship(pid, "agents", "agent-x")
        assert result.relationships == {"instances": ["instance-1"]}

    def test_remove_only_relationship_yields_empty_array(self, repo):
        pid = _create_project(repo)
        repo.add_relationship(pid, "instances", "instance-1")
        result = repo.remove_relationship(pid, "instances", "instance-1")
        # The key remains with an empty array (consistent with the
        # ``COALESCE(..., '[]')`` filter fallback).
        assert result.relationships.get("instances") == []


# =============================================================================
# H11: related_directories — atomic JSON array operations
# =============================================================================


class TestAddRelatedDirectoryFunctional:
    """Basic functional checks for ``add_related_directory``."""

    def test_add_first_directory(self, repo):
        pid = _create_project(repo)
        result = repo.add_related_directory(pid, "/dir/a")
        assert "/dir/a" in result.related_directories

    def test_add_second_directory_appends(self, repo):
        pid = _create_project(repo)
        repo.add_related_directory(pid, "/dir/a")
        result = repo.add_related_directory(pid, "/dir/b")
        assert "/dir/a" in result.related_directories
        assert "/dir/b" in result.related_directories

    def test_add_duplicate_directory_is_idempotent(self, repo):
        pid = _create_project(repo)
        repo.add_related_directory(pid, "/dir/a")
        result = repo.add_related_directory(pid, "/dir/a")
        assert result.related_directories.count("/dir/a") == 1

    def test_add_returns_none_for_missing_project(self, repo):
        assert repo.add_related_directory("missing-id", "/x") is None


class TestRemoveRelatedDirectoryFunctional:
    """Basic functional checks for ``remove_related_directory``."""

    def test_remove_existing_directory(self, repo):
        pid = _create_project(repo, name="P1")
        p2 = repo.create(name="P2", related_directories=["/dir/a", "/dir/b"])
        result = repo.remove_related_directory(p2.project_id, "/dir/a")
        assert result.related_directories == ["/dir/b"]

    def test_remove_missing_directory_is_noop(self, repo):
        pid = _create_project(repo, name="P1")
        p2 = repo.create(name="P2", related_directories=["/dir/a"])
        result = repo.remove_related_directory(p2.project_id, "/never-set")
        assert result.related_directories == ["/dir/a"]


# =============================================================================
# H12: tags / shortnames — atomic INSERT / DELETE on junction tables
# =============================================================================


class TestAddTagFunctional:
    """Basic functional checks for ``add_tag``."""

    def test_add_first_tag(self, repo):
        pid = _create_project(repo)
        result = repo.add_tag(pid, "python")
        assert "python" in result.tags

    def test_add_duplicate_tag_is_idempotent(self, repo):
        pid = _create_project(repo)
        repo.add_tag(pid, "python")
        result = repo.add_tag(pid, "python")
        assert result.tags.count("python") == 1

    def test_add_tag_returns_none_for_missing_project(self, repo):
        assert repo.add_tag("missing-id", "x") is None


class TestRemoveTagFunctional:
    """Basic functional checks for ``remove_tag``."""

    def test_remove_existing_tag(self, repo):
        pid = _create_project(repo, name="P1")
        p2 = repo.create(name="P2", tags=["python", "web"])
        result = repo.remove_tag(p2.project_id, "python")
        assert "python" not in result.tags
        assert "web" in result.tags

    def test_remove_missing_tag_is_noop(self, repo):
        pid = _create_project(repo, name="P1")
        p2 = repo.create(name="P2", tags=["python"])
        result = repo.remove_tag(p2.project_id, "never-set")
        assert result.tags == ["python"]


class TestShortnameFunctional:
    """Smoke coverage for shortname add/remove — same atomic path as tags."""

    def test_add_shortname_first(self, repo):
        pid = _create_project(repo)
        result = repo.add_shortname(pid, "mp")
        assert "mp" in result.shortnames

    def test_add_shortname_duplicate_is_idempotent(self, repo):
        pid = _create_project(repo)
        repo.add_shortname(pid, "mp")
        result = repo.add_shortname(pid, "mp")
        assert result.shortnames.count("mp") == 1

    def test_remove_shortname(self, repo):
        pid = _create_project(repo, name="P1")
        p2 = repo.create(name="P2", shortnames=["keep", "remove"])
        result = repo.remove_shortname(p2.project_id, "remove")
        assert "remove" not in result.shortnames
        assert "keep" in result.shortnames


# =============================================================================
# H14: Project.name — UNIQUE constraint + IntegrityError translation
# =============================================================================


class TestCreateDuplicateName:
    """The H14 race: concurrent ``create`` with the same name."""

    def test_create_with_same_name_raises_clean_error(self, repo):
        """Sequential duplicate must raise ``ValueError`` (not crash)."""
        repo.create(name="Duplicate")
        with pytest.raises(ValueError, match="already exists"):
            repo.create(name="Duplicate")

    def test_create_duplicate_via_concurrent_threads(self, repo):
        """N threads racing on the same name. Exactly one must win.

        This is the actual H14 regression test. Before the fix, two
        threads would both pass the pre-flight ``SELECT`` check and
        both reach the INSERT, producing duplicate ``projects`` rows
        (or one would crash with an unhandled ``IntegrityError``).
        After the fix, the loser's INSERT raises ``IntegrityError``
        which is translated into a clean ``ValueError``.
        """
        n = 12
        successes: list[str] = []
        errors: list[Exception] = []
        successes_lock = threading.Lock()
        errors_lock = threading.Lock()
        barrier = threading.Barrier(n)

        def worker(i: int) -> None:
            try:
                barrier.wait(timeout=5)
                result = repo.create(name="RaceProject")
                with successes_lock:
                    successes.append(result.project_id)
            except Exception as exc:  # noqa: BLE001
                with errors_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 1, (
            f"Expected exactly 1 winner, got {len(successes)} successes "
            f"and {len(errors)} errors. Errors: {errors}"
        )
        # All losers must surface as ValueError, not IntegrityError.
        assert len(errors) == n - 1
        for err in errors:
            assert isinstance(err, ValueError), (
                f"Expected ValueError, got {type(err).__name__}: {err}"
            )
            assert "already exists" in str(err)

    def test_create_distinct_names_all_succeed(self, repo):
        """Sanity check: distinct names must NOT collide."""
        n = 8
        ids = []
        for i in range(n):
            p = repo.create(name=f"DistinctProject_{i}")
            ids.append(p.project_id)
        assert len(set(ids)) == n


class TestUpdateDuplicateName:
    """The H14 race extends to ``update`` when changing the name."""

    def test_update_with_same_name_raises_clean_error(self, repo):
        repo.create(name="Original")
        p2 = repo.create(name="Other")
        with pytest.raises(ValueError, match="already exists"):
            repo.update(p2.project_id, name="Original")


# =============================================================================
# Atomicity under concurrency — the actual C8/H11/H12 regression tests
# =============================================================================


class TestConcurrentRelationshipAdds:
    """The C8 race: concurrent adds to the same (project, entity_type).

    Before the fix, two threads would each read the full relationships
    dict, mutate one entity_id in their local copy, and write it back.
    Whichever thread committed last would overwrite the other's change.
    With dialect-aware ``jsonb_set`` / ``json_set`` the UPDATE composes
    per-element correctly.
    """

    def test_concurrent_adds_same_type_all_persist(self, repo):
        """Spawn N threads each adding a unique entity_id. All N must survive."""
        pid = _create_project(repo)
        n = 16
        barrier = threading.Barrier(n)

        def worker(i: int) -> None:
            barrier.wait(timeout=5)
            repo.add_relationship(pid, "instances", f"instance-{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = repo.get(pid)
        expected = sorted(f"instance-{i}" for i in range(n))
        actual = sorted(final.relationships["instances"])
        assert actual == expected, (
            f"Lost-update race: got {actual}, expected all {n} ids"
        )

    def test_concurrent_adds_mixed_types_all_persist(self, repo):
        """Concurrent adds to different entity_types must each persist."""
        pid = _create_project(repo)
        n_per_type = 8
        barrier = threading.Barrier(n_per_type * 2)

        def instance_worker(i: int) -> None:
            barrier.wait(timeout=5)
            repo.add_relationship(pid, "instances", f"inst-{i}")

        def agent_worker(i: int) -> None:
            barrier.wait(timeout=5)
            repo.add_relationship(pid, "agents", f"agent-{i}")

        threads = []
        threads += [
            threading.Thread(target=instance_worker, args=(i,))
            for i in range(n_per_type)
        ]
        threads += [
            threading.Thread(target=agent_worker, args=(i,))
            for i in range(n_per_type)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = repo.get(pid)
        expected_instances = sorted(f"inst-{i}" for i in range(n_per_type))
        expected_agents = sorted(f"agent-{i}" for i in range(n_per_type))
        assert sorted(final.relationships["instances"]) == expected_instances
        assert sorted(final.relationships["agents"]) == expected_agents


class TestConcurrentRelatedDirectoryAdds:
    """The H11 race: concurrent ``add_related_directory`` with different dirs."""

    def test_concurrent_adds_all_persist(self, repo):
        pid = _create_project(repo)
        n = 16
        barrier = threading.Barrier(n)

        def worker(i: int) -> None:
            barrier.wait(timeout=5)
            repo.add_related_directory(pid, f"/dir/{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = repo.get(pid)
        expected = sorted(f"/dir/{i}" for i in range(n))
        actual = sorted(final.related_directories)
        assert actual == expected, (
            f"Lost-update race: got {actual}, expected all {n} dirs"
        )


class TestConcurrentTagAdds:
    """The H12 race: concurrent ``add_tag`` with different tags.

    Before the fix, ``add_tag`` loaded the full tags list, mutated it,
    and called ``_sync_tags_bulk`` (DELETE all → INSERT all). Two
    concurrent adds of different tags would each see only their own tag
    after the loser's commit. The fixed implementation uses a single
    ``INSERT ... ON CONFLICT DO NOTHING`` per call.
    """

    def test_concurrent_adds_all_persist(self, repo):
        pid = _create_project(repo)
        n = 16
        barrier = threading.Barrier(n)

        def worker(i: int) -> None:
            barrier.wait(timeout=5)
            repo.add_tag(pid, f"tag-{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = repo.get(pid)
        expected = sorted(f"tag-{i}" for i in range(n))
        actual = sorted(final.tags)
        assert actual == expected, (
            f"Lost-update race: got {actual}, expected all {n} tags"
        )

    def test_concurrent_add_and_remove_compose(self, repo):
        """Concurrent add + remove must compose correctly."""
        pid = _create_project(repo, name="P1")
        # Pre-seed one tag that the remove workers will target.
        repo.add_tag(pid, "doomed")

        n_add = 8
        n_remove = 8
        barrier = threading.Barrier(n_add + n_remove)

        def add_worker(i: int) -> None:
            barrier.wait(timeout=5)
            repo.add_tag(pid, f"new-{i}")

        def remove_worker(i: int) -> None:
            barrier.wait(timeout=5)
            repo.add_tag(pid, f"doomed-{i}")  # add before remove
            repo.remove_tag(pid, f"doomed-{i}")  # remove what we just added

        threads = []
        threads += [
            threading.Thread(target=add_worker, args=(i,)) for i in range(n_add)
        ]
        threads += [
            threading.Thread(target=remove_worker, args=(i,))
            for i in range(n_remove)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = repo.get(pid)
        # All new-{i} tags must be present.
        for i in range(n_add):
            assert f"new-{i}" in final.tags, f"add_tag lost update for new-{i}"
        # The seeded "doomed" tag remains because no worker removes it.
        assert "doomed" in final.tags
        # The doomed-{i} tags are removed by their owners.
        for i in range(n_remove):
            assert f"doomed-{i}" not in final.tags
