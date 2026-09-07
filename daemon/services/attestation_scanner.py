"""Attestation scanner — pure function over the leader's message stream.

Phase 2 of the leader completion attestation feature (task 2.1). The
scanner decides whether the leader has called ``attest_completion``
within the attestation window (the last ``N`` AIMessages of the
current in-node state, D4 default ``N=3``).

Contract (per ``phase2-plan.md`` task 2.1 and ``requirements.md`` FR-2):

* **Window semantics (D10(a))** — the scan walks the last ``window``
  ``AIMessage``s of the list. ANY attestation tool call inside the
  window counts (the natural ``attest → ToolMessage → final prose``
  flow places the attesting AIMessage 2–3 positions back, so a
  last-AIMessage-only scan would false-deny every legitimate
  completion — the guaranteed 3-strikes-escalation machine rejected
  by the architect).
* **Bounded scan (AC-2.5 / AC-3.4)** — the function MUST NOT inspect
  more than ``window`` AIMessages when computing ``attested``. The
  backward walk stops as soon as the window is full; a 1000-message
  state scanned at ``N=3`` touches exactly 3 AIMessages.
* **Tool-call-only claims (AC-2.3)** — text-only mentions of the tool
  name never count. Only ``AIMessage.tool_calls[i].name`` matches.
* **Non-AIMessage exclusion (D10(c))** — injected reports and
  ``language_check`` reminders are ``HumanMessage``s and compaction
  summaries are ``SystemMessage``s; an AIMessage-only scan is immune
  to them by construction.
* **Summary-doc awareness (D10(b))** — when a compaction summary doc
  (``compaction-global-{iid}-{seq}`` SystemMessage) is encountered
  during the walk, the diagnostics record it (``summary_seen``) so
  dry-log adjudication can distinguish "window truncated by pressure"
  from "attestation compacted away".

The module is deliberately dependency-light (langchain message types
only) so it stays unit-testable in isolation and importable from
``daemon.services.attestation_gate`` without pulling the graph.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator, NamedTuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

logger = logging.getLogger(__name__)

#: Default tool name the scanner matches. The leader's prompt contract
#: (``agents/leader/rule.md``) and the Phase 1 tool registration
#: (``daemon/tools/attestation.py``) both pin this name.
DEFAULT_ATTESTATION_TOOL_NAME = "attest_completion"

#: ID prefix of the single compaction summary doc written by
#: ``daemon/compaction.py`` (``GLOBAL_DOC_ID_PREFIX``). Kept as a local
#: literal (not imported) so this module stays dependency-light; the
#: value is pinned by ``tests/unit/test_attestation_scanner.py``.
_COMPACTION_GLOBAL_DOC_ID_PREFIX = "compaction-global-"


class AttestationScanResult(NamedTuple):
    """Full scanner output — everything the gate's log schema needs.

    Attributes:
        attested: True when ANY AIMessage inside the window carries an
            ``attest_completion`` tool call (the attestation decision —
            computed from at most ``window`` AIMessages).
        diagnostics: One ``{index, tool_call_names, attestation_present}``
            dict per AIMessage inspected (ordered oldest → newest of the
            walked slice). ``index`` is the position in the ORIGINAL
            message list.
        messages_scanned: Number of AIMessages actually inspected
            (≤ window). O8 consumes this: ``> 0`` confirms the scanner
            ran on a non-empty AI tail.
        window_truncated: True when fewer than ``window`` AIMessages
            exist in the whole message list (the requested window was
            larger than the available AI tail).
        summary_seen: True when a compaction summary doc was crossed
            during the walk (D10(b) diagnostic — the scan crossed a
            compaction boundary).
    """

    attested: bool
    diagnostics: list[dict[str, Any]]
    messages_scanned: int
    window_truncated: bool
    summary_seen: bool


def is_compaction_summary_doc(message: BaseMessage) -> bool:
    """True when ``message`` is the compaction summary doc.

    Compaction writes a single ``SystemMessage`` whose id carries the
    ``compaction-global-{iid}-{seq}`` prefix (``daemon/compaction.py``,
    ``GLOBAL_DOC_ID_PREFIX``). The scanner never counts it as an
    attestation (it is not an AIMessage and carries no tool_calls);
    it is only *noted* for the ``summary_seen`` diagnostic.
    """
    message_id = getattr(message, "id", None)
    return isinstance(message_id, str) and message_id.startswith(
        _COMPACTION_GLOBAL_DOC_ID_PREFIX
    )


def _tool_call_names(message: BaseMessage) -> list[str]:
    """Extract the tool-call names from an AIMessage (tolerant shape read).

    ``tool_calls`` entries are dicts in practice (LangChain normalizes
    to ``{"name": ..., "args": ..., "id": ...}``), but the codebase's
    compaction formatter (``daemon/compaction.py``) defensively reads
    both dict and attribute shapes — the scanner mirrors that tolerance.
    """
    names: list[str] = []
    for tool_call in getattr(message, "tool_calls", None) or []:
        if isinstance(tool_call, dict):
            name = tool_call.get("name", "?")
        else:
            name = getattr(tool_call, "name", "?")
        names.append(str(name))
    return names


def _backward_scan_entries(
    messages: list[BaseMessage],
) -> Iterator[tuple[int, BaseMessage, bool]]:
    """Yield ``(index, message, is_summary)`` walking BACKWARD.

    Shared traversal primitive for both scanner walks. Covers exactly
    the entries either walk inspects or crosses: compaction summary
    docs (``is_summary=True`` — crossed, never inspected; walk 1 turns
    these into the ``summary_seen`` diagnostic) and ``AIMessages``
    (``is_summary=False`` — the inspectable entries). Everything else
    (ToolMessages, HumanMessages, injected reports) is invisible to
    both walks and skipped entirely.
    """
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if is_compaction_summary_doc(message):
            yield index, message, True
            continue
        if not isinstance(message, AIMessage):
            continue
        yield index, message, False


def scan_for_attestation_detailed(
    messages: list[BaseMessage],
    window: int,
    tool_name: str = DEFAULT_ATTESTATION_TOOL_NAME,
) -> AttestationScanResult:
    """Scan the last ``window`` AIMessages for an attestation tool call.

    This is the full-fidelity variant; :func:`scan_for_attestation` is
    the plan-verbatim thin wrapper returning ``(attested, diagnostics)``.

    The walk goes BACKWARD from the newest message and stops as soon as
    ``window`` AIMessages have been inspected (AC-2.5: a 1000-message
    state at ``N=3`` inspects exactly 3 AIMessages — the full history is
    never materialized or walked to completion). Non-AI messages crossed
    on the way (ToolMessages, HumanMessages, injected reports, the
    compaction summary doc) are skipped, not inspected — but a compaction
    summary doc raises the ``summary_seen`` diagnostic.

    Args:
        messages: The in-node message list (``state["messages"]``). The
            caller reads this from the live LangGraph state — the scanner
            performs NO checkpoint access (no ``aget_state``; the
            namespace-mismatched empty-state defect is exactly what this
            seam avoids).
        window: Number of most-recent AIMessages to inspect. Values
            ``< 1`` are treated as ``1`` (a window of 0 would make the
            gate un-evaluable; fail-open to the smallest meaningful
            window rather than dividing by zero semantics).
        tool_name: Tool name that counts as an attestation.

    Returns:
        :class:`AttestationScanResult` — see the NamedTuple docs.
    """
    if window < 1:
        window = 1

    diagnostics: list[dict[str, Any]] = []
    attested = False
    summary_seen = False
    total_aimessages = 0

    # Backward walk — O(window AIMessages) inspections, NOT O(len(messages)).
    # We stop the moment the window is full; the remainder of the list is
    # never touched (bounded-scan invariant, AC-2.5 / AC-3.4).
    for index, message, is_summary in _backward_scan_entries(messages):
        if is_summary:
            summary_seen = True
            continue

        total_aimessages += 1
        names = _tool_call_names(message)
        present = tool_name in names
        diagnostics.append(
            {
                "index": index,
                "tool_call_names": names,
                "attestation_present": present,
            }
        )
        if present:
            attested = True

        if total_aimessages >= window:
            break

    # diagnostics were appended newest-first; normalize to oldest-first
    # so log readers see the walked slice in conversation order.
    diagnostics.reverse()

    return AttestationScanResult(
        attested=attested,
        diagnostics=diagnostics,
        messages_scanned=total_aimessages,
        window_truncated=total_aimessages < window,
        summary_seen=summary_seen,
    )


def scan_for_attestation(
    messages: list[BaseMessage],
    window: int,
    tool_name: str = DEFAULT_ATTESTATION_TOOL_NAME,
) -> tuple[bool, list[dict]]:
    """Plan-verbatim scanner entry point.

    Args:
        messages: The in-node message list (``state["messages"]``).
        window: Number of most-recent AIMessages to inspect.
        tool_name: Tool name that counts as an attestation.

    Returns:
        ``(attested, diagnostic_detail)`` where ``diagnostic_detail`` is
        a list of ``{index, tool_call_names, attestation_present}`` dicts
        — one per AIMessage inspected, oldest first.
    """
    result = scan_for_attestation_detailed(messages, window, tool_name)
    return result.attested, result.diagnostics


def attestation_seen_outside_window(
    messages: list[BaseMessage],
    window: int,
    tool_name: str = DEFAULT_ATTESTATION_TOOL_NAME,
) -> bool:
    """O3 diagnostic — attestation present in history but stale (outside window).

    A ``True`` here means the leader DID attest at some point, but the
    attestation has aged out of the window (e.g. a stale pre-revive
    attestation carried across a revive boundary — the exact bug class
    the window scan exists to defeat). This is diagnostic-only output
    for the canonical gate log; it is NEVER a deny trigger and it is
    NOT part of the ``attested`` decision path (which stays bounded to
    the window per AC-2.5 / AC-3.4).
    """
    if window < 1:
        window = 1

    seen_in_window = 0
    for _index, message, is_summary in _backward_scan_entries(messages):
        if is_summary:
            continue
        seen_in_window += 1
        if seen_in_window <= window:
            # Inside the window — the attested scan already accounted
            # for these; only OLDER AIMessages are diagnostic-relevant.
            continue
        if tool_name in _tool_call_names(message):
            return True
    return False


# ════════════════════════════════════════════════════════════════════════════════
# Conditional-attestation scanner — Phase 6 fastfollow (2026-09-06)
#
# Attestation is now CONDITIONAL on delegation: the gate only demands
# ``attest_completion`` when the leader has dispatched a child since the
# last REAL user message. Quick follow-ups / chart requests / plain
# answers no longer trip the gate. The scanner split below owns BOTH the
# "what is a real user message" predicate and the "was send_message
# called since the last real user message" check.
#
# Critical design constraint (self-reference trap): the nudge itself is
# injected as a HumanMessage — if it counted as a real user message, the
# deny → nudge → deny cycle would reset the delegation window on every
# deny, and the gate would relax as soon as a deny fired. This module's
# :func:`is_real_user_message` predicate EXCLUDES every internal/injected
# HumanMessage (attestation nudge, child-report, ``[SYSTEM CONTEXT: …]``
# context block, synthetic system-message via ``is_synthetic``, every
# ``internal_agent:*`` source). The exclusion reads the additional_kwargs
# surface — the same metadata the codebase already carries.
# ════════════════════════════════════════════════════════════════════════════════


#: Tool name that constitutes leader delegation to a child. Any send_message
#: tool call after the last real user message flips the conditional
#: requirement ON — instantiations (``spawn_instance``) are inert by
#: design (the child does nothing until messaged), so spawn alone does
#: NOT count as delegation; the user spec pins this. The constant is
#: local (not imported) so the module stays dependency-light; the value
#: is pinned by ``tests/unit/test_attestation_scanner.py``.
DEFAULT_DELEGATION_TOOL_NAME = "send_message"

#: Prefix that marks ``additional_kwargs["source"]`` values for child-report
#: injections (``daemon/graph.py`` ``_frame_injected_report`` constructs
#: ``source = f"internal_report:{child_iid}"``). Kept local (defense in
#: depth — the ``injected_message=True`` flag is the canonical signal, but
#: the source prefix is the canonical NAME for an injected report).
_INTERNAL_REPORT_SOURCE_PREFIX = "internal_report:"

#: Prefix that marks ``additional_kwargs["source"]`` for any internal
#: agent-to-agent message that is NOT user-authored
#: (``daemon/services/instance_messaging.py`` ``internal_agent:*`` tokens).
_INTERNAL_AGENT_SOURCE_PREFIX = "internal_agent:"


def is_real_user_message(message: BaseMessage) -> bool:
    """True when ``message`` is a real, user-authored HumanMessage.

    The conditional-attestation gate (Phase 6, 2026-09-06) anchors its
    delegation window on the LAST REAL user message — child reports,
    attestation nudges, and ``[SYSTEM CONTEXT: …]`` injections MUST NOT
    reset the window or the gate would self-defeat on the first deny.

    Exclusion ladder (defense in depth — every predicate the codebase
    already carries is read):

    1. **Type gate** — non-HumanMessage ⇒ excluded (the LLM-bound chat
       channel is typed; ``SystemMessage`` / ``AIMessage`` / ``ToolMessage``
       / ``RemoveMessage`` cannot be the user).
    2. **Attestation nudge** — ``additional_kwargs.attestation_nudge==True``
       ⇒ excluded (the gate's own deny-path injection; this is THE
       critical self-reference trap).
    3. **Injected / synthetic flag** —
       ``additional_kwargs.injected_message==True`` OR
       ``additional_kwargs.is_synthetic==True`` ⇒ excluded (covers the
       ``_make_context_message`` factory that stamps ``[SYSTEM CONTEXT: …]``
       blocks AND the ``_frame_injected_report`` child-report injector).
       The ``is_synthetic`` arm is **read-model-only defense in depth**:
       the flag is stamped downstream of in-graph state at
       ``daemon/persistence.py:645`` (the ``GET /messages``
       ``get_instance_messages`` rebuild path) and is NOT present on
       in-graph checkpoint messages, so it cannot fire from inside a
       running LangGraph node. Keeping it here is the belt-and-suspenders
       coverage if a reader consumer ever hands a synthetic-rebuilt
       list back into this predicate; the live-gate path relies on
       ``injected_message`` alone.
    4. **Source string** — ``additional_kwargs.source`` starting with
       ``internal_report:`` (child-report convention) or
       ``internal_agent:`` (agent-to-agent dispatch convention)
       ⇒ excluded. Defense in depth if the dict shim ever drops an
       ``injected_message`` flag.
    5. **Content sentinel** — the HumanMessage body starting with
       ``"[SYSTEM CONTEXT:"`` (the standard ``[SYSTEM CONTEXT: …]``
       block prefix — the same prefix the ``_make_context_message``
       factory enforces) ⇒ excluded. Belt-and-suspenders against the
       rare edge where the kwargs shim drops a flag.

    Args:
        message: The candidate message. ``BaseMessage`` accepts all
            concrete subclasses; the function is type-tolerant.

    Returns:
        ``True`` when ``message`` is a real user-authored HumanMessage;
        ``False`` otherwise (including non-HumanMessage, every
        injection kind, and the gate's own nudge).
    """
    # 1. Type gate.
    if not isinstance(message, HumanMessage):
        return False

    additional_kwargs = getattr(message, "additional_kwargs", None) or {}

    # 2. Attestation nudge — the gate's own deny injection; must NOT be
    #    the "last real user message" (would reset the delegation window
    #    on deny and self-defeat the feature).
    if additional_kwargs.get("attestation_nudge") is True:
        return False

    # 3. Injected / synthetic flag — both the ``_make_context_message``
    #    factory and the ``_frame_injected_report`` injector stamp
    #    ``injected_message=True``; the synthetic system path stamps
    #    ``is_synthetic=True`` (``daemon/persistence.py:645`` — read-
    #    model-only defense in depth, NOT present on in-graph checkpoint
    #    messages; see docstring step 3 for the live-gate vs reader
    #    scope split).
    if additional_kwargs.get("injected_message") is True:
        return False
    if additional_kwargs.get("is_synthetic") is True:
        return False

    # 4. Source string — child reports and internal agent dispatches.
    source = additional_kwargs.get("source")
    if isinstance(source, str) and (
        source.startswith(_INTERNAL_REPORT_SOURCE_PREFIX)
        or source.startswith(_INTERNAL_AGENT_SOURCE_PREFIX)
    ):
        return False

    # 5. Content sentinel — ``[SYSTEM CONTEXT: …]`` blocks. The prefix is
    #    the canonical ``CONTEXT_PREFIX`` — the same string the factory
    #    enforces (``daemon/services/context_messages.py``). A real user
    #    message never starts with this prefix.
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.startswith("[SYSTEM CONTEXT:"):
        return False

    return True


def find_last_real_user_index(messages: list[BaseMessage]) -> int:
    """Index of the last real user message in ``messages`` (-1 if none).

    Walks the list BACKWARD so the most recent real user message wins
    even when older (children, injected reports, the gate's own nudge)
    HumanMessages sit after it. Mirrors the bounded-scan contract of
    the attestation scanner: a no-real-user state returns ``-1`` (the
    canonical "not found" sentinel — the gate uses this to fall back
    to the conservative delegation-anywhere-in-window predicate per
    design trap (1) in the task spec).

    Args:
        messages: The in-node message list (``state["messages"]``).

    Returns:
        The position in the original list of the last real user
        message, or ``-1`` when no real user message exists.
    """
    for index in range(len(messages) - 1, -1, -1):
        if is_real_user_message(messages[index]):
            return index
    return -1


class DelegationScanResult(NamedTuple):
    """Conditional-attestation delegation scan output.

    Attributes:
        delegation_since_last_user: ``True`` when a ``send_message`` tool
            call exists in any AIMessage AFTER the last real user
            message (the canonical "deliver an attestation-required"
            signal for the gate).
        last_real_user_index: Position in the original list of the last
            real user message, or ``-1`` when none exists (the gate's
            delegation-anywhere fallback predicate).
        last_real_user_found: ``True`` iff a real user message exists
            (the gate reads this to decide whether to enter the
            conservative fallback branch).
        first_delegation_after_last_user_index: Position of the FIRST
            ``send_message`` AIMessage at-or-after ``last_real_user_index``,
            or ``-1`` when none exists. Logged for dry-mode soak
            diagnostics and the FR-3 conditionality audit log.
        delegation_tool_call_total: Total number of ``send_message`` tool
            calls at-or-after ``last_real_user_index`` (across the whole
            tail). Normalized log shape; ``0`` when none.
    """

    delegation_since_last_user: bool
    last_real_user_index: int
    last_real_user_found: bool
    first_delegation_after_last_user_index: int
    delegation_tool_call_total: int


def scan_delegation_after_last_user(
    messages: list[BaseMessage],
    *,
    delegation_tool_name: str = DEFAULT_DELEGATION_TOOL_NAME,
) -> DelegationScanResult:
    """Scan AIMessages after the last real user message for a send_message tool call.

    The conditional-attestation gate's R0 input. Walks the message list
    once BACKWARD to locate :func:`find_last_real_user_index`, then walks
    the AIMessages strictly AFTER that index for any tool call whose
    ``name == delegation_tool_name`` (default ``send_message``). The
    walk stops at the FIRST found delegation — the index is reported,
    but the boolean is what gates the decision.

    The walk is NOT bounded by the attestation window ``N`` — the
    delegation check covers the entire tail of the conversation since
    the last real user message, which is exactly the "did this mission
    actually dispatch children" question. The cost is O(len(tail))
    AIMessages worst-case, and the tail is bounded in practice by the
    proactive-compaction summary boundary (one final SystemMessage per
    compaction fold — the walk skips it because it is not an AIMessage).

    Args:
        messages: The in-node message list (``state["messages"]``).
        delegation_tool_name: Tool name that constitutes delegation.
            Default ``send_message`` — the user's contract pin. ANY
            ``spawn_instance`` before/without a corresponding
            ``send_message`` does NOT count (spawn is inert by design).

    Returns:
        :class:`DelegationScanResult` — see the NamedTuple docs.
    """
    last_real_user_index = find_last_real_user_index(messages)
    last_real_user_found = last_real_user_index >= 0

    first_delegation_after_last_user_index = -1
    delegation_tool_call_total = 0

    # Walk ONLY the tail at-or-after the last real user index — older
    # AIMessages are NOT counted (a stale pre-mission send_message must
    # not re-arm the conditional requirement after the user sent a fresh
    # message). When no real user message exists, walk the whole list
    # (the gate's conservative fallback branch).
    tail_start = last_real_user_index if last_real_user_found else 0
    tail = messages[tail_start:]

    for offset, message in enumerate(tail):
        if not isinstance(message, AIMessage):
            continue
        names = _tool_call_names(message)
        if delegation_tool_name in names:
            absolute_index = tail_start + offset
            delegation_tool_call_total += 1
            if first_delegation_after_last_user_index == -1:
                first_delegation_after_last_user_index = absolute_index

    return DelegationScanResult(
        delegation_since_last_user=first_delegation_after_last_user_index != -1,
        last_real_user_index=last_real_user_index,
        last_real_user_found=last_real_user_found,
        first_delegation_after_last_user_index=first_delegation_after_last_user_index,
        delegation_tool_call_total=delegation_tool_call_total,
    )
