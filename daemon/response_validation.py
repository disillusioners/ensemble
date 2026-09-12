"""Response validation utilities for LLM interactions.

This module provides structural validation for LLM responses to detect
common failure modes like empty content, truncated responses, and malformed
tool calls.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from langchain_core.messages import AIMessage, BaseMessage

logger = logging.getLogger(__name__)


class LLMResponseValidationError(Exception):
    """Raised when an LLM response fails structural validation."""

    def __init__(self, message: str, response: AIMessage | None = None):
        self.response = response
        super().__init__(message)


class EmptyLLMResponseError(LLMResponseValidationError):
    """Raised when an LLM response is empty-as-entire-answer.

    Subset of :class:`LLMResponseValidationError` so every existing
    consumer applies unchanged:

    * the ``except LLMResponseValidationError`` handler in
      ``_run_with_classification`` (``daemon/llm_error_classifier.py``)
      catches it → re-raises → tenacity retries it;
    * ``TRANSIENT_EXCEPTIONS`` membership is inherited (the retry
      predicate consumes the transient budget, and the HA facade swaps
      to the backup endpoint at ``PRIMARY_TRANSIENT_MAX``);
    * the ``agent_node`` catch tuple (``daemon/graph.py``) treats it as
      a validation failure → loud terminal ERROR.

    Raised ONLY for the incident class: an empty response that would
    become the turn's entire visible output. Legitimate empties
    (done-speaking, post-tool first empty, reasoning-only, think-tag-only,
    tool-only turns) never raise — see the L1-L13 exemption table in
    ``.agents/shared/planning/empty-response-guard/architecture-recommendation.md``
    §6 (the contract for this module).
    """

    def __init__(self, message: str, response: AIMessage | None = None):
        super().__init__(message, response=response)


# ---------------------------------------------------------------------------
# Shared emptiness predicate (empty-response-guard Phase 1, item 2)
# ---------------------------------------------------------------------------

# Single home of the empty-response nudge text. ``daemon.graph`` re-exports
# this name (``from .response_validation import NUDGE_MESSAGE``) so the
# historical ``from daemon.graph import NUDGE_MESSAGE`` import path keeps
# working; the move lets the turn-aware guard below recognize nudge
# HumanMessages without importing ``daemon.graph`` (which would be a
# circular import — graph → llm_error_classifier → response_validation).
NUDGE_MESSAGE = "Continue with your task, or provide your final response if you are finished."

# Marker stamped on nudge HumanMessages by ``nudge_node`` (daemon/graph.py)
# IN ADDITION to the pre-existing ``injected_message=True`` server-authored
# stamp. ``injected_message`` is shared by context blocks, language-check
# reminders and report injections, so it alone cannot identify a nudge —
# the dedicated marker (or the exact nudge text, for checkpoints written
# before this marker existed) is what the once-per-window nudge allowance
# keys on.
NUDGE_MARKER_KWARG = "empty_response_nudge"


def is_empty_llm_content(content: Any) -> bool:
    """Multimodal-safe emptiness predicate for LLM message content.

    The ONE emptiness definition shared by the S1 guard (this module)
    and the router's ``_is_empty_content`` (``daemon/graph.py`` — a thin
    delegate; the router function keeps its name for history).

    Truth table (empty-response-guard doc §11(b)):

    * ``None`` → empty. Non-streaming providers may yield ``None``.
    * ``str`` → empty iff whitespace-only. ``""`` → empty (streaming
      aggregation yields ``""`` for all-empty chunk streams — pinned on
      installed langchain-openai 1.1.10 ``_convert_delta_to_message_chunk``
      which coerces ``None → ""`` before concatenation).
    * ``list`` (multimodal blocks) → empty iff NO non-text blocks
      (image/audio/file/...) are present AND every text block is
      whitespace-only. An all-empty-text vision response is therefore
      EMPTY (a live defect today: the router's legacy predicate returned
      False for ANY list). ``[]`` is vacuously empty. Any unrecognized
      block shape fails OPEN as non-empty: non-dict entry (``[None]``),
      dict without a ``type`` key, unknown ``type`` value, and a
      ``text``-typed block whose ``text`` payload is NOT a string
      (``[{"type": "text", "text": None}]`` — a malformed text block
      must never be read as an empty one).
    * any other shape (``int``, ``{}``, ...) → fail-open non-empty.
    * ``<think>...</think>``-only strings are deliberately NON-empty:
      the tag characters are non-whitespace. That degenerate class is
      owned by the router's think-only re-invoke branch + the S5
      degenerate re-invoke cap (daemon/graph.py), not by this predicate.

    Returns:
        True if the content carries no user-visible payload.
    """
    if content is None:
        return True
    if isinstance(content, str):
        return content.strip() == ""
    if isinstance(content, list):
        if not content:
            return True
        has_non_text_block = False
        for block in content:
            if not isinstance(block, dict):
                return False  # fail-open: unrecognized block shape
            block_type = block.get("type")
            if block_type is None:
                return False  # fail-open: untyped block
            if block_type == "text":
                block_text = block.get("text")
                if not isinstance(block_text, str):
                    # S1 follow-up (2026-09-12 review): a MALFORMED text
                    # block (text=None / non-string) fails OPEN non-empty
                    # — str(None or "") classified it toward EMPTY, a
                    # C1-class false-positive edge.
                    return False
                if block_text.strip():
                    return False  # a non-whitespace text block → non-empty
            else:
                has_non_text_block = True
        return not has_non_text_block
    return False


def _assistant_spoke(message: BaseMessage) -> bool:
    """Turn-window spoke test: did this assistant message carry output?

    ``coding``-variant spoke rule (doc §8.1): an AIMessage counts as
    having spoken when it carries tool_calls, reasoning_content, or
    non-empty content under the shared predicate. Think-tag-only content
    counts as spoken (truthy non-whitespace) — consistent with the S1
    exemption that routes that class to the router's cap instead.
    """
    if getattr(message, "tool_calls", None):
        return True
    kwargs = getattr(message, "additional_kwargs", None) or {}
    if kwargs.get("reasoning_content"):
        return True
    return not is_empty_llm_content(getattr(message, "content", None))


def _is_nudge_human(message: BaseMessage) -> bool:
    """Identify the empty-response nudge HumanMessage in history.

    Matches the dedicated ``empty_response_nudge`` marker stamped by
    ``nudge_node`` (graph.py) first; falls back to the exact
    :data:`NUDGE_MESSAGE` text CONJUNCT with ``injected_message=True``
    so checkpoints written BEFORE the marker existed are still
    recognized. True boundary: the nudge's ``injected_message`` stamp
    exists only since e321bdb3 (2026-09-07); the kwargs-less era spans
    7d2cd0d2 (2026-04-08) → e321bdb3 (2026-09-07). The behavioral
    consequence is bounded — done-speaking windows in that era hit
    silent END, and only an empty-FIRST-response on a pre-09-07
    revived checkpoint could spuriously transient-raise. A user
    literally typing the nudge sentence (bare kwargs) can never be
    nudge-classified (W3 hardening, 2026-09-12 review).
    """
    if getattr(message, "type", None) != "human":
        return False
    kwargs = getattr(message, "additional_kwargs", None) or {}
    if kwargs.get(NUDGE_MARKER_KWARG) is True:
        return True
    content = getattr(message, "content", None)
    return (
        isinstance(content, str)
        and content == NUDGE_MESSAGE
        and bool(kwargs.get("injected_message"))
    )


def _is_server_injected_human(message: BaseMessage) -> bool:
    """Identify server-authored HumanMessages that are NOT user boundaries.

    Context blocks ([SYSTEM CONTEXT: ...], ``context_kind`` stamped),
    language-check reminders and report injections all carry
    ``injected_message=True``. The attestation-gate deny nudge carries
    ``attestation_nudge=True`` (and — post empty-response-guard C1
    follow-up, graph.py — also ``injected_message=True``; the bare
    kwarg check stays for checkpoints written before that stamp,
    retroactive heal). None of them is a real user turn, so the
    turn-window scan skips them when looking for the last real human
    boundary (L6: nudge/context humans must never be mistaken for user
    boundaries). Nudges are detected separately (they are load-bearing
    for the §8.1 allowance) — the caller checks
    :func:`_is_nudge_human` BEFORE this predicate.
    """
    kwargs = getattr(message, "additional_kwargs", None) or {}
    return (
        bool(kwargs.get("injected_message"))
        or bool(kwargs.get("context_kind"))
        or bool(kwargs.get("attestation_nudge"))
    )


def _scan_turn_window(
    input_messages: list,
) -> tuple[str | None, bool]:
    """Scan the in-scope input messages backwards for the turn shape.

    Walks from the end of ``input_messages`` (the response under
    validation is NOT in this list — it is the invoke RESULT) and
    returns:

    ``(nearest_marker, prior_spoke)`` where ``nearest_marker`` is one of

    * ``"nudge"``  — the nearest boundary-class message is the empty-
      response nudge HumanMessage → this empty is the SECOND empty of
      the current tool boundary (the §8.1 once-per-window allowance is
      consumed) → the guard raises;
    * ``"tool"``   — the nearest boundary-class message is a
      ToolMessage → this is the FIRST empty after a tool result; the
      router's row-5 nudge owns it → the guard passes;
    * ``"human"``  — the nearest boundary-class message is a REAL user
      HumanMessage (or no marker was found at all) → raise iff no
      assistant message in the window spoke;

    and ``prior_spoke`` is True when ANY assistant message since the
    real human boundary produced output (tool_calls / reasoning_content /
    non-empty content) — the done-speaking suppression that keeps
    trailing empties legitimate (L1, L9).

    Server-authored humans (context blocks, reminders, report
    injections) are skipped — they are not user boundaries (L6).
    """
    nearest_marker: str | None = None
    prior_spoke = False
    for message in reversed(input_messages):
        message_type = getattr(message, "type", None)
        if message_type == "ai":
            if _assistant_spoke(message):
                prior_spoke = True
            continue
        if message_type == "tool":
            nearest_marker = "tool"
            break
        if message_type == "human":
            if _is_nudge_human(message):
                nearest_marker = "nudge"
                break
            if _is_server_injected_human(message):
                continue  # not a user boundary (L6)
            nearest_marker = "human"
            break
        # SystemMessage / RemoveMessage / anything else — not part of the
        # turn-window shape; keep scanning.
    return nearest_marker, prior_spoke


# ---------------------------------------------------------------------------
# Empty-response guard kill-switch + compaction-skip scope
# ---------------------------------------------------------------------------

# Installed by ``load_config`` (daemon/config.py) from the resolved
# ``ENSEMBLE_EMPTY_RESPONSE_GUARD`` env value. Defaults mirror the
# documented defaults so a config-less boot (unit tests) behaves as the
# documented default ON. Kill-switch OFF restores the pre-guard
# pass-through byte-identically on BOTH halves — the S1 validator
# (``validate_llm_response``) AND the router half (the S5 degenerate
# re-invoke caps + ``_is_empty_content`` legacy semantics in
# ``daemon/graph.py``, which read the SAME installed flag via
# :func:`get_empty_response_guard_enabled` — no second env read).
# Both halves pinned by tests.
_EMPTY_RESPONSE_GUARD_ENABLED: bool = True

# Installed by ``load_config`` from ``ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP``.
# Default OFF = the guard stays ACTIVE on compaction summarizer calls
# (leader decision): an empty summary retries then lands in the
# existing truncation fallback (doc L11). ON = compaction opts out of
# the S1 raise entirely via :func:`empty_guard_disabled`.
_EMPTY_GUARD_COMPACTION_SKIP: bool = False

# Scope-local opt-out (compaction summarizer calls). ContextVar (not a
# module global) so concurrent invokes on different threads never see
# each other's scope; ``asyncio.to_thread`` copies the current context,
# and the compaction call site enters the scope ON the worker thread
# anyway, so propagation is airtight either way.
_EMPTY_GUARD_SCOPE_DISABLED: ContextVar[bool] = ContextVar(
    "empty_guard_scope_disabled", default=False
)


@contextmanager
def empty_guard_disabled() -> Iterator[None]:
    """Disable the S1 empty-response raise for the duration of the block.

    Used by the compaction summarizer when
    ``ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP=1`` so an empty summary keeps
    the pre-guard behavior (single pass-through → truncation fallback)
    instead of burning the retry/failover budget. Never affects other
    threads or later calls (ContextVar-scoped).
    """
    token = _EMPTY_GUARD_SCOPE_DISABLED.set(True)
    try:
        yield
    finally:
        _EMPTY_GUARD_SCOPE_DISABLED.reset(token)


def get_empty_response_guard_enabled() -> bool:
    """Return the installed master kill-switch state (default ON)."""
    return _EMPTY_RESPONSE_GUARD_ENABLED


def get_empty_guard_compaction_skip() -> bool:
    """Return the installed compaction-skip knob state (default OFF)."""
    return _EMPTY_GUARD_COMPACTION_SKIP


def install_empty_guard_config(*, enabled: bool, compaction_skip: bool) -> None:
    """Install the resolved guard knobs (boot path — called by load_config).

    Mirrors the ``_install_vscode_webview_csp_fix`` pattern: the RESOLVED
    values are installed once at config-resolution time so the runtime
    gate and the boot log can never disagree. Restart-required to pick up
    an env flip (the install happens at boot only).
    """
    global _EMPTY_RESPONSE_GUARD_ENABLED, _EMPTY_GUARD_COMPACTION_SKIP
    _EMPTY_RESPONSE_GUARD_ENABLED = bool(enabled)
    _EMPTY_GUARD_COMPACTION_SKIP = bool(compaction_skip)


def _reset_empty_guard_config_for_tests() -> None:
    """Restore the documented defaults (unit-test isolation only)."""
    install_empty_guard_config(enabled=True, compaction_skip=False)


def _empty_guard_active() -> bool:
    """Master switch AND scope — the single gate every raise passes through."""
    return _EMPTY_RESPONSE_GUARD_ENABLED and not _EMPTY_GUARD_SCOPE_DISABLED.get()


def _empty_response_guard_should_raise(
    response: AIMessage,
    input_messages: list | None,
) -> bool:
    """Decide whether an empty response must raise (S1, doc §5 Option 4).

    Gate order (each step can only NARROW the raise set):

    1. Kill-switch / scope — OFF restores the legacy pass-through.
    2. Response-shape exemptions: ``tool_calls`` present (L5 tool-only
       turns) or ``reasoning_content`` present (L2 — the router's
       reasoning-only re-invoke owns that class; raising here would burn
       retry budget on DESIGNED reasoning sequences, the confirmed
       bare-S1 regression).
    3. Shared predicate — non-empty content (incl. think-tag-only, L3,
       and ghost-promise ``:``, L4) never raises.
    4. Turn-aware window scan over the in-scope input messages:
       nudge-nearest → raise (§8.1 second-empty); tool-nearest → pass
       (first empty, router nudges); otherwise raise iff no prior
       assistant spoke since the real human boundary (empty-as-entire-
       answer). ``input_messages=None``/non-list (direct legacy callers,
       unit tests) → fail-open pass-through, preserving the historical
       bare-signature contract.
    """
    if not _empty_guard_active():
        return False
    if getattr(response, "tool_calls", None):
        return False
    kwargs = getattr(response, "additional_kwargs", None) or {}
    if kwargs.get("reasoning_content"):
        return False
    if not is_empty_llm_content(getattr(response, "content", None)):
        return False
    if not isinstance(input_messages, list):
        return False
    nearest_marker, prior_spoke = _scan_turn_window(input_messages)
    if nearest_marker == "nudge":
        # §8.1 synthesis: the second empty of a tool boundary raises —
        # ``empty → nudge → empty → END`` becomes
        # ``empty → nudge → empty → RAISE (retry ladder)``.
        return True
    if nearest_marker == "tool":
        # First empty after a tool result: the router's row-5 nudge is
        # the cheap rung 1 — pass through so it can fire unchanged (L1).
        return False
    # Real-human boundary (or no marker at all): empty-as-entire-answer
    # unless an assistant message already spoke this turn (done-speaking
    # / degenerate tail — rows 2-4 + S5 caps own those).
    return not prior_spoke


def validate_llm_response(
    response: AIMessage,
    input_messages: list | None = None,
) -> None:
    """Validate the structural integrity of an LLM response.

    Performs structural validation checks on an AIMessage response to detect
    common failure modes. Raises LLMResponseValidationError for invalid responses.

    Validation checks (in order):
    1. Truncated response: finish_reason is "length"
    2. Missing tool call data: tool_calls with empty function.name or function.arguments
    3. Empty-as-entire-answer (empty-response-guard Phase 1): a
       shared-predicate-empty response with no tool_calls and no
       reasoning_content, whose turn window shows no prior assistant
       output — raised as the typed subclass
       :class:`EmptyLLMResponseError`. The turn-aware gate + the L1-L13
       exemption table (planning doc §6) keep every legitimate empty
       flowing exactly as before: done-speaking empties, first-empty-
       after-tool (the router nudge owns it), reasoning-only /
       think-tag-only (router rows 2-3 + S5 caps own them), tool-only
       turns, and multimodal image-bearing content. Kill-switch:
       ``ENSEMBLE_EMPTY_RESPONSE_GUARD`` (default ON) restores the
       legacy pass-through when disabled.

    Note (updated by empty-response-guard): the blanket "empty content is
    intentionally NOT validated" rule this guard replaces was the one
    hole in the protection lattice — a provider returning continuous
    empty AI messages completed turns as silent empty "successes". Empty content
    is still valid when the assistant already spoke this turn, or after
    a tool result (nudge rung); it is a DEFECT when it is the turn's
    entire visible output, and Check 3 raises for exactly that class.
    The raise lands inside the retry scope (llm_error_classifier
    ``_run_with_classification``), so it inherits retry → failover →
    loud terminal ERROR, and — because every facade-wrapped surface
    (compaction, title-gen, keyword extraction, child-report
    summarization, attestation judge) calls THIS validator — one edit
    covers them all.

    Fail-open: If response structure is unexpected or validation cannot determine
    validity (e.g., missing response_metadata field), logs a warning but does NOT raise.
    We'd rather use a questionable response than crash.

    Args:
        response: A LangChain AIMessage object to validate.
        input_messages: The message list the LLM was invoked with (the
            response is NOT part of it). Required for the turn-aware
            empty check (Check 3); when omitted (legacy bare-signature
            callers / unit tests) Check 3 fails open — the historical
            behavior for the bare signature is unchanged.

    Raises:
        LLMResponseValidationError: If the response fails any structural validation check.
        EmptyLLMResponseError: If Check 3 fires (subclass of
            LLMResponseValidationError — every existing handler applies).
    """
    # Check 1: Truncated response (finish_reason == "length")
    if _is_truncated_response(response):
        raise LLMResponseValidationError(
            "Response was truncated (finish_reason=length)",
            response=response,
        )

    # Check 2: Malformed tool calls (empty function.name or function.arguments)
    if _has_malformed_tool_calls(response):
        raise LLMResponseValidationError(
            "Response has tool calls with empty function name or arguments",
            response=response,
        )

    # Check 3: Empty-as-entire-answer (empty-response-guard S1).
    if _empty_response_guard_should_raise(response, input_messages):
        raise EmptyLLMResponseError(
            "LLM returned an empty response with no tool calls and no "
            "reasoning content — empty-as-entire-answer is treated as a "
            "provider failure (retries + failover apply; legitimate "
            "empties are exempt, see the L1-L13 table in the "
            "empty-response-guard plan)",
            response=response,
        )


def _is_truncated_response(response: AIMessage) -> bool:
    """Check if response was truncated due to length limits.

    Checks response.response_metadata.get("finish_reason") == "length".

    Returns:
        True if response was truncated.
        False if not truncated or if metadata is missing (fail-open).
    """
    try:
        metadata = getattr(response, "response_metadata", None)
        if metadata is None:
            logger.warning(
                "Response missing response_metadata. Cannot check truncation. "
                "Passing validation."
            )
            return False

        finish_reason = metadata.get("finish_reason")
        if finish_reason is None:
            logger.warning(
                "Response metadata missing finish_reason. Cannot check truncation. "
                "Passing validation."
            )
            return False

        return finish_reason == "length"
    except Exception as e:
        logger.warning(
            f"Error checking truncation metadata: {e}. "
            "Passing validation."
        )
        return False


def _has_malformed_tool_calls(response: AIMessage) -> bool:
    """Check if response has tool calls with empty function.name or function.arguments.

    Tool calls can be in two formats:
    - ToolCall objects with .name and .args attributes
    - Dict format with "name" and "args" keys

    Returns:
        True if any tool call has empty name or arguments.
        False if all tool calls are well-formed or if no tool calls present.
    """
    tool_calls = getattr(response, "tool_calls", None)

    if not tool_calls:
        return False

    for tool_call in tool_calls:
        name = _get_tool_call_name(tool_call)
        args = _get_tool_call_args(tool_call)

        if name is None or (isinstance(name, str) and name.strip() == ""):
            logger.warning(
                f"Tool call has empty function name: {tool_call}. "
                "Failing validation."
            )
            return True

        if args is None or (isinstance(args, str) and args.strip() == ""):
            logger.warning(
                f"Tool call has empty function arguments: {tool_call}. "
                "Failing validation."
            )
            return True

    return False


def _get_tool_call_name(tool_call: Any) -> str | None:
    """Extract function name from a tool call.

    Supports both ToolCall object format (tool_call.name) and
    dict format (tool_call["name"]).

    Returns:
        Function name string or None if not found.
    """
    if isinstance(tool_call, dict):
        return tool_call.get("name")
    elif hasattr(tool_call, "name"):
        return tool_call.name
    return None


def _get_tool_call_args(tool_call: Any) -> Any | None:
    """Extract function arguments from a tool call.

    Supports both ToolCall object format (tool_call.args) and
    dict format (tool_call["args"]).

    Returns:
        Arguments dict/string or None if not found.
    """
    if isinstance(tool_call, dict):
        return tool_call.get("args")
    elif hasattr(tool_call, "args"):
        return tool_call.args
    return None
