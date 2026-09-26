"""Snapshot search service (PR5 — agent-snapshot v1).

Three-stage hybrid paralleling
:mod:`daemon.services.skill_search_service`:

1. **BM25 keyword prefilter.** Pure-Python BM25 (k1=1.5, b=0.75
   — no numpy, no external BM25 library, per ``ensemble.spec``)
   over the candidate's ``task_summary`` + ``digest.task_summary_text``
   + ``domain_tags`` corpus. Tag-overlap signal (R10) is folded into
   the score here so candidates whose tags overlap the query-side
   tags surface higher.
2. **Cached-embedding cosine rerank.** Embed the user query via
   :meth:`SnapshotEmbeddingService.embed_text` and cosine the
   result against each candidate's cached
   :class:`~daemon.repositories.snapshot.models.SnapshotEmbedding`
   rows (taking the MAX across triggers — mirrors
   :meth:`SkillEmbeddingService.embed_user_message`).
3. **LLM selection.** A chat-completion call picks the final
   list from the reranked top-20 candidates (hard cap — R10
   cost discipline). Beyond the cap the pipeline degrades
   gracefully to BM25+cosine+tag-overlap order.

Graceful degradation (same shape as the skill side):

* Stage 2 failure (embedding API down or per-candidate error) →
  fall back to BM25+tag-overlap order.
* Stage 3 failure (LLM down or malformed JSON) → fall back to
  the top ``limit`` from stage 2 as the result.
* Stage 1 failure (no active candidates, query has no shared
  terms) → ``{"results": [], "error": None}``.

Pipeline constraints (design §3.4 — PR5 binding contract):

* **Project-scoped** (D8 — PERMANENT). ``project_id`` is required.
* **Active-only candidates** — ``status='active'``; superseded
  rows never surface.
* **Tag filter (R8)** via
  :meth:`daemon.repositories.snapshot.repository.SnapshotRepository.filter_by_tags`
  (``tag_mode: all|any``, default ``all``).
* **Tag-overlap ranking (R10)** — query-side tags vs candidate's
  ``domain_tags`` contribute to the BM25+cosine composite score.
* **Top-20 LLM bound** — the LLM selection stage is hard-capped
  to ``_LLM_SELECT_TOP_N = 20`` candidates; beyond that the
  ranking degrades to BM25+cosine+tag-overlap order.
* **Freshness post-filter AFTER ranking** —
  :func:`daemon.services.snapshot_executor.compute_staleness_report`
  enriches each result with ``freshness`` + ``age_days`` +
  ``warnings`` (per §5.2). The freshness signal NEVER demotes
  candidates in v1 (R14: only the agent decides whether to trust).
* **Returns metadata + digest preview ONLY.** The full body
  is read at spawn time (Wave 2b's ``spawn_hot_instance``).

Sibling-drift hazard (§3.4 rider — pinned by tests)
----------------------------------------------------

The BM25 helpers (``_tokenize``, ``_bm25_score``) are DIRECTLY
IMPORTED from :mod:`daemon.services.skill_search_service` — the
two services share the same ranking math byte-for-byte. Any
signature change in those helpers MUST break a snapshot search
test (see :mod:`tests.unit.test_snapshot_search_service`); a
silent drift would couple the two subsystems' ranking behavior
without audit.

``text_search_common.py`` extraction is **deferred** — only
extracted when a third consumer appears (spec §3.4 rider).

Result shape (§6.1 — ready for Wave 2b's tool wrapper):

```python
{
    "results": [
        {
            "snapshot_id": str,
            "name": str,           # = snapshot.title
            "tags": list[str],     # R8 typed dim:value strings
            "freshness": "fresh" | "stale" | "expired",
            "age_days": float,
            "summary": str,        # = task_summary or digest.task_summary_text
        },
        ...
    ],
    "error": None | str,
}
```

The tool wrapper (``snapshot_search`` tool, Wave 2b) consumes
this shape and adds the staleness-report fields
(``warnings``, ``repo_state``) on top.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from typing import Any, Literal

import openai

from .llm_failover import current_failover_url, invoke_raw_with_failover
from .skill_search_service import (  # DRIFT-PIN: direct import — sibling-drift hazard
    _bm25_score,
    _extract_json_object,
    _tokenize,
)
from .snapshot_embedding_service import SnapshotEmbeddingService

logger = logging.getLogger(__name__)


# ── Pipeline constants ───────────────────────────────────────────────────

#: Top-N candidates surfaced to the LLM-selection stage (R10 cost
#: discipline — hard cap; degrades gracefully beyond).
_LLM_SELECT_TOP_N = 20

#: BM25 prefilter cutoff — broader than the LLM cap so the cosine
#: rerank has room to reorder. Mirrors ``skill_search``'s
#: ``bm25_top_k`` default of 10 — bumped here to 30 because the
#: snapshot search corpus is wider (per-instance digests can be
#: large) and the cosine rerank needs candidates to work with.
_BM25_TOP_K = 30

#: Outer SQL fetch cap for the Stage-0 active-candidate scan —
#: deliberately wider than the rerank caps; bounds the candidate
#: read without starving the BM25 prefilter.
_SQL_ACTIVE_FETCH_CAP = 200

#: Tag-overlap ranking weight (R10). Per-overlap-tag bonus added
#: to the cosine rerank score. Tunable; the spec lands on a small
#: constant weight that nudges tag-overlapping candidates up
#: without dominating BM25+cosine.
_TAG_OVERLAP_WEIGHT = 0.5


# ============================================================
# Module-level helpers
# ============================================================


def _snapshot_corpus_text(snapshot: Any) -> str:
    """Compose the BM25 corpus text for one candidate snapshot.

    Spec §3.4: ``task_summary + digest.task_summary_text +
    domain_tags``. Tags are joined as free-form text so a token
    overlap on tag values (rare but possible — e.g. ``upgrade``
    in both ``feature:upgrade`` and the query) emerges naturally;
    the explicit overlap signal is added separately via
    :func:`_tag_overlap_score`.
    """
    parts: list[str] = []
    task_summary = getattr(snapshot, "task_summary", "") or ""
    if task_summary:
        parts.append(task_summary)
    digest = getattr(snapshot, "digest", None) or {}
    if isinstance(digest, dict):
        summary_text = digest.get("task_summary_text", "") or ""
        if summary_text:
            parts.append(summary_text)
    tags = list(getattr(snapshot, "domain_tags", None) or [])
    if tags:
        parts.append(" ".join(tags))
    return " ".join(parts).strip()


def _snapshot_preview_summary(snapshot: Any) -> str:
    """Pick the digest preview text for the result shape.

    Prefers ``task_summary`` (the creator's distilled working-state
    summary); falls back to ``digest.task_summary_text`` when the
    explicit task_summary is empty; finally the first non-empty
    R11 section. Always returns a non-empty string when the
    snapshot carries ANY text — Wave 2b's tool wrapper trims/
    truncates downstream.
    """
    task_summary = getattr(snapshot, "task_summary", "") or ""
    if task_summary.strip():
        return task_summary.strip()
    digest = getattr(snapshot, "digest", None) or {}
    if isinstance(digest, dict):
        st = digest.get("task_summary_text", "") or ""
        if st.strip():
            return st.strip()
        for key in (
            "decisions",
            "gotchas",
            "conventions",
            "worked_vs_wasted",
            "workflow_refinements",
            "judgment_calls",
            "open_threads",
            "artifact_refs",
        ):
            value = digest.get(key)
            if isinstance(value, list) and value:
                joined = " ".join(str(v) for v in value if v).strip()
                if joined:
                    return joined
            elif isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _tag_overlap_score(query_tags: list[str], candidate_tags: list[str]) -> float:
    """R10 tag-overlap ranking signal.

    Returns the integer count of overlapping tags (as a float).
    The composite rerank scales this by :data:`_TAG_OVERLAP_WEIGHT`
    so a single overlap nudges the candidate up by ~half a cosine
    unit — visible in the ranking but not dominant over a strong
    cosine match.
    """
    if not query_tags or not candidate_tags:
        return 0.0
    q = set(query_tags)
    c = set(candidate_tags)
    return float(len(q & c))


# ============================================================
# SnapshotSearchService
# ============================================================


class SnapshotSearchService:
    """Three-stage snapshot search service (PR5 — agent-snapshot v1).

    Constructor parameters are duck-typed (``Any``) so this service
    is unit-testable with lightweight mocks (mirrors the skill-side
    precedent). The actual classes live in
    :mod:`daemon.repositories.snapshot.repository` and
    :mod:`daemon.services.snapshot_embedding_service` — neither is
    imported here so the module has no SQLModel/OpenAI init
    dependencies.

    Attributes:
        _repo: Duck-typed
            :class:`~daemon.repositories.snapshot.repository.SnapshotRepository`.
            Expected methods: ``list_active_by_project(project_id, limit)``,
            ``filter_by_tags(candidates, tags, tag_mode)``,
            ``get_embeddings(snapshot_id)``.
        _embedding_service: Duck-typed
            :class:`SnapshotEmbeddingService`. Expected methods:
            ``embed_text(text)`` (async) and ``cosine_similarity(a, b)``
            (sync).
        _llm_config: Dict with ``base_url`` / ``api_key`` / ``model``
            for the chat endpoint (stage 3 LLM selection).
        _staleness_fn: Callable that takes a snapshot and returns a
            :func:`~daemon.services.snapshot_executor.compute_staleness_report`
            result. Wired in :meth:`__init__` for testability —
            tests pass a stub.
    """

    def __init__(
        self,
        snapshot_repo: Any,
        embedding_service: SnapshotEmbeddingService,
        llm_config: dict[str, Any],
        staleness_fn: Any,
    ) -> None:
        """Store dependencies for the three pipeline stages.

        Args:
            snapshot_repo: SnapshotRepository-like instance.
            embedding_service: SnapshotEmbeddingService-like
                instance (must expose async ``embed_text`` + sync
                ``cosine_similarity``).
            llm_config: Chat-endpoint config dict.
            staleness_fn: Callable — ``staleness_fn(snapshot) ->
                dict`` returning the §5.2 staleness report. The
                service uses the ``freshness`` + ``snapshot_age_days``
                keys for the result envelope and ignores the rest
                (Wave 2b's tool wrapper adds the rest).
        """
        self._repo = snapshot_repo
        self._embedding_service = embedding_service
        self._llm_config = dict(llm_config) if llm_config else {}
        self._staleness_fn = staleness_fn

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    async def search(
        self,
        query: str,
        project_id: str,
        tags: list[str] | None = None,
        tag_mode: Literal["all", "any"] = "all",
        limit: int = 10,
        *,
        llm_client: Any | None = None,
        llm_model: str | None = None,
    ) -> dict[str, Any]:
        """Run the full three-stage snapshot search pipeline.

        Args:
            query: User search text (BM25 corpus + cosine embed).
            project_id: Owning project (D8 — required, permanent).
            tags: Optional R8 tag filter.
            tag_mode: ``'all'`` (default) or ``'any'`` — semantics
                for the tag filter.
            limit: Maximum number of results to surface. Default 10
                matches the §6.1 tool signature.
            llm_client: Optional pre-built OpenAI-compatible client
                (test seam — production callers leave it ``None``
                and the service builds a client from
                ``self._llm_config``).
            llm_model: Optional model override (test seam).

        Returns:
            Dict with keys:

            * ``results`` — list of result dicts (per §6.1):
              ``{"snapshot_id", "name", "tags", "freshness",
              "age_days", "summary"}``. Order is by descending
              relevance. Capped at ``limit``.
            * ``error`` — ``None`` on the happy path; a string
              on a hard failure (never raised — matches the
              fail-soft convention; search errors are rare and
              surfaced to the caller verbatim).
        """
        try:
            # ── Stage 0: candidate filter (project + status='active') ─
            candidates = await asyncio.to_thread(
                self._repo.list_active_by_project,
                project_id,
                _SQL_ACTIVE_FETCH_CAP,  # outer SQL cap; the LLM cap is the tighter bound
            )
            if not candidates:
                return {"results": [], "error": None}

            # ── Stage 0.5: tag filter (R8) ────────────────────────────
            if tags:
                candidates = await asyncio.to_thread(
                    self._repo.filter_by_tags,
                    candidates,
                    tags,
                    tag_mode,
                )
            if not candidates:
                return {"results": [], "error": None}

            # ── Stage 1: BM25 prefilter (with tag-overlap signal) ────
            bm25_ranked = await self._bm25_prefilter(
                query,
                candidates,
                tags or [],
                top_k=_BM25_TOP_K,
            )
            if not bm25_ranked:
                return {"results": [], "error": None}

            # ── Stage 2: cosine rerank (best-effort) ─────────────────
            try:
                reranked = await self._embedding_rerank(
                    query, bm25_ranked, top_k=_LLM_SELECT_TOP_N
                )
            except Exception as e:
                logger.warning(
                    f"[SnapshotSearch] Embedding rerank failed, "
                    f"falling back to BM25+tag-overlap: {e}"
                )
                # ``bm25_ranked`` is a list of ``(snap, bm25_score)``
                # tuples from stage 1 — unpack each tuple so the
                # fallback carries snapshot objects (the LLM stage
                # dereferences ``.id`` / ``.title`` / etc.).
                #
                # SCORE-SCALE OPACITY NOTE (Wave 2a review fold-in):
                # this fallback's scores live on the TAG-OVERLAP
                # scale (bounded, ≈[0, 1]), NOT the happy path's
                # composite scale (``bm25 + cosine``, unbounded —
                # see ``_embedding_rerank``). The two branches are
                # therefore never comparable to each other, and the
                # switch is silent by design — only the ORDER within
                # one path is meaningful, and no score value ever
                # surfaces to a caller (the result envelope carries
                # metadata + digest preview only). Do not "normalize"
                # one path onto the other without re-reading this
                # whole pipeline.
                reranked = [
                    (snap, _tag_overlap_score(
                        tags or [],
                        list(getattr(snap, "domain_tags", None) or []),
                    ))
                    for snap, _bm25 in bm25_ranked[:_LLM_SELECT_TOP_N]
                ]
            if not reranked:
                return {"results": [], "error": None}

            # ── Stage 3: LLM selection (best-effort) ─────────────────
            # Skip the LLM stage entirely when no chat model is
            # configured — graceful-degrade path: the search lands
            # with BM25+cosine+tag-overlap order rather than
            # spending wall-clock on a guaranteed-outbound call.
            if not self._llm_config.get("model"):
                final_snapshots = [s for s, _ in reranked[:limit]]
            else:
                try:
                    final_snapshots = await self._llm_select(
                        query,
                        reranked,
                        limit=limit,
                        client=llm_client,
                        model=llm_model,
                    )
                except Exception as e:
                    logger.warning(
                        f"[SnapshotSearch] LLM select failed, falling "
                        f"back to cosine/BM25 order: {e}"
                    )
                    final_snapshots = [s for s, _ in reranked[:limit]]

            # ── Stage 4: freshness post-filter (AFTER ranking) ───────
            results = [
                self._result_envelope(snap) for snap in final_snapshots
            ]
            return {"results": results, "error": None}
        except Exception as e:  # pragma: no cover - defensive belt
            logger.error(f"[SnapshotSearch] hard failure: {e!s}")
            return {"results": [], "error": f"{type(e).__name__}: {e}"}

    # --------------------------------------------------------
    # Stage 1 — BM25 + tag-overlap
    # --------------------------------------------------------

    async def _bm25_prefilter(
        self,
        query: str,
        candidates: list[Any],
        query_tags: list[str],
        top_k: int = _BM25_TOP_K,
    ) -> list[tuple[Any, float]]:
        """BM25 keyword prefilter with tag-overlap ranking signal (R10).

        The pure-Python BM25 helper is **directly imported** from
        :mod:`daemon.services.skill_search_service` — the sibling-drift
        hazard spec (§3.4 rider). Any signature change there MUST
        break a snapshot search test pinned by this module's
        ``test_drift_pin_*`` cases.

        Tag-overlap is folded into the BM25 score as a small
        constant-weight bonus per overlapping tag — visible in
        the ranking without dominating BM25+cosine on ties.

        Args:
            query: Search text.
            candidates: Active, project-scoped (and tag-filtered
                if requested) candidates.
            query_tags: The query-side tag filter (so the bonus
                mirrors the same set the user steered on).
            top_k: Maximum number of (snapshot, score) pairs to
                return.

        Returns:
            ``[(snapshot, score), ...]`` ordered by descending
            score. Capped at ``top_k``. Empty when no candidate
            shares any token with the query.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        # Pre-tokenize + collect corpus stats once.
        tokenized: list[tuple[Any, list[str]]] = []
        doc_freqs: Counter[str] = Counter()
        for snap in candidates:
            doc_tokens = _tokenize(_snapshot_corpus_text(snap))
            tokenized.append((snap, doc_tokens))
            for tok in set(doc_tokens):
                doc_freqs[tok] += 1
        total_docs = max(1, len(tokenized))
        avg_doc_len = (
            sum(len(t) for _, t in tokenized) / total_docs if tokenized else 0.0
        )

        scored: list[tuple[Any, float]] = []
        for snap, doc_tokens in tokenized:
            bm25 = _bm25_score(
                query_tokens,
                doc_tokens,
                doc_freqs=dict(doc_freqs),
                total_docs=total_docs,
                avg_doc_len=avg_doc_len,
            )
            if bm25 <= 0.0:
                continue
            overlap = _tag_overlap_score(
                query_tags, list(getattr(snap, "domain_tags", None) or [])
            )
            # Composite: BM25 (primary signal) + tag-overlap bonus.
            score = bm25 + (_TAG_OVERLAP_WEIGHT * overlap)
            scored.append((snap, score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    # --------------------------------------------------------
    # Stage 2 — embedding cosine rerank
    # --------------------------------------------------------

    async def _embedding_rerank(
        self,
        query: str,
        bm25_ranked: list[tuple[Any, float]],
        top_k: int = _LLM_SELECT_TOP_N,
    ) -> list[tuple[Any, float]]:
        """Cosine rerank of the BM25 candidates.

        Embed the user query once and cosine against each
        candidate's cached :class:`SnapshotEmbedding` rows
        (taking MAX across triggers — mirrors the skill-side
        pattern). The cosine similarity is blended with the
        BM25 score (weighted) so the rerank's #1 slot is a
        signal-balanced winner, not pure cosine.

        Args:
            query: Search text (embedded once).
            bm25_ranked: ``(snapshot, bm25_score)`` pairs from
                stage 1, in descending BM25 order.
            top_k: Maximum pairs returned (the LLM-stage cap).

        Returns:
            ``[(snapshot, composite_score), ...]`` ordered by
            composite descending. Capped at ``top_k``.
        """
        if not bm25_ranked:
            return []

        query_vec = await self._embedding_service.embed_text(query)

        scored: list[tuple[Any, float]] = []
        for snap, bm25_score in bm25_ranked:
            try:
                emb_rows = await asyncio.to_thread(
                    self._repo.get_embeddings, snap.id
                )
            except Exception as e:
                logger.warning(
                    f"[SnapshotSearch] embedding fetch failed for "
                    f"snapshot id={snap.id}: {e}"
                )
                emb_rows = []
            if not emb_rows:
                # No cached embeddings yet (capture predates this
                # PR, or the embedding pipeline failed) — keep the
                # BM25 score alone so we don't penalize new rows.
                scored.append((snap, bm25_score))
                continue
            best_cos = max(
                self._embedding_service.cosine_similarity(
                    query_vec, list(getattr(e, "embedding", []) or [])
                )
                for e in emb_rows
            )
            # Composite: raw BM25 + cosine in [0, 1] — NO rescale of
            # the BM25 term despite its larger magnitude, so a
            # keyword-strong candidate keeps an edge over a
            # semantic-only one unless the cosine gap exceeds the
            # BM25 gap (the blend regression test pins this). The
            # two signals share one additive scale HERE; the
            # embedding-FAILURE fallback below scores on a
            # DIFFERENT (tag-overlap) scale — see the opacity note
            # at that site.
            composite = bm25_score + best_cos
            scored.append((snap, composite))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    # --------------------------------------------------------
    # Stage 3 — LLM selection (top-20 hard cap)
    # --------------------------------------------------------

    async def _llm_select(
        self,
        query: str,
        reranked: list[tuple[Any, float]],
        limit: int = 10,
        *,
        client: Any | None = None,
        model: str | None = None,
    ) -> list[Any]:
        """LLM final selection from the top-N reranked candidates.

        Bounded to the top :data:`_LLM_SELECT_TOP_N` candidates
        (the caller guarantees this — the input list is the
        rerank's output). Beyond the cap, ranking degrades to
        BM25+cosine+tag-overlap order.

        The chat-completion prompt lists each candidate's
        ``title`` + ``task_summary`` + ``tags`` (NOT the full
        digest — too verbose for a short context window). The
        model returns JSON ``{"selected": [<id>, ...]}``;
        unmapped ids are dropped with a warning.

        Args:
            query: User search text (echoed back to the LLM so
                it can ground its relevance calls).
            reranked: ``(snapshot, composite_score)`` pairs from
                stage 2, in descending composite order.
            limit: Maximum snapshots to return.
            client: Optional pre-built OpenAI-compatible client
                (test seam).
            model: Optional model override (test seam).

        Returns:
            List of snapshot rows, in LLM-picked order, capped at
            ``limit``. Empty when the LLM declines to pick any.

        Raises:
            Exception: Propagates any exception from the LLM
                call or the JSON parser. The public ``search``
                method catches and falls back to the rerank
                order.
        """
        if not reranked:
            return []

        # Bounded to the top-N (defensive — the caller pre-bounded,
        # but rerank + tag-overlap can swell the list).
        candidates = reranked[:_LLM_SELECT_TOP_N]

        # Build the prompt — minimal per-candidate surface.
        lines: list[str] = []
        for snap, _score in candidates:
            sid = getattr(snap, "id", "?")
            title = getattr(snap, "title", "") or ""
            task_summary = getattr(snap, "task_summary", "") or ""
            digest = getattr(snap, "digest", None) or {}
            preview = ""
            if isinstance(digest, dict):
                preview = (digest.get("task_summary_text", "") or "")[:200]
            tags = list(getattr(snap, "domain_tags", None) or [])
            tag_line = " ".join(tags) if tags else "(no tags)"
            summary = task_summary or preview or "(no summary)"
            lines.append(
                f"- id={sid} title={title!r} tags=[{tag_line}] "
                f"summary={summary[:300]!r}"
            )
        candidates_block = "\n".join(lines)

        system_prompt = (
            "You are a search-result selector for an agent-snapshot "
            "system. Given a user query and a list of candidate "
            "snapshots, return a JSON object with a single key "
            "'selected' whose value is a JSON array of snapshot "
            "ids, ordered by relevance to the query. Pick AT MOST "
            f"{limit} ids. Use ONLY ids from the candidate list. "
            "Return ONLY the JSON object — no prose, no markdown "
            "fences."
        )
        user_prompt = (
            f"User query: {query}\n\n"
            f"Candidates (top {_LLM_SELECT_TOP_N} by hybrid score):\n"
            f"{candidates_block}\n\n"
            f"Return JSON: {{\"selected\": [<id>, <id>, ...]}}"
        )

        response_text = await self._chat_call(
            system_prompt, user_prompt, client=client, model=model
        )

        parsed = self._parse_selection(response_text, limit=limit)
        if not parsed:
            return []

        # Map picked ids back to snapshot rows; missing ids dropped.
        snap_by_id = {getattr(s, "id", None): s for s, _ in candidates}
        picked: list[Any] = []
        for sid in parsed:
            snap = snap_by_id.get(sid)
            if snap is not None and snap not in picked:
                picked.append(snap)
            elif snap is None:
                logger.warning(
                    f"[SnapshotSearch] LLM picked unknown id={sid!r} — "
                    "dropped"
                )
            if len(picked) >= limit:
                break
        return picked

    # --------------------------------------------------------
    # Stage 4 — freshness post-filter
    # --------------------------------------------------------

    def _result_envelope(self, snapshot: Any) -> dict[str, Any]:
        """Render the §6.1 result shape with freshness attached.

        Runs :attr:`_staleness_fn` (the §5.2 helper) for the
        post-filter; the freshness + age_days keys land in the
        envelope, the full ``warnings``/``repo_state`` is left
        to Wave 2b's tool wrapper.
        """
        staleness = self._staleness_fn(snapshot) or {}
        tags = list(getattr(snapshot, "domain_tags", None) or [])
        return {
            "snapshot_id": getattr(snapshot, "id", ""),
            "name": getattr(snapshot, "title", "") or "",
            "tags": tags,
            "freshness": staleness.get("freshness", "fresh"),
            "age_days": staleness.get("snapshot_age_days", 0.0),
            "summary": _snapshot_preview_summary(snapshot),
        }

    # --------------------------------------------------------
    # Chat-call seam (testable)
    # --------------------------------------------------------

    async def _chat_call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        client: Any | None = None,
        model: str | None = None,
    ) -> str:
        """Run a chat completion for stage 3 LLM selection.

        Production path constructs a fresh ``openai.OpenAI`` client
        from ``self._llm_config`` (HA-failover-aware). Tests can
        inject a pre-built client via ``client=`` (mirrors the
        skill-side pattern).

        Returns:
            The raw response text (JSON extraction happens in
            :meth:`_parse_selection`).
        """
        chosen_model = model or self._llm_config.get("model") or "gpt-4o-mini"
        base_url = self._llm_config.get("base_url")
        api_key = self._llm_config.get("api_key")
        failover_config = {
            "base_url": base_url,
            "base_url_backup": self._llm_config.get("base_url_backup"),
            "api_key": api_key,
        }

        if client is None:
            def _call() -> Any:
                url = current_failover_url() or base_url
                kwargs: dict[str, Any] = {
                    "api_key": api_key or "",
                    "base_url": url or None,
                }
                c = openai.OpenAI(**kwargs)
                return c.chat.completions.create(
                    model=chosen_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.0,
                )
            response = await asyncio.to_thread(
                invoke_raw_with_failover, _call, failover_config
            )
        else:
            response = await asyncio.to_thread(
                client.chat.completions.create,
                chosen_model,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                0.0,
            )

        try:
            choices = getattr(response, "choices", None) or []
            if not choices:
                return ""
            first = choices[0]
            message = getattr(first, "message", None)
            if message is None:
                return ""
            return str(getattr(message, "content", "") or "")
        except Exception:  # pragma: no cover - defensive belt
            return ""

    @staticmethod
    def _parse_selection(raw_text: str, *, limit: int) -> list[str] | None:
        """Extract ``{"selected": [<id>, ...]}`` from the LLM response.

        Tolerant of markdown code fences (re-uses the skill-side
        :func:`_extract_json_object`) and minor JSON-shape variants.
        Returns ``None`` when no JSON object can be located — the
        public ``search`` method catches and falls back to the
        rerank order.
        """
        json_text = _extract_json_object(raw_text or "")
        if not json_text:
            return None
        try:
            parsed = json.loads(json_text)
        except (ValueError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        selected = parsed.get("selected")
        if not isinstance(selected, list):
            return None
        out: list[str] = []
        for item in selected:
            if isinstance(item, str) and item:
                out.append(item)
            if len(out) >= limit:
                break
        return out


__all__ = [
    "SnapshotSearchService",
    "_snapshot_corpus_text",
    "_snapshot_preview_summary",
    "_tag_overlap_score",
    "_BM25_TOP_K",
    "_LLM_SELECT_TOP_N",
    "_SQL_ACTIVE_FETCH_CAP",
    "_TAG_OVERLAP_WEIGHT",
]
