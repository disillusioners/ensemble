"""WAITING_CHILDREN hang watchdog.

Module-size rationale (band 1000-3000): single owner of the hang + wedge passes; cohesive, not split.

Issue: when a parent enters ``WAITING_CHILDREN`` to await one or more
child completion reports, it sleeps on its dependency watcher until
``child_reports`` / ``error_reporting`` drains. The event-driven path
works for children that reach a terminal status (completed / error /
terminated / failed). It does NOT work for children that *silently
hang* — the child is still RUNNING in name, but its LLM call, tool
invocation, or external subprocess is wedged. The watcher never
fires; the parent sleeps forever; the user eventually gives up.

This service is the safety net. Every ``interval_seconds`` (default
3600s = 1h), it:

1. Enumerates instances whose status is ``WAITING_CHILDREN``.
2. For each, asks the instance repository for non-terminal children
   whose ``last_activity_at`` is older than ``hang_threshold_seconds``
   (default 3600s). Age is computed SQL-side via
   ``EXTRACT(EPOCH FROM (now() - col))`` on PostgreSQL and
   ``julianday() * 86400`` on SQLite — see
   :meth:`SQLModelInstanceRepository.list_hung_children_for_parent`
   for the rationale (avoid the psycopg 7h skew).
3. Skips ``paused`` children (those are NOT hung — they are awaiting
   a user/system decision and the parent must NOT be nagged).
   Also skips ``waiting_children`` children — a child parked on ITS
   children is waiting by design (parking does not refresh
   ``last_activity_at``), and nudging would duplicate a still-working
   subtree.
4. Skips ``paused`` parents entirely — the operator is the
   decision-maker during a pause, and a notice computed from
   pre-pause hang data would be stale by the time the instance
   resumes. Skipping also keeps the scan cheap.
   B.S.5 (wc-wake-report-integrity Wave 2, OQ-4): the wedge pass
   additionally skips parents with an ACTIVE (b) report-integrity
   notice episode (``report_integrity_guard.parent_has_active_b_notice``)
   so a parent that (b) just noticed at its completion stamp never
   gets a second wedge notice for the same episode (shared per-parent
   cooldown; the wedge is eligible again once the (b) episode closes
   or is replaced by a new violation set).
5. For each (parent, child) pair in a new "hang episode", delivers a
   terse, directive notice to the parent via
   :meth:`InstanceManager.enqueue_message` with provenance
   ``"system:watchdog"``. The notice lists the hung children, how
   long each has been stuck, and the four-step playbook:
   ``subtree_messages`` for inspection, send_message/job_continue
   for one-shot revive (mechanically bounded by
   ``_agent_tool_revive_counts``), spawn a replacement, or escalate
   to the user.
6. **Anti-spam**: tracks in-memory which (parent, child) pairs are
   currently in a notified episode. Re-notifies only when the
   episode ENDS — i.e., the child reaches a terminal status OR
   becomes paused OR the parent leaves ``WAITING_CHILDREN``.
   Cooldown resets run INDEPENDENTLY of the per-parent scan (see
   "Episode boundaries" below) so a pair can never strand in the
   set forever. The cooldown is per-process; a daemon restart
   resets it. This is an accepted v1 limitation. Restart note
   (council round-2): with ``discard_on_startup`` the startup
   ``clear_all(preserve_in_flight=True)`` may sweep a committed-
   but-undrained notice (its Task is PENDING, not running/paused);
   combined with the in-memory cooldown reset, the worst case is
   ≤1 interval of re-delay before the next tick re-notifies.

**Delivery primitive — ``enqueue_message`` (the waking path).**
The notice MUST wake the parked parent. ``set_injection`` is a RAM
FIFO append only (no Task, no status flip, no ``notify_work()``);
a quiesced ``WAITING_CHILDREN`` parent drains that FIFO only on its
next LLM cycle, which never arrives while the awaited child hangs —
the notice strands and ``_cleanup_stale_injections`` deletes it
~1h later (deep-review 2026-08-27, range 85ae6e72..fe076043,
REJECTED for exactly this). ``enqueue_message`` is the house wake
primitive: it writes the ``MessageQueue`` + ``Task`` rows in one
transaction, flips ``WAITING_CHILDREN`` → ``RUNNING``, and calls
``worker_pool.notify_work()`` so a worker claims the Task and the
notice lands in the parent's message stream as a real turn. This is
the same internal path a watched child's terminal report uses
(``dependency_bus`` → ``instance_messaging``), it creates NO
``JobItem`` mirror (JAFP: internal agent-to-agent paths use
``enqueue_message`` only), and it bypasses the operator HTTP/agent
parking-lot routing (``messages.py``) which cannot reach a parked
parent either.

**Provenance on the enqueue path.** ``enqueue_message(source=...)``
stamps the provenance on the durable ``MessageQueue.source`` column,
echoes it into the ``MESSAGE_RECEIVED`` event data
(``"source": "system:watchdog"``), and this module additionally
passes a structured ``metadata`` dict (``message_metadata`` column)
carrying the hung-child list and threshold for audit. The
``additional_kwargs['source']`` stamping used by the FIFO path does
not exist on the enqueue path — by design, provenance rides the
MessageQueue row / event data instead. ``"system:watchdog"`` is
deliberately distinct from ``"internal_agent:<caller_iid>"``
(agent-tool send_message) so the watchdog's notices are visibly
system-side infrastructure; ``_process_message_with_tracking``
resolves ``system:``-prefixed sources like internal reports (looks
up the instance's original external source) instead of stamping
itself as the instance's ``original_source``.

**Scheduler mechanism**: this watchdog is wired as an asyncio task
in the FastAPI lifespan, mirroring the existing
``_periodic_drift_reconcile_loop`` and
``_periodic_readiness_refresh_loop`` patterns. Rationale:

* **Matches house conventions for new periodic infrastructure**
  (``daemon/api.py:997`` drift reconciler; ``daemon/api.py:1066``
  readiness refresher). StaleTaskRecovery's ``threading.Thread`` is
  the older precedent; the asyncio path is what every new
  infra-loop uses because cancellation semantics are cleaner and
  the lifespan already manages task lifecycle + graceful shutdown.
* **The watchdog is async-friendly** — the delivery call is
  ``await manager.enqueue_message(...)`` (async, DB-backed) and the
  repo helpers (``list_waiting_children_parents``,
  ``list_hung_children_for_parent``) use short-lived
  SQLModelSessions that the asyncio thread pool handles fine.
* **Must shut down with the daemon** — the lifespan cancel/await
  pattern guarantees the task is cancelled cleanly on shutdown,
  with ``asyncio.CancelledError`` propagating out so the runtime
  knows the task is done. No leaked threads, no orphaned
  half-finished scans.
* **Does NOT allocate JobItems** — internal infrastructure paths
  use the lifespan directly per JAFP (``enqueue_message`` /
  send_message / report lanes are not Jobs; watchdog is not a job
  either).
* **Per-tick error isolation** — a single parent's scan failing
  (DB blip, malformed row) is caught and logged; the loop
  continues. A loop-level failure is also caught and logged so a
  persistent DB outage does not crash the daemon.

**Episode boundaries (cooldown resets).** Three independent
mechanisms clear the ``_notified`` set, so no pair can strand:

1. Scan-driven sweep (this module, per tick): for parents whose
   scan succeeded, pairs absent from the fresh SQL result ended
   their episode (child terminal, paused, or resumed activity).
   A transient scan error preserves the pair (regression-pinned).
2. Parent-left-WC purge: a parent no longer in the tick's
   ``WAITING_CHILDREN`` enumeration has all its pairs dropped —
   the parent observed something (report, external message,
   revival) and a future WC re-entry must be able to notify fresh.
3. Child-terminal purge: every tick, all children still referenced
   by the cooldown set are checked against the terminal set via
   ``list_terminal_instance_ids`` and those pairs dropped — a child
   that terminated via ANY path (including while its parent's scan
   was erroring, or after the parent left WC) ends its episode.

Purges 2 and 3 are DB-read-backed and run even when individual
parent scans failed; only a full enumeration failure (repo blip)
skips the whole tick's purges (no fresh signal at all).

**PAUSED parents**: skipped entirely per design (see step 4).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.repositories.dependency_bus.models import DependencyWatcher

from daemon.constants import TERMINAL_INSTANCE_STATUSES
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.report_integrity_guard import parent_has_active_b_notice
from daemon.services.attestation_ledger import L6_ANCHOR_META_KEY
from daemon.services.pool_orchestrator import safe_notify_all_pools

logger = logging.getLogger(__name__)


#: Provenance marker stamped onto the enqueued MessageQueue row
#: (``MessageQueue.source`` column) so the parent's stream shows the
#: message's origin; echoed into the ``MESSAGE_RECEIVED`` event data
#: and the ``message_metadata`` audit dict. Distinct from
#: ``"internal_agent:<caller_iid>"`` (agent-tool send_message) so
#: the watchdog's notices are visibly system-side infrastructure.
WATCHDOG_SOURCE: str = "system:watchdog"

#: Wedge-fix backstop provenance marker — distinct from
#: ``WATCHDOG_SOURCE`` so wedge notices can be filtered out of the
#: hang-notice metric stream. Same semantics: stamped onto the
#: durable ``MessageQueue.source`` column.
WEDGE_SOURCE: str = "system:watchdog:wedge"

#: **WITHDRAWN (S4, wake-path-carrier-fix)** — the suppression TTL is
#: GONE: it no longer functions as a suppression window. The wedge
#: episode ends on the ROW-CHECK (the stamped reason code stops
#: holding) and the dedupe is the reason-code fingerprint — not a
#: timer. The DELTA-2 rationale it carried (a re-parked parent must
#: become re-eligible) is served by the S4/B2 episode-end evaluation
#: instead. The constant is kept ONLY as an import-compatible symbol
#: (no runtime path and — since the S4 TTL test was rewritten — no
#: test in this tree imports it); it MUST NOT be reintroduced as a
#: suppression window.
WEDGE_REARM_COOLDOWN_SECONDS: float = 30 * 60


# ── S4 (wake-path-carrier-fix, phase 4) — the notice surface ──────────────
#
# The wedge notice used to be composed from a TEMPLATE that asserted a
# queue state the watchdog never read ("A PROCESS_REPORT message is in
# your queue but no worker is scheduled to deliver it") — FALSE in 100%
# of the notices ever minted (the watchdog read NO bus rows at all).
# S4 replaces the template with a COMPOSED notice: every line is a
# REASON CODE whose check is a ROW check, and a code is emitted ONLY
# when its check holds.
#
# The codes:
#
# * ``pending_watchers`` — the S4_NOTICE_SUBJECT of the parent is
#   non-empty: at least one PENDING watcher that is NOT fulfillable
#   (its watched child is already terminal, or the watcher is
#   self-referential) — i.e. a wait that can NO LONGER FIRE.
# * ``no_live_carrier`` — NO live PROCESS_REPORT carrier task is
#   scheduled for this parent.
# * ``queued_report`` — a ``completion_report`` row EXISTS in
#   ``message_queue`` and has not completed yet (the row id is cited).
# * ``non_terminal_children`` — the parent still has M non-terminal
#   children (the park is legitimate while they run).
WEDGE_REASON_PENDING_WATCHERS: str = "pending_watchers"
WEDGE_REASON_NO_LIVE_CARRIER: str = "no_live_carrier"
WEDGE_REASON_QUEUED_REPORT: str = "queued_report"
WEDGE_REASON_NON_TERMINAL_CHILDREN: str = "non_terminal_children"

#: The kill-switch key (settings registry). EMISSION ONLY: it gates the
#: notice ENQUEUE and nothing else — never detection, classification,
#: the S1 fire, the S2 arm, the S3 park predicate, the healer, or the
#: report lane. Default: NOTICES ON (absence of the key = on).
WEDGE_NOTICE_EMISSION_KEY: str = "wedge_notice_emission_enabled"


def _format_age_human(age_seconds: float) -> str:
    """Format an age-in-seconds float as a short, human-friendly string.

    Examples::

        >>> _format_age_human(45.0)
        '45s'
        >>> _format_age_human(125.0)
        '2m'
        >>> _format_age_human(3725.0)
        '1h2m'
        >>> _format_age_human(90061.0)
        '25h1m'
    """
    seconds = int(age_seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h{minutes}m"


def _build_hang_notice(
    parent_id: str,
    hung_children: list[tuple[str, float]],
    *,
    hang_threshold_seconds: int,
) -> str:
    """Build the directive hang-guide notice for ``parent_id``.

    The notice lists the hanging children, how long each has been
    stuck, and the four-step playbook. Kept terse and directive per
    design — the parent's LLM should be able to consume this in
    one pass and pick a remediation.

    Args:
        parent_id: The parent instance ID (used only for context in
            the leading line — readers can correlate with their own
            instance-id surface).
        hung_children: List of ``(child_id, age_seconds)`` tuples.
            Pre-sorted by the repository helper, oldest first.
        hang_threshold_seconds: The threshold the children crossed.
            Surfaced in the notice so a reader can verify the
            watchdog's verdict without consulting config.
    """
    threshold_human = _format_age_human(float(hang_threshold_seconds))
    lines: list[str] = [
        "[system:watchdog] Hang notice — your WAITING_CHILDREN "
        f"turn is blocking on {len(hung_children)} non-terminal "
        f"child instance(s) whose last activity exceeds "
        f"{threshold_human}:",
    ]
    for child_id, age in hung_children:
        age_human = _format_age_human(age)
        short = child_id[:8] if len(child_id) > 8 else child_id
        lines.append(f"  - {short}... stuck for {age_human}")
    lines.append("")
    lines.append("Recommended playbook (in order):")
    lines.append(
        "  1. Inspect via the `subtree_messages` tool to see what "
        "the child has reported so far."
    )
    lines.append(
        "  2. If the child is in a failed/terminal state and you "
        "want to revive it, send a `send_message` (or `job_continue` "
        "on the FAILED branch) ONCE — the agent-tool revive-once "
        "guard makes a 2nd revive auto-refused. Use it deliberately."
    )
    lines.append(
        "  3. If a revive is undesirable or the guard has already "
        "fired, spawn a replacement child."
    )
    lines.append(
        "  4. If the hang repeats, escalate to the user (the "
        "watchdog will keep observing until the episode ends)."
    )
    lines.append("")
    lines.append(
        "This notice fires once per (parent, child) hang episode. "
        "It will re-fire if the child resumes non-terminal activity "
        "without reaching a terminal status, or after your next "
        "WAITING_CHILDREN stay."
    )
    return "\n".join(lines)


def _build_escalation_notice(
    parent_id: str,
    child_id: str,
    nudge_count: int,
    *,
    hang_threshold_seconds: int,
) -> str:
    """Build the B3 escalation notice for a stuck (parent, child) pair.

    The escalation is a more urgent cousin of the hang notice. It
    fires after the watchdog has nudged the same pair
    ``escalation_nudge_count`` times without resolution. The notice
    tells the parent that the persistent hang has crossed the
    escalation threshold and recommends the
    replace-the-stuck-child path (a fresh child with a new
    identity can complete what the wedged child could not).

    Kept terse and directive per the hang-notice contract.

    Args:
        parent_id: The parent instance ID.
        child_id: The hung child instance ID.
        nudge_count: Total nudges fired for this pair (must be >=
            ``escalation_nudge_count`` to make the notice meaningful).
        hang_threshold_seconds: The hang threshold (for context).
    """
    parent_short = parent_id[:8] if len(parent_id) > 8 else parent_id
    child_short = child_id[:8] if len(child_id) > 8 else child_id
    threshold_human = _format_age_human(float(hang_threshold_seconds))
    lines: list[str] = [
        f"[system:watchdog:escalation] Escalation — your parked "
        f"child {child_short}... has been non-terminal for "
        f"{nudge_count} watchdog nudges in a row (threshold: "
        f"{threshold_human}). The previous hang notice has not "
        f"produced a resolution.",
        "",
        "Recommended playbook (urgent):",
        "  1. The previous hang notice's revive / spawn-replacement "
        "steps have not been acted on. Spawn a fresh child "
        "(different agent_id or message content) to take over the "
        "stuck work — the wedged child will be silently replaced.",
        "  2. If a replacement is undesirable, terminate the wedged "
        "child explicitly (it has been idle past the threshold; "
        "operator action is the cleanest path).",
        "  3. If the parent itself cannot make progress without this "
        "child, escalate to the user — the watchdog will keep "
        "observing until the episode ends.",
        "",
        "This escalation fires ONCE per hang episode. The next "
        "B3 release may fire if the pair remains stuck past the "
        "release threshold.",
    ]
    return "\n".join(lines)


def _build_release_notice(
    parent_id: str,
    child_id: str,
    nudge_count: int,
    *,
    hang_threshold_seconds: int,
) -> str:
    """Build the B3 release notice for a stuck (parent, child) pair.

    The release is a SYNTHESIZED wake that tells the parent to
    proceed without the hung child. It fires after
    ``release_after_nudge_count`` nudges without resolution —
    the parent has been parked long enough that infinite silent
    park is itself the bug. The release wakes the parent (WC→RUNNING
    flip via ``enqueue_message``) and instructs it to either
    spawn a replacement OR proceed with the partial tree.

    The release is best-effort: if the parent has a genuine
    blocker (e.g., the stuck child carries state only it has),
    the parent's LLM is the right place to decide — the release
    just breaks the silent-park cycle. The notice carries enough
    context for the parent to make that decision.

    Kept terse and directive per the hang-notice contract.

    Args:
        parent_id: The parent instance ID.
        child_id: The hung child instance ID.
        nudge_count: Total nudges fired for this pair (must be >=
            ``release_after_nudge_count``).
        hang_threshold_seconds: The hang threshold (for context).
    """
    parent_short = parent_id[:8] if len(parent_id) > 8 else parent_id
    child_short = child_id[:8] if len(child_id) > 8 else child_id
    threshold_human = _format_age_human(float(hang_threshold_seconds))
    lines: list[str] = [
        f"[system:watchdog:release] Release — your parked child "
        f"{child_short}... has been non-terminal for "
        f"{nudge_count} watchdog nudges in a row. You are being "
        f"RELEASED from WAITING_CHILDREN to proceed.",
        "",
        "This is an automated B3 release (see watchdog B3 doc). The "
        "watchdog has waited long enough that infinite silent park "
        "is itself the bug. You MUST take one of these paths on "
        "your next turn:",
        "  1. Spawn a fresh child to take over the wedged work "
        "(preferred). The wedged child's state is preserved; the "
        "replacement can consult it via subtree_messages.",
        "  2. Proceed with the partial tree (skip the wedged child). "
        "Note any work the wedged child owed the parent in your "
        "response so the operator can decide whether to manually "
        "retry.",
        "  3. Terminate and respawn yourself if you cannot make "
        "progress without this child. The watchdog has given up; "
        "operator action is the next step.",
        "",
        "The release fires ONCE per hang episode. Do not block on "
        f"the wedged child again. (Threshold was {threshold_human}.)",
    ]
    return "\n".join(lines)


def _build_wedge_notice(
    parent_id: str,
    *,
    reason_codes: list[str] | None = None,
    subject_child_ids: list[str] | None = None,
    queued_report_id: str | None = None,
    non_terminal_child_count: int | None = None,
) -> str:
    """Build the directive wedge-notice for ``parent_id``.

    **S4 (wake-path-carrier-fix, phase 4)** — the notice is COMPOSED
    from the reason codes, never from a template that asserts an
    unread queue state.

    The old template claimed, unconditionally, "A PROCESS_REPORT
    message is in your queue but no worker is scheduled to deliver
    it." — a claim the watchdog had NO evidence for (it read no bus
    rows at all), so it was FALSE in 100% of the notices ever minted.
    S4 makes every line a row-checked fact:

    * ``pending_watchers`` — N PENDING watchers that can no longer
      fire (the S4 subject), with the watched children cited;
    * ``no_live_carrier`` — no live PROCESS_REPORT carrier task;
    * ``queued_report`` — a ``completion_report`` row EXISTS and has
      not completed (the row id is CITED — the claim is only made
      when the row check holds);
    * ``non_terminal_children`` — M children are still non-terminal.

    A code is rendered ONLY when it is in ``reason_codes`` — the
    caller computes those from the row checks, so this builder can
    never assert a state that was not observed.

    Args:
        parent_id: The parent instance ID (used only for context in
            the leading line).
        reason_codes: The codes whose checks held (see the module
            constants). Unknown codes are ignored.
        subject_child_ids: The watched child ids of the unfulfillable
            watchers (for the ``pending_watchers`` line).
        queued_report_id: The ``message_queue`` row id of the queued
            completion report (for the ``queued_report`` line).
        non_terminal_child_count: M — the non-terminal child count
            (for the ``non_terminal_children`` line). ``int | None``:
            ``None`` means the count is UNKNOWN (the read failed, or
            it returned an empty set while the children gate said
            children exist) and renders "at least one child (the exact
            count is unknown)" — never a fabricated 0 (B4(1), fold-in
            #8). The DEFAULT is ``None`` (unknown), not 0.

    Returns:
        The notice text.
    """
    codes = set(reason_codes or [])
    short = parent_id[:8] if len(parent_id) > 8 else parent_id
    lines: list[str] = [
        f"[system:watchdog:wedge] Wedge notice — your "
        f"WAITING_CHILDREN turn is parked ({short}...).",
        "",
    ]
    if codes:
        lines.append(
            "Reason codes: " + ", ".join(sorted(codes)) + "."
        )
    else:
        lines.append("Reason codes: (none observed).")
    lines.append("")
    lines.append("Findings (each one checked against a row):")
    if WEDGE_REASON_PENDING_WATCHERS in codes:
        children = [str(c)[:8] for c in (subject_child_ids or [])]
        child_text = ", ".join(children) if children else "unknown"
        lines.append(
            f"  - {WEDGE_REASON_PENDING_WATCHERS}: "
            f"{len(subject_child_ids or [])} pending dependency "
            f"watchers (watched children: {child_text}) that can no "
            f"longer fire."
        )
    if WEDGE_REASON_NO_LIVE_CARRIER in codes:
        lines.append(
            f"  - {WEDGE_REASON_NO_LIVE_CARRIER}: no live "
            f"PROCESS_REPORT carrier task is scheduled for this turn."
        )
    if WEDGE_REASON_QUEUED_REPORT in codes:
        lines.append(
            f"  - {WEDGE_REASON_QUEUED_REPORT}: a completion_report "
            f"message (id={queued_report_id or 'unknown'}) is queued "
            f"and no carrier is scheduled."
        )
    if WEDGE_REASON_NON_TERMINAL_CHILDREN in codes:
        if non_terminal_child_count is None:
            # B4(1)/#8: an UNKNOWN count renders "at least one child"
            # — the gate said children exist, so 0 would be a
            # fabricated lie in a human-facing notice.
            count_text = "at least one child (the exact count is unknown)"
        else:
            count_text = str(non_terminal_child_count)
        lines.append(
            f"  - {WEDGE_REASON_NON_TERMINAL_CHILDREN}: "
            f"{count_text} children are still "
            f"non-terminal."
        )
    lines.append("")
    lines.append("Recommended playbook (in order):")
    lines.append(
        "  1. The reconciler should self-heal on its next sweep — "
        "if this notice repeats, check the manager logs for "
        "sub-shape (c, c_revival) lines."
    )
    lines.append(
        "  2. If the wedge persists, call `send_message` to "
        "yourself (any message) — the resulting PROCESS_MESSAGE "
        "task will wake the parked turn."
    )
    if WEDGE_REASON_NON_TERMINAL_CHILDREN in codes:
        # Terminate-and-respawn would ORPHAN the live children — the
        # code tells the operator they are still running.
        lines.append(
            "  3. Children are still running — do NOT terminate and "
            "re-spawn: let them report first."
        )
    else:
        lines.append(
            "  3. As a last resort, terminate and re-spawn — this "
            "rebuilds the carrier surface from scratch."
        )
    return "\n".join(lines)


class WaitingChildrenWatchdog:
    """Detect parents stuck in ``WAITING_CHILDREN`` and nudge them.

    The watchdog is a thin coordinator: it owns the cadence, the
    cooldown set, the per-parent error isolation, and the notice
    construction. The repo helpers own the SQL-side age computation
    and the status enumeration.

    The class is constructable directly with injected collaborators
    so tests can wire mocks without spinning up an ``InstanceManager``
    or a real DB.

    Args:
        instance_repository: Repo with ``list_waiting_children_parents``,
            ``list_hung_children_for_parent``, and
            ``list_terminal_instance_ids``.
        manager: Object exposing
            ``await enqueue_message(instance_id, message, source=...,
            priority=..., metadata=...)``. In production this is the
            ``InstanceManager`` (whose ``enqueue_message`` is the
            internal-only no-JobItem wake path); in tests it is any
            object implementing the same surface — the acceptance
            test wires the REAL ``InstanceMessagingService`` here so
            the wake path (MessageQueue + Task rows, WC→RUNNING flip,
            ``notify_work``) is exercised, not mocked.
        enabled: Master switch. When False, ``run_once`` and the loop
            are no-ops; the lifespan skips task creation entirely.
        interval_seconds: Seconds between scans. Default 3600 = 1h.
        hang_threshold_seconds: Strictly-greater-than threshold for
            hang detection. Default 3600 = 1h.
        escalation_nudge_count: Number of nudges before an
            escalation notice fires. Default 3.
        release_after_nudge_count: Number of nudges before the
            watchdog synthesizes a release (enqueues a wake turn with
            a directive to proceed without the hung children, since
            infinite silent park is a bug). Default 5.

    B3 RESOLVED 2026-09-11 (Watchdog escalation): the design freedom
    in the invariant is exercised via ``escalation_nudge_count`` and
    ``release_after_nudge_count``. No env flags (HARD POLICY).

    Anti-spam invariant: ``_notified`` (a ``set`` of
    ``(parent_id, child_id)`` tuples) records every pair currently in
    a notified episode. On the next tick, a pair is re-notified ONLY
    if it was previously cleared — by the scan-driven episode-end
    sweep (child absent from a successful parent scan: terminal,
    paused, or resumed activity), the parent-left-WC purge, or the
    child-terminal purge (both DB-backed and independent of per-parent
    scan success; see the module docstring's "Episode boundaries").
    A daemon restart resets the set — documented limitation.
    """

    def __init__(
        self,
        instance_repository: SQLModelInstanceRepository,
        manager: Any,
        *,
        enabled: bool = True,
        interval_seconds: int = 3600,
        hang_threshold_seconds: int = 3600,
        task_repository: Any | None = None,
        escalation_nudge_count: int = 3,
        release_after_nudge_count: int = 5,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(
                f"interval_seconds must be > 0; got {interval_seconds!r}"
            )
        if hang_threshold_seconds < 0:
            raise ValueError(
                "hang_threshold_seconds must be >= 0; got "
                f"{hang_threshold_seconds!r}"
            )
        if escalation_nudge_count < 1:
            raise ValueError(
                f"escalation_nudge_count must be >= 1; got "
                f"{escalation_nudge_count!r}"
            )
        if release_after_nudge_count <= escalation_nudge_count:
            raise ValueError(
                f"release_after_nudge_count must be > "
                f"escalation_nudge_count ({escalation_nudge_count}); "
                f"got {release_after_nudge_count!r}"
            )

        self._repo = instance_repository
        self._manager = manager
        self._enabled = bool(enabled)
        self._interval_seconds = int(interval_seconds)
        self._hang_threshold_seconds = int(hang_threshold_seconds)
        # B3: escalation policy. ``escalation_nudge_count`` triggers
        # an escalation notice; ``release_after_nudge_count``
        # synthesizes a release turn. The values are passed through
        # from the config layer (no env flags per HARD POLICY).
        self._escalation_nudge_count = int(escalation_nudge_count)
        self._release_after_nudge_count = int(release_after_nudge_count)
        # Wedge-fix backstop: optional task repository for the
        # live-carrier presence query. When ``None``, the watchdog
        # falls back to ``self._manager._task_repo`` at scan time —
        # mirrors the ``getattr(manager, "_task_repo", None)`` pattern
        # used in ``daemon/api.py``. Tests inject a real repo;
        # production relies on the manager surface.
        self._task_repository = task_repository

        # ``set`` of ``(parent_id, child_id)`` tuples in a currently
        # notified episode. Cleared implicitly on each tick by
        # re-deriving from the SQL result. See ``run_once`` for the
        # episode-end logic.
        self._notified: set[tuple[str, str]] = set()

        # Wedge-episode map: parent id → the REASON-CODE
        # FINGERPRINT (the sorted tuple of the codes whose row-checks
        # held when the notice was emitted). INDEPENDENT of
        # ``_notified`` so a hang notice does not preempt a wedge
        # notice (and vice versa) — they target different
        # signatures. NOT a timestamp: the TTL cooldown is dropped
        # (S4) — an episode ends on the ROW-CHECK (the S4 subject
        # empty), never on a timer and never on WC membership (the
        # notice's own enqueue flips WC→RUNNING).
        self._wedge_notified: dict[str, tuple[str, ...]] = {}
        # The per-tick memo of the emission kill-switch (ONE settings
        # read per tick; reset at the top of every ``run_once``).
        self._wedge_emission_cache: bool | None = None

        # Wedge-pass counters (observability — separate from the
        # ``run_once`` stats dict so existing 4-key tests stay
        # green). Incremented per tick; reset only on daemon
        # restart. Exposed via properties below.
        self._wedge_parents_scanned_total: int = 0
        self._wedge_notices_enqueued_total: int = 0

        # B3: per-(parent, child) episode nudge counter (lifetime
        # within this process). Tracks how many nudges the watchdog
        # has fired for each pair so the escalation and release
        # thresholds fire on the right tick. The counter resets
        # only when the pair leaves the notified set (episode ends).
        # Daemon restart resets all counters — documented limitation
        # (consistent with the cooldown-set semantics).
        self._nudge_counts: dict[tuple[str, str], int] = {}

        # B3: escalation cooldown — parents that have already
        # received an escalation notice for this episode. Same
        # episode-boundary semantics as ``_notified``.
        self._escalation_notified: set[tuple[str, str]] = set()

        # B3: release cooldown — parents that have already been
        # synthesized-released for this episode. Same
        # episode-boundary semantics.
        self._release_notified: set[tuple[str, str]] = set()

        # B3: per-tick observability counters — separate from the
        # existing 4-key ``run_once`` stats dict so existing tests
        # stay green. Reset only on daemon restart.
        self._escalation_notices_enqueued_total: int = 0
        self._release_notices_enqueued_total: int = 0

    # ─── Public introspection (tests) ──────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def interval_seconds(self) -> int:
        return self._interval_seconds

    @property
    def hang_threshold_seconds(self) -> int:
        return self._hang_threshold_seconds

    @property
    def notified_episodes(self) -> frozenset[tuple[str, str]]:
        """Read-only view of the in-memory cooldown set.

        Exposed for tests. Production callers should treat the set as
        opaque; the watchdog is the sole writer.
        """
        return frozenset(self._notified)

    @property
    def wedge_episodes(self) -> frozenset[str]:
        """Read-only view of the in-memory wedge-episode cooldown set.

        Exposed for tests. Production callers should treat the set as
        opaque; the watchdog is the sole writer.
        """
        return frozenset(self._wedge_notified)

    @property
    def wedge_parents_scanned(self) -> int:
        """Lifetime count of parents the wedge-pass has scanned.

        Observability counter — separate from the 4-key
        ``run_once`` stats dict (which is reserved for the hang
        pass). Increments per tick; reset only on daemon restart.
        """
        return self._wedge_parents_scanned_total

    @property
    def wedge_notices_enqueued(self) -> int:
        """Lifetime count of wedge notices enqueued via the wake
        path.

        Observability counter — separate from the 4-key
        ``run_once`` stats dict. Increments per tick; reset only on
        daemon restart.
        """
        return self._wedge_notices_enqueued_total

    # ─── B3 escalation / release introspection ──────────────────────

    @property
    def escalation_nudge_count(self) -> int:
        return self._escalation_nudge_count

    @property
    def release_after_nudge_count(self) -> int:
        return self._release_after_nudge_count

    @property
    def escalation_notices_enqueued(self) -> int:
        """Lifetime count of escalation notices enqueued (B3)."""
        return self._escalation_notices_enqueued_total

    @property
    def release_notices_enqueued(self) -> int:
        """Lifetime count of release notices enqueued (B3)."""
        return self._release_notices_enqueued_total

    @property
    def escalation_episodes(self) -> frozenset[tuple[str, str]]:
        """Read-only view of the B3 escalation cooldown set."""
        return frozenset(self._escalation_notified)

    @property
    def release_episodes(self) -> frozenset[tuple[str, str]]:
        """Read-only view of the B3 release cooldown set."""
        return frozenset(self._release_notified)

    def nudge_count_for(self, parent_id: str, child_id: str) -> int:
        """Return the current per-pair nudge count (B3 observability).

        Returns 0 for pairs with no recorded nudges (default).
        """
        return self._nudge_counts.get((parent_id, child_id), 0)

    # ─── Wedge-fix backstop helper ─────────────────────────────────────

    # ─── S4 (wake-path-carrier-fix, phase 4) — the notice surface ────

    def _fetch_wedge_subjects(
        self, parent_ids: list[str]
    ) -> dict[str, tuple[int, list["DependencyWatcher"]]]:
        """The batched SUBJECT read — the watchdog's FIRST bus read.

        Returns ``{parent_id: (total_pending, unfulfillable_rows)}``:
        the S4_NOTICE_SUBJECT (the PENDING watchers that can no longer
        fire) and the total PENDING count (which distinguishes "no
        watchers at all" — the classic wedge — from "watchers, all
        fulfillable" — the legitimately-parked parent, OUT OF SCOPE).

        The bus is not wired in some fixtures → an empty map (the
        classic-wedge shape), which keeps the pre-S4 behaviour for
        those callers.
        """
        from daemon.services.dependency_bus import get_dependency_bus

        bus = get_dependency_bus()
        if bus is None:
            return {}
        return bus.fetch_pending_subject_for_targets_sync(list(parent_ids))

    def _queued_completion_report_id(
        self, parent_id: str
    ) -> tuple[bool, str | None]:
        """The queued ``completion_report`` row check — TRI-STATE.

        The ROW check behind the ``queued_report`` reason code: a
        ``completion_report`` message row EXISTS for this parent and
        has not completed yet. When no such row exists the code MUST
        NOT be emitted and the notice MUST NOT claim one — the old
        template claimed it unconditionally (FALSE in 100% of the
        notices ever minted).

        **R1(6): the read outcome is tri-state** — a swallowed error
        used to be indistinguishable from "no queued report", so the
        code could END an episode on a queue-read blip:

        * ``(True, "mq-...")`` — a queued report EXISTS (the code holds);
        * ``(True, None)`` — the read succeeded, NO queued report (the
          code does NOT hold);
        * ``(False, None)`` — the read FAILED (UNKNOWN — the caller must
          not end an episode on it).
        """
        repo = getattr(self._manager, "_queue_repository", None)
        if repo is None:
            return (True, None)
        try:
            rows = repo.get_by_instance(parent_id)
        except Exception as exc:  # noqa: BLE001 — fail-soft (no claim)
            logger.warning(
                "[Watchdog] _queued_completion_report_id failed for "
                "%s...: %s: %s",
                parent_id[:8],
                type(exc).__name__,
                exc,
            )
            return (False, None)
        if not isinstance(rows, (list, tuple)):
            # An async double (AsyncMock) hands back a coroutine — a
            # sync helper must not await it, and a coroutine is NOT
            # evidence. Close it so it does not warn.
            if hasattr(rows, "close"):
                try:
                    rows.close()
                except Exception:  # noqa: BLE001
                    pass
            return (False, None)
        for row in rows:
            if getattr(row, "type", None) != "completion_report":
                continue
            if getattr(row, "status", None) in ("completed", "failed"):
                continue
            # EXCLUDE the watchdog's OWN notices — belt-and-braces
            # SECOND LAYER. The notices are NOT ``completion_report``
            # rows: ``enqueue_message`` derives ``type="system"`` for
            # a ``system:`` source (``instance_messaging.
            # _derive_internal_message_type``), so the ``type`` filter
            # above already excludes them. The metadata/source filters
            # below are a REDUNDANT guard, kept for the case where a
            # notice ever lands typed as a completion report: without
            # it the notice would observe ITSELF on the next tick —
            # the ``queued_report`` code would appear, the episode
            # fingerprint would change, and the pass would notify
            # again (an endless self-notice loop).
            _mm = getattr(row, "message_metadata", None)
            meta = _mm if isinstance(_mm, dict) else {}
            if meta.get("wedge_notice") or meta.get("watchdog_notice"):
                continue
            if getattr(row, "source", None) in (
                "system:watchdog:wedge",
                "system:watchdog",
            ):
                continue
            return (True, getattr(row, "message_id", None))
        return (True, None)

    def _count_non_terminal_children(self, parent_id: str) -> int | None:
        """M — the parent's non-terminal child count (the
        ``non_terminal_children`` reason code's number).

        B4(1): a FAILED read returns ``None`` — NEVER a fabricated 0.
        The count feeds a human-facing notice; "0 children" from a
        failed read is a lie that would steer the operator toward
        terminate-and-respawn against live children.
        """
        try:
            child_ids = self._repo.list_child_ids_permanent(parent_id)
        except Exception as exc:  # noqa: BLE001 — fail-soft
            logger.warning(
                "[Watchdog] _count_non_terminal_children list failed for "
                "%s...: %s: %s",
                parent_id[:8],
                type(exc).__name__,
                exc,
            )
            return None
        if not child_ids:
            return 0
        try:
            statuses = self._repo.get_statuses(list(child_ids))
        except Exception as exc:  # noqa: BLE001 — fail-soft
            logger.warning(
                "[Watchdog] _count_non_terminal_children statuses failed "
                "for %s...: %s: %s",
                parent_id[:8],
                type(exc).__name__,
                exc,
            )
            return None
        return sum(
            1
            for child_id in child_ids
            if statuses.get(child_id) not in TERMINAL_INSTANCE_STATUSES
        )

    def _wedge_code_still_holds(
        self,
        parent_id: str,
        code: str,
        *,
        subjects: dict[str, tuple[int, list]],
        parents_with_children: set[str],
        subjects_read_failed: bool = False,
    ) -> bool | None:
        """B2/R1: does ONE stamped reason code still hold for
        ``parent_id``?

        Evaluated against the CURRENT row-checks — for every open
        episode, including parents that have left the WC scan set.

        **R1: TRI-STATE.** ``None`` means UNKNOWN — the read behind
        that code FAILED. An unknown must NEVER be read as "the code
        stopped holding": the end-evaluation leaves the episode
        UNTOUCHED on any ``None`` (a read failure may not end an
        episode — it may only postpone the verdict).
        """
        if code == WEDGE_REASON_PENDING_WATCHERS:
            if subjects_read_failed:
                return None
            _total, _rows = subjects.get(parent_id, (0, []))
            return bool(_rows)
        if code == WEDGE_REASON_NO_LIVE_CARRIER:
            _carrier = self._has_live_carrier_task(parent_id)
            if _carrier is None:
                return None
            return not _carrier
        if code == WEDGE_REASON_QUEUED_REPORT:
            _read_ok, _msg_id = self._queued_completion_report_id(parent_id)
            if not _read_ok:
                return None
            return _msg_id is not None
        if code == WEDGE_REASON_NON_TERMINAL_CHILDREN:
            # The children gate's own fallback is fail-CLOSED (a failed
            # read marks every candidate as HAVING children), so this
            # leg is never UNKNOWN.
            return parent_id in parents_with_children
        return False

    def _wedge_notice_emission_enabled(self) -> bool:
        """The kill-switch read — EMISSION ONLY, read PER TICK.

        The registry key alone is VOID: ``self._enabled`` is a
        BOOT-time read, so the switch is read from the settings
        service on every tick (the value is memoised per tick — ONE
        DB read per tick, not one per parent).

        Default: NOTICES ON (the absence of the key means ON). The
        switch gates the notice ENQUEUE and NOTHING else — never
        detection, classification, the S1 fire, the S2 arm, the S3
        park predicate, the healer or the report lane.
        """
        cached = getattr(self, "_wedge_emission_cache", None)
        if cached is not None:
            return cached
        enabled = True
        svc = getattr(self._manager, "_settings_service", None)
        if svc is not None:
            try:
                overrides = svc.get_overrides()
            except Exception:  # noqa: BLE001 — fail-OPEN (notices on)
                overrides = None
            if isinstance(overrides, dict):
                value = overrides.get(WEDGE_NOTICE_EMISSION_KEY)
                if value is not None:
                    enabled = bool(value)
            elif overrides is not None and hasattr(overrides, "close"):
                # An async service double — never awaited here; close
                # the coroutine so it does not warn.
                try:
                    overrides.close()
                except Exception:  # noqa: BLE001
                    pass
        self._wedge_emission_cache = enabled
        return enabled

    def _has_live_carrier_task(self, instance_id: str) -> bool | None:
        """Return True iff ``instance_id`` has any live
        PROCESS_REPORT Task (PENDING or RUNNING).

        Used by the wedge-pass to detect the wedge condition: a
        ``WAITING_CHILDREN`` parent with zero non-terminal children
        AND zero live carrier is wedged — the report message is in
        the queue but no worker is scheduled to deliver it.

        Resolution order:

        1. ``self._task_repository`` (injected via constructor) —
           tests wire this directly. Preferred — the helper is
           sync, cheap, and tests can pin the exact query.
        2. ``self._manager._task_repo`` — production wiring per
           ``daemon/api.py``. Falls back when the constructor
           argument was omitted.
        3. ``False`` (silent no-op) — when neither path has a real
           repo (test fixtures using AsyncMock as the manager). The
           wedge pass becomes a no-op for those tests so the
           existing hang-detection assertions are not disturbed.

        **R1(3): the outcome is TRI-STATE** — a repo call that RAISES
        returns ``None`` (UNKNOWN), not ``False``: an unknown carrier
        state must not be read as "no carrier" (that would mint a
        false wedge notice) NOR end an open episode.

        Args:
            instance_id: The parent instance ID whose live-carrier
                presence we are checking.

        Returns:
            ``True`` iff a live PROCESS_REPORT Task exists for this
            instance_id; ``False`` if there is none (no task, all
            terminal, or no usable repo wired); ``None`` if the read
            FAILED (unknown — callers must not act on it).
        """
        import inspect as _inspect
        repo = self._task_repository
        if repo is None:
            repo = getattr(self._manager, "_task_repo", None)
        if repo is None:
            # No repo wired — silent no-op. The wedge pass silently
            # treats every parent as having a live carrier so the
            # backstop does NOT fire (the production path always
            # wires the repo via ``daemon/api.py``).
            return True
        # The repo helper is sync; the calling context is the
        # watchdog loop on the asyncio event loop thread, but
        # the SQLAlchemy session uses a thread-local connection
        # that does not require async — short-lived read, no
        # transaction held across awaits. A clean ``hasattr``
        # probe (no mock-shaped try/except) gates the call —
        # production repos always implement the helper.
        if not hasattr(
            repo, "list_live_process_report_carriers_for_instance"
        ):
            # Injected repo does not implement the helper —
            # silent no-op (same as the no-repo case).
            return True
        try:
            live = repo.list_live_process_report_carriers_for_instance(
                instance_id
            )
        except Exception as exc:  # noqa: BLE001 — R1(3): UNKNOWN
            logger.warning(
                "[Watchdog] live-carrier probe failed for %s...: "
                "%s: %s",
                instance_id[:8],
                type(exc).__name__,
                exc,
            )
            return None
        # AsyncMock test fixtures return coroutines from method
        # calls — detect and treat as having-a-carrier (True)
        # so the wedge pass stays silent for tests that mock
        # the manager without wiring the repo. New tests wire
        # the repo explicitly via the constructor and get the
        # real behavior.
        if _inspect.iscoroutine(live):
            return True
        return len(live) > 0

    # ─── W-B liveness-gate helper ────────────────────────────────────

    def _has_recent_heartbeat(
        self, child_id: str, threshold_seconds: int
    ) -> bool:
        """Return True iff ``child_id`` has any task row with a heartbeat
        fresher than ``threshold_seconds`` ago.

        **W-B liveness gate** (feature/fix-wc-wake-resilience, 2026-09-11).
        The pre-W-B B3 escalation / release decision was gated only on
        the freshness proxy ``instances.last_activity_at`` — but that
        column is NOT refreshed by in-flight tool calls, LLM turns, or
        background subprocess activity. Only ``task.last_heartbeat_at``
        (which the worker pool's heartbeat thread updates on a bounded
        interval) moves during a running turn. A genuinely-working-but-
        DB-silent child turn can therefore have a very stale
        ``last_activity_at`` while its task row carries a freshly-
        beating heartbeat — exactly the bug class the W-B review
        flagged: "the B3 claim that forced release 'cannot fire while
        a child is genuinely still working' is overstated — it holds
        only under the freshness proxy, not true liveness."

        W-B closes the gap by routing escalation AND release through
        this helper. The signal is "any task row for the child with
        ``last_heartbeat_at`` newer than the threshold". If true, the
        child is by construction genuinely working — the watchdog
        suppresses escalation AND release for this pair.

        **Failure modes** (recorded for the next operator reading):

        * *False negative* (we say "dead" when the child is alive):
          happens when the child's most-recent heartbeat is older
          than ``threshold_seconds`` but the child is actually
          grinding through a long-running tool call. Risk = spurious
          release. Mitigated by using the existing
          ``hang_threshold_seconds`` (default 3600s = 1h) — the same
          window that already classifies the child as "hung" for the
          parent's view. A child whose heartbeat is >1h old is, by
          the watchdog's own definition, hung.
        * *False positive* (we say "alive" when actually dead):
          happens when an OLD heartbeat row exists but the worker is
          actually wedged. This is the same operational surface as
          the existing ``list_hung_children_for_parent`` helper — no
          regression.

        Net: the gate only changes behavior on the borderline cases
        where the freshness proxy says "hung" but the heartbeat says
        "alive". Those cases are exactly the ones the W-B fix targets.

        **Wiring**: backed by
        :meth:`daemon.repositories.task.repository.TaskRepository.child_has_recent_heartbeat`.
        The helper is sync (one indexed probe on ``instance_id`` +
        a covering predicate on ``last_heartbeat_at``); cheap enough
        to call per-(parent, child) pair inside the watchdog's
        per-tick scan.

        **Strictly opt-in via constructor**: the helper consults ONLY
        ``self._task_repository`` (the constructor-injected
        ``task_repository=...`` arg). It does NOT fall back to
        ``self._manager._task_repo`` because the B3 test fixtures
        (and any future MagicMock-based test that does not wire the
        helper explicitly) rely on the manager's MagicMock auto-attrs
        — a MagicMock ``child_has_recent_heartbeat`` returns a truthy
        MagicMock, which would wrongly suppress escalation. By
        gating the helper on a real, constructor-injected repo, the
        gate is:

        * *active in production* — ``daemon/api.py``'s lifespan
          ``WaitingChildrenWatchdog(...)`` construction wires the
          manager's real ``TaskRepository`` as ``task_repository=``
          on every watchdog construction.
        * *opt-in for tests* — tests that want to assert the gate
          behavior must wire the helper explicitly (see the W-B
          test surface for examples).
        * *permissive when unwired* — any caller that does not
          inject ``task_repository`` keeps the pre-W-B B3 behavior
          unchanged (escalation/release fire on the nudge-count
          thresholds with no liveness gate).
        * *explicit fail-closed on DB-error* (B3 polish,
          cycle-2 2026-09-11): the previous behavior caught the
          probe's DB error ONLY via the watchdog's broad per-parent
          ``except Exception as exc`` clause (the
          ``WaitingChildrenWatchdog.run_once`` body — search for
          ``except Exception as exc`` near ``stats["errors"]`` to
          locate the exact handler) — fail-closed by accident,
          not by design. Any tightening of that broad catch (the
          vgap test fails loudly if that ever happens) would
          re-introduce the wedge. This helper now catches the
          probe's exceptions itself and returns ``True`` (assume
          alive — suppress escalation/release for this tick).
          Fail-closed is by contract, not by incidental broad catch.

        Args:
            child_id: The child instance_id whose recent-heartbeat
                presence we are checking. This is the child leg of
                the (parent, child) pair — the watchdog is asking
                "is this child actually still working?".
            threshold_seconds: Maximum age (in seconds) of the most
                recent heartbeat that still counts as "live".
                Typically the watchdog's
                ``hang_threshold_seconds`` (default 3600s). Same
                threshold the freshness proxy uses — keeps the two
                predicates on the same time axis.

        Returns:
            True iff at least one task row for ``child_id`` has a
            ``last_heartbeat_at`` newer than ``threshold_seconds``
            ago. False otherwise (no task rows, all tasks have
            NULL heartbeat, no repo injected, the helper is not
            implemented, or the newest heartbeat is older than the
            threshold). False means "no recent liveness evidence"
            — the watchdog is free to escalate or release.
        """
        repo = self._task_repository
        if repo is None:
            # Strictly opt-in: when no ``task_repository`` was
            # injected via the constructor, the gate is permissive
            # (returns False — "no liveness evidence"). This
            # preserves the pre-W-B B3 behavior for callers that
            # do not wire the helper. Production wiring always
            # passes the manager's real ``TaskRepository`` so the
            # gate is active in prod.
            return False
        method = getattr(repo, "child_has_recent_heartbeat", None)
        if method is None or not callable(method):
            # Test fixtures using AsyncMock / MagicMock without
            # the helper — silent permissive fallback (same shape
            # as ``_has_live_carrier_task`` above). The production
            # TaskRepository always implements the helper.
            return False
        # B3 polish (cycle-2 2026-09-11, W-B review): wrap the
        # sync DB probe in an explicit try/except returning True
        # on any error — assume-alive is the fail-CLOSED direction
        # for the B3 release/escalation decision (a release-notice
        # row landing in MessageQueue while the DB is unhealthy is
        # a worse outcome than a spurious release suppression).
        # Mirrors the watchdog's broad per-parent ``except
        # Exception`` at :1267+ as the SECOND backstop (in case
        # the helper itself raises in an unexpected path) — the
        # FIRST backstop is this try/except, designed to guarantee
        # the fail-closed direction without relying on the broad
        # catch's incidental coverage.
        try:
            return bool(method(child_id, threshold_seconds))
        except Exception as probe_exc:
            logger.warning(
                f"[Watchdog] _has_recent_heartbeat probe raised "
                f"({probe_exc!r}); failing-CLOSED (assuming "
                f"alive, suppressing B3 escalation/release for "
                f"child={child_id[:8]}...) — fail-closed by design, "
                f"not by incidental broad-catch."
            )
            return True

    # ─── Core scan ──────────────────────────────────────────────────────

    async def run_once(self) -> dict[str, int]:
        """Execute one watchdog scan.

        Returns a small stats dict for observability / tests::

            {
                "parents_scanned": <int>,
                "parents_skipped_paused": <int>,
                "notices_enqueued": <int>,
                "errors": <int>,
            }

        Wedge-pass counters (``wedge_parents_scanned`` and
        ``wedge_notices_enqueued``) are NOT in this dict — they
        live on the watchdog as separate properties
        (:attr:`wedge_parents_scanned` / :attr:`wedge_notices_enqueued`)
        so existing tests that pin the 4-key contract stay green.
        Wedge stats are observability-only — the canonical pass
        metrics are the hang pass.

        The method is idempotent within a single tick — calling it
        twice in quick succession will not double-notify because
        the cooldown set is updated atomically per parent before the
        next parent is processed.

        Per-parent error isolation: a single parent's scan that
        raises is caught, logged, and counted in ``errors``; the
        next parent is processed. The whole ``run_once`` does NOT
        abort on a per-parent failure.
        """
        # S4: the emission kill-switch is read PER TICK — clear
        # the per-tick memo (the registry key alone is VOID: the
        # watchdog's ``_enabled`` is a boot-time read).
        self._wedge_emission_cache = None

        stats = {
            "parents_scanned": 0,
            "parents_skipped_paused": 0,
            "notices_enqueued": 0,
            "errors": 0,
        }
        if not self._enabled:
            return stats

        # Step 1: enumerate parents. A repo blip is logged at ERROR
        # and surfaced as a single ``errors++`` so the daemon does
        # not crash; the next tick will retry. The cooldown purges
        # below are ALSO skipped — without a fresh enumeration we
        # have no signal to conclude any episode ended.
        try:
            parent_ids = self._repo.list_waiting_children_parents()
        except Exception as exc:
            logger.error(
                f"[Watchdog] Failed to enumerate WAITING_CHILDREN "
                f"parents: {exc}",
                exc_info=True,
            )
            stats["errors"] += 1
            return stats

        # Step 2: per-parent hang detection + notice enqueue.
        # Re-derive the cooldown set from the SQL result: a
        # (parent, child) that is in ``_notified`` but absent from
        # the new SQL result has either reached a terminal status
        # OR become paused — i.e., the episode has ENDED, and the
        # watchdog may re-notify on a subsequent live episode. We
        # implement that by removing absent pairs from the set at
        # the START of each parent scan; pairs that remain in the
        # SQL result are NOT re-notified because they're already
        # in the set.
        currently_hung_pairs: set[tuple[str, str]] = set()
        # Parents whose scan completed cleanly this tick
        # (``get(P)`` returned non-None, status was not PAUSED,
        # and ``list_hung_children_for_parent`` did not raise).
        # Only pairs whose parent is in this set participate in
        # the episode-end sweep below. Pairs for parents whose
        # scan failed or hit a transient error stay in
        # ``_notified`` unchanged — a failed scan MUST NOT
        # silently clear the cooldown, which would otherwise
        # violate the anti-spam invariant (the next healthy tick
        # would re-notify without any real episode change).
        # Documented invariant — pinned by
        # ``TestEpisodeEndScanErrorPreservesCooldown``.
        scanned_ok: set[str] = set()

        for parent_id in parent_ids:
            stats["parents_scanned"] += 1
            try:
                # Step 2a: per-parent guard against PAUSED parents.
                # A PAUSED target gets no notice (hang data would be
                # stale by resume time — see the branch below); we
                # skip the DB scan too so we do not waste the
                # threshold-eval query on a parent that will not be
                # notified. Read via the facade (per D14 layering)
                # using a lightweight status-only fetch — the
                # manager exposes ``get_instance`` via the repo.
                parent = self._repo.get(parent_id)
                if parent is None:
                    # Parent disappeared between enumeration and
                    # per-parent scan (terminal transition + delete).
                    # Skip silently; nothing to notify. The parent
                    # is NOT added to ``scanned_ok`` so any prior
                    # ``_notified`` entries for it stay untouched —
                    # we cannot conclude the episode ended when the
                    # row is missing; a fresh row with the same id
                    # could in theory reappear.
                    continue
                if parent.status == InstanceStatus.PAUSED.value:
                    stats["parents_skipped_paused"] += 1
                    # Parent is PAUSED. The operator is the canonical
                    # decision-maker during a pause, and a notice
                    # computed from pre-pause hang data would be
                    # stale by the time the instance resumes —
                    # enqueue would merely park a PENDING task that
                    # fires a misleading turn on resume. Do NOT add
                    # to ``scanned_ok``: a pair for this parent in
                    # ``_notified`` stays put, and the parent must
                    # transition back to WAITING_CHILDREN (the
                    # ``list_waiting_children_parents`` filter) for
                    # the next tick to consider it again.
                    continue

                # Step 2b: SQL-side hang detection.
                hung = self._repo.list_hung_children_for_parent(
                    parent_id=parent_id,
                    threshold_seconds=self._hang_threshold_seconds,
                )

                # Record what is currently hung for episode-end
                # detection after the loop.
                for child_id, _age in hung:
                    currently_hung_pairs.add((parent_id, child_id))

                if not hung:
                    # No hung children this tick. We still mark the
                    # parent ``scanned_ok`` so any prior pair in
                    # ``_notified`` for this parent is allowed to
                    # clear at episode-end — the child has reached
                    # terminal or paused and the episode truly ended.
                    scanned_ok.add(parent_id)
                    continue

                # Step 2c: anti-spam filter. Only notify (parent,
                # child) pairs that are NOT already in the cooldown
                # set from a previous tick — a pair that persists
                # in both the SQL result and the set is in a
                # continuing episode and gets ONE notice.
                new_pairs = [
                    (child_id, age)
                    for child_id, age in hung
                    if (parent_id, child_id) not in self._notified
                ]

                # B3 RESOLVED 2026-09-11: escalation policy. After
                # the first nudge, bump the per-pair nudge counter
                # and check the escalation / release thresholds.
                # Runs BEFORE the ``if not new_pairs`` early-out so
                # persistent pairs (already in ``_notified`` from a
                # prior tick) still get their nudge count bumped —
                # otherwise a base notice on tick 1 + a stuck pair
                # would never reach the escalation threshold.
                #
                # - ``escalation_nudge_count`` (default 3): fire an
                #   escalation notice (one per episode, gated by
                #   ``_escalation_notified`` cooldown set).
                # - ``release_after_nudge_count`` (default 5):
                #   synthesize a release — enqueue a wake with a
                #   directive to proceed without the hung children
                #   (one per episode, gated by
                #   ``_release_notified`` cooldown set).
                # - Pairs that persist in BOTH the SQL result AND
                #   the cooldown set are in a continuing episode
                #   and their nudge count keeps climbing each tick
                #   until the episode ends (the wedge-pass purge
                #   below).
                # - Episode-end resets the nudge count (the pair
                #   leaves ``_notified`` at episode end → its nudge
                #   count is purged below).
                #
                # Scope: ALL pairs in this tick's ``hung`` list
                # (still currently hung) are counted — both the
                # first-time ``new_pairs`` AND the persistent pairs
                # that already triggered the base notice on a prior
                # tick.
                for child_id, _age in hung:
                    pair = (parent_id, child_id)
                    self._nudge_counts[pair] = (
                        self._nudge_counts.get(pair, 0) + 1
                    )
                    nudge_count = self._nudge_counts[pair]
                    # W-B LIVENESS GATE (feature/fix-wc-wake-resilience,
                    # 2026-09-11). The freshness proxy
                    # (``instances.last_activity_at``) that the B3
                    # pre-W-B code relied on does NOT refresh during
                    # a running turn — only ``task.last_heartbeat_at``
                    # moves. A genuinely-working-but-DB-silent >~6h
                    # child turn would otherwise get its parent
                    # force-released. W-B requires TRUE liveness
                    # evidence absence: a child with a recently-
                    # beating task heartbeat must NOT be eligible for
                    # escalation OR release.
                    #
                    # The gate is Boolean (True = heartbeat fresher
                    # than threshold = suppress both paths). The
                    # helper resolves to True on a wired repo with
                    # task rows carrying a fresh ``last_heartbeat_at``;
                    # False otherwise (no repo wired, no task rows,
                    # all NULL heartbeats, or all heartbeats older
                    # than threshold). The fallback-to-False shape is
                    # intentional: when no liveness evidence is
                    # available the existing B3 behavior is
                    # preserved, not weakened — a release-eligible
                    # pair that has no heartbeat is already in the
                    # "hung by every signal" bucket.
                    #
                    # Same threshold as ``hang_threshold_seconds``:
                    # the heartbeat signal is on the same time axis
                    # as the freshness proxy, so a child whose
                    # heartbeat is fresher than ``hang_threshold``
                    # ago is by construction NOT in the "hung by
                    # threshold" category the freshness proxy said
                    # it was. The two predicates agree on the
                    # borderline case W-B targets.
                    child_has_recent_heartbeat = (
                        self._has_recent_heartbeat(
                            child_id,
                            self._hang_threshold_seconds,
                        )
                    )
                    if child_has_recent_heartbeat:
                        # Child is genuinely working — suppress both
                        # escalation and release for this tick. The
                        # nudge count keeps climbing (so a future
                        # tick that finds the heartbeat silent still
                        # has the count) but the gate re-evaluates
                        # every tick, so a child that subsequently
                        # goes silent will see escalation/release
                        # resume at the next nudge threshold. Logged
                        # at INFO for diagnosability — the operator
                        # following the watchdog's per-tick log sees
                        # why a stuck pair is not escalating.
                        logger.info(
                            f"[Watchdog] (parent={parent_id[:8]}..., "
                            f"child={child_id[:8]}...) "
                            f"nudge={nudge_count} suppressed by W-B "
                            f"liveness gate — recent heartbeat "
                            f"detected on child's task rows."
                        )
                        # Known residual (W-B review): a wedged turn
                        # whose child heartbeat keeps beating suppresses
                        # B3 release indefinitely — the heartbeat signal
                        # is independent of worker execution progress,
                        # so a hung-but-heartbeating child stays parked.
                        # The pre-W-B base hang-notice (escalation, not
                        # release) preserves operator visibility into
                        # the wedged state.
                        continue
                    if (
                        nudge_count >= self._release_after_nudge_count
                        and pair not in self._release_notified
                    ):
                        # Release: synthesize a wake turn that
                        # tells the parent to proceed without the
                        # hung children (the same primitive B2
                        # uses for cascade resume). This breaks the
                        # infinite-silent-park cycle — the parent's
                        # next turn sees the release directive and
                        # either spawns a replacement child or
                        # proceeds with the partial tree.
                        release_notice = _build_release_notice(
                            parent_id=parent_id,
                            child_id=child_id,
                            nudge_count=nudge_count,
                            hang_threshold_seconds=self._hang_threshold_seconds,
                        )
                        try:
                            await self._manager.enqueue_message(
                                instance_id=parent_id,
                                message=release_notice,
                                source=WATCHDOG_SOURCE,
                                priority=0,
                                metadata={
                                    "watchdog_release": True,
                                    "released_pair": {
                                        "parent_id": parent_id,
                                        "child_id": child_id,
                                    },
                                    "nudge_count": nudge_count,
                                },
                            )
                            self._release_notified.add(pair)
                            self._release_notices_enqueued_total += 1
                            logger.warning(
                                f"[Watchdog] Release enqueued for "
                                f"parent {parent_id[:8]}... after "
                                f"{nudge_count} nudges — parked "
                                f"child {child_id[:8]}...; releasing "
                                f"the parent to proceed without it."
                            )
                        except Exception as release_exc:
                            # Soft-fail — the release is best-effort.
                            # The escalation notice (below) and the
                            # next tick will retry.
                            logger.warning(
                                f"[Watchdog] release enqueue failed "
                                f"for {pair}: {release_exc!r}"
                            )
                    elif (
                        nudge_count >= self._escalation_nudge_count
                        and pair not in self._escalation_notified
                        and pair not in self._release_notified
                    ):
                        # Escalation notice: more urgent than the
                        # base hang notice. Same delivery path
                        # (enqueue_message + notify_work) but with
                        # a different provenance marker and a
                        # different message body.
                        escalation = _build_escalation_notice(
                            parent_id=parent_id,
                            child_id=child_id,
                            nudge_count=nudge_count,
                            hang_threshold_seconds=self._hang_threshold_seconds,
                        )
                        try:
                            await self._manager.enqueue_message(
                                instance_id=parent_id,
                                message=escalation,
                                source=WATCHDOG_SOURCE,
                                priority=0,
                                metadata={
                                    "watchdog_escalation": True,
                                    "escalated_pair": {
                                        "parent_id": parent_id,
                                        "child_id": child_id,
                                    },
                                    "nudge_count": nudge_count,
                                },
                            )
                            self._escalation_notified.add(pair)
                            self._escalation_notices_enqueued_total += 1
                            logger.warning(
                                f"[Watchdog] Escalation enqueued for "
                                f"parent {parent_id[:8]}... after "
                                f"{nudge_count} nudges — parked "
                                f"child {child_id[:8]}... is still "
                                f"non-terminal; consider replacement."
                            )
                        except Exception as esc_exc:
                            logger.warning(
                                f"[Watchdog] escalation enqueue "
                                f"failed for {pair}: {esc_exc!r}"
                            )

                if not new_pairs:
                    # No new pairs to base-notify. The B3
                    # escalation/release loop above still ran (it
                    # covers both new and persistent pairs).
                    scanned_ok.add(parent_id)
                    continue

                notice = _build_hang_notice(
                    parent_id=parent_id,
                    hung_children=new_pairs,
                    hang_threshold_seconds=self._hang_threshold_seconds,
                )
                # Deliver via the WAKING path. ``enqueue_message`` is
                # the house primitive that actually reaches a parked
                # WAITING_CHILDREN parent: it writes the MessageQueue
                # + Task rows, flips WC→RUNNING, and notifies the
                # worker pool (deep-review 2026-08-27: the previous
                # ``set_injection`` RAM-FIFO append never woke the
                # parent and was TTL-deleted ~1h later). priority=0
                # (system lane) and a structured metadata dict give
                # the durable row full audit provenance.
                await self._manager.enqueue_message(
                    instance_id=parent_id,
                    message=notice,
                    source=WATCHDOG_SOURCE,
                    priority=0,
                    metadata={
                        "watchdog_notice": True,
                        "hung_children": [
                            {"child_id": child_id, "age_seconds": age}
                            for child_id, age in new_pairs
                        ],
                        "hang_threshold_seconds": self._hang_threshold_seconds,
                    },
                )
                # Stamp the cooldown set BEFORE moving on so a
                # crash mid-loop does not double-notify on retry.
                # At-least-once caveat (council round-2 note): the
                # stamp happens AFTER the ``enqueue_message`` above
                # has committed, so a post-commit raise in the
                # delivery tail (inside enqueue_message after its
                # transaction, or between the enqueue and this
                # stamp) skips the stamp and the next tick
                # re-enqueues → a duplicate notice. Narrow window;
                # notice content is advisory, so a duplicate is
                # benign. Delivery is at-least-once, not
                # exactly-once.
                for child_id, _age in new_pairs:
                    self._notified.add((parent_id, child_id))
                stats["notices_enqueued"] += 1
                scanned_ok.add(parent_id)

                logger.info(
                    f"[Watchdog] Enqueued hang notice for parent "
                    f"{parent_id[:8]}... "
                    f"({len(new_pairs)} new hung children, "
                    f"{len(hung)} total hung)"
                )
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    f"[Watchdog] Failed to process parent "
                    f"{parent_id[:8]}...: {exc}",
                    exc_info=True,
                )
                # Per-parent isolation — continue to next parent.
                # Crucially: this parent is NOT added to
                # ``scanned_ok``, so any prior pair in ``_notified``
                # for it is preserved across the episode-end sweep
                # below. A transient scan error MUST NOT silently
                # clear the cooldown — the next healthy tick would
                # re-notify without a real episode change.
                continue

        # Episode-end detection: any pair in the cooldown set that
        # belongs to a parent whose scan succeeded this tick AND
        # is NOT in the freshly-computed ``currently_hung_pairs``
        # has ended (terminal or paused). Drop those so the
        # watchdog can re-notify on a future live episode. Pairs
        # whose parent failed or was skipped (PAUSED) this tick
        # stay in ``_notified`` unchanged — we have no fresh
        # signal for them, so we must not conclude the episode
        # ended. This is the documented anti-spam invariant.
        ended_pairs = {
            (parent_id, child_id)
            for parent_id, child_id in self._notified
            if parent_id in scanned_ok
            and (parent_id, child_id) not in currently_hung_pairs
        }
        if ended_pairs:
            self._notified -= ended_pairs
            logger.info(
                f"[Watchdog] Episode(s) ended for "
                f"{len(ended_pairs)} (parent, child) pair(s); "
                "cooldown cleared so future live episodes can re-notify."
            )

        # ─── Independent purge 1: parent left WAITING_CHILDREN ────
        # A parent absent from this tick's enumeration has exited
        # WAITING_CHILDREN (terminal report, external message,
        # revival, ...). Its pairs MUST be discarded: the notified
        # episode belonged to the previous WC stay, and a future WC
        # re-entry must be able to notify fresh — otherwise the
        # (parent, child) pair strands in ``_notified`` forever
        # while the child stays hung (deep-review warning 2). Runs
        # even when the parent's own scan failed this tick or in an
        # earlier tick — the enumeration itself is the fresh signal.
        #
        # Deliberate behavior (on record, council round-2): a parent
        # that re-parks on the SAME hung child (exits
        # WAITING_CHILDREN, then returns while the child stays hung)
        # WILL be re-notified on the next tick — bounded at 1 notice
        # per interval. Episodes end when the parent leaves the
        # waiting state; the parent-side boundary IS the episode
        # boundary, by design.
        still_waiting = set(parent_ids)
        departed_parent_pairs = {
            pair for pair in self._notified if pair[0] not in still_waiting
        }
        if departed_parent_pairs:
            self._notified -= departed_parent_pairs
            logger.info(
                f"[Watchdog] {len(departed_parent_pairs)} (parent, child) "
                f"pair(s) dropped — parent left WAITING_CHILDREN."
            )

        # ─── Independent purge 2: child terminal via ANY path ─────
        # The scan-driven sweep above only clears pairs whose parent
        # scanned cleanly this tick. A child can terminate while its
        # parent's scan was erroring, after the parent left WC, or
        # between ticks — those pairs would otherwise never clear.
        # One cheap SQL check over every child still referenced by
        # the cooldown set, independent of per-parent scan success
        # (deep-review "also required" clause).
        terminal_pairs: set[tuple[str, str]] = set()
        if self._notified:
            child_ids = {child for _p, child in self._notified}
            try:
                terminal_children = self._repo.list_terminal_instance_ids(
                    sorted(child_ids)
                )
            except Exception as exc:
                # DB blip on the purge query — keep the pairs (no
                # fresh signal), log, and count the error. The next
                # tick retries; the anti-spam invariant holds.
                stats["errors"] += 1
                logger.error(
                    f"[Watchdog] Failed to purge cooldown for terminal "
                    f"children: {exc}",
                    exc_info=True,
                )
                terminal_children = set()
            if terminal_children:
                terminal_pairs = {
                    (p, c)
                    for p, c in self._notified
                    if c in terminal_children
                }
                self._notified -= terminal_pairs
                logger.info(
                    f"[Watchdog] {len(terminal_pairs)} (parent, child) "
                    f"pair(s) dropped — child reached terminal status."
                )

        # ─── B3 episode-end purges for escalation state ────────────
        # Pairs that left ``_notified`` (episode ended — child
        # terminal, parent left WC, or the scan-driven sweep
        # cleared them) MUST also clear their nudge count and the
        # escalation/release cooldowns. A future episode with the
        # same pair must start fresh at nudge=1.
        all_ended_pairs = (
            ended_pairs | departed_parent_pairs | terminal_pairs
        )
        if all_ended_pairs:
            # Drop the per-pair nudge counts.
            purged_nudge_count = sum(
                1 for pair in all_ended_pairs
                if self._nudge_counts.pop(pair, None) is not None
            )
            if purged_nudge_count:
                logger.info(
                    f"[Watchdog] Purged {purged_nudge_count} per-pair "
                    f"nudge count(s) at episode end."
                )
            # Drop the escalation / release cooldowns.
            self._escalation_notified -= all_ended_pairs
            self._release_notified -= all_ended_pairs

        # ─── Wedge-fix backstop pass ─────────────────────────────────
        # Detect the wedge signature: parent in WAITING_CHILDREN +
        # ZERO non-terminal children + ZERO live (PENDING/RUNNING)
        # PROCESS_REPORT carrier task. When matched, enqueue a wedge
        # notice via the same wake path (enqueue_message + notify_work).
        # Composition property: when the sub-shape (c) carrier-revival
        # seam in ``manager._reconcile_deferred_report`` works, a live
        # carrier exists → this backstop stays silent. When the
        # revival seam is slow (or missed), this backstop catches
        # the gap until the next sweep cycle.
        #
        # B.S.5 (wc-wake-report-integrity Wave 2, OQ-4 disposition):
        # the wedge predicate ALSO skips parents that the (b)
        # report-integrity guard is actively guarding
        # (``report_integrity_guard.parent_has_active_b_notice`` —
        # an open adjudication-notice episode for this parent). (b)
        # fires at the completion stamp; the wedge sees WC-status
        # parents — the overlap is the tick-snapshot race (parent
        # observed WC pre-stamp, wedge pass runs post-stamp/notice).
        # A parent that (b) just noticed must NOT also get a wedge
        # notice for the SAME episode (no double-fire); the shared
        # cooldown clears when a later same-tx evaluation for the
        # parent comes back CLEAN (episode closed) or is replaced by
        # a different violation set (new episode) — after which the
        # wedge is eligible again.
        #
        # Budget per tick: ONE batched query for the children gate
        # (across all WC parents, via
        # ``repository.parents_with_non_terminal_children``) + ONE
        # per-parent carrier check (``_has_live_carrier_task``). The
        # existing ``parents_scanned`` enumeration already yielded the
        # WC parent set — we reuse ``parent_ids`` for the children
        # gate so we do not re-scan. The in-memory cooldown is
        # per-parent (``set[str]``), not per-(parent, child).
        #
        # Why the children gate matters: carriers are ONLY created at
        # child completion (``daemon/services/child_reports.py:2844-
        # 2852``). A HEALTHY WC parent waiting on still-running
        # children has no carrier yet — without the gate the backstop
        # fires a spurious wedge notice whose playbook recommends
        # terminate-and-respawn, which would orphan in-flight
        # children. The gate fires AFTER the PAUSED skip (a paused
        # parent is filtered out before any extra query) and BEFORE
        # the carrier check (the cheap per-parent branch), so the
        # batched query is paid at most once per tick.
        try:
            # Wedge-fix children gate — single batched query for the
            # entire WC-parent set. ``parents_with_non_term_children``
            # returns the subset of WC parents that have at least one
            # non-terminal child; the wedge predicate requires
            # ``parent_id NOT IN parents_with_non_term_children`` so a
            # healthy parent with live children stays silent. Mirrors
            # the third ``NOT EXISTS`` clause at
            # ``_build_zombie_scan_sql``:1083-1087 but in the inverse
            # direction (parents WITH non-terminal children).
            # ── B2: the CANDIDATES come FIRST — the WC parents PLUS
            # every parent with an OPEN episode. The children gate and
            # the subject read both run over this union so that an
            # open episode's codes can be re-evaluated even after the
            # parent has LEFT the WC scan set (its own notice flipped
            # WC→RUNNING).
            # F8: the subjects declarations are hoisted ABOVE the
            # candidates computation (the first raiser): if the
            # ``sorted(set(...) | set(...))`` ever raises, the outer
            # per-tick handler jumps straight to the episode-end
            # evaluation, which reads both names — unbound names there
            # would be a latent NameError.
            subjects: dict[str, tuple[int, list["DependencyWatcher"]]] = {}
            # R1(1): the subject read's failure is RECORDED, not
            # silently absorbed — every consumer below must fail
            # CLOSED on it (a fabricated "no watchers" would admit
            # healthy parents to the classic-wedge arm).
            subjects_read_failed = False
            _subject_candidates = sorted(
                set(parent_ids) | set(self._wedge_notified)
            )
            parents_with_non_term_children: set[str] = set()
            try:
                parents_with_non_term_children = (
                    self._repo.parents_with_non_terminal_children(
                        list(_subject_candidates)
                    )
                )
            except Exception as exc:
                # B4(2): FAIL-CLOSED. The old fallback (``set()``)
                # treated every WC parent as childless on a repo blip
                # — admitting healthy parents to the classic-wedge arm
                # and minting FALSE wedge notices on a DB hiccup
                # (plan success criterion #7: a legitimately-parked
                # parent gets no notice). A read failure now treats
                # every candidate as HAVING children: the classic-
                # wedge arm (``total_pending == 0 and no children``)
                # stays closed until the read recovers; only the S4
                # subject arm (a genuinely unfulfillable wait, read
                # from the bus) can still notify.
                logger.warning(
                    f"[Watchdog] Wedge children-gate query failed; "
                    f"FAIL-CLOSED: treating every candidate parent as "
                    f"HAVING non-terminal children (the classic-wedge "
                    f"arm stays closed). exc={exc}"
                )
                parents_with_non_term_children = set(_subject_candidates)
            # ── S4: the SUBJECT read (the watchdog's FIRST bus read) ──
            # One batched read for the whole tick: the PENDING watcher
            # rows of every candidate parent plus ONE batched
            # child-status read (never a per-parent N+1). The subject
            # of a parent is the set of PENDING watchers that can NO
            # LONGER FIRE (their watched child is already terminal, or
            # the watcher is self-referential) — the S4_NOTICE_SUBJECT.
            #
            # The candidates are the WC parents PLUS the parents with
            # an OPEN episode: an episode ends on the ROW-CHECK (the
            # subject empty), NOT on WC membership (the notice's own
            # enqueue flips WC→RUNNING, so a membership-based end
            # would end its own episode and restore the hourly
            # re-notification loop).
            try:
                subjects = self._fetch_wedge_subjects(
                    _subject_candidates
                )
            except Exception as exc:  # noqa: BLE001 — fail-soft
                logger.warning(
                    f"[Watchdog] Wedge subject read failed; treating "
                    f"every parent as UNKNOWN (fail-closed: no "
                    f"classic-wedge admission on a failed read). "
                    f"exc={exc}"
                )
                subjects = {}
                subjects_read_failed = True
            for parent_id in parent_ids:
                self._wedge_parents_scanned_total += 1
                # Skip PAUSED parents — same rationale as the
                # hang pass above. ``self._repo.get(parent_id)`` is
                # cheap (single-row fetch by primary key).
                parent_row = self._repo.get(parent_id)
                if parent_row is None:
                    continue
                if parent_row.status == InstanceStatus.PAUSED.value:
                    continue
                # B.S.5 shared cooldown: skip parents that the (b)
                # report-integrity guard is actively guarding (an open
                # adjudication-notice episode). OQ-4 disposition — no
                # double-fire for the same (parent, child, episode):
                # (b) fired at the completion stamp; the wedge must not
                # re-notice the same episode from a stale WC snapshot.
                # Cheap in-memory read (module-level ledger in
                # ``daemon/services/report_integrity_guard``).
                #
                # M2 (2026-10-03, report-delivery-bug-family) —
                # FP4 L6-anchor exemption: when a parent carries the
                # dedicated ``L6_ANCHOR_META_KEY`` key in its
                # instance_metadata (FP4 L6 settle-bound in flight,
                # declared-wait outstanding), the B.S.5 skip would
                # silently strand the worst-case backstop. The 6h
                # settle-bound contract requires terminal to fire
                # ONCE WITH the escalated-unverified display at the
                # bound, not a log-only annotation — the wedge pass is
                # the periodic re-trigger that wakes a parked
                # L6-anchor parent, whose next message-completed
                # signal re-runs the L6 release (set
                # completion_gate_escalated=True, proceed with
                # publish). Skipping the wedge for an l6_anchor
                # parent (because (b) is concurrently active on a
                # different episode) would defer the L6 release until
                # the (b) episode closes — possibly long past the
                # 6h bound. The narrow exemption only affects the
                # B.S.5 gate; every other wedge-condition leg
                # (children gate, live-carrier gate, anti-spam
                # cooldown) still applies, so an l6_anchor parent
                # with live children or a live carrier stays silent.
                if parent_has_active_b_notice(parent_id):
                    _l6_anchor = (
                        (parent_row.instance_metadata.get(L6_ANCHOR_META_KEY) if isinstance(parent_row.instance_metadata, dict) else None)
                        if parent_row is not None
                        else None
                    )
                    if not _l6_anchor:
                        continue
                # ── S4 (wake-path-carrier-fix, phase 4): the subject ──
                # The S4_NOTICE_SUBJECT of this parent — the PENDING
                # watchers that can NO LONGER FIRE (child already
                # terminal, or self-referential) — plus the total
                # PENDING count. Batched over the whole tick (one
                # bus read + ONE batched child-status read).
                _total_pending, _subject_rows = subjects.get(
                    parent_id, (0, [])
                )
                _has_non_terminal_children = (
                    parent_id in parents_with_non_term_children
                )
                # ── IN SCOPE (S4) ────────────────────────────────────
                # A parent whose only PENDING waits are FULFILLABLE is
                # LEGITIMATELY parked — it is OUT OF SCOPE (zero
                # notices; the raw-PENDING reading would have fired on
                # ~9x as many parents). A parent with NO watchers at
                # all is the classic wedge (nothing to wait for and
                # nothing to deliver it) — in scope, as before.
                if subjects_read_failed:
                    # R1(5): FAIL-CLOSED — with a failed subject read
                    # the totals are FABRICATED (``total_pending == 0``
                    # means "we could not read", not "there are no
                    # watchers"), so the classic-wedge arm must NOT be
                    # admitted on a failed read.
                    _in_scope = False
                elif _subject_rows:
                    _in_scope = True
                elif (
                    _total_pending == 0
                    and not _has_non_terminal_children
                ):
                    _in_scope = True
                else:
                    _in_scope = False
                if not _in_scope:
                    # The ROW-CHECK ended the episode (the S4 subject
                    # is empty for this parent) — drop the entry so a
                    # later recurrence may notify again.
                    # R1: a FAILED read is not a row-check verdict —
                    # the entry must survive it (fail-closed).
                    if not subjects_read_failed:
                        self._wedge_notified.pop(parent_id, None)
                    continue
                # Wedge condition part 1: zero live carrier. If a
                # carrier exists, the wedge is NOT active — the
                # existing carrier will deliver (the natural claim
                # path).
                _carrier_state = self._has_live_carrier_task(parent_id)
                if _carrier_state is None:
                    # R1(3): the carrier read FAILED — UNKNOWN. Fail
                    # CLOSED: no notice on an unknown carrier state
                    # (a false wedge notice is the harm), and the
                    # episode must NOT end on it.
                    continue
                if _carrier_state:
                    # Composition property: the revival seam worked.
                    # Clear any prior wedge episode for this parent
                    # so a future genuine wedge can re-notify.
                    self._wedge_notified.pop(parent_id, None)
                    continue
                # ── The reason codes (each a ROW check) ─────────────
                _codes: list[str] = []
                if _subject_rows:
                    _codes.append(WEDGE_REASON_PENDING_WATCHERS)
                # The carrier gate above guarantees this fact holds.
                _codes.append(WEDGE_REASON_NO_LIVE_CARRIER)
                _queue_read_ok, _queued_report_id = (
                    self._queued_completion_report_id(parent_id)
                )
                if _queue_read_ok and _queued_report_id:
                    _codes.append(WEDGE_REASON_QUEUED_REPORT)
                _non_terminal_count: int | None = 0
                if _has_non_terminal_children:
                    _codes.append(WEDGE_REASON_NON_TERMINAL_CHILDREN)
                    # B4(1): None (a failed read) flows through to the
                    # builder, which renders "unknown (read failed)".
                    _non_terminal_count = (
                        self._count_non_terminal_children(parent_id)
                    )
                    if _non_terminal_count == 0:
                        # #8: a successful-but-EMPTY children read is
                        # SELF-CONTRADICTING with the gate (which said
                        # this parent HAS non-terminal children) ⇒ the
                        # count is UNKNOWN, never a fabricated 0.
                        _non_terminal_count = None
                # ── Episode dedupe (S4): NO TTL ─────────────────────
                # The episode fingerprint is the REASON-CODE SET (the
                # DEDUPE_KEY is (parent_id, reason_code)). The
                # episode ends when a code's row-check stops holding
                # — NOT on a timer, and NOT on WC membership (the
                # notice's own enqueue flips WC→RUNNING, so a
                # membership-based end would end its own episode and
                # bring back the hourly re-notification loop).
                _fingerprint = tuple(sorted(_codes))
                if self._wedge_notified.get(parent_id) == _fingerprint:
                    # Same episode (same codes) — already notified.
                    continue
                # ── Kill switch (EMISSION ONLY, per-tick read) ─────
                # The switch gates the ENQUEUE and nothing else:
                # detection, classification, the codes and the
                # subject read all ran above. When the emission is
                # suppressed the episode is NOT stamped, so turning
                # the switch back ON notifies (no restart needed).
                if not self._wedge_notice_emission_enabled():
                    continue
                # Compose + deliver the wedge notice. Same
                # ``enqueue_message`` wake primitive as the hang
                # pass — it writes MessageQueue + Task rows, flips
                # WC→RUNNING, and notifies the worker pool.
                try:
                    notice = _build_wedge_notice(
                        parent_id=parent_id,
                        reason_codes=_codes,
                        subject_child_ids=[
                            str(
                                (
                                    (lambda _m: _m.get("child_id", "") if isinstance(_m, dict) else "")
                                )(
                                    (
                                        (lambda _p: (_p.get("metadata") if isinstance(_p, dict) else None))
                                        (_row.follow_up_payload if isinstance(_row.follow_up_payload, dict) else {})
                                    )
                                )
                            )
                            for _row in _subject_rows
                        ],
                        queued_report_id=_queued_report_id,
                        non_terminal_child_count=_non_terminal_count,
                    )
                    await self._manager.enqueue_message(
                        instance_id=parent_id,
                        message=notice,
                        source=WEDGE_SOURCE,
                        priority=0,
                        metadata={
                            "wedge_notice": True,
                            # Res6 (review advisory): derived from the
                            # SAME reason codes the notice body renders
                            # — the old flat literal went stale the
                            # moment S4 composed the body from codes.
                            "wedge_reason": ",".join(_codes),
                        },
                    )
                    # ── Batch A — A5 (2026-09-11): direct notify ────
                    # ``enqueue_message`` internally calls
                    # ``worker_pool.notify_work()`` once the Task row is
                    # committed, but that notify has proven unreliable
                    # on the wedge-notice path (incident 33252
                    # 2026-09-11: the wedge notice was stranded PENDING
                    # for zero claims ever). The cause is opaque — could
                    # be a lost wake between the enqueue commit and the
                    # pool's notify, or a defer-gate race that parked the
                    # notice behind a busy witness.
                    #
                    # The defensive fix: dispatch a DIRECT notify from
                    # the watchdog, AFTER ``enqueue_message`` returns, so
                    # the pool sees the wake regardless of whatever race
                    # swallowed the creation-time notify. Idempotent on
                    # the pool side (``notify_work()`` is a condition-
                    # variable signal — double-notify is wasted but
                    # benign). Wrapped in try/except so a transient
                    # pool-side blip does NOT abort the sweep (A3 sweep
                    # is the systemic backstop).
                    # Phase 2 / chat-source-worker-lane (D5): route
                    # through the manager helper — both default + chat
                    # pools receive the wake so freshly-eligible rows on
                    # either lane surface in the next claim cycle. The
                    # helper None-guards every pool and per-pool
                    # exceptions are swallowed (A3 sweep is the systemic
                    # backstop).
                    #
                    # The wedge-pass wake site is async (this is inside
                    # a coroutine), so we await the helper's return
                    # value when it is a coroutine — preserves the
                    # pre-Phase-B ``inspect.iscoroutine`` handling for
                    # ``AsyncMock`` fixtures that return a coroutine on
                    # ``notify_work()`` (otherwise the un-awaited
                    # coroutine emits a ``RuntimeWarning`` and breaks
                    # ``assert_awaited_once`` tests).
                    #
                    # ``safe_notify_all_pools`` (Phase B) holds the
                    # Mock-compat ``__dict__``-probe + try/except +
                    # legacy-fixture fallback logic that used to be
                    # duplicated here. Consolidated here as a single
                    # helper call with sync/async dispatch.
                    import inspect

                    notify_result = safe_notify_all_pools(
                        self._manager,
                        site_label="[Watchdog] wedge-pass",
                    )
                    if inspect.iscoroutine(notify_result):
                        await notify_result
                    # S4: stamp the REASON-CODE FINGERPRINT as the episode
                    # marker (not a timestamp — the TTL cooldown is
                    # dropped; the episode ends on the row-check, not on a
                    # timer). The fingerprint re-arms the pass for the NEXT
                    # wake if this one re-parks with a different reason set.
                    self._wedge_notified[parent_id] = _fingerprint
                    self._wedge_notices_enqueued_total += 1
                    logger.warning(
                        f"[Watchdog] Wedge notice enqueued for parent "
                        f"{parent_id[:8]}... (no live carrier, zero "
                        f"non-terminal children); waking parked WC turn."
                    )
                except Exception as _parent_err:  # noqa: BLE001 — per-parent isolation
                    logger.warning(
                        "[Watchdog] wedge composition/delivery failed for "
                        "parent %s...: %s: %s",
                        parent_id[:8],
                        type(_parent_err).__name__,
                        _parent_err,
                    )
                    continue
        except Exception as exc:
            # Per-tick error isolation — a wedge-pass failure must
            # NOT crash the whole ``run_once``. The hang pass already
            # ran; this is purely additive observability.
            stats["errors"] += 1
            logger.error(
                f"[Watchdog] Wedge-pass scan failed: {exc}",
                exc_info=True,
            )

        # Wedge-episode-end purges — distinct from the hang-episode
        # purges above so a wedge notice can re-fire even when hang
        # notices are silenced. Two independent mechanisms clear
        # ``_wedge_notified``:
        #
        # 1. Composition check above: a parent that acquires a live
        #    carrier between ticks has its wedge entry dropped. This
        #    is the cheap + frequent case — most wedge notices
        #    resolve themselves once the sub-shape (c) seam runs.
        # 2. ROW-CHECK purge below: a parent whose S4 subject row-check
        #    turns FALSE — its S4_NOTICE_SUBJECT (the PENDING watchers
        #    that can no longer fire) goes EMPTY while it still has
        #    PENDING waits — has its wedge entry dropped. That parent
        #    is legitimately parked again, so the episode is over and a
        #    later recurrence may notify afresh. WC MEMBERSHIP is
        #    deliberately NOT the trigger: the notice's OWN enqueue
        #    flips the parent WC→RUNNING, so a membership-based end
        #    would end the episode it just raised and restore the
        #    hourly re-notification loop (T5b).
        if self._wedge_notified:
            # ── B2 (review blocker): the episode-end evaluation now
            # covers EVERY open episode — INCLUDING parents that have
            # LEFT the WC scan set (their own notice flipped
            # WC→RUNNING). The old purge required ``total_pending > 0``
            # and only ran for scanned parents, so a genuinely
            # resolved episode NEVER ended: a later same-code
            # recurrence fingerprint-matched and the parent was
            # PERMANENTLY SILENCED. The plan body wins: an episode
            # ends on the FIRST TICK on which its reason code no
            # longer holds — evaluated here against the parent's
            # CURRENT row-checks (the subject read and the children
            # gate both already cover the open-episode candidates).
            #
            # Per-code semantics: a stamped code that stops holding
            # ENDS that code's episode. The surviving codes keep the
            # entry (narrowed, WITHOUT re-notifying — their episodes
            # continue); when the last code stops holding the entry
            # is dropped outright and a later recurrence of ANY of
            # them may notify again. A code that REAPPEARS is caught
            # by the in-loop fingerprint comparison on the parent's
            # next WC pass (the set grows ⇒ a new notice).
            for _p in list(self._wedge_notified):
                _stamped = self._wedge_notified.get(_p) or ()
                # R1(4): per-parent isolation + the UNKNOWN rule. An
                # exception, or ANY stamped code that evaluates to
                # None (its read FAILED), leaves the entry UNTOUCHED —
                # a read failure may POSTPONE the verdict, never end
                # the episode, and never re-notify.
                try:
                    _verdicts = [
                        self._wedge_code_still_holds(
                            _p,
                            code,
                            subjects=subjects,
                            parents_with_children=(
                                parents_with_non_term_children
                            ),
                            subjects_read_failed=subjects_read_failed,
                        )
                        for code in _stamped
                    ]
                    if any(_v is None for _v in _verdicts):
                        # UNKNOWN on at least one stamped code — leave
                        # the episode exactly as it is.
                        logger.info(
                            f"[Watchdog] wedge episode for {_p[:8]}... "
                            f"left UNTOUCHED — a stamped reason code's "
                            f"read FAILED (the verdict is postponed, "
                            f"not decided)."
                        )
                        continue
                    _surviving = tuple(
                        code
                        for code, _v in zip(_stamped, _verdicts)
                        if _v
                    )
                except Exception as exc:  # noqa: BLE001 — fail-closed
                    logger.warning(
                        f"[Watchdog] wedge episode evaluation failed "
                        f"for {_p[:8]}...: %s: %s — the episode is left "
                        f"UNTOUCHED",
                        type(exc).__name__,
                        exc,
                    )
                    continue
                if _surviving != _stamped:
                    if _surviving:
                        # Narrowed: the surviving codes' episodes
                        # continue (no re-notify for them).
                        self._wedge_notified[_p] = _surviving
                    else:
                        # Fully resolved: the episode is over.
                        self._wedge_notified.pop(_p, None)
                    logger.info(
                        f"[Watchdog] wedge episode for {_p[:8]}... "
                        f"{'narrowed' if _surviving else 'ended'} — a "
                        f"stamped reason code no longer holds "
                        f"(stamped={list(_stamped)}, "
                        f"surviving={list(_surviving)})."
                    )

        return stats


async def run_waiting_children_watchdog_loop(
    watchdog: WaitingChildrenWatchdog,
    *,
    interval_seconds: int,
) -> None:
    """Drive ``watchdog.run_once`` in a periodic asyncio loop.

    Mirrors ``_periodic_drift_reconcile_loop`` at
    ``daemon/api.py:997``. Cancellation contract:

    * ``asyncio.CancelledError`` is the shutdown signal. The outer
      ``try/except asyncio.CancelledError: raise`` propagates it so
      the asyncio runtime knows the task is done.
    * The inner ``try/except Exception`` swallows a single cycle's
      failure (DB blip, malformed row) — the loop is best-effort
      and a missed cycle is recovered on the next tick. The
      watchdog's own per-parent error isolation further contains
      blast radius inside a single cycle.
    * The post-tick ``asyncio.sleep`` swallows ``CancelledError``
      and returns cleanly — preferred over ``raise`` here because
      the loop's natural end is between ticks, not in the middle
      of one.

    Args:
        watchdog: The watchdog instance to drive. If
            ``watchdog.enabled`` is False, the loop returns
            immediately without running a single cycle.
        interval_seconds: Sleep between cycles. Must be > 0; the
            lifespan validates this when constructing the watchdog.
    """
    if not watchdog.enabled:
        logger.info(
            "[Watchdog] Disabled by config — loop not started."
        )
        return

    logger.info(
        f"[Watchdog] Starting periodic loop: "
        f"interval={interval_seconds}s, "
        f"hang_threshold={watchdog.hang_threshold_seconds}s"
    )

    while True:
        try:
            stats = await watchdog.run_once()
            if stats["notices_enqueued"] > 0 or stats["errors"] > 0:
                logger.info(
                    f"[Watchdog] tick stats: {stats}"
                )
        except asyncio.CancelledError:
            # Shutdown — propagate so the runtime knows the task is done.
            raise
        except Exception as exc:
            # Best-effort: a single failed cycle is not fatal. The
            # next tick will retry. Log at ERROR so the operator has
            # visibility.
            logger.error(
                f"[Watchdog] cycle failed: {exc}",
                exc_info=True,
            )

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return


__all__ = [
    "WATCHDOG_SOURCE",
    "WaitingChildrenWatchdog",
    "run_waiting_children_watchdog_loop",
]
