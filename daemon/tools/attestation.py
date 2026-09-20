"""Leader completion attestation tool (``attest_completion``).

Marks that a delegated mission is genuinely complete — all
dispatched children have reported and the work is done. The
attestation is recorded by virtue of the tool call existing in
the leader's message stream, but the tool's RETURN VALUE is the
TEACHER for the next AI message: it tells the leader exactly
what shape the next message must take (standalone text report,
no tool calls).

Attest-first pure-toolcall-turn contract (2026-09-19, user
decision; flips the previous report-first-then-attest teaching;
closes incident c5d9a38a where a leader bundled report+attest
into ONE AIMessage — prompt-only fixes failed twice, so the
contract is now enforced system-side via the HOLD-state gate in
``daemon/services/attestation_gate.py``):

1. ``attest_completion`` MUST be called in a PURE TOOLCALL
   TURN: the AIMessage that carries the call must have EMPTY
   content — no report text, no ack, nothing.

2. The FULL detailed final report MUST follow as a SUBSEQUENT
   standalone AI message (no tool calls). It becomes the
   transcript's LAST AI message; the in-graph completion gate
   (``daemon/graph.py:create_attestation_gate_node``) allows END
   ONLY on that final message.

3. If the AIMessage that called the tool carried non-empty
   content (the c5d9a38a shape — report + attest bundled into
   ONE message), the tool returns a different teacher text that
   REJECTS the bundle and asks the leader to re-issue the
   report as its own standalone message.

Failure evidence: instance c5d9a38a bundled report+toolcall in
ONE AIMessage twice; prompt-only fixes failed — hence
system-side enforcement now. The HOLD-state gate injects a
reminder HumanMessage and routes back to ``agent`` whenever
attestation is present but the final AIMessage is NOT a
standalone text report (>= 150 words, no tool calls). Reminder
injection is COUNTER-INDEPENDENT: it does NOT increment
``attestation_denied_count`` (no bound/escalation interaction)
and is capped at :data:`ATTESTATION_REMINDER_CAP` per mission.
On the cap, the gate falls through to plain ``meta_bypass``
allow — the documented escape so a leader that keeps emitting
empty/short finals is never stuck in an infinite HOLD loop.

CONDITIONAL USE — call ONLY when ALL hold:

* This mission dispatched children via ``send_message``.
* The system is nudging the attestation (e.g. a completion-check
  nudge has reached you).
* You are about to end your turn.

WHEN NOT TO CALL: plain answers, chart requests, quick
follow-ups, or any mission that did not dispatch children. You
also do not need to call it when the gate or judge has already
released you without nudging — the nudge is the trigger.

The attestation tool-call message must NOT carry the report —
empty content only. Per-agent teaching source is the deny-time
nudge; this docstring header is the single canonical tool-side
reference. Scope: leader-only via ``agents/leader/meta.json``
``tools.allow``; NOT privileged per the project's closed-by-leader
D7 ruling.
"""
from __future__ import annotations

import inspect
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Attestation"
CATEGORY_DOC = """\
Leader completion attestation — a deterministic no-op signal that a
delegated mission is genuinely complete (all dispatched children have
reported, work done). The attestation is recorded by virtue of the
tool call existing in the leader's message stream; the in-graph
completion gate scans that stream to allow or deny the END transition.

ATTEST-FIRST PURE-TOOLCALL-TURN CONTRACT (2026-09-19, user
decision; closes incident c5d9a38a):

1. ``attest_completion`` MUST be called in a PURE TOOLCALL TURN:
   the AIMessage that carries the call must have EMPTY content
   — no report text, no ack, nothing.
2. The FULL detailed final report MUST follow as a SUBSEQUENT
   standalone AI message (no tool calls). It becomes the
   transcript's LAST AI message; the in-graph completion gate
   allows END ONLY on that final message.
3. If the AIMessage that called the tool carried non-empty
   content (report + attest bundled into ONE message), the tool
   returns a different teacher text that REJECTS the bundle and
   asks the leader to re-issue the report as its own standalone
   message. The system-side HOLD-state gate (``daemon/services/
   attestation_gate.py``) detects the bundle shape and injects a
   reminder to enforce this contract.

CONDITIONAL — call ONLY when ALL hold:

* This mission dispatched children via ``send_message``.
* The system is nudging you (a completion-check nudge arrived).
* You are about to end your turn.

DO NOT call for plain answers, chart requests, quick follow-ups,
or any mission that did not dispatch children — and do not call
when the gate or judge has already released you without nudging.
The attestation tool-call message must NOT carry the report. It
must be EMPTY content — at most a one-line ack such as "Report
delivered above; attesting completion." Idempotent: any number
of calls in the same turn counts as one.

Scope: leader-only via ``agents/leader/meta.json`` ``tools.allow``;
NOT privileged per the project's closed-by-leader D7 ruling. Per-
agent teaching source is the deny-time nudge; this category doc
is the single canonical tool-side reference (alongside the tool's
own docstring + the module header).
"""


# ─────────────────────────────────────────────────────────────────────────────
# Teacher result texts (canonical home; the single source of truth for the
# tool's RETURN-VALUE-as-teacher shape — the LLM reads this verbatim via the
# ToolMessage and acts on it for the NEXT message)
# ─────────────────────────────────────────────────────────────────────────────

#: Teacher result text returned when the AIMessage that called
#: ``attest_completion`` was a PURE toolcall turn (empty content).
#: The next AI message MUST be a standalone text report (>= 150
#: words, no tool calls) for the in-graph completion gate to
#: allow END.
ATTEST_CLEAN_RESULT_TEXT = (
    "Attestation recorded. Now deliver your full detailed final "
    "report as your final message - a standalone message with no "
    "tool calls. (Your attestation call must contain no text.)"
)

#: Teacher result text returned when the AIMessage that called
#: ``attest_completion`` carried non-empty content (the c5d9a38a
#: shape — report + attest bundled into ONE message). The leader
#: MUST re-issue the report as its own standalone message before
#: the gate can allow END.
ATTEST_BUNDLED_RESULT_TEXT = (
    "Attestation recorded, but your tool-call message contained "
    "text - the attestation call must be text-free. Re-issue "
    "your full detailed final report now as its own standalone "
    "message."
)


# ─────────────────────────────────────────────────────────────────────────────
# Caller-AIMessage detection (the "did the calling AIMessage carry text"
# question the tool needs to answer in order to pick the teacher text)
# ─────────────────────────────────────────────────────────────────────────────

#: Per-context state used by the agent runtime to communicate the
#: calling AIMessage content to the tool body. Set right BEFORE
#: the tool invocation by the tools-node caller; read at the top
#: of the tool body. Default ``""`` (empty content) maps to the
#: clean-call teacher text — the safe default if the runtime
#: forgets to set it.
#:
#: Uses :class:`contextvars.ContextVar` (NOT ``threading.local``)
#: because the tool body runs in a worker thread via
#: ``asyncio.to_thread`` (the LangChain tool invocation
#: framework dispatches the body off the event loop) —
#: ``threading.local`` would NOT transfer state across the
#: thread boundary, and the runtime hook's per-thread state
#: would be invisible to the tool body. ``ContextVar``
#: transfers across ``asyncio.to_thread`` via Python's
#: ``contextvars.copy_context`` (CPython 3.9+) — the runtime
#: hook's set propagates to the worker thread where the tool
#: body executes.
_attest_caller_state: ContextVar[str] = ContextVar(
    "attest_caller_state", default=""
)


def _get_attest_caller_content() -> str:
    """Return the calling AIMessage content if set by the agent runtime.

    Returns empty string when the runtime has not set the state
    (degenerate embeddings, test-only call sites). Empty content
    maps to the clean-call teacher text — the safe default.
    """
    return _attest_caller_state.get()


def set_attest_caller_content(ai_message: AIMessage | None) -> None:
    """Record the AIMessage that triggered the next ``attest_completion`` call.

    Called by the agent runtime (the tools-node caller in
    ``daemon/graph.py``) right BEFORE invoking the
    ``attest_completion`` tool. The tool body reads the recorded
    content via :func:`_get_attest_caller_content` to decide
    which teacher text to return.

    Args:
        ai_message: The AIMessage that contains the
            ``attest_completion`` tool call. ``None`` ⇒ record
            empty content (the clean-call case).
    """
    if ai_message is None:
        _attest_caller_state.set("")
        return
    raw_content = getattr(ai_message, "content", "") or ""
    # LangChain text+reasoning blocks: flatten to plain text so the
    # "did the AIMessage carry text" check sees the same shape the
    # gate's marker/length scanners see. The marker scanner's
    # ``_flatten_ai_content`` is the canonical helper, but we keep
    # this minimal inline equivalent to avoid an import cycle
    # (this module is on the tool-creation hot path).
    if isinstance(raw_content, list):
        flat_parts: list[str] = []
        for block in raw_content:
            if isinstance(block, dict):
                flat_parts.append(str(block.get("text", "")))
            else:
                flat_parts.append(str(block))
        flattened = " ".join(flat_parts)
    else:
        flattened = str(raw_content)
    _attest_caller_state.set(flattened)


def reset_attest_caller_content_for_tests() -> None:
    """Reset the per-context state to the default. Test-only —
    production code never invokes this.

    Uses :meth:`ContextVar.set` with the default value
    (``""``) — there is no ``ContextVar.delete`` for the
    context-local state; ``set("")`` restores the same
    observable behavior as the unset default."""
    _attest_caller_state.set("")


#: Inspector-based fallback used ONLY when the runtime path that
#: sets :func:`set_attest_caller_content` is bypassed (legacy tool
#: calls that reach the body without going through the
#: ``attest_completion`` tools-node hook — e.g. direct unit-test
#: invocation in some existing tests). Walks the call stack and
#: looks for the AIMessage with an ``attest_completion`` tool_call
#: in the calling frames' local variables. Returns the AIMessage
#: content if found, empty string otherwise. NOT a primary code
#: path — the runtime-set path is the contract.
def _fallback_extract_attest_caller_content() -> str:
    """Walk the call stack to find the AIMessage that triggered this tool call.

    Legacy fallback — the production path is :func:`set_attest_caller_content`
    set by the tools-node caller. This helper exists so direct
    unit-test invocations (which bypass the runtime hook) still
    pick the correct teacher text. Returns empty string when no
    matching AIMessage is found (the safe default → clean-call
    text).

    NOTE: This uses :mod:`inspect`, which is fragile across
    LangChain versions. The runtime-set path is the canonical
    contract; this fallback is a defense-in-depth layer for
    test-only call sites.
    """
    try:
        stack = inspect.stack()
    except Exception:
        return ""
    try:
        # Skip self (frame 0). Walk upward looking for any local
        # variable that is (or contains) an AIMessage with an
        # attest_completion tool_call.
        for frame_info in stack[1:]:
            try:
                local_vars = frame_info.frame.f_locals
            except (ValueError, AttributeError):
                continue
            content = _try_extract_attest_ai_content_from_locals(local_vars)
            if content is not None:
                return content
    finally:
        del stack
    return ""


def _try_extract_attest_ai_content_from_locals(
    local_vars: dict[str, Any],
) -> str | None:
    """Look for an AIMessage with an attest_completion tool_call in local_vars."""
    for value in local_vars.values():
        if isinstance(value, AIMessage):
            if _has_attest_call(value):
                return _flatten_ai_content(getattr(value, "content", ""))
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, AIMessage) and _has_attest_call(item):
                    return _flatten_ai_content(getattr(item, "content", ""))
        if isinstance(value, dict):
            for v in value.values():
                if isinstance(v, AIMessage) and _has_attest_call(v):
                    return _flatten_ai_content(getattr(v, "content", ""))
                if isinstance(v, (list, tuple)):
                    for item in v:
                        if isinstance(item, AIMessage) and _has_attest_call(item):
                            return _flatten_ai_content(
                                getattr(item, "content", "")
                            )
    return None


def _has_attest_call(ai_message: AIMessage) -> bool:
    """True iff ``ai_message`` carries an ``attest_completion`` tool call."""
    for tool_call in getattr(ai_message, "tool_calls", None) or []:
        if isinstance(tool_call, dict) and tool_call.get("name") == "attest_completion":
            return True
    return False


def _flatten_ai_content(content: object) -> str:
    """Flatten an AIMessage's content into a plain string.

    Mirrors :func:`daemon.services.attestation_marker_scanner._flatten_ai_content`
    (kept inline here to avoid a hot-path import cycle). List-of-
    blocks content (LangChain text + reasoning blocks) is flattened
    to plain text; everything else is coerced via ``str(...)``.
    """
    if isinstance(content, list):
        flat_parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                flat_parts.append(str(block.get("text", "")))
            else:
                flat_parts.append(str(block))
        return " ".join(flat_parts)
    return str(content) if content else ""


def _resolve_attest_caller_content() -> str:
    """Return the calling AIMessage's flattened content.

    The runtime-set per-thread state (:func:`set_attest_caller_content`)
    is the canonical contract. The stack-inspector fallback
    (:func:`_fallback_extract_attest_caller_content`) handles
    direct unit-test invocations that bypass the runtime hook.
    Either path returns empty string when nothing is found, which
    maps to the clean-call teacher text — the safe default.
    """
    runtime_content = _get_attest_caller_content()
    if runtime_content:
        return runtime_content
    return _fallback_extract_attest_caller_content()


@register_tool_category("attestation")
@tool
def attest_completion() -> str:
    """Call this tool ALONE in one turn - the message containing this call must contain nothing else (no report, no commentary). Then deliver your full detailed final report as your final standalone message.

CONDITIONAL — call ONLY when ALL hold:
      • This mission dispatched children via ``send_message``.
      • The system is nudging you (a completion-check nudge arrived).
      • You are about to end your turn.

DO NOT call for plain answers, charts, quick follow-ups, or any
mission that did not dispatch children — and do not call when
the gate or judge has already released you without nudging.

The attestation tool-call message must NOT carry the report —
empty content only. Idempotent: any number of calls in the
same turn counts as one.

Returns:
        The teacher text for the NEXT AI message: the clean-call
        shape when the calling AIMessage had empty content, the
        re-issue shape when it carried text. The leader reads
        this verbatim via the ToolMessage and acts on it.
    """
    caller_content = _resolve_attest_caller_content()
    if caller_content.strip():
        # Bundled case — the AIMessage that called this tool
        # carried non-empty content (the c5d9a38a shape). The
        # teacher text tells the leader to re-issue the report
        # as its own standalone message. The in-graph gate's
        # HOLD-state will inject a reminder to enforce.
        return ATTEST_BUNDLED_RESULT_TEXT
    # Clean case — the AIMessage was a pure toolcall turn. The
    # teacher text tells the leader to deliver the report as the
    # next AI message (no tool calls, standalone).
    return ATTEST_CLEAN_RESULT_TEXT


def create_attestation_tools(
    manager: Any | None = None,
    current_instance_id: str = "",
    agent_id: str = "",
) -> list:
    """Factory: return the ``attest_completion`` tool list for an instance.

    The tool is module-level registered via
    ``@register_tool_category("attestation")`` + ``@tool`` (so the
    decorator discipline pins the category registration to source
    — see ``tests/unit/tools/test_attestation_registration.py``).
    The factory exists to satisfy ``create_instance_tools``'s
    contract (every category module exposes a
    ``create_<category>_tools`` factory even when no per-instance
    closure is needed) and to surface the tool in the per-instance
    tool list returned to the agent runtime.

    Args:
        manager: Optional per-instance manager handle. The attestation
            tool is a static no-op that returns the teacher text from
            the runtime-set per-thread state — the manager is NOT used
            in the tool body, but the factory signature mirrors the
            other category factories for symmetry.
        current_instance_id: Optional instance id for tooling symmetry.
        agent_id: Optional agent id for tooling symmetry.

    Returns:
        A single-element list containing the ``attest_completion``
        tool (the category exposes exactly one tool — the leader
        completion attestation signal). The factory returns the
        decorator-bound tool verbatim; per-instance scoping is
        handled by ``create_instance_tools`` via ``tools.allow``.
    """
    # Tool is module-level registered (the decorator-bound function
    # is the live tool handle). The factory just exposes it through
    # the per-instance tool list — no closure capture needed because
    # the attestation tool reads its caller-AIMessage context from
    # the per-thread runtime state set by the tools-node caller in
    # ``daemon/services/long_tool_nudge.py:wrapped_tools_node``.
    return [attest_completion]


attest_completion._full_doc_ = """\
Call this tool ALONE in one turn - the message containing this call must contain nothing else (no report, no commentary). Then deliver your full detailed final report as your final standalone message.

CONDITIONAL — call ONLY when ALL hold:
  • This mission dispatched children via ``send_message``.
  • The system is nudging you (a completion-check nudge arrived).
  • You are about to end your turn.

DO NOT call for plain answers, charts, quick follow-ups, or any
mission that did not dispatch children — and do not call when
the gate or judge has already released you without nudging.

The attestation tool-call message must NOT carry the report —
empty content only. Idempotent: any number of calls in the
same turn counts as one.

Mechanics: deterministic no-op body; the tool returns the
teacher text (clean vs bundled) based on whether the calling
AIMessage had empty content. The in-graph completion gate
(``daemon/services/attestation_gate.py`` + ``daemon/graph.py``)
enforces the contract: attestation_present + non-text-report
final AIMessage ⇒ HOLD-state with reminder injection (counter-
INDEPENDENT — does NOT increment ``attestation_denied_count``;
capped at ``ATTESTATION_REMINDER_CAP`` per mission, then
plain ``meta_bypass`` allow fallback). Completion (meta_bypass
allow) fires ONLY when attestation_present AND the final
AIMessage is a standalone text report (no tool calls, >=
``SHORT_REPORT_WORD_THRESHOLD`` words).

Scope: leader-only via ``agents/leader/meta.json`` ``tools.allow``;
NOT privileged per the project's closed-by-leader D7 ruling. Per-
agent teaching source is the deny-time nudge; this docstring is
the single canonical tool-side reference (alongside the tool's own
docstring + the module header).

Returns:
    The teacher text for the NEXT AI message: the clean-call
    shape (``ATTEST_CLEAN_RESULT_TEXT``) when the calling
    AIMessage had empty content, the re-issue shape
    (``ATTEST_BUNDLED_RESULT_TEXT``) when it carried text.
"""
