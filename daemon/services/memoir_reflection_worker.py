"""Memoir Store v2 — the REFLECTION worker ("the dream's clock").

Phase 2 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase2-plan.md`` T2.3;
flags V2-5, V2-24, V2-25, V2-35, V2-16).

The worker is a CLONE of :mod:`daemon.services.memoir_embedding_worker`
— the same architecture, the same house contract, the same knobs:

* a lifespan task on ``app.state`` (``memoir_reflection_worker_task``),
  created with ``asyncio.create_task`` and cancelled + awaited at
  shutdown;
* a DEDICATED executor — ``ThreadPoolExecutor(max_workers=1,
  thread_name_prefix="memoir-reflect")`` — so the reflection lane's
  blocking work never touches the event loop and never touches the
  shared default executor. ``asyncio.to_thread`` is FORBIDDEN in worker
  modules (D3.5);
* a PER-TICK knob re-read with park/resume: the lane OFF parks the
  worker (it stays alive and resumes when the knob returns), and the
  knob is read again on the NEXT tick, never cached;
* per-tick EXCEPTION ISOLATION: one failed tick is logged and the next
  tick still runs; only ``CancelledError`` propagates.

THE TICK BODY IS SEQUENTIAL AND THE NIGHTLY GATE COMES FIRST (§3.1,
V2-D 2.1, V2-24 / Register #19 — LOAD-BEARING):

.. code-block:: text

    [Tick Start]
        Re-read ENSEMBLE_MEMOIR_REFLECTION_ENABLED (OFF ⇒ park, sleep)
        ▼
    NIGHTLY GATE: 02:00 <= now < 04:00 UTC AND last_nightly_date != today
        ├── YES ──> the nightly bounded pass (oldest-first, cap 30)
        └── NO  ──> the in-day idle-debounce pass (sum >= 25 AND
                    (idle >= debounce OR elapsed >= 3600 s))

THE ORDER IS THE POINT. The nightly pass advances the ``reflected_at``
watermarks, so the in-day branch of the NEXT tick sees a derived sum
below the threshold and idles cleanly. The two cadences cannot race —
they share ONE thread and ONE tick body, so the sequencing is
structural rather than a lock. The order is OBSERVABLE:
:attr:`MemoirReflectionWorker.pass_order` records which passes ran in
which order, and the AC7 mutation leg asserts that the nightly pass is
never evaluated after the in-day pass.
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from daemon.services.memoir_reflection_service import (
    MemoirReflectionService,
    ReflectionSettings,
    resolve_reflection_settings,
)
from daemon.services.timestamps import now_utc

logger = logging.getLogger(__name__)

#: The dedicated executor — ONE thread, named ``memoir-reflect``.
#:
#: The name is LOAD-BEARING: the worker runs its blocking work on this
#: thread, so a thread-name assertion is a real proof that the lane
#: never ran on the event loop or on the shared default executor.
_REFLECT_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="memoir-reflect"
)

#: The default tick interval (seconds) when no knob says otherwise.
DEFAULT_TICK_INTERVAL_S = 30


class MemoirReflectionWorker:
    """The lifespan-owned reflection loop.

    Constructed by the lifespan with the shared manager engine's
    repositories and the resolved daemon ``Config``; driven by
    :meth:`run` inside ``asyncio.create_task``.
    """

    def __init__(
        self,
        service: MemoirReflectionService,
        *,
        config: Any = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._service = service
        self._config = config
        self._clock = clock or now_utc
        self._monotonic = monotonic or time.monotonic
        #: The park state (D3.12 shape): the worker stays ALIVE while
        #: parked and resumes when the knob returns.
        self._parked = False
        self._phase = "running"
        self._last_tick_at: str | None = None
        self._last_pass_order: list[str] = []
        #: The last pass outcome (the metered truth of the last tick).
        self._last_outcome: Any = None
        #: The published counts of the last tick.
        self._last_tick_counts: dict[str, int] = {}

    @property
    def last_outcome(self) -> Any:
        """The LAST tick's pass outcome (``None`` before the first pass)."""
        return self._last_outcome

    # ── the published state (the readiness surface) ────────────────────

    def snapshot(self) -> dict[str, Any]:
        """The worker's park/tick state for the readiness surface.

        In-memory reads only — never a DB round trip.
        """
        return {
            "phase": self._phase,
            "parked": self._parked,
            "last_tick_at": self._last_tick_at,
            "last_pass_order": list(self._last_pass_order),
            "last_syntheses": self._last_tick_counts.get("syntheses", 0),
            "last_considered": self._last_tick_counts.get("considered", 0),
            "last_llm_calls": self._last_tick_counts.get("llm_calls", 0),
            "last_deferred": self._last_tick_counts.get("deferred", 0),
        }

    @property
    def pass_order(self) -> list[str]:
        """The passes that ran in the LAST tick, in order.

        The AC7 mutation leg reads this: reversing the order in the tick
        body flips the assertion RED.
        """
        return list(self._last_pass_order)

    # ── the loop ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """The lifespan task body: tick, sleep, repeat; cancel-safe.

        House contract (the readiness refresher's): a ``CancelledError``
        propagates; any other per-tick failure is logged and the NEXT
        tick still runs.
        """
        while True:
            try:
                interval = await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one bad tick is not fatal
                logger.error(
                    f"[MemoirReflect] tick failed: {exc}", exc_info=True
                )
                interval = self._tick_interval()
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return

    # ── one tick ────────────────────────────────────────────────────────

    async def _tick(self) -> int:
        """Run ONE worker tick; return the sleep interval for the next.

        THE ORDER IS LOAD-BEARING: the nightly gate is evaluated FIRST,
        and the in-day branch is the ELSE. The order is recorded in
        :attr:`pass_order` so it is OBSERVABLE (and so the mutation leg
        can assert it).
        """
        settings = self._resolve_settings()

        # ── the kill-switch gates every tick ──────────────────────────
        if not settings.enabled:
            if not self._parked:
                self._parked = True
                self._phase = "parked"
                logger.info(
                    "[MemoirReflect] parked: the reflection knob is OFF — "
                    "the worker stays alive and resumes when the knob "
                    "returns ON"
                )
            return self._tick_interval(settings)
        if self._parked:
            self._parked = False
            self._phase = "running"
            logger.info(
                "[MemoirReflect] resumed: the reflection knob is ON — the "
                "dream continues on the following ticks"
            )

        self._last_pass_order = []
        self._last_outcome = None
        self._last_tick_at = now_utc().isoformat()
        loop = asyncio.get_running_loop()

        # ── 1. THE NIGHTLY GATE — FIRST (V2-24 / Register #19) ─────────
        now = self._clock()
        if self._service.nightly_gate_open(now):
            outcome = await loop.run_in_executor(
                _REFLECT_EXECUTOR, self._service.run_nightly_pass, now
            )
            self._last_pass_order.append("nightly")
            self._last_outcome = outcome
            self._publish(outcome)
            return self._tick_interval(settings)

        # ── 2. the in-day idle-debounce branch (the ELSE) ─────────────
        should_run, reason, _total = self._service.evaluate_in_day(now)
        if not should_run:
            self._publish(None)
            return self._tick_interval(settings)
        logger.info(
            "[MemoirReflect] the in-day pass is due (reason=%s)", reason
        )
        outcome = await loop.run_in_executor(
            _REFLECT_EXECUTOR, self._service.run_in_day_pass, now
        )
        self._last_pass_order.append("in_day")
        self._last_outcome = outcome
        self._publish(outcome)
        return self._tick_interval(settings)

    # ── the settings (the per-tick re-read) ────────────────────────────

    def _resolve_settings(self) -> ReflectionSettings:
        """The per-tick knob + numeric re-read (D3.12's shape).

        The resolution goes through
        :func:`daemon.services.memoir_reflection_service.resolve_reflection_settings`
        — the ONE per-tick reader, which itself uses the shared
        classifier, so the worker tier and the boot tier can never drift.
        """
        settings = resolve_reflection_settings(self._config)
        self._service.apply_settings(settings)
        return settings

    def _tick_interval(self, settings: ReflectionSettings | None = None) -> int:
        """The sleep interval for the next tick."""
        if settings is None:
            settings = self._resolve_settings()
        return max(1, int(settings.tick_interval_s))

    # ── the published counts ────────────────────────────────────────────

    def _publish(self, outcome: Any) -> None:
        """Record the tick's counts (in-memory only)."""
        if outcome is None:
            self._last_tick_counts = {}
            return
        self._last_tick_counts = {
            "considered": int(getattr(outcome, "considered", 0)),
            "syntheses": int(getattr(outcome, "syntheses_accepted", 0)),
            "rejected": int(getattr(outcome, "syntheses_rejected", 0)),
            "duplicates": int(getattr(outcome, "syntheses_duplicate", 0)),
            "llm_calls": int(getattr(outcome, "llm_calls", 0)),
            "deferred": int(getattr(outcome, "deferred", 0)),
            "lines": int(getattr(outcome, "lines_written", 0)),
        }


def executor_thread_prefix() -> str:
    """The dedicated executor's thread prefix (the AM-29-style pin).

    The house shape (``daemon/services/memoir_embedding_worker.py``):
    the ThreadPoolExecutor appends ``_0`` to the prefix, so the pin
    reads the PREFIX, not the full name.
    """
    return "memoir-reflect"


def executor_thread_name() -> str:
    """The executor thread's name as seen from INSIDE the executor."""
    import threading

    future = _REFLECT_EXECUTOR.submit(lambda: threading.current_thread().name)
    return str(future.result())


__all__ = [
    "DEFAULT_TICK_INTERVAL_S",
    "MemoirReflectionWorker",
    "executor_thread_name",
    "executor_thread_prefix",
]
