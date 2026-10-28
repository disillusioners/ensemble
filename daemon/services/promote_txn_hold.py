"""Promote-transaction hold (soak-gate-mcp-warmup, A3).

A promote ceremony drives a burst of resumes through the daemon at the
exact moment the soak is observing it. The burst is the incident's
largest single contributor to pool saturation (``incident-20261026-db-
pool-idle-in-transaction.md``). A3 DEFERS that burst while a promote
transaction is live — it never DROPS it.

THE NORMATIVE PREDICATE lives in ``requirements.md`` §4.7 (P7); this
module implements it verbatim:

* **Promote-txn LIVE** = ``in_flight.kind == "promote"`` AND
  (**POST-BOOT** ``journal_owner_heartbeat_fresh`` is True OR
  (**BOOT WINDOW** the daemon's boot age < ``BOOT_LIVENESS_S`` AND
  ``in_flight.started_at`` is present AND
  ``now - in_flight.started_at < PROMOTE_TOTAL_DEADLINE_S``)).
* **RELEASE** requires ``in_flight`` to be **ABSENT** during the BOOT
  WINDOW (the hold is HOLD-only there) and TWO CONSECUTIVE FALSE
  predicate reads at least one driver tick (5 s) apart everywhere else.
  A single torn/unreadable ``state.json`` read returns False and MUST
  NOT release a mid-promote burst.
* The hold **DEFERS, never drops**; human-initiated resumes are NOT
  gated.

FAIL-OPEN POSTURE (council-adjudicated; NOT changed by the tidy round
— see advisory A6 in the lane's adjudication note): the predicate
FAILS OPEN where the alternative would starve the resume set — an
UNRESOLVABLE install root releases the hold, and a STALE heartbeat
releases it post-boot. The reason is the asymmetry of the two
failures: a stuck hold starves resumes (the worse failure), while the
fail-open exposure is bounded by the TWO-CONSECUTIVE-FALSE-reads
hysteresis and the driver's 5 s tick — a single torn/unreadable read
does NOT release. One read DOES release in the fail-open cases, and
that is the disclosed, accepted trade-off.

READ-ONLY BY CONSTRUCTION: this module only READS
``<install_dir>/releases/state.json``. It never writes, never splices
and never locks the journal (the read-only pin asserts zero journal
writes across hold ticks).

The vocabulary is the EXISTING refusal reason ``"pipeline-busy"``
(``daemon/tools/upgrade_tools.py``) — no new concept is invented.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

#: The ``in_flight.kind`` value the hold reacts to.
PROMOTE_TXN_KIND = "promote"

#: The daemon-boot liveness window (§4.7 P7). It exceeds the observed
#: ~75 s boot-to-first-green AND the 60 s owner-heartbeat freshness
#: window, and stays far inside the 900 s ceremony envelope.
BOOT_LIVENESS_S: float = 120.0

#: The ceremony's outer bound — a promote txn older than this is not
#: considered live by the boot-window leg (§4.7 P7).
PROMOTE_TOTAL_DEADLINE_S: float = 900.0

#: The release driver's cadence — the hold re-consults the predicate
#: this often WHILE deferrals are outstanding (and never otherwise).
RELEASE_DRIVER_TICK_S: float = 5.0

#: The release HYSTERESIS: two consecutive false predicate reads, at
#: least one driver tick apart (§4.7 P7). A single torn read must never
#: release a mid-promote burst.
RELEASE_HYSTERESIS_READS: int = 2

#: The bounded release timeout — the CRASH BACKSTOP for a driver that
#: dies with deferrals outstanding. It is deliberately longer than the
#: ceremony's own deadline so a healthy promote is never raced by it.
RELEASE_TIMEOUT_S: float = 900.0

#: The refusal reason token (the EXISTING vocabulary).
HOLD_REASON = "pipeline-busy"

#: The seam names. One per dispatch site (requirements.md §3-A3 — FOUR
#: seams).
SEAM_BOOT_CONTINUE = "boot_continue"
SEAM_RUNTIME_RESUME = "runtime_resume"
#: SEAM_ELIGIBLE_PENDING deliberately does NOT defer: the eligible-pending
#: sweep SKIPS statelessly while the txn is live (decisions §9 Q3 — the
#: next 90s tick retries; the sweep owns no deferral rows). The constant
#: is kept to name the fourth seam; no handler is registered for it.
SEAM_ELIGIBLE_PENDING = "eligible_pending"
SEAM_ORPHAN_SWEEP = "orphan_sweep"

#: This process's start — the daemon's boot age. Captured at IMPORT
#: time (the module is imported during daemon startup), so the boot
#: window is measured from the daemon's own start, not from the
#: promote's.
_PROCESS_START_EPOCH = time.time()

#: Test/ops seam — override the daemon boot age (seconds). ``None``
#: means "use the real process start".
_boot_age_override: Optional[float] = None

#: Test/ops seam — override the install root (a scratch dir in tests).
#: ``None`` means "resolve it from the SELF-ENV marker".
_install_dir_override: Optional[Path] = None


@dataclass
class DeferredCandidate:
    """One DEFERRED resume (the hold's unit of work).

    ``seam`` — which dispatch seam deferred it.
    ``payload`` — the seam-specific resume parameters, captured verbatim
    so the release can re-issue the ORIGINAL action (the
    checkpoint-resume path — never a cold dispatch).
    ``deferred_at`` — the wall-clock stamp of the deferral (epoch
    seconds); the bounded release timeout is measured from it.
    """

    seam: str
    payload: dict[str, Any] = field(default_factory=dict)
    deferred_at: float = 0.0


# ── module state ──────────────────────────────────────────────────────
_DEFERRED: dict[str, list[DeferredCandidate]] = {}
_COUNTERS: dict[str, int] = {}
_RELEASE_HANDLERS: dict[str, Callable[[list[DeferredCandidate]], Awaitable[Any]]] = {}
_DRIVER_TASK: Optional[asyncio.Task] = None
_FALSE_READS: int = 0


# ── the predicate (requirements.md §4.7 P7) ───────────────────────────


def _resolve_install_root() -> Optional[Path]:
    """Resolve the install root whose ``releases/state.json`` is read.

    Reuses the EXISTING resolution machinery
    (``daemon/tools/upgrade_tools.py``): ``_resolve_install_dir`` (the
    SELF-ENV topology) with ``_canonical_install_root`` as the
    fallback. The imports are LAZY so this module never creates an
    import cycle with the tools package.
    """
    if _install_dir_override is not None:
        return _install_dir_override
    try:
        from daemon.tools.upgrade_tools import (
            _canonical_install_root,
            _resolve_install_dir,
            _self_env_marker,
        )
    except Exception:  # noqa: BLE001 — the tools package is optional
        return None
    try:
        install_dir = _resolve_install_dir(_self_env_marker())
    except Exception:  # noqa: BLE001 — defensive: never raise from here
        install_dir = None
    if install_dir is not None:
        return install_dir
    try:
        return _canonical_install_root("agents-ensemble")
    except Exception:  # noqa: BLE001 — defensive
        return None


def _read_state(install_dir: Any) -> Optional[dict[str, Any]]:
    """READ-ONLY, torn-safe read of ``<install_dir>/releases/state.json``.

    Returns the parsed dict, or ``None`` when the journal is absent /
    torn / unreadable. NEVER writes, NEVER splices, NEVER locks.
    """
    try:
        from daemon.tools.upgrade_tools import _journal_read
    except Exception:  # noqa: BLE001 — the tools package is optional
        return None
    try:
        data, status = _journal_read(install_dir)
    except Exception:  # noqa: BLE001 — a torn read must never raise
        return None
    if status != "ok" or not isinstance(data, dict):
        return None
    return data


def _heartbeat_fresh(install_dir: Any) -> bool:
    """The POST-BOOT heartbeat leg — ``journal_owner_heartbeat_fresh``.

    READ-ONLY accessor (``daemon/tools/upgrade_journal.py``); it returns
    False on a torn / unreadable journal (fail-closed).
    """
    try:
        from daemon.tools.upgrade_journal import journal_owner_heartbeat_fresh
    except Exception:  # noqa: BLE001 — the accessor is optional; a missing leg reads as stale
        return False
    try:
        return bool(journal_owner_heartbeat_fresh(install_dir))
    except Exception:  # noqa: BLE001 — defensive: a torn heartbeat read must never raise
        return False


def daemon_boot_age_s() -> float:
    """The daemon's boot age in seconds (never negative).

    Both paths return a float: the override seam coerces, and the real
    path clamps at 0.0 — there is no "unknown" case.
    """
    if _boot_age_override is not None:
        return float(_boot_age_override)
    return max(0.0, time.time() - _PROCESS_START_EPOCH)


def _parse_started_at(value: Any) -> Optional[float]:
    """Parse ``in_flight.started_at`` (the shell's ISO-8601 UTC stamp).

    Returns epoch seconds, or ``None`` when the field is absent or
    unparseable (the boot-window leg then does NOT apply — fail-closed).
    """
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def evaluate_promote_txn() -> tuple[bool, bool]:
    """Evaluate the §4.7 predicate from ONE read.

    Returns ``(promote_txn_live, release_authorized)``:

    * ``promote_txn_live`` — the LIVE predicate (post-boot heartbeat leg
      OR boot-window leg).
    * ``release_authorized`` — whether a flip-to-false may RELEASE the
      deferred candidates:

      - ``in_flight`` ABSENT (or not a promote txn) ⇒ True (nothing to
        hold);
      - during the BOOT WINDOW ⇒ **False** (the hold is HOLD-only
        there: release requires ``in_flight`` ABSENT);
      - post-boot ⇒ True when the heartbeat is STALE (fail-open-on-stale:
        a crashed promote releases rather than freezing the resume set).

    A torn / unreadable read yields ``(False, False)`` — never a
    release on a single bad read (the two-read hysteresis is the second
    line of defence).

    FAIL-OPEN POSTURE (council-adjudicated; NOT changed by the tidy
    round — advisory A6 in the lane's adjudication note). The
    predicate is fail-open where the alternative would starve the
    resume set, and fail-closed on a single torn read:

    * an UNRESOLVABLE install root ⇒ ``(False, True)`` — the hold
      RELEASES. Fail-open is deliberate: an unreadable journal must
      not hold resumes indefinitely (a stuck hold starves the resume
      set — the worse failure). The two-read hysteresis and the
      driver's 5 s tick bound the exposure.
    * a TORN / unreadable ``state.json`` ⇒ ``(False, False)`` — that
      ONE read does not release; the release needs TWO CONSECUTIVE
      FALSE reads at least one driver tick apart.
    * a STALE heartbeat post-boot ⇒ release authorized (fail-open on
      stale: a crashed promote releases rather than freezing resumes).
    """
    install_dir = _resolve_install_root()
    if install_dir is None:
        return False, True
    state = _read_state(install_dir)
    if state is None:
        return False, False
    in_flight = state.get("in_flight")
    if not isinstance(in_flight, dict):
        # in_flight ABSENT ⇒ nothing is held.
        return False, True
    if in_flight.get("kind") != PROMOTE_TXN_KIND:
        # A non-promote txn (e.g. kind="restart") does not gate resumes.
        return False, True

    heartbeat_fresh = _heartbeat_fresh(install_dir)
    boot_age = daemon_boot_age_s()
    in_boot_window = boot_age < BOOT_LIVENESS_S
    started_at = _parse_started_at(in_flight.get("started_at"))

    live = False
    if heartbeat_fresh:
        live = True
    elif (
        in_boot_window
        and started_at is not None
        and (time.time() - started_at) < PROMOTE_TOTAL_DEADLINE_S
    ):
        live = True

    if in_boot_window:
        # BOOT WINDOW ⇒ HOLD-only: release requires in_flight ABSENT.
        release_authorized = False
    else:
        # Post-boot: the heartbeat leg governs (fail-open-on-stale).
        release_authorized = not heartbeat_fresh
    return live, release_authorized


def promote_txn_live() -> bool:
    """Is a promote transaction live right now? (§4.7 P7).

    The single predicate every dispatch seam consults.
    """
    live, _release = evaluate_promote_txn()
    return live


# ── the deferral registry ─────────────────────────────────────────────


def defer_resume(seam: str, payload: Optional[dict[str, Any]] = None) -> DeferredCandidate:
    """DEFER a resume candidate for the given seam.

    The candidate is PARKED (never dropped) and the release driver is
    started lazily. The caller must have already decided to defer (the
    predicate said the txn is live).
    """
    candidate = DeferredCandidate(
        seam=seam,
        payload=dict(payload or {}),
        deferred_at=time.time(),
    )
    _DEFERRED.setdefault(seam, []).append(candidate)
    _COUNTERS[seam] = _COUNTERS.get(seam, 0) + 1
    logger.info(
        "[PROMOTE_HOLD] deferred seam=%s payload_keys=%s reason=%s",
        seam, sorted(candidate.payload.keys()), HOLD_REASON,
    )
    ensure_driver()
    return candidate


def pending_count(seam: Optional[str] = None) -> int:
    """How many candidates are currently deferred (all seams or one)."""
    if seam is not None:
        return len(_DEFERRED.get(seam, []))
    return sum(len(v) for v in _DEFERRED.values())


def hold_snapshot() -> dict[str, Any]:
    """The hold's OBSERVABLE counters (published in the readyz detail).

    ``{deferred, per_seam, counters, reason, driver_running,
    false_reads}`` — journal-free and in-process, so an operator can
    see that resumes were DEFERRED rather than lost.
    """
    return {
        "deferred": pending_count(),
        "per_seam": {seam: len(v) for seam, v in _DEFERRED.items() if v},
        "counters": dict(_COUNTERS),
        "reason": HOLD_REASON,
        "driver_running": bool(
            _DRIVER_TASK is not None and not _DRIVER_TASK.done()
        ),
        "false_reads": int(_FALSE_READS),
    }


def register_release_handler(
    seam: str,
    handler: Callable[[list[DeferredCandidate]], Awaitable[Any]],
) -> None:
    """Register the seam's RELEASE handler.

    The handler re-issues the seam's ORIGINAL action (the
    checkpoint-resume path — never a cold dispatch). Registering is
    idempotent (the last registration wins).
    """
    _RELEASE_HANDLERS[seam] = handler


# ── the release driver ────────────────────────────────────────────────


def ensure_driver() -> Optional[asyncio.Task]:
    """Lazily create the release driver (the first deferral starts it).

    The driver is MODULE-HELD: a daemon that never defers never creates
    a task and never pays the poll. It ticks ONLY while deferrals are
    outstanding and is cancelled once the lists are empty.
    """
    global _DRIVER_TASK
    if _DRIVER_TASK is not None and not _DRIVER_TASK.done():
        return _DRIVER_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (a sync caller) — the driver is started by the
        # next deferral that happens on the loop.
        return None
    _DRIVER_TASK = loop.create_task(_driver_loop())
    return _DRIVER_TASK


async def _driver_loop() -> None:
    """The release driver: re-consult the predicate on a bounded cadence.

    * ticks every ``RELEASE_DRIVER_TICK_S`` while deferrals are
      outstanding;
    * releases on TWO CONSECUTIVE FALSE predicate reads (the §4.7
      hysteresis) AND a release-authorized state;
    * releases a candidate whose bounded release timeout expired (the
      CRASH BACKSTOP) even if the predicate never flipped;
    * returns (and is therefore cancelled/completed) once the deferred
      lists are empty.
    """
    global _FALSE_READS
    try:
        while True:
            await asyncio.sleep(RELEASE_DRIVER_TICK_S)
            if pending_count() == 0:
                return
            live, release_authorized = evaluate_promote_txn()
            if live:
                _FALSE_READS = 0
                continue
            _FALSE_READS += 1
            if _FALSE_READS >= RELEASE_HYSTERESIS_READS and release_authorized:
                await release_deferred()
                if pending_count() == 0:
                    return
                _FALSE_READS = 0
                continue
            if not release_authorized:
                # BOOT WINDOW ⇒ HOLD-only. Keep holding; do not consume
                # the hysteresis (a later release still needs two
                # consecutive false reads).
                _FALSE_READS = 0
                continue
            # The bounded release timeout (crash backstop).
            expired = _expired_candidates()
            if expired:
                await _release_candidates(expired)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a driver crash must be loud, not silent
        logger.exception(
            "[PROMOTE_HOLD] release driver crashed — deferrals remain "
            "parked; the bounded release timeout is the backstop"
        )


def _expired_candidates() -> list[DeferredCandidate]:
    """The deferred candidates whose bounded release timeout expired."""
    now = time.time()
    expired: list[DeferredCandidate] = []
    for candidates in _DEFERRED.values():
        for cand in candidates:
            if now - cand.deferred_at >= RELEASE_TIMEOUT_S:
                expired.append(cand)
    return expired


async def _release_candidates(candidates: list[DeferredCandidate]) -> None:
    """Release a specific set of candidates through their OWN seams.

    The lane's contract is "DEFERS, never drops", and this function
    honours it for every RELEASE that was actually ATTEMPTED:

    * the removal is by IDENTITY (``id()``), never by VALUE equality —
      ``DeferredCandidate`` is a dataclass, so two candidates with the
      same fields compare EQUAL and a value-based ``not in`` could drop
      the WRONG one;
    * a candidate whose seam handler RAISES is RETAINED (requeued) and
      ERROR-logged — a failed release is retried on the next driver
      pass, never silently abandoned.

    ONE disclosed exception remains (advisory A8 in the lane's
    adjudication note): a seam with NO registered handler cannot be
    released at all, so its candidates are dropped — LOUDLY, at
    WARNING. That case is not a "failed release" but a "no release
    path"; retaining them forever would park the resumes without
    bound while the driver never drains.
    """
    by_seam: dict[str, list[DeferredCandidate]] = {}
    for cand in candidates:
        by_seam.setdefault(cand.seam, []).append(cand)
    for seam, seam_candidates in by_seam.items():
        remaining = _DEFERRED.get(seam, [])
        # Remove by IDENTITY (F4b) — a value-based ``not in`` would
        # treat two field-equal candidates as the same one.
        releasing_ids = {id(c) for c in seam_candidates}
        keep = [c for c in remaining if id(c) not in releasing_ids]
        _DEFERRED[seam] = keep
        handler = _RELEASE_HANDLERS.get(seam)
        if handler is None:
            logger.warning(
                "[PROMOTE_HOLD] no release handler for seam=%s — %d "
                "candidates could not be released (they are dropped, NOT "
                "silently re-dispatched) [disclosed advisory A8]",
                seam, len(seam_candidates),
            )
            continue
        try:
            await handler(seam_candidates)
            logger.info(
                "[PROMOTE_HOLD] released %d candidate(s) through seam=%s",
                len(seam_candidates), seam,
            )
        except Exception:  # noqa: BLE001 — one seam's failure never blocks another
            logger.exception(
                "[PROMOTE_HOLD] release handler for seam=%s failed — the "
                "candidate(s) are RETAINED for the next driver pass (the "
                "hold DEFERS, never drops)",
                seam,
            )
            # F4a: requeue the candidates the failing handler did not
            # release — a failed release must be RETRIED, never dropped.
            retained = _DEFERRED.setdefault(seam, [])
            retained.extend(seam_candidates)


async def release_deferred() -> None:
    """Release EVERY deferred candidate through its original seam."""
    candidates = [c for values in _DEFERRED.values() for c in values]
    if not candidates:
        return
    await _release_candidates(candidates)


async def shutdown() -> None:
    """Cancel the release driver (the daemon's ordered-shutdown path).

    A shutdown mid-hold cancels the driver deterministically instead of
    leaking a task. The deferred candidates stay parked — the daemon is
    going away, and the next boot's pass owns them.
    """
    global _DRIVER_TASK
    task = _DRIVER_TASK
    _DRIVER_TASK = None
    if task is None:
        return
    try:
        task.cancel()
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("[PROMOTE_HOLD] release driver shutdown failed")


# ── test/ops seams ───────────────────────────────────────────────────


def set_install_dir_override(path: Optional[Path]) -> None:
    """Override the install root (a scratch dir in tests)."""
    global _install_dir_override
    _install_dir_override = path


def set_boot_age_override(age_s: Optional[float]) -> None:
    """Override the daemon's boot age (seconds)."""
    global _boot_age_override
    _boot_age_override = age_s


def reset_state() -> None:
    """Reset the module state (test seam) — clears the deferrals."""
    global _FALSE_READS
    _DEFERRED.clear()
    _COUNTERS.clear()
    _FALSE_READS = 0
