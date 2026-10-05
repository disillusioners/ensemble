"""Repo-layer tests for ``SnapshotRepository.list_with_filters`` (snapshot-uiux v1).

10 cases per ``be-plan.md`` §8.2 — pins the new generic-list method's
contract independently of the HTTP surface:

* Pipeline composition (project_id + agent + statuses + age window +
  tag post-pass) — total reflects the post-filter count.
* Sort (default + all 8 allow-listed keys).
* Pagination (offset + limit → rows 3-4 of 5).
* Tag filter on the SQLite scan path (all / any / empty passthrough).
* PG arm drift-pin: the rendered SQL contains ``@>`` (NOT ``LIKE``).
* Compat wrapper: ``list_active_by_project`` still returns the same
  rows it did before — the spawn-hot WARM path is byte-compatible.
* Unknown ``tag_mode`` raises ``ValueError`` (the router's pattern
  check fires first on the HTTP path, but the repo's own belt is
  non-negotiable).

Engine fixture mirrors ``tests/unit/test_snapshot_repository.py``:
in-memory SQLite, StaticPool, ``check_same_thread=False``. No
``PRAGMA foreign_keys=ON`` (matches the existing repo test). The
PG arm drift-pin is compile-only — no live PG connection is needed.
"""

from __future__ import annotations

from typing import Iterator, Sequence

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.snapshot.models import (
    SNAPSHOT_STATUS_ACTIVE,
    SNAPSHOT_STATUS_FAILED,
    SNAPSHOT_STATUS_RUNNING,
    SNAPSHOT_STATUS_SUPERSEDED,
    Snapshot,
)
from daemon.repositories.snapshot.repository import SnapshotRepository


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory SQLite engine with the snapshot tables created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> SnapshotRepository:
    return SnapshotRepository(engine)


def _snapshot(
    *,
    project_id: str = "p1",
    target: str = "inst-1",
    title: str = "snap-1",
    status: str = SNAPSHOT_STATUS_ACTIVE,
    agent: str = "coder",
    tags: list[str] | None = None,
    created_at: str | None = None,
) -> Snapshot:
    """Build a minimal valid Snapshot row for tests."""
    row = Snapshot(
        project_id=project_id,
        created_by_agent_id=agent,
        target_instance_id=target,
        title=title,
        task_summary="",
        domain_tags=tags or [],
        status=status,
        repo_path="/repo",
        vcs_type="git",
        git_sha="abc1234",
        git_branch="latest",
        git_dirty=False,
        runtime_version="0.14.2",
        effective_model="cheap-model",
        digest={},
    )
    if created_at is not None:
        row.created_at = created_at
    return row


# ============================================================================
# Tests
# ============================================================================


class TestListWithFilters:
    """Repo-layer pins for ``list_with_filters`` (10 cases, §8.2)."""

    # 1 ────────────────────────────────────────────────────────────────────

    def test_filters_compose(self, repo: SnapshotRepository):
        """project_id + agent + statuses + age window all apply; total reflects post-filter count."""
        # Three projects × two agents × multiple statuses × multiple ages.
        for i, ts in enumerate([
            "2026-10-01T00:00:00+00:00",
            "2026-10-05T00:00:00+00:00",
            "2026-10-10T00:00:00+00:00",
        ]):
            repo.create_with_embeddings(
                _snapshot(
                    project_id="p1", agent="coder", title=f"p1c-{i}",
                    created_at=ts,
                )
            )
            repo.create_with_embeddings(
                _snapshot(
                    project_id="p1", agent="tester", title=f"p1t-{i}",
                    created_at=ts,
                )
            )
            repo.create_with_embeddings(
                _snapshot(
                    project_id="p2", agent="coder", title=f"p2c-{i}",
                    created_at=ts,
                )
            )
        # Project p1 + agent=coder + status=active + age 2026-10-04..06 ⇒
        # the row at 2026-10-05 only.
        items, total = repo.list_with_filters(
            project_id="p1",
            agent_id="coder",
            statuses=(SNAPSHOT_STATUS_ACTIVE,),
            created_after="2026-10-04T00:00:00+00:00",
            created_before="2026-10-06T00:00:00+00:00",
        )
        assert total == 1
        assert [s.title for s in items] == ["p1c-1"]

    # 2 ────────────────────────────────────────────────────────────────────

    def test_default_sort_created_at_desc(self, repo: SnapshotRepository):
        """No ``sort`` arg → newest first."""
        repo.create_with_embeddings(
            _snapshot(title="a", created_at="2026-10-05T01:00:00+00:00")
        )
        repo.create_with_embeddings(
            _snapshot(title="b", created_at="2026-10-05T03:00:00+00:00")
        )
        repo.create_with_embeddings(
            _snapshot(title="c", created_at="2026-10-05T02:00:00+00:00")
        )
        items, _ = repo.list_with_filters()
        assert [s.title for s in items] == ["b", "c", "a"]

    # 3 ────────────────────────────────────────────────────────────────────

    def test_each_sort_key(self, repo: SnapshotRepository):
        """All 8 sort keys produce the expected ordering."""
        repo.create_with_embeddings(_snapshot(title="alpha", created_at="2026-10-05T01:00:00+00:00"))
        repo.create_with_embeddings(
            _snapshot(
                title="zebra", status=SNAPSHOT_STATUS_FAILED,
                created_at="2026-10-05T02:00:00+00:00",
            )
        )
        repo.create_with_embeddings(
            _snapshot(title="middle", created_at="2026-10-05T03:00:00+00:00")
        )

        # created_at_desc
        items, _ = repo.list_with_filters(sort="created_at_desc")
        assert [s.title for s in items] == ["middle", "zebra", "alpha"]
        # created_at_asc
        items, _ = repo.list_with_filters(sort="created_at_asc")
        assert [s.title for s in items] == ["alpha", "zebra", "middle"]
        # title_asc
        items, _ = repo.list_with_filters(sort="title_asc")
        assert [s.title for s in items] == ["alpha", "middle", "zebra"]
        # title_desc
        items, _ = repo.list_with_filters(sort="title_desc")
        assert [s.title for s in items] == ["zebra", "middle", "alpha"]
        # status_asc — alphabetical: 'active' < 'failed' < 'running' < 'superseded'.
        items, _ = repo.list_with_filters(sort="status_asc")
        assert items[0].status == SNAPSHOT_STATUS_ACTIVE
        # status_desc — reverse alphabetical.
        items, _ = repo.list_with_filters(sort="status_desc")
        assert items[0].status == SNAPSHOT_STATUS_FAILED
        # project_id_asc / project_id_desc — single project so both order
        # the same; pin that the call works and returns all 3 rows.
        items, _ = repo.list_with_filters(sort="project_id_asc")
        assert len(items) == 3
        items, _ = repo.list_with_filters(sort="project_id_desc")
        assert len(items) == 3

    # 4 ────────────────────────────────────────────────────────────────────

    def test_pagination_offset(self, repo: SnapshotRepository):
        """``limit=2, offset=2`` returns rows 3-4 of 5."""
        for i in range(5):
            repo.create_with_embeddings(
                _snapshot(
                    title=f"snap-{i}",
                    created_at=f"2026-10-05T0{i}:00:00+00:00",
                )
            )
        # Default sort is created_at_desc → ordered list is
        # [snap-4, snap-3, snap-2, snap-1, snap-0]. Slicing [2:4]
        # yields [snap-2, snap-1].
        items, total = repo.list_with_filters(limit=2, offset=2)
        # total = 5 (post-filter), items = rows 3-4 of the ordered list.
        assert total == 5
        assert [s.title for s in items] == ["snap-2", "snap-1"]

    # 5 ────────────────────────────────────────────────────────────────────

    def test_tags_all_mode_sqlite(self, repo: SnapshotRepository):
        """SQLite path: ``tag_mode='all'`` intersects correctly."""
        r1 = repo.create_with_embeddings(
            _snapshot(
                title="both",
                tags=["kind:implementation", "subsystem:upgrade-pipeline"],
            )
        )
        repo.create_with_embeddings(
            _snapshot(title="only-kind", tags=["kind:implementation"])
        )
        repo.create_with_embeddings(
            _snapshot(title="only-subsystem", tags=["subsystem:upgrade-pipeline"])
        )
        items, total = repo.list_with_filters(
            tags=["kind:implementation", "subsystem:upgrade-pipeline"],
            tag_mode="all",
        )
        assert total == 1
        assert [s.title for s in items] == ["both"]

    # 6 ────────────────────────────────────────────────────────────────────

    def test_tags_any_mode_sqlite(self, repo: SnapshotRepository):
        """SQLite path: ``tag_mode='any'`` unions correctly."""
        repo.create_with_embeddings(
            _snapshot(title="a-row", tags=["a", "x"])
        )
        repo.create_with_embeddings(
            _snapshot(title="b-row", tags=["b", "y"])
        )
        repo.create_with_embeddings(
            _snapshot(title="c-row", tags=["c"])
        )
        items, total = repo.list_with_filters(
            tags=["a", "b"],
            tag_mode="any",
        )
        assert total == 2
        assert {s.title for s in items} == {"a-row", "b-row"}

    # 7 ────────────────────────────────────────────────────────────────────

    def test_tags_empty_passthrough(self, repo: SnapshotRepository):
        """``tags=[]`` → all candidates (no filter)."""
        repo.create_with_embeddings(_snapshot(title="a"))
        repo.create_with_embeddings(
            _snapshot(title="b", tags=["unrelated"])
        )
        items, total = repo.list_with_filters(tags=[], tag_mode="all")
        assert total == 2
        assert {s.title for s in items} == {"a", "b"}

    # 8 ────────────────────────────────────────────────────────────────────

    def test_pg_drift_pin_for_filter_with_tags(self):
        """PG arm SHAPE — JSONB ``@>`` containment (no live PG required).

        The drift-pin compiles the new ``list_with_filters`` query path
        against the postgresql dialect and asserts the rendered SQL
        contains ``@>`` (NOT ``LIKE``). If the production cast to JSONB
        is ever dropped, the rendered SQL contains ``LIKE`` and this
        test fails loudly.

        Mirrors the existing
        ``TestTagFilterPGDriftPin`` in
        ``tests/unit/test_snapshot_repository.py`` (the
        ``_build_filter_by_tags_pg_stmt`` path); this new pin extends
        the same guardrail to the new method.
        """
        from sqlalchemy.dialects import postgresql

        # Build a candidate query the way ``list_with_filters`` would.
        # We render the underlying ``filter_by_tags`` PG query (the
        # post-pass), since the SQL where + order clauses are
        # dialect-agnostic and the only PG-specific surface is the
        # JSONB containment.
        stmt = SnapshotRepository._build_filter_by_tags_pg_stmt(
            ids=["snap-001", "snap-002"],
            wanted=["kind:implementation", "subsystem:upgrade-pipeline"],
            tag_mode="all",
        )
        rendered = str(stmt.compile(dialect=postgresql.dialect()))
        assert "@>" in rendered
        assert "LIKE" not in rendered.upper()
        assert "||" not in rendered

    # 9 ────────────────────────────────────────────────────────────────────

    def test_compat_wrapper_list_active_by_project(
        self, repo: SnapshotRepository
    ):
        """``list_active_by_project`` returns the same rows it did before.

        The spawn-hot WARM path (``snapshot_search_service.py:328``)
        and the duck-typed test fakes
        (``tests/unit/test_snapshot_search_service.py:141``) both
        call this signature. The 3-line compat wrapper must remain
        byte-compatible: only ``active`` rows for the project,
        newest first.
        """
        a1 = repo.create_with_embeddings(_snapshot(title="a1"))
        a2 = repo.create_with_embeddings(_snapshot(title="a2"))
        repo.create_with_embeddings(
            _snapshot(title="superseded", status=SNAPSHOT_STATUS_SUPERSEDED)
        )
        repo.create_with_embeddings(
            _snapshot(title="other-project", project_id="p2")
        )
        rows = repo.list_active_by_project("p1")
        assert all(r.project_id == "p1" for r in rows)
        assert all(r.status == SNAPSHOT_STATUS_ACTIVE for r in rows)
        assert {r.id for r in rows} == {a1.id, a2.id}
        # Newest first.
        assert rows[0].id == a2.id

    # 10 ───────────────────────────────────────────────────────────────────

    def test_value_error_on_bad_tag_mode(self, repo: SnapshotRepository):
        """``tag_mode='xor'`` → ``ValueError``.

        The router's pattern check (``Query(pattern=...)``) fires
        FIRST on the HTTP path (422), but the repo's own belt is
        non-negotiable for direct callers.
        """
        repo.create_with_embeddings(_snapshot(title="a"))
        with pytest.raises(ValueError, match="tag_mode"):
            repo.list_with_filters(tag_mode="xor")
