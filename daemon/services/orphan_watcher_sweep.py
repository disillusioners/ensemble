"""Periodic orphan-watcher sweep — C2 systemic backstop (Batch C, 2026-09-11).

C2 (Batch C, 2026-09-11): ``DependencyBus._sweep_orphan_watchers``
was a one-shot startup-only sweep (called from
:meth:`DependencyBus.start` after the cache warm). Its job: cancel
PENDING watchers whose ``source_task_id`` no longer corresponds to an
active task (``running`` / ``pending`` / ``paused``), so the parent's
completion gate (``count_pending_for_target(parent) > 0``) doesn't
hold the parent in ``waiting_children`` forever. The bug class: a
child task force-cancelled or completed without the bus being
notified, leaving a stranded PENDING watcher.

The startup-only timing leaves a long gap: any orphan watcher that
accumulates AFTER startup (mid-run force-cancel, mid-run task
death) waits for a daemon restart to be swept. C2 closes that gap
with a periodic sweep that runs alongside the A3
``EligiblePendingSweepService`` — same cadence, same bounded-interval
asyncio-loop pattern. The startup sweep is preserved (so the
restart-window still gets the eager cleanup); the periodic sweep is
the structural backstop for the steady state.

**ALWAYS ON** (no env flag). Per the project owner's HARD POLICY
(Batch A spec, 2026-09-11): a kill-switch defaulting OFF that gates
a fix is an unacceptable deliverable. The sweep is ALWAYS-ON
infrastructure; the only tuning knob is the interval (default 90s,
mirroring A3's interval; the age predicate is on the bus DB table
itself — orphans are recognised by source_task_id being absent from
the active ``task`` set, not by an age threshold).

**Idempotent**: ``_sweep_orphan_watchers`` is a single atomic
conditional UPDATE — a second concurrent sweep on the same DB finds
zero work to do (the first sweep already transitioned the rows to
CANCELLED). The ``transition_state`` guarded ``WHERE state =
'PENDING'`` Core UPDATE on the bus side further ensures that a
delivered terminal event on a just-swept watcher is a safe no-op
(``rowcount == 0``).

Modeling: borrows the asyncio-task + cancel/await lifecycle
pattern from ``daemon/services/eligible_pending_sweep.py`` and the
``daemon/api.py`` lifespan wiring from
``_periodic_drift_reconcile_loop``. Sweep itself is a single
async method (``sweep_once()``) — bounded DB read, structured log.
NO new ``admission_state`` writers / JobItem creators / ``work_id``
mints; the census stays at 23/1/0.

Service vs lane: ``OrphanWatcherSweepService`` is intentionally a
FOCUSED small wrapper around the bus's
``_sweep_orphan_watchers``. It is not a "new top-level service" in
the sense the Batch C spec uses — it has one job (call
``bus._sweep_orphan_watchers()`` on a bounded interval), zero
admission-state writers, zero DB writes outside the bus's guarded
UPDATE, and the same shutdown contract as
``EligiblePendingSweepService``. It lives in
``daemon/services/orphan_watcher_sweep.py`` to keep the bus
implementation (``dependency_bus.py``) unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from daemon.services.dependency_bus import get_dependency_bus
from daemon.services.engine.dualpath import ownership_for

if TYPE_CHECKING:
    from daemon.services.dependency_bus import DependencyBus

logger = logging.getLogger(__name__)


# ── Engine Arc Phase-2 pilot: dual-path identity (wiring only) ────────────
# ``dualpath.py:31`` derives ``SERVICES_<SLUG>_USE_ENGINE`` from the slug.
# This slug is the estate row's ``service_tag`` (estate.py
# "orphan-watcher-sweep") and the P4 observer spec's ``kill_switch_key`` —
# one identity shared by the legacy construct site and the P4 boot block.
ORPHAN_WATCHER_ENGINE_SLUG: str = "orphan-watcher-sweep"
ORPHAN_WATCHER_ENGINE_JOB_ID: str = "orphan-watcher"
ORPHAN_WATCHER_ENGINE_FLAG: str = "SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE"


def engine_owns_execution(environ: Any = None) -> bool:
    """The ONE dual-path ownership decision, shared by both api.py sites.

    ``environ=None`` reads the live ``os.environ``; tests pass a dict.
    Unset / OFF ⇒ ``False`` (legacy loop owns) — the shipped dormant
    default. ON ⇒ the engine owns and the legacy loop is suppressed.

    NOTE (engine-core drift, reported not patched — engine/** is a Phase-1
    reviewed fence): ``dualpath.py:31`` derives the key as
    ``SERVICES_{slug.upper()}_USE_ENGINE`` WITHOUT normalizing hyphens, while
    ``boot.py:179`` (the spec's ``kill_switch_key``) DOES ``replace('-', '_')``.
    Passing the hyphenated estate slug raw would read a DIFFERENT variable
    than the engine's kill switch and could double-execute on a flag flip.
    We normalize here so BOTH paths read the one canonical flag.
    """
    normalized_slug = ORPHAN_WATCHER_ENGINE_SLUG.replace("-", "_")
    return ownership_for(normalized_slug, environ).engine_owns


def build_engine_executor(
    min_watcher_age_seconds: int | None = None,
) -> Callable[[], dict[str, Any]]:
    """Build the engine's DB_OFFLOAD ``execute_fn`` for the orphan pilot.

    MUST be a SYNC callable: ``dispatch.run_db_offload`` refuses coroutine
    functions (W-4 discipline — an async fn would silently defeat the
    off-loop guarantee). The bus method it bridges to is async, so the sync
    adapter runs it in a fresh event loop inside the engine's worker thread
    via ``asyncio.run``. ``DependencyBus._sweep_orphan_watchers`` is
    loop-agnostic (its DB write is already ``await asyncio.to_thread(...)``),
    so no loop-bound bus state crosses the seam.

    The bus is LATE-BOUND via ``get_dependency_bus()`` on EVERY tick — never
    captured at construction (the born-dead trap this migration exists to
    make impossible). The grace window is the SAME value the legacy service
    forwards (``ServicesConfig.orphan_watcher_sweep_grace_seconds``), so both
    paths apply the identical age predicate.

    Errors propagate to the engine's honest state machine (FAILED_BACKOFF);
    the legacy path swallows them into ``_errors_total``. The parity pack
    maps that difference explicitly (it is not a behavior divergence of the
    sweep itself).
    """
    # Resolved at CALL time (the default constant is defined below this block).
    grace = int(
        DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS
        if min_watcher_age_seconds is None
        else min_watcher_age_seconds
    )

    def _execute() -> dict[str, Any]:
        bus = get_dependency_bus()  # late bind, in the worker thread, every tick
        if bus is None:
            # DependencyRef gates this at due-time; a disappearance between
            # resolution and dispatch is a counted no-op, never an exception.
            return {
                "cancelled": 0,
                "bus_wired": 0,
                "grace_seconds": grace,
            }
        cancelled = int(
            asyncio.run(
                bus._sweep_orphan_watchers(min_watcher_age_seconds=grace)
            )
            or 0
        )
        return {
            "cancelled": cancelled,
            "bus_wired": 1,
            "grace_seconds": grace,
        }

    return _execute


# Default — mirrors ``eligible_pending_sweep.DEFAULT_SWEEP_INTERVAL_SECONDS``
# (90s, midpoint of the A3 brief range). The orphan sweep is on the
# bus DB (one atomic UPDATE per tick), not a hot path, so sharing
# the A3 cadence is the canonical wiring. Tuning knobs live on
# ``ServicesConfig`` so the config layer owns the resolution order.
DEFAULT_ORPHAN_SWEEP_INTERVAL_SECONDS: int = 90

# Default grace window — mirrors
# ``dependency_bus.DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS`` (W-C, 30s).
# Sized to comfortably exceed the worst-case commit→emit latency
# observed in production while bounding orphan-recovery latency by
# ``grace + sweep_interval`` (30s + 90s = 120s worst case).
DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS: int = 30


class OrphanWatcherSweepService:
    """Periodic orphan-watcher sweep — C2 systemic backstop.

    Each tick calls ``DependencyBus._sweep_orphan_watchers()`` which
    runs the single atomic conditional UPDATE that cancels PENDING
    watchers whose ``source_task_id`` no longer corresponds to an
    active task. The sweep is the steady-state companion to the
    startup-time ``DependencyBus.start()`` sweep — the startup sweep
    cleans the restart-window, the periodic sweep cleans the
    steady-state.

    The service does NOT write any task / job state — it only signals
    the bus. Census stays untouched (23/1/0). The bus's
    ``transition_state`` guarded UPDATE is the only write primitive
    the sweep invokes.

    Args:
        dependency_bus: ``DependencyBus`` (already wired via the
            module-level ``set_dependency_bus`` singleton). When
            ``None`` (legacy test fixture / pre-wiring lifespan), the
            sweep still ticks on each interval and emits a DEBUG log;
            no sweep is dispatched (the startup sweep in
            ``bus.start()`` is the primary path; the periodic lane
            is the secondary backstop).
        interval_seconds: How often the sweep runs. Default 90s
            (mirrors A3's brief range). Floor 1 to avoid spin;
            out-of-range values FAIL FAST AT BOOT via the pydantic
            ``Field(ge=1)`` constraint in ``ServicesConfig``.
        min_watcher_age_seconds: Grace window (W-C, 2026-09-11)
            forwarded to ``bus._sweep_orphan_watchers``. Watchers
            younger than this are protected from the sweep during
            the commit→emit window — the natural emit_terminal
            path needs the window to transition the watcher to
            FIRED before the sweep races it. Default
            ``DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS`` (30s). Floor 0
            (= disable grace — same as pre-W-C behavior). Mirrors
            the
            ``ServicesConfig.orphan_watcher_sweep_grace_seconds``
            knob.

    Lifecycle:
        * ``start()`` — spawn the asyncio task. Idempotent.
        * ``stop()`` — cancel + await the asyncio task. Safe to
          call when the task was never started (silent no-op).
    """

    def __init__(
        self,
        dependency_bus: "DependencyBus | None" = None,
        *,
        interval_seconds: int = DEFAULT_ORPHAN_SWEEP_INTERVAL_SECONDS,
        min_watcher_age_seconds: int = DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS,
    ) -> None:
        # NOTE (born-dead wiring fix, 2026-11-21): ``dependency_bus`` is
        # an EXPLICIT injection point for legacy test fixtures only.
        # The production lifespan (daemon/api.py) constructs this
        # service BEFORE ``init_dependency_bus`` registers the module
        # singleton, so capturing ``get_dependency_bus()`` HERE froze
        # ``None`` for the daemon's entire lifetime — the periodic
        # lane ticked forever on the bus-is-None branch ("born dead").
        # The tick path now LATE-BINDS: an explicitly injected bus
        # (fixture) wins; otherwise the module singleton is re-read on
        # EVERY tick via ``get_dependency_bus()`` and is picked up as
        # soon as ``init_dependency_bus`` wires it.
        self._dependency_bus = dependency_bus
        self._interval_seconds = max(1, int(interval_seconds))
        self._min_watcher_age_seconds = max(0, int(min_watcher_age_seconds))

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # Counters — surface via ``counters()`` for tests / observability.
        self._ticks_total: int = 0
        self._cancelled_total: int = 0
        self._errors_total: int = 0
        # Born-dead detection (2026-11-21): a tick where the bus was
        # resolved and the sweep actually dispatched counts as an
        # "effective run". Zero effective runs once the lane is past
        # its first two ticks (= 2x interval on the loop cadence)
        # means the lane has NEVER done its job — escalate loudly.
        self._effective_runs_total: int = 0
        self._dead_detection_fired: bool = False

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def start(self) -> None:
        """Spawn the periodic sweep as an asyncio task.

        Idempotent — calling ``start()`` while a task is already
        alive is a silent no-op. Mirrors
        ``EligiblePendingSweepService.start`` and
        ``WaitingChildrenWatchdog.run_waiting_children_watchdog_loop``.
        """
        if self._task is not None and not self._task.done():
            logger.debug(
                "OrphanWatcherSweepService: start() called while "
                "the sweep task is already alive — silent no-op"
            )
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="orphan-watcher-sweep",
        )
        logger.info(
            f"OrphanWatcherSweepService started: "
            f"interval={self._interval_seconds}s"
        )

    async def stop(self, timeout: float = 5.0) -> None:
        """Cancel + await the sweep task.

        Honest worst-case join budget: 5s — the loop body is a single
        ``bus._sweep_orphan_watchers()`` call (which itself has a
        bounded internal DB roundtrip via ``asyncio.to_thread``); an
        unhealthy tick is bounded by the cancel timeout. Politer
        shutdown: ``self._stop_event`` is set so an ``asyncio.sleep``
        between ticks exits promptly.
        """
        self._stop_event.set()
        if self._task is None:
            return
        try:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None
        logger.info("OrphanWatcherSweepService stopped")

    async def sweep_once(self) -> dict[str, int]:
        """One bounded sweep tick — public for tests + on-demand use.

        Returns:
            Structured counters dict:

            * ``ticks`` — total tick count including this one
            * ``cancelled`` — orphan PENDING watchers cancelled this
              tick (returned by the bus's atomic UPDATE)
            * ``errors`` — per-tick errors caught
            * ``cumulative_cancelled`` — running total
            * ``cumulative_errors`` — running total
        """
        self._ticks_total += 1
        cancelled = 0
        try:
            # Late-bind (born-dead wiring fix, 2026-11-21): an
            # explicitly injected bus (legacy test fixture) wins;
            # otherwise the module singleton is RE-READ at tick time —
            # never captured at construction (the api.py lifespan
            # constructs this service before ``init_dependency_bus``
            # registers the singleton).
            bus = self._dependency_bus
            if bus is None:
                bus = get_dependency_bus()
            if bus is None:
                # No bus wired — DEBUG log so the operator sees the
                # lane is alive but unconnected. The bus's own
                # ``start()`` sweep is the primary path; the
                # periodic lane is the secondary backstop that
                # depends on the bus singleton being live.
                logger.debug(
                    f"OrphanWatcherSweepService: tick "
                    f"{self._ticks_total}, but dependency_bus is "
                    f"not wired (legacy test fixture / pre-wiring "
                    f"lifespan) — relying on the startup sweep"
                )
                self._check_dead_detection()
            else:
                cancelled = int(
                    await bus._sweep_orphan_watchers(
                        min_watcher_age_seconds=(
                            self._min_watcher_age_seconds
                        ),
                    ) or 0
                )
                self._effective_runs_total += 1
                self._cancelled_total += cancelled
                if self._dead_detection_fired and (
                    self._effective_runs_total == 1
                ):
                    logger.info(
                        "OrphanWatcherSweepService: first effective "
                        f"sweep on tick {self._ticks_total} — the "
                        "lane recovered after dead-detection fired "
                        "(the bus singleton is wired now)"
                    )
                if cancelled > 0:
                    logger.info(
                        f"OrphanWatcherSweepService: tick "
                        f"{self._ticks_total} cancelled "
                        f"{cancelled} orphan PENDING watcher(s)"
                    )
        except Exception as tick_err:
            self._errors_total += 1
            logger.error(
                f"OrphanWatcherSweepService: tick "
                f"{self._ticks_total} failed: {tick_err}",
                exc_info=True,
            )

        return {
            "ticks": self._ticks_total,
            "cancelled": cancelled,
            "errors": self._errors_total,
            "cumulative_cancelled": self._cancelled_total,
            "cumulative_errors": self._errors_total,
            "effective_runs": self._effective_runs_total,
        }

    def counters(self) -> dict[str, int]:
        """Read-only view of the sweep counters (for tests / observability)."""
        return {
            "ticks": self._ticks_total,
            "cancelled_total": self._cancelled_total,
            "errors_total": self._errors_total,
            "effective_runs_total": self._effective_runs_total,
        }

    # --------------------------------------------------------
    # Born-dead detection (2026-11-21)
    # --------------------------------------------------------

    def _check_dead_detection(self) -> None:
        """Escalate LOUDLY when the lane has never done its job.

        Born-dead wiring trap (census A1, 2026-11-21): a service
        whose dependency is captured before the dependency exists
        ticked forever on the bus-is-None branch while its own
        health counters climbed — invisible at DEBUG level. This
        check fires an ERROR once the lane has ticked past its
        grace window with ZERO effective sweeps, then a WARNING
        reminder every 10 subsequent no-op ticks (rate-limited,
        never silent again).

        Grace window = 2x the interval: tick 1 is immediate (t=0),
        tick N lands at (N-1) x interval, so the check arms at tick
        3 (t = 2 x interval). The lifespan's ``init_dependency_bus``
        runs a few seconds after this service starts, so a healthy
        boot registers the bus long before the window closes.
        """
        if self._effective_runs_total > 0:
            return
        if self._ticks_total < 3:
            # Grace: < 2x interval elapsed since start — the wiring
            # window is still legitimately open.
            return
        if not self._dead_detection_fired:
            self._dead_detection_fired = True
            logger.error(
                "OrphanWatcherSweepService DEAD-DETECTION: "
                f"{self._ticks_total} tick(s) with ZERO effective "
                "sweeps — the dependency bus was NEVER resolved, so "
                "the periodic orphan-watcher backstop has done "
                "nothing since start(). Check the boot wiring "
                "assertion + init_dependency_bus failure in the "
                "startup log."
            )
        elif self._ticks_total % 10 == 0:
            logger.warning(
                "OrphanWatcherSweepService still dead: tick "
                f"{self._ticks_total}, zero effective sweeps "
                "(dependency bus never resolved)"
            )

    # --------------------------------------------------------
    # Internal — the asyncio loop
    # --------------------------------------------------------

    async def _run_loop(self) -> None:
        """Bounded-interval asyncio loop — cancel-safe.

        The loop body calls ``sweep_once()`` on each tick and sleeps
        ``interval_seconds`` between ticks. Sleep is interruptible
        via ``self._stop_event`` (polite shutdown via ``stop()``);
        ``asyncio.CancelledError`` is the hard-shutdown path.
        """
        try:
            while not self._stop_event.is_set():
                try:
                    await self.sweep_once()
                except asyncio.CancelledError:
                    raise
                except Exception as tick_err:
                    # ``sweep_once`` already swallows its own
                    # exceptions; this is a defense-in-depth catch
                    # so the loop never crashes on an unexpected
                    # shape (e.g., a future contributor raising
                    # SystemExit from a hook).
                    logger.error(
                        f"OrphanWatcherSweepService: unexpected "
                        f"loop error: {tick_err}",
                        exc_info=True,
                    )
                # Sleep — interruptible via ``stop_event.wait``
                # so a polite shutdown does not block on the full
                # interval.
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._interval_seconds,
                    )
                    # If wait_for returns without TimeoutError, the
                    # stop event was set — exit cleanly.
                    return
                except asyncio.TimeoutError:
                    # Expected — interval elapsed, loop iterates.
                    continue
        except asyncio.CancelledError:
            # Hard cancel — exit silently.
            return