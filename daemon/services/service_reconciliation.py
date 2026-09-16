"""Service reconciliation — periodic liveness sweep for detached services.

**Phase 2 (2.A)** — full implementation of the D6 sweep
(``decisions.md`` §D6, amended per A3 + A5 + A6 + A8 + A9 + A12 + A13):

* :meth:`ServiceReconciliationService.sweep_once` — scans every active
  row (``status IN ('starting','running')``), re-validates the OS
  ``(pid, start_time)`` triple, and transitions dead / PID-recycled
  rows to ``EXITED`` via :meth:`ServiceRepo.mark_exited` (the A13
  guarded UPDATE makes sweep↔stop races idempotent).
* **A3 eternal-``starting`` reaper** — a crash between INSERT and
  Popen (or an interrupted spawn) leaks a row whose ``pid IS NULL``.
  After ``starting_grace_seconds`` (default 30) the row is reaped
  with ``reason=spawn_failed_or_interrupted`` so the D5 cap is not
  held forever.
* **A9 narrow collaborator** — :class:`ServiceRepo` is injected
  DIRECTLY; the service never reaches through a ``ServiceManager``
  facade (per the ap1.py wiring at :mod:`daemon.api`).
* **A6 guaranteed boot pass** — ap1.py awaits ``sweep_once()`` once
  BEFORE the lifespan yield (precedent: ``recover_stale_job_locks()``
  in the same boot block).
* **A8 stop semantics** — ``set()`` → ``cancel()`` → ``await`` with
  ``CancelledError`` swallowed (the template shape mirroring
  ``EligiblePendingSweepService.stop``).  ``timeout=5.0`` bounds the
  polite join.
* **A13 atomic guard** — every status-mutating UPDATE goes through
  :meth:`ServiceRepo.mark_exited` / :meth:`ServiceRepo.update_status`
  — never a direct SQL UPDATE — so a sweep↔stop race is idempotent
  (the loser sees ``rowcount=0`` and treats it as success).

The kill-switch (D7 / 2.A.6) is checked at SWEEP TIME via an optional
``enabled_check`` callable. When the callable returns ``False`` the
sweep no-ops (DEBUG log + the disabled-shape counters dict). The
ap1.py lifespan mount is the primary kill-switch gate (the sweep is
NEVER constructed when the kill-switch is OFF); ``enabled_check`` is a
defense-in-depth shim so future lifecycle callers can short-circuit
the sweep without unmounting the service.

F16 (Risk-17 wording) — a sweep that falsely marks EXITED because
``ps``/``/proc`` failed to read (transient OS error) is an ACCEPTED
RESIDUAL: a false positive makes the row exit slightly early; it does
NOT affect the liveness of the underlying PID (the OS continues to
schedule the process). The ``Reason.carrier`` log captures the
misreport class so a future operator sees the noise.

Wiring contract (1.C.14, A5/A6 amended):

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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

from sqlalchemy.exc import SQLAlchemyError

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


# Type alias for the kill-switch resolver (2.A.6 — optional callable,
# default ``None`` ⇒ sweep is always enabled; production passes a
# resolver that reads the ap1.py-cached config bool).
EnabledCheck = Callable[[], bool]


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _row_age_seconds(row: Any) -> float:
    """Return wall-clock age (seconds) of a ``service_tracking`` row.

    Reads the row's ``created_at`` ISO-8601 string (A12 format —
    ``aware UTC``) and computes the delta against
    ``datetime.now(timezone.utc)``. Returns ``0.0`` on parse failure
    (defensive — never raises; the A3 reaper compares against a
    grace seconds floor so a 0.0 age is the safest misreport: the
    row stays inside the grace window until a fresh ``created_at``
    parses correctly on the next tick).

    The helper runs synchronously (the sweep wraps every call in
    ``asyncio.to_thread``).
    """
    created_at = getattr(row, "created_at", None)
    if not isinstance(created_at, str) or not created_at:
        return 0.0
    try:
        # A12 producer emits aware ISO-8601 with ``+00:00``; the
        # ``fromisoformat`` parser accepts the format byte-stable
        # (Python 3.11+).
        dt = datetime.fromisoformat(created_at)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        # Defensive — legacy rows may carry a naive stamp; treat as UTC.
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


class ServiceReconciliationService:
    """Periodic reconcile sweep for the ``service_tracking`` table.

    Phase 2 ships the full :meth:`sweep_once` body per D6 in
    ``decisions.md``. Each tick:

    * reads ``list_active()`` (rows in ``STARTING`` / ``RUNNING``);
    * for each row whose ``pid IS NULL`` and row-age >=
      ``starting_grace_seconds`` → reap with
      ``reason=spawn_failed_or_interrupted`` (A3);
    * for each row with a live ``pid`` → compare
      ``service_spawner.get_process_start_time(row.pid)`` against
      ``row.start_time``; mismatch → ``mark_exited`` with
      ``reason=pid_recycled``; ``None`` → ``mark_exited`` with
      ``reason=dead``;
    * the repo's guarded ``mark_exited`` (A13) makes the sweep
      race-safe with concurrent ``service_stop`` / ``service_status``
      calls — the loser sees ``rowcount=0`` and treats as idempotent.

    Args:
        repo: ``ServiceRepo`` injected directly (A9 — narrowest
            collaborator). Sync repository; every call from this
            async service goes through ``asyncio.to_thread``.
        interval_seconds: Seconds between ticks. Default 90. The
            authoritative floor is the pydantic
            ``Field(ge=1)`` on
            ``ServicesConfig.service_tool_reconcile_interval_seconds``;
            the constructor additionally clamps via ``max(1, ...)``
            so directly-constructed legacy fixtures cannot spin.
        starting_grace_seconds: Grace window (A3) before an
            eternal-``starting`` row may be reaped. Default 30.
        enabled_check: Optional callable — 2.A.6 (D7) — invoked at
            the top of every :meth:`sweep_once`. ``True`` (default):
            no check; ``False``-returning callable: the sweep no-ops
            with the disabled-shape counters dict and a DEBUG log.
            The ap1.py lifespan mount is the primary kill-switch
            gate; ``enabled_check`` is defense-in-depth so a future
            caller can short-circuit the sweep WITHOUT unmounting
            the service. The callable runs SYNCHRONOUSLY (kept
            cheap — a cached bool read).

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
        enabled_check: Optional[EnabledCheck] = None,
    ) -> None:
        # A9: repo injected directly (api.py wiring passes service_tool_manager.repo).
        self._repo = repo
        self._interval_seconds = max(1, int(interval_seconds))
        self._starting_grace_seconds = max(0, int(starting_grace_seconds))
        # 2.A.6: optional kill-switch resolver — kept ``None`` by default
        # so wiring changes are not forced. Production paths supply a
        # ``lambda: cached_bool()`` so a config flip mid-flight is visible
        # on the NEXT tick (cached-boot-precedence matches the shape at
        # ``daemon/config.py:3137-3150``).
        self._enabled_check: Optional[EnabledCheck] = enabled_check
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # Public counters — surface via ``counters()`` for tests /
        # observability (template pattern). ``sweep_errors`` counts
        # ticks whose body raised; ``disabled_ticks`` counts the
        # kill-switch-OFF shortcut path (D7 observability — surface
        # the gate state to a future operator dashboard).
        self.ticks_total: int = 0
        self.sweep_errors: int = 0
        self.disabled_ticks: int = 0

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

        **Phase 2 (2.A.2) full body** per D6 §"Sweep pseudocode" in
        :mod:`.agents.shared.planning.service-tool.decisions.md`,
        amended per A3 + A8 + A9 + A12 + A13.

        Steps:

        1. **Kill-switch gate (2.A.6)** — when ``enabled_check`` is
           set and returns ``False``, log DEBUG and return the
           disabled-shape counters (no DB queries, no row touch).
           The ap1.py mount is the primary gate; this shim is
           defense-in-depth (cheap, synchronous).
        2. ``await asyncio.to_thread(self._repo.list_active)`` —
           the A9 narrow collaborator (no ``manager.repo``
           reach-through).
        3. Per-row:
           * **A3** — ``pid IS NULL`` AND row-age ≥ grace → reap
             with ``reason=spawn_failed_or_interrupted``, increment
             ``starting_reaped``.
           * ``pid IS NULL`` within grace → leave alone (spawn may be
             in progress).
           * ``pid`` assigned → read
             ``service_spawner.get_process_start_time(pid)`` via
             ``asyncio.to_thread``.
             - ``None`` → ``mark_exited`` + ``reason=dead`` + ``reaped``.
             - mismatch → ``mark_exited`` + ``reason=pid_recycled`` +
               ``reaped``.
             - match → ``alive++``.
           * Errors caught into ``errors`` (per-row isolation) +
             ``logger.exception``.
        4. **Summary log** — INFO ``[ServiceTool] reconcile_swept
           alive=N reaped=M starting_reaped=S errors=K`` when
           ``reaped > 0`` OR ``starting_reaped > 0`` (so a no-op
           sweep is silent — same shape as
           ``recover_stale_job_locks``).
        5. **F16 (Risk-17 residual)** — a sweep that falsely marks
           EXITED because ``service_spawner.get_process_start_time``
           misread (transient /proc or ``ps`` failure) is the
           accepted error class: a false-positive makes the row
           exit slightly early; it does NOT affect the liveness of
           the underlying PID (the OS continues scheduling the
           process). The reason tag carries the misreport class.

        Returns:
            Counters dict — ``alive`` (rows verified live),
            ``reaped`` (rows marked EXITED — dead / PID-recycled),
            ``errors`` (per-row errors caught), ``starting_reaped``
            (eternal-``starting`` rows reaped), and — only when
            ``enabled_check`` returned ``False`` — ``disabled=True``.
        """
        self.ticks_total += 1

        # 2.A.6 — kill-switch gate first; cheap, no DB touch.
        if self._enabled_check is not None and not self._enabled_check():
            self.disabled_ticks += 1
            logger.debug("service_reconciliation_disabled")
            return {
                "alive": 0,
                "reaped": 0,
                "errors": 0,
                "starting_reaped": 0,
                "disabled": True,
            }

        counters: dict[str, int] = {
            "alive": 0,
            "reaped": 0,
            "errors": 0,
            "starting_reaped": 0,
        }

        try:
            active_rows = await asyncio.to_thread(self._repo.list_active)
        except (SQLAlchemyError, OSError):
            # Repo read failure (DB locked / engine dropped) — the
            # tick must surface the error but never kill the loop.
            # Narrowed from ``Exception`` (Block 7 — leader-approved
            # behavior change): SQLAlchemyError covers the DB-layer
            # failures we know to expect, OSError covers transient
            # filesystem faults; anything else is caught by the
            # outer ``_run_loop`` backstop at :486 so the lifespan
            # never breaks.
            self.sweep_errors += 1
            logger.exception(
                "ServiceReconciliationService.sweep_once: list_active failed"
            )
            return counters

        # Lazy import — the spawner is a small module and we keep
        # this service free of static coupling for testability.
        from daemon.tools import service_spawner

        for row in active_rows:
            try:
                # A3: eternal-``starting`` reaper. ``pid IS NULL`` AND
                # row-age ≥ grace → reap. Within grace → leave alone
                # (spawn may be in progress).
                if row.pid is None:
                    age = await asyncio.to_thread(_row_age_seconds, row)
                    if age >= self._starting_grace_seconds:
                        # A13: ``mark_exited`` is a guarded UPDATE
                        # (``WHERE id=? AND status IN ('starting','running')``).
                        # rowcount=0 means a concurrent stop won the
                        # race — treat as idempotent (do not bump
                        # ``starting_reaped`` in that case so the
                        # log stays accurate).
                        n = await asyncio.to_thread(
                            self._repo.mark_exited, row.id, None
                        )
                        if n:
                            logger.warning(
                                "[ServiceTool] reconcile_reaped "
                                "name=%s reason=spawn_failed_or_interrupted "
                                "age=%ss grace=%ss",
                                row.name,
                                age,
                                self._starting_grace_seconds,
                            )
                            counters["starting_reaped"] += 1
                    # else: still inside grace — leave alone.
                    continue

                current_start = await asyncio.to_thread(
                    service_spawner.get_process_start_time, row.pid
                )
                if current_start is None:
                    # PID is dead — ESCRH / NoEnt. ``mark_exited``
                    # rowcount=0 ⇒ race-lost (already EXITED); count
                    # it as a reaped line only when we won the race
                    # so the diagnostic log stays truthful.
                    n = await asyncio.to_thread(
                        self._repo.mark_exited, row.id, None
                    )
                    if n:
                        logger.info(
                            "[ServiceTool] reconcile_reaped "
                            "name=%s pid=%s reason=dead",
                            row.name,
                            row.pid,
                        )
                        counters["reaped"] += 1
                elif current_start != row.start_time:
                    # F16 residual — kernel recycled the PID onto an
                    # unrelated process; we mark EXITED instead of
                    # signaling (a SIGKILL on a recycled PID would
                    # be a stray kill against an unrelated process).
                    n = await asyncio.to_thread(
                        self._repo.mark_exited, row.id, None
                    )
                    if n:
                        logger.warning(
                            "[ServiceTool] reconcile_reaped "
                            "name=%s pid=%s reason=pid_recycled "
                            "expected_start=%s got=%s",
                            row.name,
                            row.pid,
                            row.start_time,
                            current_start,
                        )
                        counters["reaped"] += 1
                else:
                    # PID-and-start-time match — service is live.
                    counters["alive"] += 1
            except (SQLAlchemyError, OSError):
                # Per-row repo / filesystem failure — record and
                # continue. Narrowed from ``Exception`` (Block 7):
                # SQLAlchemyError + OSError cover the known fault
                # surface; anything else propagates to the outer
                # ``_run_loop`` backstop at :486.
                counters["errors"] += 1
                logger.exception(
                    "Reconcile error for row id=%s name=%s",
                    row.id,
                    row.name,
                )

        if counters["reaped"] > 0 or counters["starting_reaped"] > 0:
            logger.info(
                "[ServiceTool] reconcile_swept alive=%s reaped=%s "
                "starting_reaped=%s errors=%s",
                counters["alive"],
                counters["reaped"],
                counters["starting_reaped"],
                counters["errors"],
            )

        return counters

    def counters(self) -> dict[str, int]:
        """Read-only view of the lifecycle counters (tests / observability)."""
        return {
            "ticks_total": self.ticks_total,
            "sweep_errors": self.sweep_errors,
            "disabled_ticks": self.disabled_ticks,
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
