"""Keyword extraction for the OpenCode context preloader.

Two paths produce a focused query string for ``get_shared_context``:

1. **LLM extract** (``extract_keywords``) — one-shot call to the configured
   ``model_keywords`` LLM (defaults to ``model``; ops typically pin this to
   ``"quick"`` to mirror the explorer agent's per-instance model). Bounded by
   :data:`KEYWORD_EXTRACTION_TIMEOUT_S` (40s — this is a harness platform, not
   a chat UI, and the opencode call we are feeding dwarfs the extraction cost).

2. **Heuristic** (``_heuristic_keywords``) — pure-Python fallback that
   extracts backtick-quoted terms, CamelCase tokens, the first non-empty line,
   and a few high-signal words from the first 500 chars. Used when the LLM
   call is unavailable, times out, raises, or returns nothing usable.

Both paths are best-effort and never raise — failures degrade to ``[]`` so
the caller can skip preloading cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Config

logger = logging.getLogger(__name__)


KEYWORD_EXTRACTION_TIMEOUT_S: float = 40.0

_MAX_KEYWORDS = 12
_MAX_KEYWORD_LEN = 40
_HEURISTIC_MAX_KEYWORDS = 8

_LLM_PROMPT_SYSTEM = (
    "You are a keyword extractor for a context-file matcher. "
    "Given a user message, extract 3-8 short noun-phrase keywords (1-3 words each) "
    "that would best match the topic-slugs of project context files. "
    "Return ONLY a comma-separated list with no prose, no numbering, no quotes."
)

_LLM_PROMPT_USER_TEMPLATE = (
    "User message:\n{message}\n\n"
    "Keywords (comma-separated, 3-8 items, short noun-phrases only):"
)

_BACKTICK_RE = re.compile(r"`([^`]{1,40})`")
_CAMELCASE_RE = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
_ALLCAPS_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9_]{1,20}\b")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,40}")

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
    "neither", "each", "every", "all", "any", "few", "more", "most", "other",
    "some", "such", "no", "only", "own", "same", "than", "too", "very",
    "just", "because", "if", "when", "where", "how", "what", "which", "who",
    "whom", "this", "that", "these", "those", "i", "me", "my", "we", "our",
    "you", "your", "he", "she", "it", "they", "them", "their",
})


def _strip_surrounding_quotes(s: str) -> str:
    """Strip a single layer of matching surrounding ``"`` or ``'`` from ``s``.

    Used by :func:`_normalize_keywords` to clean tokens that came out of a
    JSON-array-as-string (e.g. ``"git commit"`` → ``git commit``). Returns
    the input unchanged when it is shorter than 2 chars or the surrounding
    pair does not match.
    """
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1].strip()
    return s


def _normalize_keywords(raw: list[str] | str | None) -> list[str]:
    """Sanitize and dedupe a list (or delimited string) of keywords.

    Accepts any of the following shapes and normalizes to a flat list:

    - A Python list of strings (preferred).
    - A comma-/semicolon-/newline-separated string (e.g. ``"auth, login"``).
    - A JSON-array-as-string (e.g. ``'["auth", "login"]'``) — agents commonly
      serialize a list of keywords as one string. Detected by the surrounding
      ``[...]`` and parsed via :mod:`json`.
    - ``None`` — returns ``[]``.

    Drops empty entries, strips whitespace and surrounding quotes, removes
    pure stop-words, drops entries longer than :data:`_MAX_KEYWORD_LEN`,
    dedupes case-insensitively (preserving first-seen casing), and caps the
    result at :data:`_MAX_KEYWORDS`. Returns ``[]`` for any falsy / non-
    iterable input other than a non-empty string.

    Args:
        raw: A list, a delimited string, a JSON-array-as-string, or ``None``.

    Returns:
        A cleaned, deduped, capped list of keywords.
    """
    if raw is None:
        return []

    # If the string looks like a JSON-encoded list, try to parse it before
    # falling back to delimiter splitting — agents frequently stringify a
    # list rather than sending the actual array. A failed parse falls through
    # to the plain-string path (with surrounding brackets stripped) so that
    # malformed inputs degrade gracefully instead of yielding "["/"]"-wrapped
    # garbage tokens.
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                import json
                parsed = json.loads(stripped)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, list):
                raw = parsed

    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("["):
            s = s[1:]
        if s.endswith("]"):
            s = s[:-1]
        parts = re.split(r"[,;\n]+", s)
    else:
        try:
            parts = list(raw)
        except TypeError:
            return []

    seen_lower: set[str] = set()
    out: list[str] = []
    for part in parts:
        if not isinstance(part, str):
            continue
        cleaned = _strip_surrounding_quotes(part.strip())
        if not cleaned:
            continue
        if len(cleaned) > _MAX_KEYWORD_LEN:
            continue
        lower = cleaned.lower()
        if lower in _STOP_WORDS:
            continue
        if lower in seen_lower:
            continue
        seen_lower.add(lower)
        out.append(cleaned)
        if len(out) >= _MAX_KEYWORDS:
            break
    return out


def _heuristic_keywords(message: str) -> list[str]:
    """Extract a small keyword list from ``message`` using pure-Python heuristics.

    Sources (in priority order, then capped at :data:`_HEURISTIC_MAX_KEYWORDS`):

    1. Backtick-quoted terms (e.g., `` `auth` ``, `` `payment-module` ``) —
       these almost always refer to a specific topic or identifier.
    2. CamelCase identifiers (e.g., ``PaymentModule``, ``UserAuth``).
    3. ALL_CAPS tokens of length ≥ 2 (e.g., ``JWT``, ``API``).
    4. The first non-empty line of the message, capped to 80 chars (most
       agent prompts lead with the topic).
    5. Up to 3 high-signal tokens from the first 500 chars: tokens of length
       ≥ 4 that are not stop words.

    Args:
        message: The outgoing prompt to extract from.

    Returns:
        A list of up to :data:`_HEURISTIC_MAX_KEYWORDS` keywords, in the
        priority order above. Returns ``[]`` for empty / whitespace input.
    """
    if not message or not message.strip():
        return []

    candidates: list[str] = []
    seen_lower: set[str] = set()

    def _add(token: str) -> None:
        token = token.strip()
        if not token or len(token) > _MAX_KEYWORD_LEN:
            return
        lower = token.lower()
        if lower in _STOP_WORDS or lower in seen_lower:
            return
        seen_lower.add(lower)
        candidates.append(token)

    for match in _BACKTICK_RE.findall(message):
        _add(match)

    for match in _CAMELCASE_RE.findall(message):
        _add(match)

    for match in _ALLCAPS_TOKEN_RE.findall(message):
        _add(match)

    first_line = ""
    for line in message.splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped[:80]
            break
    if first_line and not first_line.startswith("`"):
        # Drop stop-words / short tokens from the first line before adding —
        # a pure-stop-word sentence like "the and or but if when" should not
        # produce a multi-word entry, since the matcher scores by individual
        # tokens and stop words are filtered out anyway.
        meaningful = [
            tok for tok in _WORD_RE.findall(first_line)
            if len(tok) >= 2 and tok.lower() not in _STOP_WORDS
        ]
        if meaningful:
            _add(" ".join(meaningful))

    head = message[:500]
    tokens = _WORD_RE.findall(head)
    added_from_tokens = 0
    for tok in tokens:
        if len(tok) < 4:
            continue
        before = len(candidates)
        _add(tok)
        if len(candidates) > before:
            added_from_tokens += 1
            if added_from_tokens >= 3:
                break

    return candidates[:_HEURISTIC_MAX_KEYWORDS]


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_QUOTED_TERM_RE = re.compile(r"[\"'`]\s*([^\\[\]{}()\"'`\n]{1,40})\s*[\"'`]")
_TRAILING_PUNCT_RE = re.compile(r"[.,;:!?]+$")


def _parse_llm_keywords(raw_text: str) -> list[str]:
    """Parse a comma/semicolon/newline-separated keyword list from an LLM response.

    Defensive against common LLM quirks:

    - ``<think>...</think>`` reasoning blocks (DeepSeek, Qwen, GLM, etc.) —
      stripped before any other parsing.
    - Quoted terms (``"auth" "payment"``) — when the response contains
      quoted single-/double-/backtick-wrapped tokens, those are extracted
      first because they almost always represent the actual keyword list
      even when the surrounding prose is chatty.
    - Leading bullet markers, numbered prefixes (``"1. auth"``), and trailing
      prose — dropped when they look like intro/outro sentences.

    Delegates to :func:`_normalize_keywords` for the final cleanup pass.

    Args:
        raw_text: The raw LLM response text.

    Returns:
        A normalized, capped keyword list. ``[]`` if the response is empty
        or unparseable.
    """
    if not raw_text or not raw_text.strip():
        return []

    # Step 1: try quoted-term extraction on the RAW response first. If the
    # model emitted 2+ quoted tokens anywhere (including inside a <think>
    # block), those are almost certainly the keyword list even when the
    # surrounding text is chatty prose or chain-of-thought reasoning. The
    # production log showed glm-5 returning:
    #   <think> For matching against topic-slugs I should focus on: "greeting" "confirmation" "test session" </think>
    # — the actual keywords are the quoted terms, buried inside the think
    # block. Extracting quoted terms FIRST avoids losing them when we strip
    # the think block in step 2.
    quoted = _QUOTED_TERM_RE.findall(raw_text)
    if len(quoted) >= 2:
        return _normalize_keywords(quoted)

    # Step 2: strip <think>...</think> blocks. Many chat-tuned models emit
    # chain-of-thought inside these tags even when the system prompt asks
    # for a pure keyword list — the thinking is noise to us. We only get
    # here when the model didn't wrap keywords in quotes.
    text = _THINK_BLOCK_RE.sub(" ", raw_text)

    candidates: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if not line:
            continue
        # Keep a line if it has a delimiter (the LLM followed the comma-list
        # format), it's a numbered entry, or it's a short single-keyword line.
        # Drop lines that look like prose: 3+ words, no delimiter, no
        # numbered prefix — these are almost always an LLM intro/outro like
        # "Here are the keywords:" or "Let me know." that would pollute the
        # final normalized list.
        has_delim = bool(re.search(r"[,;]", line))
        looks_like_numbered = bool(re.match(r"^\d+[.)]\s*\S", line))
        looks_like_single_keyword = (
            len(line) <= 30 and len(line.split()) <= 2
        )
        if not (has_delim or looks_like_numbered or looks_like_single_keyword):
            continue
        # Strip trailing punctuation that some LLMs append out of habit.
        line = _TRAILING_PUNCT_RE.sub("", line).strip()
        if line:
            candidates.append(line)
    if not candidates:
        return []
    blob = ", ".join(candidates)
    return _normalize_keywords(blob)


async def extract_keywords(
    message: str,
    *,
    config: "Config | None" = None,
    timeout_s: float | None = None,
) -> list[str]:
    """Extract focus keywords from ``message`` using the configured LLM.

    Best-effort: never raises. Returns ``[]`` on timeout, exception, or empty
    / unparseable LLM output. Callers should fall back to
    :func:`_heuristic_keywords` when this returns ``[]``.

    Args:
        message: The outgoing prompt to extract keywords from.
        config: Optional pre-loaded :class:`Config` instance. When ``None``,
            the function loads config itself (cached after first call).
        timeout_s: Override the default
            :data:`KEYWORD_EXTRACTION_TIMEOUT_S`. Useful for tests.

    Returns:
        A normalized, deduped list of 3-8 keywords. ``[]`` on any failure.
    """
    if not message or not message.strip():
        return []

    if timeout_s is None:
        timeout_s = KEYWORD_EXTRACTION_TIMEOUT_S

    try:
        if config is None:
            from ..config import load_config
            config = load_config()
        model = (config.llm.model_keywords or "").strip() or config.llm.model

        from langchain_core.messages import HumanMessage, SystemMessage
        from ..graph import ThinkingChatOpenAI, clean_llm_config
        from .llm_failover import wrap_langchain_failover

        llm_config = {
            "base_url": config.llm.base_url,
            # Threaded for config-surface uniformity; consumed by
            # ``wrap_langchain_failover`` (HA-failover facade) below.
            # See ``LLMConfig.base_url_backup`` in ``daemon/config.py``
            # and ``daemon/services/llm_failover.py``.
            "base_url_backup": config.llm.base_url_backup,
            "api_key": config.llm.api_key,
            "model": model,
            "temperature": 0.0,
            "default_headers": {"x-proxy-app": "ensemble"},
        }
        # F1 kwarg hygiene: clean INSIDE the constructor call only —
        # the facade needs the RAW dict to read ``base_url_backup``.
        # (Pre-cleaning ``llm_config`` here would strip the backup
        # before the facade sees it and silently kill failover.)
        llm = ThinkingChatOpenAI(**clean_llm_config(dict(llm_config)))
        # v2 HA: wrap invoke with FailoverController + tenacity
        # retry-with-failover via the shared facade. When
        # ``base_url_backup`` is unset the wrapper is a no-op over
        # the same shape v1 uses — zero behavior change.
        llm_wrapper = wrap_langchain_failover(llm, llm_config)

        messages = [
            SystemMessage(content=_LLM_PROMPT_SYSTEM),
            HumanMessage(content=_LLM_PROMPT_USER_TEMPLATE.format(
                message=message[:2000],
            )),
        ]

        response = await asyncio.wait_for(
            asyncio.to_thread(llm_wrapper.invoke, messages),
            timeout=timeout_s,
        )

        content: Any = getattr(response, "content", "")
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                else:
                    parts.append(str(block))
            raw_text = " ".join(parts)
        else:
            raw_text = str(content) if content else ""

        return _parse_llm_keywords(raw_text)

    except asyncio.TimeoutError:
        logger.debug(
            "[OpenCode] Keyword extraction timed out after %.1fs", timeout_s,
        )
        return []
    except Exception as e:
        logger.debug("[OpenCode] Keyword extraction failed: %s", e)
        return []
