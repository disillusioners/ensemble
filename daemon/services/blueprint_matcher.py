"""Multi-algorithm blueprint matcher: BM25 + vector fusion + threshold gate.

Phase 1.6 of the Project Blueprint subsystem. Given a task's query
text, this service ranks the project's active blueprints (the
``core.md`` plus zero-or-more ``area`` blueprints) by relevance so
the caller can inject the matched blueprint content into the agent's
context.

The pipeline is a two-stage fusion (deliberately simpler than
:class:`~daemon.services.skill_search_service.SkillSearchService`):

1. **core.md — reserved slot.** The project's ``core`` blueprint is
   **always** included (unconditionally, score ``1.0``) as slot 1,
   regardless of the query. This guarantees the agent always sees the
   project's core conventions.

2. **Area matches — BM25 + vector fusion.** For every ``area``
   blueprint the repository surfaces (paired with its
   :class:`BlueprintTrigger` rows), we compute:

   * a **BM25** keyword score over ``content + trigger queries + name
     + tags`` (reusing the proven ``_bm25_score`` /
     ``_tokenize`` from :mod:`skill_search_service`), min-max
     normalized to ``[0, 1]``;
   * a **vector** score = the **MAX** cosine similarity between the
     query embedding and each trigger's embedding (reusing
     :meth:`SkillEmbeddingService.embed_text` /
     :meth:`SkillEmbeddingService.cosine_similarity`).

   The two are fused with configurable weights
   (``final = alpha*bm25_norm + beta*vector``), threshold-gated at
   ``config.match_threshold`` (default ``0.30``), and capped at
   ``max_results`` (default ``5``).

There is **no LLM re-rank stage** — that is deferred to Phase 6.

Graceful degradation
--------------------

* If the embedding API fails, the vector stage is skipped
  (``query_emb = None``) and ranking falls back to BM25-only with
  every vector contribution set to ``0.0``.
* If there are no area candidates, or the query has no shared terms,
  only the reserved ``core`` slot is returned.

Design notes
------------

* **Reuse, don't reimplement.** BM25 / tokenization / cosine
  similarity are imported from the existing, production-proven skill
  services. No duplicate math lives here.
* **Duck-typed dependencies.** Like ``SkillSearchService``, the
  constructor accepts ``Any`` for the repository and embedding
  service so this module stays unit-testable with lightweight mocks
  and decoupled from the engine factory.
* **Sync repo, async matcher.** ``match`` is ``async`` (it awaits the
  embedding API). The repository is sync, so each repo call is wrapped
  in :func:`asyncio.to_thread` individually — the whole ``match`` is
  never shoved into a thread.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from daemon.services.skill_search_service import _bm25_score, _tokenize
# ^ Reuse the EXACT BM25 + tokenizer already proven in production.
#   Do NOT reimplement them here.

logger = logging.getLogger(__name__)


# ============================================================
# Result dataclass
# ============================================================


@dataclass
class MatchedBlueprint:
    """One blueprint selected by the matcher for injection.

    AUTHORITATIVE fields (per task spec): ``id``, ``name``, ``kind``,
    ``version``, ``content``, ``file_refs``, ``score``. Do NOT use
    ``blueprint_id`` or ``lineage``.

    Attributes:
        id: The blueprint's primary key.
        name: Human-readable blueprint name.
        kind: ``"core"`` for the reserved core slot, ``"area"`` for
            matched area blueprints.
        version: Blueprint revision number.
        content: The markdown body to inject.
        file_refs: File paths referenced by the blueprint (for
            context-loading downstream).
        score: Fusion score (``1.0`` for the always-included core;
            the ``alpha*bm25 + beta*vector`` value for area matches).
    """

    id: str
    name: str
    kind: str
    version: int
    content: str
    file_refs: list[str] = field(default_factory=list)
    score: float = 0.0


# ============================================================
# BlueprintMatcher
# ============================================================


class BlueprintMatcher:
    """Multi-algorithm blueprint matcher: BM25 + vector fusion + threshold gate.

    Architectural parallel of
    :class:`~daemon.services.skill_search_service.SkillSearchService`
    but with TWO differences:

      1. **No LLM re-rank stage** (deferred to Phase 6).
      2. **core.md is ALWAYS included** (reserved slot 1,
         unconditional, score ``1.0``).

    Constructor dependencies are duck-typed (``Any``) so this is
    unit-testable with lightweight mocks, exactly like
    ``SkillSearchService``.

    Args:
        repository: ``BlueprintRepository`` instance (sync). Methods
            used: ``get_core(project_id)``, ``search_candidates(project_id)``.
        embedding_service: ``SkillEmbeddingService`` instance (reused).
            Methods used: ``embed_text`` (async),
            ``cosine_similarity`` (staticmethod).
        config: ``BlueprintConfig`` instance owning
            ``bm25_weight``, ``vector_weight``,
            ``match_threshold``, ``max_results``.
    """

    def __init__(
        self,
        repository: Any,          # BlueprintRepository
        embedding_service: Any,   # SkillEmbeddingService (reused)
        config: Any,              # BlueprintConfig
    ) -> None:
        self._repo = repository
        self._embedding_service = embedding_service
        self._config = config

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    async def match(
        self,
        project_id: str,
        query: str,
        max_results: int = 5,
    ) -> list[MatchedBlueprint]:
        """Rank blueprints for ``query`` and return the injection set.

        The core blueprint is always slot 1 (score ``1.0``); area
        blueprints fill the remaining slots by BM25+vector fusion,
        gated at ``config.match_threshold``. The combined result is
        capped at ``max_results``.

        Args:
            project_id: Project to match within.
            query: The task's query text (v1: only the user query is
                available). May be empty — in that case only the core
                slot is returned.
            max_results: Override for ``config.max_results`` (default
                ``5``). ``core`` always counts as one slot.

        Returns:
            Ordered list of :class:`MatchedBlueprint` (core first),
            length ``<= max_results``.
        """
        t0 = time.perf_counter()

        # Resolve config defaults (max_results param defaults to 5 per
        # signature, but the canonical source is config when callers
        # rely on the default).
        cfg_max = int(getattr(self._config, "max_results", 5) or 5)
        effective_max = max_results if max_results != 5 else cfg_max
        threshold = float(getattr(self._config, "match_threshold", 0.30) or 0.30)
        alpha = float(getattr(self._config, "bm25_weight", 0.4) or 0.4)
        beta = float(getattr(self._config, "vector_weight", 0.6) or 0.6)

        matched: list[MatchedBlueprint] = []
        core_id: str | None = None

        # --- Slot 1: core.md (reserved, always included) -----------
        core = await asyncio.to_thread(self._repo.get_core, project_id)
        if core is not None:
            core_id = core.id
            matched.append(
                MatchedBlueprint(
                    id=core.id,
                    name=core.name,
                    kind="core",
                    version=core.version,
                    content=core.content,
                    file_refs=list(core.file_refs or []),
                    score=1.0,
                )
            )

        # --- Slots 2+: area matches (BM25 + vector fusion) ---------
        candidates = await asyncio.to_thread(
            self._repo.search_candidates, project_id
        )

        if candidates:
            # Deduplicate against core up front so the reserved slot
            # can't also appear as an area match.
            if core_id is not None:
                candidates = [
                    (bp, triggers)
                    for (bp, triggers) in candidates
                    if getattr(bp, "id", None) != core_id
                ]

            max_area = effective_max - len(matched)
            if max_area > 0 and query and query.strip():
                area_matched, _top_scores = await self._match_area(
                    query=query,
                    candidates=candidates,
                    alpha=alpha,
                    beta=beta,
                    threshold=threshold,
                    max_area=max_area,
                )
                matched.extend(area_matched)

        latency_ms = (time.perf_counter() - t0) * 1000.0

        logger.info(
            "blueprint_match",
            extra={
                "project_id": project_id,
                "query_source": "task_only",  # v1: only user_query available
                "query_length": len(query),
                "matched_count": len(matched),
                "matched_ids": [b.id for b in matched[:5]],
                "top_score": matched[0].score if matched else 0.0,
                "latency_ms": round(latency_ms, 2),
            },
        )

        return matched

    # --------------------------------------------------------
    # Stage — BM25 + vector fusion for area candidates
    # --------------------------------------------------------

    async def _match_area(
        self,
        query: str,
        candidates: list[tuple[Any, list[Any]]],
        alpha: float,
        beta: float,
        threshold: float,
        max_area: int,
    ) -> tuple[list[MatchedBlueprint], list[float]]:
        """Score area candidates by BM25 + vector fusion.

        Args:
            query: The task query text.
            candidates: ``[(Blueprint, [BlueprintTrigger, ...]), ...]``
                from ``search_candidates``.
            alpha: BM25 fusion weight.
            beta: Vector fusion weight.
            threshold: Minimum fused score to keep a candidate.
            max_area: Max area matches to return.

        Returns:
            ``(matched, top_scores)`` where ``matched`` is the list
            of surviving :class:`MatchedBlueprint` (length
            ``<= max_area``) and ``top_scores`` is the list of all
            fused scores *before* the threshold cut (for logging).
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return ([], [])

        # --- Build the per-blueprint document for BM25 -------------
        # doc = content + trigger queries + name + tags(flattened)
        tokenized_docs: list[tuple[Any, list[str]]] = []
        for bp, triggers in candidates:
            name = getattr(bp, "name", "") or ""
            content = getattr(bp, "content", "") or ""
            trigger_text = " ".join(
                getattr(t, "query_text", "") or "" for t in (triggers or [])
            )
            # tags is list[dict] with category:value — flatten to values
            tags = getattr(bp, "tags", None) or []
            tags_text = " ".join(
                str(t.get("value", "")) for t in tags if isinstance(t, dict)
            )
            doc_text = f"{content} {trigger_text} {name} {tags_text}"
            tokens = _tokenize(doc_text)
            tokenized_docs.append((bp, tokens))

        # --- Document frequency per query term across the corpus ---
        df: dict[str, int] = {}
        for _, tokens in tokenized_docs:
            unique_terms = set(tokens)
            for term in query_tokens:
                if term in unique_terms:
                    df[term] = df.get(term, 0) + 1

        n_docs = len(tokenized_docs)
        total_tokens = sum(len(toks) for _, toks in tokenized_docs)
        avgdl = total_tokens / n_docs if n_docs else 1.0

        # --- BM25 raw scores --------------------------------------
        bm25_raw: dict[int, float] = {}  # candidate index -> raw bm25
        for idx, (_, tokens) in enumerate(tokenized_docs):
            score = _bm25_score(
                query_tokens=query_tokens,
                doc_tokens=tokens,
                doc_freqs=df,
                total_docs=n_docs,
                avg_doc_len=avgdl,
            )
            bm25_raw[idx] = score

        # --- Vector stage -----------------------------------------
        # query embedding (graceful degradation on failure)
        query_emb: list[float] | None = None
        try:
            query_emb = await self._embedding_service.embed_text(query)
        except Exception as e:  # noqa: BLE001 - intentional broad guard
            logger.warning("blueprint_embed_failed: %s", e)
            query_emb = None

        # --- Min-max normalize BM25 to [0,1] ----------------------
        raw_values = list(bm25_raw.values())
        bm25_min = min(raw_values) if raw_values else 0.0
        bm25_max = max(raw_values) if raw_values else 0.0
        span = bm25_max - bm25_min

        # --- Fuse + collect scored candidates ---------------------
        scored: list[tuple[float, int]] = []  # (final, candidate idx)
        for idx, (bp, triggers) in enumerate(candidates):
            # Normalize BM25 to [0,1].
            bm25_norm = (
                (bm25_raw[idx] - bm25_min) / span
                if span > 0
                else 0.0
            )

            # Vector score = MAX cosine similarity over trigger embeddings.
            vec_score = 0.0
            if query_emb is not None:
                for t in (triggers or []):
                    t_emb = getattr(t, "embedding", None)
                    if not t_emb:
                        continue
                    try:
                        sim = self._embedding_service.cosine_similarity(
                            query_emb, t_emb
                        )
                    except Exception:  # noqa: BLE001 - per-trigger guard
                        continue
                    if sim > vec_score:
                        vec_score = sim
            # Clip vector to [0,1].
            vec_score = max(0.0, min(1.0, vec_score))

            final = alpha * bm25_norm + beta * vec_score
            scored.append((final, idx))

        # top_scores = all fused values before the threshold cut (for logging)
        top_scores = [s for s, _ in scored]

        # Sort descending by final.
        scored.sort(key=lambda pair: pair[0], reverse=True)

        # Threshold gate + cap.
        matched: list[MatchedBlueprint] = []
        for final, idx in scored:
            if final < threshold:
                continue
            bp, _ = candidates[idx]
            matched.append(
                MatchedBlueprint(
                    id=bp.id,
                    name=bp.name,
                    kind="area",
                    version=bp.version,
                    content=bp.content,
                    file_refs=list(bp.file_refs or []),
                    score=round(final, 4),
                )
            )
            if len(matched) >= max_area:
                break

        return (matched, top_scores)
