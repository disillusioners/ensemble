"""UN-MOCKED critical-notes embed pipeline + boot probe (Phase-2 B3).

Why this file exists: every pre-existing embed-path test monkeypatched
the embedder at the module boundary (``build_critical_notes_embedder``
or ``embed_critical_note_text``), which is exactly why the Phase-2
REJECT verdict found the pipeline DEAD while the suite stayed green —
``build_critical_notes_embedder`` imported ``get_default_engine`` from
the nonexistent ``daemon.services.persistence`` module and the fail-open
contract swallowed the ModuleNotFoundError, so NO vector was ever
minted and the gate never noticed.

These tests run the REAL construction path end-to-end:

* the embedder is built by the real ``build_critical_notes_embedder``
  against a real FILE-BACKED SQLite engine (repo test convention:
  ``tmp_path`` + ``NullPool`` + WAL + ``busy_timeout`` — never
  ``StaticPool``/``:memory:``);
* the write-time, lazy-mint, and backfill mint sites each persist a
  real ``critical_note_embeddings`` row through the real repository;
* the boot probe (``probe_critical_notes_boot_state``) executes its
  non-deferred branch against the real engine.

What is stubbed, exactly, and why: ONLY the embedding HTTP/SDK
transport — ``SkillEmbeddingService.embed_text`` is replaced at the
CLASS level (not the module-level helpers) so the service construction,
engine binding, repo wiring, thread/asyncio bridge, and persistence are
all real. The transport call is the single external boundary (an
OpenAI-compatible ``/embeddings`` endpoint); stubbing deeper would
recreate the blindness this file exists to close.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.project.models  # noqa: F401  (table registration)
from daemon.config import probe_critical_notes_boot_state
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.services.critical_notes_embedding import (
    DEFAULT_EMBEDDING_MODEL,
    build_critical_notes_embedder,
    resolve_critical_notes_embedding_model,
)
from daemon.services.skill_embedding_service import SkillEmbeddingService


VECTOR = [0.1, 0.2, 0.3]


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout)."""
    db_path = tmp_path / "cn-embed-pipeline.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def repo(engine) -> SQLModelProjectRepository:
    return SQLModelProjectRepository(engine)


@pytest.fixture
def project_id(repo) -> str:
    project = repo.create(name="cn-embed-pipeline-project")
    return project.project_id


@pytest.fixture
def stub_transport(monkeypatch):
    """Stub ONLY the embedding HTTP/SDK transport (class level).

    The construction path (builder → repo → service) stays real; the
    single external network call is replaced with a deterministic
    vector so the tests run in unit time with no API dependency.
    """

    async def _fake_embed_text(self, text, *args, **kwargs):
        assert isinstance(text, str) and text.strip()
        return list(VECTOR)

    monkeypatch.setattr(SkillEmbeddingService, "embed_text", _fake_embed_text)


# ─── B3: real embedder construction ─────────────────────────────────────────


class TestRealEmbedderConstruction:
    def test_builder_returns_real_service_bound_to_caller_engine(
        self, engine
    ):
        """``build_critical_notes_embedder(engine=<real engine>)`` returns
        a live :class:`SkillEmbeddingService` whose embedding repo is
        bound to the CALLER's engine — no monkeypatching anywhere on
        this path (the pre-fix suite never exercised it un-mocked).
        """
        service = build_critical_notes_embedder(
            model=DEFAULT_EMBEDDING_MODEL, engine=engine,
        )
        assert service is not None
        assert isinstance(service, SkillEmbeddingService)
        # Engine wiring is REAL: the embedded repo targets OUR engine,
        # so a minted vector lands on the same DB that hosts the note.
        assert service.embedding_repo.engine is engine

    def test_builder_carries_real_embedding_config(self, engine):
        """The service carries a REAL embedding config object.

        Regression pin for the second half of the dead pipeline:
        ``SkillEmbeddingService.embed_text`` reads
        ``self.config.embedding_model`` unconditionally — the previous
        ``config=None`` made every real embed call raise
        AttributeError inside ``embed_text`` (absorbed as
        ``api_failure`` → None vector → permanent BM25-only).
        """
        service = build_critical_notes_embedder(
            model=DEFAULT_EMBEDDING_MODEL, engine=engine,
        )
        assert service is not None
        assert service.config is not None
        assert service.config.embedding_model == (
            resolve_critical_notes_embedding_model()
        )

    def test_builder_without_engine_degrades_loudly_telemetry(self, caplog):
        """``engine=None`` now degrades via the EXPLICIT no-engine
        branch (the invented ``daemon.services.persistence`` lazy
        import is gone — a ModuleNotFoundError must be impossible on
        this path).
        """
        import logging

        import daemon.services.critical_notes_embedding as emb_mod

        assert emb_mod.build_critical_notes_embedder(
            model=DEFAULT_EMBEDDING_MODEL, engine=None,
        ) is None
        with caplog.at_level(logging.INFO):
            emb_mod.build_critical_notes_embedder(
                model=DEFAULT_EMBEDDING_MODEL, engine=None,
            )
        assert any(
            "reason=no_engine" in rec.message for rec in caplog.records
        )


# ─── B3: mint sites run the REAL pipeline to a persisted row ────────────────


def _wait_for_embedding_row(repo, note_id, timeout_s: float = 5.0):
    """Poll for the write-time thread's row (fire-and-forget bridge)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        row = repo.get_critical_note_embedding(note_id)
        if row is not None:
            return row
        time.sleep(0.05)
    return None


class TestRealMintPipeline:
    def test_write_time_thread_persists_row_on_same_engine(
        self, repo, project_id, stub_transport,
    ):
        """Write → fire-and-forget embed → REAL persisted row.

        Exercises the full write-time chain with the transport stubbed:
        ``add_critical_note`` → ``_fire_and_forget_embed`` (daemon
        thread + asyncio.run) → REAL ``build_critical_notes_embedder``
        → transport stub → REAL ``set_critical_note_embedding`` on the
        SAME engine. Pre-fix, this chain died at the builder and minted
        nothing.
        """
        note = repo.add_critical_note(
            project_id=project_id,
            source_agent="leader",
            category="risk",
            priority="critical",
            summary="write-time pipeline pin",
        )
        row = _wait_for_embedding_row(repo, note.id)
        assert row is not None, (
            "write-time embed minted NO row — pipeline is dead"
        )
        assert list(row.embedding) == VECTOR
        # Item 7: the model stamp is the RESOLVED model, not a stale
        # hardcoded literal disconnected from the embed call.
        assert row.model == resolve_critical_notes_embedding_model()

    def test_backfill_mints_rows_through_real_builder(
        self, repo, project_id, stub_transport,
    ):
        """Explicit backfill runs the real builder → minted=1 + row."""
        repo.add_critical_note(
            project_id=project_id,
            source_agent="leader",
            category="convention",
            priority="high",
            summary="backfill pipeline pin",
        )
        summary = repo.backfill_critical_note_embeddings(project_id)
        assert "minted=1" in summary
        notes = repo.list_critical_notes(project_id)
        assert len(notes) == 1
        row = repo.get_critical_note_embedding(notes[0].id)
        assert row is not None
        assert list(row.embedding) == VECTOR
        assert row.model == resolve_critical_notes_embedding_model()

    @pytest.mark.asyncio
    async def test_lazy_mint_window_mints_through_real_builder(
        self, repo, project_id, stub_transport,
    ):
        """Lazy-mint cap window runs the real builder → row minted.

        The orchestrator's lazy-mint site previously called the embed
        WITHOUT an engine (same dead-import class); this drives the
        real window over a real repo and asserts the vector lands.
        """
        from types import SimpleNamespace

        from daemon.services.critical_notes_selection_orchestrator import (
            _lazy_mint_embeddings,
        )

        repo.add_critical_note(
            project_id=project_id,
            source_agent="leader",
            category="pattern",
            priority="medium",
            summary="lazy mint pipeline pin",
        )
        manager = SimpleNamespace(_project_repository=repo)
        minted = await _lazy_mint_embeddings(
            project_id=project_id, manager=manager, cap=10,
        )
        assert minted == 1
        notes = repo.list_critical_notes(project_id)
        row = repo.get_critical_note_embedding(notes[0].id)
        assert row is not None
        assert list(row.embedding) == VECTOR


# ─── B3: boot probe executes its NON-deferred branch ────────────────────────


class TestBootProbeNonDeferred:
    def test_probe_emits_real_counts_on_empty_store(self, engine, caplog):
        """The probe's non-deferred branch executes with a REAL engine:
        real counts, never the ``?/?`` deferred variant.
        """
        import logging

        with caplog.at_level(logging.INFO):
            probe_critical_notes_boot_state(engine=engine)
        state_lines = [
            rec.message for rec in caplog.records
            if "[CriticalNotes:state]" in rec.message
        ]
        assert len(state_lines) == 1
        assert "projects_with_pins=0/0" in state_lines[0]
        assert "total_pinned=0" in state_lines[0]
        assert "?/?" not in state_lines[0]
        assert "probe deferred" not in state_lines[0]

    def test_probe_counts_pinned_project(self, repo, project_id, engine, caplog):
        """A project with a pinned note reports
        ``projects_with_pins=1/1 total_pinned=1`` — the tiered-gate
        active signal operators grep for.
        """
        import logging

        note = repo.add_critical_note(
            project_id=project_id,
            source_agent="leader",
            category="risk",
            priority="critical",
            summary="probe pin",
        )
        assert repo.pin_critical_note(project_id, note.id, pinned=True)

        # A second, unpinned project — denominator moves, numerator
        # does not.
        other = repo.create(name="cn-probe-unpinned")
        repo.add_critical_note(
            project_id=other.project_id,
            source_agent="leader",
            category="pattern",
            priority="medium",
            summary="probe unpinned",
        )

        with caplog.at_level(logging.INFO):
            probe_critical_notes_boot_state(engine=engine)
        state_lines = [
            rec.message for rec in caplog.records
            if "[CriticalNotes:state]" in rec.message
        ]
        assert len(state_lines) == 1
        assert "projects_with_pins=1/2" in state_lines[0]
        assert "total_pinned=1" in state_lines[0]
        assert "?/?" not in state_lines[0]
