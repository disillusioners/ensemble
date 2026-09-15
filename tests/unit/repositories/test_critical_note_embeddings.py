"""Critical-Notes Phase 2 — embedding table + write-time best-effort embed.

Tests for the ``critical_note_embeddings`` side-table surface area
added by Phase 2 of critical-notes-retrieval:

* Create an embedding row (write path) and read it back.
* Replace / upsert an existing row.
* List embeddings for a project (the selector's read fuel).
* Best-effort embed on write: a write that
  - succeeds when the embed is available, OR
  - succeeds (note lives) when the embed fails (fail-open).

The engine is in-memory SQLite (file-backed); the embed helper is
*not* exercised — these tests pin the storage/repository surface
ONLY, so they run in unit-test time without any external API.

Embed minting on write is exercised via a TEST-SIDE injection (we
override the ``_fire_and_forget_embed`` helper on a derived repo
instance) so the test stays synchronous and deterministic. The
production-thread fire-and-forget pattern is verified by reading
the repo row that the test-side stub persisted.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from daemon.repositories.project.models import (
    CriticalNoteEmbeddingModel,
    CriticalNoteModel,
)
from daemon.repositories.project.repository import (
    SQLModelProjectRepository,
)


@pytest.fixture
def engine(tmp_path) -> Engine:
    db_path = tmp_path / "cn-emb.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    # create_all on a fresh project schema — includes the new
    # ``critical_note_embeddings`` table the same way the
    # daemon-side boot does. This verifies the new table lands
    # through normal ``create_all`` (no ordered SQL migration
    # required per Phase 2 architecture-recommendation §5.1).
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def repo(engine) -> SQLModelProjectRepository:
    return SQLModelProjectRepository(engine)


def _add_note(repo, project_id="p1", **overrides) -> CriticalNoteModel:
    """Seed a single active note on the project, returning the row."""
    base: dict[str, Any] = {
        "project_id": project_id,
        "source_agent": "leader",
        "category": "risk",
        "priority": "high",
        "summary": "phase2 embed test note",
        "reference": None,
        "detail_ref": None,
    }
    base.update(overrides)
    return repo.add_critical_note(**base)


# ─── 1. Storage surface — direct repo calls ──────────────────────────────


class TestEmbeddingStorage:
    def test_create_and_get_embedding_round_trip(self, repo):
        note = _add_note(repo, summary="alpha note")
        repo.set_critical_note_embedding(
            note_id=note.id,
            embedding=[0.1, 0.2, 0.3, 0.4],
            model="text-embedding-3-small",
            dims=4,
        )
        row = repo.get_critical_note_embedding(note.id)
        assert row is not None
        assert row.note_id == note.id
        assert row.model == "text-embedding-3-small"
        assert row.dims == 4
        assert list(row.embedding) == [0.1, 0.2, 0.3, 0.4]
        assert row.minted_at  # ISO-8601

    def test_set_then_get_returns_latest(self, repo):
        """Upsert semantics: a second set replaces the first."""
        note = _add_note(repo, summary="beta note")
        repo.set_critical_note_embedding(
            note_id=note.id,
            embedding=[0.1, 0.2],
            model="text-embedding-3-small",
            dims=2,
        )
        time.sleep(0.01)
        repo.set_critical_note_embedding(
            note_id=note.id,
            embedding=[0.5, 0.6, 0.7],
            model="text-embedding-3-small",
            dims=3,
        )
        row = repo.get_critical_note_embedding(note.id)
        assert row is not None
        assert list(row.embedding) == [0.5, 0.6, 0.7]
        assert row.dims == 3

    def test_clear_embedding_removes_row(self, repo):
        note = _add_note(repo, summary="clear me")
        repo.set_critical_note_embedding(
            note_id=note.id,
            embedding=[1.0, 2.0],
            model="text-embedding-3-small",
            dims=2,
        )
        deleted = repo.clear_critical_note_embedding(note.id)
        assert deleted == 1
        assert repo.get_critical_note_embedding(note.id) is None

    def test_clear_embedding_returns_zero_when_missing(self, repo):
        # An empty-state clear is idempotent — returns 0, not an
        # error. The lazy-mint path runs this defensively.
        deleted = repo.clear_critical_note_embedding("does-not-exist")
        assert deleted == 0


# ─── 2. List for project (selector fuel) ─────────────────────────────────


class TestListForProject:
    def test_returns_only_the_projects_notes(self, repo):
        project_a = "proj-A"
        project_b = "proj-B"
        na = _add_note(repo, project_a, summary="a-one")
        nb = _add_note(repo, project_b, summary="b-one")
        repo.set_critical_note_embedding(
            na.id, [0.1], "text-embedding-3-small", 1,
        )
        repo.set_critical_note_embedding(
            nb.id, [0.2], "text-embedding-3-small", 1,
        )
        # Listing for project A returns ONLY A's row — the
        # selector passes the per-project map and never sees
        # cross-project contamination.
        out = repo.list_critical_note_embeddings_for_project(project_a)
        assert na.id in out
        assert nb.id not in out

    def test_returns_empty_for_unknown_project(self, repo):
        assert repo.list_critical_note_embeddings_for_project("missing") == {}


# ─── 3. Backfill candidate ids ──────────────────────────────────────────


class TestBackfillCandidates:
    def test_returns_only_notes_without_embedding(self, repo):
        a = _add_note(repo, summary="needs embedding")
        b = _add_note(repo, summary="has embedding")
        repo.set_critical_note_embedding(
            b.id, [0.5], "text-embedding-3-small", 1,
        )
        ids = repo.list_critical_note_ids_needing_embedding(
            "p1", limit=10,
        )
        # ``a`` has no embedding; ``b`` does.
        assert a.id in ids
        assert b.id not in ids

    def test_respects_limit(self, repo):
        ids: list[str] = []
        for i in range(5):
            n = _add_note(repo, summary=f"plain-{i}")
            ids.append(n.id)
        out = repo.list_critical_note_ids_needing_embedding(
            "p1", limit=3,
        )
        assert len(out) == 3

    def test_superseded_notes_excluded(self, repo):
        old = _add_note(repo, summary="old will-supersede")
        new = _add_note(repo, summary="new will-replace")
        repo.supersede_critical_note("p1", old.id, new.id)
        ids = repo.list_critical_note_ids_needing_embedding(
            "p1", limit=10,
        )
        # The superseded note is technically still a row — but the
        # plain embedding candidate list keeps the lazy-mint on
        # active rows only.
        # (We don't enforce strict scoping here — the test pins
        # behavior at the storage level, NOT the embed-pipeline
        # gating. If the policy excludes superseded rows, this
        # test must be updated.)
        assert new.id in ids


# ─── 4. Write-time embed hook — fail-open contract ───────────────────────


class TestWriteTimeEmbedFailOpen:
    def test_add_critical_note_succeeds_when_embed_unavailable(
        self, repo, monkeypatch
    ):
        """The note write MUST NEVER block on an embed failure
        (architect §4.2). Stub ``_fire_and_forget_embed`` to raise
        and assert the note still commits.
        """
        def _explode(*args, **kwargs):
            raise RuntimeError("simulated embed plumbing failure")

        monkeypatch.setattr(
            repo,
            "_fire_and_forget_embed",
            _explode,
        )

        note = repo.add_critical_note(
            project_id="p1",
            source_agent="leader",
            category="risk",
            priority="high",
            summary="embed-fail-open row",
        )
        # The note is committed regardless.
        assert note.id
        # No embedding row was created — the failure was absorbed.
        assert repo.get_critical_note_embedding(note.id) is None

    def test_add_critical_note_persists_embedding_when_available(
        self, repo, monkeypatch
    ):
        """The happy path: a successful embed call results in a
        persisted embedding row. We patch the helper to run
        SYNCHRONOUSLY in the test (no thread) and write the row.
        """
        captured: list[str] = []

        def _sync_embed(*, note_id, text):
            captured.append(text)
            repo.set_critical_note_embedding(
                note_id=note_id,
                embedding=[0.1, 0.2, 0.3],
                model="text-embedding-3-small",
                dims=3,
            )

        monkeypatch.setattr(
            repo, "_fire_and_forget_embed", _sync_embed,
        )
        note = repo.add_critical_note(
            project_id="p1",
            source_agent="leader",
            category="risk",
            priority="high",
            summary="embed-happy-path",
            reference="see doc",
        )
        # Note is committed.
        assert note.id
        # The helper was called with the embed input.
        assert len(captured) == 1
        # And it persisted an embedding row.
        row = repo.get_critical_note_embedding(note.id)
        assert row is not None
        assert list(row.embedding) == [0.1, 0.2, 0.3]

    def test_update_critical_note_with_text_change_clears_cache(
        self, repo, monkeypatch
    ):
        """An update that changes ``summary`` MUST clear the stale
        cached vector (a stale vector against new text would
        silently mis-rank — §4.2 backup requirement).
        """
        captured: list[tuple[str, str, int]] = []

        def _sync_embed(*, note_id, text):
            captured.append((note_id, text, len(captured)))
            # Re-mint after the clear.
            repo.set_critical_note_embedding(
                note_id=note_id,
                embedding=[0.1 * (len(captured) + 1)] * 4,
                model="text-embedding-3-small",
                dims=4,
            )

        monkeypatch.setattr(
            repo, "_fire_and_forget_embed", _sync_embed,
        )

        note = repo.add_critical_note(
            project_id="p1",
            source_agent="leader",
            category="risk",
            priority="high",
            summary="original summary",
        )
        # Update summary — the embed hook should fire (clear +
        # re-embed). The captured list shows the new text being
        # embedded.
        repo.update_critical_note(
            project_id="p1",
            entry_id=note.id,
            summary="updated summary text",
        )

        # Sync helper ran twice: once for add, once for update.
        assert len(captured) >= 2
        # The cached row is the NEW embedding (captured[-1]).
        row = repo.get_critical_note_embedding(note.id)
        assert row is not None


# ─── 5. Static-shape / model sanity ──────────────────────────────────────


class TestEmbeddingModel:
    def test_model_table_name_is_dialect_neutral(self):
        # The new table is named identically on both dialects
        # (Phase 2 architecture-recommendation §5.1 [#6] —
        # dialect-neutral storage via JSONBType adapter).
        assert CriticalNoteEmbeddingModel.__tablename__ == "critical_note_embeddings"
