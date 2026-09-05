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

from langchain_core.messages import AIMessage, BaseMessage

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
