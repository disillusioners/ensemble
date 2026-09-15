"""Critical-notes tiered selection (Phase 2, critical-notes-retrieval).

The compute-fusion selection pipeline per architecture-recommendation
§4.2. Operates on ACTIVE-only rows (R21 entry gate is enforced at the
caller surface — this module assumes ``superseded_by_id is None`` for
every row it sees).

Stage layout (orchestrated by :func:`select_critical_notes_for_injection`):

1. **Query shaping** — truncate the user query to ``query_max_chars``;
   empty / whitespace-only → skip ranking, return core + floor only.
2. **BM25 prefilter** — top-10 by raw BM25 score against each note's
   ``summary + reference`` doc (reusing
   :func:`daemon.services.skill_search_service._bm25_score`, ID/avg-doc-len
   state computed fresh per call so the signature / IDF safety
   invariant from §9 holds).
3. **Vector re-rank (best-effort)** — embed the user query (cached
   per instance by the orchestrator); cosine-similarity each note's
   stored vector; clamp to ``[0, 1]``.
4. **Fusion** — ``final = bm25_weight * bm25_norm + vector_weight * cosine``;
   threshold gate at ``fusion_threshold``.
5. **Tail + floor** — top-``tail_cap`` (default 6) survivors by fused
   score, then ``floor_count`` (default 2) priority-floor (priority
   rank desc, then recency desc) for under-selection.
6. **Char-budget + hint** — pack the SURVIVING notes into the
   ``section_char_cap`` total; if more candidates were DROPPED than
   fit, append the canonical hint line.

Pinned rows (D2, core tier) ALWAYS load first — they are not part
of the selection competition. They bypass BM25/vector entirely and
render in the priority-sorted injected block per R19.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from daemon.services.skill_search_service import (
    _bm25_score,
    _tokenize,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration (bound from CriticalNotesConfig at install time)
# ---------------------------------------------------------------------------


@dataclass
class SelectionConfig:
    """Bound copy of :class:`daemon.config.CriticalNotesConfig` knobs.

    Decouples the pure selection function from the global pydantic
    object so unit tests can drive the pipeline with explicit
    boundaries without touching the process-wide install side
    effect. Lifecycle: this dataclass is built PER-CALL by the
    orchestrator from the resolved config; no mutable state.
    """

    tail_cap: int = 6
    section_char_cap: int = 12000
    fusion_bm25_weight: float = 0.4
    fusion_vector_weight: float = 0.6
    fusion_threshold: float = 0.30
    floor_count: int = 2
    query_max_chars: int = 2000
    # Phase-3 reserved knob included for forward-compat (the
    # selector MUST never read it; ``llm_select=False`` keeps the
    # C2 stage disengaged. Documented presence for grep-ability).
    llm_select: bool = False

    def with_overrides(self, **overrides: Any) -> "SelectionConfig":
        """Return a shallow-copied config with the given fields overridden.

        Used by tests to drive boundary conditions without mutating
        the instance — preserves the per-call dataclass discipline.
        """
        kwargs = {f.name: getattr(self, f.name) for f in self.__dataclass_fields__.values()}
        for k, v in overrides.items():
            if k in kwargs:
                kwargs[k] = v
        return SelectionConfig(**kwargs)


@dataclass
class SelectionResult:
    """Structured output of :func:`select_critical_notes_for_injection`.

    Carries the ordered notes (pinned first, then tail order), the
    dropped count driving the hint line, and the routing telemetry
    meta for the ``[CriticalNotes]`` routine log line. Pure data;
    no render logic.

    Attributes:
        pinned: Pinned rows (already priority-ordered by the
            caller-passed ordering — Phase-2 keeps Phase-1's R19
            ordering for this tier unchanged).
        tail: Tail rows (fusion-ranked; ordering matters for
            render — first row is the highest-confidence pick).
        dropped_count: Number of active notes NOT in the final
            ``pinned + tail`` set. Drives the canonical hint line
            ``(N additional notes not shown — use project_cn_list
            for the full view)``.
        truncated_for_char_cap: Set when notes were dropped
            specifically because of the ``section_char_cap`` (NOT
            the threshold / selection stage). Operators triage this
            vs the under-selection ladder rung.
        telemetry: Dict of strings (no secret values) for the
            routine ``[CriticalNotes] selected=N total=M
            floor_applied=bool instance=X`` INFO line.
    """

    pinned: list[dict] = field(default_factory=list)
    tail: list[dict] = field(default_factory=list)
    dropped_count: int = 0
    truncated_for_char_cap: bool = False
    telemetry: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Priority tier ordering (R19 — injected block only)
# ---------------------------------------------------------------------------


_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2}


def _priority_key(note: dict) -> int:
    """Stable ordering key: priority tier (critical→high→medium), unknown last.

    A missing / unknown priority sorts last within its group — pure
    R19 inherited behavior. Compatible with the
    ``_order_critical_notes_for_injection`` Phase-1 helper.
    """
    return _PRIORITY_RANK.get(note.get("priority", ""), 3)


# ---------------------------------------------------------------------------
# Internal: note → BM25 doc
# ---------------------------------------------------------------------------


def _build_bm25_doc(note: dict) -> str:
    """Tokenize a note into a single BM25 doc string.

    Shape: ``category summary reference`` joined by single space.
    The ``category`` and ``summary`` are the priority-bearing
    semantic payload (any token match here boosts the note
    meaningfully); ``reference`` is the bounded reference text
    (already ≤500 chars, never expanded into detail_ref).
    """
    parts: list[str] = []
    cat = note.get("category")
    if isinstance(cat, str) and cat.strip():
        parts.append(cat.strip())
    summary = note.get("summary")
    if isinstance(summary, str) and summary.strip():
        parts.append(summary.strip())
    ref = note.get("reference")
    if isinstance(ref, str) and ref.strip():
        parts.append(ref.strip())
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Internal: per-call BM25 corpus stats — proves §9 (a) per-call safety
# ---------------------------------------------------------------------------


def _bm25_corpus_stats(
    notes: list[dict],
    query_tokens: list[str],
) -> tuple[dict[str, int], int, float]:
    """Compute the per-call BM25 corpus stats.

    Per-call state discipline (architecture-recommendation §9):
    ``doc_freqs`` / ``total_docs`` / ``avg_doc_len`` are all built
    FRESH per call from the ``notes`` arg — there is NO module-
    level IDF cache. ``_bm25_score`` is stateless with respect to
    its arguments (it only reads the parameters we pass in), so
    concurrent calls would not race; the discipline documented
    here is the SOURCE CODE invariant — every per-call entry point
    builds a fresh stats trio.

    Args:
        notes: The candidate notes the corpus covers (typically
            the active non-pinned non-floor tail pool). The
            ``pinned`` tier is scored as-is from BM25 also —
            selection ONLY gates the tail set; pinning is a
            curator's choice and bypasses fusion (D2).
        query_tokens: Pre-tokenized query terms.

    Returns:
        ``(doc_freqs, total_docs, avg_doc_len)`` for downstream
        BM25 scoring. ``doc_freqs`` is keyed by token string.
    """
    if not notes:
        return ({}, 0, 1.0)

    df: dict[str, int] = {}
    total_tokens = 0
    for note in notes:
        tokens = _tokenize(_build_bm25_doc(note))
        total_tokens += len(tokens)
        # Document frequency: the set of unique tokens IN the
        # query that this document carries (cheap short-circuit
        # — the full token set isn't needed for the BM25 score,
        # only the intersection with the query).
        unique_terms = set(tokens)
        for term in query_tokens:
            if term in unique_terms:
                df[term] = df.get(term, 0) + 1

    n_docs = len(notes)
    avg_doc_len = total_tokens / n_docs if n_docs else 1.0
    return (df, n_docs, avg_doc_len)


# ---------------------------------------------------------------------------
# Internal: cosine similarity re-rank
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity, clipped to ``[0, 1]``.

    Mirrors :meth:`SkillEmbeddingService.cosine_similarity` so the
    two code paths cannot drift; imports avoided here to keep
    this module dependency-light. Returns ``0.0`` for any zero-
    vector input (avoids ``ZeroDivisionError``).

    Vector cosine symmetry is intentional — fusion already
    symmetrizes via the per-component weights. The result is
    clipped to ``[0, 1]`` because BM25 covers "negative"
    dissimilarity through its score threshold, and the fused
    score is a probability-ish accumulator bounded at 1.0 by
    construction.
    """
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    sim = dot / (norm_a * norm_b)
    return max(0.0, min(1.0, sim))


# ---------------------------------------------------------------------------
# Main selector
# ---------------------------------------------------------------------------


def select_critical_notes_for_injection(
    active_notes: list[dict],
    query: str,
    embeddings_map: dict[str, list[float]],
    query_embedding: list[float] | None,
    *,
    config: SelectionConfig | None = None,
    instance_id: str | None = None,
) -> SelectionResult:
    """Compute the tiered selection for the first-turn injected block.

    Args:
        active_notes: All ACTIVE notes for the project (already
            R21-filtered by the caller). Each entry is the dict
            shape produced by :meth:`CriticalNoteModel.to_dict`.
            Pinned and unpinned rows MAY be mixed here — pinned
            rows bypass selection entirely.
        query: First-turn user message text. Truncated to
            ``config.query_max_chars`` for the BM25 path. Empty
            / whitespace → ``skip_ranking``; the floor is still
            applied so the always-load surface covers a relevant
            baseline.
        embeddings_map: ``{note_id: list[float]}`` from
            :meth:`SQLModelProjectRepository.list_critical_note_embeddings_for_project`.
            Notes missing from this map fall back to BM25-only
            scoring (vector stage skips for that candidate) —
            the lazy-mint pipeline replenishes the map on the
            next first turn.
        query_embedding: Cached query embedding for the instance
            (the orchestrator owns the cache). When ``None`` the
            selector returns BM25-only tail — callers MUST log
            the missing-cached-query case under
            ``[CriticalNotes:Degraded]`` separately.
        config: Effective :class:`SelectionConfig`. Defaults to
            the documented Phase-2 values when ``None``.
        instance_id: Owning instance id (only used for the
            routine telemetry log key — not persisted here).

    Returns:
        :class:`SelectionResult` carrying the ordered
        ``pinned`` + ``tail`` sets, ``dropped_count``, and
        ``telemetry`` dict for the
        ``[CriticalNotes] selected=N total=M floor_applied=bool``
        routine INFO line.

    Notes:
        * ``dropped_count`` excludes the truncated-for-char-cap
          drop events — the hint-line message and operators
          triage those separately (architecture-recommendation
          §4.6 [#10]).
        * The floor is applied AFTER fusion: under-selection is
          defined as "fewer than ``tail_cap`` rows made it past
          the threshold gate"; the floor then fills the gap
          with the highest-priority + most-recent active rows
          not already in the pinned set and not already in
          the tail.
    """
    cfg = config or SelectionConfig()

    # ── Stage 1: shape the query ───────────────────────────────────────────
    query_text = (query or "").strip()
    if len(query_text) > cfg.query_max_chars:
        query_text = query_text[: cfg.query_max_chars]

    # ── Stage 2: split pinned vs unpinned ──────────────────────────────────
    pinned = [n for n in active_notes if n.get("pinned")]
    unpinned = [n for n in active_notes if not n.get("pinned")]

    # Pinned rows keep the R19 ordering (priority→recency), as in
    # Phase 1. The Phase-2 fusion does NOT touch the core tier —
    # core is curated, not ranked.
    pinned_ordered = _order_pinned(pinned)

    # ── Stage 3: BM25 prefilter over unpinned (top-10) ─────────────────────
    skipped_ranking = False
    skipped_query_embed = False
    floor_applied = False

    if not query_text:
        # Empty query → skip the entire ranking stage. Tail = empty,
        # floor picks priority-sorted active rows below.
        bm25_shortlist: list[tuple[float, dict]] = []
        skipped_ranking = True
    else:
        query_tokens = _tokenize(query_text)
        if not query_tokens:
            bm25_shortlist = []
            skipped_ranking = True
        else:
            df, n_docs, avgdl = _bm25_corpus_stats(unpinned, query_tokens)
            scored: list[tuple[float, dict]] = []
            for note in unpinned:
                doc_tokens = _tokenize(_build_bm25_doc(note))
                if not doc_tokens:
                    continue
                raw = _bm25_score(
                    query_tokens=query_tokens,
                    doc_tokens=doc_tokens,
                    doc_freqs=df,
                    total_docs=n_docs,
                    avg_doc_len=avgdl,
                )
                if raw <= 0:
                    continue
                scored.append((raw, note))
            # BM25 shortlist = top-10 by raw score (pre-fusion).
            scored.sort(key=lambda pair: pair[0], reverse=True)
            bm25_shortlist = scored[:10]

    # ── Stage 4: vector re-rank (best-effort) ──────────────────────────────
    if query_embedding is not None and bm25_shortlist:
        try:
            scored_candidates = _fuse_candidates(
                bm25_shortlist=bm25_shortlist,
                embeddings_map=embeddings_map,
                query_embedding=query_embedding,
                cfg=cfg,
            )
        except Exception as e:
            logger.info(
                "[CriticalNotes:Degraded] stage=vector_rank "
                "reason=fuse_failure err=%s",
                type(e).__name__,
            )
            scored_candidates = _bm25_only_fallback(bm25_shortlist, cfg)
            skipped_query_embed = True
    else:
        if bm25_shortlist:
            # No cached query embedding → BM25-only rank.
            scored_candidates = _bm25_only_fallback(bm25_shortlist, cfg)
            if query_text:
                skipped_query_embed = True
        else:
            scored_candidates = []

    # ── Stage 5: threshold gate + tail cap ────────────────────────────────
    tail_after_gate = [
        note for note, score in scored_candidates
        if score >= cfg.fusion_threshold
    ]

    # ── Stage 6: apply floor when under-selected ──────────────────────────
    if len(tail_after_gate) < cfg.tail_cap and unpinned:
        held_ids = {n.get("id") for n in pinned_ordered + tail_after_gate}
        floor_pool = [
            n for n in _order_pinned(unpinned)
            if n.get("id") not in held_ids
        ]
        floor = floor_pool[: cfg.floor_count]
        if floor:
            tail_after_gate = tail_after_gate + floor
            floor_applied = True

    # Top-N by tail_cap (preserve the scored ordering when there
    # were ties — the floor rows are appended in their priority
    # order which is correct).
    tail_ordered = tail_after_gate[: cfg.tail_cap]

    # ── Stage 7: char budget + dropped count ──────────────────────────────
    pinned_chars = sum(_estimate_note_chars(n) for n in pinned_ordered)
    tail_remaining = max(0, cfg.section_char_cap - pinned_chars)
    final_tail, truncated = _enforce_char_cap(tail_ordered, tail_remaining)

    n_active = len(active_notes)
    n_kept = len(pinned_ordered) + len(final_tail)
    dropped_count = max(0, n_active - n_kept)

    telemetry = {
        "selected": n_kept,
        "total": n_active,
        "floor_applied": floor_applied,
        "skipped_ranking": skipped_ranking,
        "skipped_query_embed": skipped_query_embed,
        "instance_id": (instance_id or "")[:12],
    }

    return SelectionResult(
        pinned=pinned_ordered,
        tail=final_tail,
        dropped_count=dropped_count,
        truncated_for_char_cap=truncated,
        telemetry=telemetry,
    )


# ---------------------------------------------------------------------------
# Internal helpers — kept module-private so the public surface
# stays small and testable in isolation.
# ---------------------------------------------------------------------------


def _order_pinned(notes: list[dict]) -> list[dict]:
    """Priority→recency ordering for the injected block (R19).

    Within each priority tier recency DESC (newest first). Unknown
    priorities sort last; unparseable ``created_at`` strings sort
    last within their pass. Pure & stable for sorted-comparable
    inputs.
    """
    pinned = [n for n in notes if n.get("pinned")]
    rest = [n for n in notes if not n.get("pinned")]

    def _recency_desc(seq: list[dict]) -> list[dict]:
        return sorted(seq, key=lambda n: (n.get("created_at") or ""), reverse=True)

    def _priority_then_recency(seq: list[dict]) -> list[dict]:
        return sorted(
            _recency_desc(seq),
            key=lambda n: _PRIORITY_RANK.get(n.get("priority", ""), 3),
        )

    return _priority_then_recency(pinned) + _priority_then_recency(rest)


def _estimate_note_chars(note: dict) -> int:
    """Per-row char estimate used by the budget cap.

    Cost = ``len(summary) + len(reference or "") + 64`` (64 for the
    markdown row envelope — icon, brackets, optional ref suffix,
    newline). The estimate is intentionally loose; the renderer
    adjusts for the actual prefix in :func:`_format_critical_notes_section`.
    """
    return len(note.get("summary", "") or "") + len(
        note.get("reference", "") or ""
    ) + 64


def _enforce_char_cap(
    notes: list[dict],
    budget: int,
) -> tuple[list[dict], bool]:
    """Greedy char-budget packing — keep notes until the budget is exhausted.

    Returns:
        ``(kept, truncated)`` where ``kept`` is the in-budget
        notes in their original order and ``truncated`` is ``True``
        if any incoming note was dropped purely for budget.
    """
    if budget <= 0:
        return ([], bool(notes))
    kept: list[dict] = []
    used = 0
    truncated = False
    for note in notes:
        cost = _estimate_note_chars(note)
        if used + cost > budget:
            truncated = True
            break
        kept.append(note)
        used += cost
    return (kept, truncated)


def _bm25_only_fallback(
    bm25_shortlist: list[tuple[float, dict]],
    cfg: SelectionConfig,
) -> list[tuple[dict, float]]:
    """Normalize BM25-only results when the vector stage is unavailable.

    Min-max normalization mirrors the BlueprintMatcher precedent
    (architecture-recommendation §4.7 fusion-bm25-weight=0.4
    default). Edge cases:

    * single candidate with a non-zero raw score → normalized to 1.0
    * all candidates zero → normalize to 0.0 (thence the
      under-selection floor takes over)
    """
    if not bm25_shortlist:
        return []

    raw_values = [s for s, _ in bm25_shortlist]
    bm25_min = min(raw_values)
    bm25_max = max(raw_values)
    span = bm25_max - bm25_min
    out: list[tuple[dict, float]] = []
    for raw, note in bm25_shortlist:
        if span > 0:
            norm = (raw - bm25_min) / span
        elif raw > 0:
            norm = 1.0
        else:
            norm = 0.0
        # BM25-only fusion: weight the BM25 by ``fusion_bm25_weight``
        # AND the absent vector score by ``fusion_vector_weight`` =
        # 0 (zero norm for missing vectors). The fused score is
        # therefore biased toward the BM25 side.
        fused = cfg.fusion_bm25_weight * norm + 0.0
        out.append((note, fused))
    out.sort(key=lambda pair: pair[1], reverse=True)
    return out


def _fuse_candidates(
    *,
    bm25_shortlist: list[tuple[float, dict]],
    embeddings_map: dict[str, list[float]],
    query_embedding: list[float],
    cfg: SelectionConfig,
) -> list[tuple[dict, float]]:
    """Compute fused scores and return notes sorted descending.

    Mirrors BlueprintMatcher._match_area semantics (architecture-
    recommendation §4.2 first-turn stage):
    ``final = alpha * bm25_norm + beta * vector_score`` where
    ``alpha = cfg.fusion_bm25_weight`` and
    ``beta = cfg.fusion_vector_weight``.

    Notes without a cached embedding map to ``vector_score = 0``
    (their BM25-only fusion is still computed via the same
    formula — falls through to _bm25_only_fallback's equivalent
    product). The vector stage's failure mode is its own concern
    (the BM25-only fallback is the caller-level degradation rung);
    here we treat a missing embed as a zero vector score.
    """
    raw_values = [s for s, _ in bm25_shortlist]
    bm25_min = min(raw_values) if raw_values else 0.0
    bm25_max = max(raw_values) if raw_values else 0.0
    span = bm25_max - bm25_min

    scored: list[tuple[dict, float]] = []
    for raw, note in bm25_shortlist:
        if span > 0:
            bm25_norm = (raw - bm25_min) / span
        elif raw > 0:
            bm25_norm = 1.0
        else:
            bm25_norm = 0.0

        vec_norm = 0.0
        nid = note.get("id")
        if nid and nid in embeddings_map and embeddings_map[nid]:
            vec_norm = _cosine_similarity(
                query_embedding, embeddings_map[nid]
            )

        final = (
            cfg.fusion_bm25_weight * bm25_norm
            + cfg.fusion_vector_weight * vec_norm
        )
        scored.append((note, final))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored
