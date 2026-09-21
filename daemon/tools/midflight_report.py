"""Mid-flight report tool — non-blocking progress surfacing.

2026-09-21, ``feature/midflight-qa-channel`` (design §4.2 / §7.2).

``mid_flight_report`` complements ``ask_questions``: it surfaces
progress or a decision to the job watcher WITHOUT pausing. The emit
rides the same push lanes as the question channel (EventBus →
LiveEventHub banner → work_notifier ``[JOB_EVENT] Job {work_id}...
mid-flight report ⟳`` into every watcher's instance, non-terminal so
the watcher row survives for the eventual terminal event).

Hard constraint: NO pause flag is set, no post-tools-router side
effect fires. Mid-flight reports are by design non-blocking — the
``decision_required`` affordance marks "this is a question in
disguise" for the orchestrator's prioritization, but the blocking
path remains ``ask_questions``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "midflight"
CATEGORY_DOC = """\
Mid-flight reporting: surface progress or a decision to the job
watcher without pausing.

- mid_flight_report(summary, details, level, decision_required):
  fires a non-blocking MIDFLIGHT_REPORT event on the push lanes —
  the FE gets a banner (LiveEventHub), and every job watcher receives
  a ``[JOB_EVENT] Job {work_id}... mid-flight report ⟳`` line in its
  context. Does NOT pause; for blocking questions use ask_questions.
"""

# Payload caps per design §3.3.
MAX_SUMMARY_CHARS = 500
MAX_DETAILS_CHARS = 8 * 1024


def create_midflight_tools(
    manager: "InstanceManager",
    current_instance_id: str,
) -> list:
    """Create the mid-flight report tools with an injected manager.

    Mirrors the closure-injection pattern of ``create_question_tools``
    / ``create_todo_tools``: invoked from ``create_instance_tools``.
    Opt-in per agent via ``tools.allow: ["midflight"]`` — the category
    is NOT auto-granted through any innate-skill mapping.
    """

    @register_tool_category("midflight")
    @tool
    async def mid_flight_report(
        summary: str,
        details: str = "",
        level: Literal["info", "warning", "decision"] = "info",
        decision_required: bool = False,
    ) -> str:
        """Surface progress or a decision to the job watcher without pausing.

        Fires a non-blocking MIDFLIGHT_REPORT event:
          - via LiveEventHub to the FE (banner; non-modal)
          - via work_notifier to any job watcher (jober, orchestrator) —
            the watcher sees ``[JOB_EVENT] Job {work_id}... mid-flight
            report ⟳`` in its context, can relay to the human, and is
            NOT blocked on a reply.

        Does NOT pause the instance. For blocking, use ask_questions
        instead (``decision_required=True`` here only marks priority
        for the orchestrator; it never pauses the reporter).

        Args:
            summary: ≤500-char human-readable headline (required).
            details: ≤8KB optional longer text.
            level: ``"info"`` | ``"warning"`` | ``"decision"`` — lets
                the orchestrator prioritize relaying.
            decision_required: ``True`` marks "a decision point was
                reached" (still non-blocking).

        Returns:
            ``"Reported. N watcher(s) notified."``
        """
        from daemon.services.midflight_qa import emit_midflight_report

        # Truncate (never reject) — a report that overruns the cap is
        # still useful; failing the tool over a length nit would lose
        # the signal entirely.
        summary = (summary or "")[:MAX_SUMMARY_CHARS]
        details = (details or "")[:MAX_DETAILS_CHARS]
        if not summary:
            return "ERROR: mid_flight_report requires a non-empty summary."

        try:
            notified = await emit_midflight_report(
                manager,
                current_instance_id,
                summary=summary,
                details=details,
                level=level,
                decision_required=decision_required,
            )
        except Exception as e:  # noqa: BLE001 — §8.6: emission must never break the caller's turn
            logger.warning(
                f"mid_flight_report emission failed for instance "
                f"{current_instance_id[:8]}...: {e}"
            )
            return f"Report attempted but emission failed: {e}"

        return f"Reported. {notified} watcher(s) notified."

    mid_flight_report._full_doc_ = f"""\
Surface progress or a decision to the job watcher without pausing.

Fires a non-blocking MIDFLIGHT_REPORT event on the push lanes:
  1. EventBus — persisted ``midflight_report`` event row + global
     broadcast (chat adapters default-ignore unknown kinds).
  2. LiveEventHub — FE banner (non-modal).
  3. work_notifier — every watcher of every live work_id owned by
     this instance receives ``[JOB_EVENT] Job {{work_id}}... mid-flight
     report ⟳`` in its context. Non-terminal: the watch survives for
     the eventual terminal event.

Does NOT pause. For blocking, use ask_questions instead.

Args:
  summary: ≤{MAX_SUMMARY_CHARS}-char human-readable headline (required).
  details: ≤{MAX_DETAILS_CHARS // 1024}KB optional longer text.
  level: "info" | "warning" | "decision" — orchestrator prioritization hint.
  decision_required: True marks a decision point (still non-blocking).

Returns:
  "Reported. N watcher(s) notified."
"""

    return [mid_flight_report]
