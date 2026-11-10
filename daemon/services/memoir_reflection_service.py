"""Memoir Store v2 — the REFLECTION ENGINE ("the dream").

Phase 2 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase2-plan.md`` T2.1/T2.2;
flags V2-5, V2-6, V2-7, V2-13, V2-15, V2-16, V2-17, V2-19, V2-24, V2-27,
V2-30, V2-31, V2-35, V2-Q9).

THE DREAM is the layer where raw episodes become BELIEFS. Ari writes
episodes (append-only, instant, never blocking the turn); this engine
periodically looks at what she has written, asks a model to CONSOLIDATE
it into a small set of cited claims, and writes those claims into
``memoir_syntheses``. The recall then serves the beliefs FIRST and the
raw episodes after them.

THE TWO CADENCES (§3.1 / V2-D 2.1, Register #19 — load-bearing):

1. the NIGHTLY pass — a bounded consolidation inside the quiet hours
   (``02:00 <= now < 04:00 UTC``), oldest-unreflected-first, capped at
   30 episodes, walking the pre-cutover backlog without the 2-day
   window restriction (C2 / V2-27);
2. the IN-DAY pass — an idle-debounce pass whose window starts at 60 s
   and DOUBLES on every burst up to a 960 s ceiling, with a 3600 s
   force-fire so a continuous conversation stream can never defer the
   dream indefinitely.

Both live in ONE sequential worker tick, and the NIGHTLY GATE IS
EVALUATED FIRST (V2-24): the nightly pass advances the ``reflected_at``
watermarks, so the in-day branch of the NEXT tick sees a derived sum
below the threshold and idles cleanly. The order is not an optimisation
— it is the reason the two cadences cannot race.

THE TRIGGER IS DERIVED PER TICK (V2-D 2.2 / Register #4): the sum comes
from a SQL query over the unreflected episodes, never from an in-memory
rolling counter and never from a delta-notify. A rolling counter
double-counts on a re-score, dies on restart, and couples two service
lifecycles; a derived query is restart-durable and self-clearing.

THE BUDGETS (§3.2) are a CONTRACT, not a suggestion: partial-commit
everywhere, abort nowhere, defer on time. Every bound has exactly one
enforcement point and a named breach behaviour:

======================  =====  ==========================================
Bound                   Value  Breach behaviour
======================  =====  ==========================================
syntheses / pass        5      stop accepting, keep what is committed
chars / synthesis        1000   REJECT the row (``claim_too_long``)
LLM calls / pass        4      stop the pass, keep what is committed
tick budget             60.0s  defer the remainder to the next pass
per-call timeout        45.0s  the call fails, the pass continues
scan window             2 days the nightly catch-up ignores it (C2)
nightly cap             30     the remainder is deferred
======================  =====  ==========================================

THE 1000-CHARACTER BOUND REJECTS, NEVER TRUNCATES: a truncated claim is
a FALSE proposition — it says something the model did not say, and it
says it with the authority of a citation.

THE LLM LANE is the house pattern and only the house pattern:
:func:`daemon.services.llm_failover.invoke_raw_with_failover` with a
re-entrant factory, the client built with
``httpx.Timeout(45.0, connect=5.0)``, and the token cost metered with
:func:`daemon.loader.estimate_tokens`.

THE ATOMICITY STORY (§3.2 / §3-A2): the CLAIM lives in
``memoir_reflection_state`` and is taken with a 15-minute STALE-TAKEOVER
window, so a crashed pass cannot strand the lane. The watermarks
(``reflected_at``) and the state row are committed in ONE transaction
(:meth:`MemoirReflectionRepository.commit_pass`), so the watermark never
advances ahead of the beliefs it claims to reflect. A crash between the
belief write and the watermark write is SAFE: the re-run meets the same
claims, the dedup key says "already known", and the store does not grow
a second copy.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from daemon.repositories.memoir.models import SYNTHESIS_VERBS
from daemon.services.timestamps import now_utc

logger = logging.getLogger(__name__)

#: The trigger threshold (V2-5): the derived sum that opens the in-day
#: pass. The constitutional default; the config knob is the live tier.
MEMOIR_REFLECTION_TRIGGER_SUM = 25

#: The idle-debounce base (V2-5): the FIRST idle window.
MEMOIR_REFLECTION_DEBOUNCE_BASE_S = 60.0

#: The idle-debounce ceiling (V2-5): the doubling stops here.
MEMOIR_REFLECTION_DEBOUNCE_CEILING_S = 960.0

#: The force-fire interval (V2-5): the continuous-stream escape hatch.
MEMOIR_REFLECTION_FORCE_FIRE_S = 3600.0

#: The synthesis cap per pass (§3.2).
MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS = 5

#: The claim-length cap per synthesis (§3.2) — REJECTED, never truncated.
MEMOIR_REFLECTION_MAX_CHARS_PER_SYNTHESIS = 1000

#: The LLM-call cap per pass (§3.2) — the JEV call counts towards it.
MEMOIR_REFLECTION_MAX_LLM_CALLS_PER_PASS = 4

#: The DECOUPLED constellation-extraction budget (V2-31 / SF7).
MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS = 2

#: The PER-PURPOSE cap of the constellation budget (F-2 / V2-D 3.9).
#: V2-D 3.9 splits the pass's TWO constellation calls into "max 1
#: extraction, max 1 tier-2 card summarization" — so each PURPOSE may
#: spend at most ONE call of the shared pool.
MEMOIR_CONSTELLATION_MAX_CALLS_PER_PURPOSE_PER_PASS = 1

#: The per-pass wall-clock budget (§3.2).
MEMOIR_REFLECTION_TICK_BUDGET_S = 60.0

#: The per-LLM-call timeout and its connect timeout (§3.2).
MEMOIR_REFLECTION_CALL_TIMEOUT_S = 45.0
MEMOIR_REFLECTION_CALL_CONNECT_TIMEOUT_S = 5.0

#: The nightly episode cap (§3.2).
MEMOIR_REFLECTION_NIGHTLY_CAP = 30

#: The regular scan window in days (§3.2).
MEMOIR_REFLECTION_SCAN_WINDOW_DAYS = 2

#: The nightly quiet hours, in UTC (§3.1) — ``[start, end)``.
MEMOIR_REFLECTION_NIGHTLY_START_HOUR = 2
MEMOIR_REFLECTION_NIGHTLY_END_HOUR = 4

#: The composed-prompt clamp: the prompt is COMPOSED from the episodes
#: and CLAMPED to this many tokens, so one enormous episode cannot blow
#: the context window. The clamp is on the PROMPT (an input), never on a
#: claim (an output) — a truncated claim would be a false proposition.
MEMOIR_REFLECTION_MAX_PROMPT_TOKENS = 8000

#: The confidence clamp (§3-A4): a missing or out-of-range confidence is
#: CLAMPED, never rejected — clamping a scalar is not truncating a
#: claim.
MEMOIR_REFLECTION_MIN_CONFIDENCE = 0.1
MEMOIR_REFLECTION_MAX_CONFIDENCE = 1.0

#: The star-type vocabulary (the same one the CHECK constraint enforces).
MEMOIR_STAR_TYPES: frozenset[str] = frozenset(
    {"person", "project", "place", "concept", "domain"}
)

#: The refusal markers (§3-A4): a model refusal is a VALID-EMPTY (the
#: batch is considered, zero syntheses) — never a retry loop, because a
#: refusal is deterministic.
_REFUSAL_MARKERS: tuple[str, ...] = (
    "i cannot",
    "i can't",
    "i can not",
    "i am unable",
    "i'm unable",
    "i am sorry",
    "i'm sorry",
    "as an ai",
    "i do not have",
    "i don't have",
)


class ConstellationBudgetRequiredError(RuntimeError):
    """Raised when an LLM path is taken WITHOUT a per-pass budget.

    V2-31 makes the constellation's LLM calls a MANDATED cost control:
    every call must be counted against the pass's budget.  A caller that
    reaches an LLM path with ``budget=None`` has found a bypass — the
    call is REFUSED loudly instead of being taken silently.
    """


class ConstellationCallBudget:
    """The PER-PASS LLM-call budget of the constellation layer (V2-D 3.9).

    THE BUDGET IS PER PASS, NOT PER CHUNK.  V2-D 3.9 caps the
    constellation at "max 1 extraction, max 1 tier-2 card summarization
    per PASS", and ONE pass dispatches MANY chunks — so the cap must be
    counted against the pass, never against a single chunk.  ONE budget
    object is created per pass and threaded through EVERY chunk
    invocation; each LLM call takes one unit from it.

    THE SHARED POOL + THE PER-PURPOSE CAPS (F-2): the pool holds TWO
    calls and each PURPOSE ("extraction", "tier2") may spend at most
    ONE of them.  The per-purpose cap is what makes the split of V2-D
    3.9 REAL: with a shared pool alone, a Tier-2-heavy pass could
    spend BOTH calls on Tier-2 and leave the extraction with ZERO
    (the council's own probe produced "2 tier-2 calls, 0 extraction",
    which defeats the T3.1b seed).

    THE HEURISTIC IS FREE.  The ``stars[]`` seed never touches this
    object — only the LLM fallback extraction and the Tier-2
    summarization consume it.  Blocking the LLM work must NEVER block
    the heuristic extraction (the primary mechanism).

    The count is taken AT DISPATCH (the house rule): a call that was
    dispatched counts, even if it failed.
    """

    __slots__ = ("limit", "used", "per_purpose_limit", "purpose_used")

    def __init__(
        self,
        limit: int,
        *,
        per_purpose_limit: int = MEMOIR_CONSTELLATION_MAX_CALLS_PER_PURPOSE_PER_PASS,
    ) -> None:
        self.limit = max(0, int(limit))
        self.per_purpose_limit = max(0, int(per_purpose_limit))
        self.used = 0
        self.purpose_used: dict[str, int] = {}

    @property
    def remaining(self) -> int:
        """The calls still available in this pass (the shared pool)."""
        return max(0, self.limit - self.used)

    def remaining_for(self, purpose: str) -> int:
        """The calls still available for ONE purpose."""
        used = int(self.purpose_used.get(purpose, 0))
        return max(0, self.per_purpose_limit - used)

    def take(self, purpose: str = "unspecified") -> bool:
        """Consume ONE call of ``purpose``; ``False`` when it is refused.

        The call is refused when EITHER the shared pool OR the
        per-purpose cap is spent — the pool is the pass's ceiling and
        the per-purpose cap is V2-D 3.9's split of that ceiling.
        """
        if self.used >= self.limit:
            return False
        used = int(self.purpose_used.get(purpose, 0))
        if used >= self.per_purpose_limit:
            return False
        self.used += 1
        self.purpose_used[purpose] = used + 1
        return True


@dataclass(frozen=True)
class ReflectionSettings:
    """One pass's resolved settings (re-read every tick by the worker).

    The dataclass mirrors the config section field-for-field, so the
    worker can re-resolve the knobs per tick without the service
    reaching back into the configuration.
    """

    enabled: bool = True
    agent_id: str = "ari"
    trigger_sum: int = MEMOIR_REFLECTION_TRIGGER_SUM
    debounce_base_s: float = MEMOIR_REFLECTION_DEBOUNCE_BASE_S
    debounce_ceiling_s: float = MEMOIR_REFLECTION_DEBOUNCE_CEILING_S
    force_fire_s: float = MEMOIR_REFLECTION_FORCE_FIRE_S
    max_syntheses_per_pass: int = MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS
    max_chars_per_synthesis: int = MEMOIR_REFLECTION_MAX_CHARS_PER_SYNTHESIS
    max_llm_calls_per_pass: int = MEMOIR_REFLECTION_MAX_LLM_CALLS_PER_PASS
    constellation_max_llm_calls_per_pass: int = (
        MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS
    )
    tick_budget_s: float = MEMOIR_REFLECTION_TICK_BUDGET_S
    call_timeout_s: float = MEMOIR_REFLECTION_CALL_TIMEOUT_S
    tick_interval_s: int = 30
    scan_window_days: int = MEMOIR_REFLECTION_SCAN_WINDOW_DAYS
    nightly_cap: int = MEMOIR_REFLECTION_NIGHTLY_CAP
    nightly_start_hour: int = MEMOIR_REFLECTION_NIGHTLY_START_HOUR
    nightly_end_hour: int = MEMOIR_REFLECTION_NIGHTLY_END_HOUR
    max_prompt_tokens: int = MEMOIR_REFLECTION_MAX_PROMPT_TOKENS


@dataclass(frozen=True)
class PassOutcome:
    """The result of one reflection pass — the metered truth.

    The counters are the pass's own bookkeeping; the ``[MemoirReflection]``
    log line is rendered from exactly this object, so the log can never
    disagree with the returned state.
    """

    ran: bool = False
    cadence: str = ""
    reason: str = ""
    considered: int = 0
    marked: int = 0
    syntheses_accepted: int = 0
    syntheses_rejected: int = 0
    syntheses_duplicate: int = 0
    llm_calls: int = 0
    constellation_calls: int = 0
    lines_written: int = 0
    deferred: int = 0
    duration_s: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: The episode ids the pass actually CONSIDERED (a valid envelope was
    #: produced for their chunk). ONLY these may be marked ``reflected_at``
    #: — a chunk whose call failed stays unreflected and is re-considered
    #: next pass.
    considered_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def stopped_reason(self) -> str:
        """The named breach behaviour (empty when the pass ran clean)."""
        return self.reason if self.ran else self.reason


class ReflectionEnvelopeError(ValueError):
    """The model's response was not a usable envelope.

    A BATCH-level failure: the episodes are NOT marked (they are
    re-considered next pass) and the pass itself does not fail.
    """


class MemoirReflectionService:
    """The reflection engine: cadences, budgets, synthesis generation.

    The service owns the DECISIONS (when to reflect, what to accept, how
    much to spend); the repositories own the SQL; the worker owns the
    loop. It is SYNC by design — it is called from the worker's
    dedicated thread, never from the event loop.
    """

    def __init__(
        self,
        *,
        reflection_repository: Any,
        syntheses_repository: Any,
        constellation_repository: Any = None,
        persona_repository: Any = None,
        persona_gate: Any = None,
        keepsake_proposal_service: Any = None,
        settings: ReflectionSettings | None = None,
        config: Any = None,
        llm_config: dict[str, Any] | None = None,
        llm_caller: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._reflection = reflection_repository
        self._syntheses = syntheses_repository
        self._constellation = constellation_repository
        self._persona_repo = persona_repository
        self._persona_gate = persona_gate
        #: The KEEPSAKE PROPOSAL service (phase 6, T6.1/T6.3). Injected by
        #: the lifespan wiring; ``None`` (the default) means the lane is
        #: not wired and the pass skips the proposal phase entirely — so
        #: every existing construction site and test double keeps working
        #: byte-identically.
        self._keepsake_proposals = keepsake_proposal_service
        self._settings = settings or ReflectionSettings()
        #: The resolved daemon configuration (the config tier of the
        #: knobs — the worker re-resolves the settings per tick).
        self._config = config
        self._llm_config = dict(llm_config or {})
        #: The LLM caller — INJECTABLE so the engine is testable without a
        #: provider. The default is the house failover path.
        self._llm_caller = llm_caller or self._house_llm_call
        self._clock = clock or now_utc
        self._monotonic = monotonic or time.monotonic
        # ── the in-memory cadence state (benign to lose on a crash) ────
        #: The CURRENT idle-debounce window. It starts at the base and
        #: DOUBLES on every burst up to the ceiling.
        self._debounce_s: float = float(self._settings.debounce_base_s)
        #: The newest unreflected episode observed at the last doubling
        #: decision — a NEWER one means "an episode arrived during the
        #: debounce", which doubles the window.
        self._debounce_anchor_ts: datetime | None = None
        #: When the trigger FIRST became ready — the force-fire anchor.
        self._trigger_anchor: datetime | None = None

        if self._persona_gate is None and self._persona_repo is not None:
            from daemon.services.memoir_persona_gate import MemoirPersonaGateService

            engine = getattr(
                self._persona_repo,
                "engine",
                getattr(self._reflection, "engine", None),
            )
            if engine is not None:
                self._persona_gate = MemoirPersonaGateService(
                    engine,
                    persona_repository=self._persona_repo,
                    llm_caller=self._llm_caller,
                    llm_config=self._llm_config,
                    clock=self._clock,
                    agent_id=self._settings.agent_id,
                    # THE GATE RIDES THE SAME TIMEOUT KNOB (F2) — the
                    # gate's local fallback lane must not carry a
                    # second, hard-coded copy of the call timeout.
                    call_timeout_s=float(self._settings.call_timeout_s),
                )

    # ── the settings ────────────────────────────────────────────────────

    @property
    def settings(self) -> ReflectionSettings:
        """The resolved settings (the worker re-resolves them per tick)."""
        return self._settings

    def apply_settings(self, settings: ReflectionSettings) -> None:
        """Adopt freshly resolved settings (the per-tick re-read)."""
        self._settings = settings
        if self._debounce_s <= 0 or self._debounce_s > float(settings.debounce_ceiling_s):
            self._debounce_s = float(settings.debounce_base_s)

    @property
    def debounce_s(self) -> float:
        """The CURRENT idle-debounce window (in seconds)."""
        return self._debounce_s

    # ── the derived trigger (V2-D 2.2) ──────────────────────────────────

    def window_start(self, now: datetime | None = None) -> datetime:
        """The inclusive lower bound of the regular scan window."""
        moment = now or self._clock()
        return moment - timedelta(days=int(self._settings.scan_window_days))

    def derive_trigger(
        self, now: datetime | None = None
    ) -> tuple[int, datetime | None]:
        """Derive the trigger sum + the newest unreflected episode.

        DERIVED PER TICK — never a rolling counter, never a delta-notify.
        """
        return self._reflection.derive_trigger(
            self._settings.agent_id,
            window_start=self.window_start(now),
        )

    # ── the nightly gate (§3.1) ─────────────────────────────────────────

    def nightly_gate_open(self, now: datetime | None = None) -> bool:
        """Is the NIGHTLY pass due?

        ``02:00 <= now < 04:00 UTC`` AND the last completed nightly pass
        is not today. The date watermark is DURABLE (a restart at 02:30
        still knows whether tonight already ran).
        """
        moment = _as_utc(now or self._clock())
        if moment.hour < self._settings.nightly_start_hour:
            return False
        if moment.hour >= self._settings.nightly_end_hour:
            return False
        last_date = self._reflection.last_nightly_date()
        return last_date != moment.date().isoformat()

    # ── the in-day trigger (§3.1) ───────────────────────────────────────

    def evaluate_in_day(
        self, now: datetime | None = None
    ) -> tuple[bool, str, int]:
        """Evaluate the in-day trigger.

        Returns:
            ``(should_run, reason, derived_sum)`` where ``reason`` is
            one of ``below_threshold`` / ``no_episodes`` / ``idle`` /
            ``force_fire`` / ``debouncing``.

        THE DOUBLING: a NEWER unreflected episode than the one observed
        at the last decision means "an episode arrived during the
        debounce" — the window DOUBLES (base → 2× → 4× … capped at the
        ceiling). The window resets to the base after a pass.
        """
        moment = _as_utc(now or self._clock())
        total, max_ts = self.derive_trigger(moment)
        if total < int(self._settings.trigger_sum):
            # The trigger is no longer ready — the cadence state resets.
            self._debounce_s = float(self._settings.debounce_base_s)
            self._debounce_anchor_ts = None
            self._trigger_anchor = None
            return False, "below_threshold", total
        if max_ts is None:
            return False, "no_episodes", total

        if self._debounce_anchor_ts is None or max_ts > self._debounce_anchor_ts:
            if self._debounce_anchor_ts is not None:
                # A BURST — the window doubles.
                self._debounce_s = min(
                    float(self._settings.debounce_ceiling_s),
                    self._debounce_s * 2.0,
                )
            self._debounce_anchor_ts = max_ts
        if self._trigger_anchor is None:
            self._trigger_anchor = moment

        idle_s = (moment - max_ts).total_seconds()
        elapsed_s = (moment - self._trigger_anchor).total_seconds()
        if idle_s >= self._debounce_s:
            return True, "idle", total
        if elapsed_s >= float(self._settings.force_fire_s):
            # The continuous-stream escape hatch: the dream never defers
            # indefinitely under a live conversation.
            return True, "force_fire", total
        return False, "debouncing", total

    # ── the nightly pass (T2.3) ─────────────────────────────────────────

    def run_nightly_pass(self, now: datetime | None = None) -> PassOutcome:
        """Run the NIGHTLY bounded pass.

        The pass:

        1. takes the CLAIM (with the 15-minute stale takeover);
        2. scans the OLDEST unreflected episodes WITHOUT the 2-day
           window restriction (C2 / V2-27), capped at the nightly cap;
        3. generates the syntheses under the §3.2 budgets;
        4. commits the watermarks and the nightly date in ONE
           transaction;
        5. releases the claim.
        """
        moment = _as_utc(now or self._clock())
        started = self._monotonic()
        if not self._reflection.claim(now=moment):
            logger.info(
                "[MemoirReflection] nightly pass skipped: the claim is held "
                "by another pass (a fresh claim is never stolen)"
            )
            return PassOutcome(ran=False, cadence="nightly", reason="claim_held")
        try:
            episodes = self._reflection.scan_unreflected(
                self._settings.agent_id,
                limit=int(self._settings.nightly_cap),
                window_start=None,
            )
            outcome = self._run_pass(
                episodes, now=moment, cadence="nightly", started=started
            )
            # THE ATOMIC COMMIT: the watermarks and the nightly date land
            # together — the watermark never advances ahead of the beliefs.
            marked = self._reflection.commit_pass(
                [episode.id for episode in outcome_considered_episodes(episodes, outcome)],
                reflected_at=moment,
                last_nightly_date=moment.date().isoformat(),
            )
            outcome = _with_marked(outcome, marked)
            self._reset_cadence_state()
            logger.info(
                "[MemoirReflection] cadence=nightly considered=%d marked=%d "
                "syntheses=%d rejected=%d duplicates=%d llm_calls=%d "
                "constellation_calls=%d lines=%d deferred=%d duration=%.1fs",
                outcome.considered,
                outcome.marked,
                outcome.syntheses_accepted,
                outcome.syntheses_rejected,
                outcome.syntheses_duplicate,
                outcome.llm_calls,
                outcome.constellation_calls,
                outcome.lines_written,
                outcome.deferred,
                outcome.duration_s,
            )
            return outcome
        finally:
            self._reflection.release_claim()

    # ── the in-day pass (T2.1) ──────────────────────────────────────────

    def run_in_day_pass(self, now: datetime | None = None) -> PassOutcome:
        """Run the IN-DAY idle-debounce pass."""
        moment = _as_utc(now or self._clock())
        started = self._monotonic()
        if not self._reflection.claim(now=moment):
            logger.info(
                "[MemoirReflection] in-day pass skipped: the claim is held "
                "by another pass"
            )
            return PassOutcome(ran=False, cadence="in_day", reason="claim_held")
        try:
            episodes = self._reflection.scan_unreflected(
                self._settings.agent_id,
                limit=int(self._settings.nightly_cap),
                window_start=self.window_start(moment),
            )
            outcome = self._run_pass(
                episodes, now=moment, cadence="in_day", started=started
            )
            marked = self._reflection.commit_pass(
                [episode.id for episode in outcome_considered_episodes(episodes, outcome)],
                reflected_at=moment,
            )
            outcome = _with_marked(outcome, marked)
            self._reset_cadence_state()
            logger.info(
                "[MemoirReflection] cadence=in_day considered=%d marked=%d "
                "syntheses=%d rejected=%d duplicates=%d llm_calls=%d "
                "constellation_calls=%d lines=%d deferred=%d duration=%.1fs",
                outcome.considered,
                outcome.marked,
                outcome.syntheses_accepted,
                outcome.syntheses_rejected,
                outcome.syntheses_duplicate,
                outcome.llm_calls,
                outcome.constellation_calls,
                outcome.lines_written,
                outcome.deferred,
                outcome.duration_s,
            )
            return outcome
        finally:
            self._reflection.release_claim()

    def _reset_cadence_state(self) -> None:
        """After a pass: the debounce window returns to its base."""
        self._debounce_s = float(self._settings.debounce_base_s)
        self._debounce_anchor_ts = None
        self._trigger_anchor = None

    # ── the pass body (the budgets) ─────────────────────────────────────

    def _run_pass(
        self,
        episodes: Sequence[Any],
        *,
        now: datetime,
        cadence: str,
        started: float,
    ) -> PassOutcome:
        """The pass body: chunk, dispatch, validate, write, meter.

        The budget enforcement points, in the order they are hit:

        * the TICK BUDGET — checked BEFORE every dispatch (the
          per-item pre-dispatch check). Over budget ⇒ the remainder is
          DEFERRED (never marked, re-considered next pass) and the pass
          logs ``pass_deferred``;
        * the LLM-CALL CAP — counted AT DISPATCH (a call that was
          dispatched counts, even if it failed). Over cap ⇒ the pass
          stops and logs ``call_cap_hit``;
        * the SYNTHESIS CAP — checked in the accept loop. Over cap ⇒ the
          loop stops accepting and logs ``synthesis_cap_hit``;
        * the CLAIM-LENGTH CAP — checked per candidate. Over cap ⇒ the
          candidate is REJECTED (never truncated) and logs
          ``claim_too_long``.
        """
        settings = self._settings
        considered = 0
        marked_episode_ids: list[str] = []
        #: The episodes the pass CONSIDERED but must NOT mark: a WRITE
        #: FAILURE or a CAP-DEFERAL leaves the belief un-written, so the
        #: episode stays unreflected and the NEXT pass retries it. The
        #: dedup key (V2-17) makes that retry idempotent — the beliefs
        #: that DID land come back as "duplicate" and are skipped, so
        #: only the missing belief is re-derived — but ONLY while the
        #: re-derivation yields the SAME claim over the SAME source ids
        #: AND the landed row is still ACTIVE inside the 2-day dedup
        #: window (the LLM re-derivation is NOT deterministic, so a
        #: DRIFTED belief re-derives as a NEW row, not a duplicate).
        unmarked_episode_ids: set[str] = set()
        accepted = 0
        accepted_synthesis_rows: list[Any] = []
        rejected = 0
        duplicates = 0
        llm_calls = 0
        # THE PER-PASS CONSTELLATION BUDGET (V2-D 3.9): ONE object for
        # the whole pass.  Every chunk threads the SAME budget, so the
        # cap is a PASS cap — a multi-chunk pass can never exceed it.
        constellation_budget = ConstellationCallBudget(
            int(settings.constellation_max_llm_calls_per_pass)
        )
        lines = 0
        deferred = 0
        notes: list[str] = []
        stop_reason = ""

        # THE DURABLE PASS IDENTITY (F-3): minted ONCE, HERE, at the
        # PRODUCTION site — and REPRODUCIBLE across a crash-retry of the
        # SAME pass (the anchor episode is the pass's oldest considered
        # episode, so a retry that reconsiders it mints the SAME stamp
        # and the ``last_pass_id`` guard treats the re-run as the same
        # pass instead of inflating ``mention_count``).  A per-chunk
        # uuid (the old mint) could NEVER bind the guard in production.
        pass_id = durable_pass_id(self._settings.agent_id, cadence, episodes)

        chunks = self._compose_chunks(episodes)
        for chunk in chunks:
            # ── the TICK BUDGET — the per-item pre-dispatch check ──────
            elapsed = self._monotonic() - started
            if elapsed >= float(settings.tick_budget_s):
                deferred += len(chunk)
                stop_reason = "pass_deferred"
                notes.append(
                    f"pass_deferred remaining={deferred} elapsed={elapsed:.1f}s"
                )
                logger.info(
                    "[MemoirReflection] %s: the %0.1fs tick budget is spent — "
                    "%d episode(s) deferred to the next pass",
                    stop_reason,
                    settings.tick_budget_s,
                    deferred,
                )
                break

            # ── the LLM-CALL CAP — counted AT DISPATCH ─────────────────
            if llm_calls >= int(settings.max_llm_calls_per_pass):
                deferred += len(chunk)
                stop_reason = "call_cap_hit"
                notes.append(
                    f"call_cap_hit calls={llm_calls} deferred={deferred}"
                )
                logger.info(
                    "[MemoirReflection] %s: the %d-call budget is spent — "
                    "%d episode(s) deferred to the next pass",
                    stop_reason,
                    settings.max_llm_calls_per_pass,
                    deferred,
                )
                break

            llm_calls += 1
            considered += len(chunk)
            candidates = self._synthesize(chunk, now=now)
            if candidates is None:
                # A BATCH-level failure (garbage JSON, a refusal is NOT a
                # failure): the chunk is NOT marked — it is re-considered
                # next pass, and the dedup key makes the re-run safe.
                notes.append("batch_parse_failed")
                # THE CONSTELLATION EXTRACTION STILL RUNS (R2): the
                # ``stars[]`` seed is the PRIMARY mechanism, so a
                # permanently-failing synthesis batch must NOT starve
                # the constellation.  The chunk is NOT considered, so its
                # episodes are NOT marked here — the next pass retries
                # them (and the durable pass id makes that retry a
                # no-op for the mention counts).
                lines += self._extract_constellation(
                    chunk,
                    now=now,
                    budget=constellation_budget,
                    pass_id=pass_id,
                    mark_ids=(),
                ) or 0
                continue
            marked_episode_ids.extend(episode.id for episode in chunk)
            chunk_ids = {episode.id for episode in chunk}

            for candidate_index, candidate in enumerate(candidates):
                if accepted >= int(settings.max_syntheses_per_pass):
                    stop_reason = "synthesis_cap_hit"
                    # THE CAP-DEFERAL: the dropped candidates are NOT
                    # lost — their source episodes are UNMARKED so the
                    # next pass re-derives them. THE DEDUP KEY (V2-17) is
                    # what makes the retry idempotent (the beliefs that
                    # DID land come back as "duplicate") — but ONLY while
                    # the re-derivation yields the SAME claim over the
                    # SAME source ids AND the landed row is still ACTIVE
                    # inside the 2-day dedup window (the LLM
                    # re-derivation is NOT deterministic, so a DRIFTED
                    # belief re-derives as a NEW row, not a duplicate).
                    for dropped in candidates[candidate_index:]:
                        unmarked_episode_ids.update(
                            _candidate_source_ids(dropped, chunk_ids)
                        )
                    notes.append(
                        f"synthesis_cap_hit kept={accepted} dropped={len(candidates)}"
                    )
                    logger.info(
                        "[MemoirReflection] %s kept=%d dropped=%d",
                        stop_reason,
                        accepted,
                        len(candidates),
                    )
                    break
                verdict = self._write_synthesis(
                    candidate, collector=accepted_synthesis_rows
                )
                if verdict == "accepted":
                    accepted += 1
                elif verdict == "duplicate":
                    duplicates += 1
                elif verdict == "write_failed":
                    # THE WRITE FAILURE: the row did NOT land, so the
                    # source episode is UNMARKED and the next pass
                    # retries it (the dedup key, V2-17, makes the retry
                    # idempotent) — but ONLY while the re-derivation
                    # yields the SAME claim over the SAME source ids AND
                    # the landed row is still ACTIVE inside the 2-day
                    # dedup window (the LLM re-derivation is NOT
                    # deterministic, so a DRIFTED belief re-derives as a
                    # NEW row, not a duplicate).
                    unmarked_episode_ids.update(
                        _candidate_source_ids(candidate, chunk_ids)
                    )
                    rejected += 1
                else:
                    # THE INVARIANT REJECTION: the candidate is
                    # PERMANENTLY invalid (too long, uncited, an unknown
                    # episode, a verb outside the vocabulary). It will
                    # NEVER become valid, so the episode STAYS MARKED — a
                    # retry would re-derive the same invalid belief
                    # forever.
                    rejected += 1

            # ── the constellation extraction — its OWN budget (V2-31) ──
            # THE HEURISTIC SEED IS FREE: the extraction ALWAYS runs for
            # every chunk (the ``stars[]`` seed is the primary
            # mechanism), and the LLM half stops on its OWN per-pass
            # budget — the counter IS the budget's counter.
            #
            # THE PASS UNIT (F-3): the constellation edges AND the
            # per-episode watermark of the chunk's CONSIDERED episodes
            # land in ONE transaction — the crash window of V2-29 / A2
            # (edges written, marks not) is closed.
            if self._constellation is not None:
                written = self._extract_constellation(
                    chunk,
                    now=now,
                    budget=constellation_budget,
                    pass_id=pass_id,
                    mark_ids=[
                        episode.id
                        for episode in chunk
                        if episode.id not in unmarked_episode_ids
                    ],
                )
                if written is None:
                    # THE UNIT FAILED: the edges rolled back with the
                    # marks, so the chunk's episodes must NOT be marked —
                    # the next pass has to retry them (marking them would
                    # lose the seed for good).
                    unmarked_episode_ids.update(
                        episode.id for episode in chunk
                    )
                else:
                    lines += written

        # ── THE TIER-2 PHASE (F-2) — AFTER every chunk's extraction ────
        # THE EXTRACTION-FIRST ORDERING: the Tier-2 summarization runs
        # once, at the END of the pass, so a Tier-2-qualified star can
        # never take the extraction's budget unit (the council's probe
        # produced "2 tier-2 calls, 0 extraction" under the old
        # per-chunk order).  The per-purpose cap allows ONE Tier-2 call
        # per pass.
        if self._constellation is not None:
            try:
                self._constellation_service(now).summarize_qualifying(
                    llm_caller=self._llm_caller,
                    agent_id=self._settings.agent_id,
                    now=now,
                    budget=constellation_budget,
                )
            except ConstellationBudgetRequiredError:
                raise
            except Exception:  # noqa: BLE001 — never fail the pass
                logger.warning(
                    "[MemoirReflection] the Tier-2 summarization failed — "
                    "the pass continues"
                )

        # ── THE PERSONA GATE (JEV) PHASE (V2-D 5.7 / Phase 5) ──────────
        # Execution ordering: commit synthesis -> constellation -> JEV.
        # JEV failure never rolls back or blocks the committed synthesis.
        if self._persona_gate is not None:
            try:
                from daemon.services.memoir_persona_gate import (
                    MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
                )

                self._persona_gate.process_pass(
                    new_syntheses=accepted_synthesis_rows,
                    max_composition_calls=MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
                )
            except Exception:  # noqa: BLE001 — never fail the pass
                logger.warning(
                    "[MemoirReflection] the Persona Gate (JEV) evaluation failed — "
                    "the pass continues"
                )

        # ── THE KEEPSAKE PROPOSAL PHASE (phase 6, T6.1/T6.3 / V2-D 6.1) ─
        # Execution ordering (the §2-Q4 doctrine): commit synthesis →
        # constellation → JEV → KEEPSAKE PROPOSAL. Every step is a
        # CONSUMER of the durable synthesis; a proposal failure must never
        # roll back or block it, so the call is isolated here and the
        # service itself never raises.
        #
        # NO LLM CALLS in this phase: qualification and staging are
        # heuristic over rows already in the database, and the reason text
        # is composed from row facts (plan §1/§4).
        if self._keepsake_proposals is not None:
            try:
                self._keepsake_proposals.process_pass(
                    now=now,
                    agent_id=self._settings.agent_id,
                )
            except Exception:  # noqa: BLE001 — never fail the pass
                logger.warning(
                    "[MemoirReflection] the keepsake proposal phase failed — "
                    "the pass continues"
                )

        duration = self._monotonic() - started
        outcome = PassOutcome(
            ran=True,
            cadence=cadence,
            reason=stop_reason,
            considered=considered,
            syntheses_accepted=accepted,
            syntheses_rejected=rejected,
            syntheses_duplicate=duplicates,
            llm_calls=llm_calls,
            constellation_calls=constellation_budget.used,
            lines_written=lines,
            deferred=deferred,
            duration_s=duration,
            notes=tuple(notes),
            considered_ids=tuple(
                episode_id
                for episode_id in marked_episode_ids
                if episode_id not in unmarked_episode_ids
            ),
        )
        return outcome

    # ── the prompt composition (the CLAMPED input) ─────────────────────

    def _compose_chunks(self, episodes: Sequence[Any]) -> list[list[Any]]:
        """Split the episodes into dispatch units that fit the prompt.

        The unit is a CHUNK of episodes that share ONE LLM call — the
        call cap (4/pass) and the nightly cap (30/pass) are only
        consistent if a call can carry several episodes.
        """
        chunks: list[list[Any]] = []
        current: list[Any] = []
        current_tokens = 0
        budget = int(self._settings.max_prompt_tokens)
        for episode in episodes:
            body = str(getattr(episode, "body", "") or "")
            tokens = _estimate_tokens(body)
            if current and current_tokens + tokens > budget:
                chunks.append(current)
                current = []
                current_tokens = 0
            current.append(episode)
            current_tokens += tokens
        if current:
            chunks.append(current)
        return chunks

    def _synthesis_prompt(self, chunk: Sequence[Any]) -> str:
        """The synthesis prompt (the composed + clamped INPUT)."""
        lines = [
            "You are the reflection engine of a person who writes her own "
            "memories.",
            "Read the episodes below and consolidate them into a small "
            "number of BELIEFS she now holds.",
            "",
            "Rules:",
            "- Every belief MUST cite the episode ids it came from "
            "(\"source_episode_ids\"). A belief with no source is refused.",
            "- Use one verb per belief: CREATE (a new belief), CORROBORATE "
            "(an existing belief, now supported again), REFINE (a belief "
            "sharper than before), CORRECT (a belief that was wrong).",
            "- confidence is a number between 0.0 and 1.0.",
            "- If nothing in the episodes deserves to be believed, return an "
            "empty syntheses list.",
            "- If you cannot answer, return an empty syntheses list.",
            "",
            "Episodes:",
        ]
        for episode in chunk:
            lines.append(
                f"- id={episode.id} importance={getattr(episode, 'importance', 0)} "
                f"body={getattr(episode, 'body', '') or ''}"
            )
        lines.extend(
            [
                "",
                "Answer with ONE JSON object and nothing else:",
                '{"episodes": ["<episode id>", ...], "syntheses": '
                '[{"claim": "...", "source_episode_ids": ["..."], '
                '"verb": "CREATE|CORROBORATE|REFINE|CORRECT", '
                '"confidence": 0.0, "supersedes": null}]}',
            ]
        )
        prompt = "\n".join(lines)
        return _clamp_prompt(prompt, int(self._settings.max_prompt_tokens))

    def _extraction_prompt(self, chunk: Sequence[Any]) -> str:
        """The constellation-extraction prompt (the star mentions)."""
        lines = [
            "You extract ENTITY MENTIONS from a person's own memories.",
            "",
            "Rules:",
            "- For each episode, list the named entities it mentions.",
            "- star_type is one of: person, project, place, concept, domain.",
            "- Use the canonical surface form of the entity.",
            "- An episode with no named entity gets an empty list.",
            "",
            "Episodes:",
        ]
        for episode in chunk:
            lines.append(
                f"- id={episode.id} body={getattr(episode, 'body', '') or ''}"
            )
        lines.extend(
            [
                "",
                "Answer with ONE JSON object and nothing else:",
                '{"episodes": ["<episode id>", ...], "mentions": '
                '[{"episode_id": "...", "name": "...", "star_type": "concept"}]}',
            ]
        )
        prompt = "\n".join(lines)
        return _clamp_prompt(prompt, int(self._settings.max_prompt_tokens))

    # ── the synthesis generation (T2.2) ─────────────────────────────────

    def _synthesize(self, chunk: Sequence[Any], *, now: datetime) -> list[dict] | None:
        """One synthesis call for a chunk.

        Returns:
            The parsed candidates, or ``None`` for a BATCH-level failure
            (the chunk is then NOT marked — it is re-considered next
            pass). A model REFUSAL is a valid-empty, not a failure.
        """
        prompt = self._synthesis_prompt(chunk)
        try:
            raw = self._llm_caller(prompt)
        except Exception as exc:  # noqa: BLE001 — one failed call is not a failed pass
            logger.warning(
                "[MemoirReflection] the synthesis call failed (%s) — the "
                "batch is not marked and is re-considered next pass",
                type(exc).__name__,
            )
            return None
        return self._parse_envelope(raw, kind="syntheses")

    def _parse_envelope(
        self, raw: str, *, kind: str
    ) -> list[dict] | None:
        """Parse the model's envelope, with the §3-A4 failure rules.

        * garbage JSON ⇒ ``None`` (a batch-level failure: the episodes
          are NOT marked);
        * a TRUNCATED envelope ⇒ ``None`` (the completeness check —
          both ``episodes`` and ``syntheses`` must be present);
        * a MODEL REFUSAL ⇒ a valid-empty (``[]``) — the batch counts as
          CONSIDERED, and the pass never retries a deterministic refusal.
        """
        text = (raw or "").strip()
        if not text:
            return None
        payload = _extract_json_object(text)
        if payload is None:
            if _looks_like_refusal(text):
                logger.info(
                    "[MemoirReflection] the model declined to answer — the "
                    "batch counts as considered with zero syntheses"
                )
                return []
            logger.warning(
                "[MemoirReflection] the response carried no JSON object — "
                "the batch is not marked and is re-considered next pass"
            )
            return None
        try:
            envelope = json.loads(payload)
        except (ValueError, TypeError):
            logger.warning(
                "[MemoirReflection] the response was not valid JSON — the "
                "batch is not marked and is re-considered next pass"
            )
            return None
        if not isinstance(envelope, dict):
            return None
        # THE COMPLETENESS CHECK (§3-A4): a truncated envelope can still
        # parse, so the episode list AND the payload list must both be
        # present and be lists. The payload key is kind-dependent (the
        # synthesis envelope carries ``syntheses``, the extraction
        # envelope carries ``mentions``).
        payload_key = "syntheses" if kind == "syntheses" else "mentions"
        if "episodes" not in envelope or payload_key not in envelope:
            logger.warning(
                "[MemoirReflection] the envelope is incomplete (missing the "
                f"episodes/{payload_key} lists) — the batch is not marked and "
                "is re-considered next pass"
            )
            return None
        if not isinstance(envelope.get("episodes"), list) or not isinstance(
            envelope.get(payload_key), list
        ):
            return None
        candidates = envelope.get(payload_key) or []
        return [item for item in candidates if isinstance(item, dict)]

    # ── the synthesis writer (the accept loop) ──────────────────────────

    def _write_synthesis(
        self, candidate: dict, collector: list[Any] | None = None
    ) -> str:
        """Validate and write ONE candidate synthesis.

        Returns:
            ``"accepted"`` / ``"duplicate"`` / ``"rejected"``.

        THE ORDER OF THE GUARDS (each one is a refusal, not a repair):

        1. the claim must be a non-empty string;
        2. the citations must be NON-EMPTY (V2-6) and must name REAL
           episodes of this agent (V2-D 2.3);
        3. the verb must be inside the closed vocabulary;
        4. the claim must fit the 1000-character bound — a longer claim
           is REJECTED, NEVER truncated (a truncated claim is a false
           proposition);
        5. the confidence is CLAMPED into ``[0.1, 1.0]`` (§3-A4);
        6. the dedup key decides whether the row is new.
        """
        claim = candidate.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            logger.info(
                "[MemoirReflection] claim_rejected: the candidate carries no "
                "claim — skipped"
            )
            return "rejected"
        sources = candidate.get("source_episode_ids")
        if not isinstance(sources, list) or not sources:
            logger.info(
                "[MemoirReflection] claim_rejected: the claim %r cites no "
                "source episode — skipped (an uncited belief is a rumour)",
                claim[:60],
            )
            return "rejected"
        source_ids = [str(item) for item in sources if item]
        known = self._reflection.known_episode_ids(
            self._settings.agent_id, source_ids
        )
        if len(known) != len(set(source_ids)):
            logger.info(
                "[MemoirReflection] claim_rejected: the claim %r cites an "
                "episode that does not exist — skipped",
                claim[:60],
            )
            return "rejected"
        verb = str(candidate.get("verb") or "").upper()
        if verb not in SYNTHESIS_VERBS:
            logger.info(
                "[MemoirReflection] claim_rejected: the verb %r is outside "
                "the vocabulary — skipped",
                verb,
            )
            return "rejected"
        # THE LENGTH BOUND — REJECT, never truncate.
        if len(claim) > int(self._settings.max_chars_per_synthesis):
            logger.info(
                "[MemoirReflection] claim_too_long: %d chars > the %d-char "
                "bound — the claim is REJECTED (never truncated: a "
                "truncated claim is a false proposition)",
                len(claim),
                self._settings.max_chars_per_synthesis,
            )
            return "rejected"
        confidence = _clamp_confidence(candidate.get("confidence"))
        supersedes = candidate.get("supersedes")
        supersedes = str(supersedes) if supersedes else None

        # THE DEDUP KEY (V2-17): the same belief over the same sources is
        # the same belief — the retry is a no-op.
        existing = self._syntheses.find_duplicate(
            self._settings.agent_id, claim, source_ids
        )
        if existing is not None:
            logger.info(
                "[MemoirReflection] claim_duplicate: the belief already "
                "exists (id=%s) — skipped",
                existing.id,
            )
            return "duplicate"

        try:
            if supersedes:
                row = self._syntheses.supersede_with(
                    self._settings.agent_id,
                    supersedes,
                    {
                        "claim": claim,
                        "source_episode_ids": source_ids,
                        "verb": verb,
                        "confidence": confidence,
                    },
                )
            else:
                row = self._syntheses.create_synthesis(
                    self._settings.agent_id,
                    claim,
                    source_ids,
                    verb,
                    confidence,
                )
            if collector is not None and row is not None:
                collector.append(row)
        except ValueError as exc:
            # AN INVARIANT REJECTION RAISED BY THE STORE (the citations
            # are missing, the verb is outside the closed vocabulary):
            # the candidate is PERMANENTLY invalid — it will NEVER become
            # valid, so the episode STAYS MARKED (a retry would re-derive
            # the same invalid belief forever).
            logger.warning(
                "[MemoirReflection] claim_rejected_by_store (%s) — the belief "
                "is skipped, the pass continues",
                type(exc).__name__,
            )
            return "rejected"
        except (KeyError, RuntimeError) as exc:
            # A WRITE FAILURE: the row did NOT land (the superseded
            # synthesis is gone, or another writer holds the claim). The
            # source episode is UNMARKED so the NEXT pass retries it —
            # THE DEDUP KEY (V2-17) is what makes that retry idempotent:
            # the beliefs that DID land come back as "duplicate" and are
            # skipped, so only the missing belief is re-derived — but ONLY
            # while the re-derivation yields the SAME claim over the SAME
            # source ids AND the landed row is still ACTIVE inside the
            # 2-day dedup window (the LLM re-derivation is NOT
            # deterministic, so a DRIFTED belief re-derives as a NEW row,
            # not a duplicate).
            logger.warning(
                "[MemoirReflection] claim_write_failed (%s) — the belief is "
                "skipped and the source episode is UNMARKED (the next pass "
                "retries it; the dedup key, V2-17, makes the retry "
                "idempotent ONLY when the re-derivation yields the same "
                "claim over the same source ids and the landed row is "
                "still active in the 2-day dedup window; a drifted "
                "re-derivation lands as a NEW row)",
                type(exc).__name__,
            )
            return "write_failed"
        return "accepted"

    # ── the constellation extraction (V2-31, the decoupled budget) ─────

    def _constellation_service(self, now: datetime) -> Any:
        """The constellation SERVICE for this pass (the local import keeps
        the two service modules free of an import cycle)."""
        from daemon.services.memoir_constellation_service import (
            MemoirConstellationService,
        )

        return MemoirConstellationService(
            self._constellation,
            llm_caller=self._llm_caller,
            clock=lambda: now,
            agent_id=self._settings.agent_id,
        )

    def _extract_constellation(
        self,
        chunk: Sequence[Any],
        *,
        now: datetime,
        budget: ConstellationCallBudget | None = None,
        pass_id: str | None = None,
        mark_ids: Sequence[str] = (),
    ) -> int | None:
        """Extract star mentions and write constellation LINES.

        Delegates to :class:`MemoirConstellationService` so the heuristic
        ``stars[]`` seed is the PRIMARY mechanism and the LLM extraction
        is the FALLBACK (V2-D 3.1 / 3.9).  The pass id is always explicit
        (the durable pass identity, F-3).

        The ``budget`` is the PASS's LLM-call budget — it is threaded
        through EVERY chunk so the cap holds across the whole pass (the
        heuristic seed never consumes it).

        THE PASS UNIT (F-3): the edges AND the per-episode watermark of
        the chunk's CONSIDERED episodes are written in ONE transaction —
        the constellation repository's ``record_line`` and the reflection
        repository's ``mark_reflected`` ride the SAME session.  The
        atomicity scope is the PROCESSED EPISODES' unit (the chunk), NOT
        the whole pass: a crash rolls back the chunk's edges together
        with its marks, so a retry can never leave a written edge behind
        an unwritten watermark (the window V2-29 / A2 exist to close).

        Returns:
            The number of edges written, or ``None`` when the unit
            FAILED — the caller must then leave the chunk's episodes
            UNMARKED (their edges rolled back, so the next pass has to
            retry them; marking them would lose the seed).
        """
        if self._constellation is None:
            return 0
        try:
            svc = self._constellation_service(now)
            # RESOLVE first (the star upserts are their own
            # transactions), then WRITE inside the unit's transaction.
            edges = svc.collect_edges(
                chunk,
                agent_id=self._settings.agent_id,
                now=now,
                budget=budget,
            )
            stamp = pass_id or durable_pass_id(
                self._settings.agent_id, "unknown", ()
            )
            with self._reflection.unit_of_work() as session:
                written = svc.write_edges(edges, pass_id=stamp, session=session)
                if mark_ids:
                    self._reflection.mark_reflected(
                        mark_ids, reflected_at=now, session=session
                    )
            return int(written)
        except ConstellationBudgetRequiredError:
            # V2-31: an unbudgeted LLM call is a cost-control bypass —
            # it REFUSES loudly (the pass fails; the bypass is visible).
            logger.error(
                "[MemoirReflection] the constellation extraction reached an "
                "LLM path WITHOUT a budget — the call is REFUSED (V2-31)"
            )
            raise
        except Exception as exc:  # noqa: BLE001 — never fail the pass
            logger.warning(
                "[MemoirReflection] the constellation extraction failed "
                "(%s) — the pass continues and the chunk stays UNMARKED",
                type(exc).__name__,
            )
            return None

    # ── the house LLM lane ──────────────────────────────────────────────

    def _house_llm_call(self, prompt: str) -> str:
        """The house LLM call: failover + a bounded client.

        :func:`daemon.services.llm_failover.invoke_raw_with_failover` is
        the ONLY entry point (the house pattern); the factory is
        re-entrant so every retry attempt reads the CURRENT failover
        URL.
        """
        from daemon.services.llm_failover import (
            current_failover_url,
            invoke_raw_with_failover,
        )

        config = self._llm_config
        model = str(config.get("model") or "gpt-4")
        api_key = str(config.get("api_key") or "")
        base_url = config.get("base_url")
        timeout_s = float(self._settings.call_timeout_s)

        def _factory() -> str:
            import openai

            url = current_failover_url() or base_url
            client = openai.OpenAI(
                api_key=api_key,
                base_url=url or None,
                timeout=httpx_timeout(timeout_s),
                # THE SDK'S OWN RETRY IS OFF — the house lane (see
                # ``daemon/plugin_subsystem/opendesign/generate.py``):
                # the SDK's default ``max_retries=2`` issues up to THREE
                # HTTP requests per facade attempt, so the facade's
                # 6-attempt ladder would silently become up to 18 HTTP
                # requests per LLM call and inflate the failure window.
                # THE FACADE OWNS THE RETRY DISCIPLINE; the SDK must not
                # add a second, invisible ladder on top of it.
                max_retries=0,
            )
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise ValueError("reflection_response_empty: no choices")
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if not content:
                raise ValueError("reflection_response_empty: no content")
            return str(content)

        result = invoke_raw_with_failover(_factory, config)
        return str(result)


# ── the settings resolver (the ONE per-tick reader) ───────────────────────


def resolve_reflection_settings(config: Any = None) -> ReflectionSettings:
    """Resolve the reflection settings NOW — the ONE per-tick reader.

    The worker re-reads the knobs EVERY tick (the D3.12 house shape), so
    an operator's mid-flight change takes effect on the next tick without
    a restart.

    THE TRUTH TABLE IS NOT COPIED HERE. The raw env values are run
    through the SHARED classifier
    (:func:`daemon.config._classify_numeric`), the same one the config
    section's ``mode="before"`` validators use — so the worker tier and
    the boot tier can never drift into two different readings of the
    same variable (§3-A4: "three copies of a truth-table reader is
    exactly the drift class DR-3 exists to prevent").

    A garbage value CLAMPS to the constitutional default with a warning
    and NEVER raises.
    """
    from daemon.config import (
        NUMERIC_CLASS_BUDGET,
        NUMERIC_CLASS_CADENCE,
        _classify_numeric,
    )

    section = getattr(config, "memoir_reflection", None) if config is not None else None
    if section is None:
        from daemon.config import MemoirReflectionConfig

        section = MemoirReflectionConfig()

    def _numeric(
        field_name: str,
        env_name: str,
        numeric_class: str,
        minimum: float | None,
    ) -> Any:
        default = getattr(section, field_name)
        raw = os.environ.get(env_name)
        if raw is None or not raw.strip():
            return default
        return _classify_numeric(
            raw,
            default=default,
            numeric_class=numeric_class,
            name=env_name,
            minimum=minimum,
        )

    from daemon.tools.memoir_knob import resolve_memoir_reflection_knob

    enabled = resolve_memoir_reflection_knob(config).enabled
    return ReflectionSettings(
        enabled=enabled,
        agent_id=getattr(section, "agent_id", "ari") or "ari",
        trigger_sum=int(
            _numeric(
                "trigger_sum",
                "ENSEMBLE_MEMOIR_REFLECTION_TRIGGER_SUM",
                NUMERIC_CLASS_BUDGET,
                1,
            )
        ),
        debounce_base_s=float(
            _numeric(
                "debounce_base_s",
                "ENSEMBLE_MEMOIR_REFLECTION_DEBOUNCE_BASE_S",
                NUMERIC_CLASS_CADENCE,
                0.001,
            )
        ),
        debounce_ceiling_s=float(
            _numeric(
                "debounce_ceiling_s",
                "ENSEMBLE_MEMOIR_REFLECTION_DEBOUNCE_CEILING_S",
                NUMERIC_CLASS_CADENCE,
                0.001,
            )
        ),
        force_fire_s=float(
            _numeric(
                "force_fire_s",
                "ENSEMBLE_MEMOIR_REFLECTION_FORCE_FIRE_S",
                NUMERIC_CLASS_CADENCE,
                0.001,
            )
        ),
        max_syntheses_per_pass=int(
            _numeric(
                "max_syntheses_per_pass",
                "ENSEMBLE_MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS",
                NUMERIC_CLASS_BUDGET,
                1,
            )
        ),
        max_chars_per_synthesis=int(
            _numeric(
                "max_chars_per_synthesis",
                "ENSEMBLE_MEMOIR_REFLECTION_MAX_CHARS_PER_SYNTHESIS",
                NUMERIC_CLASS_BUDGET,
                1,
            )
        ),
        max_llm_calls_per_pass=int(
            _numeric(
                "max_llm_calls_per_pass",
                "ENSEMBLE_MEMOIR_REFLECTION_MAX_LLM_CALLS_PER_PASS",
                NUMERIC_CLASS_BUDGET,
                1,
            )
        ),
        constellation_max_llm_calls_per_pass=int(
            _numeric(
                "constellation_max_llm_calls_per_pass",
                "ENSEMBLE_MEMOIR_REFLECTION_CONSTELLATION_MAX_LLM_CALLS_PER_PASS",
                NUMERIC_CLASS_BUDGET,
                1,
            )
        ),
        tick_budget_s=float(
            _numeric(
                "tick_budget_s",
                "ENSEMBLE_MEMOIR_REFLECTION_TICK_BUDGET_S",
                NUMERIC_CLASS_CADENCE,
                0.001,
            )
        ),
        call_timeout_s=float(
            _numeric(
                "call_timeout_s",
                "ENSEMBLE_MEMOIR_REFLECTION_CALL_TIMEOUT_S",
                NUMERIC_CLASS_CADENCE,
                0.001,
            )
        ),
        tick_interval_s=int(
            _numeric(
                "tick_interval_s",
                "ENSEMBLE_MEMOIR_REFLECTION_TICK_INTERVAL_S",
                NUMERIC_CLASS_CADENCE,
                1,
            )
        ),
        scan_window_days=int(
            _numeric(
                "scan_window_days",
                "ENSEMBLE_MEMOIR_REFLECTION_SCAN_WINDOW_DAYS",
                NUMERIC_CLASS_CADENCE,
                1,
            )
        ),
        nightly_cap=int(
            _numeric(
                "nightly_cap",
                "ENSEMBLE_MEMOIR_REFLECTION_NIGHTLY_CAP",
                NUMERIC_CLASS_BUDGET,
                1,
            )
        ),
    )


# ── the helpers ───────────────────────────────────────────────────────────


def httpx_timeout(timeout_s: float) -> Any:
    """The house per-call timeout: ``httpx.Timeout(45.0, connect=5.0)``."""
    import httpx

    return httpx.Timeout(
        float(timeout_s), connect=MEMOIR_REFLECTION_CALL_CONNECT_TIMEOUT_S
    )


def _estimate_tokens(text: str) -> int:
    """The house token estimator (``daemon/loader.py``)."""
    from daemon.loader import estimate_tokens

    return estimate_tokens(text)


def _clamp_prompt(prompt: str, max_tokens: int) -> str:
    """CLAMP the composed prompt to the token budget.

    The clamp is on the INPUT: a prompt that would not fit is cut at a
    line boundary. (A claim is never clamped — that would be a false
    proposition.)
    """
    if _estimate_tokens(prompt) <= max_tokens:
        return prompt
    lines = prompt.splitlines()
    kept: list[str] = []
    total = 0
    for line in lines:
        tokens = _estimate_tokens(line)
        if kept and total + tokens > max_tokens:
            break
        kept.append(line)
        total += tokens
    return "\n".join(kept)


def _candidate_source_ids(candidate: Any, allowed_ids: set[str]) -> set[str]:
    """The chunk episode ids a candidate's belief is derived FROM.

    Only ids in ``allowed_ids`` (the CURRENT chunk's episodes) can be
    unmarked — the watermark only ever moves for the episodes the pass
    actually consumed. A candidate may cite episodes from an EARLIER
    chunk (already marked, already reflected on); those are never
    un-marked by a later chunk's failure.
    """
    sources = candidate.get("source_episode_ids")
    if not isinstance(sources, list):
        return set()
    return {
        str(item) for item in sources if item and str(item) in allowed_ids
    }


def _clamp_confidence(value: Any) -> float:
    """CLAMP the confidence into ``[0.1, 1.0]`` (§3-A4)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = MEMOIR_REFLECTION_MIN_CONFIDENCE
    if number != number:  # NaN
        number = MEMOIR_REFLECTION_MIN_CONFIDENCE
    return max(
        MEMOIR_REFLECTION_MIN_CONFIDENCE, min(MEMOIR_REFLECTION_MAX_CONFIDENCE, number)
    )


def _looks_like_refusal(text: str) -> bool:
    """Does the response read as a MODEL REFUSAL (a valid-empty)?"""
    lowered = text.casefold()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def _extract_json_object(text: str) -> str | None:
    """Pull the first JSON object out of the model's text.

    The house extractor (``daemon/services/skill_search_service.py``)
    is used — it tolerates markdown fences and surrounding prose. Its
    last-``}`` fallback can return a TRUNCATED object, which is why the
    caller ALSO validates the envelope's completeness (§3-A4).
    """
    from daemon.services.skill_search_service import _extract_json_object as _house

    return _house(text)


def _as_utc(value: datetime | None) -> datetime:
    """Normalize a timestamp to aware UTC."""
    if value is None:
        return now_utc()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def durable_pass_id(agent_id: str, cadence: str, episodes: Sequence[Any]) -> str:
    """THE DURABLE PASS IDENTITY (F-3) — minted at the PRODUCTION site.

    THE GUARD MUST BE BINDABLE: the ``last_pass_id`` crash-recovery
    guard (V2-29 / SF2) only protects a re-run when the re-run carries
    the SAME stamp.  A per-chunk uuid can NEVER bind it — the retry of a
    crashed pass mints a NEW stamp and inflates ``mention_count``.

    THE IDENTITY IS DERIVED, NOT RANDOM: it is a digest of the agent,
    the cadence and the pass's ANCHOR episode (the OLDEST episode the
    pass considered).  The anchor is durable state — it is the same
    episode after a crash-retry, because the crashed pass never marked
    it — so the retry mints the SAME id and the guard treats the re-run
    as the same pass.

    THE TRADE-OFF (disclosed): two passes that consider the SAME anchor
    episode share the identity, so the second one does not inflate the
    mention counts.  That is the CONSERVATIVE direction (the guard
    under-counts, it never over-counts), and it is the price of a
    durable identity without a new persisted column.

    Args:
        agent_id: The agent the pass belongs to.
        cadence: The pass cadence ("nightly" / "in_day") — two cadences
            over the same anchor are two DIFFERENT passes.
        episodes: The episodes the pass considered, OLDEST FIRST.

    Returns:
        The pass id, e.g. ``reflect-<16 hex chars>``.
    """
    anchor = ""
    for episode in episodes or ():
        candidate = str(getattr(episode, "id", "") or "")
        if candidate:
            anchor = candidate
            break
    digest = hashlib.sha256(
        f"{agent_id}|{cadence}|{anchor}".encode("utf-8")
    ).hexdigest()[:16]
    return f"reflect-{digest}"


def outcome_considered_episodes(
    episodes: Sequence[Any], outcome: PassOutcome
) -> list[Any]:
    """The episodes a pass actually CONSIDERED (the ones it may mark).

    An episode counts as considered when its chunk's LLM call produced a
    VALID envelope (including a valid-empty and a refusal) — the §3-A4
    rule. A chunk whose call failed is NOT considered: its episodes stay
    unreflected and are re-considered next pass.
    """
    considered_ids = getattr(outcome, "considered_ids", ())
    if not considered_ids:
        return []
    wanted = set(considered_ids)
    return [episode for episode in episodes if episode.id in wanted]


def _with_marked(outcome: PassOutcome, marked: int) -> PassOutcome:
    """Return a COPY of the outcome with the marked count filled in."""
    return PassOutcome(
        ran=outcome.ran,
        cadence=outcome.cadence,
        reason=outcome.reason,
        considered=outcome.considered,
        marked=marked,
        syntheses_accepted=outcome.syntheses_accepted,
        syntheses_rejected=outcome.syntheses_rejected,
        syntheses_duplicate=outcome.syntheses_duplicate,
        llm_calls=outcome.llm_calls,
        constellation_calls=outcome.constellation_calls,
        lines_written=outcome.lines_written,
        deferred=outcome.deferred,
        duration_s=outcome.duration_s,
        notes=outcome.notes,
        considered_ids=outcome.considered_ids,
    )


__all__ = [
    "MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS",
    "MEMOIR_REFLECTION_CALL_CONNECT_TIMEOUT_S",
    "MEMOIR_REFLECTION_CALL_TIMEOUT_S",
    "MEMOIR_REFLECTION_DEBOUNCE_BASE_S",
    "MEMOIR_REFLECTION_DEBOUNCE_CEILING_S",
    "MEMOIR_REFLECTION_FORCE_FIRE_S",
    "MEMOIR_REFLECTION_MAX_CHARS_PER_SYNTHESIS",
    "MEMOIR_REFLECTION_MAX_LLM_CALLS_PER_PASS",
    "MEMOIR_REFLECTION_MAX_PROMPT_TOKENS",
    "MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS",
    "MEMOIR_REFLECTION_NIGHTLY_CAP",
    "MEMOIR_REFLECTION_NIGHTLY_END_HOUR",
    "MEMOIR_REFLECTION_NIGHTLY_START_HOUR",
    "MEMOIR_REFLECTION_SCAN_WINDOW_DAYS",
    "MEMOIR_REFLECTION_TICK_BUDGET_S",
    "MEMOIR_REFLECTION_TRIGGER_SUM",
    "MemoirReflectionService",
    "PassOutcome",
    "ReflectionEnvelopeError",
    "ReflectionSettings",
    "resolve_reflection_settings",
]
