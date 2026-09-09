"""Leader completion attestation tool (``attest_completion``).

Marks that a delegated mission is genuinely complete — all
dispatched children have reported and the work is done. Idempotent
no-op: the attestation is recorded by virtue of the tool call
existing in the leader's message stream, which the in-graph
completion gate scans to allow or deny the END transition.

CONDITIONAL USE — call ONLY when ALL hold:

* This mission dispatched children via ``send_message``.
* The system is nudging the attestation (e.g. a completion-check
  nudge has reached you).
* The detailed final report is ALREADY delivered as its own
  separate message.

WHEN NOT TO CALL: plain answers, chart requests, quick
follow-ups, or any mission that did not dispatch children. You
also do not need to call it when the gate or judge has already
released you without nudging — the nudge is the trigger.

The attestation tool-call message must NOT carry the report — at
most a one-line ack such as "Report delivered above; attesting completion." Calling this tool more than once in the same turn
has the same effect as calling it once.

Per-agent teaching source is the deny-time nudge; this docstring
header is the single canonical tool-side reference. Scope:
leader-only via ``agents/leader/meta.json`` ``tools.allow``; NOT
privileged per the project's closed-by-leader D7 ruling.
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
Leader completion attestation — a deterministic no-op signal that a
delegated mission is genuinely complete (all dispatched children have
reported, work done). The attestation is recorded by virtue of the
tool call existing in the leader's message stream; the in-graph
completion gate scans that stream to allow or deny the END transition.

CONDITIONAL — call ONLY when ALL hold:

* This mission dispatched children via ``send_message``.
* The system is nudging the attestation (e.g. a completion-check
  nudge has reached you).
* The detailed final report is ALREADY delivered as its own
  separate message.

DO NOT call for plain answers, chart requests, quick follow-ups,
or any mission that did not dispatch children — and do not call
when the gate or judge has already released you without nudging.
The attestation tool-call message must NOT carry the report — at
most a one-line ack such as "Report delivered above; attesting completion." Idempotent: any number of calls in the same turn
counts as one.

Scope: leader-only via ``agents/leader/meta.json`` ``tools.allow``;
NOT privileged per the project's closed-by-leader D7 ruling. Per-
agent teaching source is the deny-time nudge; this category doc
is the single canonical tool-side reference (alongside the tool's
own docstring + the module header).
"""


@register_tool_category("attestation")
@tool
def attest_completion() -> dict[str, Any]:
    """Signal that a delegated mission is genuinely complete.

    CONDITIONAL — call ONLY when ALL hold:
      • This mission dispatched children via ``send_message``.
      • The system is nudging you (a completion-check nudge arrived).
      • The detailed final report is ALREADY delivered as its own
        separate message.

    DO NOT call for plain answers, charts, quick follow-ups, or any
    mission that did not dispatch children — and do not call when
    the gate or judge has already released you without nudging.

    The attestation tool-call message must NOT carry the report —
    at most a one-line ack such as "Report delivered above; attesting completion." Idempotent: any number of calls in the
    same turn counts as one.

    Returns:
        ``{"attested": True, "timestamp": "<iso8601 UTC>"}``.
    """
    return {
        "attested": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


attest_completion._full_doc_ = """\
Signal that a delegated mission is genuinely complete.

CONDITIONAL — call ONLY when ALL hold:
  • This mission dispatched children via ``send_message``.
  • The system is nudging you (a completion-check nudge arrived).
  • The detailed final report is ALREADY delivered as its own
    separate message.

DO NOT call for plain answers, charts, quick follow-ups, or any
mission that did not dispatch children — and do not call when the
gate or judge has already released you without nudging.

The attestation tool-call message must NOT carry the report — at
most a one-line ack such as "Report delivered above; attesting completion." Idempotent: any number of calls in the same turn
counts as one.

Mechanics: deterministic no-op body; the attestation is recorded by
virtue of the tool call existing in the leader's message stream,
which the in-graph completion gate scans to allow or deny the END
transition.

Scope: leader-only via ``agents/leader/meta.json`` ``tools.allow``;
NOT privileged per the project's closed-by-leader D7 ruling. Per-
agent teaching source is the deny-time nudge; this docstring is
the single canonical tool-side reference (alongside the tool's own
docstring + the module header).

Returns:
    ``{"attested": True, "timestamp": "<iso8601 UTC>"}``.
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