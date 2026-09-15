"""Critical-notes embedding service (Phase 2, critical-notes-retrieval).

Thin wrapper that reuses the established embedding recipe
(``daemon.services.skill_embedding_service._do_embed_call`` and
``SkillEmbeddingService.embed_text``) for the ``critical_note_embeddings``
side table. The contract is identical to the skill path: best-effort,
fail-open, async ``embed_user_message`` shape.

Three call-sites:

* **Write-time embed** — :func:`daemon.tools.critical_notes.add_critical_note`
  triggers a best-effort embed of ``summary + reference[:800]`` after
  committing the note. The note write is NEVER blocked by an embed
  failure (the note ranks BM25-only until a lazy-mint covers it).
* **Lazy mint cap window** — ``assemble_context_messages`` first-turn
  path triggers up to ``mint_cap_per_read`` lazy mints on notes that
  predate Phase 2 and have no cached row yet. The cap keeps the
  first-token latency budget bounded.
* **Explicit backfill** — a one-shot maintenance command processes
  every note in the store once (idempotent — ``WHERE embedding IS
  NULL`` semantics at the caller's level; cap un-scoped because
  the operator runs it deliberately). ~15s for ~50 notes at the
  recipe's measured 250ms/embed budget.

Design notes
------------

* **No numpy.** Per the build spec the daemon excludes ``numpy``;
  vector math goes through :meth:`SkillEmbeddingService.cosine_similarity`.
* **Cross-driver storage.** Embeddings persist as plain JSON float
  arrays via the ``JSONBType`` adapter (PG JSONB / SQLite TEXT-JSON).
* **Pure function, async-first.** :func:`embed_critical_note_text`
  is the canonical shape (always called from an async context);
  sync callers bridge via ``asyncio.run``.
* **Engine-aware construction.** :func:`build_critical_notes_embedder`
  builds a real :class:`SkillEmbeddingService` bound to a project
  engine — the lazy-mint window requires this so the cached
  vector lands on the same SQLAlchemy engine that hosts the note.
"""

from __future__ import annotations

import logging
from typing import Any

from daemon.services.skill_embedding_service import (
    SkillEmbeddingService,
)


logger = logging.getLogger(__name__)


# Default embedding model name (D4: NO ``ENSEMBLE_*`` env var).
# Matches the skill-evolution default; Phase-2/3 reuse. When a
# future CriticalNotesConfig.embedding_model knob lands it can
# override via the explicit ``model=`` kwarg.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# Reference bound for the embed input (§4.2): ``summary + reference[:800]``.
# Mirrors the skill-evolution precedent; ``reference`` is already
# tool-bounded at 500 (``reference_max``) so ``[:800]`` always covers
# the full tool-written text — the 800 cap is the future-proof headroom
# for legacy pre-bound rows (defensive bound matches the architecture
# recommendation).
REFERENCE_EMBED_PREVIEW = 800


async def embed_critical_note_text(
    text: str,
    *,
    embedding_service: Any | None = None,
    model: str | None = None,
) -> list[float] | None:
    """Embed ``text`` for a critical note, or return ``None`` on failure.

    Best-effort contract (§4.2): callers (write-time path, lazy
    mint, backfill) MUST treat ``None`` as "skip — BM25-only".
    The function never raises so the hot write path stays silent;
    every failure mode logs a single INFO line at this site under
    the ``[CriticalNotes:Degraded]`` prefix so ops can alert on
    the prefix alone (architecture-recommendation §4.6 [#10]).

    Args:
        text: The text to embed (``summary + reference[:800]``).
        embedding_service: Optional pre-built
            :class:`SkillEmbeddingService` to reuse.
        model: Override for the embedding model name
            (default ``DEFAULT_EMBEDDING_MODEL``).

    Returns:
        The embedding as a plain ``list[float]``, or ``None``
        on any failure.
    """
    if not text or not text.strip():
        return None

    chosen_model = model or DEFAULT_EMBEDDING_MODEL

    if embedding_service is None:
        embedding_service = build_critical_notes_embedder(model=chosen_model)
        if embedding_service is None:
            return None

    try:
        vector = await embedding_service.embed_text(text)
    except Exception as e:
        logger.info(
            "[CriticalNotes:Degraded] stage=embed reason=api_failure "
            "model=%s err=%s",
            chosen_model,
            type(e).__name__,
        )
        return None

    if not vector:
        logger.info(
            "[CriticalNotes:Degraded] stage=embed reason=empty_vector "
            "model=%s",
            chosen_model,
        )
        return None

    return [float(x) for x in vector]


def make_embed_input(summary: str, reference: str | None) -> str:
    """Build the embed input string for a critical note (§4.2).

    Shape: ``summary + reference[:REFERENCE_EMBED_PREVIEW]``. The
    bounded ``reference`` slice aligns with the architecture
    recommendation; the per-row markdown header (``detail_ref``)
    is intentionally excluded — the ``detail_ref`` is unbounded
    list-read text only, never used in the vector path.

    Args:
        summary: The note's primary summary text (already
            tool-bounded at ``_MAX_SUMMARY_LEN = 200``).
        reference: Optional reference URL/path text (already
            tool-bounded at ``_MAX_REFERENCE_LEN = 500``).

    Returns:
        The concatenated text the embedder sees — empty string
        when both inputs are empty / None.
    """
    parts: list[str] = []
    if summary:
        parts.append(summary.strip())
    if reference and reference.strip():
        ref_slice = reference.strip()
        if len(ref_slice) > REFERENCE_EMBED_PREVIEW:
            ref_slice = ref_slice[:REFERENCE_EMBED_PREVIEW]
        parts.append(ref_slice)
    return "\n".join(parts)


# ─── Internal helpers ──────────────────────────────────────────────────────────


def resolve_critical_notes_embedding_model() -> str:
    """Return the embedding model name the critical-notes pipeline uses.

    Resolution mirrors :func:`build_critical_notes_embedder`: the
    env-resolved :class:`SkillEvolutionConfig.embedding_model` when a
    config object can be built, else ``DEFAULT_EMBEDDING_MODEL``.
    Mint sites stamp THIS value (not a hardcoded literal) so the
    ``critical_note_embeddings.model`` audit column records the model
    the embed call actually used.
    """
    try:
        from daemon.config import SkillEvolutionConfig

        return (
            SkillEvolutionConfig().embedding_model or DEFAULT_EMBEDDING_MODEL
        )
    except Exception:
        return DEFAULT_EMBEDDING_MODEL


def build_critical_notes_embedder(
    *, model: str, engine: Any | None = None
) -> Any | None:
    """Construct a :class:`SkillEmbeddingService` for the critical-notes path.

    The skill-evolution plumbing expects a ``SkillEmbeddingRepository``
    bound to an engine; lazy-mint + write-time paths run OUTSIDE
    the spawn loop so this builds a transient service bound to the
    project's engine when available.

    Args:
        model: Embedding model name (audit value).
        engine: The SQLAlchemy engine that hosts the note tables
            (``SQLModelProjectRepository.engine``). REQUIRED in
            practice — callers pass ``repo.engine`` explicitly
            (B1 fix: the previous lazy fallback imported
            ``get_default_engine`` from the nonexistent
            ``daemon.services.persistence`` module, so engineless
            calls ALWAYS degraded to ``no_engine`` and no vector
            was ever minted). ``None`` logs ``no_engine`` and
            returns ``None`` so callers degrade gracefully.

    Returns:
        A :class:`SkillEmbeddingService` ready to call
        ``embed_text`` on, or ``None`` when the plumbing cannot be
        resolved.
    """
    try:
        from daemon.repositories.skill.repository import (
            SkillEmbeddingRepository,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.info(
            "[CriticalNotes:Degraded] stage=embed reason=service_unavailable "
            "err=%s",
            type(e).__name__,
        )
        return None

    # B1: engine is caller-supplied, period. The previous fallback
    # (``from daemon.services.persistence import get_default_engine``)
    # referenced a module that does not exist, so the except branch
    # swallowed a ModuleNotFoundError on EVERY call and the whole
    # pipeline silently went BM25-only.
    if engine is None:
        logger.info(
            "[CriticalNotes:Degraded] stage=embed reason=no_engine"
        )
        return None

    # B1 (same dead-pipeline class): the service MUST carry a real
    # embedding config — ``SkillEmbeddingService.embed_text`` reads
    # ``self.config.embedding_model`` unconditionally, so the previous
    # ``config=None`` made every real embed call raise AttributeError
    # inside ``embed_text`` (absorbed as ``api_failure`` → None vector
    # → permanent BM25-only even with a live engine). A default
    # ``SkillEvolutionConfig`` resolves ``embedding_model`` (+
    # ``EMBEDDING_*`` env overrides) exactly like the skill path.
    llm_dict: dict[str, Any] = {"model": model}
    try:
        from daemon.config import SkillEvolutionConfig

        service_config: Any = SkillEvolutionConfig()
        llm_dict.update(
            {
                "base_url": getattr(service_config, "embedding_base_url", None),
                "api_key": getattr(service_config, "embedding_api_key", None),
                "model": (
                    getattr(service_config, "embedding_model", None) or model
                ),
            }
        )
    except Exception as e:
        # Config construction failing means the embedding model name
        # itself is unresolvable — degrade (callers treat None as
        # "skip — BM25-only", same contract as every other rung).
        logger.info(
            "[CriticalNotes:Degraded] stage=embed reason=service_unavailable "
            "err=%s",
            type(e).__name__,
        )
        return None

    try:
        repo = SkillEmbeddingRepository(engine)
        return SkillEmbeddingService(
            config=service_config,
            embedding_repo=repo,
            llm_config=llm_dict,
        )
    except Exception as e:
        logger.info(
            "[CriticalNotes:Degraded] stage=embed reason=service_unavailable "
            "err=%s",
            type(e).__name__,
        )
        return None
