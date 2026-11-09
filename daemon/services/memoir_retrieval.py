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

import logging
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

logger = logging.getLogger(__name__)

#: The ONE spelling of the "semantic ranker did not run" reason tokens
#: shipped in the footer (``semantic_reason=...``). The footer token is
#: the agent-visible half of the same fact the WARNING logs (tidier H7 /
#: AM-33 footer extension): a degraded recall must never be
#: indistinguishable from a complete one.
SEMANTIC_REASON_NONE = "none"
SEMANTIC_REASON_NO_EMBEDDER = "no_embedder"
SEMANTIC_REASON_TIMEOUT = "timeout"
SEMANTIC_REASON_DIMS_MISMATCH = "embedding_query_dims_mismatch"

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

#: The DUAL-RECALL syntheses cap (V2-D 2.4 / §3.3): how many active
#: beliefs the recall envelope may carry. The syntheses are the HEAD of
#: the envelope, so the cap is small by design — a recall that returned
#: fifty beliefs would be a ledger, not a memory.
MEMOIR_RECALL_MAX_SYNTHESES = 5

#: The header of the syntheses section (§3.3).
MEMOIR_SYNTHESES_SECTION_HEADER = "--- SYNTHESES ---"

#: The header that separates the episodes from the beliefs (only
#: rendered when a syntheses section is present).
MEMOIR_EPISODES_SECTION_HEADER = "--- EPISODES ---"
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
    #: The DUAL-RECALL arm (V2-D 2.4 / §3.3): the active syntheses that
    #: matched the query, strongest first. EMPTY in the v1 lane (and in
    #: the reflection lane when it is OFF), so the v1 surface is
    #: byte-identical.
    syntheses: list[dict[str, Any]] = field(default_factory=list)
    #: How many syntheses the envelope DROPPED for budget (the weakest
    #: tail is dropped first). Zero in the v1 lane.
    syntheses_dropped: int = 0


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


def render_synthesis(synthesis: dict[str, Any]) -> str:
    """The SHIPPED text of ONE synthesis (V2-D 2.4 / §3.3).

    The shape is ``[synthesis:ID] (verb, confidence=…) claim`` followed
    by the ``sources:`` line — the belief first (what she believes),
    then its provenance (how she came to believe it). The provenance is
    not decoration: V2-6 makes the citations mandatory, and an
    uncited belief is a rumour.
    """
    confidence = synthesis.get("confidence")
    confidence_text = (
        f"{float(confidence):.2f}" if confidence is not None else "n/a"
    )
    sources = synthesis.get("source_episode_ids") or []
    sources_text = ", ".join(str(item) for item in sources)
    return "\n".join(
        [
            f"[synthesis:{synthesis.get('id')}] "
            f"({synthesis.get('verb')}, confidence={confidence_text}) "
            f"{synthesis.get('claim') or ''}",
            f"  sources: {sources_text}",
        ]
    )


def render_syntheses_section(syntheses: Sequence[dict[str, Any]]) -> str:
    """The syntheses SECTION — the head of the dual-recall envelope.

    The section is OMITTED entirely when there is nothing to say: a
    recall with zero matching beliefs carries no ``--- SYNTHESES ---``
    header at all (the footer still reports ``syntheses=0``).
    """
    if not syntheses:
        return ""
    blocks = [MEMOIR_SYNTHESES_SECTION_HEADER]
    blocks.extend(render_synthesis(item) for item in syntheses)
    return "\n".join(blocks)


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
        syntheses_repository: Any = None,
        reflection_enabled: Callable[[], bool] | None = None,
        constellation_repository: Any = None,
    ) -> None:
        self.repository = repository
        self._config = config
        #: THE DUAL-RECALL ARM (V2-D 2.4 / §3.3). The repository is
        #: OPTIONAL: without it (or without the reflection lane being
        #: ON) the service produces BYTE-IDENTICAL v1 output — no
        #: syntheses section, no footer token.
        self._syntheses_repository = syntheses_repository
        #: The lane check — a CALLABLE, so the knob is read per recall
        #: (the house call-time read) and never cached.
        self._reflection_enabled = reflection_enabled
        #: The constellation repository — the source of the star heat
        #: increment (W5).
        self._constellation_repository = constellation_repository
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
        #: Per-recall reason token for the footer (AM-33 extension). One
        #: of the ``SEMANTIC_REASON_*`` values, or "" before the first
        #: read. Reset at the top of every public read.
        self._semantic_reason: str = ""
        # ONE-SHOT construction INFO naming the MISSING ARM (tidier H7):
        # a lexical-only service is a configured steady state, not a
        # surprise — but it must be visible without reading logs per
        # recall. Never a WARNING here (the per-read WARNING covers
        # actual degradations).
        if self.embedder is None:
            logger.info(
                "[MemoirRecall] semantic lane OFF — missing arm=%s "
                "(recall runs lexical-only)",
                self._missing_semantic_arm(),
            )

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
        self._embed_call_failed = False
        self._semantic_reason = ""
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

        semantic, embedding_excluded = self._semantic_pool(rows, clean_query)
        missing_embedding = self._count_missing_embeddings(lexical, by_id)

        fused = fuse_rankings(lexical, semantic)
        ranked = self._rank(fused, by_id)

        # THE DUAL-RECALL ARM (§3.3). The syntheses are the HEAD of the
        # envelope, so they are queried FIRST — but they only exist when
        # the reflection lane is ON. With the lane OFF (or without a
        # syntheses repository) the list is EMPTY and the whole v1 path
        # below is byte-identical.
        syntheses = self._match_syntheses(agent_id, clean_query)

        shipped, dropped, body_tokens, footer, total_tokens, kept_syntheses, syntheses_dropped = (
            self._apply_budget(
                ranked,
                rank_basis="rrf",
                missing_embedding=missing_embedding,
                embedding_excluded=embedding_excluded,
                semantic_used=self._semantic_enabled() and not self._embed_call_failed,
                count=count,
                syntheses=syntheses,
            )
        )
        rendered_ids = [episode["id"] for episode in shipped]

        # THE STATS: exactly the SHIPPED ids (D2.8 / AM-14). The episodes
        # dropped for budget are NOT returned and must NOT be marked.
        marked_ids: list[str] = []
        stats_updated = False
        if rendered_ids:
            # Tidier M18: the STATS write is bookkeeping, the RECALL is
            # the product. A failed ``mark_recalled`` (a locked table, a
            # vanished row) must not destroy a read that already
            # succeeded — guard it, keep the computed result, and say so
            # in the log. The shipped footer was measured BEFORE the
            # write (it must be, to price the envelope), so it keeps the
            # pre-write flag; the RESULT fields below are the truth.
            try:
                self.repository.mark_recalled(rendered_ids, now=self._now_fn())
                marked_ids = list(rendered_ids)
                stats_updated = True
            except Exception as exc:  # noqa: BLE001 — never lose the recall
                logger.warning(
                    "[MemoirRecall] stats_write_failed:%s — the recall "
                    "result is kept; the access stats were NOT updated",
                    type(exc).__name__,
                )
                marked_ids = []
                stats_updated = False

        # THE HEAT INCREMENT (W5): an explicit recall hit is an act of
        # attention — the stars the RETURNED episodes cite get their heat
        # incremented. It is a bookkeeping write: a failure NEVER
        # destroys the recall (the same rule as the stats write above).
        self._increment_star_heat(agent_id, shipped)

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
            syntheses=kept_syntheses,
            syntheses_dropped=syntheses_dropped,
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
        self._embed_call_failed = False
        self._semantic_reason = ""
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
        shipped, dropped, body_tokens, footer, total_tokens, _kept, _dropped = (
            self._apply_budget(
                ranked,
                rank_basis="timeline_lexical" if (q or "").strip() else "timeline",
            )
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

    def _missing_semantic_arm(self) -> str:
        """WHICH arm of the default-embedder resolution is missing (H7).

        Named tokens (never prose) so the log line is greppable:
        ``no_config`` / ``no_api_key`` / ``no_base_url`` /
        ``no_model_or_dims``. Only called when ``self.embedder is None``.
        """
        if self._config is None:
            return "no_config"
        if not self._resolve_default_api_key():
            return "no_api_key"
        if not self._resolve_default_base_url():
            return "no_base_url"
        return "no_model_or_dims"

    @staticmethod
    def _degrade_reason(exc: BaseException) -> str:
        """The per-read degrade reason token for a raised embed call (H7).

        ``timeout`` for any timeout-shaped failure (the house SDK raises
        ``APITimeoutError``; ``asyncio.TimeoutError`` is a builtin
        ``TimeoutError`` alias) and the exception CLASS NAME otherwise —
        a token, never a message, so the footer and the log agree.
        """
        if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
            return SEMANTIC_REASON_TIMEOUT
        return type(exc).__name__ or "unknown_error"

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
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[MemoirRecall] no embedding model resolvable "
                "(reason=%s) — the semantic lane stays off",
                type(exc).__name__,
            )
            return None

    def _resolve_default_embedding_dims(self) -> int | None:
        """Resolve the active embedding dimensionality (mirrors the worker)."""
        import os

        env_value = (os.environ.get("ENSEMBLE_MEMOIR_EMBEDDING_DIMENSIONS") or "").strip()
        if env_value:
            try:
                return int(env_value)
            except ValueError:
                logger.warning(
                    "[MemoirRecall] ENSEMBLE_MEMOIR_EMBEDDING_DIMENSIONS="
                    "%r is not an integer — falling back to the config tier",
                    env_value,
                )
        memoir_cfg = getattr(self._config, "memoir", None)
        if memoir_cfg is not None:
            dims = getattr(memoir_cfg, "embedding_dimensions", None)
            if dims:
                try:
                    return int(dims)
                except (TypeError, ValueError):
                    logger.warning(
                        "[MemoirRecall] memoir.embedding_dimensions=%r is not "
                        "an integer — falling back to the class default",
                        dims,
                    )
        # Fall back to the worker's default (1536).
        try:
            from daemon.config import MemoirConfig

            return int(MemoirConfig.model_fields["embedding_dimensions"].default)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[MemoirRecall] MemoirConfig default dimensions unresolvable "
                "(reason=%s) — keeping 1536",
                type(exc).__name__,
            )
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
            # A recall served lexical-only is DEGRADED, and the degrade
            # must be visible in the log AND in the footer (H7). The
            # construction INFO already named the missing arm once.
            self._semantic_reason = SEMANTIC_REASON_NO_EMBEDDER
            logger.warning(
                "[MemoirRecall] semantic ranker DEGRADED reason=%s — "
                "this recall is lexical-only",
                SEMANTIC_REASON_NO_EMBEDDER,
            )
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
        except Exception as exc:  # noqa: BLE001 — degrade to lexical-only
            self._embed_call_failed = True
            reason = self._degrade_reason(exc)
            self._semantic_reason = reason
            logger.warning(
                "[MemoirRecall] semantic ranker DEGRADED reason=%s (%s) — "
                "this recall is lexical-only",
                reason,
                type(exc).__name__,
            )
            return [], excluded

        # ── H8: the query vector's DIMENSIONS must match the active
        # identity. The house cosine zips the shorter sequence, so a
        # wrong-length vector silently scores against a PREFIX of every
        # row embedding — silent corruption, not a crash. Degrade to
        # lexical-only and say why.
        if dims and len(query_vector) != dims:
            self._embed_call_failed = True
            self._semantic_reason = SEMANTIC_REASON_DIMS_MISMATCH
            logger.warning(
                "[MemoirRecall] embedding_query_dims_mismatch expected=%d "
                "got=%d — this recall is lexical-only",
                dims,
                len(query_vector),
            )
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
        syntheses: Sequence[dict[str, Any]] | None = None,
    ) -> tuple[
        list[dict[str, Any]],
        int,
        int,
        str,
        int,
        list[dict[str, Any]],
        int,
    ]:
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

        THE DUAL-RECALL EXTENSION (§3.3): when ``syntheses`` is present
        the section is the HEAD of the envelope, so it is measured FIRST
        and the episodes get what is left. The tail-drop ORDER is
        load-bearing: the EPISODES are dropped FIRST and only then is
        the WEAKEST synthesis dropped (the syntheses are ordered
        strongest-first, so the tail is the weakest).

        THE v1 PATH IS UNTOUCHED: with no syntheses the arithmetic is
        identical to the v1 service and the footer carries no
        ``syntheses=`` token — the envelope is byte-identical.
        """
        candidates: list[Any] = list(ranked)
        if count is not None:
            candidates = candidates[:count]
        # ── the syntheses head ─────────────────────────────────────────
        kept_syntheses = list(syntheses or [])
        syntheses_dropped = 0
        # The footer's ``syntheses=`` token rides the LANE, not the match
        # count: with the lane ON and zero matches the footer reports
        # ``syntheses=0``; with the lane OFF there is no token at all
        # (the v1 footer is byte-identical).
        synth_count_token: int | None = None
        if syntheses is not None:
            synth_count_token = len(kept_syntheses)

        synth_text = render_syntheses_section(kept_syntheses)
        synth_tokens = _estimate_tokens(synth_text) if synth_text else 0

        shipped: list[dict[str, Any]] = []
        shipped_texts: list[str] = []
        body_tokens = 0
        for row in candidates:
            projection = self._project(row)
            text = render_episode(projection)
            tokens = _estimate_tokens(text)
            if synth_tokens + body_tokens + tokens > MEMOIR_RECALL_TOKEN_BUDGET:
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
            semantic_reason=self._semantic_reason,
            syntheses_count=synth_count_token,
        )
        total_tokens = synth_tokens + body_tokens + _estimate_tokens(footer)

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
                semantic_reason=self._semantic_reason,
                syntheses_count=synth_count_token,
            )
            total_tokens = synth_tokens + body_tokens + _estimate_tokens(footer)

        # THE SYNTHESES TAIL-DROP: the episodes are gone and the envelope
        # is STILL over — now the WEAKEST synthesis goes (the list is
        # strongest-first, so the tail is the weakest). The footer is
        # re-measured after every drop.
        while (
            total_tokens > MEMOIR_RECALL_TOKEN_BUDGET
            and kept_syntheses
        ):
            kept_syntheses.pop()
            syntheses_dropped += 1
            synth_count_token = len(kept_syntheses)
            synth_text = render_syntheses_section(kept_syntheses)
            synth_tokens = _estimate_tokens(synth_text) if synth_text else 0
            footer = self._footer(
                episodes=len(shipped),
                dropped=dropped,
                body_tokens=body_tokens,
                rank_basis=rank_basis,
                missing_embedding=missing_embedding,
                embedding_excluded=embedding_excluded,
                semantic_used=semantic_used,
                semantic_reason=self._semantic_reason,
                syntheses_count=synth_count_token,
            )
            total_tokens = synth_tokens + body_tokens + _estimate_tokens(footer)

        return (
            shipped,
            dropped,
            body_tokens,
            footer,
            total_tokens,
            kept_syntheses,
            syntheses_dropped,
        )

    # ── the dual-recall arm (§3.3) ──────────────────────────────────────

    def _match_syntheses(
        self, agent_id: str, query: str
    ) -> list[dict[str, Any]] | None:
        """The matching ACTIVE syntheses — or ``None`` when the lane is OFF.

        ``None`` means "the dual-recall lane is not in play": the caller
        then produces BYTE-IDENTICAL v1 output — no syntheses section
        and no ``syntheses=`` footer token. An EMPTY LIST means "the lane
        is ON and nothing matched": the footer reports ``syntheses=0``
        and the section is omitted.

        THE LANE DECIDES THE TOKEN, NOT THE MATCH COUNT: with the lane ON
        the footer always carries ``syntheses=M`` (M may be 0), and with
        the lane OFF it carries no token at all. A service that is built
        WITHOUT a syntheses repository therefore reports ``syntheses=0``
        — honest ("no beliefs were surfaced") and CONSISTENT with a
        fully wired service, which is what keeps the v1 byte-pin
        (the tool's footer vs a directly built service's footer) stable
        across the lane.

        The lane check is a CALL-TIME read (the house shape): flipping
        the environment between two recalls changes the answer without a
        restart.
        """
        if not self._lane_enabled():
            return None
        if self._syntheses_repository is None:
            return []
        try:
            rows = self._syntheses_repository.search_active(
                agent_id, query, MEMOIR_RECALL_MAX_SYNTHESES
            )
        except Exception as exc:  # noqa: BLE001 — never lose the recall
            logger.warning(
                "[MemoirRecall] the syntheses lookup failed (%s) — the "
                "recall continues without the beliefs",
                type(exc).__name__,
            )
            return []
        return [
            {
                "id": row.id,
                "claim": row.claim,
                "verb": row.verb,
                "confidence": row.confidence,
                "source_episode_ids": list(row.source_episode_ids or []),
            }
            for row in rows
        ]

    def _lane_enabled(self) -> bool:
        """Is the DUAL-RECALL lane ON? (a call-time read, never cached)

        The injected callable wins (the tool layer passes the knob); with
        no callable the SHARED knob authority decides — the same source
        the embedding worker reads.
        """
        if self._reflection_enabled is not None:
            try:
                return bool(self._reflection_enabled())
            except Exception as exc:  # noqa: BLE001 — a knob never breaks a read
                logger.warning(
                    "[MemoirRecall] the reflection lane check failed (%s) — "
                    "the recall runs in its v1 shape",
                    type(exc).__name__,
                )
                return False
        try:
            # The knob module is imported LAZILY: the tool layer imports
            # this module, so a module-level import would close a cycle
            # (``daemon.tools.__init__`` -> ... -> ``daemon.tools.memoir``
            # -> this module).
            from daemon.tools.memoir_knob import resolve_memoir_reflection_knob

            return bool(resolve_memoir_reflection_knob(self._config).enabled)
        except Exception as exc:  # noqa: BLE001 — a knob never breaks a read
            logger.warning(
                "[MemoirRecall] the reflection knob could not be resolved "
                "(%s) — the recall runs in its v1 shape",
                type(exc).__name__,
            )
            return False

    def _increment_star_heat(
        self, agent_id: str, shipped: Sequence[dict[str, Any]]
    ) -> None:
        """THE HEAT INCREMENT (W5): an explicit recall hit is attention.

        Every star cited by a RETURNED episode gets its stored heat
        incremented through
        :meth:`MemoirConstellationRepository.increment_heat`.

        A star that does not exist (or a repository that is not wired)
        NEVER breaks the recall — the heat is a bookkeeping write, not
        the product (the same rule as the access-stats write).
        """
        if self._constellation_repository is None or not shipped:
            return
        names: list[str] = []
        for episode in shipped:
            for name in episode.get("stars") or []:
                text = str(name).strip()
                if text and text not in names:
                    names.append(text)
        if not names:
            return
        for name in names:
            try:
                star = self._constellation_repository.resolve_star(agent_id, name)
                if star is None:
                    continue
                self._constellation_repository.increment_heat(agent_id, star.id)
            except Exception as exc:  # noqa: BLE001 — never lose the recall
                logger.warning(
                    "[MemoirRecall] the heat increment failed for %r (%s) — "
                    "the recall result is kept",
                    name,
                    type(exc).__name__,
                )

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
        semantic_reason: str = "",
        syntheses_count: int | None = None,
    ) -> str:
        """The recall footer (D2.10 / D2.11).

        It carries the counts, the rank basis, the embedding-identity
        disclosure (``active_model`` and ``embedding_excluded``) and the
        stats flag. When episodes were skipped for an identity/dims
        mismatch, a PARTIAL-RECALL note rides along — a silent partial
        recall is indistinguishable from a complete one.

        THE ``syntheses=`` TOKEN (V2-D 2.4 / §3.3): the token appears
        ONLY when the dual-recall lane is ON (``syntheses_count`` is not
        ``None``). With the lane OFF the footer is BYTE-IDENTICAL to the
        v1 footer — the v1 envelope is a frozen surface and a token that
        appears and disappears with a feature flag would make every
        consumer's byte-pin unstable.
        """
        kind = "memoir_recall" if rank_basis == "rrf" else "memoir_timeline"
        note = " note=partial_recall" if embedding_excluded > 0 else ""
        # The stats flag is DERIVED: a recall marks exactly the ids it
        # shipped (so a recall with no shipped episode marked nothing),
        # and a timeline never marks at all.
        stats_flag = rank_basis == "rrf" and episodes > 0
        synth_token = (
            "" if syntheses_count is None else f" syntheses={int(syntheses_count)}"
        )
        return (
            f"[{kind}: episodes={episodes} dropped={dropped} "
            f"body_tokens={body_tokens} budget={MEMOIR_RECALL_TOKEN_BUDGET} "
            f"rank={rank_basis} semantic={'on' if semantic_used else 'off'} "
            f"semantic_reason={semantic_reason or SEMANTIC_REASON_NONE} "
            f"missing_embedding={missing_embedding} "
            f"active_model={self.embedding_model or 'none'} "
            f"embedding_excluded={embedding_excluded} "
            f"stats_updated={'true' if stats_flag else 'false'}"
            f"{synth_token}"
            f"{note}]"
        )
