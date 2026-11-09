"""Memoir Store v2 — the POIGNANCY worker (the background scorer).

Phase 4 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase4-plan.md`` T4.3/T4.5;
decisions V2-D 4.2, 4.3, 4.5, 4.9; flags V2-14, V2-16).

THE WORKER SHAPE IS A CLONE of
:mod:`daemon.services.memoir_embedding_worker` — the battle-tested house
worker pattern (V2-D 4.2):

* a lifespan asyncio task stored on ``app.state``
  (``memoir_poignancy_worker_task``), cancelled AND awaited at shutdown;
* a DEDICATED executor — ``ThreadPoolExecutor(max_workers=2,
  thread_name_prefix="memoir-poignancy")`` — so the lane's blocking work
  never touches the event loop and never touches the shared default
  executor. ``asyncio.to_thread`` is FORBIDDEN in worker modules
  (D3.5): a cancelled ``to_thread`` does not free a shared worker.
  THE WORKER COUNT IS TWO (the embedding lane's AM-29 shape, and the
  reason is load-bearing — see :data:`TICK_SCORE_BUDGET_S`): the
  scoring calls stay SEQUENTIAL (the worker awaits each one before it
  starts the next), and the SECOND thread serves the repository calls
  that interleave with an in-flight scoring call — the failure record
  of a scoring call that blew past the tick's budget. With ONE thread
  the hung call would hold the only slot and the tick's own accounting
  write would queue behind it, which would break the tick's wall-clock
  bound (the very thing the second thread exists to preserve);
* a PER-TICK knob re-read with park/resume: the lane OFF parks the
  worker (it stays ALIVE and resumes when the knob returns) and the knob
  is read again on the NEXT tick, never cached;
* per-tick EXCEPTION ISOLATION: one failed tick is logged and the next
  tick still runs — only ``CancelledError`` propagates.

THE PER-EPISODE ISOLATION (the reviewer's W1): a scoring call that
RAISES is RECORDED as a failed attempt (the counter advances, the row
terminalizes at the cap) and the tick CONTINUES with the next episode.
The queue is ordered ``created_at ASC``, so an UNGUARDED raiser at the
head of the queue is retried every tick while every row behind it is
never reached — a silent, permanent starvation.

THE LANE-DOWN LATCH (V2-D 4.6 / the embedding lane's D3.9-D3.10): a
dead credential (401/403) is lane-down at the FIRST occurrence — the
tick stops (the remaining rows are DEFERRED, so a dead credential
never burns one request per pending row per tick), the tick slows to
:data:`LATCH_TICK_INTERVAL_S` seconds, and the latch is released by the
first success. The latch state is on the snapshot surface.

THE TICK BOUND (V2-44): the tick's TOTAL scoring budget is
:data:`TICK_SCORE_BUDGET_S` seconds (the PLAN's number) and it is a
REAL wall-clock bound — each episode's wait is sized to the REMAINING
budget (``asyncio.wait_for``), not merely checked BETWEEN episodes.

THE TICK BODY IS SEQUENTIAL AND THE KILL-SWITCH GATE COMES FIRST:

.. code-block:: text

    [Tick Start]
        Re-read ENSEMBLE_MEMOIR_POIGNANCY_ENABLED (OFF ⇒ park, sleep)
        ▼
    Scan the queue (the NEW unscored rows + the OPT-IN legacy rows)
        │
        ▼
    For each episode: score it (one LLM call, BOUNDED by the tick's
    remaining budget) → write the score back, or record the failed
    attempt (the attempt cap terminalizes the row). A raising episode
    is RECORDED and the tick continues; a dead credential stops the
    tick and arms the lane-down latch.

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
    POIGNANCY_AUTH_REJECTED_REASON,
    POIGNANCY_CALL_TIMEOUT_REASON,
    POIGNANCY_EXCEPTION_REASON,
    MemoirPoignancyService,
    PoignancyOutcome,
    PoignancySettings,
    resolve_poignancy_settings,
)

logger = logging.getLogger(__name__)

__all__ = [
    "LATCH_THRESHOLD_TICKS",
    "LATCH_TICK_INTERVAL_S",
    "MEMOIR_POIGNANCY_ATTEMPT_CAP",
    "MEMOIR_POIGNANCY_EXECUTOR_THREAD_PREFIX",
    "MEMOIR_POIGNANCY_STALL_WARN_TICKS",
    "MEMOIR_POIGNANCY_TERMINAL_MARKER",
    "MemoirPoignancyWorker",
    "TICK_SCORE_BUDGET_S",
    "current_executor_thread_name",
    "executor_thread_prefix",
    "run_score_on_executor",
]

#: The dedicated executor — TWO threads, named ``memoir-poignancy``.
#:
#: The name is LOAD-BEARING: the worker runs its blocking work on this
#: thread, so a thread-name assertion is a real proof that the lane
#: never ran on the event loop and never on the shared default executor.
#:
#: TWO threads, SEQUENTIAL scoring (the embedding lane's AM-29 shape):
#: the worker awaits each scoring call before it starts the next, so the
#: second thread is idle in the normal path. It exists for ONE case: a
#: scoring call that blew past the tick's budget is still running in the
#: first thread, and the tick's own accounting write (the failed
#: attempt) must NOT queue behind it — otherwise the tick would exceed
#: its wall-clock bound by the length of the hung call.
MEMOIR_POIGNANCY_EXECUTOR_THREAD_PREFIX = "memoir-poignancy"
_POIGNANCY_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix=MEMOIR_POIGNANCY_EXECUTOR_THREAD_PREFIX
)

#: The tick's TOTAL scoring budget (seconds).
#:
#: V2-44: the PLAN's number — ``v2-phase4-plan.md`` objective 1 says
#: "tick budget 20s" (the code shipped 60 s; V2-D 4.2's "30 s" is the
#: PER-CALL timeout, a different knob — see
#: ``docs/memoir-v2/implementation-notes.md``).
#:
#: THE HOUSE PATTERN (the embedding lane's ``TICK_EMBED_BUDGET_S``): the
#: batch is a COUNT, not a promise — when the budget is spent the
#: remainder is DEFERRED to the next tick instead of monopolising the
#: single thread. THE BOUND IS REAL (V2-44): each episode's wait is
#: sized to the REMAINING budget (``asyncio.wait_for``), so the tick's
#: scoring work can never exceed the budget — checking the budget only
#: BETWEEN episodes let one episode run to the per-call timeout (30 s)
#: and the tick to reach ~1.5x its budget.
TICK_SCORE_BUDGET_S = 20.0

# ── the lane-down latch (V2-D 4.6 — the embedding lane's D3.10) ──────────
#: The consecutive all-fail ticks before the worker slows down.
LATCH_THRESHOLD_TICKS = 3

#: The slowed tick interval (seconds) while the latch is engaged.
LATCH_TICK_INTERVAL_S = 300

# ── the stall warning (the embedding lane's W-2 shape) ───────────────────
#: The number of consecutive ``pending>0 and scored==0`` ticks after
#: which the worker logs a LOUD stall WARNING naming the stalled
#: episode. Silent-forever is unacceptable: a starved window must be
#: visible to the operator.
MEMOIR_POIGNANCY_STALL_WARN_TICKS = 3


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
        #: The lane-down latch state (per-process; a restart re-evaluates).
        self._all_fail_streak = 0
        self._credential_reject_streak = 0
        self._latched = False
        #: The stall state: consecutive ``pending>0, scored==0`` ticks
        #: and whether the loud WARNING already fired for this stall.
        self._stall_streak = 0
        self._stall_warned = False

    # ── the published state (the readiness surface) ─────────────────────

    def snapshot(self) -> dict[str, Any]:
        """The worker's park/tick/latch state — in-memory reads only."""
        return {
            "phase": self._phase,
            "parked": self._parked,
            "latched": self._latched,
            "consecutive_all_fail_ticks": self._all_fail_streak,
            "credential_rejected_streak": self._credential_reject_streak,
            "latch_tick_interval_s": LATCH_TICK_INTERVAL_S,
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
        #: The HEAD of the queue — the episode a starved tick is stuck on.
        stalled_episode = getattr(pending[0], "id", "") if pending else ""

        scored = 0
        failed = 0
        terminal = 0
        deferred = 0
        credential_rejected = False

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
            # THE PER-EPISODE ISOLATION (W1): the blocking half catches a
            # raising scoring call, RECORDS it as a failed attempt (the
            # counter advances, the row terminalizes at the cap) and
            # returns — so the tick CONTINUES with the next episode.
            did_score, attempts, status, reason = await self._process_episode(
                episode, remaining
            )
            if did_score:
                scored += 1
                continue
            failed += 1
            if reason == POIGNANCY_AUTH_REJECTED_REASON:
                credential_rejected = True
            if status is not None:
                terminal += 1
                logger.warning(
                    f"[MemoirPoignancy] terminal episode={getattr(episode, 'id', '')} "
                    f"reason={status} attempts={attempts}"
                )
            if credential_rejected:
                # W2: a DEAD CREDENTIAL must not burn one request per
                # pending row per tick — the remaining rows are DEFERRED
                # to the next (slowed) tick instead.
                deferred = len(pending) - scored - failed
                logger.warning(
                    "[MemoirPoignancy] the credential was rejected — "
                    f"deferring {deferred} episode(s) to the next tick "
                    "(the lane-down latch engages: the tick slows down)"
                )
                break

        # ── the lane-down latch (V2-D 4.6 / D3.9-D3.10) ─────────────────
        if scored > 0:
            if self._latched:
                self._latched = False
                logger.info(
                    "[MemoirPoignancy] latch released: a scoring call "
                    "succeeded — returning to the configured tick interval"
                )
            self._all_fail_streak = 0
            self._credential_reject_streak = 0
        elif failed > 0:
            if credential_rejected:
                # D3.9: a dead credential is lane-down at the FIRST
                # occurrence — the streak jumps to the threshold.
                self._credential_reject_streak += 1
                self._all_fail_streak = LATCH_THRESHOLD_TICKS
            else:
                self._all_fail_streak += 1
            if self._all_fail_streak >= LATCH_THRESHOLD_TICKS and not self._latched:
                self._latched = True
                logger.warning(
                    "[MemoirPoignancy] latch engaged: "
                    f"{self._all_fail_streak} consecutive all-fail tick(s) "
                    f"(credential rejections: {self._credential_reject_streak}) "
                    f"— slowing to {LATCH_TICK_INTERVAL_S}s ticks until the "
                    "first success"
                )
        # failed == 0 and scored == 0: a neutral tick (nothing to do)
        # — the streak and the latch hold their state.

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
            f"duration={tick_ms / 1000:.1f}s slow_mode={self._latched}"
        )

        # ── the stall warning (the embedding lane's W-2 shape) ──────────
        # ``pending > 0`` while ``scored == 0`` tick after tick means the
        # queue is NOT ADVANCING. Silent-forever is unacceptable: after N
        # consecutive starved ticks the worker names the pending count,
        # the head of the queue and the counts OUT LOUD.
        if pending_total > 0 and scored == 0:
            self._stall_streak += 1
            if (
                self._stall_streak >= MEMOIR_POIGNANCY_STALL_WARN_TICKS
                and not self._stall_warned
            ):
                self._stall_warned = True
                logger.warning(
                    f"[MemoirPoignancy] STALL: pending={pending_total} but "
                    f"scored=0 for {self._stall_streak} consecutive ticks — "
                    f"stalled_episode={stalled_episode} failed={failed} "
                    f"terminal={terminal} deferred={deferred} — the scoring "
                    "queue is not advancing"
                )
        else:
            self._stall_streak = 0
            self._stall_warned = False

        if self._latched:
            return LATCH_TICK_INTERVAL_S
        return self._tick_interval(settings)

    # ── per-row pipeline ────────────────────────────────────────────────

    async def _process_episode(
        self, episode: Any, remaining_s: float
    ) -> tuple[bool, int, str | None, str | None]:
        """Score ONE episode on the dedicated executor — BOUNDED.

        ``remaining_s`` is the tick's REMAINING scoring budget: the
        per-episode wait is sized to it (``asyncio.wait_for`` — the
        embedding lane's AM-28 shape), so the tick's TOTAL scoring work
        is bounded by the tick budget instead of being checked only
        BETWEEN episodes (V2-44).

        Returns:
            ``(scored, attempts, terminal_status, reason)`` — the reason
            token is ``None`` on a success.
        """
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            _POIGNANCY_EXECUTOR, self._process_episode_blocking, episode
        )
        try:
            return await asyncio.wait_for(
                future, timeout=max(0.05, remaining_s)
            )
        except asyncio.TimeoutError:
            if not future.cancelled():
                # The blocking half raised a TimeoutError of its own —
                # that is a scoring failure, not a budget expiry.
                raise
            return await self._record_episode_timeout(episode, remaining_s)

    def _process_episode_blocking(
        self, episode: Any
    ) -> tuple[bool, int, str | None, str | None]:
        """The blocking half of the per-row pipeline (the executor's thread).

        THE PER-EPISODE ISOLATION (W1): a scoring call that RAISES is
        caught HERE, turned into a failed attempt and RECORDED (the
        counter advances, the row terminalizes at the cap). The tick then
        continues with the next episode — an unguarded raiser at the head
        of the ``created_at ASC`` queue used to abort the whole tick, so
        every row behind it was never reached (a silent, permanent
        starvation).

        A failure of the FAILURE RECORD itself (the DB is down) is NOT
        swallowed: it propagates to the tick-level guard, which logs the
        bad tick and lets the next tick rescan (the embedding lane's
        AM-30 shape).
        """
        try:
            outcome = self._service.score_episode(episode)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the episode is isolated
            logger.error(
                "[MemoirPoignancy] the scoring of one episode raised (%s) — "
                "the episode counts an attempt and the tick CONTINUES",
                type(exc).__name__,
                exc_info=True,
            )
            outcome = PoignancyOutcome(
                scored=False,
                reason=POIGNANCY_EXCEPTION_REASON,
                immediate_terminal=False,
            )
        if outcome.scored:
            return True, 0, None, None
        attempts, status = self._service.record_failure(
            getattr(episode, "id", ""), outcome
        )
        return False, attempts, status, outcome.reason

    async def _record_episode_timeout(
        self, episode: Any, remaining_s: float
    ) -> tuple[bool, int, str | None, str | None]:
        """The per-episode budget expired — record it as a failed attempt.

        The scoring call is still running in the executor's first thread
        (a call that blew past the tick's budget cannot be cancelled
        mid-flight), so the record runs on the SECOND thread — the tick's
        wall-clock bound survives the accounting write.
        """
        logger.warning(
            "[MemoirPoignancy] the scoring call exceeded the tick's "
            f"remaining budget ({remaining_s:.1f}s) — the episode counts "
            "an attempt"
        )
        loop = asyncio.get_running_loop()
        outcome = PoignancyOutcome(
            scored=False,
            reason=POIGNANCY_CALL_TIMEOUT_REASON,
            immediate_terminal=False,
        )
        attempts, status = await loop.run_in_executor(
            _POIGNANCY_EXECUTOR,
            self._service.record_failure,
            getattr(episode, "id", ""),
            outcome,
        )
        return False, attempts, status, POIGNANCY_CALL_TIMEOUT_REASON

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
