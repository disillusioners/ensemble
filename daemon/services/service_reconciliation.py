"""Service reconciliation — periodic liveness sweep for detached services.

**Phase 1.C SKELETON** (service-tool plan task 1.C.12). This module
ships the lifecycle shell — ``start`` / ``stop`` / ``sweep_once`` —
modeled on :class:`daemon.services.eligible_pending_sweep.EligiblePendingSweepService`
(the canonical template) with the ``JobLockSweepService`` lifespan
shape (``daemon/api.py``, the newest write-capable variant). The
``sweep_once`` BODY is a stub returning zeroed counters; the full
sweep (PID liveness + start-time match + the A3 eternal-``starting``
reaper + ``reason=`` log lines) lands in Phase 2 (task 2.A.2) per
``decisions.md`` §D6.

Wiring contract (Phase 1.C.14, A5/A6 amended):

* Mounted in the ``daemon/api.py`` lifespan AFTER the existing sweep
  services and BEFORE the vscode boot block.
* The manager is grabbed via ``getattr(manager,
  "_service_tool_manager", None)`` — NOT ``app.state`` (the original
  pseudocode read a phantom source of truth; A5). The SWEEP SERVICE
  itself is stored on ``app.state.service_reconciliation_service``
  for shutdown only (house pattern).
* ``await svc.sweep_once()`` runs once BEFORE the lifespan ``yield``
  (A6 guaranteed boot pass) so restart-survival cannot race the first
  tick; a boot-sweep failure logs and continues (never aborts boot).
* A8: the ``repo`` collaborator is the :class:`ServiceRepo` injected
  DIRECTLY (narrowest-collaborator house pattern, A9) — not reached
  through ``ServiceManager``. There is deliberately NO
  ``max_concurrent`` parameter here: the daemon-global cap lives on
  ``ServiceToolManager`` (the reconcile sweep only marks rows EXITED,
  it never enforces capacity).
* A8 None-manager guard: when the manager is absent the lifespan
  skips ``start()`` and logs DISABLED — this class never sees a
  ``None`` repo from production wiring (tests may exercise that
  branch directly).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.repositories.service_tool.repository import ServiceRepo

logger = logging.getLogger(__name__)


# Defaults — overridable via ``ServicesConfig`` (kept module-level so
# the constants have one source of truth, mirroring
# ``eligible_pending_sweep.py``). 90s shares the A3/eligible/orphan/
# job-lock sweep cadence; the 30s starting-grace window (A3) protects
# young ``status='starting'`` rows from the Phase-2 reaper (a crash
# between INSERT and Popen leaks a row that would otherwise count
# against the cap forever — reaped only after the grace elapses).
DEFAULT_SWEEP_INTERVAL_SECONDS: int = 90
DEFAULT_STARTING_GRACE_SECONDS: int = 30


class ServiceReconciliationService:
    """Periodic reconcile sweep for the ``service_tracking`` table.

    Phase 1.C ships the lifecycle shell only. Each tick calls
    :meth:`sweep_once` (a Phase-1 stub returning zeroed counters);
    Phase 2 fills the body:

    * scan rows in ``status IN ('starting','running')``;
    * ``pid IS NULL`` rows older than ``starting_grace_seconds`` are
      reaped (A3 eternal-``starting`` defense,
      ``reason=spawn_failed_or_interrupted``);
    * liveness check per row via
      ``service_spawner.get_process_start_time`` — dead PID or
      start-time mismatch ⇒ ``repo.mark_exited`` (the A13 guarded
      UPDATE makes sweep↔stop races idempotent);
    * NO re-spawn, NO signaling — the kernel reaps; the daemon only
      updates rows (D5 "no reaping" decision).

    Args:
        repo: ``ServiceRepo`` injected directly (A9 — narrowest
            collaborator). Sync repository; every call from this
            async service goes through ``asyncio.to_thread``.
        interval_seconds: Seconds between ticks. Default 90 (module
            constant). The authoritative floor is the pydantic
            ``Field(ge=1)`` on
            ``ServicesConfig.service_tool_reconcile_interval_seconds``;
            the constructor additionally clamps via ``max(1, ...)``
            so directly-constructed legacy fixtures cannot spin.
        starting_grace_seconds: Grace window (A3) before an
            eternal-``starting`` row may be reaped. Default 30.

    Lifecycle:
        * ``start()`` — spawn the asyncio task. Idempotent (silent
          no-op while the task is alive).
        * ``stop()`` — template ``set()`` → ``cancel()`` → ``await``
          with ``CancelledError`` swallowed (A8 — mirrors
          ``EligiblePendingSweepService.stop``). Safe to call when
          never started.
        * ``sweep_once()`` — one bounded tick; public for tests and
          the A6 guaranteed boot pass.
    """

    def __init__(
        self,
        repo: "ServiceRepo",
        interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS,
        starting_grace_seconds: int = DEFAULT_STARTING_GRACE_SECONDS,
    ) -> None:
        # A9: repo injected directly (api.py wiring passes service_tool_manager.repo).
        self._repo = repo
        self._interval_seconds = max(1, int(interval_seconds))
        self._starting_grace_seconds = max(0, int(starting_grace_seconds))
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # Public counters — surface via ``counters()`` for tests /
        # observability (template pattern). ``sweep_errors`` counts
        # ticks whose body raised; per-row error isolation arrives
        # with the Phase-2 body.
        self.ticks_total: int = 0
        self.sweep_errors: int = 0

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    @property
    def interval_seconds(self) -> int:
        """Effective tick interval (clamped ≥ 1) — read by the api
        lifespan for the boot INFO line."""
        return self._interval_seconds

    @property
    def starting_grace_seconds(self) -> int:
        """Effective eternal-``starting`` grace window (A3)."""
        return self._starting_grace_seconds

    async def start(self) -> None:
        """Spawn the periodic sweep as an asyncio task.

        Idempotent — calling ``start()`` while a task is already
        alive is a silent no-op (template pattern).
        """
        if self._task is not None and not self._task.done():
            logger.debug(
                "ServiceReconciliationService: start() called while "
                "the sweep task is already alive — silent no-op"
            )
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="service-reconciliation",
        )

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the sweep task (A8 template semantics).

        ``set()`` → ``cancel()`` → ``await`` with
        ``CancelledError`` swallowed. ``timeout`` bounds the polite
        join; the loop body is a bounded DB read, so 5s is a generous
        worst case (template default). Safe to call when the task was
        never started.
        """
        self._stop_event.set()
        task = self._task
        if task is None:
            return
        try:
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(task), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    # The polite join budget elapsed — the task was
                    # already cancelled above; reap it so no
                    # un-awaited cancelled task leaks (A8 review note).
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                except asyncio.CancelledError:
                    pass
        finally:
            self._task = None

    async def sweep_once(self) -> dict[str, int]:
        """One bounded reconcile tick — public for tests + boot pass.

        **Phase-1 STUB**: returns the zeroed counters dict (shape
        pinned now so the A6 boot pass and the lifespan log format
        are stable across the Phase-2 body fill). The Phase-2 body
        (plan task 2.A.2) replaces this stub with the D6 sweep:
        eternal-``starting`` reap (A3) + PID-liveness / start-time
        match + A13 guarded ``mark_exited``.

        Returns:
            Counters dict — ``alive`` (rows verified live),
            ``reaped`` (rows marked EXITED), ``errors`` (per-tick
            errors caught), ``starting_reaped`` (eternal-``starting``
            rows reaped inside the grace-window defense).
        """
        self.ticks_total += 1
        # Phase-2 fills the body; the stub is deliberately inert —
        # OFF-shaped behavior with the flag ON (no row is touched).
        return {"alive": 0, "reaped": 0, "errors": 0, "starting_reaped": 0}

    def counters(self) -> dict[str, int]:
        """Read-only view of the lifecycle counters (tests / observability)."""
        return {
            "ticks_total": self.ticks_total,
            "sweep_errors": self.sweep_errors,
        }

    # --------------------------------------------------------
    # Internal — the asyncio loop
    # --------------------------------------------------------

    async def _run_loop(self) -> None:
        """Bounded-interval asyncio loop — cancel-safe (template shape).

        Sweep-first-then-wait: each iteration calls
        :meth:`sweep_once` (exceptions swallowed + counted — the
        loop never dies on a bad tick) then sleeps
        ``interval_seconds`` interruptibly via ``_stop_event``.
        """
        try:
            while not self._stop_event.is_set():
                try:
                    await self.sweep_once()
                except asyncio.CancelledError:
                    raise
                except Exception as tick_err:  # noqa: BLE001 — backstop
                    self.sweep_errors += 1
                    logger.error(
                        "ServiceReconciliationService: tick failed: %s",
                        tick_err,
                        exc_info=True,
                    )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._interval_seconds,
                    )
                    # wait() returned — stop event set: exit cleanly.
                    return
                except asyncio.TimeoutError:
                    # Expected — interval elapsed, iterate.
                    continue
        except asyncio.CancelledError:
            # Hard cancel — exit silently (stop() owns the await).
            return


__all__ = [
    "DEFAULT_STARTING_GRACE_SECONDS",
    "DEFAULT_SWEEP_INTERVAL_SECONDS",
    "ServiceReconciliationService",
]
