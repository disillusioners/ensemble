"""Memoir Store v2 — KEEPSAKE PROPOSALS (the proposal pipeline + the bridge).

Phase 6 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase6-plan.md`` T6.1/T6.3,
V2-D 6.1/6.2/6.3/6.6/6.7; packs
``test/packs/memoir_v2_keepsake_proposals_test.sh`` +
``test/packs/memoir_v2_proposal_notification_test.sh``).

WHAT THIS MODULE OWNS

1. **Qualification + staging (V2-D 6.7 / W2 / V2-36).** An episode is a
   keepsake-proposal CANDIDATE when
   ``COALESCE(poignancy, importance) >= 8``, it is NOT already a
   keepsake, it is not superseded, and it carries an ENDURING-significance
   shape (the heuristic below — the only judgment this phase adds). A
   candidate is staged into ``memoir_proposal_ledger`` with
   ``notification_status = 'pending'``.

2. **ONCE-EVER-PER-EPISODE (V2-36, LOAD-BEARING).** An episode that
   already has a ledger row is skipped PERMANENTLY — regardless of the
   row's outcome, age or status. The durable
   ``uq_memoir_proposal_agent_episode`` constraint is the second line of
   defence: a racing re-proposal attempt hits the constraint and is
   SWALLOWED as a skip (never an exception leak into the reflection pass).

3. **The 1/day notification bridge (V2-D 6.2 / V2-40 / B4).** This
   service is DAEMON-SCOPED (it runs inside the reflection worker's
   thread) and therefore cannot call ``notify_user`` — that tool requires
   an instance anchor. It ENQUEUES a message into Ari's own instance lane
   via ``manager.enqueue_message(instance_id, message,
   source="system:memoir-proposal")`` through the sanctioned manager-loop
   bridge (``asyncio.run_coroutine_threadsafe(...).result(8.0)``) — the
   RDRS lane-6 precedent at
   ``daemon/services/report_delivery_recovery.py:126-135``.

4. **The ledger slot is consumed ONLY on an ACCEPTED enqueue (V2-23 →
   V2-40, the invariant that survived the mechanism change).** The row is
   flipped ``pending → enqueued`` (+ ``notified_at``) ONLY after the
   bridge returns; an enqueue failure — or the absence of a running ari
   instance — leaves the row ``pending``, so the day's slot is NOT
   consumed and a later pass may retry.

   THE 1/DAY GATE KEYS ON THE CONSUMPTION DAY, NEVER ON THE STAGING
   DATE. ``proposal_date`` is stamped when the row is STAGED; it is not
   the day the slot was SPENT, so a row staged on D and enqueued on D+1
   (the ordinary "no active instance at staging" path) would leave D+1
   uncounted and the same episode re-announced every following day. The
   gate therefore counts the CONSUMPTION event:
   ``COUNT(*) WHERE notified_at IN [today00:00Z, tomorrow00:00Z)``.
   THE NOTIFY TARGET IS ``pending`` ROWS ONLY for the same reason — an
   ``enqueued`` row has already been announced and is never the next
   announcement (the briefing's zone keeps ``pending``/``enqueued``, so
   an enqueued-but-unreviewed proposal still SURFACES there).

5. **Restart-safety by construction.** Every piece of state above lives in
   the durable ledger; nothing here is memoized across passes.

WHAT THIS MODULE NEVER DOES

* It NEVER pins (V2-D 6.3 / R2). Pinning stays the Emperor/Empress
  confirmation path through the v1 ``memoir_pin(id, reason, actor)``
  tool — the actor gate that records WHO confirmed. Proposals are never
  auto-pinned, and this module holds no reference to the pin writer.
* It never raises out of :meth:`MemoirKeepsakeProposalService.process_pass`
  — a proposal failure must not fail a reflection pass (the phase-2/5
  ordering doctrine: every post-synthesis consumer is isolated).

NO LLM CALLS. Phase 6 is heuristic + render only (plan §1, §4): the
enduring-significance test is a deterministic rule over the episode row.

FENCE: this module NEVER builds an engine and NEVER reads an ambient
``POSTGRES_*`` variable — the engine is the CALLER'S (the reflection
worker shares ``manager.engine``), exactly like
``daemon/services/memoir_briefing.py``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from daemon.repositories.memoir.models import (
    MEMOIR_PROPOSAL_REASON_CHAR_CAP,
    MemoirEpisode,
    MemoirProposalLedger,
    render_proposal_line,
)
from daemon.services.timestamps import coerce_to_aware_utc, now_utc

logger = logging.getLogger(__name__)

#: The agent whose proposals this lane owns (the memoir lane is ari-only,
#: the same identity the briefing and the reflection service pin).
MEMOIR_PROPOSAL_AGENT_ID = "ari"

#: The qualification threshold (V2-D 6.1 / V2-11): the EFFECTIVE
#: poignancy — ``COALESCE(poignancy, importance)`` (the importance ruling,
#: §3-A2) — must reach this value for an episode to be proposal-eligible.
MEMOIR_KEEPSAKE_POIGNANCY_THRESHOLD = 8

#: The ENDURING-SIGNIFICANCE heuristic's substance floor, in characters of
#: collapsed body text. A terse line ("ok") scored 9 by the poignancy
#: model is an intensity signal, not an enduring one; a keepsake proposal
#: is a claim about what must NEVER be lost, so the row must carry enough
#: substance to judge. Chosen at the phase-6 build; recorded in
#: ``docs/memoir-v2/implementation-notes.md`` §16 as a plan-defined gap
#: (the plan names the heuristic but never specifies its rule).
MEMOIR_PROPOSAL_MIN_BODY_CHARS = 80

#: How many candidate episodes ONE pass may stage. Bounded so a large
#: backfill never floods the ledger (and the briefing zone's top-3
#: selection) in a single tick.
MEMOIR_PROPOSAL_STAGE_CAP_PER_PASS = 10

#: The BOUNDED OVER-FETCH multiplier (W-2). The candidate window pages up
#: to this multiple of the window per pass, collecting ELIGIBLE rows —
#: the substance floor is enforced read-side (dialect-neutral: it must
#: stay in Python), so a terse head-of-line would otherwise hold the
#: slots on EVERY pass and starve the eligible rows behind it. The bound
#: is deliberate: an unbounded walk would trade starvation for a
#: full-corpus scan on every tick.
MEMOIR_PROPOSAL_WINDOW_FETCH_MULTIPLE = 3

#: The bridge's `.result()` timeout (the RDRS lane-6 precedent value).
MEMOIR_PROPOSAL_ENQUEUE_TIMEOUT_S = 8.0

#: The source token the bridge stamps on the enqueued delivery (V2-D 6.2
#: verbatim). It starts with ``system:`` so the messaging layer stamps the
#: message ``injected_message=True`` (daemon-minted traffic, never
#: masquerading as a user turn) and classifies it SYSTEM.
MEMOIR_PROPOSAL_SOURCE = "system:memoir-proposal"

#: The CLOSED four-token ledger vocabulary (V2-D 1.5 / V2-28 — the model's
#: CHECK constraint is the enforcer; these constants keep the call sites
#: and the DDL from drifting).
PROPOSAL_STATUS_PENDING = "pending"
PROPOSAL_STATUS_ENQUEUED = "enqueued"
PROPOSAL_STATUS_SENT = "sent"
PROPOSAL_STATUS_FAILED = "failed"

PROPOSAL_STATUSES: frozenset[str] = frozenset(
    {
        PROPOSAL_STATUS_PENDING,
        PROPOSAL_STATUS_ENQUEUED,
        PROPOSAL_STATUS_SENT,
        PROPOSAL_STATUS_FAILED,
    }
)


@dataclass(frozen=True)
class ProposalCandidateRow:
    """One row of the briefing's proposals-zone SELECT (the JOIN result).

    The ledger alone carries NEITHER the display columns NOR the
    effective poignancy — ``title`` / ``event_at`` / ``poignancy`` /
    ``importance`` all live on ``memoir_episodes`` — which is why the
    selection is an explicit JOIN (plan §3.1 / N2).
    """

    ledger_id: str
    episode_id: str
    title: str | None
    event_at: Any
    effective_poignancy: int | None
    reason: str
    proposal_date: str
    notification_status: str
    proposed_at: datetime


@dataclass(frozen=True)
class CandidateScan:
    """The window scan's result — the ELIGIBLE candidates + the census.

    ``episodes`` holds only ELIGIBLE rows (strongest first, at most the
    window's ``limit``). ``skipped_ineligible`` counts the rows the scan
    examined and rejected on the substance floor — the read-side
    exclusion the SQL cannot express DIALECT-NEUTRALLY (SQLite's
    ``length()`` and PostgreSQL's ``char_length()`` do not count the
    same thing).
    """

    episodes: tuple[MemoirEpisode, ...] = ()
    skipped_ineligible: int = 0


@dataclass(frozen=True)
class ProposalPassOutcome:
    """What ONE proposal pass did (the metered truth of the lane)."""

    staged: int = 0
    skipped_existing: int = 0
    skipped_ineligible: int = 0
    enqueued: bool = False
    enqueue_failure: str | None = None
    proposal_ids: tuple[str, ...] = field(default_factory=tuple)


class MemoirKeepsakeProposalService:
    """The keepsake-proposal pipeline: qualify, stage, notify (at most 1/day).

    Constructed with the CALLER'S engine (the reflection worker's shared
    ``manager.engine``) — the service never builds one (the fence).

    The enqueue bridge is INJECTABLE (``enqueue_bridge``) so the lane is
    testable without an event loop; the production default resolves the
    manager's loop and calls ``manager.enqueue_message`` exactly as the
    RDRS precedent does.
    """

    def __init__(
        self,
        engine: Any,
        *,
        agent_id: str = MEMOIR_PROPOSAL_AGENT_ID,
        clock: Callable[[], datetime] | None = None,
        manager: Any = None,
        enqueue_bridge: Callable[[str, str, str], Any] | None = None,
        enqueue_timeout_s: float = MEMOIR_PROPOSAL_ENQUEUE_TIMEOUT_S,
    ) -> None:
        self._engine = engine
        self._agent_id = agent_id
        self._clock = clock or now_utc
        self._manager = manager
        self._enqueue_bridge = enqueue_bridge
        self._enqueue_timeout_s = float(enqueue_timeout_s)

    # ── identity ────────────────────────────────────────────────────────

    @property
    def agent_id(self) -> str:
        """The agent this lane stages proposals for."""
        return self._agent_id

    # ── the qualification (V2-D 6.7 / W2) ───────────────────────────────

    def has_proposal(self, episode_id: str, *, agent_id: str | None = None) -> bool:
        """``True`` when the episode ALREADY has a ledger row (ONCE-EVER).

        ANY row counts — pending, enqueued, sent or failed — regardless of
        its age or outcome (V2-36: a rejected or stale proposal must never
        re-nag).
        """
        if not episode_id:
            return False
        owner = agent_id or self._agent_id
        with Session(self._engine) as session:
            row = session.exec(
                select(MemoirProposalLedger.id).where(
                    MemoirProposalLedger.agent_id == owner,
                    MemoirProposalLedger.episode_id == episode_id,
                )
            ).first()
        return row is not None

    @staticmethod
    def is_enduring(episode: Any) -> bool:
        """The ENDURING-SIGNIFICANCE heuristic (deterministic, no LLM).

        An episode is ENDURING when it is:
          * not superseded (a superseded record is a retracted belief —
            proposing it for forever-status would enshrine a correction),
          * not already a keepsake (the plan's hard exclusion), and
          * substantive: its collapsed body carries at least
            :data:`MEMOIR_PROPOSAL_MIN_BODY_CHARS` characters.

        The poignancy threshold is enforced by the SQL predicate (it uses
        the COALESCE over the two columns and must stay one query); this
        method is the part of the rule the database cannot express
        without a second index.
        """
        if bool(getattr(episode, "is_keepsake", False)):
            return False
        if getattr(episode, "superseded_by", None):
            return False
        body = " ".join(str(getattr(episode, "body", "") or "").split())
        return len(body) >= MEMOIR_PROPOSAL_MIN_BODY_CHARS

    def compose_reason(self, episode: Any) -> str:
        """Compose the proposal's ``<reason>`` — deterministic, bounded.

        The reason states WHY the row qualified, from row facts only: the
        effective poignancy and (when present) the star count. It is
        capped by construction at
        :data:`MEMOIR_PROPOSAL_REASON_CHAR_CAP`; a longer composition is
        never truncated mid-word because the inputs are two integers.
        """
        poignancy = self.effective_poignancy(episode)
        stars = getattr(episode, "stars", None) or []
        reason = (
            f"enduring significance: effective poignancy "
            f"{poignancy}/10"
        )
        if stars:
            reason = f"{reason}, {len(list(stars))} star(s)"
        return reason[:MEMOIR_PROPOSAL_REASON_CHAR_CAP]

    @staticmethod
    def effective_poignancy(episode: Any) -> int | None:
        """``COALESCE(poignancy, importance)`` — the ONE effective score."""
        poignancy = getattr(episode, "poignancy", None)
        if poignancy is not None:
            return int(poignancy)
        importance = getattr(episode, "importance", None)
        return None if importance is None else int(importance)

    # ── the staging ─────────────────────────────────────────────────────

    def scan_candidates(
        self,
        *,
        limit: int = MEMOIR_PROPOSAL_STAGE_CAP_PER_PASS,
        agent_id: str | None = None,
    ) -> CandidateScan:
        """The candidate episodes, STRONGEST first — ELIGIBLE ones only.

        WHERE EACH EXCLUSION LIVES:

        * **IN THE QUERY (the threshold + all THREE exclusions).** The
          poignancy threshold, plus the ``is_keepsake`` flag, the
          ``superseded_by`` pointer, and the agent-scoped once-ever
          ledger (an agent-scoped ``notin_`` subquery over the SAME
          ``(agent_id, episode_id)`` predicate the staging write
          enforces — one SQL predicate, not a second JOIN).
          A ledgered / superseded / already-pinned row therefore cannot
          OCCUPY one of the ``limit`` slots and starve the eligible rows
          below it (W-1 — the post-window read-side skip left the slots
          occupied; the council's probe: 12 ledgered poignancy-9 rows
          starved the remaining 2).
        * **READ-SIDE, IN PYTHON (the substance floor).** The collapsed
          body must carry at least :data:`MEMOIR_PROPOSAL_MIN_BODY_CHARS`
          characters. SQL cannot express that DIALECT-NEUTRALLY —
          SQLite's ``length()`` and PostgreSQL's ``char_length()`` do not
          count the same thing — so the floor stays in Python.

        THE BOUNDED OVER-FETCH (W-2). The floor is enforced AFTER the
        query, so a HIGH-poignancy-but-terse row would otherwise hold a
        window slot and be skipped — and a skipped row is never ledgered,
        so it never LEAVES the window: an unbounded head of terse rows
        starves the eligible rows behind them on EVERY pass (a PERMANENT
        wedge, not the self-healing ledgered case). The scan therefore
        PAGES: it reads up to ``MEMOIR_PROPOSAL_WINDOW_FETCH_MULTIPLE``
        (3) × the window per pass and keeps collecting until it holds
        ``limit`` ELIGIBLE rows, applying the floor in Python as it goes.

        THE BOUND IS HONEST AND DISCLOSED. A pathological corpus whose
        whole head-of-line is terse can still defer the eligible rows
        behind it to a LATER pass — the bound is 3 × the window, NOT an
        unbounded walk (an unbounded walk would trade starvation for a
        full-corpus scan on every tick). The deferral self-heals as soon
        as the terse head leaves the window. The disclosure lives in
        ``docs/memoir-v2/implementation-notes.md`` §16.6.
        """
        owner = agent_id or self._agent_id
        target = max(0, int(limit))
        if target == 0:
            return CandidateScan(episodes=(), skipped_ineligible=0)
        effective = func.coalesce(MemoirEpisode.poignancy, MemoirEpisode.importance)
        # W-1: exclude ineligible rows IN THE QUERY (same-class
        # completion for both the ledger exclusion and the superseded
        # filter).
        ledgered = select(MemoirProposalLedger.episode_id).where(
            MemoirProposalLedger.agent_id == owner,
        )
        base_filters = (
            MemoirEpisode.agent_id == owner,
            effective >= MEMOIR_KEEPSAKE_POIGNANCY_THRESHOLD,
            MemoirEpisode.is_keepsake.is_(False),
            MemoirEpisode.superseded_by.is_(None),
            MemoirEpisode.id.notin_(ledgered),
        )
        # A TOTAL order — the id breaks an exact effective/created_at
        # tie — so the pages of the over-fetch cannot overlap.
        order = (
            effective.desc(),
            MemoirEpisode.created_at.desc(),
            MemoirEpisode.id.asc(),
        )
        fetch_cap = target * MEMOIR_PROPOSAL_WINDOW_FETCH_MULTIPLE
        eligible: list[MemoirEpisode] = []
        skipped_ineligible = 0
        fetched = 0
        with Session(self._engine) as session:
            while fetched < fetch_cap and len(eligible) < target:
                page_size = min(target, fetch_cap - fetched)
                statement = (
                    select(MemoirEpisode)
                    .where(*base_filters)
                    .order_by(*order)
                    .offset(fetched)
                    .limit(page_size)
                )
                rows = list(session.exec(statement).all())
                if not rows:
                    break
                for row in rows:
                    fetched += 1
                    if not self.is_enduring(row):
                        # The SUBSTANCE FLOOR — skipped and NEVER ledgered,
                        # so it stays in the window; the over-fetch is what
                        # keeps it from starving the rows behind it.
                        skipped_ineligible += 1
                        continue
                    eligible.append(row)
                    if len(eligible) >= target:
                        break
                if len(rows) < page_size:
                    break  # the corpus is exhausted
        return CandidateScan(
            episodes=tuple(eligible), skipped_ineligible=skipped_ineligible
        )

    def stage_candidate(
        self,
        episode: Any,
        *,
        now: datetime | None = None,
        agent_id: str | None = None,
    ) -> str | None:
        """Stage ONE candidate row; return the ledger id or ``None``.

        ``None`` means SKIPPED, and it is never an error:
          * the episode already has a row (ONCE-EVER, V2-36), or
          * the row lost a race — the durable
            ``uq_memoir_proposal_agent_episode`` constraint rejected the
            insert, which IS the same skip expressed by the database.

        The inserted row carries ``notification_status = 'pending'``: the
        slot is NOT consumed until an enqueue is ACCEPTED.
        """
        owner = agent_id or self._agent_id
        episode_id = str(getattr(episode, "id", "") or "")
        if not episode_id:
            return None
        if not self.is_enduring(episode):
            return None
        moment = now or self._clock()
        ledger_id = uuid.uuid4().hex
        row = MemoirProposalLedger(
            id=ledger_id,
            agent_id=owner,
            episode_id=episode_id,
            proposal_date=moment.date().isoformat(),
            proposed_at=moment,
            notification_status=PROPOSAL_STATUS_PENDING,
            notified_at=None,
            reason=self.compose_reason(episode),
            created_at=moment,
        )
        try:
            with Session(self._engine) as session:
                session.add(row)
                session.commit()
        except IntegrityError:
            # THE RACE — the unique constraint is the once-ever guard's
            # durable half. A skip, never an exception leak.
            logger.info(
                "[MemoirProposal] episode %s already has a ledger row "
                "(unique constraint) — skipped",
                episode_id[:8],
            )
            return None
        return ledger_id

    # ── the zone selection (T6.2 / N2) ──────────────────────────────────

    def select_zone_rows(
        self,
        *,
        limit: int = 3,
        agent_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> list[ProposalCandidateRow]:
        """The briefing proposals zone's selection — the EXPLICIT JOIN.

        The ledger alone cannot render a proposal line: it carries neither
        the effective poignancy nor ``title`` / ``event_at``. The JOIN onto
        ``memoir_episodes`` is therefore REQUIRED (plan §3.1 / N2).

        WHERE ``notification_status IN ('pending', 'enqueued')`` by
        DEFAULT — a ``sent`` / ``failed`` row is history, not an open
        question — AND ``is_keepsake = False`` (W-3: an already-pinned
        episode is no longer proposable, so its pending proposal is
        neither announced nor rendered — the no-spam doctrine) AND
        ``superseded_by IS NULL`` (W-3's SAME-CLASS completion: a
        superseded record is a retracted belief, so its pending proposal
        is neither announced nor rendered either — otherwise it would
        consume the day's single announcement slot), then
        ``ORDER BY COALESCE(poignancy, importance) DESC,
        proposed_at DESC`` and the caller's cap (the zone renders at most
        ``MEMOIR_PROPOSALS_ZONE_MAX`` lines).

        ``statuses`` lets the BRIDGE narrow the same ordering to
        ``('pending',)``: the zone SURFACES an enqueued-but-unreviewed
        proposal, but an ``enqueued`` row has ALREADY been announced and
        is never the next announcement (re-announcing it would starve
        every later proposal — see :meth:`notify_once`).
        """
        owner = agent_id or self._agent_id
        wanted = (
            (PROPOSAL_STATUS_PENDING, PROPOSAL_STATUS_ENQUEUED)
            if statuses is None
            else tuple(statuses)
        )
        effective = func.coalesce(MemoirEpisode.poignancy, MemoirEpisode.importance)
        statement = (
            select(
                MemoirProposalLedger.id,
                MemoirProposalLedger.episode_id,
                MemoirEpisode.title,
                MemoirEpisode.event_at,
                effective,
                MemoirProposalLedger.reason,
                MemoirProposalLedger.proposal_date,
                MemoirProposalLedger.notification_status,
                MemoirProposalLedger.proposed_at,
            )
            .join(
                MemoirEpisode,
                MemoirEpisode.id == MemoirProposalLedger.episode_id,
            )
            .where(
                MemoirProposalLedger.agent_id == owner,
                MemoirProposalLedger.notification_status.in_(list(wanted)),
                MemoirEpisode.is_keepsake.is_(False),
                MemoirEpisode.superseded_by.is_(None),
            )
            .order_by(effective.desc(), MemoirProposalLedger.proposed_at.desc())
            .limit(max(0, int(limit)))
        )
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        return [
            ProposalCandidateRow(
                ledger_id=str(row[0]),
                episode_id=str(row[1]),
                title=row[2],
                event_at=row[3],
                effective_poignancy=(None if row[4] is None else int(row[4])),
                reason=str(row[5] or ""),
                proposal_date=str(row[6]),
                notification_status=str(row[7]),
                proposed_at=row[8],
            )
            for row in rows
        ]

    def count_notified_today(
        self,
        *,
        day: str | None = None,
        agent_id: str | None = None,
    ) -> int:
        """The 1/day gate: notifications CONSUMED on THIS calendar day (V2-28).

        KEYED ON THE CONSUMPTION DAY (``notified_at``), NEVER ON
        ``proposal_date``. ``proposal_date`` is the STAGING stamp: a row
        staged on D and enqueued on D+1 — the ordinary "no active
        instance at staging" path, which deliberately leaves the row
        ``pending`` — would leave D+1 uncounted under a staging-date gate,
        so the same episode would be re-announced every following day.

        The consumption event IS the enqueue (V2-40), so the count is
        status-INDEPENDENT: a row that consumed a slot stays counted
        after any downstream status advance (``sent`` / ``failed``); a
        ``pending`` row has no ``notified_at`` and consumes nothing.
        """
        owner = agent_id or self._agent_id
        day_key = day or self._clock().date().isoformat()
        day_start = datetime.combine(
            date.fromisoformat(day_key), time.min, tzinfo=timezone.utc
        )
        day_end = day_start + timedelta(days=1)
        with Session(self._engine) as session:
            total = session.exec(
                select(func.count())
                .select_from(MemoirProposalLedger)
                .where(
                    MemoirProposalLedger.agent_id == owner,
                    MemoirProposalLedger.notified_at >= day_start,
                    MemoirProposalLedger.notified_at < day_end,
                )
            ).first()
        return int(total or 0)

    def mark_enqueued(self, ledger_id: str, *, now: datetime | None = None) -> bool:
        """Consume the slot for ONE row: ``pending → enqueued`` (+ stamp).

        Called ONLY after an ACCEPTED enqueue (V2-23 → V2-40's surviving
        invariant). Returns ``True`` when the row moved.

        The stamp is COERCED TO AWARE UTC: ``notified_at`` IS the gate's
        calendar-day key, so it must carry ONE unambiguous instant (the
        house assume-UTC policy for a naive value).
        """
        if not ledger_id:
            return False
        moment = coerce_to_aware_utc(now or self._clock())
        with Session(self._engine) as session:
            row = session.get(MemoirProposalLedger, ledger_id)
            if row is None:
                return False
            row.notification_status = PROPOSAL_STATUS_ENQUEUED
            row.notified_at = moment
            session.add(row)
            session.commit()
        return True

    # ── the active-instance resolution (ND-22) ──────────────────────────

    def resolve_active_instance_id(self, *, agent_id: str | None = None) -> str | None:
        """Ari's RUNNING instance id, or ``None`` (ND-22).

        ``agent_id = 'ari' AND status = 'running' ORDER BY updated_at DESC
        LIMIT 1`` — the instance lane the bridge delivers into. A missing
        running instance is NOT an error: the slot stays ``pending`` and a
        later pass retries (V2-D 6.2's own wording).
        """
        owner = agent_id or self._agent_id
        try:
            from daemon.repositories.instance.models import Instance

            statement = (
                select(Instance.instance_id)
                .where(
                    Instance.agent_id == owner,
                    Instance.status == "running",
                )
                .order_by(Instance.updated_at.desc())
                .limit(1)
            )
            with Session(self._engine) as session:
                found = session.exec(statement).first()
            return str(found) if found else None
        except Exception as exc:  # noqa: BLE001 — never fail the pass
            logger.warning(
                "[MemoirProposal] active-instance lookup failed (%s) — the "
                "proposal stays pending",
                type(exc).__name__,
            )
            return None

    # ── the bridge (V2-D 6.2 / V2-40 / B4) ──────────────────────────────

    def _bridge_message(self, row: ProposalCandidateRow) -> str:
        """Compose the bridged message (plan §3.1's shape, one line)."""
        title = (row.title or "(untitled)").strip()
        return (
            f"[system:memoir-proposal] Ari has reflected and proposed 1 new "
            f"keepsake: '{title}'. Review via briefing."
        )

    def _enqueue(
        self,
        instance_id: str,
        message: str,
        source: str,
    ) -> Any:
        """Deliver the message into the instance lane (the sanctioned bridge).

        The production path mirrors the RDRS lane-6 precedent: resolve the
        MANAGER'S loop (``manager._loop``, set during ``initialize()``) and
        call ``manager.enqueue_message`` through
        ``asyncio.run_coroutine_threadsafe(...).result(timeout)``. This
        service runs on the reflection worker's thread, so there is no
        running loop here — the bridge is the only correct shape.

        A missing/closed loop raises; the caller treats any failure as "the
        enqueue was not accepted" (the slot stays ``pending``).
        """
        if self._enqueue_bridge is not None:
            return self._enqueue_bridge(instance_id, message, source)

        manager = self._manager
        if manager is None:
            raise RuntimeError("no manager is bound to the proposal service")
        loop = getattr(manager, "_loop", None)
        if loop is None or loop.is_closed():
            raise RuntimeError("the manager event loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(
            manager.enqueue_message(instance_id, message, source=source),
            loop,
        )
        return future.result(timeout=self._enqueue_timeout_s)

    def notify_once(
        self,
        *,
        now: datetime | None = None,
        agent_id: str | None = None,
    ) -> tuple[bool, str | None]:
        """Enqueue AT MOST ONE proposal notification for today.

        Returns ``(enqueued, failure_reason)``. The slot is consumed ONLY
        on success (:meth:`mark_enqueued`): every failure path leaves the
        row ``pending`` so the day's slot survives for a later retry.

        The candidate is the STRONGEST row that has NOT been announced
        — ``pending`` only, in the briefing's own ordering. An
        ``enqueued`` row is excluded from the TARGET (it was already
        announced; re-selecting it would re-send the same episode forever
        and starve every weaker proposal) even though the ZONE still
        renders it. A day that already consumed its slot is silent
        (V2-D 6.1 / R8) — measured on the CONSUMPTION day
        (:meth:`count_notified_today`), not on the staging date.
        """
        owner = agent_id or self._agent_id
        # The gate's day key and the row's stamp must name the SAME
        # instant, so both are read in UTC (the house assume-UTC policy
        # for a naive value) — a local-timezone ``now`` would otherwise
        # count a UTC day the stamp does not belong to.
        moment = coerce_to_aware_utc(now or self._clock())
        if self.count_notified_today(
            day=moment.date().isoformat(), agent_id=owner
        ) > 0:
            return False, "daily_slot_consumed"
        open_rows = self.select_zone_rows(
            limit=1, agent_id=owner, statuses=(PROPOSAL_STATUS_PENDING,)
        )
        if not open_rows:
            return False, "no_proposal"
        row = open_rows[0]
        instance_id = self.resolve_active_instance_id(agent_id=owner)
        if not instance_id:
            # NO ACTIVE INSTANCE — the slot is PRESERVED (V2-D 6.2).
            return False, "no_active_instance"
        try:
            self._enqueue(instance_id, self._bridge_message(row), MEMOIR_PROPOSAL_SOURCE)
        except Exception as exc:  # noqa: BLE001 — never fail the pass
            logger.warning(
                "[MemoirProposal] enqueue bridge failed (%s: %s) — the "
                "proposal stays pending (the daily slot is NOT consumed)",
                type(exc).__name__,
                exc,
            )
            return False, f"enqueue_failed:{type(exc).__name__}"
        self.mark_enqueued(row.ledger_id, now=moment)
        logger.info(
            "[MemoirProposal] bridged 1 keepsake proposal to %s… "
            "(ledger=%s, slot consumed for %s)",
            instance_id[:8],
            row.ledger_id[:8],
            moment.date().isoformat(),
        )
        return True, None

    # ── the pass seam (the reflection service calls THIS) ────────────────

    def process_pass(
        self,
        *,
        now: datetime | None = None,
        agent_id: str | None = None,
        limit: int = MEMOIR_PROPOSAL_STAGE_CAP_PER_PASS,
        notify: bool = True,
    ) -> ProposalPassOutcome:
        """One proposal pass: qualify → stage → notify (at most 1/day).

        NEVER raises: a ledger or bridge failure degrades the pass (the
        reflection pass that called it must not fail because a proposal
        could not be staged — the §2-Q4 ordering doctrine).
        """
        owner = agent_id or self._agent_id
        # UTC (the house assume-UTC policy) so the STAGING stamp and the
        # CONSUMPTION stamp below name the same calendar day.
        moment = coerce_to_aware_utc(now or self._clock())
        staged = 0
        skipped_existing = 0
        skipped_ineligible = 0
        proposal_ids: list[str] = []
        try:
            scan = self.scan_candidates(limit=limit, agent_id=owner)
            # The scan's census carries the substance-floor skips; the
            # check below STAYS as the race guard (a row that turned
            # ineligible between the window read and the staging write).
            skipped_ineligible = scan.skipped_ineligible
            for episode in scan.episodes:
                if not self.is_enduring(episode):
                    skipped_ineligible += 1
                    continue
                if self.has_proposal(str(episode.id), agent_id=owner):
                    # THE ONCE-EVER SKIP — permanent, no error (V2-36).
                    skipped_existing += 1
                    continue
                ledger_id = self.stage_candidate(
                    episode, now=moment, agent_id=owner
                )
                if ledger_id is None:
                    # Either the once-ever race or a persistence refusal —
                    # both are skips, never failures.
                    skipped_existing += 1
                    continue
                staged += 1
                proposal_ids.append(ledger_id)
        except Exception as exc:  # noqa: BLE001 — never fail the pass
            logger.warning(
                "[MemoirProposal] staging degraded (%s: %s) — the reflection "
                "pass continues",
                type(exc).__name__,
                exc,
            )

        enqueued = False
        failure: str | None = None
        if notify:
            try:
                enqueued, failure = self.notify_once(now=moment, agent_id=owner)
            except Exception as exc:  # noqa: BLE001 — never fail the pass
                enqueued, failure = False, f"notify_failed:{type(exc).__name__}"
                logger.warning(
                    "[MemoirProposal] notification degraded (%s) — the "
                    "reflection pass continues",
                    failure,
                )
        return ProposalPassOutcome(
            staged=staged,
            skipped_existing=skipped_existing,
            skipped_ineligible=skipped_ineligible,
            enqueued=enqueued,
            enqueue_failure=failure,
            proposal_ids=tuple(proposal_ids),
        )

    # ── the briefing's read seam ────────────────────────────────────────

    def zone_lines(
        self,
        *,
        limit: int = 3,
        agent_id: str | None = None,
    ) -> list[str]:
        """The proposals zone's LINE bodies (the briefing renders these).

        Rendered through :func:`daemon.repositories.memoir.models.render_proposal_line`
        — the SAME function the zone admission measures with, so the
        measured bytes and the shipped bytes are identical (W-1).
        """
        return [
            render_proposal_line(
                title=row.title,
                event_at=row.event_at,
                poignancy=row.effective_poignancy,
                reason=row.reason,
            )
            for row in self.select_zone_rows(limit=limit, agent_id=agent_id)
        ]


__all__ = [
    "CandidateScan",
    "MEMOIR_KEEPSAKE_POIGNANCY_THRESHOLD",
    "MEMOIR_PROPOSAL_AGENT_ID",
    "MEMOIR_PROPOSAL_ENQUEUE_TIMEOUT_S",
    "MEMOIR_PROPOSAL_MIN_BODY_CHARS",
    "MEMOIR_PROPOSAL_SOURCE",
    "MEMOIR_PROPOSAL_STAGE_CAP_PER_PASS",
    "MEMOIR_PROPOSAL_WINDOW_FETCH_MULTIPLE",
    "MemoirKeepsakeProposalService",
    "PROPOSAL_STATUSES",
    "PROPOSAL_STATUS_ENQUEUED",
    "PROPOSAL_STATUS_FAILED",
    "PROPOSAL_STATUS_PENDING",
    "PROPOSAL_STATUS_SENT",
    "ProposalCandidateRow",
    "ProposalPassOutcome",
]
