"""``verdict`` — the verdict record, the breach predicate and the bounded
over-count re-arm.

Contract: architecture-recommendation.md §4.4 (the verdict record,
RAM-only, keyed by ``tracking_id``), §4.1a (the BOUNDED re-arm),
§17.1 (the ``reason`` sink), §17.2 (the terminal ``REFUSED`` state),
phase2-plan.md T4 / T5 / T6 / T7.

**The record is the honesty carrier.**  Every number the envelope and
the event payload publish comes from here — the peak, the under-report
bound, the member counts, the deciding PSS value.

**The bounded re-arm (§4.1a / §15 B1, verbatim):**

    rearm_threshold_bytes = max(previous_rearm_threshold_bytes, ceiling_bytes + min(shared_extra_bytes, RAM_GUARD_REARM_MAX_EXTRA_BYTES))
    confirm_due = (summed_rss_bytes >= rearm_threshold_bytes) and (summed_rss_bytes - last_confirm_rss_bytes >= RAM_GUARD_REARM_MIN_GROWTH_BYTES)
    RAM_GUARD_REARM_MAX_EXTRA_BYTES = 33554432
    RAM_GUARD_REARM_MIN_GROWTH_BYTES = 134217728

The two constants are MODULE CONSTANTS, not config knobs (arch §2 DQ7:
"depth/node caps are module constants, not knobs" — the same
precedent).

**The state machine (§4.4 / §17.2).**  WATCHING → BREACHED →
{OVERCOUNTED (sticky) | KILLED | REFUSED} → RELEASED.
First-writer-wins applies to the terminal transitions and to
WATCHING→BREACHED; ``OVERCOUNTED → OVERCOUNTED`` is an IDEMPOTENT
in-place ratchet (no state re-entry, no BREACHED re-entry).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from .tree_reader import exceeds

# ── Module constants (NOT knobs — §15 B1) ──────────────────────────────────

#: The bounded re-arm's extra-headroom cap (32 MiB).
RAM_GUARD_REARM_MAX_EXTRA_BYTES = 33554432

#: The confirm's growth governor (128 MiB) — a confirm does NOT run on
#: every tick.
RAM_GUARD_REARM_MIN_GROWTH_BYTES = 134217728

# ── States (§4.4) ───────────────────────────────────────────────────────────

STATE_WATCHING = "WATCHING"
STATE_BREACHED = "BREACHED"
STATE_OVERCOUNTED = "OVERCOUNTED"
STATE_KILLED = "KILLED"
STATE_REFUSED = "REFUSED"
STATE_RELEASED = "RELEASED"

#: The states from which no further transition is allowed.
TERMINAL_STATES = frozenset({STATE_KILLED, STATE_REFUSED, STATE_RELEASED})

# ── Trigger vocabulary (§4.4 / §15 B3) ──────────────────────────────────────

#: The episode began with an RSS threshold crossing (the ordinary path).
TRIGGER_RSS_THRESHOLD = "rss_threshold"
#: The kill decision crossed at the CONFIRM stage (a re-armed episode
#: whose summed PSS is what proved the kill).
TRIGGER_PSS_THRESHOLD = "pss_threshold"
#: The fail-open discriminator — the confirm could not establish the
#: deciding value (failure or timeout), so the kill proceeded on the
#: RSS peak.
TRIGGER_PSS_CONFIRM_UNAVAILABLE = "pss_confirm_unavailable"

TRIGGER_VALUES = (
    TRIGGER_RSS_THRESHOLD,
    TRIGGER_PSS_THRESHOLD,
    TRIGGER_PSS_CONFIRM_UNAVAILABLE,
)

# ── Lanes ───────────────────────────────────────────────────────────────────

LANE_BASH = "bash"
LANE_PROC = "proc"


@dataclass(frozen=True)
class PeakRecord:
    """The peak bookkeeping — monotonic by construction (test-plan S7)."""

    peak_bytes: int = 0
    is_lower_bound: bool = True
    last_total_bytes: int = 0

    def with_sample(self, total_bytes: int) -> "PeakRecord":
        """Fold one sample in — the peak NEVER decreases."""
        return PeakRecord(
            peak_bytes=max(self.peak_bytes, total_bytes),
            is_lower_bound=self.is_lower_bound,
            last_total_bytes=total_bytes,
        )


@dataclass
class SpawnRecord:
    """The verdict record — RAM-only, keyed by ``tracking_id`` (§4.4).

    ``ceiling_bytes`` is the RESOLVED ceiling (derived or explicit), not
    the raw knob (§4.7d).  ``rearm_threshold_bytes`` is the ENFORCED
    trigger threshold: 0 (never ``None``) when no re-arm was armed.
    """

    tracking_id: str
    instance_id: str
    lane: str
    pid: int
    pgid: int
    start_time_jiffies: Optional[int]
    ceiling_bytes: int
    poll_interval_ms: int
    grace_ms: int

    state: str = STATE_WATCHING
    reason: Optional[str] = None

    observed_peak_bytes: int = 0
    last_total_bytes: int = 0
    samples: int = 0
    metric: str = ""
    trigger_metric: str = ""
    peak_is_lower_bound: bool = True
    under_report_bound_bytes: int = 0
    confirmed_pss_bytes: Optional[int] = None
    tree_complete: bool = False

    members_total: int = 0
    members_killable: int = 0
    members_unreachable: int = 0
    unreadable_pids: tuple[int, ...] = ()
    gone_pids: tuple[int, ...] = ()
    enumeration_failures: int = 0

    breach_at: Optional[float] = None
    killed_at: Optional[float] = None
    last_sample_at: Optional[float] = None
    trigger: Optional[str] = None

    rearm_threshold_bytes: int = 0
    last_confirm_rss_bytes: int = 0
    rearm_count: int = 0
    confirm_count: int = 0

    # ── Operational fields (not published; never in an envelope/event) ──
    #: The observed member set — the carry-forward's seed (§4.1).
    members: tuple[int, ...] = ()
    #: The live member count of the CAPTURED GROUP — 0 means the watch
    #: is over (``group_member_count == 0``, §15 B6).
    group_member_count: int = 0
    #: Scan completeness of the LAST sample (False when unreadable pids
    #: were present) — gates the release on incomplete scans.
    group_scan_complete: bool = True
    #: The EPISODE-scoped claim token (§17.6) — ``None`` when unclaimed.
    claim_token: Optional[int] = None
    #: Whether this record has EVER been claimed (§18.5 slot reservation).
    ever_claimed: bool = False
    #: Backoff ticks remaining after a transient refusal (§18.5).
    transient_backoff: int = 0
    #: The bounded sample series the under-report bound is derived from.
    sample_totals: list[int] = field(default_factory=list)

    # ── Derived helpers ────────────────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def effective_threshold_bytes(self) -> int:
        """The ENFORCED trigger threshold (the re-armed one when armed)."""
        return self.rearm_threshold_bytes or self.ceiling_bytes

    # ── State transitions (first-writer-wins) ──────────────────────────────

    def mark_breached(self, now: float) -> bool:
        """WATCHING → BREACHED (first-writer-wins)."""
        if self.state != STATE_WATCHING:
            return False
        self.state = STATE_BREACHED
        self.breach_at = now
        return True

    def mark_overcounted(
        self,
        *,
        now: float,
        shared_extra_bytes: int,
        summed_rss_bytes: int,
    ) -> bool:
        """The OVERCOUNTED re-arm — the STICKY idempotent update.

        The threshold ratchets (never decreases); the episode's
        recorded outcome IS the re-armed threshold.  No verdict is
        published and no event is emitted (arch §4.5 / §15 B21).
        """
        if self.state not in (STATE_BREACHED, STATE_OVERCOUNTED):
            return False
        self.state = STATE_OVERCOUNTED
        self.rearm_threshold_bytes = compute_rearm_threshold(
            self.rearm_threshold_bytes,
            self.ceiling_bytes,
            shared_extra_bytes,
        )
        self.last_confirm_rss_bytes = summed_rss_bytes
        self.rearm_count += 1
        self.confirm_count += 1
        self.last_sample_at = now
        return True

    def mark_killed(self, now: float, reason: str) -> bool:
        """→ KILLED (first-writer-wins) — the terminal kill transition."""
        if self.state not in (STATE_BREACHED, STATE_OVERCOUNTED):
            return False
        self.state = STATE_KILLED
        self.killed_at = now
        self.reason = reason
        return True

    def mark_refused(self, reason: str, *, permanent: bool) -> bool:
        """→ REFUSED (kill-decision terminal, ruling A) or → WATCHING
        (transient re-arm with the §18.5 backoff).

        §17.2/§18.1: the transient refusals (``refused:leader-unreadable``,
        ``refused:group-unknown``) re-arm to WATCHING and are
        re-claimable after the backoff; the permanent ones terminate the
        KILL DECISION only — the record stays REFUSED-but-watching.
        """
        # Ruling A (§18.1): a permanent refusal terminates the KILL
        # DECISION only — the record stays REFUSED-but-watching.  OVERCOUNTED
        # is an accepted predecessor (a re-armed tree can later refuse).
        if self.state not in (STATE_BREACHED, STATE_OVERCOUNTED):
            return False
        self.reason = reason
        if permanent:
            self.state = STATE_REFUSED
            return True
        self.state = STATE_WATCHING
        return False

    def mark_released(self) -> bool:
        """→ RELEASED (first-writer-wins)."""
        if self.state == STATE_RELEASED:
            return False
        self.state = STATE_RELEASED
        return True

    # ── Sampling ───────────────────────────────────────────────────────────

    def record_sample(self, total_bytes: int, now: float) -> None:
        """Fold one RSS sample in (the peak is monotonic)."""
        self.samples += 1
        self.last_sample_at = now
        self.last_total_bytes = total_bytes
        self.observed_peak_bytes = max(self.observed_peak_bytes, total_bytes)
        self.peak_is_lower_bound = True
        self.sample_totals.append(total_bytes)
        if len(self.sample_totals) > 256:
            del self.sample_totals[:-256]
        self.under_report_bound_bytes = under_report_bound_bytes(self.sample_totals)


# ── The breach predicate (T5) ───────────────────────────────────────────────


def breaches(total_bytes: int, ceiling_bytes: int) -> bool:
    """The breach predicate — ``total >= ceiling`` (INCLUSIVE).

    The trigger fires on the CURRENT sample, not on the peak: a spike
    released before the next sample is reported (peak) but never
    killed.
    """
    return exceeds(total_bytes, ceiling_bytes)


# ── The bounded re-arm (§4.1a / §15 B1) ─────────────────────────────────────


def compute_rearm_threshold(
    previous_rearm_threshold_bytes: int,
    ceiling_bytes: int,
    shared_extra_bytes: int,
) -> int:
    """``max(previous, ceiling + min(shared_extra, RAM_GUARD_REARM_MAX_EXTRA_BYTES))``.

    The threshold is BOUNDED by the 32 MiB cap and RATCHETED (never
    decreases).  The unbounded ``ceiling + shared_extra_bytes`` rule is
    WITHDRAWN — it made the effective threshold ≈4.825 GiB while the
    envelope reported ``limit_bytes = 3 GiB`` (a §3.5 honesty
    violation).
    """
    extra = min(max(int(shared_extra_bytes), 0), RAM_GUARD_REARM_MAX_EXTRA_BYTES)
    return max(int(previous_rearm_threshold_bytes), int(ceiling_bytes) + extra)


def confirm_due(
    summed_rss_bytes: int,
    rearm_threshold_bytes: int,
    last_confirm_rss_bytes: int,
) -> bool:
    """The confirm's growth governor (the B1 formula, verbatim).

    ``(summed_rss_bytes >= rearm_threshold_bytes) and
    (summed_rss_bytes - last_confirm_rss_bytes >= RAM_GUARD_REARM_MIN_GROWTH_BYTES)``
    """
    if summed_rss_bytes < rearm_threshold_bytes:
        return False
    return (summed_rss_bytes - last_confirm_rss_bytes) >= RAM_GUARD_REARM_MIN_GROWTH_BYTES


# ── The honest-peak bound (T7 / §4.4) ───────────────────────────────────────


def under_report_bound_bytes(sample_totals: Sequence[int]) -> int:
    """The per-sample under-report bound — the MAX POSITIVE sample-to-sample delta.

    Derived from the recorded samples themselves (no external input).
    """
    bound = 0
    for previous, current in zip(sample_totals, sample_totals[1:]):
        delta = current - previous
        if delta > bound:
            bound = delta
    return bound


def exposure_window_seconds(
    poll_interval_ms: int,
    confirm_deadline_ms: int,
    grace_ms: int,
) -> float:
    """The total-exposure window: ``Δt + confirm_deadline + grace``.

    The CONFIRM TERM IS ADDED (the previous form omitted it) — the
    window runs from the breach to all-dead, and the confirm sits
    inside it.
    """
    return (poll_interval_ms + confirm_deadline_ms + grace_ms) / 1000.0


def total_exposure_bound_bytes(
    per_sample_bound_bytes: int,
    poll_interval_ms: int,
    confirm_deadline_ms: int,
    grace_ms: int,
) -> int:
    """The total exposure bound: ``R·(Δt + confirm_deadline + grace)``.

    Computed from the empirical per-sample rate (the max positive
    sample-to-sample delta) scaled over the full exposure window — the
    runtime needs no knowledge of R.
    """
    if poll_interval_ms <= 0:
        return 0
    window = exposure_window_seconds(poll_interval_ms, confirm_deadline_ms, grace_ms)
    return int(per_sample_bound_bytes * (window / (poll_interval_ms / 1000.0)))


__all__ = [
    "LANE_BASH",
    "LANE_PROC",
    "RAM_GUARD_REARM_MAX_EXTRA_BYTES",
    "RAM_GUARD_REARM_MIN_GROWTH_BYTES",
    "STATE_BREACHED",
    "STATE_KILLED",
    "STATE_OVERCOUNTED",
    "STATE_REFUSED",
    "STATE_RELEASED",
    "STATE_WATCHING",
    "TERMINAL_STATES",
    "TRIGGER_PSS_CONFIRM_UNAVAILABLE",
    "TRIGGER_PSS_THRESHOLD",
    "TRIGGER_RSS_THRESHOLD",
    "TRIGGER_VALUES",
    "PeakRecord",
    "SpawnRecord",
    "breaches",
    "compute_rearm_threshold",
    "confirm_due",
    "exposure_window_seconds",
    "total_exposure_bound_bytes",
    "under_report_bound_bytes",
]
