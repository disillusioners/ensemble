"""Skill embedding service for the Skill Evolution System.

Phase 2 of the Skill Evolution System. Generates per-skill trigger
query embeddings so the resolver can do a batched similarity scan
instead of an LLM embedding call per incoming task.

Workflow (one ``update_skill_embeddings`` call per skill):

1. Clear the skill's existing cached embeddings
   (:meth:`SkillEmbeddingRepository.delete_by_skill`).
2. Ask the configured chat model to emit 3-10 example user messages
   that should trigger this skill (:meth:`generate_trigger_queries`).
3. Embed each query via the OpenAI-compatible ``/embeddings``
   endpoint (:meth:`embed_text`).
4. Persist each ``(trigger_query, embedding)`` row via
   :meth:`SkillEmbeddingRepository.create`.

Design notes
------------

* **No numpy.** Per ``ensemble.spec`` the build excludes ``numpy``;
  all vector math is pure Python (see :meth:`cosine_similarity`).
  Embeddings are stored as plain JSON arrays of floats via
  :class:`~daemon.repositories.infra.types.JSONBType` — NOT BYTEA,
  NOT pickle.
* **OpenAI-compatible client.** Both the chat model (for generating
  trigger queries) and the embedding endpoint use the standard
  ``openai.OpenAI(api_key=..., base_url=...)`` client so the same
  code path works against OpenAI, vLLM, Ollama's OpenAI-compatible
  surface, or any drop-in OpenAI-compatible provider.
* **Endpoints fall back independently.** ``embedding_base_url`` /
  ``embedding_api_key`` override the LLM defaults only when set —
  the chat-client values come from the caller's ``llm_config``
  (``base_url``, ``api_key``, ``model``). Embedding endpoint falls
  back to ``llm_config.get("base_url")`` and
  ``llm_config.get("api_key")`` when its own override is ``None``.
* **Sync client behind ``asyncio.to_thread``.** The underlying
  OpenAI SDK is synchronous; we wrap calls in
  ``await asyncio.to_thread(...)`` so the service is async-callable
  without blocking the event loop.
* **Repository sync bridge.** :class:`SkillEmbeddingRepository`
  methods are synchronous (matching the Phase 1 repository
  contract); callers hop to the worker pool via
  ``asyncio.to_thread`` for DB-bound work.
* **Best-effort failures.** :meth:`generate_trigger_queries`
  returns ``[]`` if the LLM is unreachable or returns junk —
  callers fall back to no cached embeddings for that skill. An
  embedding API failure on a single query is logged and skipped so
  a partial batch still produces usable rows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from typing import Any
from urllib.parse import urlsplit

import openai

from .llm_failover import current_failover_url, invoke_raw_with_failover

logger = logging.getLogger(__name__)


def _normalize_endpoint_url(url: str | None) -> str:
    """Normalize an endpoint URL for equivalence comparison.

    Strips cosmetic differences that do NOT change the endpoint —
    trailing slash and scheme/host case — so the embedding guard
    compares apples to apples:

    * ``"https://x/v1"``    ≡ ``"https://x/v1/"``
    * ``"https://X/v1"``    ≡ ``"https://x/v1"``
    * ``"HTTPS://x:443/v1"`` ≡ ``"https://x:443/v1"`` (scheme case)

    The path itself is compared case-SENSITIVELY (paths can be
    case-sensitive on real servers). Port is preserved; userinfo
    is dropped (treated as equivalent). Empty / ``None`` inputs
    normalize to ``""``.

    Used ONLY for comparison — the original URL string is still
    passed to the client untouched.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        # Malformed URL — fall back to raw string comparison so the
        # guard stays conservative (treats unparseable as different).
        return url.strip()
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    # Note: ``parts.hostname`` is ALREADY lowercased by urlsplit, and
    # it strips brackets from IPv6 literals; the ``.lower()`` is
    # belt-and-braces for exotic inputs.
    netloc = host
    if parts.port is not None:
        netloc = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    # Query/fragment are dropped — they don't identify the endpoint.
    if scheme and netloc:
        return f"{scheme}://{netloc}{path}"
    # No scheme/host (e.g. a bare path or relative URL) — compare the
    # trimmed original so ``"x"`` != ``"/x"`` still holds.
    return f"{netloc}{path}" if netloc else path


def _do_chat_call(
    chat_model: str,
    chat_base_url: str | None,
    chat_api_key: str | None,
    system_prompt: str,
    user_prompt: str,
    *,
    http_client: Any | None = None,
) -> Any:
    """Construct a fresh ``openai.OpenAI`` client and run a chat-completion.

    Module-level helper (NOT a nested closure) so the HA facade can
    re-enter it on every retry attempt — the closure-pattern with
    late-bound defaults would risk capturing the wrong URL across
    retries. The URL is read at call time from
    :func:`current_failover_url` (a thread-local the facade
    updates per attempt) so each retry constructs the client
    against the correct endpoint.

    When failover is inactive, ``current_failover_url()`` returns
    ``None`` and we fall back to ``chat_base_url`` (the chat
    endpoint) — same behavior as pre-v2 (zero behavior change).

    The ``http_client`` kwarg is the seam for outbound request-body
    gzip compression (``OPENAI_REQUEST_GZIP=true``). When None
    (default), the openai client uses its built-in default httpx
    client — byte-identical to the pre-feature wire format. When
    a gzip-enabled client is provided (built by
    ``daemon.services.llm_gzip.make_gzip_httpx_client``), every
    request body the client sends is gzip-compressed and
    ``Content-Encoding: gzip`` is stamped on the wire.
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
    """Construct a fresh ``openai.OpenAI`` client and run an embedding call.

    Module-level helper for the embeddings path. Mirror of
    :func:`_do_chat_call`; same per-attempt URL re-read pattern via
    :func:`current_failover_url`. See the embedding-endpoint guard in
    ``daemon.services.llm_failover.invoke_raw_with_failover``:
    embedding failover is only correct when ``embed_base_url`` is the
    chat endpoint (i.e. ``config.embedding_base_url`` is unset and
    the chat ``base_url`` is used). Sites configure this via
    ``llm_config["base_url_backup"]`` plumbed through from
    ``daemon/manager.py``.

    The ``http_client`` kwarg is the seam for outbound request-body
    gzip compression (``OPENAI_REQUEST_GZIP=true``). When None
    (default), the openai client constructs its own built-in
    default httpx client (the openai SDK's ``DefaultHttpxClient``)
    — no gzip wrapping, no ``Content-Encoding`` header, no
    transport mutation. Embedding request bodies are tiny (one
    short string) so the savings are small — but the gzip transport
    is a no-op on bodies that don't shrink under compression, so
    it's free to apply uniformly.
    """
    url = current_failover_url() or embed_base_url
    client_kwargs: dict[str, Any] = {
        "api_key": embed_api_key or "",
        "base_url": url or None,
    }
    if http_client is not None:
        client_kwargs["http_client"] = http_client
    client = openai.OpenAI(**client_kwargs)
    return client.embeddings.create(model=embed_model, input=text)


# Min/max trigger queries per skill. The LLM is asked to produce
# "3-10"; we clamp the parsed output to this band so a chatty model
# can't blow up the embedding cache.
_MIN_TRIGGER_QUERIES = 3
_MAX_TRIGGER_QUERIES = 10

# Defensive regexes for parsing the LLM response. The chat model is
# asked to return a JSON array; these extract that array even when
# the model wraps it in markdown fences (````json [...]``) or
# surrounding prose.
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL | re.IGNORECASE)
_BARE_LIST_RE = re.compile(r"\[.*?\]", re.DOTALL)
_NUMBERED_ITEM_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.+)$")
_QUOTED_RE = re.compile(r"\"([^\"\\]*(?:\\.[^\"\\]*)*)\"|'([^'\\]*(?:\\.[^'\\]*)*)'")


# ============================================================
# SkillEmbeddingService
# ============================================================


class SkillEmbeddingService:
    """Service for generating and managing skill embeddings.

    Calls the OpenAI-compatible ``/embeddings`` endpoint directly.
    Generates 3-10 example trigger queries per skill via LLM,
    then embeds each query and stores in ``skill_embeddings`` table.

    The constructor takes the embedding-specific configuration
    (:class:`~daemon.config.SkillEvolutionConfig`), the Phase 1
    repository for ``skill_embeddings``, and the LLM defaults
    ``llm_config`` dict (``base_url``, ``api_key``, ``model`` …).
    All three are stored as attributes so callers can introspect
    or override in tests.

    Attributes:
        config: :class:`~daemon.config.SkillEvolutionConfig`. The
            embedding model name, dimensions, and per-endpoint
            ``base_url`` / ``api_key`` overrides live here.
        embedding_repo: Sync :class:`SkillEmbeddingRepository`
            used for all DB reads/writes against the
            ``skill_embeddings`` table.
        llm_config: Dict with the chat-model defaults — typically
            ``{"base_url": ..., "api_key": ..., "model": ...}``.
            Embedding calls fall back to ``llm_config["base_url"]``
            and ``llm_config["api_key"]`` when
            ``config.embedding_base_url`` /
            ``config.embedding_api_key`` are unset.
    """

    def __init__(
        self,
        config: Any,
        embedding_repo: Any,
        llm_config: dict[str, Any],
    ) -> None:
        """Store the configuration and dependencies.

        Args:
            config: :class:`~daemon.config.SkillEvolutionConfig`.
                Owns ``embedding_model``, ``embedding_base_url``,
                ``embedding_api_key``, etc.
            embedding_repo: :class:`SkillEmbeddingRepository`
                instance bound to the project's SQLAlchemy engine.
            llm_config: Dict with at least ``base_url`` and
                ``api_key`` (and typically ``model``). Used as the
                fallback for embedding calls when the dedicated
                ``embedding_*`` overrides are unset, and as the
                primary source for chat-completion calls.
        """
        self.config = config
        self.embedding_repo = embedding_repo
        self.llm_config = dict(llm_config)  # Defensive shallow copy

    # --------------------------------------------------------
    # Trigger-query generation
    # --------------------------------------------------------

    async def generate_trigger_queries(self, skill: Any) -> list[str]:
        """Generate 3-10 example user messages that would trigger this skill.

        Sends a chat completion to the configured model asking for
        realistic user prompts that match the skill, then parses
        the response into a clean list of strings. Defensive against
        common LLM quirks: JSON in markdown fences, lists in prose
        form, numbered lists, etc.

        Returns an empty list on any failure (LLM unreachable,
        malformed response, no usable queries). Callers should treat
        an empty list as "skip embedding refresh for this skill".

        Args:
            skill: A :class:`~daemon.repositories.skill.models.Skill`
                instance. Accessed attributes: ``name`` (str),
                ``description`` (str). The body content is also
                included so the model can ground its examples in
                the actual instructions.

        Returns:
            List of trigger-query strings. Length is bounded by
            :data:`_MIN_TRIGGER_QUERIES` ≤ len ≤ :data:`_MAX_TRIGGER_QUERIES`.
            Empty list on any failure to produce / parse the result.
        """
        name = getattr(skill, "name", "") or "unnamed skill"
        description = getattr(skill, "description", "") or ""
        content = getattr(skill, "content", "") or ""

        system_prompt = (
            "You are an assistant that generates realistic example user "
            "messages. Given a skill's metadata, produce a JSON array of "
            "between 3 and 10 short user messages (each <= 200 characters) "
            "that would naturally trigger this skill. Return ONLY a JSON "
            "array of strings. No prose, no markdown fences, no comments."
        )
        user_prompt = (
            f"Skill name: {name}\n"
            f"Skill description: {description}\n"
            f"Skill content (excerpt): {content[:1500]}\n\n"
            "Return a JSON array of 3-10 example user messages that would "
            "trigger this skill."
        )

        try:
            chat_model = self._resolve_chat_model()
            chat_base_url = self._resolve_chat_base_url()
            chat_api_key = self._resolve_chat_api_key()

            # v2 HA: wrap the raw ``openai.OpenAI`` SDK call in the
            # shared HA facade. ``invoke_raw_with_failover`` builds a
            # tenacity retry pipeline with the v1 budget-split
            # predicate (transient + timeout + IndexError-gated-on-backup).
            # When ``base_url_backup`` is unset the facade is a no-op over
            # the same shape the LangChain facade uses — zero behavior
            # change. The factory is re-entered on every retry attempt;
            # each entry reads the current target URL via
            # ``current_failover_url()`` so a swap is observable as a
            # change in the URL passed to ``openai.OpenAI(...)``.
            #
            # Opt-in outbound request-body gzip compression — see
            # ``daemon.services.llm_gzip.resolve_gzip_client`` for the
            # full rationale (singleton reuse + early-return on the
            # disabled path keeps flag-OFF behavior byte-identical to
            # the pre-feature state).
            from .llm_gzip import resolve_gzip_client

            chat_failover_config = {
                "base_url": chat_base_url,
                "base_url_backup": self.llm_config.get("base_url_backup"),
                "api_key": chat_api_key,
            }
            gzip_http_client = resolve_gzip_client(
                bool(self.llm_config.get("request_gzip"))
            )
            chat_callable = lambda: _do_chat_call(
                chat_model=chat_model,
                chat_base_url=chat_base_url,
                chat_api_key=chat_api_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                http_client=gzip_http_client,
            )
            response = await asyncio.to_thread(
                invoke_raw_with_failover,
                chat_callable,
                chat_failover_config,
            )
            raw_text = self._extract_chat_content(response)
            queries = self._parse_trigger_queries(raw_text)
            return self._clamp_queries(queries)
        except Exception as e:
            logger.warning(
                f"[SkillEmbedding] generate_trigger_queries failed "
                f"for skill={getattr(skill, 'id', '?')}: {e}"
            )
            return []

    # --------------------------------------------------------
    # Embeddings
    # --------------------------------------------------------

    async def embed_text(self, text: str) -> list[float]:
        """Embed ``text`` via the OpenAI-compatible ``/embeddings`` endpoint.

        Endpoint resolution: uses ``config.embedding_base_url`` /
        ``config.embedding_api_key`` when set, otherwise falls
        back to ``llm_config["base_url"]`` /
        ``llm_config["api_key"]``. Model name comes from
        ``config.embedding_model``.

        Returns the embedding as a plain Python ``list[float]`` —
        the same shape stored in the JSONB ``embedding`` column
        of :class:`~daemon.repositories.skill.models.SkillEmbedding`.
        No numpy, no bytes, no pickled blob.

        Args:
            text: The query string to embed. May be short
                (a trigger phrase) or longer (a full user
                message) — the embedding model handles both.

        Returns:
            A plain ``list[float]`` of length
            ``config.embedding_dimensions``.

        Raises:
            RuntimeError: If the embedding API call fails or
                returns an empty / malformed response. The
                :meth:`update_skill_embeddings` pipeline catches
                this per-row so a single failure doesn't abort the
                whole batch.
        """
        base_url = self._resolve_embedding_base_url()
        api_key = self._resolve_embedding_api_key()
        model = self.config.embedding_model

        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")

        # Opt-in outbound request-body gzip compression — see
        # ``daemon.services.llm_gzip.resolve_gzip_client`` for the
        # full rationale. Embedding request bodies are small (one
        # short string); the gzip transport is a no-op on bodies
        # that don't shrink under compression. The variable is
        # declared here (BEFORE ``_call_embed`` below) so the closure
        # captures it eagerly instead of relying on Python's late
        # binding — same shape as the chat path.
        from .llm_gzip import resolve_gzip_client

        gzip_embed_client = resolve_gzip_client(
            bool(self.llm_config.get("request_gzip"))
        )

        def _call_embed() -> Any:
            return _do_embed_call(
                embed_model=model,
                embed_base_url=base_url,
                embed_api_key=api_key,
                text=text,
                http_client=gzip_embed_client,
            )

        try:
            # v2 HA: wrap in the shared raw-SDK facade. Embedding
            # failover is correct ONLY when ``embed_base_url`` is the
            # chat endpoint (see :func:`_do_embed_call` docstring and
            # ``daemon.services.llm_failover.invoke_raw_with_failover``).
            embed_backup = self.llm_config.get("base_url_backup")
            # Embedding-endpoint guard: an explicit
            # ``embedding_base_url`` that DIFFERS from the chat
            # ``base_url`` means the chat backup is the wrong
            # endpoint for embedding calls — a swap would hit a
            # different API with different creds and possibly a
            # different model. Short-circuit: drop the backup so
            # the facade skips failover entirely on this path (the
            # call retries on the embedding endpoint only).
            # BOTH sides are normalized (trailing slash, scheme/host
            # case) before comparing so equivalent spellings of the
            # SAME endpoint ("https://x/v1" vs "https://x/v1/")
            # don't silently disable failover. The failure direction
            # stays conservative: a GENUINELY different endpoint
            # still drops the backup.
            embedding_override = getattr(self.config, "embedding_base_url", None)
            if (
                embedding_override
                and _normalize_endpoint_url(embedding_override)
                != _normalize_endpoint_url(self.llm_config.get("base_url"))
            ):
                embed_backup = None
            embed_failover_config = {
                "base_url": base_url,
                "base_url_backup": embed_backup,
                "api_key": api_key,
            }
            # ``gzip_embed_client`` is resolved eagerly above (before
            # the ``_call_embed`` closure definition) so the closure
            # captures it directly — no late-binding surprise.
            embed_callable = lambda: _call_embed()
            response = await asyncio.to_thread(
                invoke_raw_with_failover,
                embed_callable,
                embed_failover_config,
            )
        except Exception as e:
            raise RuntimeError(
                f"Embedding API call failed: {e}"
            ) from e

        # Defensive parsing — different SDK versions expose the
        # data field differently (``data`` is the documented
        # attribute, but a future refactor could shift to
        # ``.embedding``). Handle both shapes.
        data = getattr(response, "data", None)
        if not data:
            raise RuntimeError(
                f"Embedding API returned empty data: {response!r}"
            )
        first = data[0]
        # Newer OpenAI SDK: ``item.embedding`` is a list[float].
        # Older or wrapped SDK: ``item.embedding`` may be a
        # different attribute name — fall back to common names.
        vector = getattr(first, "embedding", None) or getattr(
            first, "vector", None
        )
        if vector is None:
            raise RuntimeError(
                f"Embedding API returned no vector: {first!r}"
            )
        return [float(x) for x in vector]

    async def embed_user_message(self, message: str) -> list[float]:
        """Embed a user message for similarity search.

        Thin wrapper around :meth:`embed_text` — kept as a separate
        method so call-sites are explicit about intent (this is a
        runtime query to compare against cached skill embeddings,
        not a bulk-cache embed of a static trigger phrase).

        Args:
            message: The incoming user message to embed.

        Returns:
            A plain ``list[float]`` of length
            ``config.embedding_dimensions``.

        Raises:
            RuntimeError: If the embedding API call fails.
            ValueError: If ``message`` is empty.
        """
        return await self.embed_text(message)

    # --------------------------------------------------------
    # Pipeline
    # --------------------------------------------------------

    async def update_skill_embeddings(self, skill: Any) -> int:
        """Refresh the embedding cache for a skill end-to-end.

        Full pipeline:

        1. Delete any existing cached embeddings for the skill
           (:meth:`SkillEmbeddingRepository.delete_by_skill`).
        2. Generate 3-10 trigger queries via
           :meth:`generate_trigger_queries`.
        3. Embed each query via :meth:`embed_text` (a single
           embedding failure does not abort the batch — that
           query is skipped).
        4. Persist each successful ``(trigger_query, embedding)``
           row via
           :meth:`SkillEmbeddingRepository.create`.

        Returns the count of cached embeddings written. ``0`` is a
        perfectly valid response — for example, if the skill has
        an empty description and the LLM declined to generate
        triggers, or every embedding call failed.

        Args:
            skill: A :class:`~daemon.repositories.skill.models.Skill`
                instance. Accessed attributes: ``id``,
                ``name``, ``description``, ``content``.

        Returns:
            Number of embeddings written to the ``skill_embeddings``
            table. ``0`` if generation or embedding failed.
        """
        skill_id = getattr(skill, "id", None)
        if not skill_id:
            logger.warning(
                "[SkillEmbedding] update_skill_embeddings called "
                "without skill.id"
            )
            return 0

        # Step 1: clear the existing cache.
        await asyncio.to_thread(
            self.embedding_repo.delete_by_skill, skill_id
        )

        # Step 2: generate trigger queries.
        queries = await self.generate_trigger_queries(skill)
        if not queries:
            logger.info(
                f"[SkillEmbedding] No trigger queries for skill "
                f"id={skill_id} — leaving cache empty"
            )
            return 0

        # Steps 3+4: embed + persist, skipping failures on a
        # per-query basis so a single API error doesn't wipe out
        # the whole batch.
        written = 0
        for query in queries:
            try:
                vector = await self.embed_text(query)
            except Exception as e:
                logger.warning(
                    f"[SkillEmbedding] Failed to embed query "
                    f"for skill id={skill_id}: {e!s}. Query: "
                    f"{query[:80]!r}"
                )
                continue

            try:
                await asyncio.to_thread(
                    self.embedding_repo.create,
                    skill_id,
                    query,
                    vector,
                )
                written += 1
            except Exception as e:
                logger.warning(
                    f"[SkillEmbedding] Failed to persist embedding "
                    f"for skill id={skill_id}: {e!s}"
                )

        logger.info(
            f"[SkillEmbedding] Refreshed embeddings for skill "
            f"id={skill_id}: wrote={written}, queries={len(queries)}"
        )
        return written

    # --------------------------------------------------------
    # Vector math
    # --------------------------------------------------------

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Return the cosine similarity between two float vectors.

        Pure Python — uses :func:`math.sqrt` and the builtin
        :func:`sum` so it runs without ``numpy`` (which the build
        excludes per ``ensemble.spec``). Returns ``0.0`` when
        either input is the zero vector (avoids a divide-by-zero).

        Args:
            a: First vector. May be empty.
            b: Second vector. Must be the same length as ``a``
                — caller is responsible for padding / truncating.
                Mismatched lengths silently zip to the shorter
                side (Python ``zip`` semantics), which is
                acceptable for similarity ranking where exact
                alignment isn't critical.

        Returns:
            Cosine similarity, in range ``[-1.0, 1.0]``
            (``0.0`` for any zero-vector input).
        """
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # --------------------------------------------------------
    # Internal helpers
    # --------------------------------------------------------

    def _resolve_chat_model(self) -> str:
        """Return the chat-model name to use for trigger-query generation.

        Prefers ``llm_config['model']`` (the project default LLM);
        falls back to ``config.embedding_model`` if the caller
        didn't pass one.
        """
        return (
            self.llm_config.get("model")
            or getattr(self.config, "embedding_model", None)
            or "gpt-4o-mini"
        )

    def _resolve_chat_base_url(self) -> str | None:
        """Return the chat endpoint's ``base_url`` (no embedding override)."""
        return self.llm_config.get("base_url")

    def _resolve_chat_api_key(self) -> str | None:
        """Return the chat endpoint's ``api_key`` (no embedding override)."""
        return self.llm_config.get("api_key")

    def _resolve_embedding_base_url(self) -> str | None:
        """Resolve ``config.embedding_base_url`` → ``llm_config.base_url`` → None."""
        override = getattr(self.config, "embedding_base_url", None)
        if override:
            return override
        return self.llm_config.get("base_url")

    def _resolve_embedding_api_key(self) -> str | None:
        """Resolve ``config.embedding_api_key`` → ``llm_config.api_key`` → None."""
        override = getattr(self.config, "embedding_api_key", None)
        if override:
            return override
        return self.llm_config.get("api_key")

    @staticmethod
    def _extract_chat_content(response: Any) -> str:
        """Extract the textual content from a chat-completion response.

        Handles both the common ``response.choices[0].message.content``
        shape and the less common ``response.choices[0].text`` shape
        (legacy / proxy surfaces). Strips ``<think>...</think>`` blocks
        from chat-tuned models (DeepSeek, Qwen, GLM) so they don't
        pollute the JSON parser.
        """
        content = ""
        try:
            choices = getattr(response, "choices", None) or []
            if not choices:
                return ""
            first = choices[0]
            # Standard ChatCompletion shape.
            message = getattr(first, "message", None)
            if message is not None:
                content = getattr(message, "content", "") or ""
            else:
                # Legacy / proxy shape.
                content = getattr(first, "text", "") or ""
        except Exception:
            return ""

        if isinstance(content, list):
            # Some providers return a list of content blocks
            # (``[{"type": "text", "text": "..."}]``). Flatten.
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if text:
                        parts.append(str(text))
                else:
                    parts.append(str(block))
            content = " ".join(parts)

        return _THINK_BLOCK_RE.sub("", str(content or ""))

    @staticmethod
    def _parse_trigger_queries(raw_text: str) -> list[str]:
        """Extract a clean list of trigger queries from the LLM response.

        Tries three parsers in order:

        1. Markdown-fenced JSON block (``\\`\\`\\`json [...]\\`\\`\\` ``).
        2. Bare JSON array anywhere in the text.
        3. Fallback parser: numbered / bulleted list, or quoted
           strings (covers models that ignore the JSON instruction
           and reply in prose).

        Returns an empty list when nothing usable comes back —
        callers fall back to "no cached embeddings" gracefully.
        """
        if not raw_text or not raw_text.strip():
            return []

        # Parser 1: fenced JSON block.
        fenced = _FENCED_JSON_RE.search(raw_text)
        candidates: list[str] | None = None
        if fenced:
            candidates = _try_parse_json_list(fenced.group(1))
        if candidates is not None:
            return _clean_queries(candidates)

        # Parser 2: bare JSON array.
        bare = _BARE_LIST_RE.search(raw_text)
        if bare:
            candidates = _try_parse_json_list(bare.group(0))
        if candidates is not None:
            return _clean_queries(candidates)

        # Parser 3: numbered / bulleted / quoted fallback.
        return _clean_queries(_parse_prose_list(raw_text))

    @staticmethod
    def _clamp_queries(queries: list[str]) -> list[str]:
        """Clamp the parsed query list to the spec's 3-10 range.

        If we got fewer than the minimum, just return what we have
        (caller will treat as "no cache" if it's empty). If we got
        more than the maximum, truncate.
        """
        if not queries:
            return []
        if len(queries) < _MIN_TRIGGER_QUERIES:
            # Don't refuse the result — even 1-2 queries is better
            # than no cache. Caller can re-run the evolution pipeline
            # to regenerate.
            return queries
        return queries[:_MAX_TRIGGER_QUERIES]


# ============================================================
# Module-level helpers
# ============================================================


# Strip ``<think>...</think>`` reasoning blocks. Chat-tuned models
# (DeepSeek, Qwen, GLM, …) emit chain-of-thought inside these tags
# even when told to return only JSON — the thinking is noise to us
# but parsing it as JSON would yield garbage.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _try_parse_json_list(text: str) -> list[str] | None:
    """Try to parse ``text`` as a JSON array of strings.

    Returns ``None`` (not an exception) on any parse failure so
    the caller can move on to the next parser in the chain.
    """
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    # Coerce each entry to a stripped string; drop non-string entries.
    out: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                out.append(stripped)
        elif isinstance(item, (int, float)):
            out.append(str(item))
    return out


def _parse_prose_list(text: str) -> list[str]:
    """Best-effort parser for a prose / numbered / quoted trigger list.

    Tries (in order):

    * Numbered / bulleted list — each line whose leading
      ``1.``, ``2)``, ``-``, ``*``, ``•`` is stripped down to its
      body.
    * Quoted terms — if the response contains 2+ quoted strings,
      those are taken as the implicit list (a common LLM
      "default to JSON-looking output" failure mode).

    Returns an empty list if neither path yields content.
    """
    out: list[str] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _NUMBERED_ITEM_RE.match(line)
        if m:
            candidate = m.group(1).strip()
            # Strip matching quotes if present.
            candidate = candidate.strip().strip('"').strip("'").strip()
            if candidate:
                out.append(candidate)

    if out:
        return out

    quoted = _QUOTED_RE.findall(text)
    if quoted:
        out = [a or b for a, b in quoted]
        out = [q.strip() for q in out if q.strip()]
    return out


def _clean_queries(queries: list[str]) -> list[str]:
    """Strip whitespace, drop blanks, dedupe case-insensitively.

    Preserves the first-seen casing of each entry.
    """
    seen_lower: set[str] = set()
    out: list[str] = []
    for q in queries:
        cleaned = q.strip()
        if not cleaned:
            continue
        lower = cleaned.lower()
        if lower in seen_lower:
            continue
        seen_lower.add(lower)
        out.append(cleaned)
    return out
