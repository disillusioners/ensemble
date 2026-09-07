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

* Timeout: :data:`JUDGE_TIMEOUT_S` seconds (default 10.0). Belt-and-braces
  ``asyncio.wait_for`` wraps the facade-wrapped call; the facade's
  ``wall_clock_cap_s`` is the primary defense (the same pattern as
  ``daemon/services/keyword_extraction.py:405-408``).
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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Config
    from langchain_core.messages import BaseMessage


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Bounds (single source of truth — referenced by tests + setup.md)
# ─────────────────────────────────────────────────────────────────────────────

#: Wall-clock cap (seconds) for the judge call. Async-wait_for backstop;
#: the HA facade's wall_clock_cap_s is the primary defense (retry-budget
#: stop_after_delay inside the tenacity retry loop).
JUDGE_TIMEOUT_S: float = 10.0

#: Max chars (UTF-8) of the user-role payload fed to the judge. Each
#: AIMessage content is truncated to ``JUDGE_MAX_INPUT_CHARS / count``
#: so the total stays below the cap; per-message budget is shared
#: evenly across the bounded window. 12000 chars covers the gate's
#: window=3 default + headroom for the system prompt + judge framing.
JUDGE_MAX_INPUT_CHARS: int = 12_000

#: Max chars of the LLM response we'll consider before truncating to
#: parse. The JSON is small by construction (a single object) so the
#: cap is a defensive ceiling against an LLM that returns prose.
JUDGE_MAX_OUTPUT_CHARS: int = 400

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
            Empty string on error / timeout / unparsable paths.
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
    """

    is_complete_report: bool
    verdict: str
    reason: str
    model: str
    latency_ms: int
    error_class: str | None = None


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
# LLM call — async, bounded, fail-safe
# ─────────────────────────────────────────────────────────────────────────────


async def _invoke_judge_llm(
    config: "Config",
    user_payload: str,
    *,
    timeout_s: float,
) -> tuple[str, str]:
    """Run the judge's LLM call. Returns ``(raw_text, model)``.

    Constructs a fresh ``ThinkingChatOpenAI`` with the resolved
    quick-model + the standard HA failover facade (mirrors the
    pattern at ``daemon/services/keyword_extraction.py:384-387``).

    Args:
        config: Loaded :class:`Config`.
        user_payload: The formatted AIMessage slice (single string).
        timeout_s: Wall-clock cap. The HA facade's
            ``wall_clock_cap_s`` is the primary defense; the
            ``asyncio.wait_for`` belt-and-braces wraps the entire
            ``to_thread`` invocation.

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
    # ``request_timeout`` is bound per-attempt to ``min(JUDGE_TIMEOUT_S,
    # config.llm.request_timeout or JUDGE_TIMEOUT_S)`` mirroring the
    # compaction-site precedent at ``daemon/manager.py:398``. The
    # HA facade's ``wall_clock_cap_s`` bounds only BETWEEN attempts
    # (``daemon/services/llm_failover.py:174``); a hung FIRST attempt
    # can pin the ``asyncio.to_thread`` worker because
    # ``asyncio.wait_for`` cannot cancel a to_thread worker — without
    # a per-attempt HTTP timeout the executor thread stays blocked for
    # the full ``config.llm.request_timeout`` (default 610s).
    judge_request_timeout = min(
        JUDGE_TIMEOUT_S,
        config.llm.request_timeout or JUDGE_TIMEOUT_S,
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
        SystemMessage(content=JUDGE_SYSTEM_PROMPT),
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


async def judge_completion_report_async(
    messages: list["BaseMessage"],
    *,
    config: "Config",
    window: int = JUDGE_DEFAULT_WINDOW,
    timeout_s: float = JUDGE_TIMEOUT_S,
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
        timeout_s: Wall-clock cap. Default
            :data:`JUDGE_TIMEOUT_S` (10.0s).

    Returns:
        :class:`JudgeResult` — populated on every path (success /
        error / timeout / unparsable).
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

    try:
        raw_text, model = await _invoke_judge_llm(
            config, user_payload, timeout_s=timeout_s
        )
    except asyncio.TimeoutError:
        return JudgeResult(
            is_complete_report=False,
            verdict="timeout",
            reason="",
            model=resolve_judge_model(config),
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class="TimeoutError",
        )
    except Exception as exc:  # noqa: BLE001 — judge is best-effort
        return JudgeResult(
            is_complete_report=False,
            verdict="error",
            reason="",
            model=resolve_judge_model(config),
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=type(exc).__name__,
        )

    # Truncate to JUDGE_MAX_OUTPUT_CHARS before parsing — a runaway
    # LLM response gets parsed against a small window.
    if len(raw_text) > JUDGE_MAX_OUTPUT_CHARS:
        raw_text = raw_text[:JUDGE_MAX_OUTPUT_CHARS]
    parsed = _parse_judge_response(raw_text)
    if parsed is None:
        return JudgeResult(
            is_complete_report=False,
            verdict="unparsable",
            reason="",
            model=model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=None,
        )
    is_complete, reason = parsed
    return JudgeResult(
        is_complete_report=is_complete,
        verdict="yes" if is_complete else "no",
        reason=reason,
        model=model,
        latency_ms=int((time.monotonic() - start) * 1000),
        error_class=None,
    )


def judge_completion_report_sync(
    messages: list["BaseMessage"],
    *,
    config: "Config",
    window: int = JUDGE_DEFAULT_WINDOW,
    timeout_s: float = JUDGE_TIMEOUT_S,
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
        timeout_s: Wall-clock cap.

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