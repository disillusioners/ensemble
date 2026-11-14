"""``errors`` — the envelope producer and the event-payload allow-list.

Contract: architecture-recommendation.md §4.3 (the typed-error contract
+ the key set + the negative space), §4.5 (the durable event + its
payload key set), §15 B2 (the envelope key set), §15 B3 (the event
payload key set), §15 B10 (the message clause), phase2-plan.md T16 /
T17 / T23.

**One producer, two surfaces** (arch §8.1): the bash lane's return
string IS the envelope; the proc lane renders a ``ram_breach:`` line
+ a flat ``ram_killed: true`` from the same payload.

**The key sets are ORDERED and EXACT** — the allow-list is enforced AT
THE PRODUCER because the event bus persists ``data`` UNSANITIZED
(``event_bus.py:174-181``): a key that is not on the list is never
copied, and no code path may mutate the returned dict before it
reaches ``EventBus.create_event``.

**The negative space (§4.3 / T17).**  The envelope and the event
payload carry USAGE NUMBERS ONLY.  Forbidden in any form: command /
argv INCLUDING hashes (argv carries secrets; a hash is a brute-force
oracle and the sanitized form still leaks the ``--flag value``
residual) · env keys or values (a live child's ``/proc/<pid>/environ``
holds plaintext resolved secrets) · stdout/stderr · secret ids ·
per-member pid lists / RSS breakdowns · host paths / hostnames /
workdir · exception reprs.  ``message`` is FIXED TEXT with numeric
interpolation only.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .verdict import LANE_BASH, LANE_PROC, SpawnRecord, STATE_KILLED

# ── The envelope key set (§15 B2 — ordered, exact) ─────────────────────────

#: The envelope key set, in order.  ``rearm_threshold_bytes`` sits
#: IMMEDIATELY AFTER ``limit_bytes`` so the configured ceiling and the
#: enforced trigger threshold read together (§15 B2).
ENVELOPE_KEYS: tuple[str, ...] = (
    "ok",
    "code",
    "surface",
    "reason",
    "limit_bytes",
    "rearm_threshold_bytes",
    "observed_peak_bytes",
    "peak_is_lower_bound",
    "poll_interval_ms",
    "metric",
    "trigger_metric",
    "confirmed_pss_bytes",
    "under_report_bound_bytes",
    "pid",
    "pgid",
    "tree_complete",
    "members_total",
    "members_killable",
    "members_unreachable",
    "grace_ms",
    "killed_at",
    "ram_breach_observed",
    "coincident_timeout",
    "timeout_seconds",
    "message",
)

# ── The event payload key set (§15 B3 — ordered, exact) ────────────────────

#: The envelope key set + EXACTLY four event-only keys: ``trigger``
#: (the fail-open discriminator), ``state`` (the transition),
#: ``tracking_id`` and ``breach_at``.
EVENT_ONLY_KEYS: tuple[str, ...] = (
    "trigger",
    "state",
    "tracking_id",
    "breach_at",
)

EVENT_PAYLOAD_KEYS: tuple[str, ...] = ENVELOPE_KEYS + EVENT_ONLY_KEYS

# ── The vocabulary ─────────────────────────────────────────────────────────

#: The envelope's code — the SAME string as ``EventKind
#: .RAM_LIMIT_EXCEEDED`` (§17.1).
RAM_LIMIT_EXCEEDED_CODE = "ram_limit_exceeded"

#: The surfaces.  Both strings are PINNED by the contract (arch §2 DQ6:
#: ``surface="bash.exec"|"proc.exec"``; plan-overview.md:481) — the
#: proc lane's surface is ``proc.exec``, NOT a tool-name variant.
SURFACE_BY_LANE: dict[str, str] = {
    LANE_BASH: "bash.exec",
    LANE_PROC: "proc.exec",
}

DEFAULT_SURFACE = SURFACE_BY_LANE[LANE_BASH]

#: The message clause (§15 B10 — AMENDED: "the resolved ceiling").
#: FIXED TEXT with numeric interpolation only — there is NO argv
#: interpolation path.  (The pinned string, verbatim:)
#: "The enforced trigger threshold was {threshold_mib} MiB (the resolved
#: ceiling is {limit_mib} MiB); when the summed-RSS metric over-counts
#: shared pages the threshold is re-armed above the ceiling and the kill
#: decision is always made on the shared-page total."
MESSAGE_TEMPLATE = (
    "The enforced trigger threshold was {threshold_mib} MiB (the resolved "
    "ceiling is {limit_mib} MiB); when the summed-RSS metric over-counts "
    "shared pages the threshold is re-armed above the ceiling and the kill "
    "decision is always made on the shared-page total."
)

#: Bytes per MiB.
MIB = 1024 * 1024


def surface_for_lane(lane: Optional[str]) -> str:
    """The surface string for a lane."""
    if lane and lane in SURFACE_BY_LANE:
        return SURFACE_BY_LANE[lane]
    return DEFAULT_SURFACE


def _mib(value_bytes: int) -> int:
    """Bytes → MiB (rounded down) for the message clause."""
    return int(value_bytes) // MIB


def build_message(record: SpawnRecord) -> str:
    """The FIXED message clause with numeric interpolation only."""
    threshold = record.effective_threshold_bytes
    return MESSAGE_TEMPLATE.format(
        threshold_mib=_mib(threshold),
        limit_mib=_mib(record.ceiling_bytes),
    )


def build_ram_envelope_payload(
    record: SpawnRecord,
    *,
    code: str = RAM_LIMIT_EXCEEDED_CODE,
    surface: Optional[str] = None,
    ram_breach_observed: bool = True,
    coincident_timeout: bool = False,
    timeout_seconds: Optional[float] = None,
    message: Optional[str] = None,
) -> dict[str, Any]:
    """The envelope payload — EXACTLY the §15 B2 key set, in order.

    One producer, two surfaces: the bash lane returns this as the
    whole string; the proc lane renders lines from it.
    """
    payload: dict[str, Any] = {
        "ok": False,
        "code": code,
        "surface": surface if surface is not None else surface_for_lane(record.lane),
        "reason": record.reason,
        "limit_bytes": record.ceiling_bytes,
        "rearm_threshold_bytes": record.rearm_threshold_bytes,
        "observed_peak_bytes": record.observed_peak_bytes,
        "peak_is_lower_bound": record.peak_is_lower_bound,
        "poll_interval_ms": record.poll_interval_ms,
        "metric": record.metric,
        "trigger_metric": record.trigger_metric,
        "confirmed_pss_bytes": record.confirmed_pss_bytes,
        "under_report_bound_bytes": record.under_report_bound_bytes,
        "pid": record.pid,
        "pgid": record.pgid,
        "tree_complete": record.tree_complete,
        "members_total": record.members_total,
        "members_killable": record.members_killable,
        "members_unreachable": record.members_unreachable,
        "grace_ms": record.grace_ms,
        "killed_at": record.killed_at,
        "ram_breach_observed": ram_breach_observed,
        "coincident_timeout": coincident_timeout,
        "timeout_seconds": timeout_seconds,
        "message": message if message is not None else build_message(record),
    }
    assert tuple(payload.keys()) == ENVELOPE_KEYS, "envelope key set drifted"
    return payload


def format_ram_envelope(
    record: SpawnRecord,
    *,
    code: str = RAM_LIMIT_EXCEEDED_CODE,
    surface: Optional[str] = None,
    ram_breach_observed: bool = True,
    coincident_timeout: bool = False,
    timeout_seconds: Optional[float] = None,
    message: Optional[str] = None,
) -> str:
    """The envelope as the tool's return STRING (the bash surface).

    The 3-key house prefix (``ok``, ``code``, ``surface``) is
    byte-identical to ``_format_secret_envelope`` and the numerics
    precede ``message``.
    """
    payload = build_ram_envelope_payload(
        record,
        code=code,
        surface=surface,
        ram_breach_observed=ram_breach_observed,
        coincident_timeout=coincident_timeout,
        timeout_seconds=timeout_seconds,
        message=message,
    )
    return json.dumps(payload)


def build_ram_event_payload(record: SpawnRecord) -> dict[str, Any]:
    """The event payload — EXACTLY the §15 B3 key set, in order.

    The ONLY producer of the event payload: it projects the listed keys
    from the :class:`SpawnRecord` and nothing else.  A key that is not
    on the list is NEVER copied — the bus persists ``data`` unsanitized,
    so the allow-list MUST be enforced here, at the producer.
    """
    payload = build_ram_envelope_payload(record)
    payload["trigger"] = record.trigger
    payload["state"] = record.state
    payload["tracking_id"] = record.tracking_id
    payload["breach_at"] = record.breach_at
    assert tuple(payload.keys()) == EVENT_PAYLOAD_KEYS, "event payload key set drifted"
    return payload


def format_ram_event_payload(record: SpawnRecord) -> str:
    """The event payload as JSON (the transition log line's body)."""
    return json.dumps(build_ram_event_payload(record))




# ── Proc-lane additive surface (arch §8.1, §15 B25) ────────────────────────
# The SECOND surface of the SAME producer: the bash lane returns the
# envelope; the proc lane renders these lines from the SAME payload
# (build_ram_envelope_payload).  B25-verbatim line templates live here so
# both lanes share ONE producer; the proc lane only calls this helper.
# The proc lane has no typed error surface — proc_run returns before any
# breach can exist (arch §4.3, §3.5 item 12).

#: The proc lane's resolved-kill line prefix (its name is pinned by the
#: plan as ``ram_breach:``).
PROC_BREACH_LINE_PREFIX = "ram_breach: "

#: The proc lane's flat kill-confirmed line.
PROC_KILLED_LINE = "ram_killed: true"

#: The B25-verbatim refusal lines (mirrored from the bash lane's
#: RAM_REFUSAL_LINE / RAM_BREACH_LINE — the names are pinned by B25).
PROC_REFUSAL_LINE = "ram_kill_refused: {reason}"
PROC_BREACH_OBSERVED_LINE = "ram_breach_observed: true"

#: The pending-surface peak line — name mirrors bash's RAM_PEAK_LINE
#: (bash.py:850) verbatim (dispatcher ruling 2026-11-11: the pending-
#: surface peak line must be named ``ram_observed_peak_bytes``).
PROC_PEAK_LINE = "ram_observed_peak_bytes: {peak_bytes}"

# The curated subset of envelope keys the proc ``ram_breach:`` line
# carries (arch §4.3 negative space: no argv / env / stdout / secret
# ids / host paths / per-member lists — the payload already enforces
# this).  ``rearm_threshold_bytes`` sits immediately after ``limit_bytes``
# ("beside" the configured ceiling), mirroring the envelope key order.
# ``coincident_timeout`` rides the line so the coincidence ruling is
# OBSERVABLE in this lane (its semantics are ``bool(timed_out)`` — the
# dispatcher ruling 2026-11-11: the coincidence is reported when the
# timeout arm ALSO fired, NOT when the guard "wins" the race).
PROC_BREACH_KEYS: tuple[str, ...] = (
    "limit_bytes",
    "rearm_threshold_bytes",
    "observed_peak_bytes",
    "confirmed_pss_bytes",
    "peak_is_lower_bound",
    "under_report_bound_bytes",
    "metric",
    "trigger_metric",
    "coincident_timeout",
)


def _fmt_proc_value(v: Any) -> str:
    """Render a payload value as a compact, content-free token."""
    if v is None:
        return "none"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def format_ram_proc_lines(record, *, timed_out: bool = False) -> list[str]:
    """The proc lane's additive surface lines for ``proc_status``.

    Returns a **list of lines** (each a string) to be APPENDED to the
    existing ``proc_status`` block — never replacing it.  Empty list when
    the record is None or carries no breach.  The proc_status block stays
    byte-identical to today for a non-RAM process (DQ6).

    States (checked in this order):
      - ``reason`` starts with ``refused:`` ⇒ the B25 refusal surface
        (``ram_kill_refused: <reason>`` + ``ram_breach_observed: true``)
        — a refusal is NEVER swallowed.
      - terminal ``KILLED`` (or a ``killed:`` reason) ⇒ a ``ram_breach:``
        line (projected from the SAME payload as the bash envelope) +
        the flat ``ram_killed: true``.
      - breach observed but the episode is NOT yet terminal (pending) ⇒
        ``ram_breach_observed: true`` + ``ram_observed_peak_bytes: <n>``
        (the pending-surface peak line mirrors bash verbatim).
      - otherwise ⇒ no RAM lines (byte-identical block).

    ``timed_out`` feeds the envelope's ``coincident_timeout`` flag, whose
    semantics are ``bool(timed_out)`` (dispatcher ruling 2026-11-11):
    the coincidence is reported when the timeout arm ALSO fired — NOT
    when the guard "wins" the race.
    """
    if record is None:
        return []
    reason = getattr(record, "reason", None)
    reason_str = reason if isinstance(reason, str) else ""
    if reason_str.startswith("refused:"):
        lines = [PROC_REFUSAL_LINE.format(reason=reason_str)]
        if breach_observed(record):
            lines.append(PROC_BREACH_OBSERVED_LINE)
        return lines
    state = getattr(record, "state", None)
    if state == STATE_KILLED or reason_str.startswith("killed:"):
        payload = build_ram_envelope_payload(
            record, coincident_timeout=bool(timed_out)
        )
        parts = [
            f"{k}={_fmt_proc_value(payload.get(k))}" for k in PROC_BREACH_KEYS
        ]
        return [
            PROC_BREACH_LINE_PREFIX + " ".join(parts),
            PROC_KILLED_LINE,
        ]
    # Pending: a breach was observed but the episode is not resolved yet —
    # the decision is reported as PENDING, never fabricated (§4.3).
    if (
        getattr(record, "breach_at", None) is not None
        and not getattr(record, "is_terminal", False)
    ):
        return [
            PROC_BREACH_OBSERVED_LINE,
            PROC_PEAK_LINE.format(peak_bytes=int(record.observed_peak_bytes)),
        ]
    return []



def breach_observed(record) -> bool:
    """Canonical predicate: was a breach actually OBSERVED?

    ``True`` iff ``record`` is not None and ``record.breach_at`` is not
    None.  Shared by the bash lane (``_ram_additive_surface``) and the
    proc lane (``format_ram_proc_lines``) so the two surfaces can never
    drift on the breach-observed question.
    """
    return bool(record is not None and getattr(record, "breach_at", None) is not None)



__all__ = [
    "DEFAULT_SURFACE",
    "ENVELOPE_KEYS",
    "EVENT_ONLY_KEYS",
    "EVENT_PAYLOAD_KEYS",
    "MESSAGE_TEMPLATE",
    "RAM_LIMIT_EXCEEDED_CODE",
    "SURFACE_BY_LANE",
    "breach_observed",
    "build_message",
    "build_ram_envelope_payload",
    "build_ram_event_payload",
    "format_ram_envelope",
    "format_ram_event_payload",
    "format_ram_proc_lines",
    "PROC_BREACH_KEYS",
    "PROC_BREACH_LINE_PREFIX",
    "PROC_BREACH_OBSERVED_LINE",
    "PROC_KILLED_LINE",
    "PROC_PEAK_LINE",
    "PROC_REFUSAL_LINE",
    "surface_for_lane",
]
