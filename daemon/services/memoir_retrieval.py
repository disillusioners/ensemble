"""Memoir retrieval — the hybrid recall and the timeline read.

Phase 2 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase2-plan.md`` — the whole
phase; decisions D2.1..D2.17).

Placement rationale (D0.2): the retrieval service sits BESIDE its
consumer seam (``daemon/services/critical_notes_selector.py`` is the
precedent) because it is consumed by BOTH the tools (phase 4) and the
briefing builder (phase 5) — putting it inside the tool module would
leave the briefing with a second implementation.

Design contract refs: ``design.md`` §7 (the retrieval algorithm) and §8
(the wake briefing); ``phase2-plan.md`` (the whole phase); amendments
AM-1 (the PG lexical-ranker argument order — ``ts_rank_cd(tsv,
plainto_tsquery('english', :q))``), AM-2..AM-5 (the RRF arithmetic),
AM-14 (the marked-id set equals the shipped projection), AM-33 (the
model-identity drift disclosure), AM-36 (the worker must stay
driver-agnostic — no ``tsv`` reference in the portable SQL paths).

THE ARITHMETIC (adjudicated — decisions.md §4 row 15):

* the candidate pool is 100 PER RANKER (``MEMOIR_RANK_CANDIDATES``); the
  two pools are INDEPENDENT and their UNION is the candidate set;
* ranks are 1-based, PER-RANKER and LOCAL, with COMPETITION ranking
  inside a ranker (equal scores share a rank; the next distinct score
  skips: 1, 2, 2, 4);
* a row ABSENT from a ranker's list contributes NO term for that ranker —
  "absent" means the term is MISSING, not that the row scores zero (the
  design §7 degradation sentence governs: an episode without an embedding
  contributes to the lexical ranker only; it is neither dropped nor
  zero-scored);
* the fusion is ``score = Σ_r 1/(RRF_K + rank_r)`` with ``RRF_K = 60`` as a
  MODULE CONSTANT (not a config knob), the terms summed in a FIXED
  ranker order (lexical, then semantic) so float addition is
  deterministic, and NO rounding (rounding manufactures ties and breaks
  monotonicity);
* the final comparator is TOTAL: ``(-prior_multiplied_score DESC,
  importance DESC, event_at DESC, id ASC)``. The prior MULTIPLIES the
  fused score for non-keepsakes (a keepsake's multiplier is 1.0 — never
  decayed), so it MAY reorder rows that importance had separated, and
  importance is RE-APPLIED after the multiplication. The idle basis is
  ``now − coalesce(last_accessed_at, created_at)`` (the design's
  ``idle = now − last_accessed_at`` is NULL-unsafe);
* the access stats are updated ONLY on an explicit recall, and they mark
  EXACTLY the ids in the SHIPPED projection — post-clamp and
  post-ranked-drop. The marked-id list and the rendered-id list are
  asserted by EQUALITY, never by containment;
* the recall budget is enforced on the SHIPPED projection with a FINAL
  RE-CHECK before the return (the metrics-read M-1 doctrine: measure the
  shipped bytes, never the raw rows).

THE KNOB IS NOT READ HERE. The gate ORDER is adjudicated (AM-16): the
kill-switch is checked by the CALLER (the tool layer, phase 4; the
briefing builder, phase 5) BEFORE any DB touch, and an unauthorized
caller learns nothing about the knob's state. The service is the layer
BEHIND that gate, so it neither reads nor caches the knob.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Sequence

from daemon.repositories.memoir.repository import (
    MEMOIR_RANK_CANDIDATES,
    MemoirRepository,
    as_aware_utc,
)
from daemon.services.timestamps import now_utc, to_utc_iso

__all__ = [
    "MEMOIR_PRIOR_TAU_DAYS",
    "MEMOIR_QUERY_EMBED_TIMEOUT_S",
    "MEMOIR_RANK_CANDIDATES",
    "MEMOIR_RECALL_BODY_TOKEN_CLAMP",
    "MEMOIR_RECALL_DEFAULT_K",
    "MEMOIR_RECALL_MAX_K",
    "MEMOIR_RECALL_TOKEN_BUDGET",
    "RRF_K",
    "MemoirRecallError",
    "MemoirRecallResult",
    "MemoirRetrievalService",
    "competition_ranks",
    "fuse_rankings",
    "recency_multiplier",
    "render_episode",
]


#: The RRF fusion constant (AM-5) — a MODULE CONSTANT, not a config knob.
#: A fusion constant is not a budget; the token budgets below govern the
#: RENDERED size, not the ranking.
RRF_K = 60

#: Sentinel for "embedder not provided" (ITEM 1). Distinct from
#: an explicit ``None`` (which means "semantic disabled").
_UNSET = object()

#: The recency prior's half-life-ish constant: ``exp(−idle_days / tau)``
#: with tau = 180 days (design §7).
MEMOIR_PRIOR_TAU_DAYS = 180.0

#: The query-embed budget for a single recall (ITEM 1 of the memoir
#: phase-7 close-out). The worker's 30 s budget is for ticks; a recall
#: sits in an agent's turn, so it gets a SHORT bounded timeout. On any
#: exception/timeout/config-missing the service degrades to lexical-only
#: (design section 3 degradation contract) — a recall never fails and
#: never hangs because of the embedder.
MEMOIR_QUERY_EMBED_TIMEOUT_S = 5.0

#: The recall budget (D2.10 / adjudication row 10).
MEMOIR_RECALL_DEFAULT_K = 5
MEMOIR_RECALL_MAX_K = 20
MEMOIR_RECALL_BODY_TOKEN_CLAMP = 1200
MEMOIR_RECALL_TOKEN_BUDGET = 6000

#: The house truncation marker for a clamped episode body.
MEMOIR_BODY_TRUNCATION_MARKER = "[…body truncated, {chars} chars total]"


class MemoirRecallError(ValueError):
    """A typed refusal from the recall service.

    A ``ValueError`` subclass so a caller that catches ``ValueError``
    still gets the specific type; the ``code`` is what the tool layer maps
    onto the frozen ``memoir_*`` refusal vocabulary (AM-21).
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class MemoirRecallResult:
    """The envelope of a recall (or timeline) read.

    ``episodes`` is the SHIPPED projection: the ids in it are exactly the
    ids that were marked (equality, not containment), and its bodies are
    the CLAMPED bodies that were measured against the budget.
    """

    episodes: list[dict[str, Any]] = field(default_factory=list)
    footer: str = ""
    body_tokens: int = 0
    total_tokens: int = 0
    dropped: int = 0
    rendered_ids: list[str] = field(default_factory=list)
    marked_ids: list[str] = field(default_factory=list)
    stats_updated: bool = False
    rank_basis: str = "rrf"
    active_model: str | None = None
    embedding_excluded: int = 0
    missing_embedding: int = 0
    semantic_enabled: bool = False


# ── the pure arithmetic (the hand-computed fixture's subject) ─────────────


def competition_ranks(rows: Sequence[tuple[str, float]]) -> dict[str, int]:
    """Assign 1-based COMPETITION ranks to a ranker's rows (D2.5 / AM-3).

    The rows are expected in ``(score DESC, id ASC)`` order. Equal scores
    SHARE a rank and the next distinct score SKIPS: 1, 2, 2, 4. Dense
    ranking would change the RRF mass of the ties, so it is rejected.
    """
    ranks: dict[str, int] = {}
    previous_score: float | None = None
    previous_rank = 0
    for position, (row_id, score) in enumerate(rows, start=1):
        if previous_score is not None and score == previous_score:
            rank = previous_rank
        else:
            rank = position
            previous_rank = rank
            previous_score = score
        ranks[row_id] = rank
    return ranks


def fuse_rankings(
    lexical: Sequence[tuple[str, float]],
    semantic: Sequence[tuple[str, float]],
    *,
    k: int = RRF_K,
) -> dict[str, float]:
    """Fuse the two ranker lists by Reciprocal Rank Fusion (D2.6 / AM-5).

    ``score = Σ_r 1/(k + rank_r)``. The terms are summed in a FIXED
    ranker order — lexical first, then semantic — so float addition is
    deterministic, and NOTHING is rounded (rounding manufactures ties and
    breaks monotonicity).

    A row ABSENT from a ranker's list contributes NO term for that ranker:
    the term is MISSING, not zero.
    """
    lexical_ranks = competition_ranks(lexical)
    semantic_ranks = competition_ranks(semantic)
    fused: dict[str, float] = {}
    for row_id in set(lexical_ranks) | set(semantic_ranks):
        score = 0.0
        if row_id in lexical_ranks:
            score += 1.0 / (k + lexical_ranks[row_id])
        if row_id in semantic_ranks:
            score += 1.0 / (k + semantic_ranks[row_id])
        fused[row_id] = score
    return fused


def recency_multiplier(
    *, is_keepsake: bool, idle_days: float
) -> float:
    """The recency prior (design §7 / D2.7).

    A KEEPSAKE is exempt: its multiplier is 1.0 and is NEVER decayed — a
    keepsake is not "stale", it is permanent.
    """
    if is_keepsake:
        return 1.0
    return math.exp(-max(0.0, float(idle_days)) / MEMOIR_PRIOR_TAU_DAYS)


# ── the projection + the rendering ────────────────────────────────────────


def render_episode(projection: dict[str, Any]) -> str:
    """The SHIPPED text of one episode — the bytes the budget measures.

    The budget is measured on THIS, not on the raw row (the metrics-read
    M-1 doctrine). The projection and its rendering are produced together
    by the service, so they cannot drift apart.
    """
    return "\n".join(
        [
            f"id={projection['id']}",
            f"title={projection['title'] or ''}",
            f"event_at={projection['event_at']}",
            f"importance={projection['importance']}",
            f"keepsake={'yes' if projection['is_keepsake'] else 'no'}",
            f"body={projection['body']}",
        ]
    )


def _estimate_tokens(text: str) -> int:
    """The house token estimator (``daemon/loader.py``).

    Imported lazily: the loader is a heavy module and the service must
    stay importable without it.
    """
    from daemon.loader import estimate_tokens

    return estimate_tokens(text)


def clamp_body(
    body: str, max_tokens: int = MEMOIR_RECALL_BODY_TOKEN_CLAMP
) -> tuple[str, bool, int]:
    """Clamp one episode body to the per-episode token budget (D2.10).

    Returns ``(clamped_body, was_truncated, original_char_count)``. The
    house marker carries the ORIGINAL character count, so the agent can
    see how much was withheld.
    """
    total_chars = len(body)
    if _estimate_tokens(body) <= max_tokens:
        return body, False, total_chars
    marker = MEMOIR_BODY_TRUNCATION_MARKER.format(chars=total_chars)
    low, high = 0, len(body)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        candidate = body[:middle] + marker
        if _estimate_tokens(candidate) <= max_tokens:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return body[:best] + marker, True, total_chars


# ── the service ───────────────────────────────────────────────────────────


class MemoirRetrievalService:
    """The hybrid recall + the timeline read.

    The service owns the ARITHMETIC and the BUDGET; the repository owns
    the SQL. The service never builds an engine and never reads an
    ambient ``POSTGRES_*`` variable.
    """

    def __init__(
        self,
        repository: MemoirRepository,
        *,
        embedder: Any = _UNSET,
        embedding_model: str | None = None,
        embedding_dims: int | None = None,
        config: Any = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self._config = config
        # Resolve identity defaults only when a config is available
        # (production path). Without config (tests / degraded boots) keep
        # None so the service stays lexical-only and hermetic. An
        # explicitly injected model (e.g. the degradation test) is
        # preserved.
        if embedding_model is None and self._config is not None:
            if self._resolve_default_api_key() and self._resolve_default_base_url():
                embedding_model = self._resolve_default_embedding_model()
            else:
                embedding_model = None
        if embedding_dims is None and self._config is not None:
            if self._resolve_default_api_key() and self._resolve_default_base_url():
                embedding_dims = self._resolve_default_embedding_dims()
            else:
                embedding_dims = None
        #: The ACTIVE embedding identity. The semantic ranker only
        #: considers rows whose ``embedding_model`` AND ``embedding_dims``
        #: match (DD-6: "a dimension is not an identity").
        self.embedding_model = embedding_model
        self.embedding_dims = embedding_dims
        #: The QUERY embedder. Called EXACTLY ONCE per recall (D2.3) —
        #: embedding the query per candidate would turn one embed into a
        #: hundred. When None, the service degrades to lexical-only. When
        #: not injected, a DEFAULT house embedder is resolved (ITEM 1) so
        #: production recall runs hybrid (design section 7 / D2.3) rather
        #: than lexical-only. The embed call is bounded and degradable:
        #: any exception/timeout/config-missing degrades to lexical-only
        #: (design section 3). The tool module stays PURE — it holds no
        #: embedder symbol; the service owns the resolution.
        if embedder is _UNSET:
            if self._can_resolve_default_embedder():
                embedder = self._make_default_embedder()
            else:
                embedder = None
        self.embedder = embedder
        self._now_fn = now_fn or now_utc
        #: Per-recall flag: set True in ``_semantic_pool`` when the query
        #: embed call raised or timed out. Drives ``semantic_used`` so a
        #: degraded recall reports ``semantic=off`` honestly.
        self._embed_call_failed: bool = False

    # -- the public reads --

    def recall(
        self, agent_id: str, query: str, k: int = MEMOIR_RECALL_DEFAULT_K
    ) -> MemoirRecallResult:
        """The hybrid recall: lexical + semantic, fused by RRF.

        Args:
            agent_id: The owning agent (the identity is captured by the
                caller, never taken from the query — DD-13).
            query: The recall query.
            k: How many episodes to return. Default 5, hard max 20; an
                out-of-range value is a TYPED REFUSAL, never a silent
                clamp (the agent cannot tell its request was reduced if
                the value is silently reduced).

        Raises:
            MemoirRecallError: on an out-of-range ``k`` or an empty query.
        """
        count = self._validate_k(k)
        clean_query = (query or "").strip()
        if not clean_query:
            raise MemoirRecallError(
                "query_required", "the recall query must not be empty"
            )

        lexical = self.repository.lexical_rank(
            agent_id, clean_query, MEMOIR_RANK_CANDIDATES
        )
        rows = self.repository.fetch_latest(agent_id, limit=None)
        by_id = {row.id: row for row in rows}

        self._embed_call_failed = False
        semantic, embedding_excluded = self._semantic_pool(rows, clean_query)
        missing_embedding = self._count_missing_embeddings(lexical, by_id)

        fused = fuse_rankings(lexical, semantic)
        ranked = self._rank(fused, by_id)

        shipped, dropped, body_tokens, footer, total_tokens = self._apply_budget(
            ranked,
            rank_basis="rrf",
            missing_embedding=missing_embedding,
            embedding_excluded=embedding_excluded,
            semantic_used=self._semantic_enabled() and not self._embed_call_failed,
            count=count,
        )
        rendered_ids = [episode["id"] for episode in shipped]

        # THE STATS: exactly the SHIPPED ids (D2.8 / AM-14). The episodes
        # dropped for budget are NOT returned and must NOT be marked.
        marked_ids: list[str] = []
        stats_updated = False
        if rendered_ids:
            self.repository.mark_recalled(rendered_ids, now=self._now_fn())
            marked_ids = list(rendered_ids)
            stats_updated = True

        return MemoirRecallResult(
            episodes=shipped,
            footer=footer,
            body_tokens=body_tokens,
            total_tokens=total_tokens,
            dropped=dropped,
            rendered_ids=rendered_ids,
            marked_ids=marked_ids,
            stats_updated=stats_updated,
            rank_basis="rrf",
            active_model=self.embedding_model,
            embedding_excluded=embedding_excluded,
            missing_embedding=missing_embedding,
            semantic_enabled=self._semantic_enabled() and not self._embed_call_failed,
        )

    def timeline(
        self,
        agent_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        q: str | None = None,
    ) -> MemoirRecallResult:
        """The timeline read — filter-then-rank (D2.9).

        ``since`` / ``until`` are INCLUSIVE bounds on ``event_at``. With a
        ``q`` the LEXICAL-ONLY ranker narrows the window; without one the
        order is ``event_at DESC``. There is NO cosine, NO RRF, NO recency
        prior and NO keepsake priority — the prior would actively distort
        a historical window ("what was true in March" must not be demoted
        for being March).

        The timeline writes NO access stats: a read is not an act of
        attention (DD-12).

        The parameter names mirror the blessed tool signature
        ``memoir_timeline(since?, until?, q?)`` (DR-4 renamed only the
        window bounds; the narrowing term stays ``q``).
        """
        if since is not None and until is not None:
            if as_aware_utc(since) > as_aware_utc(until):  # type: ignore[operator]
                raise MemoirRecallError(
                    "invalid_window",
                    "the timeline window is inverted: since is after until",
                )
        rows = self.repository.timeline_window(
            agent_id, since=since, until=until, query=q
        )
        ranked = list(rows)
        shipped, dropped, body_tokens, footer, total_tokens = self._apply_budget(
            ranked,
            rank_basis="timeline_lexical" if (q or "").strip() else "timeline",
        )
        return MemoirRecallResult(
            episodes=shipped,
            footer=footer,
            body_tokens=body_tokens,
            total_tokens=total_tokens,
            dropped=dropped,
            rendered_ids=[episode["id"] for episode in shipped],
            marked_ids=[],
            stats_updated=False,
            rank_basis="timeline_lexical" if (q or "").strip() else "timeline",
            active_model=self.embedding_model,
            embedding_excluded=0,
            missing_embedding=0,
            semantic_enabled=False,
        )

    # -- internals --

    @staticmethod
    def _validate_k(k: int) -> int:
        try:
            value = int(k)
        except (TypeError, ValueError) as exc:
            raise MemoirRecallError(
                "k_out_of_range", f"k must be an integer; got {k!r}"
            ) from exc
        if value < 1 or value > MEMOIR_RECALL_MAX_K:
            raise MemoirRecallError(
                "k_out_of_range",
                (
                    f"k must be between 1 and {MEMOIR_RECALL_MAX_K}; got {value} "
                    f"(an out-of-range k is refused, never silently clamped)"
                ),
            )
        return value

    def _semantic_enabled(self) -> bool:
        """The semantic lane needs BOTH an identity and a dimension (D2.3)."""
        return bool(self.embedder) and bool(self.embedding_model) and bool(
            self.embedding_dims
        )

    # ── ITEM 1: default query-embedder resolution (design section 7) ─────

    def _resolve_default_embedding_model(self) -> str | None:
        """Resolve the active embedding model identity (mirrors the worker).

        Precedence: ``EMBEDDING_MODEL`` env > the shared config's
        ``embedding_model`` > the worker's ``DEFAULT_EMBEDDING_MODEL``
        (``text-embedding-3-small``). Returns ``None`` only when nothing
        usable can be resolved.
        """
        import os

        env_value = (os.environ.get("EMBEDDING_MODEL") or "").strip()
        if env_value:
            return env_value
        cfg = getattr(self._config, "llm", None)
        # The memoir config does not carry an embedding_model; the shared
        # embedding family does. Consult the config's embedding_model if it
        # exposes one (it does not in v1 — the env/worker default wins).
        shared = getattr(self._config, "embedding", None)
        if shared is not None:
            model = getattr(shared, "embedding_model", None)
            if model:
                return str(model)
        if cfg is not None:
            model = getattr(cfg, "embedding_model", None)
            if model:
                return str(model)
        try:
            from daemon.services.memoir_embedding_worker import (
                DEFAULT_EMBEDDING_MODEL,
            )

            return DEFAULT_EMBEDDING_MODEL
        except Exception:  # noqa: BLE001
            return None

    def _resolve_default_embedding_dims(self) -> int | None:
        """Resolve the active embedding dimensionality (mirrors the worker)."""
        import os

        env_value = (os.environ.get("ENSEMBLE_MEMOIR_EMBEDDING_DIMENSIONS") or "").strip()
        if env_value:
            try:
                return int(env_value)
            except ValueError:
                pass
        memoir_cfg = getattr(self._config, "memoir", None)
        if memoir_cfg is not None:
            dims = getattr(memoir_cfg, "embedding_dimensions", None)
            if dims:
                try:
                    return int(dims)
                except (TypeError, ValueError):
                    pass
        # Fall back to the worker's default (1536).
        try:
            from daemon.config import MemoirConfig

            return int(MemoirConfig.model_fields["embedding_dimensions"].default)
        except Exception:  # noqa: BLE001
            return 1536

    def _resolve_default_base_url(self) -> str | None:
        import os

        env_value = (os.environ.get("EMBEDDING_BASE_URL") or "").strip()
        if env_value:
            return env_value
        cfg = getattr(self._config, "llm", None)
        if cfg is not None:
            base_url = getattr(cfg, "base_url", None)
            if base_url:
                return str(base_url)
        return None

    def _resolve_default_api_key(self) -> str | None:
        import os

        env_value = (os.environ.get("EMBEDDING_API_KEY") or "").strip()
        if env_value:
            return env_value
        cfg = getattr(self._config, "llm", None)
        if cfg is not None:
            api_key = getattr(cfg, "api_key", None)
            if api_key:
                return str(api_key)
        return None

    def _can_resolve_default_embedder(self) -> bool:
        """Whether a usable house embedder can be built.

        Config-missing degrades to lexical-only (design section 3): if we
        cannot resolve an API key, semantic stays off rather than failing
        at recall time.
        """
        if self._config is None:
            return False
        return bool(self._resolve_default_api_key()) and bool(
            self._resolve_default_base_url()
        ) and bool(self.embedding_model) and bool(self.embedding_dims)

    def _make_default_embedder(self) -> Callable[[str], Sequence[float]]:
        """Build the bounded, degradable house embedder (ITEM 1).

        Wraps ``memoir_embedding_worker._default_embed`` (the ONLY SDK call
        site in the lane) with the short query timeout and converts any
        exception into a degraded (lexical-only) signal by re-raising a
        sentinel that ``_semantic_pool`` catches. The embedder is called
        EXACTLY ONCE per recall (D2.3).
        """
        from daemon.services.memoir_embedding_worker import _default_embed

        model = self.embedding_model
        base_url = self._resolve_default_base_url()
        api_key = self._resolve_default_api_key()
        timeout_s = MEMOIR_QUERY_EMBED_TIMEOUT_S

        def _embed(text: str) -> Sequence[float]:
            return _default_embed(
                text,
                model=model,  # type: ignore[arg-type]
                base_url=base_url,
                api_key=api_key,
                timeout_s=timeout_s,
            )

        return _embed

    def _semantic_pool(
        self, rows: Sequence[Any], query: str
    ) -> tuple[list[tuple[str, float]], int]:
        """The SEMANTIC ranker's pool (D2.3 / AM-31).

        The candidate set is the rows that carry an embedding whose
        IDENTITY and DIMENSIONS match the active ones; the cosine is
        computed in Python; the pool is the top
        ``MEMOIR_RANK_CANDIDATES`` by ``(score DESC, id ASC)``.

        The returned count is the number of rows SKIPPED for an
        identity/dims mismatch — the disclosure D2.11 requires, so a
        partial recall is visible to the agent instead of being
        indistinguishable from a complete one.
        """
        embedded = [row for row in rows if row.embedding is not None]
        if not self._semantic_enabled():
            return [], 0

        matched: list[Any] = []
        excluded = 0
        dims = int(self.embedding_dims or 0)
        for row in embedded:
            if (
                row.embedding_model != self.embedding_model
                or int(row.embedding_dims or 0) != dims
                or len(row.embedding or []) != dims
            ):
                excluded += 1
                continue
            matched.append(row)

        # THE QUERY IS EMBEDDED ONCE per call (D2.3). Bounded and
        # degradable (ITEM 1): any exception/timeout/config-missing
        # degrades to lexical-only and the footer discloses it
        # (semantic=off). A recall never fails or hangs because of the
        # embedder (design section 3).
        from daemon.services.critical_notes_selector import _cosine_similarity

        try:
            query_vector = list(self.embedder(query) or [])  # type: ignore[misc]
        except Exception:  # noqa: BLE001 — degrade to lexical-only
            self._embed_call_failed = True
            return [], excluded
        scored: list[tuple[str, float]] = []
        for row in matched:
            score = _cosine_similarity(query_vector, list(row.embedding or []))
            scored.append((row.id, float(score)))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:MEMOIR_RANK_CANDIDATES], excluded

    @staticmethod
    def _count_missing_embeddings(
        lexical: Sequence[tuple[str, float]], by_id: dict[str, Any]
    ) -> int:
        """How many CANDIDATES carry no embedding at all.

        These are the rows that contribute to the lexical ranker only —
        the design §7 degradation, made countable so the agent can see
        that the recall was partial.
        """
        missing = 0
        for row_id, _score in lexical:
            row = by_id.get(row_id)
            if row is not None and row.embedding is None:
                missing += 1
        return missing

    def _rank(
        self, fused: dict[str, float], by_id: dict[str, Any]
    ) -> list[Any]:
        """The prior + the TOTAL comparator (D2.7 / AM-4)."""
        now = as_aware_utc(self._now_fn())
        scored: list[tuple[float, int, float, str, Any]] = []
        for row_id, fused_score in fused.items():
            row = by_id.get(row_id)
            if row is None:
                continue
            last_accessed = as_aware_utc(row.last_accessed_at)
            created_at = as_aware_utc(row.created_at)
            basis = last_accessed or created_at or now
            idle_seconds = max(0.0, (now - basis).total_seconds())
            idle_days = idle_seconds / 86400.0
            multiplier = recency_multiplier(
                is_keepsake=bool(row.is_keepsake), idle_days=idle_days
            )
            event_at = as_aware_utc(row.event_at) or basis
            scored.append(
                (
                    -float(fused_score * multiplier),
                    -int(row.importance or 0),
                    -event_at.timestamp(),
                    row_id,
                    row,
                )
            )
        scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        return [item[4] for item in scored]

    def _apply_budget(
        self,
        ranked: Sequence[Any],
        *,
        rank_basis: str,
        missing_embedding: int = 0,
        embedding_excluded: int = 0,
        semantic_used: bool = False,
        count: int | None = None,
    ) -> tuple[list[dict[str, Any]], int, int, str, int]:
        """The budget enforcement on the SHIPPED projection (D2.10).

        The K-SLICE first (C-1): when ``count`` is set, only the first
        ``count`` ranked candidates are even considered — ``k`` is the
        cap on HOW MANY episodes ship and the token envelope is the cap
        on HOW BIG they may be, and BOTH must hold. Without the slice
        the validated ``k`` was checked twice and applied never, so a
        ``k=5`` recall over a larger corpus shipped everything that fit
        the budget. The timeline passes ``count=None`` (AM-42: it has no
        ``k``) and is bounded by the shared envelope alone.

        Then the per-episode body clamp, then the total envelope with a
        RANKED-DROP (the head results stay whole), then the FINAL
        RE-CHECK — the footer is part of the shipped bytes, so the
        envelope is re-measured with it in place and the tail is dropped
        again if the footer pushed the total over.
        """
        candidates: list[Any] = list(ranked)
        if count is not None:
            candidates = candidates[:count]
        shipped: list[dict[str, Any]] = []
        shipped_texts: list[str] = []
        body_tokens = 0
        for row in candidates:
            projection = self._project(row)
            text = render_episode(projection)
            tokens = _estimate_tokens(text)
            if body_tokens + tokens > MEMOIR_RECALL_TOKEN_BUDGET:
                break  # RANKED-DROP — the head stays whole
            shipped.append(projection)
            shipped_texts.append(text)
            body_tokens += tokens
        dropped = len(candidates) - len(shipped)

        footer = self._footer(
            episodes=len(shipped),
            dropped=dropped,
            body_tokens=body_tokens,
            rank_basis=rank_basis,
            missing_embedding=missing_embedding,
            embedding_excluded=embedding_excluded,
            semantic_used=semantic_used,
        )
        total_tokens = body_tokens + _estimate_tokens(footer)

        # THE FINAL RE-CHECK: the footer is shipped too, so the envelope is
        # re-measured with it in place. Removing this check lets the
        # shipped projection exceed the budget whenever the footer tips it
        # over.
        while total_tokens > MEMOIR_RECALL_TOKEN_BUDGET and shipped:
            removed_text = shipped_texts.pop()
            shipped.pop()
            body_tokens -= _estimate_tokens(removed_text)
            dropped += 1
            footer = self._footer(
                episodes=len(shipped),
                dropped=dropped,
                body_tokens=body_tokens,
                rank_basis=rank_basis,
                missing_embedding=missing_embedding,
                embedding_excluded=embedding_excluded,
                semantic_used=semantic_used,
            )
            total_tokens = body_tokens + _estimate_tokens(footer)

        return shipped, dropped, body_tokens, footer, total_tokens

    @staticmethod
    def _project(row: Any) -> dict[str, Any]:
        """One episode's shipped projection (AM-23's field set).

        The BODY IS THE CLAMPED BODY: the shipped bytes are the measured
        bytes.
        """
        body, truncated, total_chars = clamp_body(row.body or "")
        return {
            "id": row.id,
            "title": row.title,
            "body": body,
            "body_truncated": truncated,
            "body_chars_total": total_chars,
            "created_at": to_utc_iso(row.created_at),
            "event_at": to_utc_iso(row.event_at),
            "importance": int(row.importance or 0),
            "stars": list(row.stars) if row.stars is not None else None,
            "is_keepsake": bool(row.is_keepsake),
            "pinned_by": row.pinned_by,
            "last_accessed_at": to_utc_iso(row.last_accessed_at),
        }

    def _footer(
        self,
        *,
        episodes: int,
        dropped: int,
        body_tokens: int,
        rank_basis: str,
        missing_embedding: int = 0,
        embedding_excluded: int = 0,
        semantic_used: bool = False,
    ) -> str:
        """The recall footer (D2.10 / D2.11).

        It carries the counts, the rank basis, the embedding-identity
        disclosure (``active_model`` and ``embedding_excluded``) and the
        stats flag. When episodes were skipped for an identity/dims
        mismatch, a PARTIAL-RECALL note rides along — a silent partial
        recall is indistinguishable from a complete one.
        """
        kind = "memoir_recall" if rank_basis == "rrf" else "memoir_timeline"
        note = " note=partial_recall" if embedding_excluded > 0 else ""
        # The stats flag is DERIVED: a recall marks exactly the ids it
        # shipped (so a recall with no shipped episode marked nothing),
        # and a timeline never marks at all.
        stats_flag = rank_basis == "rrf" and episodes > 0
        return (
            f"[{kind}: episodes={episodes} dropped={dropped} "
            f"body_tokens={body_tokens} budget={MEMOIR_RECALL_TOKEN_BUDGET} "
            f"rank={rank_basis} semantic={'on' if semantic_used else 'off'} "
            f"missing_embedding={missing_embedding} "
            f"active_model={self.embedding_model or 'none'} "
            f"embedding_excluded={embedding_excluded} "
            f"stats_updated={'true' if stats_flag else 'false'}"
            f"{note}]"
        )
