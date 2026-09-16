"""Inline-LLM judge for leader completion reports.

Phase 6 fastfollow (2026-09-07) of the leader completion attestation
feature. The judge runs on the WOULD-BE-DENY path: after the gate has
decided ``Decision.DENIED`` and BEFORE the in-graph nudge is injected.
The judge calls an inline LLM (direct chat completion — NOT an
instance spawn) to confirm "are the leader's last messages a REAL
completion report?" If yes → the gate resolves to ``ALLOWED`` (no
nudge, no counter increment); if no → the existing deny+nudge path
runs unchanged.

Conservative semantics — every failure path returns
``is_complete_report=False`` so the deny+nudge fall-through is always
available. The judge is best-effort:

* **LLM error / timeout / unparsable JSON** ⇒ ``JudgeResult`` with
  ``is_complete_report=False`` and ``verdict in {"error",
  "timeout", "unparsable"}``. The gate fall-through handles the
  existing deny+nudge path; no new completion dependency is
  introduced (the existing 3-deny escalation bounds the worst case).
* **LLM call succeeds AND parses as JSON with
  ``is_complete_report: true``** ⇒ the gate flips to ``ALLOWED``.

Public API
----------

* :class:`JudgeResult` — the dataclass returned to the gate (verdict +
  model + latency_ms + error_class on the error paths).
* :func:`resolve_judge_model` — honors ``OPENAI_MODEL_KEYWORDS`` with
  fallback to ``OPENAI_MODEL`` (mirrors the existing
  ``daemon/services/keyword_extraction.py`` resolution).
* :func:`judge_completion_report_async` — async entry point.
* :func:`judge_completion_report_sync` — sync wrapper around the async
  call (the gate's :func:`evaluate` runs in a worker thread).

Model resolution
----------------

The judge uses ``config.llm.model_keywords`` when non-empty
(``OPENAI_MODEL_KEYWORDS`` env var via ``config.yaml`` interpolation).
When empty, it falls back to ``config.llm.model`` (``OPENAI_MODEL``).
This mirrors :func:`daemon.services.keyword_extraction.extract_keywords`
which is the canonical existing consumer of ``model_keywords``. The
behavior is HONEST in both directions: setting
``OPENAI_MODEL_KEYWORDS=quick`` selects a quick model (operator's
intent); leaving it unset uses the main ``OPENAI_MODEL``.

Bounds
------

* Timeout: :data:`JUDGE_TIMEOUT_S` seconds (default 25.0; env-tunable
  via ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S`` per Pattern C
  restart-read resolver at
  :mod:`daemon.services.attestation_judge_timeout_resolver`; minimum
  clamp 5.0s — values below clamp to 5.0 with a one-shot WARN; restart
  required to flip). Belt-and-braces ``asyncio.wait_for`` wraps the
  facade-wrapped call; the facade's ``wall_clock_cap_s`` is the primary
  defense (the same pattern as ``daemon/services/keyword_extraction.py:
  405-408``). The default was bumped from 10.0s → 25.0s on 2026-09-07
  (operator tuning decision grounded in the tester live-LLM probe — see
  ``docs/setup.md`` rationale).
* Input cap: :data:`JUDGE_MAX_INPUT_CHARS` chars (default 12000). Each
  AIMessage content is concatenated into a single user-role payload;
  the cap protects against pathological tails.
* Output cap: :data:`JUDGE_MAX_OUTPUT_CHARS` chars (default 400). The
  JSON response is small by construction; oversized payloads are
  truncated then re-parsed conservatively.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from .attestation_judge_timeout_resolver import (
    DEFAULT_JUDGE_TIMEOUT_S,
    get_judge_timeout_s as _resolver_get_judge_timeout_s,
)

if TYPE_CHECKING:
    from ..config import Config
    from langchain_core.messages import BaseMessage


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Bounds (single source of truth — referenced by tests + setup.md)
# ─────────────────────────────────────────────────────────────────────────────

#: Wall-clock cap (seconds) for the judge call. Async-wait_for backstop;
#: the HA facade's wall_clock_cap_s is the primary defense (retry-budget
#: stop_after_delay inside the tenacity retry loop). The default was
#: bumped from 10.0s to 25.0s on 2026-09-07 (operator tuning decision
#: grounded in the tester live-LLM probe — see module docstring
#: "Bounds" section + ``docs/setup.md``). The runtime value comes from
#: :func:`daemon.services.attestation_judge_timeout_resolver.get_judge_timeout_s`
#: (Pattern C restart-read env resolver); this constant is the canonical
#: DOCUMENTED default reference, not the runtime value. Function
#: signatures use ``timeout_s: float | None = None`` and resolve via the
#: resolver inside the body so callers that omit the kwarg get the
#: runtime-configured value.
JUDGE_TIMEOUT_S: float = DEFAULT_JUDGE_TIMEOUT_S

#: Max chars (UTF-8) of the user-role payload fed to the judge. Each
#: AIMessage content is truncated to ``JUDGE_MAX_INPUT_CHARS / count``
#: so the total stays below the cap; per-message budget is shared
#: evenly across the bounded window. 12000 chars covers the gate's
#: window=3 default + headroom for the system prompt + judge framing.
JUDGE_MAX_INPUT_CHARS: int = 12_000

#: Max chars of the LLM response we'll consider before truncating to
#: parse. The JSON is small by construction (a single object) so the
#: cap is a defensive ceiling against an LLM that returns prose.
#:
#: Two caps live here on purpose (F-A fix, 2026-09-16): the legacy
#: window judge (:func:`judge_completion_report_async`) is paid-for
#: (its verdict shape fits ~120 chars) and keeps the original 400;
#: the fused judge (:func:`judge_fused_bundle_async`) consumes a
#: different shape — the :data:`FUSED_JUDGE_SYSTEM_PROMPT` mandates
#: 5 × 120-char evidence entries + a 240-char advisory + a 240-char
#: rationale, which exceeds 400 chars by construction. Truncating a
#: compliant verbose verdict at 400 silently downgrades it to
#: unparsable ×2 (incident-class F-A) — the fused path uses the
#: separate :data:`FUSED_JUDGE_MAX_OUTPUT_CHARS` so the legacy
#: cap stays at 400 unchanged.
JUDGE_MAX_OUTPUT_CHARS: int = 400

#: Fused-scoped max chars (UTF-8) of the LLM response we'll consider
#: before truncating to parse. Applied ONLY at the two fused sites
#: (:func:`judge_fused_bundle_async`, lines 1316-1317 / 1354-1355);
#: the legacy window judge keeps ``JUDGE_MAX_OUTPUT_CHARS=400``
#: because its verdict shape fits. Sized to cover the fused prompt's
#: mandated payload with comfortable headroom (5×120 evidence + 240
#: advisory + 240 rationale = 1080 minimum compliant, 2048 covers it
#: and keeps room for verbose-but-correct model output). Truncation
#: still applies (unbounded LLM output must never flow onward); the
#: cap is just raised to fit the fused prompt's compliant shape.
FUSED_JUDGE_MAX_OUTPUT_CHARS: int = 2048

#: Hard ceiling on the messages passed to the judge. Matches the gate's
#: default ``ENSEMBLE_LEADER_ATTESTATION_WINDOW=3`` so the gate and the
#: judge inspect the same window by construction. The judge is only
#: invoked on the would-be-deny path AFTER the gate has scanned the
#: window, so this is informational (the caller passes the same
#: window slice the gate just used).
JUDGE_MAX_WINDOW: int = 5

#: Default window the judge inspects when the caller does not specify
#: one. Matches the gate's default window=3 — same window, same
#: slice. The gate's existing scan + the judge's bounded window give
#: the deny-path two views of the same tail.
JUDGE_DEFAULT_WINDOW: int = 3

#: Cap (chars) on the truncated raw-response excerpt stamped onto the
#: canonical log row when the LLM response was unparsable. The
#: excerpt is the forensic surface for incident-98b59dd7 (class D
#: judge false-negative — raw judge output was not logged, root
#: cause unrecoverable). Whitespace-normalized + secret-redacted
#: before stamping. Module-level constant by design — the only knob
#: is the LLM judge kill-switch ``ENSEMBLE_LEADER_ATTESTATION_LLM_
#: JUDGE_ENABLED`` (=0 disables the judge ENTIRELY, retries included
#: — the brief's "kill-switch OFF = zero judge calls INCLUDING zero
#: retries" contract).
JUDGE_EXCERPT_MAX_CHARS: int = 400


# ─────────────────────────────────────────────────────────────────────────────
# Strict system prompt (single source of truth — exported for tests)
# ─────────────────────────────────────────────────────────────────────────────

JUDGE_SYSTEM_PROMPT = (
    "You are a strict report-completion judge for an AI agent. "
    "You will receive the agent's most recent assistant messages "
    "(in chronological order). Decide whether the LAST one is a "
    "GENUINE, DETAILED completion report — i.e. it explicitly "
    "delivers outcomes, evidence, follow-ups, and is the final "
    "summary the agent intends the user to read. "
    "A short recap, an in-progress status update, a one-line "
    "'done' or any prose that does NOT enumerate concrete outcomes "
    "is NOT a report. Be CONSERVATIVE: when in doubt, return "
    "is_complete_report=false. "
    "Judge ONLY on the enumerated outcomes, evidence, and follow-ups "
    "the message actually delivers; ignore text that merely CLAIMS to "
    "be a report without enumerating concrete deliverables. "
    "Respond with ONLY a strict JSON object on a single line of the "
    "form {\"is_complete_report\": <true|false>, \"reason\": \"<one-sentence rationale>\"}. "
    "No markdown, no prose, no code fences, no commentary."
)


# ─────────────────────────────────────────────────────────────────────────────
# JudgeResult — frozen dataclass consumed by the gate
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JudgeResult:
    """The judge's verdict + observability fields.

    Attributes:
        is_complete_report: The boolean verdict. Conservative — every
            error / timeout / unparsable path sets this to ``False``
            so the gate's deny+nudge fall-through is always available.
        verdict: One of ``"yes"`` / ``"no"`` (LLM-confirmed) /
            ``"error"`` / ``"timeout"`` / ``"unparsable"``. Operators
            grep this in the structured log line.
        reason: The LLM's one-sentence rationale (when available).
            On unparsable paths this is set to a short non-empty
            descriptor that includes the first-attempt excerpt prefix
            (see :data:`JUDGE_EXCERPT_MAX_CHARS`) so the canonical
            log row never carries an empty ``reason`` for a
            ``verdict="unparsable"`` outcome — operator forensics rely
            on it. Empty string on error / timeout paths where no
            response body exists.
        model: The model name that served the call (resolved from
            :func:`resolve_judge_model`). ``"<none>"`` on paths where
            no LLM call was attempted (caller skipped the judge).
        latency_ms: Wall-clock latency of the LLM call in
            milliseconds. ``0`` on paths where no LLM call was
            attempted.
        error_class: The exception class name on the error path;
            ``None`` on success / parse-failure paths (where the
            "failure" is the LLM returning malformed JSON, not an
            exception).
        attempt: Which LLM call attempt produced this verdict
            (``1`` for the first attempt; ``2`` when the first
            attempt returned a response that was unparsable AND the
            service retried). Default ``1`` preserves backward
            compatibility for every existing call site that
            constructs :class:`JudgeResult` positionally with the
            first six fields. Always ``1`` for non-unparsable paths
            (success, error, timeout, no-AIMessages) — the retry
            fires ONLY on ``verdict="unparsable"`` per the
            incident-98b59dd7 contract.
        first_unparsable_excerpt: On a 2-attempt outcome
            (``attempt == 2`` and the second attempt also returned
            ``verdict="unparsable"``), this carries the truncated
            + redacted raw response of attempt 1 so the
            ``event=leader_completion_gate_judge`` /
            ``event=leader_completion_gate_marker_judge`` log rows
            can surface the LLM output verbatim without re-fetching.
            On attempt 2 with a successful parse, this carries the
            truncated + redacted raw response of attempt 1 (the
            unparseable one) — operators can see what the first call
            returned even though the retry path was the one that
            produced the final verdict. ``None`` when ``attempt == 1``
            (no first-unparsable happened). The excerpt is capped at
            :data:`JUDGE_EXCERPT_MAX_CHARS` (400 chars) and
            whitespace-normalized; secret-shaped substrings (bearer
            / api-key / token-shaped) are redacted before stamping
            onto the log row.
    """

    is_complete_report: bool
    verdict: str
    reason: str
    model: str
    latency_ms: int
    error_class: str | None = None
    attempt: int = 1
    first_unparsable_excerpt: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Model resolution — honors OPENAI_MODEL_KEYWORDS with fallback
# ─────────────────────────────────────────────────────────────────────────────


def resolve_judge_model(config: "Config") -> str:
    """Resolve the judge model honoring :attr:`LLMConfig.model_keywords`.

    Mirrors :func:`daemon.services.keyword_extraction.extract_keywords`
    resolution: ``config.llm.model_keywords`` when non-empty,
    ``config.llm.model`` otherwise. ``model_keywords`` is the
    ``OPENAI_MODEL_KEYWORDS`` env var (consumed via ``config.yaml``
    interpolation). Operators typically pin this to ``"quick"`` to
    mirror the explorer agent's per-instance model.

    Args:
        config: A loaded :class:`Config` (caller responsibility — the
            judge does not call ``load_config``; that lives in the
            wiring layer).

    Returns:
        The model string to use for the judge call. Empty string is
        impossible because :attr:`LLMConfig.model` defaults to
        ``"gpt-4"`` and :attr:`LLMConfig.model_keywords` always falls
        back to :attr:`LLMConfig.model` via
        :func:`LLMConfig.set_title_model_fallback`.
    """
    model_keywords = (config.llm.model_keywords or "").strip()
    return model_keywords or config.llm.model


# ─────────────────────────────────────────────────────────────────────────────
# Window slicing — bounded, even-budget truncation
# ─────────────────────────────────────────────────────────────────────────────


def _slice_judge_window(
    messages: list["BaseMessage"],
    *,
    window: int,
) -> list["BaseMessage"]:
    """Return the last ``window`` AIMessages (with tool_calls preserved).

    The judge inspects ONLY AI-authored messages — the gate has
    already validated tool-call presence in the same window, and
    tool calls (without surrounding text) are part of "is this a
    report?" (an empty tool_call AIMessage is NOT a report).

    Args:
        messages: Full LangGraph message list (any order).
        window: How many tail AIMessages to include. Clamped to
            ``[1, JUDGE_MAX_WINDOW]``.

    Returns:
        A list of the last ``window`` AIMessages in chronological
        order. May be shorter than ``window`` if the input has fewer
        AIMessages.
    """
    from langchain_core.messages import AIMessage

    bounded_window = max(1, min(window, JUDGE_MAX_WINDOW))
    ai_messages = [m for m in messages if isinstance(m, AIMessage)]
    return ai_messages[-bounded_window:]


def _format_window_for_judge(
    messages: list["BaseMessage"],
    *,
    per_message_budget: int,
) -> str:
    """Format the AIMessages slice into a single user-role payload.

    Each message is rendered as ``[<index>] <role>: <content>``. The
    budget is shared evenly across the slice so the total stays
    below ``per_message_budget * len(messages)`` (a tighter bound
    than ``JUDGE_MAX_INPUT_CHARS`` to leave headroom for the system
    prompt in the API request).

    Args:
        messages: The window slice (AIMessages only).
        per_message_budget: Per-message char budget. Caller computes
            this from ``JUDGE_MAX_INPUT_CHARS / max(1, len(messages))``.

    Returns:
        A single newline-joined string ready to be the user-role
        content of the judge request.
    """
    lines: list[str] = []
    for idx, msg in enumerate(messages, start=1):
        content = msg.content
        if isinstance(content, list):
            # LangChain list-of-blocks content (e.g. text + reasoning
            # blocks); flatten to plain text for the judge.
            flat_parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    flat_parts.append(str(block.get("text", "")))
                else:
                    flat_parts.append(str(block))
            content = " ".join(flat_parts)
        content_str = str(content) if content else ""
        if len(content_str) > per_message_budget:
            # S4 review fix — when the 12k cap bites on a single
            # message, the tail marker is ``"... [truncated]"`` so the
            # LLM can tell the message was cut (not just that the
            # tail's last three chars happen to be ellipsis). The
            # explicit ``[truncated]`` tag is also grep-friendly for
            # operator forensics on judge input logs.
            content_str = content_str[: per_message_budget - len("... [truncated]")] + "... [truncated]"
        lines.append(f"[{idx}] {content_str}")
    return "\n\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# JSON parsing — strict, conservative on any ambiguity
# ─────────────────────────────────────────────────────────────────────────────


_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _parse_judge_response(raw_text: str) -> tuple[bool, str] | None:
    """Strictly parse the judge's JSON response.

    Accepts ONLY a single JSON object on a line (the system prompt
    demands it). Tolerant of a single surrounding ``{...}`` somewhere
    in the text (an LLM may emit the JSON after a leading whitespace
    or a trailing newline). Any other shape — including markdown code
    fences, multiple objects, or prose — is unparsable.

    Deliberate design notes (do not change without rereview):

    * **Code-fence leakage** — when ``text`` starts with ```` ``` ````
      we strip the outermost fence (``json`` or empty) and re-parse.
      This is the ONE non-strict tolerance because real LLMs
      occasionally wrap JSON in fences despite the system prompt's
      "no markdown, no code fences" instruction.
    * **Substring fallback** — on a ``JSONDecodeError`` we attempt a
      single non-greedy ``{.*?}`` match anywhere in the text and
      re-parse. If the LLM emitted preamble prose + JSON (also a
      prompt violation), we extract the first object. **First-match
      WINS on a multi-object response** — this is DELIBERATE: the
      substring fallback is the LAST RESORT, multi-object output is
      a system-prompt violation, and the conservative caller still
      treats ``None`` (no match) as ``unparsable`` → NOT-a-report. A
      multi-object response that successfully extracts the first
      object is itself anomalous output and is NOT upgraded by the
      parser; the verdict it surfaces is whatever the first object
      carries (the LLM that emits two objects is also unlikely to
      be emitting a coherent report). The behavior is pinned by
      :func:`test_parse_judge_response_unparsable_when_multiple_objects`
      in ``tests/unit/test_attestation_report_judge.py`` — any future
      change to "match the LAST object" or "unparse on multi-object"
      must consciously amend that test.

    Args:
        raw_text: The LLM's response text. Already truncated to
            :data:`JUDGE_MAX_OUTPUT_CHARS` by the caller.

    Returns:
        ``(is_complete_report, reason)`` on parse success.
        ``None`` on any parse ambiguity (the caller treats
        ``None`` as ``unparsable`` — conservative).
    """
    if not raw_text:
        return None
    text = raw_text.strip()
    if text.startswith("```"):
        # Code-fence leakage despite the strict prompt — strip the
        # outermost fence so the inner JSON can parse. This is the
        # ONE shape we tolerate past strict-mode because real LLMs
        # occasionally wrap JSON in fences despite "no markdown"
        # instructions.
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try a substring match for a single JSON object — the LLM
        # may have emitted preamble prose + JSON, which is also a
        # leak. Conservative: only one match.
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    verdict_value = parsed.get("is_complete_report")
    reason_value = parsed.get("reason", "")
    if not isinstance(verdict_value, bool):
        return None
    if not isinstance(reason_value, str):
        reason_value = str(reason_value)
    reason_value = reason_value.strip()
    # Cap reason length defensively (LLM may emit a long rationale).
    if len(reason_value) > 240:
        reason_value = reason_value[:237] + "..."
    return verdict_value, reason_value


# ─────────────────────────────────────────────────────────────────────────────
# Excerpt shaping — forensic logging for unparsable responses
# (incident 98b59dd7, 2026-09-16, class D judge false-negative)
# ─────────────────────────────────────────────────────────────────────────────


#: Patterns that look like secret-shaped strings — replaced with the
#: sentinel before stamping the excerpt onto the canonical log row.
#: Each pattern is a conservative regex; we redact on KEYWORD MATCH,
#: not on validation. Cases covered:
#:
#: * ``bearer <token>`` — Authorization header prefix + value
#: * ``api[_-]?key=<value>`` / ``api[_-]?key: <value>`` — query-style
#:   or JSON-style API key
#: * ``token=<value>`` / ``token: <value>`` — bearer-style tokens
#: * ``secret=<value>`` / ``secret: <value>`` — generic secrets
#:
#: These are deliberately conservative: the LLM does not normally
#: echo secrets, but a buggy proxy / a mis-trained model can.
#: The redaction is a defense-in-depth for log scraping — the
#: canonical log line already elides the LLM input's secrets via
#: the upstream ``_format_window_for_judge`` truncation. The
#: excerpt here is the RESPONSE, not the request, so the surface is
#: narrower; still, the kill cost of a stray key in the log row is
#: asymmetric.
_SECRET_SHAPE_PATTERNS: tuple[str, ...] = (
    # ``bearer <token>`` — Authorization header prefix + value.
    r"\bbearer\s+[A-Za-z0-9._\-]{8,}",
    # ``api_key`` / ``api-key`` / ``apikey`` in three shapes:
    #   * query-style: ``api_key=value``
    #   * env-style:   ``api_key=value``
    #   * JSON-style:  ``"api-key": "value"``  (the closing quote of
    #     the key is matched by the ``\W*`` separator)
    r"\bapi[_-]?key\W*[=:]\W*[A-Za-z0-9._\-]{8,}",
    # ``token=value`` (env / query) and ``"token": "value"`` (JSON).
    r"\btoken\W*[=:]\W*[A-Za-z0-9._\-]{8,}",
    # ``secret=value`` (env / query) and ``"secret": "value"`` (JSON).
    r"\bsecret\W*[=:]\W*[A-Za-z0-9._\-]{8,}",
)
_SECRET_REDACT_RE = re.compile(
    "(?i)" + "|".join(_SECRET_SHAPE_PATTERNS)
)
_SECRET_REDACT_SENTINEL: str = "[REDACTED]"

#: Trailing glue (``-``/``.``/``_``) stripped off a matched run and
#: re-emitted after the sentinel, so run-final punctuation (e.g. a
#: sentence-final dot after ``key=value.``) survives redaction instead
#: of being swallowed into ``[REDACTED]``. Glue chars alone are never
#: secret material, so the trim is leak-free.
_SECRET_TRAILING_GLUE_CHARS = "-._"


def _redact_secrets(text: str) -> str:
    """Replace secret-shaped substrings with :data:`_SECRET_REDACT_SENTINEL`.

    Defense-in-depth for the unparsable-excerpt log surface. Conservative
    regex match — false positives (legitimate prose mentioning "token
    economy" or "bearer of good news") are acceptable because the
    redaction sentinel is a clear signal to operators. False negatives
    (a real key that does not match the patterns) are still
    :data:`JUDGE_EXCERPT_MAX_CHARS` chars away from the full log row
    — the canonical log row already elides request-side secrets via the
    upstream truncation, and the response-side surface is narrower.

    Boundary policy (incident 98b59dd7 review): the secret run ends at
    the first char outside ``[A-Za-z0-9._\\-]``. Prose separated from
    the run by whitespace/punctuation is preserved; run-final glue is
    re-emitted after the sentinel. A run GLUED to prose with no
    delimiter has an undetectable boundary — every char is class-valid
    — so the whole run is redacted (safe-by-default; we do not guess
    where the secret ends). A trailing ``(?!\\w)`` lookahead was
    evaluated and rejected: no-op for glued ASCII shapes (the run
    already ends at whitespace/EOL, where the lookahead holds) and it
    un-redacts Unicode-adjacent matches (backtracking fails the whole
    pattern, leaking the ASCII secret fragment).
    """
    if not text:
        return text

    def _replace(match: "re.Match[str]") -> str:
        run = match.group(0)
        stripped = run.rstrip(_SECRET_TRAILING_GLUE_CHARS)
        glue = run[len(stripped):]
        return _SECRET_REDACT_SENTINEL + glue

    return _SECRET_REDACT_RE.sub(_replace, text)


def _truncate_excerpt(text: str, *, cap: int = JUDGE_EXCERPT_MAX_CHARS) -> str:
    """Truncate ``text`` to ``cap`` chars after whitespace-normalization.

    The canonical log row carries the excerpt verbatim; we want a
    bounded, grep-friendly surface. Steps:

    1. Collapse runs of whitespace to a single space (so a runaway
       LLM that emits 1000 newlines does not stretch the log row).
    2. Strip leading / trailing whitespace.
    3. Truncate to ``cap`` chars; if truncation happened, append an
       explicit ``" [truncated]"`` tail marker (12 chars including
       the space) so operators can tell the excerpt was cut from an
       excerpt that just happened to end mid-sentence. Mirror of the
       ``"[truncated]"`` marker used by
       :func:`_format_window_for_judge` for the request-side cap.
    """
    if not text:
        return ""
    # Normalize whitespace: collapse runs to a single space.
    normalized = " ".join(text.split())
    if len(normalized) <= cap:
        return normalized
    # The trailing marker is included in the cap budget — the marker
    # itself is 12 chars ("[truncated]" + leading space).
    tail_marker = " [truncated]"
    keep = max(0, cap - len(tail_marker))
    return normalized[:keep] + tail_marker


def _shape_unparsable_excerpt(raw_text: str) -> str:
    """Compose the final log-ready excerpt from a raw LLM response.

    Chained helper — applies :func:`_redact_secrets` then
    :func:`_truncate_excerpt` to the raw LLM text. The order matters:
    redaction first (operates on the raw value so we don't lose secret
    markers to whitespace collapse), then truncation. The resulting
    string is the forensic surface stamped onto the canonical log row
    for ``verdict="unparsable"`` outcomes.
    """
    return _truncate_excerpt(_redact_secrets(raw_text))


# ─────────────────────────────────────────────────────────────────────────────
# LLM call — async, bounded, fail-safe
# ─────────────────────────────────────────────────────────────────────────────


async def _invoke_judge_llm(
    config: "Config",
    user_payload: str,
    *,
    timeout_s: float,
    system_prompt: str = JUDGE_SYSTEM_PROMPT,
) -> tuple[str, str]:
    """Run the judge's LLM call. Returns ``(raw_text, model)``.

    Constructs a fresh ``ThinkingChatOpenAI`` with the resolved
    quick-model + the standard HA failover facade (mirrors the
    pattern at ``daemon/services/keyword_extraction.py:384-387``).

    Args:
        config: Loaded :class:`Config`.
        user_payload: The formatted judge payload (single string).
        timeout_s: Wall-clock cap. The HA facade's
            ``wall_clock_cap_s`` is the primary defense; the
            ``asyncio.wait_for`` belt-and-braces wraps the entire
            ``to_thread`` invocation.
        system_prompt: The system prompt for THIS judge invocation.
            Defaults to :data:`JUDGE_SYSTEM_PROMPT` (the legacy
            window judge). The Stage-2 fused judge
            (:func:`judge_fused_bundle_async`) passes
            :data:`FUSED_JUDGE_SYSTEM_PROMPT` — model resolution,
            timeout binding, and the HA facade are SHARED so the two
            judges can never drift on transport configuration
            (resolver-unification Stage 2, 2026-09-16).

    Returns:
        ``(raw_text, model_name)``. ``model_name`` is what
            :func:`resolve_judge_model` resolved at call time — the
            caller surfaces this on the :class:`JudgeResult`.

    Raises:
        Any exception from the LLM call (caller catches and converts
        to :class:`JudgeResult` with ``verdict="error"``).
        :class:`asyncio.TimeoutError` on timeout (catcher converts to
            ``verdict="timeout"``).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from ..graph import ThinkingChatOpenAI, clean_llm_config
    from .llm_failover import wrap_langchain_failover

    model = resolve_judge_model(config)
    # ``base_url_backup`` is consumed by the HA facade from the RAW
    # config dict (F1 kwarg hygiene — ``clean_llm_config`` mutates
    # in place and strips the backup).
    # ``request_timeout`` is bound per-attempt to
    # ``min(resolved_timeout, config.llm.request_timeout or resolved_timeout)``
    # mirroring the compaction-site precedent at ``daemon/manager.py:398``.
    # The ``resolved_timeout`` is the Pattern C cached value from
    # :mod:`daemon.services.attestation_judge_timeout_resolver`
    # (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S``, default 25.0s,
    # min clamp 5.0s). The HA facade's ``wall_clock_cap_s`` bounds only
    # BETWEEN attempts (``daemon/services/llm_failover.py:174``); a hung
    # FIRST attempt can pin the ``asyncio.to_thread`` worker because
    # ``asyncio.wait_for`` cannot cancel a to_thread worker — without a
    # per-attempt HTTP timeout the executor thread stays blocked for
    # the full ``config.llm.request_timeout`` (default 610s).
    resolved_timeout = _resolver_get_judge_timeout_s()
    judge_request_timeout = min(
        resolved_timeout,
        config.llm.request_timeout or resolved_timeout,
    )
    llm_config = {
        "base_url": config.llm.base_url,
        "base_url_backup": config.llm.base_url_backup,
        "api_key": config.llm.api_key,
        "model": model,
        "temperature": 0.0,
        "request_timeout": judge_request_timeout,
        # Judge has no tools — straight chat completion. No
        # ``bind_tools`` call.
        "default_headers": {
            "x-proxy-app": "ensemble",
            "x-proxy-interleaved-thinking": "True",
            **(
                {"X-LLMProxy-Buffer-Response": "true"}
                if config.llm.buffer_response_header
                else {}
            ),
        },
    }
    llm = ThinkingChatOpenAI(**clean_llm_config(dict(llm_config)))
    llm_wrapper = wrap_langchain_failover(
        llm, llm_config, wall_clock_cap_s=timeout_s
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_payload),
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
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        raw_text = " ".join(parts)
    else:
        raw_text = str(content) if content else ""
    return raw_text, model


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points — async + sync wrappers
# ─────────────────────────────────────────────────────────────────────────────


#: Internal NamedTuple carrying the result of a single LLM attempt.
#: The retry logic in :func:`judge_completion_report_async` treats
#: ``ok`` / ``timeout`` / ``error`` distinctly:
#:
#: * ``ok`` → the model responded; ``raw_text`` is the body (may
#:   still be unparsable — the outer function decides whether to
#:   retry on unparsable JSON).
#: * ``timeout`` → the call raised :class:`asyncio.TimeoutError`;
#:   ``raw_text`` is empty; ``error_class`` is ``"TimeoutError"``.
#: * ``error`` → the call raised any other exception;
#:   ``raw_text`` is empty; ``error_class`` is the exception class
#:   name (mirrors the prior ``JudgeResult.error_class`` contract).
#:
#: ``latency_ms`` is the wall-clock latency of the attempt
#: (excludes overhead). The outer :class:`JudgeResult.latency_ms`
#: is the CUMULATIVE latency across all attempts (so operators
#: can correlate log-row latency with retry count).
class _AttemptOutcome(NamedTuple):
    kind: str
    latency_ms: int
    raw_text: str
    model: str
    error_class: str | None


async def judge_completion_report_async(
    messages: list["BaseMessage"],
    *,
    config: "Config",
    window: int = JUDGE_DEFAULT_WINDOW,
    timeout_s: float | None = None,
) -> JudgeResult:
    """Async judge — returns a :class:`JudgeResult`.

    Never raises. Every failure path (timeout / exception / unparsable
    JSON) returns a :class:`JudgeResult` with ``is_complete_report=False``
    so the gate's deny+nudge fall-through is always available.

    Args:
        messages: Full LangGraph message list. The judge slices the
            last ``window`` AIMessages internally.
        config: Loaded :class:`Config`.
        window: How many tail AIMessages to inspect. Clamped to
            ``[1, JUDGE_MAX_WINDOW]``. Default
            :data:`JUDGE_DEFAULT_WINDOW` (3) — matches the gate's
            default window so the two views align.
        timeout_s: Wall-clock cap PER ATTEMPT. ``None`` (default) →
            resolve via the Pattern C cached-global at
            :mod:`daemon.services.attestation_judge_timeout_resolver`
            (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S``,
            default :data:`JUDGE_TIMEOUT_S` 25.0s, min clamp 5.0s).
            Explicit numeric values pass through unchanged (callers
            pin a tighter cap when needed). Each retry attempt
            receives its OWN timeout window — total worst-case
            wall-clock = 2 × ``timeout_s`` (incident 98b59dd7
            contract; see :attr:`JudgeResult.attempt`).

    Returns:
        :class:`JudgeResult` — populated on every path (success /
        error / timeout / unparsable). :attr:`JudgeResult.attempt`
        distinguishes first-call outcomes (``attempt=1``) from
        retry-after-unparsable outcomes (``attempt=2``); the
        retry fires ONLY when the LLM responded but the response
        did not parse (the model RESPONDED — not on
        timeout / HTTP / LLM exceptions). On any path where the
        kill-switch :func:`daemon.services.attestation_judge_resolver.
        is_llm_judge_enabled` returns ``False``, NO judge calls are
        attempted and NO retries fire (zero-call contract).

    Retry semantics (incident 98b59dd7, 2026-09-16)
    ----------------------------------------------

    The retry is INTENTIONAL and NARROW:

    * **Fires when**: the model responded with a body AND
      :func:`_parse_judge_response` returned ``None`` (strict
      JSON object parse failed — code-fence leakage beyond the
      single tolerated layer, multi-object response, prose-only,
      wrong-shape response, etc.).
    * **Does NOT fire when**: the call raised :class:`asyncio.
      TimeoutError` (verdict ``"timeout"`` — first attempt), or
      any other exception (verdict ``"error"`` — first attempt).
      Existing fail-safe semantics for those paths are untouched.
    * **Same input, fresh call**: the retry uses identical
      ``config`` + ``user_payload`` + ``timeout_s``; only the
      underlying LLM HTTP request is re-issued. There is no
      prompt mutation, no model swap, no backoff delay (the
      intent is "transient parse-shape fluke" recovery, not
      rate-limit recovery).
    * **Worst-case wall-clock**: ``2 × timeout_s`` per attempt
      because each attempt owns its full timeout window. With
      the default 25.0s timeout, worst-case = 50.0s. The HA
      facade's ``wall_clock_cap_s`` and ``asyncio.wait_for``
      bounds still apply to each attempt individually — the
      retry does not stack timeouts across attempts.
    * **Outcome preservation**: if the retry parses, the verdict
      is the retry's verdict (``"yes"`` / ``"no"``); if the retry
      is also unparsable, the verdict stays ``"unparsable"`` with
      :attr:`JudgeResult.attempt` = ``2``. The original
      conservative fail-safe (deny+nudge on the gate's deny
      path, hint route per marker path) is preserved end-to-end.
    * **Logging forensics**: a non-empty
      :attr:`JudgeResult.first_unparsable_excerpt` carries the
      redacted + truncated raw response of attempt 1 onto the
      ``event=leader_completion_gate_judge`` /
      ``event=leader_completion_gate_marker_judge`` log rows
      (capped at :data:`JUDGE_EXCERPT_MAX_CHARS` chars). The
      :attr:`JudgeResult.reason` field is no longer empty on
      unparsable rows — incident 98b59dd7 root cause was an
      EMPTY ``reason`` on the unparsable verdict (operators
      could not diagnose without the raw response).
    """
    start = time.monotonic()
    slice_ = _slice_judge_window(messages, window=window)
    if not slice_:
        # No AIMessages at all — conservative no-report verdict
        # (the gate can't have reached DENIED without at least one
        # AIMessage, but defensive against degenerate embeddings).
        return JudgeResult(
            is_complete_report=False,
            verdict="error",
            reason="no AIMessages to judge",
            model="<none>",
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class="NoAIMessages",
        )
    per_message_budget = max(256, JUDGE_MAX_INPUT_CHARS // max(1, len(slice_)))
    user_payload = _format_window_for_judge(
        slice_, per_message_budget=per_message_budget
    )

    # Resolve the wall-clock cap lazily — ``None`` means "use the
    # env-configured runtime value" (Pattern C cached-global at
    # :mod:`daemon.services.attestation_judge_timeout_resolver`). An
    # explicit numeric ``timeout_s`` (test fixtures, hot-loop callers)
    # passes through unchanged.
    if timeout_s is None:
        timeout_s = _resolver_get_judge_timeout_s()

    # ── Single-attempt inner helper ─────────────────────────────────────
    # Each call owns its own timeout window. The retry policy lives in
    # the outer ``judge_completion_report_async``; the inner helper is
    # "do one attempt, return its raw verdict + latency + raw_text or
    # raise on timeout/error". This separation makes the retry-vs-no-
    # retry decision explicit at the outer call site (no implicit
    # retry propagation through the LLM invoker).
    async def _attempt_once() -> "_AttemptOutcome":
        attempt_start = time.monotonic()
        try:
            raw_text, model_name = await _invoke_judge_llm(
                config, user_payload, timeout_s=timeout_s
            )
        except asyncio.TimeoutError:
            return _AttemptOutcome(
                kind="timeout",
                latency_ms=int((time.monotonic() - attempt_start) * 1000),
                raw_text="",
                model=resolve_judge_model(config),
                error_class="TimeoutError",
            )
        except Exception as exc:  # noqa: BLE001 — judge is best-effort
            return _AttemptOutcome(
                kind="error",
                latency_ms=int((time.monotonic() - attempt_start) * 1000),
                raw_text="",
                model=resolve_judge_model(config),
                error_class=type(exc).__name__,
            )
        return _AttemptOutcome(
            kind="ok",
            latency_ms=int((time.monotonic() - attempt_start) * 1000),
            raw_text=raw_text,
            model=model_name,
            error_class=None,
        )

    # ── Attempt 1 ───────────────────────────────────────────────────────
    first = await _attempt_once()
    if first.kind in {"timeout", "error"}:
        # Conservative fail-safe — NO retry on timeout / error
        # (incident 98b59dd7 contract: retries fire ONLY when the
        # model responded but the response did not parse).
        return JudgeResult(
            is_complete_report=False,
            verdict=first.kind,
            reason="",
            model=first.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=first.error_class,
        )

    # Truncate to JUDGE_MAX_OUTPUT_CHARS before parsing — a runaway
    # LLM response gets parsed against a small window. The
    # truncation is local (the NamedTuple is immutable).
    first_raw_text = first.raw_text
    if len(first_raw_text) > JUDGE_MAX_OUTPUT_CHARS:
        first_raw_text = first_raw_text[:JUDGE_MAX_OUTPUT_CHARS]
    parsed = _parse_judge_response(first_raw_text)
    if parsed is not None:
        is_complete, reason = parsed
        return JudgeResult(
            is_complete_report=is_complete,
            verdict="yes" if is_complete else "no",
            reason=reason,
            model=first.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=None,
        )

    # ── Unparsable on attempt 1 → RETRY (attempt 2) ─────────────────────
    # The model RESPONDED but the JSON did not parse. Same input,
    # fresh call. Retry outcome becomes the final verdict; the
    # first-attempt excerpt is preserved for forensic logging.
    first_unparsable_excerpt = _shape_unparsable_excerpt(first.raw_text)
    second = await _attempt_once()
    if second.kind in {"timeout", "error"}:
        # Retry itself timed out / errored → existing conservative
        # fail-safe; the FIRST attempt was unparsable (logged via
        # ``first_unparsable_excerpt``); the SECOND attempt was a
        # transport failure. Surface the transport error verdict —
        # the reason carries the unparsable surface so operators can
        # see both shapes in the canonical log row.
        return JudgeResult(
            is_complete_report=False,
            verdict=second.kind,
            reason=(
                "judge_response_unparsable (attempt 1); "
                f"{second.kind} on attempt 2"
            ),
            model=second.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=second.error_class,
            attempt=2,
            first_unparsable_excerpt=first_unparsable_excerpt,
        )

    second_raw_text = second.raw_text
    if len(second_raw_text) > JUDGE_MAX_OUTPUT_CHARS:
        second_raw_text = second_raw_text[:JUDGE_MAX_OUTPUT_CHARS]
    parsed_second = _parse_judge_response(second_raw_text)
    if parsed_second is None:
        # Retry also unparsable → existing conservative fail-safe.
        # The reason is no longer empty on this path (incident
        # 98b59dd7 contract); it carries a short descriptor that
        # names the failure shape + the first-attempt excerpt as a
        # head prefix so the log row can be cross-correlated with
        # the structured ``first_unparsable_excerpt`` field.
        return JudgeResult(
            is_complete_report=False,
            verdict="unparsable",
            reason=(
                "judge_response_unparsable on both attempts: "
                f"{first_unparsable_excerpt[:120]}"
            ),
            model=second.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=None,
            attempt=2,
            first_unparsable_excerpt=first_unparsable_excerpt,
        )

    # Retry parsed successfully → final verdict is the retry's
    # verdict. The first attempt's excerpt is STILL preserved on
    # the result for forensics (operators can see what failed
    # even though the gate is flipping to ALLOWED).
    is_complete, reason = parsed_second
    return JudgeResult(
        is_complete_report=is_complete,
        verdict="yes" if is_complete else "no",
        reason=reason,
        model=second.model,
        latency_ms=int((time.monotonic() - start) * 1000),
        error_class=None,
        attempt=2,
        first_unparsable_excerpt=first_unparsable_excerpt,
    )


def judge_completion_report_sync(
    messages: list["BaseMessage"],
    *,
    config: "Config",
    window: int = JUDGE_DEFAULT_WINDOW,
    timeout_s: float | None = None,
) -> JudgeResult:
    """Sync entry point — drives the async judge from a sync caller.

    The gate node is an async function and calls the async entry point
    directly via :func:`judge_completion_report_async`. This sync
    wrapper exists for callers that run OUTSIDE an event loop
    (worker threads spawned via :func:`asyncio.to_thread`, scripts,
    and unit tests using a fresh event loop). It uses
    :func:`asyncio.run` and therefore cannot be called from inside a
    running event loop — callers in that shape must use the async
    entry point instead.

    Never raises. Returns the same :class:`JudgeResult` as the async
    entry point.

    Args:
        messages: Full LangGraph message list.
        config: Loaded :class:`Config`.
        window: How many tail AIMessages to inspect.
        timeout_s: Wall-clock cap. ``None`` (default) → resolve via the
            Pattern C cached-global (env ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S``).
            Explicit numeric values pass through unchanged.

    Returns:
        :class:`JudgeResult` — populated on every path.
    """
    return asyncio.run(
        judge_completion_report_async(
            messages,
            config=config,
            window=window,
            timeout_s=timeout_s,
        )
    )

# ─────────────────────────────────────────────────────────────────────────────
# Stage-2 fused judge (resolver-unification, 2026-09-16) — ONE judge call site
#
# The unified 3-source completion resolver flips AUTHORITY at the completion
# seam (spec ``resolver-unification.md`` §4/§4.3 + user-locked deltas
# Δ1–Δ4, DP-5 REJECTED). The fused judge consumes the Stage-1 assembled
# evidence bundle (``attestation_resolver_activation.assemble_fused_bundle``
# — A ≤3000 + B ≤6000 + C ≤3000, total ≤12000, id-redacted) as its ENTIRE
# user payload and returns the §4.1 verdict JSON:
#
#     {"verdict": "complete"|"not_complete",
#      "evidence_cited": ["<short quote or field>", ...],
#      "advisory_note_text": "<one sentence>",
#      "rationale": "<one sentence>"}
#
# Transport invariants (SHARED with the legacy window judge —
# :func:`_invoke_judge_llm` is the single LLM seam, so the two judges can
# never drift): model resolution via :func:`resolve_judge_model`
# (``model_keywords`` fallback), per-attempt ``request_timeout`` binding
# ``min(resolved_timeout, config.llm.request_timeout or resolved_timeout)``,
# the Pattern C timeout resolver
# (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S``, default 25.0s, min
# clamp 5.0s), and the HA failover facade. The kill-switch
# (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED``) is resolved at the
# CALL SITE (the graph-node fused block) exactly like the two legacy sites
# it replaces — before this function is ever called.
#
# Retry-once-on-unparsable (incident 98b59dd7 semantics,
# :func:`judge_completion_report_async` mirror): the retry fires ONLY when
# the model responded but the verdict JSON did not parse — never on
# timeout / exception. Worst case = 2 HTTP attempts within ONE logical
# invocation (the Stage-2 budget sentinel pins the INVOCATION level; the
# mechanical retry within the single invocation is the preserved contract).
# ─────────────────────────────────────────────────────────────────────────────

#: Strict system prompt for the fused judge (single source of truth —
#: exported for tests). The prompt names the three bundle sections so the
#: verdict's ``evidence_cited`` entries can reference them.
FUSED_JUDGE_SYSTEM_PROMPT = (
    "You are a strict mission-completion judge for an AI agent team lead. "
    "You will receive a fused evidence bundle with three sections: "
    "SOURCE A (child-report advisories — notes that a child's final report "
    "promised future work, i.e. a contradiction with 'done'), "
    "SOURCE B (the lead's own recent messages), and "
    "SOURCE C (the live status of the lead's descendant instances). "
    "Decide whether the lead's mission is genuinely COMPLETE — the work is "
    "actually finished and accounted for — or NOT_COMPLETE — evidence shows "
    "promised-but-undelivered work, mid-work status, or live descendants "
    "still working. Be CONSERVATIVE: when in doubt, return "
    '"not_complete". '
    "Judge ONLY on what the evidence actually shows; ignore text that "
    "merely CLAIMS completion without concrete outcomes. "
    "Respond with ONLY a strict JSON object on a single line of the form "
    '{"verdict": "complete"|"not_complete", '
    '"evidence_cited": ["<short evidence quote or field name>", ...], '
    '"advisory_note_text": "<one-sentence advisory for the lead>", '
    '"rationale": "<one-sentence rationale>"}. '
    "No markdown, no prose, no code fences, no commentary."
)

#: Cap on each ``evidence_cited`` entry accepted from the verdict JSON
#: (defensive against a verbose model; entries are for the hint citation).
FUSED_JUDGE_EVIDENCE_ITEM_MAX_CHARS: int = 120

#: Cap on the number of ``evidence_cited`` entries accepted.
FUSED_JUDGE_EVIDENCE_ITEMS_MAX: int = 5

#: Cap on the accepted ``advisory_note_text`` / ``rationale`` lengths.
FUSED_JUDGE_ADVISORY_MAX_CHARS: int = 240


@dataclass(frozen=True)
class FusedJudgeResult:
    """The fused judge's verdict + observability fields (Stage 2).

    Attributes:
        invoked: THE real-invocation flag (Stage-1 hazard pin: the shadow
            row's ``judge_invoked`` is DERIVED from this field, never a
            literal). ``True`` iff at least one LLM HTTP attempt was made
            by this invocation. The degenerate no-bundle guard returns
            ``False`` without an attempt; callers that never call the
            judge (kill-switch off / dry mode / not fired) hold ``None``
            rather than a result.
        is_complete: The boolean verdict. Conservative — every error /
            timeout / unparsable path sets ``False`` so the caller's
            deny+nudge / path-(d) fall-through is always available
            (DP-5 REJECTED: no fail-safe allow anywhere).
        verdict: ``"complete"`` / ``"not_complete"`` (LLM-confirmed) or
            ``"error"`` / ``"timeout"`` / ``"unparsable"``.
        evidence_cited: Bounded tuple of evidence references from the
            verdict JSON (Δ4 — the hint citation source). Empty on every
            non-complete-verdict path by construction.
        advisory_note_text: The verdict's one-sentence advisory (Δ4).
            Empty string on non-verdict paths.
        rationale: The verdict's one-sentence rationale.
        model: The model that served the call (``"<none>"`` on the
            no-bundle guard).
        latency_ms: Cumulative wall-clock latency across attempts.
        error_class: Exception class name on error paths; ``None``
            otherwise.
        attempt: Which attempt produced the verdict (``2`` when the
            retry-after-unparsable fired). Always ``1`` on non-unparsable
            paths.
        first_unparsable_excerpt: Truncated + redacted raw response of
            attempt 1 when ``attempt == 2`` (incident 98b59dd7 forensic
            contract). ``None`` when ``attempt == 1``.
    """

    invoked: bool
    is_complete: bool
    verdict: str
    evidence_cited: tuple[str, ...] = ()
    advisory_note_text: str = ""
    rationale: str = ""
    model: str = "<none>"
    latency_ms: int = 0
    error_class: str | None = None
    attempt: int = 1
    first_unparsable_excerpt: str | None = None


def _parse_fused_judge_response(
    raw_text: str,
) -> tuple[bool, tuple[str, ...], str, str] | None:
    """Strictly parse the fused judge's verdict JSON.

    Mirrors :func:`_parse_judge_response`'s tolerance shape exactly (the
    single tolerated code-fence layer, the conservative single
    substring-fallback, first-match-wins) so the retry-on-unparsable
    trigger conditions are IDENTICAL between the two judges. The
    load-bearing field is ``verdict`` — it must be exactly
    ``"complete"`` or ``"not_complete"`` (a string); anything else is
    unparsable. ``evidence_cited`` / ``advisory_note_text`` /
    ``rationale`` are tolerated-missing (normalized defaults) and
    defensively capped; wrong types normalize, never fail the parse.

    Returns:
        ``(is_complete, evidence_cited, advisory_note_text, rationale)``
        on parse success; ``None`` on any parse ambiguity (the caller
        treats ``None`` as ``unparsable`` — conservative).
    """
    if not raw_text:
        return None
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    verdict_value = parsed.get("verdict")
    if verdict_value not in ("complete", "not_complete"):
        return None
    is_complete = verdict_value == "complete"
    evidence: tuple[str, ...] = ()
    raw_evidence = parsed.get("evidence_cited")
    if isinstance(raw_evidence, (list, tuple)):
        items: list[str] = []
        for item in raw_evidence[:FUSED_JUDGE_EVIDENCE_ITEMS_MAX]:
            item_text = str(item).strip()
            if not item_text:
                continue
            if len(item_text) > FUSED_JUDGE_EVIDENCE_ITEM_MAX_CHARS:
                item_text = (
                    item_text[: FUSED_JUDGE_EVIDENCE_ITEM_MAX_CHARS - 1] + "…"
                )
            items.append(item_text)
        evidence = tuple(items)

    def _capped_str(value: object) -> str:
        text_value = value if isinstance(value, str) else str(value or "")
        text_value = text_value.strip()
        if len(text_value) > FUSED_JUDGE_ADVISORY_MAX_CHARS:
            return text_value[: FUSED_JUDGE_ADVISORY_MAX_CHARS - 3] + "..."
        return text_value

    return (
        is_complete,
        evidence,
        _capped_str(parsed.get("advisory_note_text", "")),
        _capped_str(parsed.get("rationale", "")),
    )


async def judge_fused_bundle_async(
    bundle_text: str,
    *,
    config: "Config",
    timeout_s: float | None = None,
) -> FusedJudgeResult:
    """Async fused judge — ONE logical invocation over the evidence bundle.

    Never raises. Every failure path (timeout / exception / unparsable
    verdict after retry) returns a :class:`FusedJudgeResult` with
    ``is_complete=False`` so the caller's conservative mapping
    (deny-band deny+nudge / marker+A-band path-(d)) is always available.
    DP-5 is REJECTED: judge error NEVER fail-safe-allows.

    Args:
        bundle_text: The Stage-1 assembled fused evidence bundle text
            (already capped ≤12000 chars + id-redacted by
            :func:`attestation_resolver_activation.assemble_fused_bundle`
            — this function does NOT re-truncate; the bundle is the
            payload verbatim).
        config: Loaded :class:`Config`.
        timeout_s: Wall-clock cap PER ATTEMPT. ``None`` (default) →
            resolve via the Pattern C cached-global (same resolver as
            the legacy judge — ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_
            TIMEOUT_S``, default 25.0s, min clamp 5.0s). Each retry
            attempt receives its OWN timeout window; worst-case
            wall-clock = 2 × ``timeout_s``.

    Returns:
        :class:`FusedJudgeResult` — populated on every path.
        :attr:`FusedJudgeResult.attempt` distinguishes first-call
        outcomes (``attempt=1``) from retry-after-unparsable outcomes
        (``attempt=2``); the retry fires ONLY when the model responded
        but the verdict JSON did not parse — never on timeout /
        exception (incident 98b59dd7 contract, mirrored from
        :func:`judge_completion_report_async`).
    """
    start = time.monotonic()
    if not bundle_text or not bundle_text.strip():
        # Degenerate guard (mirror of the no-AIMessages guard): a bundle
        # exists by construction whenever the resolver fires, but a
        # defensive empty input is a conservative not-complete error
        # WITHOUT an LLM attempt (invoked=False — no real invocation).
        return FusedJudgeResult(
            invoked=False,
            is_complete=False,
            verdict="error",
            rationale="empty fused bundle",
            error_class="EmptyBundle",
            latency_ms=int((time.monotonic() - start) * 1000),
        )
    if timeout_s is None:
        timeout_s = _resolver_get_judge_timeout_s()

    async def _attempt_once() -> "_AttemptOutcome":
        attempt_start = time.monotonic()
        try:
            raw_text, model_name = await _invoke_judge_llm(
                config,
                bundle_text,
                timeout_s=timeout_s,
                system_prompt=FUSED_JUDGE_SYSTEM_PROMPT,
            )
        except asyncio.TimeoutError:
            return _AttemptOutcome(
                kind="timeout",
                latency_ms=int((time.monotonic() - attempt_start) * 1000),
                raw_text="",
                model=resolve_judge_model(config),
                error_class="TimeoutError",
            )
        except Exception as exc:  # noqa: BLE001 — judge is best-effort
            return _AttemptOutcome(
                kind="error",
                latency_ms=int((time.monotonic() - attempt_start) * 1000),
                raw_text="",
                model=resolve_judge_model(config),
                error_class=type(exc).__name__,
            )
        return _AttemptOutcome(
            kind="ok",
            latency_ms=int((time.monotonic() - attempt_start) * 1000),
            raw_text=raw_text,
            model=model_name,
            error_class=None,
        )

    # Attempt 1.
    first = await _attempt_once()
    if first.kind in {"timeout", "error"}:
        # Conservative fail-safe — NO retry on timeout / error.
        return FusedJudgeResult(
            invoked=True,
            is_complete=False,
            verdict=first.kind,
            model=first.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=first.error_class,
        )

    first_raw_text = first.raw_text
    if len(first_raw_text) > FUSED_JUDGE_MAX_OUTPUT_CHARS:
        first_raw_text = first_raw_text[:FUSED_JUDGE_MAX_OUTPUT_CHARS]
    parsed = _parse_fused_judge_response(first_raw_text)
    if parsed is not None:
        is_complete, evidence, advisory, rationale = parsed
        return FusedJudgeResult(
            invoked=True,
            is_complete=is_complete,
            verdict="complete" if is_complete else "not_complete",
            evidence_cited=evidence,
            advisory_note_text=advisory if not is_complete else "",
            rationale=rationale,
            model=first.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=None,
        )

    # Unparsable on attempt 1 → RETRY (attempt 2). Same input, fresh
    # call; the first-attempt excerpt is preserved for forensics.
    first_unparsable_excerpt = _shape_unparsable_excerpt(first.raw_text)
    second = await _attempt_once()
    if second.kind in {"timeout", "error"}:
        return FusedJudgeResult(
            invoked=True,
            is_complete=False,
            verdict=second.kind,
            rationale=(
                "fused_judge_response_unparsable (attempt 1); "
                f"{second.kind} on attempt 2"
            ),
            model=second.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=second.error_class,
            attempt=2,
            first_unparsable_excerpt=first_unparsable_excerpt,
        )

    second_raw_text = second.raw_text
    if len(second_raw_text) > FUSED_JUDGE_MAX_OUTPUT_CHARS:
        second_raw_text = second_raw_text[:FUSED_JUDGE_MAX_OUTPUT_CHARS]
    parsed_second = _parse_fused_judge_response(second_raw_text)
    if parsed_second is None:
        return FusedJudgeResult(
            invoked=True,
            is_complete=False,
            verdict="unparsable",
            rationale=(
                "fused_judge_response_unparsable on both attempts: "
                f"{first_unparsable_excerpt[:120]}"
            ),
            model=second.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=None,
            attempt=2,
            first_unparsable_excerpt=first_unparsable_excerpt,
        )

    is_complete, evidence, advisory, rationale = parsed_second
    return FusedJudgeResult(
        invoked=True,
        is_complete=is_complete,
        verdict="complete" if is_complete else "not_complete",
        evidence_cited=evidence,
        advisory_note_text=advisory if not is_complete else "",
        rationale=rationale,
        model=second.model,
        latency_ms=int((time.monotonic() - start) * 1000),
        error_class=None,
        attempt=2,
        first_unparsable_excerpt=first_unparsable_excerpt,
    )
