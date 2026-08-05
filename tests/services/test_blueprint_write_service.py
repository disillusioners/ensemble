"""Unit tests for the canonical write boundary — BlueprintWriteService.

Covers the five invariants enforced by the service:
  1. Rate limiter check before any write
  2. Trigger embedding generation (atomic with content, BEFORE commit)
  3. Revision snapshot capture (post-commit, via the repo's update())
  4. Atomic publish unit (content + triggers + embeddings)
  5. Rate limiter record (success/failure) after every operation

Plus: rollback on trigger-storage failure, abort on all-embeddings-failed,
disable records a revision, fail-open on limiter errors, no-limiter path.

The service is duck-typed; tests use lightweight mocks for the repo,
embedding repo, embedding service, and rate limiter. Async methods are
driven via ``asyncio.run`` (no pytest-asyncio dependency).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.blueprint_rate_limiter import BlueprintRateLimiter
from daemon.services.blueprint_write_service import (
    BlueprintNotFoundError,
    BlueprintPublishError,
    BlueprintRateLimitError,
    BlueprintWriteService,
)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _run(coro: Any) -> Any:
    """Drive an awaitable via a fresh event loop."""
    return asyncio.run(coro)


def _make_service(
    repo: Any = None,
    embedding_repo: Any = None,
    embedding_service: Any = None,
    rate_limiter: Any = None,
    project_id: str = "proj-1",
    manager: Any = None,
) -> BlueprintWriteService:
    """Build a BlueprintWriteService with the given (or mock) deps."""
    return BlueprintWriteService(
        repository=repo or MagicMock(),
        embedding_repository=embedding_repo or MagicMock(),
        embedding_service=embedding_service,
        rate_limiter=rate_limiter,
        config=MagicMock(),
        project_id=project_id,
        manager=manager or MagicMock(),
    )


def _make_embedding_service(
    embeddings: dict[str, list[float]] | None = None,
    fail_all: bool = False,
) -> MagicMock:
    """Mock embedding service.

    ``embeddings`` maps query→vector. ``fail_all`` makes every call raise.
    """
    svc = MagicMock()
    embeddings = embeddings or {}

    async def _embed(text: str) -> list[float]:
        if fail_all:
            raise RuntimeError("embedding API down")
        if text in embeddings:
            return embeddings[text]
        # Default: return a fixed vector for any query
        return [0.1, 0.2, 0.3]

    svc.embed_text = _embed
    return svc


class _FakeBlueprint:
    """Minimal blueprint stand-in."""

    def __init__(self, id: str = "bp-1") -> None:
        self.id = id
        self.project_id = "proj-1"
        self.slug = "s"
        self.name = "n"
        self.kind = "area"
        self.content = "c"
        self.version = 1
        self.source = "auto"
        self.is_active = True
        self.tags: list = []
        self.file_refs: list = []
        self.trigger_queries: list = []


# ─── Invariant 1: Rate limiter check ────────────────────────────────────────


class TestRateLimitCheck:
    """Rate limiter is checked before any write."""

    def test_create_rate_limited_aborts(self) -> None:
        """Limiter at capacity → create raises; repo.create NOT called."""
        repo = MagicMock()
        limiter = MagicMock()
        limiter.reserve.return_value = False
        svc = _make_service(repo=repo, rate_limiter=limiter)

        with pytest.raises(BlueprintRateLimitError):
            _run(svc.create_blueprint(slug="s", name="n", kind="area", content="c"))

        repo.create.assert_not_called()

    def test_create_fail_closed_on_limiter_error(self) -> None:
        """reserve() raises → create raises BlueprintRateLimitError (fail-closed).

        C2 fix d: a non-None limiter that raises is a programming/infra
        bug — fail-closed to prevent unthrottled writes.
        """
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()
        limiter = MagicMock()
        limiter.reserve.side_effect = RuntimeError("limiter broken")
        svc = _make_service(repo=repo, rate_limiter=limiter)

        # Should raise BlueprintRateLimitError (fail-CLOSED).
        with pytest.raises(BlueprintRateLimitError):
            _run(svc.create_blueprint(slug="s", name="n", kind="area", content="c"))
        repo.create.assert_not_called()

    def test_create_no_limiter_fail_open(self) -> None:
        """rate_limiter is None → create proceeds (graceful degradation)."""
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()
        svc = _make_service(repo=repo, rate_limiter=None)

        bp = _run(svc.create_blueprint(slug="s", name="n", kind="area", content="c"))
        assert bp is not None

    def test_update_rate_limited_aborts(self) -> None:
        repo = MagicMock()
        limiter = MagicMock()
        limiter.reserve.return_value = False
        svc = _make_service(repo=repo, rate_limiter=limiter)

        with pytest.raises(BlueprintRateLimitError):
            _run(svc.update_blueprint("bp-1", content="x"))

        repo.update.assert_not_called()

    def test_disable_rate_limited_aborts(self) -> None:
        repo = MagicMock()
        limiter = MagicMock()
        limiter.reserve.return_value = False
        svc = _make_service(repo=repo, rate_limiter=limiter)

        with pytest.raises(BlueprintRateLimitError):
            _run(svc.disable_blueprint("bp-1"))

        repo.soft_delete.assert_not_called()


# ─── Invariant 5: Rate limiter record ───────────────────────────────────────


class TestRateLimitRecord:
    """Rate limiter records failure after every failed operation.

    C2 fix a: after switching to ``reserve()``, success=True is a no-op
    (the reserve already consumed the slot). Only failure is recorded.
    """

    def test_create_success_does_not_call_record_success(self) -> None:
        """On success, record_success is NOT called (reserve consumed the slot)."""
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()
        limiter = MagicMock()
        limiter.reserve.return_value = True
        svc = _make_service(repo=repo, rate_limiter=limiter)

        _run(svc.create_blueprint(slug="s", name="n", kind="area", content="c"))
        limiter.record_success.assert_not_called()
        limiter.record_failure.assert_not_called()

    def test_create_records_failure_on_repo_error(self) -> None:
        repo = MagicMock()
        repo.create.side_effect = RuntimeError("DB down")
        limiter = MagicMock()
        limiter.reserve.return_value = True
        svc = _make_service(repo=repo, rate_limiter=limiter)

        with pytest.raises(RuntimeError):
            _run(svc.create_blueprint(slug="s", name="n", kind="area", content="c"))
        limiter.record_failure.assert_called_once_with("proj-1")


# ─── Invariant 2 + 4: Atomic publish unit (embed BEFORE commit) ─────────────


class TestCreatePublishUnit:
    """create_blueprint embeds before commit and stores triggers atomically."""

    def test_create_embeds_before_commit(self) -> None:
        """embed_text called BEFORE repo.create."""
        call_order: list[str] = []
        emb_repo = MagicMock()

        emb_svc = MagicMock()

        async def _embed(text: str) -> list[float]:
            call_order.append("embed")
            return [1.0, 2.0]

        emb_svc.embed_text = _embed

        repo = MagicMock()

        def _create(**kwargs):
            call_order.append("create")
            return _FakeBlueprint()

        repo.create = _create

        svc = _make_service(
            repo=repo,
            embedding_repo=emb_repo,
            embedding_service=emb_svc,
        )

        _run(svc.create_blueprint(
            slug="s", name="n", kind="area", content="c",
            trigger_queries=["q1"],
        ))

        assert call_order == ["embed", "create"]
        emb_repo.replace_triggers.assert_called_once()

    def test_create_aborts_on_all_embeddings_failed(self) -> None:
        """All embed_text calls raise → repo.create NOT called."""
        emb_svc = _make_embedding_service(fail_all=True)
        repo = MagicMock()

        svc = _make_service(
            repo=repo,
            embedding_service=emb_svc,
        )

        with pytest.raises(BlueprintPublishError, match="All trigger embeddings failed"):
            _run(svc.create_blueprint(
                slug="s", name="n", kind="area", content="c",
                trigger_queries=["q1", "q2"],
            ))

        repo.create.assert_not_called()

    def test_create_partial_embeddings_succeeds(self) -> None:
        """Some embeds succeed, some fail → create called with successful subset."""
        emb_svc = MagicMock()
        call_count = {"n": 0}

        async def _embed(text: str) -> list[float]:
            call_count["n"] += 1
            if text == "bad":
                raise RuntimeError("fail")
            return [1.0]

        emb_svc.embed_text = _embed
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()
        emb_repo = MagicMock()

        svc = _make_service(
            repo=repo,
            embedding_repo=emb_repo,
            embedding_service=emb_svc,
        )

        bp = _run(svc.create_blueprint(
            slug="s", name="n", kind="area", content="c",
            trigger_queries=["good1", "bad", "good2"],
        ))

        assert bp is not None
        repo.create.assert_called_once()
        # Only the 2 successful embeddings stored.
        emb_repo.replace_triggers.assert_called_once()
        stored = emb_repo.replace_triggers.call_args[0][1]
        assert len(stored) == 2

    def test_create_rolls_back_on_trigger_storage_failure(self) -> None:
        """replace_triggers raises after create → soft_delete called."""
        emb_svc = _make_embedding_service()
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint(id="bp-new")
        emb_repo = MagicMock()
        emb_repo.replace_triggers.side_effect = RuntimeError("trigger store down")

        svc = _make_service(
            repo=repo,
            embedding_repo=emb_repo,
            embedding_service=emb_svc,
        )

        with pytest.raises(BlueprintPublishError, match="rolled back"):
            _run(svc.create_blueprint(
                slug="s", name="n", kind="area", content="c",
                trigger_queries=["q1"],
            ))

        repo.soft_delete.assert_called_once_with("bp-new")

    def test_create_captures_initial_revision(self) -> None:
        """create_blueprint captures a version=1, source='create' revision.

        Per spec line 30: when a blueprint is created via ANY write path,
        exactly one revision row is captured. Previously only update()
        and disable() captured revisions, so a fresh blueprint had zero
        revision history until its first update.
        """
        new_bp = _FakeBlueprint(id="bp-new")
        new_bp.content = "hello"
        new_bp.file_refs = ["a.md"]
        new_bp.tags = [{"k": "v"}]
        new_bp.trigger_queries = ["q1", "q2"]
        new_bp.version = 1

        repo = MagicMock()
        repo.create.return_value = new_bp

        svc = _make_service(repo=repo)

        _run(svc.create_blueprint(
            slug="s", name="n", kind="area", content="hello",
            trigger_queries=["q1", "q2"],
            tags=[{"k": "v"}], file_refs=["a.md"],
            reason="initial",
        ))

        # add_revision called exactly once with version=1, source='create'.
        repo.add_revision.assert_called_once()
        rev = repo.add_revision.call_args
        assert rev.kwargs.get("version") == 1
        assert rev.kwargs.get("source") == "create"
        assert rev.kwargs.get("content_snapshot") == "hello"
        assert rev.kwargs.get("file_refs") == ["a.md"]
        assert rev.kwargs.get("tags") == [{"k": "v"}]
        assert rev.kwargs.get("trigger_queries") == ["q1", "q2"]
        assert rev.kwargs.get("reason") == "initial"

    def test_create_revision_failure_does_not_block(self) -> None:
        """add_revision raises → create still succeeds (C8 fail-open)."""
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint(id="bp-new")
        repo.add_revision.side_effect = RuntimeError("rev fail")

        svc = _make_service(repo=repo)

        bp = _run(svc.create_blueprint(
            slug="s", name="n", kind="area", content="c",
        ))
        assert bp is not None
        repo.create.assert_called_once()


# ─── C4 fix 2: trigger_queries semantics (None vs []) ───────────────────────


class TestUpdateTriggerSemantics:
    """update_blueprint distinguishes None (no-op) from [] (clear)."""

    def test_update_with_none_trigger_queries_leaves_triggers(self) -> None:
        """trigger_queries=None → replace_triggers NOT called."""
        repo = MagicMock()
        bp = _FakeBlueprint()
        repo.update.return_value = bp
        emb_repo = MagicMock()

        svc = _make_service(repo=repo, embedding_repo=emb_repo)

        _run(svc.update_blueprint("bp-1", content="x"))
        emb_repo.replace_triggers.assert_not_called()

    def test_update_with_empty_trigger_queries_clears_triggers(self) -> None:
        """trigger_queries=[] → replace_triggers(id, []) called."""
        repo = MagicMock()
        bp = _FakeBlueprint()
        repo.update.return_value = bp
        emb_repo = MagicMock()

        svc = _make_service(repo=repo, embedding_repo=emb_repo)

        _run(svc.update_blueprint("bp-1", trigger_queries=[]))
        emb_repo.replace_triggers.assert_called_once()
        # Second positional arg is the items list — must be empty.
        items = emb_repo.replace_triggers.call_args[0][1]
        assert items == []

    def test_update_with_trigger_queries_replaces(self) -> None:
        """trigger_queries=[a,b] → replace_triggers(id, [(a,vec),(b,vec)])."""
        repo = MagicMock()
        bp = _FakeBlueprint()
        repo.update.return_value = bp
        emb_repo = MagicMock()
        emb_svc = _make_embedding_service()

        svc = _make_service(
            repo=repo,
            embedding_repo=emb_repo,
            embedding_service=emb_svc,
        )

        _run(svc.update_blueprint("bp-1", trigger_queries=["a", "b"]))
        emb_repo.replace_triggers.assert_called_once()
        items = emb_repo.replace_triggers.call_args[0][1]
        assert len(items) == 2


# ─── C4 fix 3: reason extraction ────────────────────────────────────────────


class TestUpdateReasonExtraction:
    """update_blueprint passes reason through; repo.extract prevents ValueError."""

    def test_update_extracts_reason_before_setattr(self) -> None:
        """update(content='x', reason='y') → repo.update called with reason kwarg."""
        repo = MagicMock()
        repo.update.return_value = _FakeBlueprint()

        svc = _make_service(repo=repo)

        _run(svc.update_blueprint("bp-1", content="x", reason="y"))

        # The service calls repo.update(blueprint_id, reason=reason, **fields)
        repo.update.assert_called_once()
        call_kwargs = repo.update.call_args
        assert call_kwargs.kwargs.get("reason") == "y"
        assert call_kwargs.kwargs.get("content") == "x"


# ─── disable_blueprint ──────────────────────────────────────────────────────


class TestDisableBlueprint:
    """disable_blueprint soft-deletes and records a final revision."""

    def test_disable_records_revision(self) -> None:
        repo = MagicMock()
        repo.soft_delete.return_value = True

        svc = _make_service(repo=repo)

        result = _run(svc.disable_blueprint("bp-1", reason="obsolete"))
        assert result is True

        repo.soft_delete.assert_called_once_with("bp-1")
        repo.add_revision.assert_called_once()
        rev_kwargs = repo.add_revision.call_args.kwargs
        assert rev_kwargs["version"] == -1
        assert rev_kwargs["source"] == "disable"
        assert rev_kwargs["reason"] == "obsolete"

    def test_disable_missing_raises_not_found(self) -> None:
        repo = MagicMock()
        repo.soft_delete.return_value = False

        svc = _make_service(repo=repo)

        with pytest.raises(BlueprintNotFoundError):
            _run(svc.disable_blueprint("missing"))

    def test_disable_revision_failure_does_not_block(self) -> None:
        """add_revision raises → disable still succeeds (C8)."""
        repo = MagicMock()
        repo.soft_delete.return_value = True
        repo.add_revision.side_effect = RuntimeError("rev fail")

        svc = _make_service(repo=repo)

        result = _run(svc.disable_blueprint("bp-1"))
        assert result is True


# ─── update rollback on trigger failure ─────────────────────────────────────


class TestUpdateRollback:
    """update rolls back on trigger-storage failure via in-memory snapshot.

    Fix for the BLOCKER bug where the old code read
    ``repository.list_revisions(blueprint_id, limit=1)`` to find the
    "prior content" to restore. But ``repository.update()`` had already
    auto-captured a revision with the NEW content (G2), so
    ``list_revisions`` returned the NEW revision and the rollback wrote
    new content back onto new content — a complete no-op. On the first
    update of a fresh blueprint (v=1 → v=2), no prior revision existed
    and the rollback silently did nothing.

    The fix snapshots the pre-update state in memory before calling
    ``repository.update()`` and restores from that snapshot on rollback.
    """

    def test_update_rolls_back_on_trigger_storage_failure(self) -> None:
        """Rollback restores from in-memory snapshot, not from revisions."""
        # Pre-existing blueprint state.
        pre = _FakeBlueprint(id="bp-1")
        pre.content = "old"
        pre.tags = [{"k": "v1"}]
        pre.file_refs = ["ref1"]
        pre.trigger_queries = ["old_q"]
        pre.version = 1

        repo = MagicMock()
        repo.get_by_id.return_value = pre
        # First update returns the "new" blueprint; subsequent returns
        # are irrelevant (no further read).
        new_bp = _FakeBlueprint(id="bp-1")
        new_bp.content = "new"
        new_bp.tags = []
        new_bp.file_refs = []
        new_bp.trigger_queries = ["q"]
        new_bp.version = 2
        repo.update.return_value = new_bp

        emb_repo = MagicMock()
        emb_repo.replace_triggers.side_effect = RuntimeError("store down")
        emb_svc = _make_embedding_service()

        svc = _make_service(
            repo=repo,
            embedding_repo=emb_repo,
            embedding_service=emb_svc,
        )

        with pytest.raises(BlueprintPublishError, match="rolled back"):
            _run(svc.update_blueprint(
                "bp-1", content="new", trigger_queries=["q"]
            ))

        # Two update calls: the new write + the rollback.
        assert repo.update.call_count == 2
        # The second call (rollback) restores the pre-state across
        # ALL four version-incrementing fields, not just content.
        rollback = repo.update.call_args_list[1]
        assert rollback.kwargs.get("content") == "old"
        assert rollback.kwargs.get("tags") == [{"k": "v1"}]
        assert rollback.kwargs.get("file_refs") == ["ref1"]
        assert rollback.kwargs.get("trigger_queries") == ["old_q"]
        # Rollback does NOT use list_revisions (BLOCKER fix).
        repo.list_revisions.assert_not_called()

    def test_update_rollback_restores_all_fields(self) -> None:
        """Rollback restores ALL four fields when all four were updated."""
        pre = _FakeBlueprint(id="bp-1")
        pre.content = "old"
        pre.tags = [{"k": "old"}]
        pre.file_refs = ["old_ref"]
        pre.trigger_queries = ["old_q"]
        pre.version = 1

        repo = MagicMock()
        repo.get_by_id.return_value = pre
        new_bp = _FakeBlueprint(id="bp-1")
        new_bp.content = "new"
        new_bp.tags = [{"k": "new"}]
        new_bp.file_refs = ["new_ref"]
        new_bp.trigger_queries = ["new_q1", "new_q2"]
        new_bp.version = 2
        repo.update.return_value = new_bp

        emb_repo = MagicMock()
        emb_repo.replace_triggers.side_effect = RuntimeError("store down")
        emb_svc = _make_embedding_service()

        svc = _make_service(
            repo=repo,
            embedding_repo=emb_repo,
            embedding_service=emb_svc,
        )

        with pytest.raises(BlueprintPublishError, match="rolled back"):
            _run(svc.update_blueprint(
                "bp-1",
                content="new",
                tags=[{"k": "new"}],
                file_refs=["new_ref"],
                trigger_queries=["new_q1", "new_q2"],
            ))

        # Exactly two update calls: forward write + rollback.
        assert repo.update.call_count == 2
        rollback = repo.update.call_args_list[1]
        # All four version-incrementing fields restored to pre-state.
        assert rollback.kwargs.get("content") == "old"
        assert rollback.kwargs.get("tags") == [{"k": "old"}]
        assert rollback.kwargs.get("file_refs") == ["old_ref"]
        assert rollback.kwargs.get("trigger_queries") == ["old_q"]


# ─── Integration: real rate limiter ─────────────────────────────────────────


class TestRealRateLimiter:
    """Drive the real BlueprintRateLimiter to verify wiring."""

    def test_create_blocks_at_capacity(self) -> None:
        limiter = BlueprintRateLimiter(max_revisions_per_hour=2)
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()
        svc = _make_service(repo=repo, rate_limiter=limiter)

        # First two succeed.
        _run(svc.create_blueprint(slug="s1", name="n", kind="area", content="c"))
        _run(svc.create_blueprint(slug="s2", name="n", kind="area", content="c"))
        # Third is rate-limited.
        with pytest.raises(BlueprintRateLimitError):
            _run(svc.create_blueprint(slug="s3", name="n", kind="area", content="c"))

        assert repo.create.call_count == 2


# ─── C1: status field support ──────────────────────────────────────────────


class TestUpdateStatusField:
    """``update_blueprint`` accepts and passes through ``status`` (C1 fix).

    status is a metadata field — it updates the Blueprint row but does
    NOT increment the version (not in the version-increment set).
    """

    def test_update_with_status_field(self) -> None:
        """status='draft' is passed to repo.update as a field."""
        repo = MagicMock()
        bp = _FakeBlueprint()
        repo.update.return_value = bp
        repo.get_by_id.return_value = _FakeBlueprint()

        svc = _make_service(repo=repo)

        result = _run(svc.update_blueprint("bp-1", status="draft"))
        assert result is not None

        repo.update.assert_called_once()
        call_kwargs = repo.update.call_args
        assert call_kwargs.kwargs.get("status") == "draft"

    def test_status_only_does_not_require_other_fields(self) -> None:
        """A status-only update does not raise ValueError('No fields')."""
        repo = MagicMock()
        bp = _FakeBlueprint()
        repo.update.return_value = bp
        repo.get_by_id.return_value = _FakeBlueprint()

        svc = _make_service(repo=repo)

        result = _run(svc.update_blueprint("bp-1", status="draft"))
        assert result is not None
        repo.update.assert_called_once()


# ─── C3: rollback soft_delete failure is logged ────────────────────────────


class TestCreateRollbackFailureLogged:
    """C3 fix: rollback soft_delete failure is logged at ERROR level."""

    def test_create_rollback_soft_delete_failure_logged(
        self, caplog
    ) -> None:
        """replace_triggers AND soft_delete both raise → error logged +
        BlueprintPublishError raised (C3 fix + W10 item 5)."""
        import logging as _logging

        emb_svc = _make_embedding_service()
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint(id="bp-new")
        repo.soft_delete.side_effect = RuntimeError("soft_delete also down")
        emb_repo = MagicMock()
        emb_repo.replace_triggers.side_effect = RuntimeError("trigger store down")

        svc = _make_service(
            repo=repo,
            embedding_repo=emb_repo,
            embedding_service=emb_svc,
        )

        with caplog.at_level(_logging.ERROR, logger="daemon.services.blueprint_write_service"):
            with pytest.raises(BlueprintPublishError, match="rolled back"):
                _run(svc.create_blueprint(
                    slug="s", name="n", kind="area", content="c",
                    trigger_queries=["q1"],
                ))

        # The rollback failure was logged at ERROR.
        assert any(
            "Rollback soft_delete failed" in rec.message
            for rec in caplog.records
        ), f"Expected rollback failure log, got: {[r.message for r in caplog.records]}"
        repo.soft_delete.assert_called_once_with("bp-new")


# ─── C4: concurrent updates to same blueprint are serialized ────────────────


class TestConcurrentUpdateSerialization:
    """C4 fix: per-blueprint lock serializes concurrent updates."""

    def test_concurrent_updates_to_same_blueprint_serialized(self) -> None:
        """Two concurrent update_blueprint calls on the same blueprint
        are serialized — they never overlap inside the critical section.

        Uses a shared concurrency counter to detect overlap: if the lock
        is missing, two concurrent updates will both be in-flight inside
        the repo.update call simultaneously. With the lock, they run one
        at a time. We also assert both succeed and the final state is
        valid (no corruption).
        """
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        from daemon.repositories.blueprint.repository import BlueprintRepository
        from daemon.repositories.blueprint.embedding_repository import (
            BlueprintEmbeddingRepository,
        )

        eng = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(eng)
        repo = BlueprintRepository(eng)
        emb_repo = BlueprintEmbeddingRepository(eng)
        bp = repo.create(
            project_id="proj-1", slug="s", name="n",
            kind="area", content="base",
        )

        # Instrument: track max concurrency inside update.
        max_concurrent = 0
        current_concurrent = 0
        track_lock = threading.Lock()
        original_update = repo.update

        def _tracking_update(blueprint_id, **kwargs):
            nonlocal max_concurrent, current_concurrent
            with track_lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            try:
                return original_update(blueprint_id, **kwargs)
            finally:
                with track_lock:
                    current_concurrent -= 1

        repo.update = _tracking_update

        svc = _make_service(repo=repo, embedding_repo=emb_repo)

        async def _concurrent_updates() -> list:
            async def _update(content: str) -> Any:
                return await svc.update_blueprint(bp.id, content=content)

            # Run two concurrent updates.
            return await asyncio.gather(
                _update("content-A"),
                _update("content-B"),
            )

        results = _run(_concurrent_updates())

        # Both updates succeeded (no lost update, no exception).
        assert all(r is not None for r in results)
        # The lock serialized them — never more than 1 concurrent update.
        assert max_concurrent == 1, (
            f"Expected max 1 concurrent update (serialized), "
            f"got {max_concurrent}"
        )
        # Final state is valid (one of the two writes).
        final = repo.get_by_id(bp.id)
        assert final.content in ("content-A", "content-B")


# ─── C6: rollback revision has reason ───────────────────────────────────────


class TestUpdateRollbackRevisionReason:
    """C6 fix: rollback update passes reason='rollback after trigger
    storage failure' so the audit trail marks rollbacks."""

    def test_update_rollback_revision_has_reason(self) -> None:
        """The rollback update call includes the rollback reason."""
        pre = _FakeBlueprint(id="bp-1")
        pre.content = "old"
        pre.version = 1

        repo = MagicMock()
        repo.get_by_id.return_value = pre
        new_bp = _FakeBlueprint(id="bp-1")
        new_bp.content = "new"
        new_bp.version = 2
        repo.update.return_value = new_bp

        emb_repo = MagicMock()
        emb_repo.replace_triggers.side_effect = RuntimeError("store down")
        emb_svc = _make_embedding_service()

        svc = _make_service(
            repo=repo,
            embedding_repo=emb_repo,
            embedding_service=emb_svc,
        )

        with pytest.raises(BlueprintPublishError, match="rolled back"):
            _run(svc.update_blueprint(
                "bp-1", content="new", trigger_queries=["q"]
            ))

        # Two update calls: forward write + rollback.
        assert repo.update.call_count == 2
        rollback = repo.update.call_args_list[1]
        # C6 fix: rollback has the rollback reason.
        assert rollback.kwargs.get("reason") == "rollback after trigger storage failure"
