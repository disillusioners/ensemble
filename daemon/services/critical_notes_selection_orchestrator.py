"""Critical-notes selection orchestrator (Phase 2, critical-notes-retrieval).

The orchestrator sits between :func:`assemble_context_messages` (the
context-builder seam) and :func:`select_critical_notes_for_injection`
(the pure function in ``critical_notes_selector.py``). It owns:

* the per-instance query-embedding cache (architect §9 — cache is
  per-instance, one call per first turn, never per-turn, never shared
  across children);
* the pinned-count gating (architect §4.2 [#8] — when
  ``pinned_count >= 1`` the tiered path activates; otherwise the
  legacy render-all shape is preserved);
* the lazy-mint cap window (architect §5.4 [#9] — capped at
  ``mint_cap_per_read`` mints per first-turn read);
* the routine telemetry line (``[CriticalNotes] selected=N total=M
  floor_applied=bool instance=X``) vs the degradation prefix
  (``[CriticalNotes:Degraded]``).

Every step is best-effort: the orchestrator MUST NOT block the
build on any embed / mint / selection failure — the degradation
ladder absorbs each failure mode independently.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from daemon.services.critical_notes_selector import (
    SelectionConfig,
    select_critical_notes_for_injection,
)


logger = logging.getLogger(__name__)


# ── Per-instance query-embedding cache (architect §9) ─────────────────────────
#
# Scope: a single dict keyed by instance_id, holding the cached
# query embedding for that instance. The cache is reset on daemon
# restart (module reload) which is the natural lifecycle for a
# first-turn-frozen block. Per-children never share; each child's
# first turn mints its OWN entry. The messaging path's
# ``project_already_injected`` gate ensures we hit this code path
# exactly once per instance — there's no per-turn re-issue to
# worry about.
_query_embedding_cache: dict[str, list[float]] = {}


def reset_query_embedding_cache() -> None:
    """Clear the per-instance query-embedding cache (test isolation helper)."""
    _query_embedding_cache.clear()


def _get_cached_query_embedding(instance_id: str) -> list[float] | None:
    """Return the cached query embedding for ``instance_id`` or ``None``."""
    return _query_embedding_cache.get(instance_id)


def _set_cached_query_embedding(instance_id: str, vector: list[float]) -> None:
    """Cache the query embedding for ``instance_id`` (replace any prior entry)."""
    _query_embedding_cache[instance_id] = list(vector)


# ── Config plumbing (D4: config-layer tuning, no env vars) ────────────────────
#
# Boot installs the values via :func:`install_critical_notes_selection_config`;
# the orchestrator reads them through module-level getters that fall
# back to the documented defaults when the install hasn't run
# (unit tests, direct construction). Same shape as the tool-side
# ``_CORE_CAP / _REFERENCE_MAX / _STALE_DAYS`` install pattern.

_DEFAULT_SELECTION_CONFIG = SelectionConfig()


_cached_selection_config: SelectionConfig = _DEFAULT_SELECTION_CONFIG


def install_critical_notes_selection_config(
    *,
    tail_cap: int | None = None,
    section_char_cap: int | None = None,
    fusion_bm25_weight: float | None = None,
    fusion_vector_weight: float | None = None,
    fusion_threshold: float | None = None,
    floor_count: int | None = None,
    query_max_chars: int | None = None,
    mint_cap_per_read: int | None = None,
) -> None:
    """Install boot-resolved selection knobs (called once from ``load_config``).

    ``None`` leaves the current value untouched, so a partial yaml
    block falls back to the documented defaults above rather than
    resetting to them. Mirrors the
    :func:`daemon.tools.critical_notes.install_critical_notes_config`
    pattern. The Phase-1 ``reference_max`` / ``core_cap`` /
    ``stale_days`` knobs are NOT touched here — those live on the
    tool-side install (the render and selection paths consume the
    same bound for ``reference`` independently).
    """
    global _cached_selection_config
    updates = {}
    if tail_cap is not None:
        updates["tail_cap"] = int(tail_cap)
    if section_char_cap is not None:
        updates["section_char_cap"] = int(section_char_cap)
    if fusion_bm25_weight is not None:
        updates["fusion_bm25_weight"] = float(fusion_bm25_weight)
    if fusion_vector_weight is not None:
        updates["fusion_vector_weight"] = float(fusion_vector_weight)
    if fusion_threshold is not None:
        updates["fusion_threshold"] = float(fusion_threshold)
    if floor_count is not None:
        updates["floor_count"] = int(floor_count)
    if query_max_chars is not None:
        updates["query_max_chars"] = int(query_max_chars)
    if mint_cap_per_read is not None:
        # ``mint_cap_per_read`` lives in the SAME install surface so
        # operators get a single config block; we stash it in a
        # separate module-level slot because the SelectionConfig
        # dataclass is a per-call instance.
        globals()["_cached_mint_cap_per_read"] = int(mint_cap_per_read)
    if updates:
        _cached_selection_config = _cached_selection_config.with_overrides(
            **updates
        )


def reset_critical_notes_selection_config() -> None:
    """Restore documented defaults (test isolation helper)."""
    global _cached_selection_config
    _cached_selection_config = _DEFAULT_SELECTION_CONFIG
    globals()["_cached_mint_cap_per_read"] = _DEFAULT_MINT_CAP_PER_READ


_DEFAULT_MINT_CAP_PER_READ = 10
_cached_mint_cap_per_read: int = _DEFAULT_MINT_CAP_PER_READ


def _resolve_mint_cap_per_read() -> int:
    """Return the active lazy-mint cap (process-wide; falls back to default)."""
    return _cached_mint_cap_per_read


# ── Main orchestration entry point ─────────────────────────────────────────────


async def _maybe_tiered_critical_notes(
    *,
    project_id: str | None,
    active_notes: list[dict],
    user_query: str,
    instance_id: str | None,
    manager: Any,
) -> list[dict]:
    """Apply the Phase-2 tiered selection (or the legacy render-all fallback).

    Gating (architect §4.2 [#8]): when the project's
    ``pinned_count >= 1``, tiered selection activates. Otherwise the
    legacy render-all shape is preserved byte-identically — the
    pre-Phase-2 contract for projects that don't pin notes stays
    intact, no behavior change for any current project that has
    never been tiered-curated.

    Best-effort: every embed / mint / fail mode absorbs into the
    BM25-only path; the legacy render-all is the universal
    safety net (when gating is OFF OR the selector raises).

    Args:
        project_id: The owning project id (``None`` for system-
            default trees, but the caller already routes those
            to the scope guide branch so this path is only
            entered for real projects).
        active_notes: The pre-fetched list of critical-note dicts
            (already R21-filtered to active-only by the
            orchestrator's R21 entry-gate plumbing).
        user_query: The first-turn user message text. Empty /
            whitespace-only ⇒ renderer falls through to core +
            floor (no fusion).
        instance_id: Owning instance id. Required for the
            per-instance query-embedding cache; ``None`` ⇒ the
            selector runs without a cached vector (BM25-only).
        manager: The :class:`InstanceManager` exposing
            ``self._project_repository`` (duck-typed).

    Returns:
        A flat list of selected note dicts, in render order
        (pinned first by R19 priority sort, then tail in fusion
        score order, then the sentinel hint line encoded as a
        trailing dict — see :func:`_format_critical_notes_section`
        for the drop-count consumption contract).

    Failure modes

    * ``pinned_count == 0`` → render-all (legacy shape) —
      bypasses selection entirely, returns ``active_notes`` as-is.
    * Selector raises → :func:`_emit_critical_notes_log` writes a
      degraded-prefix WARNING, then render-all is returned.
    * Vector stage unavailable → BM25-only rank, floor picks up
      under-selected tail; routine line logs the skip.
    * Lazy mint fails → BM25-only path still runs; ops-watch
      ``[CriticalNotes:Degraded] stage=lazy_mint reason=...``.
    """
    if not active_notes:
        return []

    # ── Gate: pinned_count >= 1 activates tiered; else render-all ────
    pinned_count = await asyncio.to_thread(
        _count_pinned_critical_notes,
        project_id=project_id,
        manager=manager,
    )
    if pinned_count <= 0:
        # Fall back to the legacy shape byte-identically (no
        # selection, no hint line, no fusion work). This is the
        # critical case for ops visibility — the gate kept
        # ``[CriticalNotes]`` quiet on a render-all day.
        logger.info(
            "[CriticalNotes] tiered=gated_off total=%d pinned_count=%d "
            "instance=%s (render-all fallback per §4.2 #8 — no pin to "
            "anchor the core tier).",
            len(active_notes),
            pinned_count,
            (instance_id or "")[:12],
        )
        return active_notes

    # ── Tiered path: build embeddings map + cached query embedding ───
    embeddings_map = await asyncio.to_thread(
        _list_critical_note_embeddings_for_project,
        project_id=project_id,
        manager=manager,
    )

    # Lazy mint cap window (architect §5.4 [#9]): refill up to
    # ``mint_cap_per_read`` embeddings ONCE per first turn so legacy
    # pre-Phase-2 rows catch up to the lazy-mint regime. Failures
    # are absorbed into the BM25-only path; the cap is hard.
    try:
        await _lazy_mint_embeddings(
            project_id=project_id,
            manager=manager,
            cap=_resolve_mint_cap_per_read(),
        )
        # After the lazy mint, refresh the map so the selector
        # sees any newly-minted rows.
        embeddings_map = await asyncio.to_thread(
            _list_critical_note_embeddings_for_project,
            project_id=project_id,
            manager=manager,
        )
    except Exception as e:
        logger.info(
            "[CriticalNotes:Degraded] stage=lazy_mint reason=runner_exc "
            "err=%s",
            type(e).__name__,
        )

    # Query embedding (cached per instance, never per-turn). The
    # first call for an instance populates the cache; subsequent
    # calls (for retry / re-entry scenarios) re-use the cached
    # value so the embed latency cost is bounded.
    cached_query_embed: list[float] | None = None
    if instance_id is not None:
        cached_query_embed = _get_cached_query_embedding(instance_id)
    if cached_query_embed is None and instance_id is not None:
        cached_query_embed = await _embed_query(user_query, manager)
        if cached_query_embed is not None:
            _set_cached_query_embedding(instance_id, cached_query_embed)
        elif user_query.strip():
            logger.info(
                "[CriticalNotes:Degraded] stage=query_embed reason=unavailable "
                "instance=%s — falling back to BM25-only rank.",
                (instance_id or "")[:12],
            )

    # ── Run the selector (pure; isolated for unit tests) ───────────────
    try:
        result = select_critical_notes_for_injection(
            active_notes=active_notes,
            query=user_query,
            embeddings_map=embeddings_map,
            query_embedding=cached_query_embed,
            config=_cached_selection_config,
            instance_id=instance_id,
        )
    except Exception as e:
        # Hard failure — degrade to render-all (the universal
        # safety net). Phase 2's rollback story is byte-identical
        # pre-Phase-2 render when the selector raises.
        logger.info(
            "[CriticalNotes:Degraded] stage=select reason=runner_exc "
            "instance=%s err=%s — falling back to render-all.",
            (instance_id or "")[:12],
            type(e).__name__,
        )
        return active_notes

    # ── Routine telemetry ──────────────────────────────────────────────
    _emit_critical_notes_log(result.telemetry)
    _emit_critical_notes_hint(result.dropped_count)

    # ── Compose output: pinned + tail (flat order for the renderer) ───
    output: list[dict] = list(result.pinned) + list(result.tail)

    # The renderer reads a ``__hint_drop_count`` sentinel on the last
    # entry to emit the canonical hint line. We attach it to the
    # final tail entry when dropped_count > 0; if tail is empty
    # (everything was rendered-all and hit the cap), the hint
    # still surfaces — attach to the last pinned entry.
    if result.dropped_count > 0 and output:
        output[-1]["__hint_drop_count"] = result.dropped_count

    return output


def _emit_critical_notes_log(telemetry: dict[str, Any]) -> None:
    """Emit the routine ``[CriticalNotes]`` INFO line per §4.6.

    Separate from the :attr:`Degraded` prefix so ops can alert on
    the prefix alone and grep the routine line for the
    selected / total / floor counters in clean-window reviews.
    """
    selected = telemetry.get("selected", 0)
    total = telemetry.get("total", 0)
    floor_applied = telemetry.get("floor_applied", False)
    instance = telemetry.get("instance_id", "")
    logger.info(
        "[CriticalNotes] selected=%d total=%d floor_applied=%s instance=%s",
        selected,
        total,
        floor_applied,
        instance,
    )


def _emit_critical_notes_hint(dropped: int) -> None:
    """Emit the routine hint line as a separate INFO when notes are dropped.

    The hint line in the rendered block is the operator-visible
    cue; this log line is the observability counterpart for
    grep-friendly triage (e.g. ``grep "[CriticalNotes] hint"``).
    """
    if dropped > 0:
        logger.info(
            "[CriticalNotes] hint_drop_count=%d (use project_cn_list "
            "for full view)",
            dropped,
        )


# ─── Manager / repo bridge helpers (kept module-private) ─────────────────────


def _count_pinned_critical_notes(
    *, project_id: str | None, manager: Any
) -> int:
    """Count PINNED + ACTIVE notes for the project (core-tier size).

    Reads ``manager._project_repository.count_pinned_critical_notes``
    if available; falls back to a manual scan (best-effort,
    return -1 on failure) so a broken repo doesn't crash the
    orchestrator.
    """
    if not project_id:
        return 0
    repo = getattr(manager, "_project_repository", None)
    if repo is None:
        return 0
    try:
        if hasattr(repo, "count_pinned_critical_notes"):
            return int(repo.count_pinned_critical_notes(project_id))
        # Manual fallback if the helper isn't yet wired.
        if hasattr(repo, "list_critical_notes"):
            notes = list(repo.list_critical_notes(project_id))
            return sum(
                1 for n in notes
                if getattr(n, "pinned", False)
                and getattr(n, "superseded_by_id", None) is None
            )
        return 0
    except Exception as e:
        logger.info(
            "[CriticalNotes:Degraded] stage=pinned_count reason=repo_failure "
            "err=%s",
            type(e).__name__,
        )
        return 0


def _list_critical_note_embeddings_for_project(
    *, project_id: str | None, manager: Any
) -> dict[str, list[float]]:
    """Read the per-project embedding map via the repo bridge.

    Returns an empty mapping on any failure so the selector
    falls back to BM25-only rank for that round.
    """
    if not project_id:
        return {}
    repo = getattr(manager, "_project_repository", None)
    if repo is None or not hasattr(
        repo, "list_critical_note_embeddings_for_project"
    ):
        return {}
    try:
        return dict(repo.list_critical_note_embeddings_for_project(project_id))
    except Exception as e:
        logger.info(
            "[CriticalNotes:Degraded] stage=embed_map reason=repo_failure "
            "err=%s",
            type(e).__name__,
        )
        return {}


async def _embed_query(
    user_query: str, manager: Any
) -> list[float] | None:
    """Embed the (already truncated) first-turn user query.

    Returns ``None`` on any failure so the caller falls through
    to BM25-only rank. The embedder is built off the project's
    engine when possible; the recipe is identical to the
    write-time path in
    :mod:`daemon.services.critical_notes_embedding`.
    """
    if not user_query or not user_query.strip():
        return None

    try:
        from daemon.services.critical_notes_embedding import (
            build_critical_notes_embedder,
            embed_critical_note_text,
        )
    except Exception as e:
        logger.info(
            "[CriticalNotes:Degraded] stage=query_embed reason=service_unavailable "
            "err=%s",
            type(e).__name__,
        )
        return None

    engine = None
    try:
        repo = getattr(manager, "_project_repository", None)
        engine = getattr(repo, "engine", None)
    except Exception:
        engine = None

    service = build_critical_notes_embedder(
        model="text-embedding-3-small",
        engine=engine,
    )
    if service is None:
        return None

    try:
        vector = await embed_critical_note_text(
            user_query, embedding_service=service,
        )
    except Exception as e:
        logger.info(
            "[CriticalNotes:Degraded] stage=query_embed reason=api_failure "
            "err=%s",
            type(e).__name__,
        )
        return None
    return vector


async def _lazy_mint_embeddings(
    *,
    project_id: str | None,
    manager: Any,
    cap: int,
) -> int:
    """Lazy mint up to ``cap`` embeddings for legacy notes that lack a row.

    Failures absorb silently per architect §5.4 [#9] — the cap is
    a SAFETY bound; the operator may run the explicit backfill
    command later for unbounded coverage. Returns the number of
    rows actually minted (useful in tests; logged under the
    :attr:`Degraded` prefix if a mint fails).
    """
    if not project_id or cap <= 0:
        return 0
    repo = getattr(manager, "_project_repository", None)
    if repo is None or not hasattr(
        repo, "list_critical_note_ids_needing_embedding"
    ):
        return 0

    try:
        ids = list(
            repo.list_critical_note_ids_needing_embedding(
                project_id, limit=cap,
            )
        )
    except Exception as e:
        logger.info(
            "[CriticalNotes:Degraded] stage=lazy_mint reason=list_failed "
            "err=%s",
            type(e).__name__,
        )
        return 0

    if not ids:
        return 0

    minted = 0
    for note_id in ids:
        try:
            note_dict = await asyncio.to_thread(
                _fetch_note_for_lazy_mint,
                project_id=project_id,
                note_id=note_id,
                repo=repo,
            )
            if note_dict is None:
                continue
            from daemon.services.critical_notes_embedding import (
                embed_critical_note_text,
                make_embed_input,
            )

            text = make_embed_input(
                note_dict.get("summary", "") or "",
                note_dict.get("reference"),
            )
            if not text:
                continue
            vector = await embed_critical_note_text(text)
            if not vector:
                continue
            await asyncio.to_thread(
                repo.set_critical_note_embedding,
                note_id,
                vector,
                "text-embedding-3-small",
                len(vector),
            )
            minted += 1
        except Exception as e:
            logger.info(
                "[CriticalNotes:Degraded] stage=lazy_mint reason=row_exc "
                "note_id=%s err=%s",
                note_id,
                type(e).__name__,
            )
    return minted


def _fetch_note_for_lazy_mint(
    *, project_id: str, note_id: str, repo: Any
) -> dict | None:
    """Best-effort fetch of a note row for the lazy-mint input builder."""
    try:
        note = repo.get_critical_note(project_id, note_id)
    except Exception:
        return None
    if note is None:
        return None
    try:
        return note.to_dict()
    except Exception:
        return None
