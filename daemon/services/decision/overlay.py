"""Decision module — the overlay orchestrator (``overlay.py``).

The overlay is the ONE place where a consult is admitted, made,
measured, logged and paid for.

**THE GATES (FLAG12 — the design's admission rule conflated them).**

1. **CONSULT gate** — the overlay may CALL OUT only when: the global
   master is ON **and** the per-seam key is ON **and** the seam's own
   gate switch is ON **and** the overlay is configured
   (``DECISION_BASE_URL`` + ``DECISION_API_KEY`` present). **The stage
   does NOT gate consultation** — a ``shadow`` seam is still consulted
   (its answer is only LOGGED — D3).
2. **APPLY gate** — a consulted answer is applied ONLY when the stage
   is ``deciding`` **and** the registry holds a RATIFIED D3 transition
   for the seam. At ``shadow`` the answer is LOGGED (the A/B record)
   and the heuristic outcome is returned UNCHANGED.

Any consult-gate condition OFF or UNREADABLE ⇒ **NO CALL AT ALL** plus
a ``switch_unreadable`` / ``switch_off`` suppressed row.

**THE COST CEILING (FLAG1/FLAG2/FLAG3 + L8).** Cumulative MONTH-TO-DATE
spend is compared against the global ceiling on EVERY consult (a
consult is the only moment spend accrues — no scheduler is needed).
Crossing it trips the module to shadow-off: BOTH
``decision_seam_<id>_stage := 'shadow'`` AND
``decision_seam_<id>_enabled := 0`` per seam, plus the global master
OFF. A per-seam share guard keeps one seam from consuming the whole
global budget.

**THE COST MODEL (L8 — no invented pricing).** There is no pricing
table in the ensemble or in the proxy; the proxy meters TOKENS. So:

* the provider's REPORTED cost is used when it responds with one;
* otherwise an OPERATOR-CONFIGURED per-model USD-per-million-token
  rate converts the metered tokens;
* otherwise the consult is metered in TOKENS, a ``pricing_unconfigured``
  suppressed row is emitted, and the configured TOKEN ceiling is the
  fail-safe — the control is never ABSENT, it degrades VISIBLY.

**THE THROTTLE (AC-18).** A consecutive-failure breaker (N = 5), a
fallback-rate breaker (> 50 % over 10 min), a 5-minute open window
with one probe after it, and per-seam + global rate/concurrency
budgets — all enforced **PRE-SOCKET**. A suppressed consult makes NO
call and incurs NO cost.

**THE OPERATOR NOTIFY (FLAG4 + L9).** The default notify writes a
**board item** (the ALWAYS surface — ``BoardService.create_item``,
called DIRECTLY and sync, off the event loop), plus a daemon-log
WARNING row and a dedicated counter. When no board service is
reachable the notify degrades to counter + WARNING + the trip status
surfaced on the daemon-settings detail read — and that downgrade is
DISCLOSED. A chat/push channel remains a NAMED FUTURE.

**Why this file is large (~1,070 lines).** The 1,000–3,000-line band
requires a why-large note: the orchestrator deliberately keeps the
admission ladder, the shadow/apply split, the cost metering + trip,
the throttle breakers and the operator notify in ONE module because
they share one consult-time state machine — every gate reads
cumulative state the others write, and splitting them would put the
ordering contract across a module boundary where the compiler cannot
hold it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Mapping, Protocol, Sequence

from daemon.services.decision.logging_safe import log_exception
from daemon.services.decision import metrics
from daemon.services.decision.config import (
    DEFAULT_COST_CEILING_USD,
    DEFAULT_TOKEN_CEILING,
    DecisionConfig,
)
from daemon.services.decision.kill import KillChannelResolver
from daemon.services.decision.port import OverlayConsultResult
from daemon.services.decision.registry import SeamRegistry
from daemon.services.decision.shadow import ShadowRow, ShadowWriter
from daemon.services.decision.types import (
    VERDICT_NO_OVERLAY,
    VERDICT_UNANSWERED,
    DecisionAnswer,
    DecisionStage,
    FailureClass,
    OverlayOutcome,
    ProvisioningState,
    SuppressionReason,
    outcome_summary,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CallBudget",
    "CircuitBreaker",
    "CostAccumulator",
    "CostTotals",
    "DecisionOverlay",
    "GLOBAL_COST_CEILING_USD",
    "InMemoryCostAccumulator",
    "PER_SEAM_COST_SHARE_USD",
    "TripRecord",
]


#: The ratified cost ceiling — **USD 50/month, GLOBAL** (FLAG1). It is
#: the BINDING ceiling; the per-seam share is only a guard so that one
#: seam cannot consume the whole budget.
GLOBAL_COST_CEILING_USD = DEFAULT_COST_CEILING_USD

#: The per-seam share of the global ceiling — half of it, so one seam
#: can never eat the whole budget (``controls-and-phasing.md`` §5).
PER_SEAM_COST_SHARE_USD = GLOBAL_COST_CEILING_USD * 0.5

#: The board item's author stamp — the daemon itself, never an agent.
BOARD_AUTHOR = "ensemble-daemon"


# ── the cost ledger ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class CostTotals:
    """The month-to-date totals after one recorded attempt.

    Attributes:
        month_to_date_usd: The GLOBAL month-to-date USD spend.
        seam_month_to_date_usd: The seam's month-to-date USD spend.
        month_to_date_tokens: The GLOBAL month-to-date tokens.
        seam_month_to_date_tokens: The seam's month-to-date tokens.
    """

    month_to_date_usd: float = 0.0
    seam_month_to_date_usd: float = 0.0
    month_to_date_tokens: int = 0
    seam_month_to_date_tokens: int = 0


class CostAccumulator(Protocol):
    """The month-to-date cost ledger the ceiling is evaluated against.

    **The ledger MUST be durable across a daemon restart** — a RAM-only
    counter lets a restart reset month-to-date spend and defeats the
    global ceiling (``FLAG3``). The in-memory implementation below is
    the DEFAULT for a daemon that has no ledger wired yet; the wiring
    round must bind a durable one.

    The wiring round's exact obligation — a DB-backed implementation of
    this protocol, with ONE method:

    * ``record(seam_id, *, usd, tokens)`` — append one attempt's metered
      spend to the CURRENT UTC month's ledger and return the new
      month-to-date totals (global + per seam, USD + tokens);
    * the ledger is keyed by (utc_month, seam_id) so a new month starts
      at zero WITHOUT a reset job;
    * it is DB-backed and MUST run off the event loop (``db_offload``).

    ``usd`` is the provider-reported cost when there is one (else 0.0);
    ``tokens`` is the metered token count (0 when the ``usage`` block
    was absent).
    """

    async def record(
        self, seam_id: str, *, usd: float, tokens: int
    ) -> CostTotals:
        """Record one attempt's metered spend; return the new totals."""
        ...


class InMemoryCostAccumulator:
    """The default, process-scoped ledger.

    ⚠️ NOT durable: a daemon restart resets the month-to-date totals.
    The wiring round replaces it with a DB-backed implementation of the
    same protocol (see :class:`CostAccumulator`).
    """

    def __init__(self) -> None:
        self._month: str = _utc_month()
        self._usd = 0.0
        self._tokens = 0
        self._per_seam_usd: dict[str, float] = {}
        self._per_seam_tokens: dict[str, int] = {}

    def _roll(self) -> None:
        """Start a new month when the UTC month changes."""
        month = _utc_month()
        if month != self._month:
            self._month = month
            self._usd = 0.0
            self._tokens = 0
            self._per_seam_usd = {}
            self._per_seam_tokens = {}

    async def record(
        self, seam_id: str, *, usd: float, tokens: int
    ) -> CostTotals:
        self._roll()
        self._usd += float(usd)
        self._tokens += int(tokens)
        self._per_seam_usd[seam_id] = self._per_seam_usd.get(seam_id, 0.0) + float(usd)
        self._per_seam_tokens[seam_id] = (
            self._per_seam_tokens.get(seam_id, 0) + int(tokens)
        )
        return CostTotals(
            month_to_date_usd=self._usd,
            seam_month_to_date_usd=self._per_seam_usd.get(seam_id, 0.0),
            month_to_date_tokens=self._tokens,
            seam_month_to_date_tokens=self._per_seam_tokens.get(seam_id, 0),
        )


def _utc_month() -> str:
    """The current UTC month key (``YYYY-MM``)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ── the throttle ─────────────────────────────────────────────────────────


class CircuitBreaker:
    """The consecutive-failure + fallback-rate breaker (§3.5.1).

    Per seam AND global: N consecutive failures open it; a fail-open
    share above the threshold over the window opens it too; it stays
    open for the open duration, after which ONE probe is allowed
    through.
    """

    def __init__(
        self,
        *,
        consecutive_failures: int = 5,
        fallback_rate: float = 0.5,
        window_s: float = 600.0,
        open_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._consecutive_limit = max(1, int(consecutive_failures))
        self._fallback_rate = float(fallback_rate)
        self._window = float(window_s)
        self._open_s = float(open_s)
        self._clock = clock
        self._consecutive: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._probe_used: dict[str, bool] = {}
        self._events: dict[str, Deque[tuple[float, bool]]] = {}

    def allow(self, key: str) -> bool:
        """True when a call may be attempted for ``key``."""
        opened_at = self._opened_at.get(key)
        if opened_at is None:
            return True
        now = self._clock()
        if now - opened_at < self._open_s:
            return False
        # The window elapsed — ONE probe is allowed through.
        if self._probe_used.get(key):
            return False
        self._probe_used[key] = True
        return True

    def record(self, key: str, *, failed: bool, fell_back: bool) -> None:
        """Record one attempt's outcome for ``key``."""
        now = self._clock()
        events = self._events.setdefault(key, deque())
        events.append((now, fell_back))
        while events and now - events[0][0] > self._window:
            events.popleft()
        if failed:
            count = self._consecutive.get(key, 0) + 1
            self._consecutive[key] = count
            if count >= self._consecutive_limit:
                self._open(key, now)
        else:
            self._consecutive[key] = 0
            self._close(key)
        if events:
            fallbacks = sum(1 for _, fell in events if fell)
            if len(events) >= 5 and fallbacks / len(events) > self._fallback_rate:
                self._open(key, now)

    def _open(self, key: str, now: float) -> None:
        self._opened_at[key] = now
        self._probe_used[key] = False

    def _close(self, key: str) -> None:
        self._opened_at.pop(key, None)
        self._probe_used.pop(key, None)


class CallBudget:
    """The per-seam + global rate and concurrency budgets (§3.5.2).

    Both are enforced PRE-SOCKET: a request that would exceed a budget
    is NOT sent — the registry answers from the heuristic and writes a
    suppressed row (``rate_limited`` / ``concurrency_limited``). No call
    ⇒ no cost.
    """

    def __init__(
        self,
        *,
        per_seam_rate_per_min: int = 60,
        global_rate_per_min: int = 300,
        per_seam_concurrency: int = 2,
        global_concurrency: int = 8,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._seam_rate = max(0, int(per_seam_rate_per_min))
        self._global_rate = max(0, int(global_rate_per_min))
        self._seam_concurrency = max(1, int(per_seam_concurrency))
        self._global_concurrency = max(1, int(global_concurrency))
        self._clock = clock
        self._seam_calls: dict[str, Deque[float]] = {}
        self._global_calls: Deque[float] = deque()
        self._seam_inflight: dict[str, int] = {}
        self._global_inflight = 0

    def acquire(self, seam_id: str) -> bool:
        """Reserve a slot for ``seam_id``; False when a budget is spent."""
        now = self._clock()
        self._trim(now)

        if self._seam_inflight.get(seam_id, 0) >= self._seam_concurrency:
            return False
        if self._global_inflight >= self._global_concurrency:
            return False
        if self._seam_rate and len(self._seam_calls.get(seam_id, ())) >= self._seam_rate:
            return False
        if self._global_rate and len(self._global_calls) >= self._global_rate:
            return False

        self._seam_calls.setdefault(seam_id, deque()).append(now)
        self._global_calls.append(now)
        self._seam_inflight[seam_id] = self._seam_inflight.get(seam_id, 0) + 1
        self._global_inflight += 1
        return True

    def release(self, seam_id: str) -> None:
        """Release the slot reserved by :meth:`acquire`."""
        self._seam_inflight[seam_id] = max(
            0, self._seam_inflight.get(seam_id, 0) - 1
        )
        self._global_inflight = max(0, self._global_inflight - 1)

    def _trim(self, now: float) -> None:
        """Drop the calls older than the rate window (60 s)."""
        for seam_id, calls in list(self._seam_calls.items()):
            while calls and now - calls[0] > 60.0:
                calls.popleft()
            if not calls:
                self._seam_calls.pop(seam_id, None)
        while self._global_calls and now - self._global_calls[0] > 60.0:
            self._global_calls.popleft()


@dataclass(frozen=True)
class TripRecord:
    """One auto-trip — what tripped, what was written, what was notified."""

    reason: str
    seam_ids: tuple[str, ...]
    global_off: bool
    month_to_date_usd: float
    ceiling_usd: float
    notified: bool = False
    board_item: bool = False
    error: str | None = None


class DecisionOverlay:
    """The overlay orchestrator — consult, log, measure, pay, trip.

    Args:
        registry: The seam registry (the registry owns the call and
            the fail-open fence).
        kill: The kill-channel resolver (read PER CONSULT, served from
            the in-process resolved-state cache).
        shadow_writer: The shadow writer (the A/B record).
        config: The resolved configuration.
        accumulator: The month-to-date cost ledger (durable-capable).
        breaker: The circuit breaker.
        budget: The rate/concurrency budget.
        notify_sink: An INJECTABLE operator-notify sink. When it is
            absent the default notify writes a board item (when a
            ``board_service`` is injected) + a daemon-log WARNING row +
            the counter.
        board_service: The injected ``BoardService`` (L9). Its
            ``create_item`` is called DIRECTLY (sync) off the event
            loop.
    """

    def __init__(
        self,
        *,
        registry: SeamRegistry,
        kill: KillChannelResolver,
        shadow_writer: ShadowWriter,
        config: DecisionConfig,
        accumulator: CostAccumulator | None = None,
        configured: bool | None = None,
        breaker: CircuitBreaker | None = None,
        budget: CallBudget | None = None,
        notify_sink: Callable[[dict[str, Any]], None] | None = None,
        board_service: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry
        self._kill = kill
        self._shadow = shadow_writer
        self._config = config
        # Q2 — an EXPLICIT decision signal (DECISION_* in the env, or a
        # ``decision:`` block in config.yaml). ``None`` ⇒ derive from the
        # config object (duck-typed: ``is_configured()`` /
        # ``explicitly_configured``, then the house ``has_explicit_decision_signal``).
        self._configured_override = configured
        self._accumulator = accumulator or InMemoryCostAccumulator()
        self._breaker = breaker or CircuitBreaker(clock=clock)
        self._budget = budget or CallBudget(clock=clock)
        self._notify_sink = notify_sink
        self._board_service = board_service
        self._clock = clock
        self._trips: list[TripRecord] = []
        self._provisioning: dict[str, ProvisioningState] = {}
        self._consults: dict[str, int] = {}
        # (c) — the STRONG-REFERENCE set for detached consults. A bare
        # ``asyncio.create_task`` result is GC-eligible MID-FLIGHT: the
        # event loop holds only a weak reference, so an unreferenced
        # task can be silently garbage-collected before it runs or
        # completes — no error, no row. Every detached task is added
        # HERE on dispatch and discarded in the payload's ``finally``
        # (the loop keeps the set alive for the overlay's lifetime).
        self._detached_tasks: set[asyncio.Task[None]] = set()

    # ── the consult ─────────────────────────────────────────────────────

    async def consult(
        self,
        seam_id: str,
        *,
        heuristic_outcome: Any,
        context: Mapping[str, Any],
        force_shadow: bool = False,
    ) -> OverlayConsultResult:
        """Consult the overlay for one seam — the INLINE entry.

        This is the path a DECIDING consult uses (an applied answer is
        needed synchronously by definition). The SHADOW path uses
        :meth:`consult_detached`, which runs this same consult inside a
        bounded background task and returns immediately.

        Args:
            seam_id: The seam to consult.
            heuristic_outcome: The caller's own answer — the permanent
                default and the fallback (D2).
            context: The seam's context (the seam's ``build_input``
                reads it).
            force_shadow: The STRUCTURAL shadow/deciding split (council
                round 3, shape point 8). ``True`` — the detached path —
                FORBIDS apply regardless of the resolved stage: an
                applied answer is never detached, so a detached consult
                can never move the caller's outcome even if a P4 seam
                were dispatched through this path. ``False`` (the
                inline default) keeps the stage-driven apply gate for
                deciding seams.

        Returns:
            The consult's terminal state. Never raises.
        """
        record = self._registry.get(seam_id)
        if record is None:
            return OverlayConsultResult(
                outcome=heuristic_outcome,
                state=OverlayOutcome.SUPPRESSED.value,
                suppression=SuppressionReason.EXCLUDED_SEAM.value,
            )

        self._consults[seam_id] = self._consults.get(seam_id, 0) + 1

        # ── the CONSULT gate (FLAG12) ────────────────────────────────
        state = await self._kill.read_state(seam_id)
        if not state.readable:
            return self._suppressed(
                seam_id,
                heuristic_outcome,
                SuppressionReason.SWITCH_UNREADABLE,
                state=state,
            )

        gate_ok = True
        if record.gate_switch is not None:
            try:
                gate_ok = bool(record.gate_switch())
            except Exception as exc:  # noqa: BLE001 — an unreadable gate fails closed
                log_exception(
                    logger,
                    logging.WARNING,
                    "decision seam %s gate switch unreadable (fail-closed)",
                    exc,
                    seam_id,
                )
                return self._suppressed(
                    seam_id,
                    heuristic_outcome,
                    SuppressionReason.SWITCH_UNREADABLE,
                    state=state,
                )
        if not (state.global_on and state.seam_on and gate_ok):
            return self._suppressed(
                seam_id,
                heuristic_outcome,
                SuppressionReason.SWITCH_OFF,
                state=state,
            )

        # Q2 — the CONFIGURED predicate is an EXPLICIT decision signal,
        # never the inherited OPENAI_* values (a PAID upstream).
        if not self._is_configured():
            return self._suppressed(
                seam_id,
                heuristic_outcome,
                SuppressionReason.NOT_CONFIGURED,
                state=state,
            )

        # ── the throttle (pre-socket) ────────────────────────────────
        if not self._breaker.allow(seam_id) or not self._breaker.allow("__global__"):
            return self._suppressed(
                seam_id,
                heuristic_outcome,
                SuppressionReason.BREAKER_OPEN,
                state=state,
            )
        if not self._budget.acquire(seam_id):
            return self._suppressed(
                seam_id,
                heuristic_outcome,
                SuppressionReason.RATE_LIMITED,
                state=state,
            )

        # ── the fenced consult ───────────────────────────────────────
        deciding = state.stage is DecisionStage.DECIDING
        # The APPLY gate — force_shadow (the detached path) never
        # applies, structurally (shape point 8: "apply is never
        # detached").
        allow_apply = (
            deciding
            and self._registry.is_deciding_permitted(seam_id)
            and not force_shadow
        )
        floor = record.confidence_floor if deciding else None
        try:
            result = await self._registry.execute(
                seam_id,
                heuristic_outcome=heuristic_outcome,
                context=context,
                floor=floor,
                allow_apply=allow_apply,
            )
        finally:
            self._budget.release(seam_id)

        mode = state.stage.value

        # The breaker's bookkeeping — a failure advances the
        # consecutive-failure counter AND counts towards the
        # fallback-rate window (a timeout counts towards BOTH).
        failed = result.failure_class is not None
        self._breaker.record(seam_id, failed=failed, fell_back=failed)
        self._breaker.record("__global__", failed=failed, fell_back=failed)

        # The attempt counter — incremented for every attempt that
        # REACHED THE SOCKET, before the outcome is known (§3.5.3: a
        # timeout still bills).
        if result.reached_socket:
            metrics.record_attempt(seam_id, mode)

        if result.suppression is not None:
            # A pre-flight suppression (rows 1–5) — NO call was made.
            metrics.record_suppressed(seam_id, result.suppression.value)
            self._write_shadow_row(
                seam_id,
                record.question_id or seam_id,
                mode,
                overlay=None,
                heuristic=heuristic_outcome,
                verdict=VERDICT_NO_OVERLAY,
                latency_ms=0,
                failure_class="",
                reason=result.suppression.value,
                switch_state=_switch_state_label(state),
            )
            return OverlayConsultResult(
                outcome=heuristic_outcome,
                state=OverlayOutcome.SUPPRESSED.value,
                suppression=result.suppression.value,
            )

        metrics.record_latency(seam_id, result.latency_s)

        # ── the cost accounting (L8) — BEFORE the outcome is applied ──
        # The returned value is the BILLED cost (the provider's reported
        # cost, the operator-rate conversion, or the D3 floor) — the
        # outward port reports THAT, not the reported cost alone.
        billed_usd = await self._account_cost(
            seam_id,
            usd=result.cost_usd,
            tokens=result.total_tokens or 0,
            reached_socket=result.reached_socket,
            heuristic_outcome=heuristic_outcome,
            mode=mode,
            state=state,
        )

        if result.failure_class is not None:
            failure = result.failure_class.value
            # FIX A — an unanswered question is an EXPLICIT outcome: the
            # shadow row's verdict carries "unanswered" (the metric's
            # verdict label keeps its three values — no new label value,
            # so the zero-headroom band stays 411) and the consult is
            # COUNTED under the existing ``unmatched_answer`` class.
            unanswered = bool(
                result.failure_class is FailureClass.UNMATCHED_ANSWER
                and result.unanswered_questions
            )
            metrics.record_failure(seam_id, failure, mode)
            if mode == "deciding":
                # R-B: fallbacks_total == failures_total, scoped to
                # mode=deciding.
                metrics.record_fallback(seam_id, mode)
            if result.failure_class is FailureClass.BELOW_FLOOR:
                # The subset view — never an additive term.
                metrics.record_floor_rejection(seam_id, mode)
            self._apply_provisioning_state(seam_id, result.provisioning_state)
            self._write_shadow_row(
                seam_id,
                record.question_id or seam_id,
                mode,
                overlay=None,
                heuristic=heuristic_outcome,
                verdict=(
                    VERDICT_UNANSWERED if unanswered else VERDICT_NO_OVERLAY
                ),
                latency_ms=int(result.latency_s * 1000),
                failure_class=failure,
                reason="",
                switch_state=_switch_state_label(state),
            )
            return OverlayConsultResult(
                outcome=heuristic_outcome,
                state=OverlayOutcome.FAILED.value,
                failure_class=failure,
                latency_s=result.latency_s,
                cost_usd=billed_usd,
            )

        # ── success ──────────────────────────────────────────────────
        self._apply_provisioning_state(seam_id, result.provisioning_state)
        metrics.record_shadow_agreement(seam_id, result.verdict)
        if result.verdict == "disagree":
            metrics.record_shadow_disagreement(seam_id)
        if result.selection_overlap_ratio is not None:
            metrics.record_shadow_selection_agreement(seam_id)
            metrics.set_selection_overlap_ratio(seam_id, result.selection_overlap_ratio)
        self._write_shadow_row(
            seam_id,
            record.question_id or seam_id,
            mode,
            overlay=_overlay_summary(result.overlay_outcome, result.candidate_ids),
            heuristic=heuristic_outcome,
            verdict=result.verdict,
            latency_ms=int(result.latency_s * 1000),
            failure_class="",
            reason="",
            switch_state=_switch_state_label(state),
        )

        if result.refused:
            return OverlayConsultResult(
                outcome=heuristic_outcome,
                state=OverlayOutcome.REFUSED.value,
                verdict=result.verdict,
                latency_s=result.latency_s,
                cost_usd=billed_usd,
            )
        if result.applied:
            return OverlayConsultResult(
                outcome=result.outcome,
                state=OverlayOutcome.APPLIED.value,
                applied=True,
                verdict=result.verdict,
                latency_s=result.latency_s,
                cost_usd=billed_usd,
            )
        return OverlayConsultResult(
            outcome=heuristic_outcome,
            state=OverlayOutcome.LOGGED.value,
            verdict=result.verdict,
            latency_s=result.latency_s,
            cost_usd=billed_usd,
        )

    # ── the DETACHED entry ((c) — the shadow consult never blocks) ─────

    def consult_detached(
        self,
        seam_id: str,
        *,
        heuristic_outcome: Any,
        context: Mapping[str, Any],
    ) -> None:
        """Dispatch the consult as a BOUNDED BACKGROUND task — the (c)
        entry. Returns IMMEDIATELY; the caller's path is untouched.

        Council round 3 shape, point by point:

        1. **Capture at the hook** — the caller captures
           ``heuristic_ids`` / ``candidate_ids`` / ``query`` and hands
           them here; this method dispatches and returns. The caller's
           ``search()`` is byte-identical whether the overlay is armed
           or not.
        2. **Strong reference** — the task is added to
           :attr:`_detached_tasks` on dispatch and discarded in the
           payload's ``finally`` (a bare ``create_task`` result is
           GC-eligible mid-flight).
        3. **Per-task budget = the configured timeout** — the socket
           phase inside the task is bounded by
           ``DecisionConfig.timeout_s`` (the client's own
           ``wait_for``); concurrency is throttled by the EXISTING
           breaker / CallBudget, which run inside the task pre-socket.
        4. **The A/B row lands INCLUDING timeout rows** — the whole
           consult lifecycle runs inside the task, so a client timeout
           still writes its shadow row before the task exits.
        5. **Kill read, cost accounting and auto-trip stay INSIDE the
           task** — spend accrues on consults, not dispatches; the D3
           floor exists for the reached-socket-no-usage case, which is
           only knowable inside the task.
        6. **Detachment semantic (disclosed):** the kill gate is read
           INSIDE the task, so a kill written BETWEEN dispatch and
           execution may not be honoured for that one in-flight
           consult. Bounded: one consult.
        7. **Exceptions contained, TYPE ONLY** — the payload never
           propagates; the log carries ``type(exc).__name__`` and
           nothing else (no message, no payload, no context).
        8. **Shadow ⇒ detached; deciding ⇒ inline** — the payload runs
           the consult with ``force_shadow=True``, so a detached
           consult can NEVER apply (an applied answer is needed
           synchronously by definition). Deciding call sites keep the
           inline :meth:`consult`.

        Never raises: even the dispatch itself is guarded (a caller
        without a running loop logs and returns).
        """
        try:
            task = asyncio.create_task(
                self._detached_payload(
                    seam_id,
                    heuristic_outcome=heuristic_outcome,
                    context=context,
                ),
                name=f"decision-overlay:consult:{seam_id}",
            )
        except RuntimeError:
            # No running loop in this thread — the consult cannot be
            # scheduled. Never raise into the caller (the hook is
            # non-fatal by contract); the consult is simply not made,
            # and the heuristic outcome stands.
            logger.warning(
                "decision detached consult not dispatched for %s "
                "(no running event loop)",
                seam_id,
            )
            return
        # Point 2 — the strong reference, BEFORE any await can run.
        self._detached_tasks.add(task)
        # Point 2 — discarded in the payload's ``finally``.

    async def _detached_payload(
        self,
        seam_id: str,
        *,
        heuristic_outcome: Any,
        context: Mapping[str, Any],
    ) -> None:
        """The detached task's body: the WHOLE consult lifecycle,
        contained (shape points 4–7)."""
        task = asyncio.current_task()
        try:
            await self.consult(
                seam_id,
                heuristic_outcome=heuristic_outcome,
                context=context,
                force_shadow=True,
            )
        except Exception as exc:  # noqa: BLE001 — point 7: contained
            # TYPE ONLY — never the message, never the payload, never
            # the context.
            log_exception(
                logger,
                logging.WARNING,
                "decision detached consult failed (contained)",
                exc,
            )
        finally:
            # Point 2 — drop the strong reference when the task is
            # finished (done OR failed).
            if task is not None:
                self._detached_tasks.discard(task)

    async def wait_for_detached(self, timeout_s: float = 10.0) -> int:
        """Await every in-flight detached consult — the test + shutdown
        drain. Returns how many tasks completed within the budget."""
        tasks = [t for t in self._detached_tasks if not t.done()]
        if not tasks:
            return 0
        done, _pending = await asyncio.wait(tasks, timeout=timeout_s)
        return len(done)

    # ── cost + the auto-trip ───────────────────────────────────────────

    async def _account_cost(
        self,
        seam_id: str,
        *,
        usd: float,
        tokens: int,
        reached_socket: bool,
        heuristic_outcome: Any,
        mode: str,
        state: Any,
    ) -> float:
        """Meter one attempt and evaluate the ceiling (L8 + D3).

        Returns:
            The BILLED cost of the attempt in USD — what the ledger was
            actually charged (the provider's reported cost, the
            operator-rate conversion, or the D3 floor). This is the
            value the OUTWARD port reports as ``cost_usd``; the
            provider-REPORTED cost alone would be a mismatch (a timeout
            reports 0.0 upstream but bills the floor).
        

        Four paths, in order:

        1. the provider REPORTED a cost → the USD accumulator;
        2. no reported cost, but an OPERATOR rate exists for the model
           → the tokens are converted → the USD accumulator;
        3. the attempt REACHED THE SOCKET but carries no usage (a
           timeout, a disconnect-after-send, a malformed envelope) →
           the OPERATOR-CONFIGURED per-attempt conservative floor → the
           USD accumulator (D3: a timeout still bills — the cheapest
           adversarial trick is to be slow, and a zero floor lets
           timeout-only traffic bypass BOTH the USD ceiling and the
           token fail-safe forever);
        4. neither → the consult is metered in TOKENS, a
           ``pricing_unconfigured`` suppressed row is emitted, and the
           TOKEN ceiling is the fail-safe.
        """
        spend = float(usd or 0.0)
        priced = False
        if spend <= 0.0:
            rate = self._config.rate_for(self._config.model)
            if rate is not None and tokens > 0:
                spend = (float(tokens) / 1_000_000.0) * float(rate)
                priced = True
        if spend <= 0.0 and tokens <= 0 and reached_socket:
            # D3 — the conservative floor: the attempt reached the
            # socket (it bills server-side) but carried NO usage at all
            # (a timeout, a disconnect-after-send, a malformed
            # envelope). An attempt that DID report tokens is metered on
            # the L8 token path instead — it does not lack usage.
            floor = float(self._config.cost_floor_per_attempt_usd)
            if floor > 0:
                spend = floor
                priced = True

        try:
            totals = await self._accumulator.record(
                seam_id, usd=spend, tokens=int(tokens or 0)
            )
        except Exception as exc:  # noqa: BLE001 — never fail a caller
            log_exception(
                logger,
                logging.WARNING,
                "decision cost accounting failed (non-fatal)",
                exc,
            )
            return 0.0

        if tokens > 0:
            # The token meter — always on, and the fail-safe when no USD
            # rate is configured for the model (L8).
            metrics.record_cost_tokens(seam_id, tokens)
        if spend > 0.0:
            metrics.record_cost(seam_id, spend)
        else:
            if tokens > 0:
                # The visible degradation (L8): the control is metered in
                # TOKENS because no USD rate is configured.
                metrics.record_suppressed(seam_id, SuppressionReason.PRICING_UNCONFIGURED.value)
                self._write_shadow_row(
                    seam_id,
                    self._question_id(seam_id),
                    mode,
                    overlay=None,
                    heuristic=heuristic_outcome,
                    verdict=VERDICT_NO_OVERLAY,
                    latency_ms=0,
                    failure_class="",
                    reason=SuppressionReason.PRICING_UNCONFIGURED.value,
                    switch_state=_switch_state_label(state),
                )

        ceiling_usd = float(self._config.cost_ceiling_usd or GLOBAL_COST_CEILING_USD)
        if totals.month_to_date_usd >= ceiling_usd:
            await self._trip(
                reason="global_ceiling",
                seam_ids=self._registry.seam_ids(),
                global_off=True,
                month_to_date_usd=totals.month_to_date_usd,
                ceiling_usd=ceiling_usd,
            )
            return spend

        share = ceiling_usd * 0.5
        if totals.seam_month_to_date_usd >= share:
            await self._trip(
                reason="per_seam_share",
                seam_ids=(seam_id,),
                global_off=False,
                month_to_date_usd=totals.seam_month_to_date_usd,
                ceiling_usd=share,
            )
            return spend

        token_ceiling = int(self._config.token_ceiling or DEFAULT_TOKEN_CEILING)
        if totals.month_to_date_tokens >= token_ceiling:
            await self._trip(
                reason="global_ceiling",
                seam_ids=self._registry.seam_ids(),
                global_off=True,
                month_to_date_usd=totals.month_to_date_usd,
                ceiling_usd=ceiling_usd,
            )
        # The BILLED cost of this attempt — what the outward port
        # reports (the port's contract says "the billed cost", not the
        # provider-reported cost).
        return spend

    async def _trip(
        self,
        *,
        reason: str,
        seam_ids: Sequence[str],
        global_off: bool,
        month_to_date_usd: float,
        ceiling_usd: float,
    ) -> TripRecord:
        """Trip the module to shadow-off + notify the operator.

        The write is BOTH keys per seam (``stage := 'shadow'`` AND
        ``enabled := 0``) plus the global master OFF when the ceiling is
        global (``FLAG2``).
        """
        metrics.record_cost_trip(reason)
        error: str | None = None
        try:
            write = await self._kill.trip_shadow_off(seam_ids, global_off=global_off)
            if not write.ok:
                error = write.error or "read-back mismatch"
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"

        payload = {
            "event": "decision_overlay_cost_trip",
            "reason": reason,
            "seam_ids": list(seam_ids),
            "global_off": global_off,
            "month_to_date_usd": month_to_date_usd,
            "ceiling_usd": ceiling_usd,
            "error": error,
        }

        notified = False
        board_item = False
        sink = self._notify_sink
        if sink is not None:
            try:
                sink(payload)
                notified = True
            except Exception as exc:  # noqa: BLE001 — a broken sink never fails a caller
                log_exception(
                    logger,
                    logging.WARNING,
                    "decision cost-trip notify sink failed (non-fatal)",
                    exc,
                )
        else:
            board_item = await self._write_board_item(payload)

        logger.warning(
            "event=decision_overlay_cost_trip reason=%s month_to_date_usd=%.6f "
            "ceiling_usd=%.2f seams=%s global_off=%s error=%s",
            reason,
            month_to_date_usd,
            ceiling_usd,
            ",".join(seam_ids) or "-",
            global_off,
            error or "-",
        )
        record = TripRecord(
            reason=reason,
            seam_ids=tuple(seam_ids),
            global_off=global_off,
            month_to_date_usd=month_to_date_usd,
            ceiling_usd=ceiling_usd,
            notified=notified or board_item,
            board_item=board_item,
            error=error,
        )
        self._trips.append(record)
        return record

    async def _write_board_item(self, payload: Mapping[str, Any]) -> bool:
        """Write the board item — the ALWAYS surface (L9).

        ``BoardService.create_item`` is SYNC (it opens its own Session
        and its own transaction, guarded by a process-level RLock), so
        it is safe to call from a background thread. It is called
        DIRECTLY (not through the anomaly lane's async wrapper) and run
        OFF the event loop (the house rule: no sync DB on the loop).

        Returns:
            True when the board item was written.
        """
        service = self._board_service
        if service is None:
            return False
        title = "Decision overlay cost ceiling tripped"
        body = (
            f"reason={payload.get('reason')} "
            f"month_to_date_usd={payload.get('month_to_date_usd'):.6f} "
            f"ceiling_usd={payload.get('ceiling_usd'):.2f} "
            f"seams={','.join(payload.get('seam_ids') or []) or '-'} "
            f"global_off={payload.get('global_off')}"
        )
        try:
            from daemon.services.db_offload import offload

            await offload(
                service.create_item,
                title=title,
                body_md=body,
                tags=["decision-overlay", "cost-ceiling"],
                priority=1,
                created_by_instance=BOARD_AUTHOR,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — a broken board never fails a caller
            log_exception(
                logger,
                logging.WARNING,
                "decision cost-trip board item failed (non-fatal)",
                exc,
            )
            return False

    def trip_status(self) -> dict[str, Any]:
        """The trip status — surfaced on the daemon-settings detail read.

        This is the DEFAULT operator-notify surface: the counter
        (``decision_overlay_cost_trip_total``), the daemon-log WARNING
        row, the board item (when a board service is injected) and this
        status block.
        """
        latest = self._trips[-1] if self._trips else None
        return {
            "tripped": latest is not None,
            "reason": latest.reason if latest else None,
            "seam_ids": list(latest.seam_ids) if latest else [],
            "global_off": latest.global_off if latest else False,
            "month_to_date_usd": latest.month_to_date_usd if latest else 0.0,
            "ceiling_usd": float(self._config.cost_ceiling_usd),
            "per_seam_share_usd": float(self._config.cost_ceiling_usd) * 0.5,
            "token_ceiling": int(self._config.token_ceiling),
            "notified": latest.notified if latest else False,
            "board_item": latest.board_item if latest else False,
            "error": latest.error if latest else None,
        }

    @property
    def trips(self) -> tuple[TripRecord, ...]:
        """Every trip this overlay has fired."""
        return tuple(self._trips)

    # ── helpers ─────────────────────────────────────────────────────────

    def _question_id(self, seam_id: str) -> str:
        """The seam's opaque question id (never user text)."""
        record = self._registry.get(seam_id)
        return (record.question_id if record else "") or seam_id

    def _suppressed(
        self,
        seam_id: str,
        heuristic_outcome: Any,
        reason: SuppressionReason,
        *,
        state: Any,
    ) -> OverlayConsultResult:
        """Record a suppression: NO call, NO cost, one shadow row."""
        metrics.record_suppressed(seam_id, reason.value)
        self._write_shadow_row(
            seam_id,
            self._question_id(seam_id),
            state.stage.value if state is not None else DecisionStage.SHADOW.value,
            overlay=None,
            heuristic=heuristic_outcome,
            verdict=VERDICT_NO_OVERLAY,
            latency_ms=0,
            failure_class="",
            reason=reason.value,
            switch_state=_switch_state_label(state),
        )
        return OverlayConsultResult(
            outcome=heuristic_outcome,
            state=OverlayOutcome.SUPPRESSED.value,
            suppression=reason.value,
        )

    def _write_shadow_row(
        self,
        seam_id: str,
        question_id: str,
        mode: str,
        *,
        overlay: Mapping[str, Any] | None,
        heuristic: Any,
        verdict: str,
        latency_ms: int,
        failure_class: str,
        reason: str,
        switch_state: str,
    ) -> None:
        """Write ONE content-free shadow row (the A/B record)."""
        try:
            self._shadow.write(
                ShadowRow(
                    seam_id=seam_id,
                    question_id=question_id,
                    mode=mode,
                    overlay_outcome=overlay,
                    heuristic_outcome=outcome_summary(heuristic),
                    verdict=verdict,
                    latency_ms=latency_ms,
                    failure_class=failure_class,
                    model_id=self._config.model,
                    reason=reason,
                    switch_state=switch_state,
                )
            )
        except Exception as exc:  # noqa: BLE001 — the writer never fails a caller
            log_exception(
                logger,
                logging.DEBUG,
                "decision shadow row write failed (non-fatal)",
                exc,
            )

    def _apply_provisioning_state(
        self, seam_id: str, state: ProvisioningState | None
    ) -> None:
        """Apply a provisioning verdict — a 502 NEVER moves it.

        Only a probe-provable condition may set ``provisioning_missing``
        or ``allow_list_excluded``; every other outcome leaves the state
        UNCHANGED (a transient 502 must never park a seam).
        """
        if state is None:
            return
        if self._provisioning.get(seam_id) == state:
            return
        self._provisioning[seam_id] = state
        metrics.set_provisioning_state(seam_id, state.value)
        if state is ProvisioningState.PROVISIONING_MISSING:
            metrics.record_liveness_alarm(seam_id, "provisioning_missing")
        elif state is ProvisioningState.ALLOW_LIST_EXCLUDED:
            metrics.record_liveness_alarm(seam_id, "allow_list_excluded")

    def _is_configured(self) -> bool:
        """The Q2 CONFIGURED predicate.

        An explicit signal may be passed in (the wiring round's house
        config carries no signal field — ``daemon/config.py`` is its
        territory, not mine — so the overlay accepts the signal as a
        kwarg for the configured case). Otherwise the predicate is
        resolved duck-typed from the config object, falling back to the
        package-level signal check over the environment.
        """
        if self._configured_override is not None:
            return bool(self._configured_override)
        cfg = self._config
        if hasattr(cfg, "is_configured") and callable(cfg.is_configured):
            try:
                return bool(cfg.is_configured())
            except TypeError:
                pass
        if hasattr(cfg, "explicitly_configured"):
            return bool(cfg.explicitly_configured)
        return DecisionConfig.has_explicit_decision_signal()

    def provisioning_state(self, seam_id: str) -> ProvisioningState:
        """The seam's current provisioning state."""
        return self._provisioning.get(seam_id, ProvisioningState.OK)


def _switch_state_label(state: Any) -> str:
    """A compact, content-free label for the switch state."""
    if state is None:
        return "unknown"
    if not getattr(state, "readable", True):
        return "unreadable"
    if getattr(state, "global_on", False) and getattr(state, "seam_on", False):
        return "on"
    return "off"


def _overlay_summary(
    answer: DecisionAnswer | None, candidate_ids: Sequence[str]
) -> dict[str, Any] | None:
    """A content-free summary of the overlay's answer (ids/scores only)."""
    if answer is None:
        return None
    summary: dict[str, Any] = {"kind": answer.type}
    if answer.choice is not None:
        summary["choice"] = answer.choice
    if answer.probability is not None:
        summary["probability"] = answer.probability
    if answer.confidence is not None:
        summary["confidence"] = answer.confidence
    if answer.score is not None:
        summary["score"] = answer.score
    ranked = answer.ranked_values()
    if ranked:
        allowed = set(candidate_ids)
        summary["ids"] = [
            value for value in ranked if not allowed or value in allowed
        ][:32]
    return summary
