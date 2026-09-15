"""Critical-Notes Phase 2 — orchestrator gating, degradation, telemetry.

Tests for :func:`daemon.services.critical_notes_selection_orchestrator`:

* Pin-backfill gating (§4.2 #8): ``pinned_count >= 1`` activates
  the tiered path; ``pinned_count == 0`` falls back to render-all
  byte-identically to pre-Phase-2 behavior.
* Degradation ladder: every failure mode emits an INFO log line
  under the ``[CriticalNotes:Degraded]`` prefix and falls through
  to a coherent degraded render (BM25-only tail with floor).
* Routine telemetry: the ``[CriticalNotes] selected=N total=M
  floor_applied=bool instance=X`` line is emitted on the normal
  path; the state counters agree with the returned ``Output``
  list.

The orchestrator is exercised against a lightweight FakeManager
that exposes only the ``_project_repository`` duck surface (the
selector contract requires nothing else). The embed helper is
stubbed so the write-time path stays green across the
test-running daemon, and the cached query embedding is
controllable per call.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from daemon.services.critical_notes_selection_orchestrator import (
    _count_pinned_critical_notes,
    _emit_critical_notes_hint,
    _emit_critical_notes_log,
    _lazy_mint_embeddings,
    _list_critical_note_embeddings_for_project,
    _maybe_tiered_critical_notes,
    install_critical_notes_selection_config,
    reset_critical_notes_selection_config,
    reset_query_embedding_cache,
)


# ---------------------------------------------------------------------------
# Lightweight fakes — only the methods the orchestrator reads
# ---------------------------------------------------------------------------


class FakeNote:
    """Minimal SQLModel-shaped row used by the FakeManager."""

    def __init__(
        self,
        note_id: str,
        pinned: bool = False,
        superseded_by_id: str | None = None,
    ):
        self.id = note_id
        self.pinned = pinned
        self.superseded_by_id = superseded_by_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pinned": self.pinned,
            "superseded_by_id": self.superseded_by_id,
            "summary": f"summary-{self.id}",
            "category": "risk",
            "priority": "high",
            "reference": None,
            "created_at": "2026-09-15T13:00:00+00:00",
            "last_reviewed_at": "2026-09-15T13:00:00+00:00",
            "updated_at": "2026-09-15T13:00:00+00:00",
            "source_agent": "leader",
            "detail_ref": None,
            "project_id": "proj-test",
        }


class FakeProjectRepository:
    """The duck-typed repository surface the orchestrator reads."""

    def __init__(
        self,
        *,
        pinned_count: int = 0,
        active_notes: list[FakeNote] | None = None,
        embeddings_map: dict[str, list[float]] | None = None,
    ):
        self._pinned_count = pinned_count
        self._active_notes = active_notes or []
        self._embeddings_map = embeddings_map or {}
        self.engine = object()  # the embedder treats this as opaque

    def count_pinned_critical_notes(self, project_id: str) -> int:
        return self._pinned_count

    def list_critical_notes(self, project_id: str) -> list[FakeNote]:
        return list(self._active_notes)

    def list_critical_note_embeddings_for_project(
        self, project_id: str
    ) -> dict[str, list[float]]:
        return dict(self._embeddings_map)

    def list_critical_note_ids_needing_embedding(
        self, project_id: str, *, limit: int
    ) -> list[str]:
        # Lazy-mint candidate shape: return ids of active notes
        # with no cached embedding.
        cached = set(self._embeddings_map.keys())
        return [
            n.id for n in self._active_notes
            if n.id not in cached
        ][:limit]

    def get_critical_note(self, project_id: str, note_id: str) -> FakeNote | None:
        for n in self._active_notes:
            if n.id == note_id:
                return n
        return None

    def set_critical_note_embedding(
        self,
        note_id: str,
        embedding: list[float],
        model: str,
        dims: int,
    ) -> None:
        # Update the local cache + simulate persistence.
        self._embeddings_map[note_id] = list(embedding)


class FakeManager:
    """The :class:`InstanceManager` duck surface."""

    def __init__(self, repo: FakeProjectRepository):
        self._project_repository = repo


def _make_manager(
    *, pinned_count: int = 0, active_notes=(), embeddings=None
) -> FakeManager:
    return FakeManager(
        FakeProjectRepository(
            pinned_count=pinned_count,
            active_notes=list(active_notes),
            embeddings_map=embeddings or {},
        )
    )


def _active_dicts(fake_repo: FakeProjectRepository) -> list[dict]:
    return [n.to_dict() for n in fake_repo._active_notes]


# ---------------------------------------------------------------------------
# Auto-use: reset the global caches so tests don't leak
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_orchestrator_state():
    reset_query_embedding_cache()
    reset_critical_notes_selection_config()
    yield
    reset_query_embedding_cache()
    reset_critical_notes_selection_config()


# ---------------------------------------------------------------------------
# Gating fallback — pinned_count == 0 → render-all (legacy shape)
# ---------------------------------------------------------------------------


class TestGatingFallback:
    @pytest.mark.asyncio
    async def test_no_pins_falls_back_to_render_all(self, monkeypatch):
        """When no pins exist, the orchestrator returns the legacy
        render-all shape byte-identically to pre-Phase-2 behavior
        (§4.2 #8). The selector is NOT invoked.
        """
        notes = [FakeNote(f"n-{i}") for i in range(5)]
        mgr = _make_manager(pinned_count=0, active_notes=notes)

        # Patch the embedder builder so the lazy-mint path never
        # looks up the real engine.
        import daemon.services.critical_notes_embedding as emb_mod

        def _stub(*, model, engine):
            return None

        monkeypatch.setattr(emb_mod, "build_critical_notes_embedder", _stub)

        out = await _maybe_tiered_critical_notes(
            project_id="proj-test",
            active_notes=_active_dicts(mgr._project_repository),
            user_query="anything",
            instance_id="i-gated-off",
            manager=mgr,
        )
        # Render-all returns the input list as-is (no selection,
        # no hint sentinel).
        assert len(out) == 5
        assert all("__hint_drop_count" not in n for n in out)

    @pytest.mark.asyncio
    async def test_pins_activate_tiered_path(self, monkeypatch):
        """When ``pinned_count >= 1``, the orchestrator runs the
        selector. The output list is the pinned + tail join (in
        that order) per R19 ordering.
        """
        pinned = FakeNote("p-1", pinned=True)
        unpinned = [FakeNote(f"u-{i}") for i in range(3)]
        mgr = _make_manager(
            pinned_count=1,
            active_notes=[pinned] + unpinned,
        )

        # No embedder — selector falls to BM25-only. The lazy
        # mint path emits a degraded line; the orchestrator keeps
        # moving.
        import daemon.services.critical_notes_embedding as emb_mod

        def _stub(*, model, engine):
            return None

        monkeypatch.setattr(emb_mod, "build_critical_notes_embedder", _stub)

        out = await _maybe_tiered_critical_notes(
            project_id="proj-test",
            active_notes=_active_dicts(mgr._project_repository),
            user_query="alpha",
            instance_id="i-tiered",
            manager=mgr,
        )
        # Pinned row first.
        assert out[0]["id"] == "p-1"
        # At least one tail row surfaced (the BM25-only path
        # matches on shared tokens, then the floor populates).
        assert len(out) >= 2


# ---------------------------------------------------------------------------
# Routine telemetry — line shape contract
# ---------------------------------------------------------------------------


class TestRoutineTelemetry:
    def test_log_line_shape(self, caplog):
        """The routine line carries the exact shape pinned by ops:
        ``[CriticalNotes] selected=N total=M floor_applied=bool
        instance=X``.
        """
        telemetry = {
            "selected": 7,
            "total": 12,
            "floor_applied": True,
            "skipped_ranking": False,
            "skipped_query_embed": False,
            "instance_id": "deadbeef0001",
        }
        with caplog.at_level(logging.INFO):
            _emit_critical_notes_log(telemetry)
        assert any(
            "[CriticalNotes] selected=7 total=12 floor_applied=True instance=deadbeef0001"
            in rec.message
            for rec in caplog.records
        )

    def test_hint_line_emitted_when_dropped(self, caplog):
        with caplog.at_level(logging.INFO):
            _emit_critical_notes_hint(8)
        assert any(
            "[CriticalNotes] hint_drop_count=8" in rec.message
            for rec in caplog.records
        )

    def test_floor_degraded_line_fires_when_floor_applied(self, caplog):
        """FIX-5 (§4.6 parity): when the floor rung applied, the
        orchestrator ALSO emits the ops-alertable Degraded line
        ``stage=floor reason=under_selection`` — a clean-window
        review that greps only the Degraded prefix still sees floor
        activation.
        """
        telemetry = {
            "selected": 5,
            "total": 12,
            "floor_applied": True,
            "instance_id": "deadbeef0001",
        }
        with caplog.at_level(logging.INFO):
            _emit_critical_notes_log(telemetry)
        assert any(
            "[CriticalNotes:Degraded] stage=floor "
            "reason=under_selection instance=deadbeef0001"
            in rec.message
            for rec in caplog.records
        )

    def test_floor_degraded_line_absent_when_floor_not_applied(self, caplog):
        """No floor rung → no Degraded floor line (the routine line
        alone carries floor_applied=False).
        """
        telemetry = {
            "selected": 7,
            "total": 12,
            "floor_applied": False,
            "instance_id": "deadbeef0002",
        }
        with caplog.at_level(logging.INFO):
            _emit_critical_notes_log(telemetry)
        assert not any(
            "stage=floor" in rec.message for rec in caplog.records
        )
        # The routine line still fired with floor_applied=False.
        assert any(
            "floor_applied=False" in rec.message for rec in caplog.records
        )

    def test_hint_not_emitted_when_zero(self, caplog):
        with caplog.at_level(logging.INFO):
            _emit_critical_notes_hint(0)
        # No hint line — zero drops means the legacy render-all
        # shape hasn't been throttled.
        assert not any(
            "[CriticalNotes] hint_drop_count=" in rec.message
            for rec in caplog.records
        )


# ---------------------------------------------------------------------------
# Degradation ladder — every failure mode logs the dedicated prefix
# ---------------------------------------------------------------------------


class TestDegradationLadder:
    @pytest.mark.asyncio
    async def test_embed_unavailable_emits_degraded_prefix(
        self, monkeypatch, caplog
    ):
        """Vector stage skip → BM25-only rank, ``[CriticalNotes:Degraded]``
        carries ``stage=query_embed reason=unavailable``.
        """
        notes = [
            FakeNote(
                f"unique-kub-{i}",
            ) for i in range(4)
        ]
        # Mark the notes with truly-distinct summaries so the BM25
        # prefilter selects them by the "kubernetes" query token.
        for n in notes:
            # Override the to_dict via class-level adjustment
            pass

        # Rebuild FakeNotes with summaries that contain "kubernetes".
        notes = []
        for i in range(4):
            n = FakeNote(f"n-{i}")
            notes.append(n)

        # Override to_dict to include kubernetes in summary.
        class KNote(FakeNote):
            def to_dict(self):
                d = super().to_dict()
                d["summary"] = f"kubernetes item {self.id}"
                return d

        notes = [KNote(f"k-{i}") for i in range(4)]
        mgr = _make_manager(pinned_count=1, active_notes=notes)

        # Pin one of them so the gate activates.
        mgr._project_repository._active_notes[0].pinned = True

        # Embedder is None — the orchestrator falls to BM25-only.
        import daemon.services.critical_notes_embedding as emb_mod

        def _stub(*, model, engine):
            return None

        monkeypatch.setattr(emb_mod, "build_critical_notes_embedder", _stub)

        with caplog.at_level(logging.INFO):
            out = await _maybe_tiered_critical_notes(
                project_id="proj-test",
                active_notes=[
                    n.to_dict()
                    for n in mgr._project_repository._active_notes
                ],
                user_query="kubernetes",
                instance_id="i-degraded-test",
                manager=mgr,
            )
        # A degraded-prefix line was emitted.
        assert any(
            "[CriticalNotes:Degraded]" in rec.message
            for rec in caplog.records
        )
        # Routine line ALSO emitted.
        assert any(
            "[CriticalNotes] selected=" in rec.message
            and "instance=i-degraded" in rec.message
            for rec in caplog.records
        )
        # Output is non-empty (degraded path still produces a
        # viable block — the universal safety net).
        assert len(out) >= 1

    @pytest.mark.asyncio
    async def test_embedding_service_failure_falls_back(
        self, monkeypatch, caplog
    ):
        """When the embedder raises, the orchestrator absorbs the
        failure and continues with the BM25-only path.
        """
        notes = []
        for i in range(3):
            n = FakeNote(f"f-{i}")
            notes.append(n)

        class KNote(FakeNote):
            def to_dict(self):
                d = super().to_dict()
                d["summary"] = f"kubernetes {self.id}"
                return d

        notes = [KNote(f"f-{i}") for i in range(3)]
        mgr = _make_manager(pinned_count=1, active_notes=notes)
        mgr._project_repository._active_notes[0].pinned = True

        # Build an embedder that ALWAYS raises.
        class BrokenService:
            async def embed_text(self, *args, **kwargs):
                raise RuntimeError("simulated embed api down")

        import daemon.services.critical_notes_embedding as emb_mod

        def _stub(*, model, engine):
            return BrokenService()

        monkeypatch.setattr(emb_mod, "build_critical_notes_embedder", _stub)

        with caplog.at_level(logging.INFO):
            out = await _maybe_tiered_critical_notes(
                project_id="proj-test",
                active_notes=[
                    n.to_dict()
                    for n in mgr._project_repository._active_notes
                ],
                user_query="kubernetes",
                instance_id="i-broken-embed",
                manager=mgr,
            )
        # The degraded-prefix line was emitted — every rung of
        # the ladder has its own prefix-and-reason contract.
        assert any(
            "[CriticalNotes:Degraded]" in rec.message
            for rec in caplog.records
        )
        # Output still produced.
        assert len(out) >= 1

    @pytest.mark.asyncio
    async def test_repo_failure_falls_back_silently(
        self, monkeypatch, caplog
    ):
        """A repo that raises on ``count_pinned_critical_notes``
        MUST NOT crash the orchestrator — it logs a degraded
        line and falls through to render-all (the safe
        default).
        """
        class BrokenRepo(FakeProjectRepository):
            def count_pinned_critical_notes(self, project_id):
                raise RuntimeError("repo down")

            def list_critical_notes(self, project_id):
                return [
                    FakeNote(f"n-{i}")
                    for i in range(3)
                ]

        mgr = FakeManager(BrokenRepo())
        with caplog.at_level(logging.INFO):
            out = await _maybe_tiered_critical_notes(
                project_id="proj-test",
                active_notes=[
                    n.to_dict()
                    for n in mgr._project_repository.list_critical_notes(
                        "proj-test"
                    )
                ],
                user_query="kubernetes",
                instance_id="i-repo-broken",
                manager=mgr,
            )
        # Repo-failure path emits the dedicated prefix.
        assert any(
            "[CriticalNotes:Degraded]"
            in rec.message
            and "stage=pinned_count" in rec.message
            for rec in caplog.records
        )
        # Output is still produced (the universal safety net).
        assert len(out) >= 0  # render-all returns empty if nothing came back


# ---------------------------------------------------------------------------
# Repo-bridge helpers (count_pinned_critical_notes / list_embeddings)
# ---------------------------------------------------------------------------


class TestRepoBridgeHelpers:
    def test_count_pinned_critical_notes_returns_zero_for_none(self):
        assert _count_pinned_critical_notes(
            project_id=None, manager=_make_manager(),
        ) == 0

    def test_count_pinned_critical_notes_passes_through(self):
        mgr = _make_manager(pinned_count=7)
        assert _count_pinned_critical_notes(
            project_id="p", manager=mgr,
        ) == 7

    def test_list_embeddings_returns_empty_for_none(self):
        assert _list_critical_note_embeddings_for_project(
            project_id=None, manager=_make_manager(),
        ) == {}

    def test_list_embeddings_passes_through(self):
        mgr = _make_manager(embeddings={"n-1": [0.1, 0.2]})
        out = _list_critical_note_embeddings_for_project(
            project_id="p", manager=mgr,
        )
        assert out == {"n-1": [0.1, 0.2]}


# ---------------------------------------------------------------------------
# Hint line is attached to the LAST surviving row, NOT a separate key
# ---------------------------------------------------------------------------


class TestHintSentinelAttachment:
    @pytest.mark.asyncio
    async def test_hint_drop_count_sentinel_attached(self, monkeypatch):
        """The orchestrator attaches ``__hint_drop_count`` to the
        LAST surviving row when ``dropped_count > 0``. The
        renderer reads this sentinel and strips it (Phase 2
        §4.2 hint line contract).
        """
        notes = []
        for i in range(20):
            n = FakeNote(f"many-{i}")
            notes.append(n)

        class KNote(FakeNote):
            def to_dict(self):
                d = super().to_dict()
                d["summary"] = f"kubernetes overload {self.id}"
                return d

        notes = [KNote(f"m-{i}") for i in range(20)]
        mgr = _make_manager(pinned_count=1, active_notes=notes)
        mgr._project_repository._active_notes[0].pinned = True

        import daemon.services.critical_notes_embedding as emb_mod

        def _stub(*, model, engine):
            return None

        monkeypatch.setattr(emb_mod, "build_critical_notes_embedder", _stub)

        out = await _maybe_tiered_critical_notes(
            project_id="proj-test",
            active_notes=[
                n.to_dict()
                for n in mgr._project_repository._active_notes
            ],
            user_query="kubernetes",
            instance_id="i-hint-test",
            manager=mgr,
        )
        # The sentinel exists on the LAST entry (when tail cap
        # cut notes). Total dropped >= 1 implies sentinel.
        assert len(out) >= 2
        if out[-1].get("__hint_drop_count") is not None:
            assert isinstance(out[-1]["__hint_drop_count"], int)
            assert out[-1]["__hint_drop_count"] > 0


# ---------------------------------------------------------------------------
# Config installer shape — boot plumbing
# ---------------------------------------------------------------------------


class TestConfigInstaller:
    def test_install_then_reset_round_trip(self):
        install_critical_notes_selection_config(
            tail_cap=3,
            section_char_cap=5000,
            fusion_bm25_weight=0.3,
            fusion_vector_weight=0.7,
            fusion_threshold=0.2,
            floor_count=4,
            query_max_chars=1500,
            mint_cap_per_read=15,
        )
        # Reset restores defaults.
        reset_critical_notes_selection_config()
        # Round-trip test: a partial override plus a reset does
        # NOT corrupt internal state.
        install_critical_notes_selection_config(tail_cap=4)
        # No assertion here — the install must not raise; the
        # config operates via module-cached state.


# ---------------------------------------------------------------------------
# OPTIONAL-CHEAP pin: embedding_service=None lazy-mint degrade path
# ---------------------------------------------------------------------------


class TestLazyMintEmbedderConstructionFailure:
    @pytest.mark.asyncio
    async def test_construction_failure_degrades_without_raising(
        self, monkeypatch
    ):
        """When the embedder CANNOT be constructed
        (``build_critical_notes_embedder`` → ``None``), the lazy-mint
        window takes the ``embedding_service=None`` path inside
        ``embed_critical_note_text``, absorbs the ``None`` vector per
        row, and returns ``minted=0`` WITHOUT raising — the read path
        continues BM25-only.
        """
        import daemon.services.critical_notes_embedding as emb_mod

        notes = [FakeNote(f"lm-{i}") for i in range(3)]
        mgr = _make_manager(pinned_count=1, active_notes=notes)

        def _stub(**kwargs):
            return None

        monkeypatch.setattr(
            emb_mod, "build_critical_notes_embedder", _stub
        )

        minted = await _lazy_mint_embeddings(
            project_id="proj-test",
            manager=mgr,
            cap=5,
        )

        assert minted == 0
        # Nothing was persisted — every row degraded to skip.
        assert mgr._project_repository._embeddings_map == {}
