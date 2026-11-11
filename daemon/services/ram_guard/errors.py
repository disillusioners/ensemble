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

from .verdict import LANE_BASH, LANE_PROC, SpawnRecord

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


__all__ = [
    "DEFAULT_SURFACE",
    "ENVELOPE_KEYS",
    "EVENT_ONLY_KEYS",
    "EVENT_PAYLOAD_KEYS",
    "MESSAGE_TEMPLATE",
    "RAM_LIMIT_EXCEEDED_CODE",
    "SURFACE_BY_LANE",
    "build_message",
    "build_ram_envelope_payload",
    "build_ram_event_payload",
    "format_ram_envelope",
    "format_ram_event_payload",
    "surface_for_lane",
]
