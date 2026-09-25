"""Snapshot embedding service (PR5 — agent-snapshot v1).

Mirrors :mod:`daemon.services.skill_embedding_service` in SHAPE but
is decoupled from the Skill Evolution subsystem. Two motivations:

1. **Subsystem isolation.** The skill side is bound to
   :class:`~daemon.config.SkillEvolutionConfig` and
   :class:`~daemon.repositories.skill.repository.SkillEmbeddingRepository`,
   neither of which apply to snapshots. Wiring snapshot embeddings
   through the skill side would couple two unrelated subsystems at
   the storage and config layers.
2. **Drift-pinning.** The sibling-drift hazard the spec calls out
   (§3.4 rider) is specifically about the SEARCH ranking helpers
   (BM25, tokenize) — snapshot search IMPORTS those directly so a
   signature change in skill_search_service breaks snapshot tests.
   The embedding service has no such coupling: it owns its own
   trigger-query prompt template, its own OpenAI machinery, and a
   R10-compliant excerpt extractor for snapshot digests.

The vector math is the only thing shared: :meth:`cosine_similarity`
is a pure function on plain ``list[float]`` inputs (no numpy, per
``ensemble.spec``) — we re-export it from the skill embedding
service so the search rerank and the snapshot-side ranking
behave identically on the same math.

Embeddings-at-creation wiring (design §3.4 + R12 STAY-ALONGSIDE):

* **At capture completion**, :meth:`update_snapshot_embeddings` is
  called from :class:`~daemon.services.snapshot_executor.SnapshotExecutor._finish_row`
  — generates 3-10 trigger queries from
  ``task_summary + 1-2 digest excerpts``, embeds each via the
  OpenAI-compatible ``/embeddings`` endpoint, and writes each as a
  :class:`~daemon.repositories.snapshot.models.SnapshotEmbedding`
  row.
* **R12 supersession** mints fresh embeddings alongside the new
  row (the existing :meth:`daemon.repositories.snapshot.repository.SnapshotRepository.create_successor`
  accepts an ``embeddings`` parameter; the previous row's
  embeddings STAY ALONGSIDE — Q8-A no-eviction).
* **Q8-A no-eviction**: embeddings are removed only when their
  parent ``snapshots`` row is hard-deleted (the ``ON DELETE
  CASCADE`` FK on :attr:`SnapshotEmbedding.snapshot_id` is the
  only eviction path).

Design notes
------------

* **No numpy.** Per ``ensemble.spec`` the build excludes ``numpy``;
  the cosine math is pure Python.
* **Pure-Python BM25 (search side).** Lives in
  :mod:`daemon.services.skill_search_service` and is directly
  imported by :mod:`daemon.services.snapshot_search_service` (the
  drift-pin spec — see that module's docstring).
* **Best-effort failures.** :meth:`generate_trigger_queries`
  returns ``[]`` on any LLM/parse failure (callers fall back to
  "no cached embeddings" — search degrades to BM25+tag-overlap
  order). Per-query embedding failures are logged and skipped so
  a partial batch still produces usable rows.
* **Sync client behind ``asyncio.to_thread``.** Same pattern as
  the skill side — the underlying OpenAI SDK is synchronous.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import openai

from .llm_failover import current_failover_url, invoke_raw_with_failover
from .skill_embedding_service import SkillEmbeddingService

logger = logging.getLogger(__name__)


# Min/max trigger queries per snapshot (design §3.4 mirrors the
# skill-evolution 3-10 range so the embedding-cache footprint is
# comparable across subsystems).
_MIN_TRIGGER_QUERIES = 3
_MAX_TRIGGER_QUERIES = 10

# Defensive regexes for parsing the LLM response — mirrors the
# skill side so a chatty model that wraps JSON in fences or prose
# still produces a usable list.
_FENCED_JSON_RE = re.compile(
    r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL | re.IGNORECASE
)
_BARE_LIST_RE = re.compile(r"\[.*?\]", re.DOTALL)
_THINK_BLOCK_RE = re.compile(
    r"<think>.*?</think>", re.DOTALL | re.IGNORECASE
)


# ── Excerpt extraction (Rev 5 §3.4 — 1-2 excerpts, no per-node digests) ──

# Canonical R11 excerpt order — the first non-empty entry is taken.
# Task summary text first (strongest grounding signal), then the
# sections most likely to surface recurring-shape work.
_EXCERPT_ORDER: tuple[tuple[str, str], ...] = (
    ("task_summary_text", "text"),
    ("decisions", "list"),
    ("gotchas", "list"),
    ("worked_vs_wasted", "list"),
    ("conventions", "list"),
)


def _extract_digest_excerpts(
    digest: dict[str, Any] | None,
    *,
    max_excerpts: int = 2,
    max_chars_per_excerpt: int = 300,
) -> str:
    """Compose 1-2 short excerpts from the R11 digest (Rev 5 §3.4).

    The snapshot subsystem has NO per-node digests (the tree walk
    was deleted in Rev 5 P1) — the R11 8-tuple IS the digest. The
    excerpts ground the trigger-query prompt when ``task_summary``
    is thin or generic.

    Returns:
        Joined excerpt text (capped at 1500 chars total to mirror
        the skill-side excerpt budget), or empty string when the
        digest carries no usable signal.
    """
    if not isinstance(digest, dict):
        return ""
    out: list[str] = []
    for key, kind in _EXCERPT_ORDER:
        value = digest.get(key)
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, list) and kind == "list":
            text = " ".join(str(v) for v in value if v).strip()
        else:
            text = ""
        if text:
            out.append(text[:max_chars_per_excerpt])
        if len(out) >= max_excerpts:
            break
    return "\n".join(out)[:1500]


# ── Query parsing + cleaning (mirrors skill_embedding_service) ──


def _clean_queries(queries: list[Any]) -> list[str]:
    """Strip quotes / whitespace / out-of-band lengths from queries.

    Mirrors :func:`daemon.services.skill_embedding_service._clean_queries`
    — keeps queries in the ``3..200`` char band (anything outside is
    either too short to be a meaningful trigger or too long for an
    embedding-side user query).
    """
    cleaned: list[str] = []
    for q in queries or []:
        if not isinstance(q, str):
            continue
        q = q.strip().strip('"').strip("'").strip()
        if 3 <= len(q) <= 200:
            cleaned.append(q)
    return cleaned


def _try_parse_json_list(text: str) -> list[str] | None:
    """Parse ``text`` as a JSON array of strings; ``None`` on failure."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, list):
        return [str(x) for x in parsed]
    return None


def _parse_prose_list(text: str) -> list[str]:
    """Fallback parser — numbered / bulleted / quoted lists in prose."""
    candidates: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(
            r"^(?:\d+[\.\)]\s+|[-*]\s+|>\s+)(.+)$",
            line,
        )
        if m:
            candidates.append(m.group(1).strip().strip('"').strip("'"))
        elif line.startswith('"') and line.endswith('"') and len(line) > 2:
            candidates.append(line[1:-1].strip())
    return candidates


def _parse_trigger_queries(raw_text: str) -> list[str]:
    """Extract a clean list of trigger queries from the LLM response.

    Tries three parsers in order — mirrors the skill-side logic so
    the resilience profile is the same across subsystems:

    1. Markdown-fenced JSON block.
    2. Bare JSON array anywhere in the text.
    3. Prose-list fallback (numbered / bulleted / quoted).

    ``<think>...</think>`` blocks are stripped before parsing
    (chat-tuned models like DeepSeek/Qwen emit chain-of-thought
    there even when told to return only JSON).
    """
    if not raw_text or not raw_text.strip():
        return []
    text = _THINK_BLOCK_RE.sub("", raw_text)

    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        parsed = _try_parse_json_list(fenced.group(1))
        if parsed is not None:
            return _clean_queries(parsed)

    bare = _BARE_LIST_RE.search(text)
    if bare:
        parsed = _try_parse_json_list(bare.group(0))
        if parsed is not None:
            return _clean_queries(parsed)

    return _clean_queries(_parse_prose_list(text))


def _clamp_queries(queries: list[str]) -> list[str]:
    """Clamp to the 3-10 band; below the min returns what we have."""
    if not queries:
        return []
    if len(queries) < _MIN_TRIGGER_QUERIES:
        # Caller falls back to no cache when this is empty.
        return queries
    return queries[:_MAX_TRIGGER_QUERIES]


# ── OpenAI-compatible chat/embed calls ────────────────────────────────


def _do_chat_call(
    chat_model: str,
    chat_base_url: str | None,
    chat_api_key: str | None,
    system_prompt: str,
    user_prompt: str,
    *,
    http_client: Any | None = None,
) -> Any:
    """Run a chat completion via a fresh OpenAI-compatible client.

    Mirror of :func:`daemon.services.skill_embedding_service._do_chat_call`.
    Module-level helper so the HA facade can re-enter it on every
    retry attempt — the closure-pattern with late-bound defaults
    would risk capturing the wrong URL across retries.
    """
    url = current_failover_url() or chat_base_url
    client_kwargs: dict[str, Any] = {
        "api_key": chat_api_key or "",
        "base_url": url or None,
    }
    if http_client is not None:
        client_kwargs["http_client"] = http_client
    client = openai.OpenAI(**client_kwargs)
    return client.chat.completions.create(
        model=chat_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
    )


def _do_embed_call(
    embed_model: str,
    embed_base_url: str | None,
    embed_api_key: str | None,
    text: str,
    *,
    http_client: Any | None = None,
) -> Any:
    """Run an embedding call via a fresh OpenAI-compatible client."""
    url = current_failover_url() or embed_base_url
    client_kwargs: dict[str, Any] = {
        "api_key": embed_api_key or "",
        "base_url": url or None,
    }
    if http_client is not None:
        client_kwargs["http_client"] = http_client
    client = openai.OpenAI(**client_kwargs)
    return client.embeddings.create(model=embed_model, input=text)


def _extract_chat_content(response: Any) -> str:
    """Pull the textual content out of a chat completion response."""
    try:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        first = choices[0]
        message = getattr(first, "message", None)
        if message is None:
            return ""
        content = getattr(message, "content", "") or ""
        return str(content)
    except Exception:
        return ""


# ============================================================
# SnapshotEmbeddingService
# ============================================================


class SnapshotEmbeddingService:
    """Snapshot-side embedding service (PR5 — agent-snapshot v1).

    Three responsibilities, mirroring the skill-side shape:

    1. :meth:`generate_trigger_queries` — chat-completion call to
       produce 3-10 example user queries grounded in the
       snapshot's task_summary + 1-2 digest excerpts (Rev 5 §3.4).
    2. :meth:`embed_text` — embed a single string via the
       OpenAI-compatible ``/embeddings`` endpoint (reused by the
       search service to embed the user query for cosine rerank).
    3. :meth:`update_snapshot_embeddings` — full pipeline invoked
       from capture completion: generate + embed + persist.

    Attributes:
        config: Duck-typed config carrying ``embedding_model`` and
            optional ``embedding_base_url`` / ``embedding_api_key``
            overrides. Mirrors
            :class:`~daemon.config.SkillEvolutionConfig` surface.
        snapshot_repo: Duck-typed
            :class:`~daemon.repositories.snapshot.repository.SnapshotRepository`
            — methods ``add_embedding(snapshot_id, query, vec)``,
            ``get_embeddings(snapshot_id)``.
        llm_config: Dict with ``base_url``, ``api_key``, ``model``
            for the chat endpoint. Embedding calls fall back to
            these when the dedicated ``embedding_*`` overrides are
            unset.
    """

    def __init__(
        self,
        config: Any,
        snapshot_repo: Any,
        llm_config: dict[str, Any],
    ) -> None:
        """Store the config + repo + LLM defaults.

        Args:
            config: Duck-typed — accessed attributes:
                ``embedding_model``, ``embedding_base_url``,
                ``embedding_api_key``, ``embedding_dimensions``.
            snapshot_repo: Duck-typed
                :class:`SnapshotRepository`.
            llm_config: Chat-endpoint defaults (``base_url``,
                ``api_key``, ``model``).
        """
        self.config = config
        self.snapshot_repo = snapshot_repo
        self.llm_config = dict(llm_config) if llm_config else {}

    # --------------------------------------------------------
    # Vector math — re-export (snapshot ranking must match skill
    # ranking byte-for-byte on the same vectors; sharing the impl
    # is the cheapest way to guarantee that).
    # --------------------------------------------------------

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity — re-exports
        :meth:`SkillEmbeddingService.cosine_similarity`.

        Pure Python (no numpy); identical math to the skill side
        so the snapshot rerank and the skill rerank score the same
        way on the same vectors.
        """
        return SkillEmbeddingService.cosine_similarity(a, b)

    # --------------------------------------------------------
    # Trigger-query generation
    # --------------------------------------------------------

    async def generate_trigger_queries(self, snapshot: Any) -> list[str]:
        """Generate 3-10 example search queries for one snapshot.

        The prompt is grounded in the snapshot's ``title``,
        ``task_summary``, and 1-2 R11 digest excerpts (Rev 5 §3.4
        — no per-node digests, the tree walk is gone).

        Args:
            snapshot: A :class:`~daemon.repositories.snapshot.models.Snapshot`
                row. Accessed attributes: ``title``, ``task_summary``,
                ``digest``.

        Returns:
            Clean list of query strings (length clamped to the
            ``_MIN_TRIGGER_QUERIES.._MAX_TRIGGER_QUERIES`` band).
            Empty list on any LLM/parse failure — caller treats
            empty as "skip embedding refresh for this snapshot".
        """
        title = getattr(snapshot, "title", "") or "unnamed snapshot"
        task_summary = getattr(snapshot, "task_summary", "") or ""
        digest = getattr(snapshot, "digest", {}) or {}
        excerpts = _extract_digest_excerpts(digest)

        system_prompt = (
            "You are an assistant that generates realistic search "
            "queries for an agent-snapshot search system. Given a "
            "snapshot's metadata, produce a JSON array of between "
            "3 and 10 short user-style queries (each <= 200 "
            "characters) that would naturally retrieve this "
            "snapshot. Return ONLY a JSON array of strings. No "
            "prose, no markdown fences, no comments."
        )
        user_prompt = (
            f"Snapshot title: {title}\n"
            f"Snapshot task summary: {task_summary[:500]}\n"
            f"Snapshot digest excerpts:\n{excerpts}\n\n"
            "Return a JSON array of 3-10 example user queries that "
            "should retrieve this snapshot in a hybrid search."
        )

        try:
            chat_model = self._resolve_chat_model()
            chat_base_url = self._resolve_chat_base_url()
            chat_api_key = self._resolve_chat_api_key()

            chat_failover_config = {
                "base_url": chat_base_url,
                "base_url_backup": self.llm_config.get("base_url_backup"),
                "api_key": chat_api_key,
            }
            chat_callable = lambda: _do_chat_call(
                chat_model=chat_model,
                chat_base_url=chat_base_url,
                chat_api_key=chat_api_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            response = await asyncio.to_thread(
                invoke_raw_with_failover,
                chat_callable,
                chat_failover_config,
            )
            raw_text = _extract_chat_content(response)
            queries = _parse_trigger_queries(raw_text)
            return _clamp_queries(queries)
        except Exception as e:
            logger.warning(
                f"[SnapshotEmbedding] generate_trigger_queries "
                f"failed for snapshot={getattr(snapshot, 'id', '?')}: {e}"
            )
            return []

    # --------------------------------------------------------
    # Embeddings
    # --------------------------------------------------------

    async def embed_text(self, text: str) -> list[float]:
        """Embed ``text`` via the OpenAI-compatible ``/embeddings`` endpoint.

        Args:
            text: Query string. May be short (trigger phrase) or
                longer (full user message).

        Returns:
            Plain ``list[float]`` of length
            ``self.config.embedding_dimensions``.

        Raises:
            ValueError: empty / whitespace-only ``text``.
            RuntimeError: embedding API call failed or returned
                malformed response. The
                :meth:`update_snapshot_embeddings` pipeline catches
                this per-row.
        """
        base_url = self._resolve_embedding_base_url()
        api_key = self._resolve_embedding_api_key()
        model = getattr(self.config, "embedding_model", None) or "text-embedding-3-small"

        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")

        embed_failover_config = {
            "base_url": base_url,
            "base_url_backup": self.llm_config.get("base_url_backup"),
            "api_key": api_key,
        }
        embed_callable = lambda: _do_embed_call(
            embed_model=model,
            embed_base_url=base_url,
            embed_api_key=api_key,
            text=text,
        )
        response = await asyncio.to_thread(
            invoke_raw_with_failover,
            embed_callable,
            embed_failover_config,
        )
        data = getattr(response, "data", None) or []
        if not data:
            raise RuntimeError(
                "embedding API returned no data points "
                f"(model={model}, text_len={len(text)})"
            )
        first = data[0]
        embedding = getattr(first, "embedding", None)
        if not embedding:
            raise RuntimeError(
                "embedding API returned an empty embedding vector"
            )
        return [float(x) for x in embedding]

    # --------------------------------------------------------
    # Full pipeline (capture completion)
    # --------------------------------------------------------

    async def update_snapshot_embeddings(self, snapshot: Any) -> int:
        """Generate + embed + persist the trigger queries for one snapshot.

        Called from :meth:`~daemon.services.snapshot_executor.SnapshotExecutor._finish_row`
        on the capture-completion path. Best-effort: a partial batch
        (some queries fail to embed) still produces usable rows;
        a fully-failed pipeline returns ``0`` and the search
        degrades to BM25+tag-overlap order for that snapshot.

        The pipeline is IDEMPOTENT-ON-FIRST-RUN: this is a NEW
        row's embeddings (the snapshot was just created), so
        there are no stale rows to clear. On R12 supersession,
        the successor's embeddings land alongside the
        superseded header — STAY-ALONGSIDE semantics, no
        cascade-delete (Q8-A no-eviction).

        Args:
            snapshot: :class:`Snapshot` row — ``id``, ``title``,
                ``task_summary``, ``digest``.

        Returns:
            Number of embedding rows written (``0`` is a perfectly
            valid response — empty LLM response, parse failure,
            every embedding call failed, etc.).
        """
        snapshot_id = getattr(snapshot, "id", None)
        if not snapshot_id:
            logger.warning(
                "[SnapshotEmbedding] update_snapshot_embeddings called "
                "without snapshot.id"
            )
            return 0

        # 1. Generate trigger queries.
        queries = await self.generate_trigger_queries(snapshot)
        if not queries:
            logger.info(
                f"[SnapshotEmbedding] No trigger queries for snapshot "
                f"id={snapshot_id} — leaving cache empty"
            )
            return 0

        # 2. Embed + persist, skipping failures on a per-query basis.
        written = 0
        for query in queries:
            try:
                vector = await self.embed_text(query)
            except Exception as e:
                logger.warning(
                    f"[SnapshotEmbedding] Failed to embed query "
                    f"for snapshot id={snapshot_id}: {e!s}. "
                    f"Query: {query[:80]!r}"
                )
                continue

            try:
                await asyncio.to_thread(
                    self.snapshot_repo.add_embedding,
                    snapshot_id,
                    query,
                    vector,
                )
                written += 1
            except Exception as e:
                logger.warning(
                    f"[SnapshotEmbedding] Failed to persist embedding "
                    f"for snapshot id={snapshot_id}: {e!s}"
                )

        logger.info(
            f"[SnapshotEmbedding] Refreshed embeddings for snapshot "
            f"id={snapshot_id}: wrote={written}, queries={len(queries)}"
        )
        return written

    # --------------------------------------------------------
    # Config resolvers
    # --------------------------------------------------------

    def _resolve_chat_model(self) -> str:
        return (
            getattr(self.config, "chat_model", None)
            or self.llm_config.get("model")
            or "gpt-4o-mini"
        )

    def _resolve_chat_base_url(self) -> str | None:
        return (
            getattr(self.config, "chat_base_url", None)
            or self.llm_config.get("base_url")
        )

    def _resolve_chat_api_key(self) -> str | None:
        return (
            getattr(self.config, "chat_api_key", None)
            or self.llm_config.get("api_key")
        )

    def _resolve_embedding_base_url(self) -> str | None:
        return (
            getattr(self.config, "embedding_base_url", None)
            or self.llm_config.get("base_url")
        )

    def _resolve_embedding_api_key(self) -> str | None:
        return (
            getattr(self.config, "embedding_api_key", None)
            or self.llm_config.get("api_key")
        )


__all__ = [
    "SnapshotEmbeddingService",
    "_extract_digest_excerpts",
    "_clean_queries",
    "_parse_trigger_queries",
    "_clamp_queries",
    "_MIN_TRIGGER_QUERIES",
    "_MAX_TRIGGER_QUERIES",
]
