"""Leader completion attestation tools.

This module exposes a single tool, ``attest_completion``, that the
leader LLM MUST call when its work for the current turn is genuinely
complete (see ``agents/leader/rule.md`` for the prompt contract). The
tool is a deterministic no-op aside from returning a confirmation
frame; the attestation is recorded by virtue of the tool call existing
in the leader's message stream. The Phase 2 in-graph completion gate
(``D1 = B`` per ``architecture-recommendation.md``) scans the most
recent ``N`` AIMessages for an ``attest_completion`` tool_call to
decide whether to allow or deny the END transition.

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
leader LLM MUST call when its work for the current turn is genuinely
complete.

- ``attest_completion()``: no-arg, idempotent. Returns a confirmation
  frame ``{"attested": true, "timestamp": "<iso8601>"}``. The
  attestation is recorded by virtue of the tool call existing in the
  message stream — the tool body itself does not mutate any state.

The Phase 2 in-graph completion gate (``D1 = B``: in-graph pre-END
interception) scans the most recent ``N`` AIMessages for an
``attest_completion`` tool_call to decide whether to allow or deny
the END transition. Per ``architecture-recommendation.md``, the
tri-state env ``ENSEMBLE_LEADER_ATTESTATION_MODE`` defaults to
``dry`` at ship (telemetry before commitment); promote to
``enforce`` after a ≤2-week soak with adjudicated dry-log
false-positive rate.

Scope: leader-scoped via explicit ``agents/leader/meta.json``
``tools.allow`` opt-in. NOT privileged per D7 (CLOSED-by-leader)
— ``PRIVILEGED_TOOL_CATEGORIES`` intentionally excludes
``attestation`` (only ``system_upgrade`` is privileged today), so
the boundary is convention-based: a hypothetical future agent
without an explicit ``tools.allow`` WOULD receive
``attest_completion`` via the default-allow path. Future
hardening (privilege promotion) requires reopening D7.
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

    The leader MUST call this tool before declaring itself done (see
    the prompt contract in ``agents/leader/rule.md``). If a
    continuation nudge arrives ("The work is not yet finished —
    check current progress and continue."), treat it as a real
    user instruction, complete the remaining work, and call this
    tool again.

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

The leader LLM MUST call this tool before declaring itself done (see
the prompt contract in ``agents/leader/rule.md``). The tool is a
deterministic no-op aside from returning a confirmation frame; the
attestation is recorded by virtue of the tool call existing in the
leader's message stream. The Phase 2 in-graph completion gate scans
the most recent ``N`` AIMessages for an ``attest_completion``
tool_call to decide whether to allow or deny the END transition.

Idempotency: calling this tool any number of times in the same turn
has the same effect as calling it once. The Phase 2 scanner contract
counts ANY call in the lookback window as an attestation; this is
the contract implemented here.

Scope: leader-only via ``agents/leader/meta.json`` ``tools.allow``.
NOT privileged (only ``system_upgrade`` is privileged today). Does
NOT mutate state, enqueue work, or write to the journal.

Usage:
- Call exactly once when the leader's work is genuinely complete
  and the leader is about to be done.
- If a continuation nudge arrives ("The work is not yet finished
  — check current progress and continue."), treat it as a real
  user instruction: review your current progress, complete the
  remaining work, and call this tool again.

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