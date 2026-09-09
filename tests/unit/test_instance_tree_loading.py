"""Tests for BFS descendant loading in repository.list(include_descendants=True).

Covers:
- BFS walking children of multiple roots (2 roots, each with children)
- Deep tree traversal (root → child → grandchild → great-grandchild)
- exclude_kb=True excludes KB agent descendants
- Pagination: limit=1 returns 1 root + its descendants; offset=1 returns the next root
- Empty result: no root instances → returns ([], 0)
- project_id filter applies to both roots and descendants
- Dedup via seen_ids guards against circular parent_id references
- Depth limit logs a warning when hit
- include_descendants=False preserves original flat pagination behavior
"""

import logging
import pytest
from datetime import datetime, timezone
from sqlmodel import SQLModel, create_engine

from daemon.repositories.instance.repository import (
    MAX_DESCENDANTS_PER_PAGE,
    SQLModelInstanceRepository,
    _MAX_TRAVERSAL_DEPTH,
    _descendant_cap_warn_last_emit,
    _DESCENDANT_CAP_WARN_WINDOW_SECONDS,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def repo():
    """In-memory SQLite repository with real schema."""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return SQLModelInstanceRepository(engine)


def _make_instance(
    repo: SQLModelInstanceRepository,
    instance_id: str,
    parent_id: str | None = None,
    agent_id: str = "developer",
    status: str = "running",
    project_id: str = "proj-1",
) -> None:
    """Helper to create an instance with optional parent in the repository."""
    repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"./agents/{agent_id}",
        parent_id=parent_id,
        status=status,
        project_id=project_id,
    )


# =============================================================================
# BFS tree loading tests
# =============================================================================

class TestListIncludeDescendantsBFS:
    """Tests for BFS descendant loading when include_descendants=True."""

    def test_bfs_walks_children_of_multiple_roots(self, repo):
        """2 roots, each with 2 children → 2 roots + 4 children = 6 instances, total=2."""
        # Root A with 2 children
        _make_instance(repo, "root-a")
        _make_instance(repo, "child-a1", parent_id="root-a")
        _make_instance(repo, "child-a2", parent_id="root-a")

        # Root B with 2 children
        _make_instance(repo, "root-b")
        _make_instance(repo, "child-b1", parent_id="root-b")
        _make_instance(repo, "child-b2", parent_id="root-b")

        instances, total, _ = repo.list(include_descendants=True)

        assert total == 2
        assert len(instances) == 6
        returned_ids = {i.instance_id for i in instances}
        assert returned_ids == {
            "root-a", "root-b",
            "child-a1", "child-a2",
            "child-b1", "child-b2",
        }

    def test_bfs_deep_tree_traversal(self, repo):
        """root → child → grandchild → great-grandchild, all 4 returned, total=1."""
        _make_instance(repo, "root")
        _make_instance(repo, "child", parent_id="root")
        _make_instance(repo, "grandchild", parent_id="child")
        _make_instance(repo, "great-grandchild", parent_id="grandchild")

        instances, total, _ = repo.list(include_descendants=True)

        assert total == 1
        assert len(instances) == 4
        returned_ids = {i.instance_id for i in instances}
        assert returned_ids == {
            "root", "child", "grandchild", "great-grandchild",
        }

    def test_exclude_kb_excludes_kb_descendants(self, repo):
        """exclude_kb=True excludes KB agent descendants from the loaded tree."""
        _make_instance(repo, "root", agent_id="developer")
        _make_instance(repo, "regular-child", parent_id="root", agent_id="developer")
        _make_instance(repo, "kb-child", parent_id="root", agent_id="experiencer")
        # Nested KB child (under regular child) should also be excluded
        _make_instance(repo, "nested-kb", parent_id="regular-child", agent_id="kb-importer")

        instances, total, _ = repo.list(include_descendants=True, exclude_kb=True)

        # Only root counts as a root; KB children are excluded at all levels.
        assert total == 1
        returned_ids = {i.instance_id for i in instances}
        assert returned_ids == {"root", "regular-child"}
        assert "kb-child" not in returned_ids
        assert "nested-kb" not in returned_ids

    def test_pagination_limit_1_returns_root_and_descendants(self, repo):
        """limit=1 returns 1 root + its descendants; offset=1 returns the next root."""
        # Create root-2 FIRST so it's older, then root-1.
        # ORDER BY created_at DESC means the newer root comes first.
        _make_instance(repo, "root-2")
        _make_instance(repo, "child-2a", parent_id="root-2")
        _make_instance(repo, "root-1")
        _make_instance(repo, "child-1a", parent_id="root-1")
        _make_instance(repo, "child-1b", parent_id="root-1")

        # Page 1: limit=1, offset=0 → root-1 (newer) + 2 children
        instances_p1, total_p1, _ = repo.list(
            limit=1, offset=0, include_descendants=True
        )
        assert total_p1 == 2
        assert len(instances_p1) == 3
        assert {i.instance_id for i in instances_p1} == {
            "root-1", "child-1a", "child-1b",
        }

        # Page 2: limit=1, offset=1 → root-2 (older) + 1 child
        instances_p2, total_p2, _ = repo.list(
            limit=1, offset=1, include_descendants=True
        )
        assert total_p2 == 2
        assert len(instances_p2) == 2
        assert {i.instance_id for i in instances_p2} == {
            "root-2", "child-2a",
        }

    def test_empty_database_no_instances(self, repo):
        """Completely empty database → returns ([], 0)."""
        instances, total, _ = repo.list(include_descendants=True)
        assert instances == []
        assert total == 0

    def test_no_root_instances_only_orphans(self, repo):
        """Instances exist but none are roots (all have non-null parent_id) → ([], 0).

        This is the scenario the previous ``test_empty_result_no_roots`` claimed
        to cover but actually didn't (it used a fresh empty DB). To create
        non-root instances without a real parent we point ``parent_id`` at a
        non-existent ID — there's no FK constraint between
        ``InstanceHierarchy.parent_id`` and ``Instance.instance_id``, so the
        rows commit cleanly. The root query (parent_id IS NULL OR empty)
        matches nothing, and BFS never runs.
        """
        # Three orphans, all with parent_id pointing at non-existent parents.
        _make_instance(repo, "orphan-1", parent_id="ghost-parent-a")
        _make_instance(repo, "orphan-2", parent_id="ghost-parent-b")
        _make_instance(repo, "orphan-3", parent_id="ghost-parent-c")

        instances, total, _ = repo.list(include_descendants=True)
        assert instances == []
        assert total == 0

    def test_project_id_filter_applies_to_descendants(self, repo):
        """project_id filter applies to BOTH roots and descendants (defense-in-depth)."""
        _make_instance(repo, "root-p1", project_id="proj-1")
        _make_instance(repo, "child-p1", parent_id="root-p1", project_id="proj-1")
        _make_instance(
            repo, "child-other-project", parent_id="root-p1", project_id="proj-2"
        )

        instances, total, _ = repo.list(
            include_descendants=True, project_id="proj-1"
        )
        assert total == 1
        assert len(instances) == 2
        assert {i.instance_id for i in instances} == {"root-p1", "child-p1"}
        assert all(i.project_id == "proj-1" for i in instances)


class TestListIncludeDescendantsDedup:
    """Tests for BFS dedup via seen_ids when parent_id is circular."""

    def test_dedup_handles_circular_parent_id(self, repo):
        """If a child's parent_id points back at its grandparent, seen_ids dedups it.

        Construct:
            root
              ├── child  (parent_id=root)
              └── grandchild (parent_id=child) — also parent_id=root (corrupt)

        BFS level 1: returns [child, grandchild]
        BFS level 2: query parents IN (child, grandchild) returns... no children,
        but if there were a circular link back to root, the seen_ids set
        prevents it from being re-added.
        """
        _make_instance(repo, "root")
        _make_instance(repo, "child", parent_id="root")
        _make_instance(repo, "grandchild", parent_id="child")

        # Simulate circular ref: grandchild.parent_id = root
        # (re-save via update to set parent_id=root after creation)
        repo.update("grandchild", parent_id="root")

        instances, total, _ = repo.list(include_descendants=True)

        # root + child + grandchild; no duplicates of root.
        assert total == 1
        returned_ids = [i.instance_id for i in instances]
        assert sorted(returned_ids) == ["child", "grandchild", "root"]
        # No duplicates
        assert len(returned_ids) == len(set(returned_ids))


class TestListIncludeDescendantsDepthLimit:
    """Tests for depth-limit warning behavior."""

    def test_depth_limit_warning_logged_when_exceeded(self, repo, caplog):
        """If BFS loop exits due to depth limit, a warning is logged."""
        # Build a chain that exceeds _MAX_TRAVERSAL_DEPTH.
        chain_length = _MAX_TRAVERSAL_DEPTH + 5

        # First instance is the root.
        prev_id = f"node-0"
        _make_instance(repo, prev_id)

        # Remaining chain members reference the previous one.
        for i in range(1, chain_length):
            _make_instance(repo, f"node-{i}", parent_id=f"node-{i - 1}")

        with caplog.at_level(logging.WARNING, logger="daemon.repositories.instance.repository"):
            instances, total, _ = repo.list(include_descendants=True, limit=100)

        # Root was counted, and at least _MAX_TRAVERSAL_DEPTH descendants were
        # traversed. We should have seen the warning.
        assert total == 1
        # Look for the warning text in the log records
        warning_texts = [
            rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING
        ]
        assert any("depth limit" in t for t in warning_texts), (
            f"Expected depth-limit warning, got: {warning_texts}"
        )


class TestExcludeKBTraversesThroughKBParents:
    """C-1 regression: BFS must traverse THROUGH KB agents to find their
    non-KB children. Stripping KB agents mid-traversal would orphan any
    non-KB grandchildren whose KB parent's ID never enters the next level.
    """

    def test_kb_parent_with_non_kb_child_grandchild_kept(self, repo):
        """root (developer) → kb_child (experiencer) → non_kb_grandchild (developer).

        With ``exclude_kb=True``:
        - The KB child should be EXCLUDED from the final list.
        - The non-KB grandchild must STILL be returned (BFS traversed
          through the KB parent to reach it).
        - The total count is 1 (only the root counts).
        """
        _make_instance(repo, "root", agent_id="developer")
        _make_instance(repo, "kb_child", parent_id="root", agent_id="experiencer")
        _make_instance(
            repo, "non_kb_grandchild", parent_id="kb_child", agent_id="developer"
        )

        instances, total, _ = repo.list(include_descendants=True, exclude_kb=True)

        assert total == 1
        returned_ids = {i.instance_id for i in instances}
        # Root present, non-KB grandchild present, KB child stripped.
        assert returned_ids == {"root", "non_kb_grandchild"}
        assert "kb_child" not in returned_ids

    def test_kb_parent_with_non_kb_child_exclude_kb_false_keeps_all(self, repo):
        """Same tree, but with ``exclude_kb=False``: all three are returned."""
        _make_instance(repo, "root", agent_id="developer")
        _make_instance(repo, "kb_child", parent_id="root", agent_id="experiencer")
        _make_instance(
            repo, "non_kb_grandchild", parent_id="kb_child", agent_id="developer"
        )

        instances, total, _ = repo.list(include_descendants=True, exclude_kb=False)

        assert total == 1
        returned_ids = {i.instance_id for i in instances}
        assert returned_ids == {"root", "kb_child", "non_kb_grandchild"}


class TestDescendantCap:
    """Tests for the MAX_DESCENDANTS_PER_PAGE safety cap (C-4)."""

    def test_descendant_cap_truncates_with_warning(self, repo, caplog, monkeypatch):
        """A tree that exceeds the cap is truncated and a warning is logged.

        We monkeypatch the cap down to 3 to keep the test fast and the
        fixture small. The tree is a chain so each BFS batch adds exactly
        one node — the loop fires the cap the moment the count hits the
        limit, so the final list is exactly ``MAX_DESCENDANTS_PER_PAGE``.

            root → child-1 → child-2 → child-3 → child-4

        BFS expansion: root (1) → child-1 (2) → child-2 (3)
        → cap fires, loop breaks. child-3 and child-4 are never loaded.
        """
        monkeypatch.setattr(
            "daemon.repositories.instance.repository.MAX_DESCENDANTS_PER_PAGE", 3
        )

        _make_instance(repo, "root")
        _make_instance(repo, "child-1", parent_id="root")
        _make_instance(repo, "child-2", parent_id="child-1")
        # These should never be loaded because the cap fires first.
        _make_instance(repo, "child-3", parent_id="child-2")
        _make_instance(repo, "child-4", parent_id="child-3")

        with caplog.at_level(
            logging.WARNING, logger="daemon.repositories.instance.repository"
        ):
            instances, total, _ = repo.list(include_descendants=True)

        assert total == 1
        assert len(instances) == 3
        # Root and the first two chain links are present; the rest are dropped.
        returned_ids = {i.instance_id for i in instances}
        assert returned_ids == {"root", "child-1", "child-2"}
        assert "child-3" not in returned_ids
        assert "child-4" not in returned_ids

        # Warning must mention the descendant limit.
        warning_texts = [
            rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING
        ]
        assert any("Descendant limit" in t for t in warning_texts), (
            f"Expected descendant-cap warning, got: {warning_texts}"
        )

    def test_descendant_cap_default_constant_value(self):
        """Sanity check: the documented default is 1000.

        Guards against accidental value changes in a refactor.
        """
        assert MAX_DESCENDANTS_PER_PAGE == 1000


class TestListFlatPaginationUnchanged:
    """include_descendants=False must preserve original flat-pagination behavior."""

    def test_flat_pagination_returns_all_instances(self, repo):
        """With include_descendants=False (default), returns flat paginated list."""
        _make_instance(repo, "root-1")
        _make_instance(repo, "child-1", parent_id="root-1")
        _make_instance(repo, "root-2")
        _make_instance(repo, "child-2", parent_id="root-2")

        # Default include_descendants=False: flat list, total counts all matching.
        instances, total, _ = repo.list()

        assert total == 4
        assert len(instances) == 4
        returned_ids = {i.instance_id for i in instances}
        assert returned_ids == {"root-1", "child-1", "root-2", "child-2"}

    def test_flat_pagination_respects_limit_offset(self, repo):
        """Default flat pagination respects limit/offset on the full set."""
        for i in range(5):
            _make_instance(repo, f"inst-{i}")

        # limit=2, offset=0
        instances, total, _ = repo.list(limit=2, offset=0)
        assert total == 5
        assert len(instances) == 2

        # limit=2, offset=4 → 1 instance
        instances, total, _ = repo.list(limit=2, offset=4)
        assert total == 5
        assert len(instances) == 1


# =============================================================================
# POLL-SPAM FIX TESTS (2026-09-09)
# =============================================================================


class TestIncludeDescendantsParam:
    """The new ``include_descendants`` query param (default True for
    back-compat) routes through the repository.
    """

    def test_default_true_walks_descendants(self, repo):
        """Omitting ``include_descendants`` keeps the historical behavior:
        root-based pagination + BFS descendant loading. Back-compat
        contract for every existing consumer that doesn't pass the kwarg.
        """
        _make_instance(repo, "root")
        _make_instance(repo, "child-1", parent_id="root")
        _make_instance(repo, "child-2", parent_id="root")

        instances, total, truncated = repo.list()  # default include_descendants=False

        # Wait — the REPOSITORY default is False (the route layer overrides
        # to True for back-compat). This test pins the REPO default, which
        # is the layer below the route. The route adds ``True`` for the
        # existing ``GET /api/instances`` callers.
        # Flat pagination: 3 rows total.
        assert total == 3
        assert len(instances) == 3
        assert truncated is False

    def test_explicit_true_walks_descendants(self, repo):
        """``include_descendants=True`` runs root-based pagination +
        BFS descendant loading."""
        _make_instance(repo, "root")
        _make_instance(repo, "child-1", parent_id="root")
        _make_instance(repo, "child-2", parent_id="root")

        instances, total, truncated = repo.list(include_descendants=True)

        # Root-based pagination: total reflects roots only.
        assert total == 1
        assert len(instances) == 3  # root + 2 children
        returned_ids = {i.instance_id for i in instances}
        assert returned_ids == {"root", "child-1", "child-2"}
        # No cap hit (3 < 1000).
        assert truncated is False

    def test_explicit_false_skips_descendant_bfs(self, repo):
        """``include_descendants=False`` skips the descendant BFS entirely.
        Returns a flat paginated list of all matching instances. No
        descendant-cap WARNING — even with many descendants present.
        """
        _make_instance(repo, "root")
        _make_instance(repo, "child-1", parent_id="root")
        _make_instance(repo, "child-2", parent_id="root")
        _make_instance(repo, "grandchild", parent_id="child-1")

        instances, total, truncated = repo.list(include_descendants=False)

        # Flat pagination: 4 rows total.
        assert total == 4
        assert len(instances) == 4
        assert truncated is False
        # Returned rows are exactly what was inserted (no BFS expansion).
        returned_ids = {i.instance_id for i in instances}
        assert returned_ids == {"root", "child-1", "child-2", "grandchild"}


class TestDescendantCapTruncationFlag:
    """The ``truncated`` boolean on the 3-tuple return.

    Surfaces True iff the descendant cap fired during BFS. False on flat
    pagination (no BFS) and on cap-unaffected BFS.
    """

    def test_truncated_false_on_no_cap(self, repo, monkeypatch):
        """A small tree (cap not reached) returns ``truncated=False``."""
        monkeypatch.setattr(
            "daemon.repositories.instance.repository.MAX_DESCENDANTS_PER_PAGE", 3
        )
        _make_instance(repo, "root")
        _make_instance(repo, "child-1", parent_id="root")

        instances, total, truncated = repo.list(include_descendants=True)

        # 2 rows total, cap=3, no truncation.
        assert len(instances) == 2
        assert truncated is False

    def test_truncated_true_on_cap(self, repo, monkeypatch):
        """A tree that exceeds the cap returns ``truncated=True``."""
        monkeypatch.setattr(
            "daemon.repositories.instance.repository.MAX_DESCENDANTS_PER_PAGE", 3
        )
        # Chain shape (each BFS batch adds exactly one row).
        _make_instance(repo, "root")
        _make_instance(repo, "child-1", parent_id="root")
        _make_instance(repo, "child-2", parent_id="child-1")
        _make_instance(repo, "child-3", parent_id="child-2")
        _make_instance(repo, "child-4", parent_id="child-3")

        instances, total, truncated = repo.list(include_descendants=True)

        # Cap fires at 3 rows.
        assert len(instances) == 3
        assert truncated is True

    def test_truncated_false_on_flat_pagination_even_with_many_rows(
        self, repo, monkeypatch
    ):
        """Flat pagination (``include_descendants=False``) NEVER sets
        ``truncated=True`` — there's no BFS, so the cap can't fire.
        """
        monkeypatch.setattr(
            "daemon.repositories.instance.repository.MAX_DESCENDANTS_PER_PAGE", 3
        )
        # Many rows, would trigger cap if BFS ran.
        _make_instance(repo, "root")
        _make_instance(repo, "child-1", parent_id="root")
        _make_instance(repo, "child-2", parent_id="root")
        _make_instance(repo, "child-3", parent_id="root")
        _make_instance(repo, "child-4", parent_id="root")

        instances, total, truncated = repo.list(include_descendants=False)

        # All 5 rows returned flat.
        assert len(instances) == 5
        assert total == 5
        # truncated is False because the flat path never BFSes.
        assert truncated is False


class TestDescendantCapWarningRateLimit:
    """The descendant-cap WARNING is rate-limited to
    ``_DESCENDANT_CAP_WARN_WINDOW_SECONDS`` per offset.

    The 8s badge poll on prod-scale trees fires the cap on every tick
    (~510 WARN/hr without rate-limiting). The fix: first occurrence per
    window stays WARNING; repeats within the window are logged at DEBUG.
    Tests use module-level state reset + injected clock for determinism —
    never sleep the window.
    """

    def test_first_occurrence_is_warning(self, repo, monkeypatch, caplog):
        """First descendant-cap hit in a window emits WARNING."""
        monkeypatch.setattr(
            "daemon.repositories.instance.repository.MAX_DESCENDANTS_PER_PAGE", 3
        )
        # Reset module-level state so this test is deterministic.
        _descendant_cap_warn_last_emit.clear()

        # Chain shape → cap fires.
        _make_instance(repo, "root")
        _make_instance(repo, "child-1", parent_id="root")
        _make_instance(repo, "child-2", parent_id="child-1")
        _make_instance(repo, "child-3", parent_id="child-2")

        with caplog.at_level(
            logging.DEBUG, logger="daemon.repositories.instance.repository"
        ):
            repo.list(include_descendants=True)

        warning_texts = [
            rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING
        ]
        debug_texts = [
            rec.getMessage() for rec in caplog.records if rec.levelno == logging.DEBUG
        ]
        assert any("Descendant limit" in t for t in warning_texts), (
            f"Expected WARNING on first cap hit, got: warning={warning_texts} debug={debug_texts}"
        )

    def test_repeat_within_window_is_debug(self, repo, monkeypatch, caplog):
        """Second cap hit within the window emits DEBUG, not WARNING.

        Uses an injected monotonic clock so the test never sleeps.
        """
        monkeypatch.setattr(
            "daemon.repositories.instance.repository.MAX_DESCENDANTS_PER_PAGE", 3
        )
        # Reset state and inject a monotonic clock.
        _descendant_cap_warn_last_emit.clear()
        fake_now = [1000.0]

        def fake_monotonic():
            return fake_now[0]

        monkeypatch.setattr(
            "daemon.repositories.instance.repository.time.monotonic",
            fake_monotonic,
        )

        # Chain shape → cap fires.
        _make_instance(repo, "root")
        _make_instance(repo, "child-1", parent_id="root")
        _make_instance(repo, "child-2", parent_id="child-1")
        _make_instance(repo, "child-3", parent_id="child-2")

        # First call: t=1000 → WARNING emitted, state[0]=1000
        with caplog.at_level(
            logging.DEBUG, logger="daemon.repositories.instance.repository"
        ):
            repo.list(include_descendants=True)
        warning_count_1 = sum(
            1 for rec in caplog.records if rec.levelno == logging.WARNING
            and "Descendant limit" in rec.getMessage()
        )
        assert warning_count_1 == 1, "First cap hit must emit WARNING"

        # Advance clock within the window (1s later).
        caplog.clear()
        fake_now[0] = 1001.0

        with caplog.at_level(
            logging.DEBUG, logger="daemon.repositories.instance.repository"
        ):
            repo.list(include_descendants=True)
        warning_count_2 = sum(
            1 for rec in caplog.records if rec.levelno == logging.WARNING
            and "Descendant limit" in rec.getMessage()
        )
        debug_count_2 = sum(
            1 for rec in caplog.records if rec.levelno == logging.DEBUG
            and "Descendant limit" in rec.getMessage()
        )
        # Second call within window: DEBUG, not WARNING.
        assert warning_count_2 == 0, "Second cap hit within window must NOT emit WARNING"
        assert debug_count_2 == 1, "Second cap hit within window must emit DEBUG"

    def test_repeat_after_window_is_warning_again(
        self, repo, monkeypatch, caplog
    ):
        """Cap hit after the window has elapsed emits WARNING again.

        Uses injected clock to advance past the window without sleeping.
        """
        monkeypatch.setattr(
            "daemon.repositories.instance.repository.MAX_DESCENDANTS_PER_PAGE", 3
        )
        _descendant_cap_warn_last_emit.clear()
        fake_now = [1000.0]

        def fake_monotonic():
            return fake_now[0]

        monkeypatch.setattr(
            "daemon.repositories.instance.repository.time.monotonic",
            fake_monotonic,
        )

        # Chain shape → cap fires.
        _make_instance(repo, "root")
        _make_instance(repo, "child-1", parent_id="root")
        _make_instance(repo, "child-2", parent_id="child-1")
        _make_instance(repo, "child-3", parent_id="child-2")

        # First call: WARNING.
        with caplog.at_level(
            logging.DEBUG, logger="daemon.repositories.instance.repository"
        ):
            repo.list(include_descendants=True)

        # Advance clock past the window.
        caplog.clear()
        fake_now[0] = 1000.0 + _DESCENDANT_CAP_WARN_WINDOW_SECONDS + 1

        with caplog.at_level(
            logging.DEBUG, logger="daemon.repositories.instance.repository"
        ):
            repo.list(include_descendants=True)

        warning_count = sum(
            1 for rec in caplog.records if rec.levelno == logging.WARNING
            and "Descendant limit" in rec.getMessage()
        )
        assert warning_count == 1, (
            "Cap hit after window elapsed must emit WARNING again"
        )