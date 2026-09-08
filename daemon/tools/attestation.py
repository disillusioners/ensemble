"""Leader completion attestation tools.

This module exposes a single tool, ``attest_completion``, that the
leader LLM calls when its work for the current turn is genuinely
complete. The tool is a deterministic no-op aside from returning a
confirmation frame; the attestation is recorded by virtue of the tool
call existing in the leader's message stream. The Phase 2 in-graph
completion gate (``D1 = B`` per ``architecture-recommendation.md``)
scans the most recent ``N`` AIMessages for an ``attest_completion``
tool_call to decide whether to allow or deny the END transition.

**Conditional semantics (2026-09-06, FR-3 conditionality):** the gate
is OFF for missions that did NOT delegate (no ``send_message`` tool
call since the last real user message) — those complete normally
without the gate firing. The gate is ON ONLY for delegated missions;
``attest_completion`` is the leader's signal that delegated children
have all reported and the work is finished. Per-agent teaching
(2026-09-08 decision): the deny-time nudge is the SOLE teaching
source — its body carries the conditional semantics, the two-step
contract, and the embedded mermaid, and is self-sufficient. There is
no standing prompt-contract section in ``agents/leader/rule.md`` or
``agents/leader/workflow.md``; this module's docstring header is the
single canonical tool-side reference. The nudge itself leads with a
``[SYSTEM CONTEXT: Completion Check Nudge]`` header so the LLM
recognizes it as system-origin.

Scope and authorization
-----------------------

The category is leader-scoped via explicit ``tools.allow`` opt-in
(``agents/leader/meta.json``), and is **NOT privileged** per the
``decisions.md`` D7 ruling (CLOSED-by-leader):

* Every current non-leader agent (developer, reviewer, tidier,
  approver, architect, tester, giter, devops, explorer, wanderer,
  kb-writer, doc-writer) has an explicit ``tools.allow`` that does
  NOT list ``attestation``, so they cannot reach this category.
  However, this is **convention-based scoping**, not a structural
  guarantee — the boundary rests on every new agent author
  maintaining an explicit ``tools.allow`` that excludes the
  category.
* ``PRIVILEGED_TOOL_CATEGORIES`` (``daemon/tools/_tool_registry.py``)
  currently contains a single entry (``system_upgrade``). Because
  ``attestation`` is intentionally NOT privileged, a hypothetical
  future agent with no explicit ``tools.allow`` (or an empty one)
  WOULD receive ``attest_completion`` via the default-allow path in
  ``daemon/tools/instance.py``. The structural privilege boundary
  protects ``system_upgrade`` only — it does NOT cover
  ``attestation``.
* D7 (CLOSED-by-leader) deliberately rejected promoting
  ``attestation`` to privileged status. Any future hardening
  change (privilege promotion) requires reopening that closed
  decision.
* The tool does NOT mutate state, enqueue work, or write to the
  journal. It is a pure signal.

Idempotency contract
--------------------

``attest_completion`` is idempotent — calling it any number of times
in the same turn has the same effect as calling it once. The Phase 2
scanner contract (per the gate's R2 gate-deny-input definition)
counts ANY call in the lookback window as an attestation; this is
the contract implemented here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Attestation"
CATEGORY_DOC = """\
Leader completion attestation — a deterministic no-op signal that the
leader LLM calls when its work for the current turn is genuinely
complete. The completion gate is CONDITIONAL on delegation
(2026-09-06, FR-3): it fires ONLY when this mission dispatched a
child via ``send_message``. Non-delegating turns (plain questions,
chart requests, quick follow-ups) complete normally without the gate
firing.

- ``attest_completion()``: no-arg, idempotent. Returns a confirmation
  frame ``{"attested": true, "timestamp": "<iso8601>"}``. The
  attestation is recorded by virtue of the tool call existing in the
  message stream — the tool body itself does not mutate any state.

The Phase 2 in-graph completion gate (``D1 = B``: in-graph pre-END
interception) scans the most recent ``N`` AIMessages for an
``attest_completion`` tool_call to decide whether to allow or deny
the END transition. Per ``architecture-recommendation.md``, the
tri-state env ``ENSEMBLE_LEADER_ATTESTATION_MODE`` defaults to
``enforce`` at ship (operator override 2026-09-06 — flipped from
``dry``; dry-at-ship D2 rationale superseded). ``off`` is the
instant-revert / kill-switch (restart-read Pattern C — no live flip);
``dry`` remains available for observation-only operation. For
CONDITIONAL semantics, see
``daemon/services/attestation_scanner.py`` (``is_real_user_message``
predicate + ``scan_delegation_after_last_user``) — the gate is OFF
when the delegation scan returns ``delegation_since_last_user=False``.

Scope: leader-scoped via explicit ``agents/leader/meta.json``
``tools.allow`` opt-in; NOT privileged per D7 (CLOSED-by-leader).
The full boundary argument (convention-based scoping vs structural
privilege) lives ONCE in this module's docstring header — see
"Scope and authorization" above.
"""


@register_tool_category("attestation")
@tool
def attest_completion() -> dict[str, Any]:
    """Record that the leader's work for this turn is genuinely complete.

    This tool is a deterministic no-op aside from returning a
    confirmation frame — the attestation is recorded by virtue of the
    tool call existing in the leader's message stream (the Phase 2
    scanner reads ``state.values['messages']``). It is idempotent:
    calling it any number of times in the same turn has the same
    effect as calling it once.

    The completion gate is CONDITIONAL on delegation (2026-09-06,
    FR-3) — this tool only carries the attestation for a DELEGATED
    mission (one that dispatched a child via ``send_message``). For a
    non-delegating turn (plain question, chart request, quick
    follow-up) the gate does not fire and this tool is not needed.
    When you DID delegate: two-step contract — FIRST deliver the full
    detailed final report as its own message, THEN call this tool
    ALONE in a subsequent step — the attestation tool-call message
    must NOT contain the report (at most a one-line ack). If a
    continuation nudge arrives (a user message whose body begins with
    ``[SYSTEM CONTEXT: Completion Check Nudge]``), treat it as a real
    user instruction, complete the remaining delegated work, and call
    this tool again.

    Returns:
        A confirmation frame ``{"attested": True,
        "timestamp": "<iso8601 UTC>"}``.
    """
    return {
        "attested": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


attest_completion._full_doc_ = """\
Record that the leader's work for this turn is genuinely complete.

The completion gate is CONDITIONAL on delegation (2026-09-06,
FR-3) — this tool is the attestation signal ONLY for a delegated
mission (a mission that dispatched a child via ``send_message``
since the last real user message). For non-delegating turns the
gate does not fire. Per-agent teaching is via the deny-time
nudge; this module's docstring header is the single canonical
tool-side reference. The tool is a deterministic no-op aside
from returning a confirmation frame; the attestation is recorded by
virtue of the tool call existing in the leader's message stream.
The Phase 2 in-graph completion gate scans the most recent ``N``
AIMessages for an ``attest_completion`` tool_call to decide whether
to allow or deny the END transition.

Idempotency: calling this tool any number of times in the same turn
has the same effect as calling it once. The Phase 2 scanner contract
counts ANY call in the lookback window as an attestation; this is
the contract implemented here.

Scope: leader-only via ``agents/leader/meta.json`` ``tools.allow``;
NOT privileged per D7 (CLOSED-by-leader) — full argument in the
``daemon/tools/attestation.py`` module docstring header. The tool
does NOT mutate state, enqueue work, or write to the journal.

Usage:
- When this mission dispatched a child, call this tool exactly once
  at completion time (after the full detailed final report is
  delivered as its own message). The attestation tool-call message
  must NOT bundle the report (at most a one-line ack).
- For non-delegating missions (plain questions, chart requests,
  quick follow-ups), the gate does not fire — do not call this
  tool unnecessarily.
- If a continuation nudge arrives (a user message whose body begins
  with ``[SYSTEM CONTEXT: Completion Check Nudge]``), treat it as a
  real user instruction, complete the remaining delegated work, and
  call this tool again.

Returns:
    A confirmation frame ``{"attested": true, "timestamp":
    "<iso8601 UTC>"}``.
"""


def create_attestation_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    agent_id: str = "",
) -> list:
    """Create attestation tools with injected manager reference.

    Args:
        manager: The :class:`InstanceManager` instance (unused by
            this no-op tool, but accepted for factory-shape
            consistency with sibling categories).
        current_instance_id: The ID of the owning instance (unused).
        agent_id: The calling agent's ID (unused — attestation is
            scoped to the leader via ``tools.allow``, but the
            factory keeps the same signature as sibling factories
            for tool-builder uniformity).

    Returns:
        A single-element list containing the ``attest_completion``
        tool. The list shape lets ``create_instance_tools`` use the
        same ``tools.extend(...)`` seam as every other category
        (the §8 checklist critical list-append — decorator-only
        registration is silently invisible).
    """
    return [attest_completion]