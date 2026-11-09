"""Memoir Store v2 — the POIGNANCY worker (the background scorer).

Phase 4 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase4-plan.md`` T4.3/T4.5;
decisions V2-D 4.2, 4.3, 4.5, 4.9; flags V2-14, V2-16).

THE WORKER SHAPE IS A CLONE of
:mod:`daemon.services.memoir_embedding_worker` — the battle-tested house
worker pattern (V2-D 4.2):

* a lifespan asyncio task stored on ``app.state``
  (``memoir_poignancy_worker_task``), cancelled AND awaited at shutdown;
* a DEDICATED executor — ``ThreadPoolExecutor(max_workers=1,
  thread_name_prefix="memoir-poignancy")`` — so the lane's blocking work
  never touches the event loop and never touches the shared default
  executor. ``asyncio.to_thread`` is FORBIDDEN in worker modules
  (D3.5): a cancelled ``to_thread`` does not free a shared worker;
* a PER-TICK knob re-read with park/resume: the lane OFF parks the
  worker (it stays ALIVE and resumes when the knob returns) and the knob
  is read again on the NEXT tick, never cached;
* per-tick EXCEPTION ISOLATION: one failed tick is logged and the next
  tick still runs — only ``CancelledError`` propagates.

THE TICK BODY IS SEQUENTIAL AND THE KILL-SWITCH GATE COMES FIRST:

.. code-block:: text

    [Tick Start]
        Re-read ENSEMBLE_MEMOIR_POIGNANCY_ENABLED (OFF ⇒ park, sleep)
        ▼
    Scan the queue (the NEW unscored rows + the OPT-IN legacy rows)
        │
        ▼
    For each episode: score it (one LLM call) → write the score back,
    or record the failed attempt (the attempt cap terminalizes the row)

NO DELTA-NOTIFY COUPLING (V2-D 4.5): the worker pushes NOTHING to the
reflection service. The reflection trigger sum is DERIVED per tick in
SQL (``COALESCE(SUM(COALESCE(poignancy, importance)), 0)``), so a
re-score cannot double-count and a restart cannot lose a count.

THE BACKFILL FENCE (V2-D 4.3 / §3-A3): the LEGACY corpus (the
pre-cutover rows the migration stamped ``poignancy_status='legacy'``)
is scored ONLY when the operator raises
``ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX``. The constitutional default
is 0 — NO legacy episode is ever scored. The knob is an OPERATOR lever,
never a default (a full-corpus backfill would drain ~43k episodes/day
on a large host, replace authored history with model guesses, and pump
the reflection trigger sum past its threshold in one pass).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from daemon.repositories.memoir.repository import (
    MEMOIR_POIGNANCY_ATTEMPT_CAP,
    MEMOIR_POIGNANCY_TERMINAL_MARKER,
)
from daemon.services.memoir_poignancy_service import (
    MEMOIR_POIGNANCY_TICK_S,
    MemoirPoignancyService,
    PoignancySettings,
    resolve_poignancy_settings,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MEMOIR_POIGNANCY_ATTEMPT_CAP",
    "MEMOIR_POIGNANCY_EXECUTOR_THREAD_PREFIX",
    "MEMOIR_POIGNANCY_TERMINAL_MARKER",
    "MemoirPoignancyWorker",
    "TICK_SCORE_BUDGET_S",
    "current_executor_thread_name",
    "executor_thread_prefix",
    "run_score_on_executor",
]

#: The dedicated executor — ONE thread, named ``memoir-poignancy``.
#:
#: The name is LOAD-BEARING: the worker runs its blocking work on this
#: thread, so a thread-name assertion is a real proof that the lane
#: never ran on the event loop and never on the shared default executor.
MEMOIR_POIGNANCY_EXECUTOR_THREAD_PREFIX = "memoir-poignancy"
_POIGNANCY_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix=MEMOIR_POIGNANCY_EXECUTOR_THREAD_PREFIX
)

#: The tick's TOTAL scoring budget (seconds). The house pattern (the
#: embedding lane's ``TICK_EMBED_BUDGET_S``): the batch is a COUNT, not
#: a promise — when the budget is spent the remainder is DEFERRED to the
#: next tick instead of monopolising the single thread.
TICK_SCORE_BUDGET_S = 60.0


class MemoirPoignancyWorker:
    """The lifespan-owned scoring loop.

    Constructed by the lifespan with the shared manager engine's
    ``MemoirRepository`` (through the service) and the resolved daemon
    ``Config``; driven by :meth:`run` inside ``asyncio.create_task``.
    """

    def __init__(
        self,
        service: MemoirPoignancyService,
        *,
        config: Any = None,
        tick_score_budget_s: float = TICK_SCORE_BUDGET_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._config = config
        self._tick_score_budget_s = float(tick_score_budget_s)
        self._clock = clock
        #: The park state (the house shape): the worker stays ALIVE while
        #: parked and resumes when the knob returns.
        self._parked = False
        self._phase = "running"
        self._last_tick_at: str | None = None
        self._last_tick_ms: int | None = None
        self._last_tick_counts: dict[str, int] = {}

    # ── the published state (the readiness surface) ─────────────────────

    def snapshot(self) -> dict[str, Any]:
        """The worker's park/tick state — in-memory reads only."""
        return {
            "phase": self._phase,
            "parked": self._parked,
            "last_tick_at": self._last_tick_at,
            "last_tick_ms": self._last_tick_ms,
            "last_pending": self._last_tick_counts.get("pending", 0),
            "last_scored": self._last_tick_counts.get("scored", 0),
            "last_failed": self._last_tick_counts.get("failed", 0),
            "last_terminal": self._last_tick_counts.get("terminal", 0),
        }

    # ── the loop ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """The lifespan task body: tick, sleep, repeat; cancel-safe.

        House contract (``daemon/api.py`` readiness refresher): a
        ``CancelledError`` propagates; any other per-tick failure is
        logged and the NEXT tick still runs (park-not-die).
        """
        while True:
            try:
                interval = await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one bad tick is not fatal
                logger.error(
                    f"[MemoirPoignancy] tick failed: {exc}", exc_info=True
                )
                interval = self._tick_interval()
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return

    # ── one tick ────────────────────────────────────────────────────────

    async def _tick(self) -> int:
        """Run ONE worker tick; return the sleep interval for the next."""
        settings = self._resolve_settings()

        # ── the kill-switch gates every tick ────────────────────────────
        if not settings.enabled:
            if not self._parked:
                self._parked = True
                self._phase = "parked"
                logger.info(
                    "[MemoirPoignancy] parked: the poignancy knob is OFF — "
                    "the worker stays alive and resumes when the knob "
                    "returns ON (the episodes are untouched)"
                )
            return self._tick_interval(settings)
        if self._parked:
            self._parked = False
            self._phase = "running"
            logger.info(
                "[MemoirPoignancy] resumed: the poignancy knob is ON — the "
                "backlog is scored on the following ticks"
            )

        loop = asyncio.get_running_loop()
        started = self._clock()

        # ── the queue census + the scan (on the dedicated executor) ────
        pending = await loop.run_in_executor(
            _POIGNANCY_EXECUTOR, self._service.pending_episodes
        )
        pending_total = len(pending)

        scored = 0
        failed = 0
        terminal = 0
        deferred = 0

        for episode in pending:
            elapsed = self._clock() - started
            remaining = self._tick_score_budget_s - elapsed
            if remaining <= 0:
                deferred = len(pending) - scored - failed
                if deferred > 0:
                    logger.info(
                        "[MemoirPoignancy] the tick scoring budget is spent "
                        f"({self._tick_score_budget_s:.0f}s) — deferring "
                        f"{deferred} episode(s) to the next tick"
                    )
                break
            outcome = await self._process_episode(episode)
            if outcome[0]:
                scored += 1
                continue
            failed += 1
            _scored, attempts, status = outcome
            if status is not None:
                terminal += 1
                logger.warning(
                    f"[MemoirPoignancy] terminal episode={getattr(episode, 'id', '')} "
                    f"reason={status} attempts={attempts}"
                )

        tick_ms = int(round((self._clock() - started) * 1000))
        self._publish(
            pending=pending_total,
            scored=scored,
            failed=failed,
            terminal=terminal,
            tick_ms=tick_ms,
        )
        logger.info(
            f"[MemoirPoignancy] pending={pending_total} scored={scored} "
            f"failed={failed} terminal={terminal} "
            f"duration={tick_ms / 1000:.1f}s"
        )
        return self._tick_interval(settings)

    # ── per-row pipeline ────────────────────────────────────────────────

    async def _process_episode(self, episode: Any) -> tuple[bool, int, str | None]:
        """Score ONE episode on the dedicated executor.

        The LLM call AND the write-backs BLOCK, so the WHOLE per-row
        pipeline runs on the lane's own thread — never on the event loop
        and never on the shared default executor.

        Returns:
            ``(scored, attempts, terminal_status)``.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _POIGNANCY_EXECUTOR, self._process_episode_blocking, episode
        )

    def _process_episode_blocking(
        self, episode: Any
    ) -> tuple[bool, int, str | None]:
        """The blocking half of the per-row pipeline (the executor's thread)."""
        outcome = self._service.score_episode(episode)
        if outcome.scored:
            return True, 0, None
        attempts, status = self._service.record_failure(
            getattr(episode, "id", ""), outcome
        )
        return False, attempts, status

    # ── the settings (the per-tick re-read) ─────────────────────────────

    def _resolve_settings(self) -> PoignancySettings:
        """The per-tick knob + numeric re-read (the house shape).

        The resolution goes through
        :func:`daemon.services.memoir_poignancy_service.resolve_poignancy_settings`
        — the lane's OWN reader, which reads the ISOLATED
        ``memoir_poignancy`` section and uses the SHARED classifier. The
        global knob resolver is never called (N1 / V2-D 4.9).
        """
        settings = resolve_poignancy_settings(self._config)
        self._service.apply_settings(settings)
        return settings

    def _tick_interval(self, settings: PoignancySettings | None = None) -> int:
        """The sleep interval for the next tick (never below one second)."""
        if settings is None:
            settings = self._resolve_settings()
        return max(1, int(settings.tick_interval_s or MEMOIR_POIGNANCY_TICK_S))

    # ── the published counts ────────────────────────────────────────────

    def _publish(
        self,
        *,
        pending: int,
        scored: int,
        failed: int,
        terminal: int,
        tick_ms: int,
    ) -> None:
        self._last_tick_at = datetime.now(timezone.utc).isoformat()
        self._last_tick_ms = tick_ms
        self._last_tick_counts = {
            "pending": pending,
            "scored": scored,
            "failed": failed,
            "terminal": terminal,
        }


# ── the module-level accessors (the AM-29-style pins) ───────────────────


def executor_thread_prefix() -> str:
    """The dedicated executor's thread prefix.

    The house shape (``daemon/services/memoir_embedding_worker.py``):
    the ThreadPoolExecutor appends ``_0`` to the prefix, so the pin
    reads the PREFIX, not the full name.
    """
    return MEMOIR_POIGNANCY_EXECUTOR_THREAD_PREFIX


def run_score_on_executor(fn: Callable[[], Any]) -> Any:
    """Test/support helper: run ONE callable on the dedicated executor.

    Exposed so the packs can prove the executor's thread prefix without
    constructing a full worker.
    """
    return _POIGNANCY_EXECUTOR.submit(fn).result()


def current_executor_thread_name() -> str:
    """The executor thread's name as seen from INSIDE the executor."""
    return _POIGNANCY_EXECUTOR.submit(threading.current_thread).result().name
