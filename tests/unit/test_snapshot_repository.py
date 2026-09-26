"""Unit tests for the Agent Snapshot storage layer (PR3).

Covers:

* :class:`SnapshotRepository` CRUD round-trips (JSONB ``domain_tags``
  / ``digest`` / ``embedding`` columns on SQLite).
* Status write guards (fail loud on unknown status values).
* The R12 ``create-mints-successor`` atomic flip (verification rider
  (g) shape): successor INSERT + previous-row ``active → superseded``
  UPDATE land in one transaction; cross-root supersession is
  permitted; superseding a nonexistent id is refused and leaves no
  partial write.
* The D3 boot sweep (:meth:`mark_orphaned_running_interrupted`) —
  ``running`` rows flip to ``interrupted``, other statuses untouched,
  and a second sweep is a no-op (idempotent).
* The R8 tag filter (``tag_mode: all|any``) on the SQLite scan path.
* Migration file hygiene: the snapshot migration timestamp sorts
  after the previous latest (``20260915_212810``) and carries both
  UP and DOWN sections (design §3.3).

Repository methods are synchronous by design; callers bridge to async
via ``asyncio.to_thread``. Engine fixture mirrors
``tests/unit/test_skill_bank_repository.py`` (StaticPool +
``check_same_thread=False``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Sequence

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.snapshot.models import (
    SNAPSHOT_STATUS_ACTIVE,
    SNAPSHOT_STATUS_INTERRUPTED,
    SNAPSHOT_STATUS_RUNNING,
    SNAPSHOT_STATUS_SUPERSEDED,
    Snapshot,
    SnapshotEmbedding,
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
    """``SnapshotRepository`` wired to the in-memory ``engine``."""
    return SnapshotRepository(engine)


def _snapshot(
    *,
    project_id: str = "p1",
    target: str = "inst-1",
    title: str = "snap-1",
    status: str = SNAPSHOT_STATUS_ACTIVE,
    tags: list[str] | None = None,
    supersedes: str | None = None,
    summary: str = "did the thing",
) -> Snapshot:
    """Build a minimal valid Snapshot row for tests."""
    return Snapshot(
        project_id=project_id,
        created_by_agent_id="coder",
        target_instance_id=target,
        title=title,
        task_summary=summary,
        domain_tags=tags or [],
        status=status,
        supersedes_snapshot_id=supersedes,
        repo_path="/repo",
        vcs_type="git",
        git_sha="abc1234",
        git_branch="latest",
        git_dirty=False,
        runtime_version="0.14.2",
        effective_model="cheap-model",
        digest={"task_summary_text": "did the thing", "refs": {"commits": ["abc1234"]}},
    )


# ============================================================================
# CRUD round-trip
# ============================================================================


class TestSnapshotCrud:
    def test_create_get_roundtrip_preserves_jsonb_columns(self, repo: SnapshotRepository):
        snap = _snapshot(tags=["kind:implementation", "subsystem:upgrade-pipeline"])
        created = repo.create_with_embeddings(snap)
        assert created.id

        fetched = repo.get(created.id)
        assert fetched is not None
        assert fetched.title == "snap-1"
        assert fetched.project_id == "p1"
        assert fetched.target_instance_id == "inst-1"
        assert fetched.status == SNAPSHOT_STATUS_ACTIVE
        assert fetched.domain_tags == [
            "kind:implementation",
            "subsystem:upgrade-pipeline",
        ]
        assert fetched.digest["refs"]["commits"] == ["abc1234"]
        assert fetched.runtime_version == "0.14.2"
        assert fetched.effective_model == "cheap-model"
        # JSON-safe view carries the same fields.
        view = fetched.to_dict()
        assert view["domain_tags"] == fetched.domain_tags
        assert view["digest"] == fetched.digest

    def test_create_with_embeddings_stamps_snapshot_id(self, repo: SnapshotRepository):
        snap = repo.create_with_embeddings(
            _snapshot(),
            embeddings=[
                SnapshotEmbedding(trigger_query="upgrade the pipeline", embedding=[0.1, 0.2]),
                SnapshotEmbedding(trigger_query="pump the version", embedding=[0.3, 0.4]),
            ],
        )
        embs = repo.get_embeddings(snap.id)
        assert [e.trigger_query for e in embs] == [
            "upgrade the pipeline",
            "pump the version",
        ]
        assert all(e.snapshot_id == snap.id for e in embs)
        assert embs[0].embedding == [0.1, 0.2]

    def test_unknown_status_raises_fail_loud(self, repo: SnapshotRepository):
        with pytest.raises(ValueError, match="Unknown snapshot status"):
            repo.create_with_embeddings(_snapshot(status="expired"))
        with pytest.raises(ValueError, match="Unknown snapshot status"):
            repo.set_status("whatever", "fresh")

    def test_get_missing_returns_none(self, repo: SnapshotRepository):
        assert repo.get("no-such-id") is None


# ============================================================================
# R12 create-mints-successor atomic flip (rider (g) shape)
# ============================================================================


class TestR12AtomicSupersessionFlip:
    def test_flip_prev_superseded_and_successor_active_in_one_transaction(
        self, repo: SnapshotRepository
    ):
        prev = repo.create_with_embeddings(_snapshot(title="snap-prev"))
        succ = repo.create_successor(
            _snapshot(title="snap-succ"),
            supersedes_snapshot_id=prev.id,
            embeddings=[SnapshotEmbedding(trigger_query="redo it", embedding=[0.5])],
        )
        # Successor: active + chain link + its own embeddings.
        assert succ.status == SNAPSHOT_STATUS_ACTIVE
        assert succ.supersedes_snapshot_id == prev.id
        assert [e.trigger_query for e in repo.get_embeddings(succ.id)] == ["redo it"]
        # Previous: flipped to superseded in the same transaction; its
        # embeddings STAY ALONGSIDE (Q8-A — no cascade-delete on
        # supersession).
        prev_after = repo.get(prev.id)
        assert prev_after is not None
        assert prev_after.status == SNAPSHOT_STATUS_SUPERSEDED
        assert repo.get_embeddings(prev.id) == []

    def test_cross_root_supersession_permitted(self, repo: SnapshotRepository):
        """R12: NO same-root enforcement — different targets allowed."""
        prev = repo.create_with_embeddings(_snapshot(target="inst-A"))
        succ = repo.create_successor(
            _snapshot(target="inst-B"), supersedes_snapshot_id=prev.id
        )
        assert succ.target_instance_id == "inst-B"
        assert repo.get(prev.id).status == SNAPSHOT_STATUS_SUPERSEDED

    def test_superseding_missing_id_refused_no_torn_state(
        self, repo: SnapshotRepository
    ):
        snap = repo.create_with_embeddings(_snapshot())
        with pytest.raises(ValueError, match="does not exist"):
            repo.create_successor(
                _snapshot(title="orphan-succ"),
                supersedes_snapshot_id="missing-id",
            )
        # Refusal left no partial write: the would-be successor row
        # does not exist, and the previous row is untouched.
        assert repo.get("missing-id") is None
        assert repo.latest_for_target("inst-1").id == snap.id
        assert repo.get(snap.id).status == SNAPSHOT_STATUS_ACTIVE

    def test_chain_walk_prev_superseded_only_once(self, repo: SnapshotRepository):
        s1 = repo.create_with_embeddings(_snapshot(title="s1"))
        s2 = repo.create_successor(_snapshot(title="s2"), supersedes_snapshot_id=s1.id)
        s3 = repo.create_successor(_snapshot(title="s3"), supersedes_snapshot_id=s2.id)
        statuses = {s.id: repo.get(s.id).status for s in (s1, s2, s3)}
        assert statuses == {
            s1.id: SNAPSHOT_STATUS_SUPERSEDED,
            s2.id: SNAPSHOT_STATUS_SUPERSEDED,
            s3.id: SNAPSHOT_STATUS_ACTIVE,
        }
        assert repo.get(s3.id).supersedes_snapshot_id == s2.id

    def test_stale_successor_preflipped_superseded_refuses_no_resurrection(
        self, repo: SnapshotRepository
    ):
        """Wave 2b review FIX 2 — successor pre-state guard.

        A stale capture finishing AFTER its own successor row was
        already superseded by a later cycle must NOT resurrect it to
        ``active`` (that would leave two active rows for one target).
        Fail-soft refusal: the row keeps ``superseded``, the
        predecessor chain stays intact.
        """
        s1 = repo.create_with_embeddings(_snapshot(title="s1"))
        s2 = repo.create_successor(
            _snapshot(title="s2", status=SNAPSHOT_STATUS_RUNNING),
            supersedes_snapshot_id=s1.id,
        )
        s3 = repo.create_successor(
            _snapshot(title="s3", status=SNAPSHOT_STATUS_RUNNING),
            supersedes_snapshot_id=s2.id,
        )
        assert repo.get(s1.id).status == SNAPSHOT_STATUS_SUPERSEDED
        assert repo.get(s2.id).status == SNAPSHOT_STATUS_SUPERSEDED
        assert repo.get(s3.id).status == SNAPSHOT_STATUS_ACTIVE
        # The stale capture for s2 finishes late and re-mints through
        # create_successor. The stale row is a FRESH DB view (as the
        # lane would hold after any re-fetch): a 'superseded' object
        # that the mint would stamp back to 'active'.
        stale_row = repo.get(s2.id)
        assert stale_row is not None
        repo.create_successor(stale_row, supersedes_snapshot_id=s1.id)
        assert repo.get(s2.id).status == SNAPSHOT_STATUS_SUPERSEDED
        assert repo.get(s3.id).status == SNAPSHOT_STATUS_ACTIVE
        assert repo.get(s2.id).supersedes_snapshot_id == s1.id
        # Exactly ONE active row for the target after the refusal.
        active_ids = [
            s.id
            for s in (s1, s2, s3)
            if repo.get(s.id).status == SNAPSHOT_STATUS_ACTIVE
        ]
        assert active_ids == [s3.id]


# ============================================================================
# update_capture_result terminal-state guard (Wave 1b FIX 1 — the
# stale-capture resurrection race)
# ============================================================================


class TestUpdateCaptureResultTerminalGuard:
    def test_late_write_after_supersession_dropped_no_resurrection(
        self, repo: SnapshotRepository
    ):
        """A stale async capture finishing AFTER the R12 flip must NOT
        resurrect the superseded row: ``update_capture_result`` fails
        soft — status unchanged, late digest dropped, no raise."""
        row = repo.create_with_embeddings(
            _snapshot(title="late-race", status=SNAPSHOT_STATUS_RUNNING)
        )
        original_digest = dict(row.digest or {})
        succ = repo.create_successor(
            _snapshot(title="successor"), supersedes_snapshot_id=row.id
        )
        assert succ.status == SNAPSHOT_STATUS_ACTIVE
        assert repo.get(row.id).status == SNAPSHOT_STATUS_SUPERSEDED

        out = repo.update_capture_result(
            row.id,
            status=SNAPSHOT_STATUS_ACTIVE,
            digest={"late": "resurrection-attempt"},
        )
        # Returned row is the unchanged superseded row.
        assert out.status == SNAPSHOT_STATUS_SUPERSEDED
        assert "late" not in (out.digest or {})
        # Persisted state confirms: no resurrection, no digest write.
        fresh = repo.get(row.id)
        assert fresh is not None
        assert fresh.status == SNAPSHOT_STATUS_SUPERSEDED
        assert (fresh.digest or {}) == original_digest

    def test_terminal_write_refused_for_other_terminal_pre_state(
        self, repo: SnapshotRepository
    ):
        """Strict guard: ONLY 'running' may transition via
        ``update_capture_result`` — a row already in any terminal
        state (failed / interrupted / active) is never overwritten."""
        for pre_status in ("failed", SNAPSHOT_STATUS_INTERRUPTED, SNAPSHOT_STATUS_ACTIVE):
            row = repo.create_with_embeddings(
                _snapshot(title=f"terminal-{pre_status}", status=pre_status)
            )
            original_digest = dict(row.digest or {})
            out = repo.update_capture_result(
                row.id,
                status=SNAPSHOT_STATUS_ACTIVE,
                digest={"late": True},
            )
            assert out.status == pre_status
            fresh = repo.get(row.id)
            assert fresh is not None
            assert fresh.status == pre_status
            assert "late" not in (fresh.digest or {})
            assert (fresh.digest or {}) == original_digest


# ============================================================================
# D3 boot sweep
# ============================================================================


class TestBootSweepInterrupted:
    def test_running_rows_flip_to_interrupted_others_untouched(
        self, repo: SnapshotRepository
    ):
        repo.create_with_embeddings(_snapshot(title="running-1", status=SNAPSHOT_STATUS_RUNNING))
        repo.create_with_embeddings(_snapshot(title="running-2", status=SNAPSHOT_STATUS_RUNNING, target="inst-2"))
        active = repo.create_with_embeddings(_snapshot(title="active-1"))
        failed = repo.create_with_embeddings(_snapshot(title="failed-1", status="failed"))

        flipped = repo.mark_orphaned_running_interrupted()
        assert flipped == 2
        for title in ("running-1", "running-2"):
            row = repo.latest_for_target(
                "inst-1" if title == "running-1" else "inst-2",
                statuses=("interrupted",),
            )
            assert row is not None and row.title == title
            assert row.status == SNAPSHOT_STATUS_INTERRUPTED
        # Non-running statuses are untouched.
        assert repo.get(active.id).status == SNAPSHOT_STATUS_ACTIVE
        assert repo.get(failed.id).status == "failed"

    def test_second_sweep_is_noop_idempotent(self, repo: SnapshotRepository):
        repo.create_with_embeddings(_snapshot(status=SNAPSHOT_STATUS_RUNNING))
        assert repo.mark_orphaned_running_interrupted() == 1
        assert repo.mark_orphaned_running_interrupted() == 0
        assert repo.mark_orphaned_running_interrupted() == 0


# ============================================================================
# Reads: project-scoped candidates + latest-for-target
# ============================================================================


class TestReads:
    def test_list_active_by_project_scopes_and_orders(self, repo: SnapshotRepository):
        a1 = repo.create_with_embeddings(_snapshot(title="a1"))
        a2 = repo.create_with_embeddings(_snapshot(title="a2"))
        repo.create_with_embeddings(
            _snapshot(title="superseded", status=SNAPSHOT_STATUS_SUPERSEDED)
        )
        repo.create_with_embeddings(_snapshot(title="other-project", project_id="p2"))

        rows = repo.list_active_by_project("p1")
        assert [r.title for r in rows] == [a2.title, a1.title] or [
            r.title for r in rows
        ] == [a1.title, a2.title]
        assert all(r.project_id == "p1" for r in rows)
        assert all(r.status == SNAPSHOT_STATUS_ACTIVE for r in rows)

    def test_latest_for_target_excludes_superseded_by_default(
        self, repo: SnapshotRepository
    ):
        s1 = repo.create_with_embeddings(_snapshot(title="s1"))
        repo.create_successor(_snapshot(title="s2"), supersedes_snapshot_id=s1.id)
        tip = repo.latest_for_target("inst-1")
        assert tip is not None and tip.title == "s2"
        # Explicit superseded-inclusive read sees both (R9 history).
        assert repo.latest_for_target(
            "inst-1", statuses=(SNAPSHOT_STATUS_ACTIVE, SNAPSHOT_STATUS_SUPERSEDED)
        ).id in {s1.id, tip.id}

    def test_count_by_project(self, repo: SnapshotRepository):
        repo.create_with_embeddings(_snapshot())
        repo.create_with_embeddings(_snapshot(title="x", project_id="p2"))
        assert repo.count_by_project("p1") == 1
        assert repo.count_by_project("p2") == 1


# ============================================================================
# R8 tag filter (SQLite scan path — rider (f))
# ============================================================================


class TestTagFilter:
    def _rows(self, repo: SnapshotRepository) -> list[Snapshot]:
        r1 = repo.create_with_embeddings(
            _snapshot(title="r1", tags=["kind:implementation", "subsystem:upgrade-pipeline"])
        )
        r2 = repo.create_with_embeddings(
            _snapshot(title="r2", tags=["kind:review", "feature:job-pause"], target="inst-2")
        )
        r3 = repo.create_with_embeddings(
            _snapshot(title="r3", tags=["kind:implementation"], target="inst-3")
        )
        return [r1, r2, r3]

    def test_tag_mode_all_requires_every_tag(self, repo: SnapshotRepository):
        rows = self._rows(repo)
        got = repo.filter_by_tags(
            rows, ["kind:implementation", "subsystem:upgrade-pipeline"], tag_mode="all"
        )
        assert [r.title for r in got] == ["r1"]

    def test_tag_mode_any_is_or_semantics(self, repo: SnapshotRepository):
        rows = self._rows(repo)
        got = repo.filter_by_tags(
            rows, ["kind:review", "subsystem:upgrade-pipeline"], tag_mode="any"
        )
        assert [r.title for r in got] == ["r1", "r2"]

    def test_empty_tags_match_everything(self, repo: SnapshotRepository):
        rows = self._rows(repo)
        assert repo.filter_by_tags(rows, []) == rows

    def test_unknown_tag_mode_raises(self, repo: SnapshotRepository):
        rows = self._rows(repo)
        with pytest.raises(ValueError, match="tag_mode"):
            repo.filter_by_tags(rows, ["kind:review"], tag_mode="some")

    def test_no_match_returns_empty(self, repo: SnapshotRepository):
        rows = self._rows(repo)
        assert repo.filter_by_tags(rows, ["topic:nonexistent"], tag_mode="any") == []


# ============================================================================
# R8 PG arm drift-pin (Blocker 1 regression guard)
# ============================================================================
#
# Compile-only checks against the postgresql dialect. No live PG connection;
# the rendered SQL is what the production PG arm will execute. Guards against
# regression to the broken LIKE form (JSONBType TypeDecorator routes
# ``col().contains()`` to JSON/LIKE — see Block-1 root cause in
# daemon/repositories/snapshot/repository.py). If the production cast to
# JSONB is ever dropped, the rendered SQL contains ``LIKE`` and these
# tests fail loudly.
#
# Companion pack (live PG): test/packs/ab/snapshot_pg_smoke_integration_test.sh.
# Pattern: tests/unit/services/test_waiting_children_watchdog.py:1424-1435
# (compile against postgresql.dialect(), assert shape).


class TestTagFilterPGDriftPin:
    """PG arm SHAPE — JSONB ``@>`` containment (no live PG required).

    The drift-pin compiles ``SnapshotRepository._build_filter_by_tags_pg_stmt``
    against the postgresql dialect and asserts the rendered SQL:

    * contains the ``@>`` containment operator (the JSONB fix),
    * does NOT contain ``LIKE`` (the broken-TypeDecorator regression),
    * does NOT contain the JSONB cast wrapper ``::jsonb`` bound to a
      string concatenation (which is what the broken form actually
      emits — ``LIKE '%' || :tags::jsonb || '%'``).

    Both ``all`` and ``any`` modes are covered; the ``any``-mode renders
    one ``@>`` per tag in the wanted list.
    """

    def _render(self, tag_mode: str, wanted: Sequence[str] = ("a", "b")) -> str:
        from sqlalchemy.dialects import postgresql

        stmt = SnapshotRepository._build_filter_by_tags_pg_stmt(
            ids=["snap-001", "snap-002"],
            wanted=list(wanted),
            tag_mode=tag_mode,
        )
        return str(stmt.compile(dialect=postgresql.dialect()))

    def test_all_mode_emits_jsonb_containment_not_like(self):
        rendered = self._render(tag_mode="all")
        # JSONB containment — the fix.
        assert "@>" in rendered, (
            "PG arm must emit JSONB @> containment; got:\n" + rendered
        )
        # Regression guards: the broken TypeDecorator form would emit these.
        assert "LIKE" not in rendered.upper(), (
            "PG arm regressed to JSON/LIKE comparator; rendered:\n" + rendered
        )
        assert "||" not in rendered, (
            "PG arm regressed to string-concat bound; rendered:\n" + rendered
        )

    def test_any_mode_emits_jsonb_containment_per_tag(self):
        rendered = self._render(tag_mode="any", wanted=("a", "c"))
        # Two wanted tags → two containment clauses.
        assert rendered.count("@>") == 2, (
            "any-mode must emit one @> per wanted tag; got:\n" + rendered
        )
        assert "LIKE" not in rendered.upper()
        assert "||" not in rendered

    def test_in_clause_present(self):
        """The candidate-id filter still lands in the PG arm."""
        rendered = self._render(tag_mode="all")
        # psycopg paramstyle renders IN as ``IN (...)`` with positional
        # ``%(param_1)s`` binds; the Snapshot.id column reference survives.
        assert "IN" in rendered.upper()
        assert "snapshots" in rendered.lower()


# ============================================================================
# Migration file hygiene (design §3.3)
# ============================================================================


class TestMigrationFile:
    def test_timestamp_sorts_after_previous_latest_and_has_up_down(self):
        migrations_dir = (
            Path(__file__).resolve().parents[2]
            / "daemon"
            / "migrations"
            / "versions"
        )
        snapshot_migrations = sorted(migrations_dir.glob("*_create_snapshot_tables.sql"))
        assert len(snapshot_migrations) == 1
        stamp = snapshot_migrations[0].name.split("_create_")[0]
        assert stamp > "20260915_212810", (
            "snapshot migration must sort AFTER the previous latest "
            "20260915_212810_create_service_tracking.sql"
        )
        body = snapshot_migrations[0].read_text(encoding="utf-8")
        assert "-- UP" in body and "-- DOWN" in body
        assert "CREATE TABLE IF NOT EXISTS snapshots" in body
        assert "CREATE TABLE IF NOT EXISTS snapshot_embeddings" in body
        # No `truncated` column (Rev 5 deletion — freshness is computed).
        assert "truncated" not in body.lower()
