"""Engine honest state machine (Engine Arc P2 — T2.1).

The transition table is DATA (a dict of allowed transitions) and
``stamp_terminal`` is the ONE write path that stamps ``engine_jobs``
(``last_run_at`` / ``last_outcome`` / ``last_skip_reason``) AND appends
exactly one ``engine_runs`` row. A skip that bypasses the stamp is the
defect class this module exists to prevent (AC1 mutation pin).

Owner: T2.1 of ``.agents/shared/planning/engine-arc-phase1/phase2-plan.md``.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from daemon.repositories.engine.models import (
    EngineJob,
    EngineRun,
    OUTCOME_FAILED_BACKOFF,
    OUTCOME_SKIPPED_PREFIX,
    OUTCOME_SUCCEEDED,
    OUTCOME_TIMED_OUT,
)
from daemon.repositories.engine.repository import EngineRepository

logger = logging.getLogger(__name__)

# Closed skip-reason vocabulary (D2.6). A free-text reason folds to a
# declared member — never an unbounded label.
SKIP_REASONS: frozenset[str] = frozenset(
    {
        "IDLE_GATED",
        "KILL_SWITCHED",
        "DEP_MISSING",
        "SHADOW",
        "OVERLAP",
        "TIMEOUT_PENDING_EXTEND",
        "ERROR_BACKOFF",
        "NOT_DUE",
        "REREGISTERED",
    }
)

# Idle-gate backoff (D2.9). Only IDLE_GATED / DEP_MISSING skips count
# toward the consecutive-skip streak that doubles the effective interval.
IDLE_GATE_SKIP_REASONS: frozenset[str] = frozenset({"IDLE_GATED", "DEP_MISSING"})

# Backoff policy constants (engine-owned; NOT an estate cadence — D2.16).
SKIP_BACKOFF_CAP_FACTOR: int = 8

# Named constants for the dual spelling (W-? fold): the skip REASON token and
# the resulting outcome string. Today both spellings collapse to the same
# token ("TIMEOUT_PENDING_EXTEND"); the constants keep the family in one place.
REASON_TIMEOUT_PENDING_EXTEND: str = "TIMEOUT_PENDING_EXTEND"
OUTCOME_TIMEOUT_PENDING_EXTEND: str = "TIMEOUT_PENDING_EXTEND"
SKIP_BACKOFF_START_AFTER: int = 1

# Allowed transitions (INFORMATIONAL / non-enforcing — W-1). The table documents
# the intended lifecycle; ``stamp_terminal`` does NOT gate on it (a hard
# gate would risk stranding a job whose state drifted). ``is_allowed_transition``
# is provided for callers/tests that want to assert.
def _skipped_states() -> frozenset[str]:
    return frozenset(f"SKIPPED_{r}" for r in SKIP_REASONS)


ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "IDLE": frozenset({"DUE"}),
    "DUE": frozenset({"RUNNING"}),
    "RUNNING": frozenset({"SUCCEEDED", "FAILED_BACKOFF", "TIMED_OUT"})
    | _skipped_states(),
}
# Every terminal state may return to IDLE (a job is re-evaluated next tick).
for _terminal in set(("SUCCEEDED", "FAILED_BACKOFF", "TIMED_OUT")) | _skipped_states():
    ALLOWED_TRANSITIONS[_terminal] = frozenset({"IDLE"})


def is_allowed_transition(from_state: str, to_state: str) -> bool:
    """Whether ``from_state -> to_state`` is an allowed transition."""
    return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())


def normalize_skip_reason(reason: str) -> str:
    """Fold a free-text reason to a declared D2.6 member (checkpoint_metrics fold)."""
    if reason in SKIP_REASONS:
        return reason
    # case-insensitive exact
    upper = reason.upper()
    if upper in SKIP_REASONS:
        return upper
    # prefix match on the outcome form
    if upper.startswith("SKIPPED_"):
        inner = upper[len("SKIPPED_"):]
        if inner in SKIP_REASONS:
            return inner
    # substring containment (last resort) — folds to a declared member
    for r in SKIP_REASONS:
        if r in upper:
            return r
    # default fold
    return "ERROR_BACKOFF"


def skip_outcome(reason: str) -> str:
    return f"{OUTCOME_SKIPPED_PREFIX}{normalize_skip_reason(reason)}"


@dataclasses.dataclass
class TerminalStamp:
    """All inputs needed to stamp one terminal transition."""

    job_id: str
    service_tag: Optional[str]
    started_at: str
    finished_at: str
    outcome: str
    skip_reason: Optional[str] = None
    duration_seconds: Optional[float] = None
    attempt: int = 1
    error_class: Optional[str] = None
    error_digest: Optional[dict] = None
    boot_id: Optional[str] = None
    host_pid: Optional[int] = None
    consecutive_skips: Optional[int] = None
    dead: Optional[int] = None


def stamp_terminal(repository: EngineRepository, stamp: TerminalStamp) -> Optional[EngineJob]:
    """The ONE write path: append one ``engine_runs`` row + stamp ``engine_jobs``.

    Order: append the run row FIRST (durable claim record), then stamp the
    mutable job row. A failure between the two leaves the audit row present
    and the job row stale — the tick logs ERROR and continues (D2.12).
    """
    run = EngineRun(
        job_id=stamp.job_id,
        service_tag=stamp.service_tag,
        started_at=stamp.started_at,
        finished_at=stamp.finished_at,
        outcome=stamp.outcome,
        skip_reason=stamp.skip_reason,
        duration_seconds=stamp.duration_seconds,
        attempt=stamp.attempt,
        error_class=stamp.error_class,
        error_digest=stamp.error_digest,
        boot_id=stamp.boot_id,
        host_pid=stamp.host_pid,
    )
    # D2.12: each write is independently diagnosed. A failure logs a
    # dedicated ERROR naming the job and the write class, then re-raises so
    # the caller's per-job handler still converts the tick to FAILED_BACKOFF.
    try:
        repository.append_run(run)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "engine terminal-write FAILED: job_id=%s write_class=engine_runs_append error=%s: %s",
            stamp.job_id,
            type(exc).__name__,
            exc,
        )
        raise
    try:
        return repository.stamp_job_outcome(
            stamp.job_id,
            last_outcome=stamp.outcome,
            last_skip_reason=stamp.skip_reason,
            consecutive_skips=stamp.consecutive_skips,
            last_run_at=stamp.finished_at,
            dead=stamp.dead,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "engine terminal-write FAILED: job_id=%s write_class=engine_jobs_stamp error=%s: %s",
            stamp.job_id,
            type(exc).__name__,
            exc,
        )
        raise


def effective_interval_seconds(cache: dict, spec: Any) -> float:
    """Effective due interval with idle-skip backoff (D2.9)."""
    base = 1.0
    trigger = getattr(spec, "trigger", None)
    if trigger is not None and getattr(trigger, "seconds", None) is not None:
        try:
            base = float(trigger.seconds)
        except (TypeError, ValueError):
            base = 1.0
    cs = int((cache or {}).get("consecutive_skips", 0) or 0)
    if cs < SKIP_BACKOFF_START_AFTER:
        return float(base)
    multiplier = 1
    for _ in range(cs):
        multiplier *= 2
        if multiplier >= SKIP_BACKOFF_CAP_FACTOR:
            multiplier = SKIP_BACKOFF_CAP_FACTOR
            break
    return float(base) * float(multiplier)


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ALLOWED_TRANSITIONS",
    "IDLE_GATE_SKIP_REASONS",
    "SKIP_BACKOFF_CAP_FACTOR",
    "SKIP_BACKOFF_START_AFTER",
    "SKIP_REASONS",
    "TerminalStamp",
    "effective_interval_seconds",
    "is_allowed_transition",
    "normalize_skip_reason",
    "now_iso",
    "parse_iso",
    "skip_outcome",
    "stamp_terminal",
]
