"""Blueprint pending-lease reaper (C3 lease enforcement).

Why this module exists
----------------------
``BlueprintPendingRepository.mark_retryable`` (the C3 lease-timeout
transition) had NO production caller: the 30-minute lease was
documented in the agent contract but never enforced, so a blueprinter
that crashed (or was killed) between ``blueprint_claim_pending`` and
``blueprint_acknowledge_pending`` left its batch stranded in status
``claimed`` FOREVER — invisible to every detector. Measured on the live
DB: 344 rows on one project sat ``claimed`` across 7 runs between
2026-08-25 and 2026-11-01 (21-90 days old against a 30-minute lease)
with no blueprinter instance, task or job alive to hold any of them.

This module is the missing caller: a periodic sweep that reclaims
expired claims so the queue converges after a crash-after-claim.

The design decision (why there is NO owner-terminal conjunct)
-------------------------------------------------------------
The C3 contract is "claim a batch, process, acknowledge the batch"
(``agents/blueprinter/rule.md`` rule 6, ``agents/blueprinter/
tools_note.md`` "Pending-Batch Contract"): "Without this call, the
records stay claimed and would be re-claimed after the lease timeout."
The LEASE IS the ownership mechanism — the contract has no heartbeat
and no renewal, so an unacknowledged claim is *by design* re-claimable
once the lease expires. This reaper therefore implements the
contract's documented semantics (option (a)), NOT an owner-terminal
conjunct (option (b)). The measured reasons:

1. **There is no owner path to resolve.** The pending-queue claim token
   is minted fresh inside the claim tool (``daemon/tools/blueprint.py``
   ``blueprint_claim_pending`` → ``run_token = str(uuid.uuid4())``).
   The ``run_token`` stored on a JOB row is a DIFFERENT token — the
   coordinator's build-lease token (``daemon/services/
   blueprint_trigger_coordinator.py`` → ``"run_token": run_token`` in
   the trigger metadata). The two namespaces are disjoint: measured on
   the live DB, all 7 stranded claim tokens matched ZERO
   ``job_queue_items.metadata->>'run_token'`` rows. Building a real
   owner path would require changing the claim path and/or the
   pending-row schema — both out of scope.

2. **The contract's 30-minute default is shorter than a real run.**
   Measured over 68 acknowledged production runs: p50 14m27s, p90
   53m02s, p99 1h36m44s, max 2h38m25s — 32% of runs exceed 30
   minutes. A bare 30-minute lease would requeue LIVE claims. Measured
   against the drain that was in flight during this work (242 claimed
   rows, 6 live blueprinter instances): a 30-minute lease would have
   requeued 240 of those 242 live rows; the default lease used here
   (180 minutes) requeues 0.

3. **The quiescence guard already answers "is the owner alive?"** — at
   system granularity. This sweep is registered on the SAME
   ``MaintenanceService`` registry as every other sweep, and that
   registry is idle-gated: the job only runs when no job row has a
   non-terminal instance, no non-deferred task row is in flight, and no
   LLM request is active. A blueprinter running under a job holds an
   ``active`` job row whose linked instance is non-terminal, so the
   registry reports BUSY and the sweep is skipped. The system-wide
   quiescence signal is the in-scope stand-in for the per-claim owner
   lookup that option (b) would need.

Knobs (env-direct, read on EVERY run — never cached at boot)
-------------------------------------------------------------
``BLUEPRINT_LEASE_REAPER_ENABLED``
    Kill-switch. Default **ON** (see the module constant). Read per
    run so an operator can flip it without a daemon restart.
``BLUEPRINT_LEASE_REAPER_LEASE_MINUTES``
    Lease timeout the reaper enforces. Default **180.0** minutes (see
    ``DEFAULT_REAPER_LEASE_MINUTES`` for the measurement behind it).
``BLUEPRINT_LEASE_REAPER_MAX_RETRIES``
    Retry cap for the dead-letter path. Default **3**
    (``DEFAULT_MAX_RETRIES``).

Failure policy
--------------
The sweep NEVER raises into the maintenance loop: a failing sweep is
ERROR-logged and the next cycle retries it (the registry does not
advance ``last_run`` on a failed run). A sweep that exits permanently
is the anti-pattern — the opposite failure (rows stranded forever) is
what this module exists to remove.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ── Env knobs ────────────────────────────────────────────────────────

#: Kill-switch for the pending-lease reaper. Read from the process
#: environment on EVERY sweep (not cached at boot) so an operator can
#: disable the sweep without restarting the daemon.
BLUEPRINT_LEASE_REAPER_ENABLED_ENV = "BLUEPRINT_LEASE_REAPER_ENABLED"

#: Lease timeout (minutes) the reaper enforces.
BLUEPRINT_LEASE_REAPER_LEASE_MINUTES_ENV = "BLUEPRINT_LEASE_REAPER_LEASE_MINUTES"

#: Retry cap for the dead-letter (``abandoned``) path.
BLUEPRINT_LEASE_REAPER_MAX_RETRIES_ENV = "BLUEPRINT_LEASE_REAPER_MAX_RETRIES"


# ── Defaults ─────────────────────────────────────────────────────────

#: The sweep is ON by default. Rationale: the failure mode it removes
#: (rows stranded for 21-90 days, invisible to every detector) is
#: strictly worse than the failure mode it can introduce (a claim of a
#: run that outlives the lease is requeued and processed a second
#: time), and the two guards below keep the second failure mode out of
#: reach for every run observed in production (max 2h38m vs the 180
#: minute default lease) — plus the registry's idle gate skips the
#: sweep entirely while any blueprinter is working. A default-OFF
#: switch would leave the documented-but-unenforced lease unenforced
#: unless an operator knew to turn it on.
DEFAULT_REAPER_ENABLED = True

#: Lease timeout the reaper enforces by default.
#:
#: 180 minutes, NOT the contract's 30-minute default
#: (``DEFAULT_LEASE_TIMEOUT_MINUTES``): the measured p90 of a real
#: production run is 53 minutes and the measured max is 2h38m, so a
#: 30-minute lease would reclaim live claims (32% of observed runs
#: exceed it). 180 minutes is above every observed run with margin,
#: and still bounds a crashed claim's stranding to ~3 hours instead of
#: the 21-90 days measured on the live DB.
DEFAULT_REAPER_LEASE_MINUTES = 180.0

#: Retry cap for the ``abandoned`` dead-letter path.
DEFAULT_REAPER_MAX_RETRIES = 3

#: Cadence the sweep is registered with (minutes). The registry's own
#: check interval (``MAINTENANCE_CHECK_INTERVAL_MINUTES`` = 15) bounds
#: the effective firing rate from below, so the observed cadence is
#: ~15 minutes; the 5-minute registration keeps the job DUE well
#: inside the lease so a reclaim never waits a full lease period.
BLUEPRINT_LEASE_REAPER_INTERVAL_MINUTES = 5.0

#: Truthy strings that DISABLE the sweep when the kill-switch is set.
_FALSEY = frozenset({"0", "false", "no", "off", "disabled"})


def is_reaper_enabled() -> bool:
    """Return whether the sweep is enabled — read from the env NOW.

    The kill-switch is deliberately NOT cached: the value is read from
    ``os.environ`` on every call so an operator can flip it on a live
    daemon without a restart.
    """
    raw = os.environ.get(BLUEPRINT_LEASE_REAPER_ENABLED_ENV)
    if raw is None:
        return DEFAULT_REAPER_ENABLED
    value = raw.strip()
    if not value:
        return DEFAULT_REAPER_ENABLED
    return value.lower() not in _FALSEY


def resolve_lease_minutes(default: float = DEFAULT_REAPER_LEASE_MINUTES) -> float:
    """Resolve the lease timeout to enforce — read from the env NOW."""
    raw = os.environ.get(BLUEPRINT_LEASE_REAPER_LEASE_MINUTES_ENV)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r is not a number — using the default lease of %.1f minutes",
            BLUEPRINT_LEASE_REAPER_LEASE_MINUTES_ENV, raw, default,
        )
        return default
    if value <= 0:
        logger.warning(
            "%s=%r is not positive — using the default lease of %.1f minutes",
            BLUEPRINT_LEASE_REAPER_LEASE_MINUTES_ENV, raw, default,
        )
        return default
    return value


def resolve_max_retries(default: int = DEFAULT_REAPER_MAX_RETRIES) -> int:
    """Resolve the retry cap for the dead-letter path — read NOW."""
    raw = os.environ.get(BLUEPRINT_LEASE_REAPER_MAX_RETRIES_ENV)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r is not an integer — using the default cap of %d",
            BLUEPRINT_LEASE_REAPER_MAX_RETRIES_ENV, raw, default,
        )
        return default
    if value < 0:
        logger.warning(
            "%s=%r is negative — using the default cap of %d",
            BLUEPRINT_LEASE_REAPER_MAX_RETRIES_ENV, raw, default,
        )
        return default
    return value


class BlueprintLeaseReaper:
    """Periodic reaper for expired blueprint pending-queue claims.

    The sweep delegates the transition to
    :meth:`BlueprintPendingRepository.mark_retryable` — the repository
    method that already owns the C3 lease predicate and the retry-cap
    dead-letter path. This class adds no SQL of its own; it only
    decides WHEN to call it, with which lease timeout, and it never
    lets a failure escape into the maintenance loop.
    """

    def __init__(self, pending_repo: Any) -> None:
        """Bind the reaper to the pending-queue repository.

        Args:
            pending_repo: A ``BlueprintPendingRepository`` (or any
                object exposing ``mark_retryable(lease_timeout_minutes,
                max_retries)``).
        """
        self._pending_repo = pending_repo

    async def execute(self) -> int:
        """Run one sweep. Returns the number of claims transitioned.

        Never raises: a failing sweep is ERROR-logged and reported as
        ``0`` so the maintenance loop keeps running and the next cycle
        retries.
        """
        if not is_reaper_enabled():
            logger.debug(
                "blueprint_lease_reaper: disabled by %s — skipped",
                BLUEPRINT_LEASE_REAPER_ENABLED_ENV,
            )
            return 0

        lease_minutes = resolve_lease_minutes()
        max_retries = resolve_max_retries()

        try:
            moved = await asyncio.to_thread(
                self._pending_repo.mark_retryable, lease_minutes, max_retries,
            )
        except Exception as exc:  # noqa: BLE001 — a sweep must never raise
            logger.error(
                "blueprint_lease_reaper: sweep failed (lease=%.1f min, "
                "max_retries=%d): %s",
                lease_minutes, max_retries, exc, exc_info=True,
            )
            return 0

        moved = int(moved or 0)
        if moved:
            logger.info(
                "blueprint_lease_reaper: %d expired claim(s) reaped "
                "(lease=%.1f min, max_retries=%d)",
                moved, lease_minutes, max_retries,
            )
        else:
            logger.debug(
                "blueprint_lease_reaper: no expired claims (lease=%.1f min)",
                lease_minutes,
            )
        return moved


__all__ = [
    "BLUEPRINT_LEASE_REAPER_ENABLED_ENV",
    "BLUEPRINT_LEASE_REAPER_INTERVAL_MINUTES",
    "BLUEPRINT_LEASE_REAPER_LEASE_MINUTES_ENV",
    "BLUEPRINT_LEASE_REAPER_MAX_RETRIES_ENV",
    "BlueprintLeaseReaper",
    "DEFAULT_REAPER_ENABLED",
    "DEFAULT_REAPER_LEASE_MINUTES",
    "DEFAULT_REAPER_MAX_RETRIES",
    "is_reaper_enabled",
    "resolve_lease_minutes",
    "resolve_max_retries",
]
