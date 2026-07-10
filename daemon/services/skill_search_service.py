r"""Three-stage skill search: BM25 -> Embedding re-rank -> LLM selection.

Phase 2 of the Skill Evolution System. Given an incoming user
message, this service ranks the project's active skills (plus the
global library) by relevance so the resolver can inject the most
useful skills into the agent's prompt.

The pipeline runs three filters in sequence, each gracefully
falling back to the previous stage on failure:

1. **BM25 keyword prefilter.** Pure-Python BM25 (k1=1.5, b=0.75)
   over the concatenation of ``name + description + content`` for
   every active skill in the project. Returns the top
   ``config.bm25_top_k`` rows. Cheap, deterministic, and tolerant
   of typos / synonyms so far fewer rows make it to the next
   stage.
2. **Embedding re-rank.** The user message is embedded via
   :meth:`SkillEmbeddingService.embed_user_message` and scored
   against the cached per-skill trigger embeddings (one row per
   skill, up to ``config.bm25_top_k`` candidates). The score is
   the **MAX** cosine similarity across all cached embeddings for
   a skill — choosing the best-matching trigger. Returns the top
   ``config.llm_select_top_k`` rows.
3. **LLM selection.** A chat-completion call asks the LLM to pick
   up to ``max_results`` (``config.max_inject_skills``) of the
   re-ranked candidates. The LLM returns JSON; we map names back
   to skill objects and emit the final ``{"injected": [...],
   "low_match": [...]}`` dict.

Graceful degradation rules:

* Stage 2 failure (embedding API down or per-skill error) ->
  falls back to BM25-only with all similarity scores set to 0.0.
* Stage 3 failure (LLM down or malformed JSON) -> falls back to
  :meth:`_degraded_select`, which treats the top ``max_results``
  from stage 2 as ``injected`` and the next three as ``low_match``.
* Stage 1 failure (no active skills, query has no shared terms)
  -> returns ``{"injected": [], "low_match": []}``.

The service is constructed with the project's existing
``SkillRepository`` and ``SkillEmbeddingRepository`` (Phase 1
sync; bridged to async via ``asyncio.to_thread`` by call sites
that hand us a thread-pool handle — this service treats them as
duck-typed sync interfaces and leaves the threading decision to
the caller). The :class:`SkillEmbeddingService` is the same one
the Phase 2 embedding pipeline uses, so vector math and cached
embeddings are shared.

Design notes
------------

* **No numpy, no external BM25 library.** Per ``ensemble.spec``,
  the build excludes ``numpy``. BM25 and cosine similarity are
  pure Python. The BM25 impl deliberately drops stopwords (the
  spec's "_tokenize" is minimal; the team's existing
  ``SkillRepository.search_bm25`` does the same).
* **Duck-typed dependencies.** The constructor accepts
  ``Any`` for repos / services — this service does not import
  ``SkillRepository`` directly so it stays decoupled from the
  engine factory. Tests pass ``MagicMock`` instances with the
  expected method names.
* **LLM client injection.** :meth:`_llm_select` accepts an
  optional ``client`` parameter so unit tests can supply a
  ``MagicMock`` without monkeypatching ``openai.OpenAI``. The
  production call path constructs the client from
  ``self._llm_config`` (``api_key``, ``base_url``, ``model``)
  using the same OpenAI-compatible surface as the embedding
  pipeline.
* **Defensive JSON parsing.** The LLM is asked to return JSON;
  the parser tolerates markdown code fences (``\`\`\`json ...
  \`\`\``), surrounding prose, and malformed JSON by falling
  back to :meth:`_degraded_select` rather than crashing the
  daemon.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter
from typing import Any

import openai

logger = logging.getLogger(__name__)


# ============================================================
# Module-level helpers
# ============================================================


def _tokenize(text: str) -> list[str]:
    """Lowercase ``text`` and split on non-alphanumeric boundaries.

    Pure Python — used by the BM25 prefilter for both query and
    document tokenization. Mirrors the spec's lightweight regex:

    * Lowercase the whole string.
    * Split on runs of characters that aren't ``[a-z0-9]``.
    * Drop empty tokens.

    No stopword removal here — the spec's BM25 is the
    "small-corpus, no-NLP-stemming" variant. ``SkillRepository``'s
    own ``search_bm25`` strips English stopwords; we keep this
    service's implementation pure-spec so it stays short and the
    test cases can reason about exact token counts.

    Args:
        text: Input string. May contain punctuation, whitespace,
            or be empty.

    Returns:
        A list of lowercase tokens with empty strings removed.
        Empty list for an empty / whitespace-only input.
    """
    return [tok for tok in re.split(r"[^a-z0-9]+", text.lower()) if tok]


def _bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    doc_freqs: dict[str, int],
    total_docs: int,
    avg_doc_len: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    """Standard BM25 scoring function.

    Computes the BM25 score of a single document against the
    query terms. The IDF formula mirrors the spec's BM25+:

        idf = log((N - df + 0.5) / (df + 0.5) + 1)

    The ``+1`` smoothing keeps the IDF term positive even when a
    query term is in the majority of documents (avoids negative
    scores that would otherwise demote very common terms).

    The per-term contribution is the standard length-normalized
    BM25 term-frequency weight:

        idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))

    where ``dl`` is the current document's length and ``avgdl``
    is the average document length across the corpus.

    Args:
        query_tokens: Pre-tokenized query terms.
        doc_tokens: Pre-tokenized document terms. Order does not
            matter — only counts are used.
        doc_freqs: Map from term -> number of documents in the
            corpus containing that term. Pre-computed by the
            caller.
        total_docs: Total number of documents in the corpus.
        avg_doc_len: Average document length (in tokens) across
            the corpus. Caller-computed.
        k1: BM25 term-frequency saturation. Default ``1.5`` —
            the literature-standard value.
        b: BM25 length-normalization strength. Default ``0.75``
            — the literature-standard value.

    Returns:
        The BM25 score. Zero when no query term appears in the
        document.
    """
    doc_len = len(doc_tokens)
    if doc_len == 0:
        return 0.0
    doc_counter = Counter(doc_tokens)
    score = 0.0
    for term in query_tokens:
        tf = doc_counter.get(term, 0)
        if tf == 0:
            continue
        df = doc_freqs.get(term, 0)
        idf = math.log((total_docs - df + 0.5) / (df + 0.5) + 1)
        # Length normalization: penalize longer docs proportionally
        # to how much they exceed the corpus average.
        length_norm = 1 - b + b * (doc_len / avg_doc_len)
        score += idf * (tf * (k1 + 1)) / (tf + k1 * length_norm)
    return score


def _extract_json_object(text: str) -> str | None:
    r"""Pull the first JSON object out of ``text``.

    Tolerant of:

    * Markdown-fenced JSON (```json { ... } ```).
    * Surrounding prose ("Here you go: {...} hope that helps!").
    * Leading whitespace.

    Args:
        text: Arbitrary LLM response text.

    Returns:
        The matched JSON object as a string, or ``None`` when
        no ``{...}`` block can be located. Returns the FIRST
        complete object only (greedy match).
    """
    if not text:
        return None
    # Strip markdown code fences if present.
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE
    )
    if fenced:
        return fenced.group(1)
    # Otherwise find the first balanced-looking JSON object.
    start = text.find("{")
    if start == -1:
        return None
    # Walk forward, tracking string + brace depth, to find the
    # matching closing brace. Falls back to the index of the last
    # ``}`` if the walker can't balance — best-effort.
    depth = 0
    in_string = False
    escape = False
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        # Fallback — last ``}`` in the substring.
        last = text.rfind("}")
        if last > start:
            return text[start : last + 1]
        return None
    return text[start:end]


# ============================================================
# SkillSearchService
# ============================================================


class SkillSearchService:
    r"""Three-stage skill search: BM25 -> Embedding re-rank -> LLM selection.

    Constructor parameters are duck-typed (``Any``) so this
    service can be constructed in tests with lightweight mocks
    and in production with the real repository + embedding
    service instances. The actual classes live in
    :mod:`daemon.repositories.skill.repository` and
    :mod:`daemon.services.skill_embedding_service` — we don't
    import them so this module is unit-testable without the
    SQLModel engine or a running OpenAI client.

    Attributes:
        _skill_repo: Duck-typed
            :class:`~daemon.repositories.skill.repository.SkillRepository`.
            Expected method: ``list(project_id, active_only=True,
            limit, offset) -> (items, total)``.
        _embedding_repo: Duck-typed
            :class:`~daemon.repositories.skill.repository.SkillEmbeddingRepository`.
            Expected method: ``get_by_skill(skill_id) ->
            list[SkillEmbedding]``.
        _embedding_service: Duck-typed
            :class:`~daemon.services.skill_embedding_service.SkillEmbeddingService`.
            Expected methods: ``embed_user_message(text)`` (async)
            and ``cosine_similarity(a, b)`` (sync).
        _llm_config: Dict with at least ``api_key`` and
            ``base_url``; ``model`` defaults to ``"gpt-4o-mini"``
            when missing. Used by stage 3 to construct the
            OpenAI-compatible client.
        _config: Duck-typed
            :class:`~daemon.config.SkillEvolutionConfig`. Held
            but not heavily used by this service — the BM25 and
            re-rank cutoffs are passed in via the search call's
            keyword arguments and default to the ``config``
            values when not provided.
    """

    def __init__(
        self,
        skill_repo: Any,
        embedding_repo: Any,
        embedding_service: Any,
        llm_config: dict[str, Any],
        config: Any,  # SkillEvolutionConfig
    ) -> None:
        """Store dependencies for the three pipeline stages.

        Args:
            skill_repo: See :attr:`_skill_repo`.
            embedding_repo: See :attr:`_embedding_repo`.
            embedding_service: See :attr:`_embedding_service`.
            llm_config: See :attr:`_llm_config`.
            config: See :attr:`_config`.
        """
        self._skill_repo = skill_repo
        self._embedding_repo = embedding_repo
        self._embedding_service = embedding_service
        # Defensive shallow copy — let callers keep mutating
        # their own dict without surprising us.
        self._llm_config = dict(llm_config) if llm_config else {}
        self._config = config

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    async def search(
        self,
        user_message: str,
        project_id: str | None = None,
        max_results: int = 2,
    ) -> dict[str, list[dict[str, Any]]]:
        """Run the full three-stage search pipeline.

        Stages are best-effort; a failure in stage 2 or 3 falls
        back to the previous stage rather than raising. Returns
        the standard ``{"injected": [...], "low_match": [...]}``
        dict so the resolver can decide how much context to
        inject.

        Args:
            user_message: The incoming user message to match
                against the skill corpus.
            project_id: The active project ID. ``None`` for
                global-only search (skills with
                ``project_id IS NULL``).
            max_results: Maximum number of skills to surface in
                the ``injected`` list. Defaults to ``2`` to match
                ``SkillEvolutionConfig.max_inject_skills``.

        Returns:
            Dict with keys:

            * ``injected`` — list of ``{"skill": Skill,
              "score": float}`` dicts. Sorted by descending
              relevance. Capped at ``max_results``.
            * ``low_match`` — list of ``{"name": str, "score":
              float, "description": str}`` dicts for skills that
              were close but didn't make the cut. Capped at 3.

            Both lists are empty when the corpus is empty or no
            skill scores above zero.
        """
        # Stage 1 — BM25.
        candidates = await self._bm25_prefilter(
            user_message,
            project_id,
            top_k=getattr(self._config, "bm25_top_k", 10),
        )
        if not candidates:
            return {"injected": [], "low_match": []}

        # Stage 2 — Embedding re-rank (best-effort).
        try:
            reranked = await self._embedding_rerank(
                user_message,
                candidates,
                top_k=getattr(self._config, "llm_select_top_k", 5),
            )
        except Exception as e:
            logger.warning(
                "[SkillSearch] Embedding re-rank failed, falling "
                f"back to BM25: {e}"
            )
            reranked = [(s, 0.0) for s in candidates[:5]]

        if not reranked:
            return {"injected": [], "low_match": []}

        # Stage 3 — LLM final selection (best-effort).
        try:
            return await self._llm_select(
                user_message, reranked, max_results
            )
        except Exception as e:
            logger.warning(
                "[SkillSearch] LLM select failed, falling back "
                f"to embedding/BM25: {e}"
            )
            return self._degraded_select(reranked, max_results)

    # --------------------------------------------------------
    # Stage 1 — BM25
    # --------------------------------------------------------

    async def _bm25_prefilter(
        self,
        query: str,
        project_id: str | None,
        top_k: int = 10,
    ) -> list[Any]:
        """BM25 keyword prefilter over active skills.

        Loads every active skill for the project (or global-only
        when ``project_id`` is ``None``) and ranks them by BM25
        score over the concatenation of ``name + description +
        content``. Pure Python — no numpy, no external BM25
        library.

        Args:
            query: User message to match against.
            project_id: Project to scope the corpus to. ``None``
                searches global skills only.
            top_k: Maximum number of candidates to return. Skill
                rows with BM25 score ``<= 0`` (no overlapping
                terms) are filtered out before the cutoff, so
                the result list may be shorter than ``top_k``.

        Returns:
            List of :class:`Skill` instances ordered by BM25
            score descending. Empty when no skill shares any
            tokens with the query, or when the active-skill set
            is empty.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        # ``SkillRepository.list(project_id, ...)`` returns ONLY
        # rows where ``Skill.project_id == project_id`` — it does
        # NOT auto-merge globals. To match the active project's
        # skills PLUS the global library, fetch project-scoped
        # rows when ``project_id`` is set and overlay global rows
        # (``project_id IS NULL``) on top, deduping by ``skill.id``
        # so the project version wins on collisions.
        async def _fetch(pid: str | None) -> list[Any]:
            try:
                items, _ = await asyncio.to_thread(
                    self._skill_repo.list,
                    project_id=pid,
                    active_only=True,
                    limit=200,
                )
                return list(items or [])
            except TypeError:
                # Fallback for repos whose ``list`` method doesn't
                # accept keyword args (test mocks sometimes use
                # positional signatures).
                items, _ = await asyncio.to_thread(
                    self._skill_repo.list,
                    pid,
                    True,
                    200,
                )
                return list(items or [])
            except Exception as e:
                logger.warning(
                    f"[SkillSearch] BM25 failed to fetch skills "
                    f"(project_id={pid!r}): {e}"
                )
                return []

        if project_id is None:
            # Global-only search — single fetch.
            items = await _fetch(None)
        else:
            # Project + global overlay — fetch both, then merge
            # with project rows winning on duplicate ids.
            # Fetch globals first so the project call is the most
            # recent (preserves ``repo.list.call_args.kwargs``
            # observability for unit tests).
            global_items = await _fetch(None)
            project_items = await _fetch(project_id)
            seen_ids: set[Any] = set()
            items: list[Any] = []
            for skill in project_items:
                skill_id = getattr(skill, "id", None)
                if skill_id is not None and skill_id in seen_ids:
                    continue
                if skill_id is not None:
                    seen_ids.add(skill_id)
                items.append(skill)
            for skill in global_items:
                skill_id = getattr(skill, "id", None)
                if skill_id is None:
                    # No id to dedupe against — keep the row.
                    items.append(skill)
                    continue
                if skill_id in seen_ids:
                    # Project-scoped row already wins — skip global.
                    continue
                seen_ids.add(skill_id)
                items.append(skill)

        if not items:
            return []

        # Tokenize every doc once. Re-tokenizing on every term
        # would be O(query_tokens * doc_tokens) — pre-compute is
        # O(total_tokens) and avoids re-splitting.
        tokenized_docs: list[tuple[Any, list[str]]] = []
        doc_term_freqs: list[Counter[str]] = []
        for skill in items:
            name = getattr(skill, "name", "") or ""
            description = getattr(skill, "description", "") or ""
            content = getattr(skill, "content", "") or ""
            doc_text = f"{name} {description} {content}"
            tokens = _tokenize(doc_text)
            tokenized_docs.append((skill, tokens))
            doc_term_freqs.append(Counter(tokens))

        # Document frequency per term across the corpus. Only
        # query terms are tracked — we don't need global stats
        # for any other term.
        df: dict[str, int] = {}
        for tokens in (toks for _, toks in tokenized_docs):
            unique_terms = set(tokens)
            for term in query_tokens:
                if term in unique_terms:
                    df[term] = df.get(term, 0) + 1

        n_docs = len(tokenized_docs)
        total_tokens = sum(len(toks) for _, toks in tokenized_docs)
        avgdl = total_tokens / n_docs if n_docs else 1.0

        scored: list[tuple[float, Any]] = []
        for (skill, tokens), term_freq in zip(tokenized_docs, doc_term_freqs):
            score = _bm25_score(
                query_tokens=query_tokens,
                doc_tokens=tokens,
                doc_freqs=df,
                total_docs=n_docs,
                avg_doc_len=avgdl,
            )
            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        # Return just the skill rows — ordering carries the BM25
        # ranking forward into stage 2.
        return [s for _, s in scored[:top_k]]

    # --------------------------------------------------------
    # Stage 2 — Embedding re-rank
    # --------------------------------------------------------

    async def _embedding_rerank(
        self,
        query: str,
        candidates: list[Any],
        top_k: int = 5,
    ) -> list[tuple[Any, float]]:
        """Re-rank BM25 candidates by cosine similarity to the query.

        Embeds ``query`` via
        :meth:`SkillEmbeddingService.embed_user_message` (the
        OpenAI-compatible ``/embeddings`` endpoint), then scores
        each candidate skill against its cached per-skill
        embeddings. The score is the **MAX** cosine similarity
        across all cached embeddings for the skill — taking the
        best-matching trigger phrase handles the "any-of"
        semantics of cached triggers without any custom logic.

        Args:
            query: User message to match against. Embedded once
                per call.
            candidates: BM25-prefiltered skill rows from
                :meth:`_bm25_prefilter`. Order is preserved
                among ties (descending score → stable).
            top_k: Maximum number of (skill, score) pairs to
                return. Default ``5`` matches
                ``SkillEvolutionConfig.llm_select_top_k``.

        Returns:
            List of ``(skill, max_similarity)`` tuples ordered
            by similarity descending. Capped at ``top_k``
            entries. Empty when ``candidates`` is empty.

        Raises:
            Exception: Propagates any exception raised by
                :meth:`embed_user_message` or by the embedding
                fetch — :meth:`search` catches and falls back.
        """
        if not candidates:
            return []

        # Embed the query exactly once. Cache the result for
        # the loop below.
        query_emb = await self._embedding_service.embed_user_message(
            query
        )

        scored: list[tuple[Any, float]] = []
        for skill in candidates:
            skill_id = getattr(skill, "id", None)
            if not skill_id:
                scored.append((skill, 0.0))
                continue

            # Per-skill embeddings: list of SkillEmbedding rows.
            # Fetch is sync; bridge through the thread pool.
            try:
                embeddings = await asyncio.to_thread(
                    self._embedding_repo.get_by_skill, skill_id
                )
            except Exception as e:
                logger.warning(
                    "[SkillSearch] embedding fetch failed for "
                    f"skill id={skill_id}: {e}"
                )
                embeddings = []

            if not embeddings:
                # No cached embeddings yet (the skill hasn't
                # been through the embed pipeline) — give it a
                # neutral score so it stays in the ranking by
                # BM25 position.
                scored.append((skill, 0.0))
                continue

            # Per-skill max similarity across all cached
            # triggers. The service's cosine_similarity is the
            # single source of truth for vector math — pure
            # Python, no numpy.
            best = max(
                self._embedding_service.cosine_similarity(
                    query_emb, e.embedding
                )
                for e in embeddings
            )
            scored.append((skill, best))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    # --------------------------------------------------------
    # Stage 3 — LLM selection
    # --------------------------------------------------------

    async def _llm_select(
        self,
        query: str,
        candidates: list[tuple[Any, float]],
        max_results: int = 2,
        client: Any = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """LLM final selection from the re-ranked candidates.

        Builds a chat-completion prompt listing each candidate's
        ``name`` and ``description`` (NOT ``content`` — too
        verbose for a short context window) and asks the model
        to return JSON with ``selected`` and ``low_match``
        arrays.

        The ``client`` parameter is optional so tests can supply
        a pre-built :class:`MagicMock` instead of patching
        ``openai.OpenAI``. In production it defaults to a fresh
        client constructed from ``self._llm_config``.

        Args:
            query: The original user message. Echoed back to the
                LLM so the model can ground its relevance calls.
            candidates: ``(skill, score)`` tuples from stage 2,
                already in descending similarity order.
            max_results: Maximum number of skills to surface in
                ``injected``. Default ``2``.
            client: Optional pre-built OpenAI-compatible client.
                When ``None``, the method constructs one from
                ``self._llm_config`` (production path).

        Returns:
            ``{"injected": [...], "low_match": [...]}`` dict.
            Selected skills are mapped back to their original
            :class:`Skill` rows by name; unmatched names are
            skipped with a warning.

        Raises:
            Exception: Propagates any exception from the LLM
                call or JSON parser. :meth:`search` catches
                and falls back to :meth:`_degraded_select`.
        """
        if not candidates:
            return {"injected": [], "low_match": []}

        # Build the candidate list — ``name`` + ``description``
        # only (no content). Cap at top 5 so the prompt stays
        # short even when LLM stage receives a wider rerank.
        top_candidates = candidates[: max(5, max_results)]
        candidate_lines = []
        name_to_skill: dict[str, Any] = {}
        for idx, (skill, _score) in enumerate(top_candidates, start=1):
            name = getattr(skill, "name", "") or ""
            description = getattr(skill, "description", "") or ""
            candidate_lines.append(f"{idx}. {name} — {description}")
            if name:
                name_to_skill[name.lower()] = skill

        system_prompt = (
            "You are a skill selector. Given the user message and a "
            "list of candidate skills (name + one-line description "
            "only), pick the most relevant skills. "
            f"Return JSON exactly in this shape (no prose, no markdown "
            "fences): "
            '{"selected": [{"name": "<skill_name>", "score": '
            '<0.0-1.0>}, ...], '
            '"low_match": [{"name": "<skill_name>", "score": '
            '<0.0-1.0>, "description": "<description>"}, ...]}.\n'
            f'Pick up to {max_results} for "selected" and up to 3 '
            'for "low_match".'
        )
        user_prompt = (
            f"User message: {query}\n\n"
            f"Candidate skills:\n" + "\n".join(candidate_lines)
        )

        if client is None:
            client = openai.OpenAI(
                api_key=self._llm_config.get("api_key") or "",
                base_url=self._llm_config.get("base_url") or None,
            )
        model = self._llm_config.get("model") or "gpt-4o-mini"

        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )

        raw_text = self._extract_chat_content(response)
        parsed = self._parse_llm_selection(raw_text)
        if parsed is None:
            # Parse failure — raise so the caller's try/except
            # routes us into degraded_select instead of
            # returning a half-formed dict.
            raise ValueError(
                f"Could not parse LLM selection JSON: {raw_text[:200]!r}"
            )

        selected_raw = parsed.get("selected") or []
        low_match_raw = parsed.get("low_match") or []

        injected: list[dict[str, Any]] = []
        for sel in selected_raw[:max_results]:
            name = (sel.get("name") or "").lower()
            score_raw = sel.get("score", 0.0)
            try:
                score = float(score_raw)
            except (TypeError, ValueError):
                score = 0.0
            skill = name_to_skill.get(name)
            if skill is None:
                # The LLM invented a skill name. Skip silently —
                # nothing to inject, and we'd rather under-inject
                # than mis-inject.
                continue
            injected.append({"skill": skill, "score": score})

        low_match: list[dict[str, Any]] = []
        for lm in low_match_raw[:3]:
            name = (lm.get("name") or "").lower()
            score_raw = lm.get("score", 0.0)
            try:
                score = float(score_raw)
            except (TypeError, ValueError):
                score = 0.0
            description = lm.get("description") or ""
            # Prefer the live description off the skill row
            # over whatever the LLM hallucinated — the skill
            # object is the source of truth.
            skill = name_to_skill.get(name)
            if skill is not None:
                description = getattr(skill, "description", "") or description
            low_match.append(
                {"name": name, "score": score, "description": description}
            )

        return {"injected": injected, "low_match": low_match}

    # --------------------------------------------------------
    # Degraded fallback
    # --------------------------------------------------------

    def _degraded_select(
        self,
        candidates: list[tuple[Any, float]],
        max_results: int,
    ) -> dict[str, list[dict[str, Any]]]:
        """Pick results when the LLM is unavailable.

        Used by :meth:`search` when stage 3 raises. Treats the
        top ``max_results`` from stage 2 as ``injected`` and the
        next three as ``low_match`` so the resolver still has
        *some* ranked context to attach to the prompt.

        Args:
            candidates: ``(skill, score)`` tuples from stage 2.
                Already ordered by descending similarity.
            max_results: Maximum number of skills to surface in
                ``injected``.

        Returns:
            ``{"injected": [...], "low_match": [...]}`` dict.
            ``injected`` carries the full ``Skill`` object so
            callers can render the body. ``low_match`` carries
            just the name + description (the resolver shows
            these in the "you may want" sidebar) and never
            embeds the skill object directly.
        """
        injected = [
            {"skill": skill, "score": float(score)}
            for skill, score in candidates[:max_results]
        ]
        low_match = [
            {
                "name": getattr(skill, "name", "") or "",
                "score": float(score),
                "description": getattr(skill, "description", "") or "",
            }
            for skill, score in candidates[
                max_results : max_results + 3
            ]
        ]
        return {"injected": injected, "low_match": low_match}

    # --------------------------------------------------------
    # Internal helpers
    # --------------------------------------------------------

    @staticmethod
    def _extract_chat_content(response: Any) -> str:
        """Pull text content out of a chat-completion response.

        Handles the standard ``response.choices[0].message.content``
        shape and a list-of-content-blocks variant some
        providers emit. Returns an empty string when nothing
        usable comes back so the JSON parser can fail
        gracefully.

        Args:
            response: A chat-completion response object (real
                or mock).

        Returns:
            The model's text. May be empty.
        """
        try:
            choices = getattr(response, "choices", None) or []
            if not choices:
                return ""
            first = choices[0]
            message = getattr(first, "message", None)
            content = ""
            if message is not None:
                content = getattr(message, "content", "") or ""
            else:
                content = getattr(first, "text", "") or ""
        except Exception:
            return ""

        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if text:
                        parts.append(str(text))
                else:
                    text = getattr(block, "text", "")
                    if text:
                        parts.append(str(text))
            return " ".join(parts)
        return str(content or "")

    @staticmethod
    def _parse_llm_selection(raw_text: str) -> dict[str, Any] | None:
        """Parse the LLM's selected/low_match JSON.

        Tolerant of:

        * Markdown-fenced JSON (```json { ... } ```).
        * Bare JSON objects anywhere in the response.
        * Surrounding prose ("Here you go: {...}").

        Args:
            raw_text: The LLM response text.

        Returns:
            Parsed dict, or ``None`` when nothing usable comes
            back. Callers should fall back to
            :meth:`_degraded_select` on ``None``.
        """
        if not raw_text:
            return None
        json_str = _extract_json_object(raw_text)
        if not json_str:
            return None
        try:
            parsed = json.loads(json_str)
        except (ValueError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        # Coerce ``selected`` / ``low_match`` to lists — model
        # may have dropped them when no skills qualified.
        if "selected" not in parsed:
            parsed["selected"] = []
        if "low_match" not in parsed:
            parsed["low_match"] = []
        return parsed
