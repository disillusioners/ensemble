"""Memoir Store v2 — the REFLECTION repository (the dream's data layer).

Phase 2 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase2-plan.md`` T2.1/T2.3;
flags V2-19, V2-24, V2-27, V2-35).

This module owns EVERY read and write the reflection engine needs
against the two durable surfaces phase 1 shipped:

* ``memoir_episodes.reflected_at`` — the PER-EPISODE WATERMARK
  (V2-19). One named writer: this module. An episode is "reflected"
  once a reflection pass has CONSIDERED it (a valid parse, even an empty
  synthesis list — §3-A4's "model refusal is a valid-empty");
* ``memoir_reflection_state`` — the ONE durable row per agent
  (``id='ari'``) carrying ``last_nightly_date`` (the nightly watermark)
  and ``claimed_at`` (the claimant, with the 15-minute STALE-TAKEOVER
  window of V2-35 / SF5).

WHY A SEPARATE REPOSITORY (and not more methods on
``MemoirRepository``): the v1 repository is the byte-pinned recall
surface's owner (the hybrid ranker, the budget, the projection). The
reflection lane is a DIFFERENT concern with a DIFFERENT write authority
(the watermark), and the phase-1 precedent already split the v2
substrate per concern (``constellation_repository`` /
``syntheses_repository``). Keeping the reflection SQL here leaves the
v1 module untouched.

THE DERIVED TRIGGER (V2-D 2.2 / §3-A1): the trigger is DERIVED PER TICK
from the database, never from an in-memory rolling counter:

.. code-block:: sql

    SELECT COALESCE(SUM(COALESCE(poignancy, importance)), 0), MAX(created_at)
    FROM memoir_episodes
    WHERE agent_id = :ari AND reflected_at IS NULL
      AND created_at >= :window_start

DIALECT NOTE (ND-10): the plan writes the window as
``now() - interval '2 days'``, which is PostgreSQL syntax. The window is
bound as a PARAMETER here (``created_at >= :window_start``) so the
predicate is exactly equivalent on PostgreSQL AND SQLite — the plan's
own sanctioned escape hatch.

THE CLAIM (V2-35 / SF5): a claim is a row in ``memoir_reflection_state``
with ``claimed_at`` set. A claim is TAKEN when the row is absent, when
``claimed_at`` is NULL, or when the existing claim is STALE (older than
the 15-minute window — longer than the maximum pass wall-clock, so a
crashed claimant cannot block the next pass forever). The take is a
compare-and-set: the UPDATE carries the staleness predicate, so two
concurrent claimants can never both win.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterator, Sequence

from sqlalchemy import func, insert, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from daemon.repositories.memoir.models import (
    MemoirEpisode,
    MemoirReflectionState,
)
from daemon.services.timestamps import now_utc

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

#: The state row's id — ONE row per agent (V2-19).
MEMOIR_REFLECTION_STATE_ID = "ari"

#: The stale-takeover window (V2-35 / SF5): a claim older than this is
#: re-takeable by a subsequent pass. It is comfortably longer than the
#: maximum pass wall-clock (4 LLM calls x 45 s = 180 s), so a crashed
#: claimant cannot strand the lane.
MEMOIR_REFLECTION_CLAIM_STALE_SECONDS = 900.0

#: The scan-window default (days) — the regular intake is bounded to
#: ~48 hours (§3.2). The nightly catch-up pass walks the backlog
#: WITHOUT this restriction (C2 / V2-27).
MEMOIR_REFLECTION_SCAN_WINDOW_DAYS = 2

#: The nightly episode cap (§3.2) — the nightly pass consumes at most
#: this many episodes and defers the remainder.
MEMOIR_REFLECTION_NIGHTLY_CAP = 30


class MemoirReflectionRepository:
    """Sync repository over the reflection watermark + the nightly state.

    Sync class operating through ``Session`` blocks (the
    ``daemon/repositories/memoir/repository.py`` precedent).
    """

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine

    # ── the DERIVED trigger (V2-D 2.2) ───────────────────────────────────

    def derive_trigger(
        self,
        agent_id: str,
        *,
        window_start: datetime,
    ) -> tuple[int, datetime | None]:
        """Derive the reflection trigger from the DATABASE — per tick.

        The trigger is the SUM of the effective importance
        (``COALESCE(poignancy, importance)`` — the importance ruling,
        §A2) over the agent's UNREFLECTED episodes inside the window,
        plus the newest of those episodes (the idle anchor).

        NOTHING is cached: the sum is re-derived on every tick, so it is
        restart-durable, immune to a re-score, and self-clearing the
        moment an episode is marked ``reflected_at``.

        Args:
            agent_id: The owning agent.
            window_start: The inclusive lower bound of the scan window —
                BOUND AS A PARAMETER (dialect-neutral, ND-10).

        Returns:
            ``(sum, max_created_at)``. The sum is 0 and the timestamp is
            ``None`` when nothing is unreflected in the window.
        """
        if not agent_id:
            return 0, None
        statement = select(
            func.coalesce(
                func.sum(
                    func.coalesce(MemoirEpisode.poignancy, MemoirEpisode.importance)
                ),
                0,
            ),
            func.max(MemoirEpisode.created_at),
        ).where(
            MemoirEpisode.agent_id == agent_id,
            MemoirEpisode.reflected_at.is_(None),
            MemoirEpisode.created_at >= window_start,
        )
        with Session(self.engine) as session:
            row = session.execute(statement).first()
        if row is None:
            return 0, None
        total = int(row[0] or 0)
        newest = row[1]
        return total, _as_utc(newest)

    # ── the episode scan (oldest-unreflected-first) ─────────────────────

    def scan_unreflected(
        self,
        agent_id: str,
        *,
        limit: int = MEMOIR_REFLECTION_NIGHTLY_CAP,
        window_start: datetime | None = None,
    ) -> list[MemoirEpisode]:
        """Return the OLDEST unreflected episodes (the scan window).

        ``ORDER BY created_at ASC`` is the C2 / V2-27 ordering: the
        historical catch-up walks the backlog oldest-first, so the
        pre-cutover corpus is absorbed in write order rather than in
        whatever order the index happens to yield.

        Args:
            agent_id: The owning agent.
            limit: The episode cap (the nightly cap is 30).
            window_start: The inclusive lower bound, or ``None`` for the
                C2 catch-up pass (NO window restriction — the backlog is
                walked until it is caught up).
        """
        if not agent_id:
            return []
        statement = select(MemoirEpisode).where(
            MemoirEpisode.agent_id == agent_id,
            MemoirEpisode.reflected_at.is_(None),
        )
        if window_start is not None:
            statement = statement.where(MemoirEpisode.created_at >= window_start)
        statement = statement.order_by(
            MemoirEpisode.created_at.asc(), MemoirEpisode.id.asc()
        ).limit(max(0, int(limit)))
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    # ── the watermark writer ────────────────────────────────────────────

    def mark_reflected(
        self,
        episode_ids: Sequence[str],
        *,
        reflected_at: datetime | None = None,
        session: Session | None = None,
    ) -> int:
        """Mark episodes as CONSIDERED by a reflection pass.

        ONE column-targeted UPDATE — the watermark has exactly one named
        writer (this method), mirroring the ``embedded_at`` precedent.

        THE ``session`` OPTION (F-3): when a session is given, the mark
        rides THAT session and is NOT committed here — the caller owns
        the transaction, so the watermark can land in the SAME
        transaction as the constellation edges of the pass unit (the
        crash window of V2-29 / A2).

        Args:
            episode_ids: The episodes the pass consumed.
            reflected_at: The watermark stamp (the pass timestamp).
            session: An optional caller-owned session (the pass unit).

        Returns:
            The number of rows actually updated.
        """
        id_list = [str(item) for item in episode_ids if item]
        if not id_list:
            return 0
        stamp = reflected_at or now_utc()
        if session is not None:
            result = session.execute(
                update(MemoirEpisode)
                .where(MemoirEpisode.id.in_(id_list))
                .values(reflected_at=stamp)
            )
            session.flush()
            return int(result.rowcount or 0)
        with Session(self.engine) as own:
            result = own.execute(
                update(MemoirEpisode)
                .where(MemoirEpisode.id.in_(id_list))
                .values(reflected_at=stamp)
            )
            own.commit()
            return int(result.rowcount or 0)

    @contextmanager
    def unit_of_work(self) -> Iterator[Session]:
        """ONE transaction for the pass UNIT (F-3).

        The caller writes the unit's constellation edges AND the
        per-episode watermark through the yielded session, so both land
        together: a crash rolls them back as one, and the watermark can
        never advance ahead of the edges it claims.

        The ATOMICITY SCOPE is the processed episodes' unit (the chunk),
        NOT the whole pass — the lane's partial-commit posture stands.
        """
        with Session(self.engine) as session:
            with session.begin():
                yield session

    def known_episode_ids(
        self, agent_id: str, episode_ids: Sequence[str]
    ) -> set[str]:
        """Return the subset of ``episode_ids`` that EXISTS for an agent.

        The citation guard (V2-D 2.3): a synthesis must cite REAL
        episode ids, not invented ones. The check is a bounded IN query,
        never a per-id round trip.
        """
        id_list = [str(item) for item in episode_ids if item]
        if not agent_id or not id_list:
            return set()
        statement = select(MemoirEpisode.id).where(
            MemoirEpisode.agent_id == agent_id,
            MemoirEpisode.id.in_(id_list),
        )
        with Session(self.engine) as session:
            # ``.scalars()`` — a bare ``execute`` returns Row objects on
            # PostgreSQL (and plain strings on SQLite), so the ids must
            # be unwrapped explicitly or the caller receives tuples.
            return {str(value) for value in session.execute(statement).scalars().all()}

    # ── the durable nightly state (V2-19 / V2-35) ───────────────────────

    def get_state(
        self, state_id: str = MEMOIR_REFLECTION_STATE_ID
    ) -> MemoirReflectionState | None:
        """Return the durable state row, or ``None`` when absent."""
        if not state_id:
            return None
        with Session(self.engine) as session:
            return session.get(MemoirReflectionState, state_id)

    def last_nightly_date(
        self, state_id: str = MEMOIR_REFLECTION_STATE_ID
    ) -> str | None:
        """The date (``YYYY-MM-DD``) of the last COMPLETED nightly pass."""
        state = self.get_state(state_id)
        if state is None:
            return None
        return state.last_nightly_date

    def claim(
        self,
        *,
        now: datetime | None = None,
        stale_after_s: float = MEMOIR_REFLECTION_CLAIM_STALE_SECONDS,
        state_id: str = MEMOIR_REFLECTION_STATE_ID,
    ) -> bool:
        """TAKE the reflection claim — atomically (V2-35 / SF5).

        The claim is a compare-and-set on ``memoir_reflection_state``:

        * the row is ABSENT → the claimant creates it and wins;
        * the row exists and ``claimed_at`` is NULL → the claimant takes
          it and wins;
        * the row exists and ``claimed_at`` is STALE (older than
          ``stale_after_s``) → the claimant TAKES OVER (the crash
          recovery path) and wins;
        * the row exists with a FRESH claim → the claimant LOSES (the
          return value is ``False``).

        The take-over UPDATE carries the staleness predicate, so two
        concurrent claimants can never both win the same row.
        """
        stamp = now or now_utc()
        stale_before = stamp - timedelta(seconds=float(stale_after_s))
        table = MemoirReflectionState.__table__
        try:
            with self.engine.begin() as conn:
                existing = conn.execute(
                    select(table.c.id, table.c.claimed_at).where(
                        table.c.id == state_id
                    )
                ).first()
                if existing is None:
                    conn.execute(
                        insert(table).values(
                            id=state_id,
                            last_nightly_date=None,
                            claimed_at=stamp,
                        )
                    )
                    return True
                claimed_at = _as_utc(existing.claimed_at)
                if claimed_at is not None and claimed_at > stale_before:
                    # A FRESH claim — somebody else owns this pass.
                    return False
                result = conn.execute(
                    update(table)
                    .where(
                        table.c.id == state_id,
                        or_(
                            table.c.claimed_at.is_(None),
                            table.c.claimed_at <= stale_before,
                        ),
                    )
                    .values(claimed_at=stamp)
                )
                return int(result.rowcount or 0) == 1
        except IntegrityError:
            # A concurrent claimant created the row between our SELECT
            # and our INSERT. The row now EXISTS — fall through to the
            # compare-and-set path, which is the ONLY arbitration point.
            with self.engine.begin() as conn:
                result = conn.execute(
                    update(table)
                    .where(
                        table.c.id == state_id,
                        or_(
                            table.c.claimed_at.is_(None),
                            table.c.claimed_at <= stale_before,
                        ),
                    )
                    .values(claimed_at=stamp)
                )
                return int(result.rowcount or 0) == 1

    def release_claim(
        self,
        *,
        state_id: str = MEMOIR_REFLECTION_STATE_ID,
    ) -> bool:
        """Release the claim (the pass finished, or it never started).

        Returns:
            ``True`` when a row was updated.
        """
        table = MemoirReflectionState.__table__
        with self.engine.begin() as conn:
            result = conn.execute(
                update(table)
                .where(table.c.id == state_id)
                .values(claimed_at=None)
            )
            return int(result.rowcount or 0) == 1

    def commit_pass(
        self,
        episode_ids: Sequence[str],
        *,
        reflected_at: datetime | None = None,
        last_nightly_date: str | None = None,
        state_id: str = MEMOIR_REFLECTION_STATE_ID,
    ) -> int:
        """Commit a pass's watermarks AND its state row in ONE transaction.

        THE ATOMIC COMMIT (the mission's "marks + state committed in ONE
        transaction"): the per-episode ``reflected_at`` marks and the
        ``memoir_reflection_state`` update land together, so the
        watermark NEVER advances ahead of the beliefs it claims to
        reflect, and a crash can never leave a marked episode with an
        un-stamped nightly date (which would make the next night skip).

        Args:
            episode_ids: The episodes the pass CONSIDERED (only these may
                be marked).
            reflected_at: The watermark stamp.
            last_nightly_date: The nightly date to stamp (``None`` for
                an in-day pass — the state row's claim is still
                released).
            state_id: The state row's id.

        Returns:
            The number of episodes marked.
        """
        id_list = [str(item) for item in episode_ids if item]
        stamp = reflected_at or now_utc()
        table = MemoirReflectionState.__table__
        marked = 0
        with self.engine.begin() as conn:
            if id_list:
                result = conn.execute(
                    update(MemoirEpisode)
                    .where(MemoirEpisode.id.in_(id_list))
                    .values(reflected_at=stamp)
                )
                marked = int(result.rowcount or 0)
            # THE STATE ROW IS UPSERTED: the pass normally finds it (the
            # claim created it), but a standalone commit must not lose
            # the nightly stamp to a missing row.
            values: dict[str, Any] = {"claimed_at": None}
            if last_nightly_date is not None:
                values["last_nightly_date"] = last_nightly_date
            existing = conn.execute(
                select(table.c.id).where(table.c.id == state_id)
            ).first()
            if existing is None:
                conn.execute(
                    insert(table).values(
                        id=state_id,
                        last_nightly_date=last_nightly_date,
                        claimed_at=None,
                    )
                )
            else:
                conn.execute(
                    update(table).where(table.c.id == state_id).values(**values)
                )
        return marked

    def set_last_nightly_date(
        self,
        date_str: str,
        *,
        now: datetime | None = None,
        state_id: str = MEMOIR_REFLECTION_STATE_ID,
    ) -> bool:
        """Stamp the completion of a nightly pass (the nightly watermark).

        The stamp and the claim release are ONE transaction: a nightly
        pass that recorded its date but kept its claim would block the
        next night for the whole stale window.
        """
        stamp = now or now_utc()
        table = MemoirReflectionState.__table__
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(table.c.id).where(table.c.id == state_id)
            ).first()
            if existing is None:
                conn.execute(
                    insert(table).values(
                        id=state_id,
                        last_nightly_date=date_str,
                        claimed_at=None,
                    )
                )
                return True
            result = conn.execute(
                update(table)
                .where(table.c.id == state_id)
                .values(last_nightly_date=date_str, claimed_at=None)
            )
            return int(result.rowcount or 0) == 1


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize a timestamp read back from the driver to aware UTC.

    SQLite hands back a NAIVE datetime (it stores the UTC wall-clock
    digits); PostgreSQL hands back an aware one. The two are reconciled
    HERE so every comparison in this module is apples-to-apples.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "MEMOIR_REFLECTION_CLAIM_STALE_SECONDS",
    "MEMOIR_REFLECTION_NIGHTLY_CAP",
    "MEMOIR_REFLECTION_SCAN_WINDOW_DAYS",
    "MEMOIR_REFLECTION_STATE_ID",
    "MemoirReflectionRepository",
]
