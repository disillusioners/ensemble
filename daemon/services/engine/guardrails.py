"""Trap-impossibility guardrails (Engine Arc Phase 3 — T3.1, T3.6).

Two defect classes made mechanically impossible:

1. **Born-dead impossibility** — dependencies are declared as singleton
   getters (``Callable[[], T | None]``) and resolved by calling the
   getter AT THE MOMENT the job becomes due, on every attempt. A
   constructor capture (the live defect shape, ``api.py:1967-1971``) is
   structurally unavailable: there is no place to store a captured object.
   A job whose dependency resolves to ``None`` is ``SKIPPED_DEP_MISSING``
   (counted, every tick) and escalates to ``dead=true`` after ``2 ×
   interval`` with exactly ONE ERROR line (D3.1, D3.2, D3.4).

2. **Extend-wedge impossibility** — a ``timeout_policy=extend`` hold past
   ``2 × max_runtime_s`` escalates to ``dead=true`` + ONE WARNING (D3.10).

Plus the pure boot-wiring assertion ``assert_dependencies_resolvable``
(D3.3) that P4 calls after the last dependency-producing boot pass.

Nothing here touches ``orphan_watcher_sweep.py`` (F-10) or ``api.py``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

#: Consecutive DEP_MISSING duration (as a multiple of the effective interval)
#: before a job escalates to dead (D3.2).
DEP_MISSING_DEAD_FACTOR: int = 2

#: Extend-wedge escalation threshold (D3.10): hold longer than this multiple
#: of max_runtime_s.
EXTEND_WEDGE_FACTOR: int = 2


@dataclass
class DependencyRef:
    """A dependency declared as a singleton getter (D3.1).

    ``getter`` is called at the moment of due-ness on EVERY attempt — never
    at construction. Returning ``None`` means "not (yet) resolvable".
    """

    label: str
    getter: Callable[[], Any]

    def resolve(self) -> Any:
        try:
            return self.getter()
        except Exception:  # noqa: BLE001
            return None


@dataclass
class ResolveResult:
    """Outcome of resolving a set of dependency refs at due-time."""

    resolved: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing


def resolve_at_due(refs: Iterable[DependencyRef]) -> ResolveResult:
    """Call each getter NOW (at due-evaluation time). Never constructor capture.

    A spy on the getter proves N calls for N due-evaluations (AC1).
    """
    resolved: dict[str, Any] = {}
    missing: list[str] = []
    for ref in refs or []:
        try:
            value = ref.getter()
        except Exception:  # noqa: BLE001
            value = None
        if value is None:
            missing.append(ref.label)
        else:
            resolved[ref.label] = value
    return ResolveResult(resolved=resolved, missing=missing)


@dataclass
class WiringReport:
    """Pure report of unresolved dependencies: job_id -> list of labels (D3.3)."""

    unresolved: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.unresolved

    def summary(self) -> str:
        return "; ".join(
            f"{jid}: {labels}" for jid, labels in self.unresolved.items()
        )


def assert_dependencies_resolvable(
    specs_with_deps: Iterable[tuple[Any, Iterable[DependencyRef]]],
) -> WiringReport:
    """Pure function: for each (spec, refs), call getters now; record unresolved.

    No side effects (no DB, no logging, no mutation). P4 calls this after the
    last dependency-producing boot pass and logs ERROR per unresolved
    dependency + a summary line (D3.3, AC3).
    """
    report = WiringReport()
    for spec, refs in specs_with_deps or []:
        job_id = getattr(spec, "id", None)
        if job_id is None and isinstance(spec, str):
            job_id = spec
        job_id = str(job_id) if job_id is not None else ""
        refs = list(refs or [])
        missing: list[str] = []
        for ref in refs:
            try:
                value = ref.getter()
            except Exception:  # noqa: BLE001
                value = None
            if value is None:
                missing.append(ref.label)
        if missing:
            report.unresolved[job_id] = missing
    return report


# ── DEP_MISSING tracking + dead escalation ──────────────────────────────


class DepMissingTracker:
    """Tracks consecutive DEP_MISSING ticks per job and escalates to dead.

    State is RAM (per process). Escalation persists ``dead=true`` through
    the supplied persist callback (engine wiring). Logs exactly ONE ERROR
    per escalation; resets on a successful resolve (D3.2, D3.4).
    """

    def __init__(self) -> None:
        # job_id -> {"first_missing_at": float|None, "escalated": bool}
        self._state: dict[str, dict[str, Any]] = {}

    def record_missing(self, job_id: str, now_ts: float) -> None:
        st = self._state.setdefault(
            job_id, {"first_missing_at": None, "escalated": False}
        )
        if st["first_missing_at"] is None:
            st["first_missing_at"] = now_ts
        st["escalated"] = st.get("escalated", False)

    def record_resolved(self, job_id: str) -> None:
        st = self._state.get(job_id)
        if st is not None:
            st["first_missing_at"] = None
            st["escalated"] = False

    def should_escalate(self, job_id: str, interval: float, now_ts: float) -> bool:
        st = self._state.get(job_id)
        if st is None or st.get("escalated"):
            return False
        first = st.get("first_missing_at")
        if first is None:
            return False
        elapsed = now_ts - first
        if elapsed >= (DEP_MISSING_DEAD_FACTOR * interval):
            st["escalated"] = True
            return True
        return False

    def is_escalated(self, job_id: str) -> bool:
        st = self._state.get(job_id)
        return bool(st and st.get("escalated"))


def escalate_dead_log_once(
    job_id: str,
    escalated_set: set[str],
    *,
    level: str = "error",
    message: str = "",
) -> bool:
    """Log the escalation exactly once per job (tracked by ``escalated_set``).

    Returns True when a log was emitted, False when already escalated.
    """
    if job_id in escalated_set:
        return False
    escalated_set.add(job_id)
    if level == "warning":
        logger.warning(message or f"engine job {job_id} escalated to dead")
    else:
        logger.error(message or f"engine job {job_id} escalated to dead")
    return True


# ── Extend-wedge tracking ────────────────────────────────────────────────


class ExtendWedgeTracker:
    """Tracks extend-policy holds and escalates past ``2 × max_runtime_s``.

    Input is P2's per-tick ``TIMEOUT_PENDING_EXTEND`` stamps (D3.10): the
    caller records the hold start when the extend fire occurs, and checks
    each tick whether the hold has exceeded the threshold.
    """

    def __init__(self) -> None:
        # job_id -> {"started_at": float, "max_runtime_s": float, "escalated": bool}
        self._state: dict[str, dict[str, Any]] = {}

    def record_hold_start(self, job_id: str, now_ts: float, max_runtime_s: float) -> None:
        if job_id not in self._state or self._state[job_id].get("escalated"):
            self._state[job_id] = {
                "started_at": now_ts,
                "max_runtime_s": float(max_runtime_s),
                "escalated": False,
            }
        else:
            # keep earliest start
            if "started_at" not in self._state[job_id]:
                self._state[job_id]["started_at"] = now_ts

    def clear_hold(self, job_id: str) -> None:
        self._state.pop(job_id, None)

    def check_escalate(self, job_id: str, now_ts: float) -> bool:
        st = self._state.get(job_id)
        if st is None or st.get("escalated"):
            return False
        started = st.get("started_at")
        if started is None:
            return False
        max_runtime = float(st.get("max_runtime_s") or 1.0)
        if (now_ts - started) >= (EXTEND_WEDGE_FACTOR * max_runtime):
            st["escalated"] = True
            return True
        return False


__all__ = [
    "DEP_MISSING_DEAD_FACTOR",
    "EXTEND_WEDGE_FACTOR",
    "DepMissingTracker",
    "DependencyRef",
    "ExtendWedgeTracker",
    "ResolveResult",
    "WiringReport",
    "assert_dependencies_resolvable",
    "escalate_dead_log_once",
    "resolve_at_due",
]
