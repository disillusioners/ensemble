"""Memoir Store v2 — the Constellation repository.

Phase 1 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase1-plan.md`` T1.4,
V2-D 1.5 / 1.7 / 1.12).

The Constellation is a RELATIONAL 1–2 hop graph (V2-2): no graph
database, just tables and joins. This module is its write and read
surface:

* :meth:`MemoirConstellationRepository.upsert_star` — the canonical
  entity node. The canonical name is the IDENTITY: ``(agent_id, name)``
  is unique, and an existing star's ``last_touched_at`` only ever moves
  FORWARD (``max(existing, event_at)``) so a late-written mention can
  never rewind the timeline.
* :meth:`MemoirConstellationRepository.add_alias` — an alternative
  surface form bound to exactly one canonical star. The unique
  constraint is the guarantee, so a COLLISION RAISES (the caller
  decides whether the alias belongs to another star or is a mistake).
* :meth:`MemoirConstellationRepository.resolve_star` — canonical name
  OR alias → the one canonical star.
* :meth:`MemoirConstellationRepository.record_line` — the episodic
  co-occurrence edge, with the ``last_pass_id`` CRASH-RECOVERY GUARD
  (SF2 / V2-29): a re-run of the SAME extraction pass must not
  double-count the mention.
* :meth:`MemoirConstellationRepository.get_star_card` — the read
  surface: the star, its aliases, its recent mention lines and its
  1-hop neighbours.
* :meth:`MemoirConstellationRepository.increment_heat` — the heat
  accumulator, written by explicit recall.
* :meth:`MemoirConstellationRepository.update_star_description` — the
  LightRAG summarization writer, which also stamps the 24h cooldown
  watermark (``description_as_of``, V2-D 1.7).

THE ``last_pass_id`` GUARD, PRECISELY (V2-D 1.12):
``mention_count`` is incremented only when the stored ``last_pass_id``
is DISTINCT FROM the incoming pass id. The guard is evaluated IN SQL
(``IS DISTINCT FROM``), not in Python, because that is the plan's
form and because the two are not interchangeable for NULL: SQLite
learned ``IS DISTINCT FROM`` in 3.39 and the host runs 3.53.1
(measured — see the phase-1 report), so the SQL form is used here.
The plan's dialect-neutral fallback
(``last_pass_id IS NULL OR last_pass_id != :pass_id``) is the escape
hatch for a driver without ``IS DISTINCT FROM``; it is NOT used here
because the form was VERIFIED on a throwaway SQLite database first.

MUTABILITY (the v1 lane's partition, inherited): every mutable family
has exactly ONE named writer. ``description`` is written ONLY by
:meth:`update_star_description` — ``upsert_star`` never rewrites it —
so the cooldown watermark and the text it describes can never drift
apart.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, update
from sqlmodel import Session, select

from daemon.repositories.memoir.models import (
    MemoirLine,
    MemoirStar,
    MemoirStarAlias,
)
from daemon.services.timestamps import coerce_to_aware_utc, now_utc

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

#: How many recent mention lines a star card carries. The card is a
#: READING surface, not an export: the constellation can hold thousands
#: of lines per star and the card is a summary.
MEMOIR_STAR_CARD_LINE_LIMIT = 20

#: How many 1-hop neighbours a star card carries.
MEMOIR_STAR_CARD_NEIGHBOR_LIMIT = 50


def _as_utc(value: datetime | None) -> datetime | None:
    """Coerce an inbound timestamp to aware UTC.

    Every inbound timestamp goes through the house assume-UTC policy
    (:func:`daemon.services.timestamps.coerce_to_aware_utc`) so the
    STORED digits are the UTC digits on SQLite (which drops the offset)
    and the stored INSTANT is the same on PostgreSQL.
    """
    coerced = coerce_to_aware_utc(value)
    if coerced is None:
        return None
    return coerced.astimezone(timezone.utc)


class MemoirConstellationRepository:
    """Sync repository over the Constellation substrate.

    Sync class operating through ``Session`` blocks (the
    ``daemon/repositories/memoir/repository.py`` precedent).
    """

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine

    # ── the node ──────────────────────────────────────────────────────────

    def upsert_star(
        self,
        agent_id: str,
        name: str,
        star_type: str,
        description: str | None = None,
        event_at: datetime | None = None,
    ) -> MemoirStar:
        """Upsert ONE canonical star and return it.

        An existing star's ``last_touched_at`` moves FORWARD only
        (``max(existing, event_at)``) — a late-written mention must not
        rewind the timeline. ``event_at=None`` leaves it untouched.

        The ``description`` of an EXISTING star is NOT rewritten here:
        :meth:`update_star_description` is the sole writer of that
        family (it also stamps the cooldown watermark).

        The ``star_type`` of an existing star is likewise not rewritten:
        the first classification wins, and the CHECK constraint keeps
        the vocabulary closed.
        """
        if not agent_id:
            raise ValueError("agent_id is required")
        if not name:
            raise ValueError("name is required")
        if not star_type:
            raise ValueError("star_type is required")

        event = _as_utc(event_at)
        written_at = now_utc()

        with Session(self.engine) as session:
            star = session.exec(
                select(MemoirStar).where(
                    MemoirStar.agent_id == agent_id,
                    MemoirStar.name == name,
                )
            ).first()
            if star is not None:
                if event is not None:
                    current = _as_utc(star.last_touched_at)
                    if current is None or event > current:
                        star.last_touched_at = event
                        session.add(star)
                        session.commit()
                        session.refresh(star)
                return star

            star = MemoirStar(
                id=uuid.uuid4().hex,
                agent_id=agent_id,
                name=name,
                star_type=star_type,
                description=description or "",
                first_seen_at=event or written_at,
                last_touched_at=event or written_at,
                heat=0.0,
                created_at=written_at,
            )
            session.add(star)
            session.commit()
            session.refresh(star)
            return star

    # ── the alias ────────────────────────────────────────────────────────

    def add_alias(self, agent_id: str, star_id: str, alias: str) -> MemoirStarAlias:
        """Bind ONE alternative surface form to a canonical star.

        Raises:
            sqlalchemy.exc.IntegrityError: when the alias is already
                bound to another star for this agent — the unique
                constraint ``uq_memoir_alias_agent_alias`` IS the
                guarantee that an alias resolves to exactly one star.
        """
        if not agent_id:
            raise ValueError("agent_id is required")
        if not star_id:
            raise ValueError("star_id is required")
        if not alias:
            raise ValueError("alias is required")

        row = MemoirStarAlias(
            id=uuid.uuid4().hex,
            agent_id=agent_id,
            star_id=star_id,
            alias=alias,
            created_at=now_utc(),
        )
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    # ── the resolution ────────────────────────────────────────────────────

    def resolve_star(self, agent_id: str, query_name: str) -> MemoirStar | None:
        """Resolve a surface form to its canonical star.

        The canonical name wins; an alias is the fallback. Returns
        ``None`` when the surface form is unknown — resolution NEVER
        invents a star.
        """
        if not agent_id or not query_name:
            return None
        with Session(self.engine) as session:
            star = session.exec(
                select(MemoirStar).where(
                    MemoirStar.agent_id == agent_id,
                    MemoirStar.name == query_name,
                )
            ).first()
            if star is not None:
                return star
            alias_row = session.exec(
                select(MemoirStarAlias).where(
                    MemoirStarAlias.agent_id == agent_id,
                    MemoirStarAlias.alias == query_name,
                )
            ).first()
            if alias_row is None:
                return None
            return session.get(MemoirStar, alias_row.star_id)

    # ── the edge ─────────────────────────────────────────────────────────

    def record_line(
        self,
        agent_id: str,
        star_id: str,
        episode_id: str,
        event_at: datetime | None = None,
        pass_id: str | None = None,
        session: Session | None = None,
    ) -> MemoirLine:
        """Record ONE episodic mention of a star.

        The CRASH-RECOVERY GUARD (SF2 / V2-29): re-running the SAME
        extraction pass (``pass_id`` unchanged) must NOT increment
        ``mention_count``. A DISTINCT pass does increment it, and also
        advances ``last_mentioned_at`` and the stored ``last_pass_id``.

        The guard is evaluated in SQL with ``IS DISTINCT FROM`` — the
        plan's form (V2-D 1.12), VERIFIED on the host SQLite.

        THE ``session`` OPTION (F-3): when a session is given, the edge
        rides THAT session and is NOT committed here — the caller owns
        the transaction, so the edge can land in the SAME transaction as
        the per-episode reflection watermark (the pass unit).
        """
        if not agent_id:
            raise ValueError("agent_id is required")
        if not star_id:
            raise ValueError("star_id is required")
        if not episode_id:
            raise ValueError("episode_id is required")

        event = _as_utc(event_at) or now_utc()
        written_at = now_utc()

        if session is not None:
            return self._write_line(
                session,
                agent_id,
                star_id,
                episode_id,
                event,
                written_at,
                pass_id,
                commit=False,
            )
        with Session(self.engine) as own:
            return self._write_line(
                own,
                agent_id,
                star_id,
                episode_id,
                event,
                written_at,
                pass_id,
                commit=True,
            )

    def _write_line(
        self,
        session: Session,
        agent_id: str,
        star_id: str,
        episode_id: str,
        event: datetime,
        written_at: datetime,
        pass_id: str | None,
        *,
        commit: bool,
    ) -> MemoirLine:
        """The guard-aware edge upsert (one session, one commit)."""
        line = session.exec(
            select(MemoirLine).where(
                MemoirLine.star_id == star_id,
                MemoirLine.episode_id == episode_id,
            )
        ).first()
        if line is None:
            line = MemoirLine(
                id=uuid.uuid4().hex,
                agent_id=agent_id,
                star_id=star_id,
                episode_id=episode_id,
                mention_count=1,
                last_pass_id=pass_id,
                first_mentioned_at=event,
                last_mentioned_at=event,
                created_at=written_at,
            )
            session.add(line)
            if commit:
                session.commit()
            else:
                session.flush()
            session.refresh(line)
            return line

        distinct = session.execute(
            select(MemoirLine.last_pass_id.is_distinct_from(pass_id)).where(
                MemoirLine.id == line.id
            )
        ).scalar()
        if not distinct:
            # SAME PASS — the re-run is a no-op. The row is left
            # completely untouched: the guard exists so a crashed
            # pass can be replayed without distorting the counts.
            if commit:
                session.refresh(line)
            return line

        current = _as_utc(line.last_mentioned_at)
        line.mention_count = line.mention_count + 1
        line.last_mentioned_at = event if current is None or event > current else current
        line.last_pass_id = pass_id
        session.add(line)
        if commit:
            session.commit()
        else:
            session.flush()
        session.refresh(line)
        return line

    # ── the read surface ─────────────────────────────────────────────────

    def get_star_card(
        self,
        agent_id: str,
        star_id: str,
        line_limit: int = MEMOIR_STAR_CARD_LINE_LIMIT,
        neighbor_limit: int = MEMOIR_STAR_CARD_NEIGHBOR_LIMIT,
    ) -> dict[str, Any]:
        """Return the star card — the star, its aliases, its lines, its
        1-hop neighbours.

        An unknown star yields a card with ``star=None`` and empty
        lists, not an exception: the card is a READING surface and a
        missing star is an ordinary answer.
        """
        with Session(self.engine) as session:
            star = session.exec(
                select(MemoirStar).where(
                    MemoirStar.id == star_id,
                    MemoirStar.agent_id == agent_id,
                )
            ).first()
            if star is None:
                return {"star": None, "aliases": [], "lines": [], "neighbors": []}

            aliases = session.exec(
                select(MemoirStarAlias)
                .where(MemoirStarAlias.agent_id == agent_id)
                .order_by(MemoirStarAlias.alias)
            ).all()
            alias_rows = [
                alias for alias in aliases if alias.star_id == star_id
            ]

            lines = session.exec(
                select(MemoirLine)
                .where(
                    MemoirLine.agent_id == agent_id,
                    MemoirLine.star_id == star_id,
                )
                .order_by(MemoirLine.last_mentioned_at.desc())
                .limit(line_limit)
            ).all()

            # 1-HOP NEIGHBOURS — the stars that share at least one
            # episode with this star. This is the whole point of the
            # relational design (V2-2): no graph database, one join.
            neighbor_ids = session.execute(
                select(MemoirLine.star_id)
                .where(
                    MemoirLine.agent_id == agent_id,
                    MemoirLine.episode_id.in_(
                        select(MemoirLine.episode_id).where(
                            MemoirLine.agent_id == agent_id,
                            MemoirLine.star_id == star_id,
                        )
                    ),
                    MemoirLine.star_id != star_id,
                )
                .distinct()
            ).scalars().all()
            neighbors: list[dict[str, Any]] = []
            if neighbor_ids:
                neighbor_rows = session.exec(
                    select(MemoirStar)
                    .where(
                        MemoirStar.agent_id == agent_id,
                        MemoirStar.id.in_(list(neighbor_ids)),
                    )
                    .order_by(MemoirStar.heat.desc())
                    .limit(neighbor_limit)
                ).all()
                neighbors = [
                    {
                        "id": neighbor.id,
                        "name": neighbor.name,
                        "star_type": neighbor.star_type,
                        "heat": neighbor.heat,
                    }
                    for neighbor in neighbor_rows
                ]

            return {
                "star": {
                    "id": star.id,
                    "agent_id": star.agent_id,
                    "name": star.name,
                    "star_type": star.star_type,
                    "description": star.description,
                    "description_as_of": star.description_as_of,
                    "first_seen_at": star.first_seen_at,
                    "last_touched_at": star.last_touched_at,
                    "heat": star.heat,
                    "created_at": star.created_at,
                },
                "aliases": [
                    {"id": alias.id, "alias": alias.alias, "star_id": alias.star_id}
                    for alias in alias_rows
                ],
                "lines": [
                    {
                        "id": line.id,
                        "episode_id": line.episode_id,
                        "mention_count": line.mention_count,
                        "last_pass_id": line.last_pass_id,
                        "first_mentioned_at": line.first_mentioned_at,
                        "last_mentioned_at": line.last_mentioned_at,
                    }
                    for line in lines
                ],
                "neighbors": neighbors,
            }

    # ── the heat accumulator ─────────────────────────────────────────────

    def increment_heat(self, agent_id: str, star_id: str, amount: float = 1.0) -> float:
        """Atomically increment the stored heat accumulator.

        ONE UPDATE statement — the increment never reads, adds and
        writes back (a read-modify-write would lose a concurrent
        increment). The result is clamped at 0.0 so the write can never
        violate the model's ``heat >= 0.0`` CHECK and abort the
        transaction.

        Raises:
            KeyError: when the star does not exist for this agent.
        """
        with Session(self.engine) as session:
            result = session.execute(
                update(MemoirStar)
                .where(
                    MemoirStar.id == star_id,
                    MemoirStar.agent_id == agent_id,
                )
                .values(
                    heat=case(
                        (MemoirStar.heat + amount < 0.0, 0.0),
                        else_=MemoirStar.heat + amount,
                    )
                )
            )
            if result.rowcount == 0:
                session.rollback()
                raise KeyError(
                    f"memoir star {star_id!r} not found for agent {agent_id!r}"
                )
            session.commit()
            session.expire_all()
            heat = session.execute(
                select(MemoirStar.heat).where(MemoirStar.id == star_id)
            ).scalar()
            return float(heat) if heat is not None else 0.0

    # ── the description writer ───────────────────────────────────────────

    def update_star_description(
        self,
        agent_id: str,
        star_id: str,
        description: str,
        as_of_ts: datetime | None = None,
    ) -> bool:
        """Write the star description and stamp its cooldown watermark.

        ``description_as_of`` (V2-D 1.7) is the 24h LightRAG
        summarization watermark: ``last_touched_at`` moves on every
        mention and therefore CANNOT carry the summarization time.

        Returns ``True`` when a row was written, ``False`` when the
        star does not exist for this agent.
        """
        with Session(self.engine) as session:
            star = session.exec(
                select(MemoirStar).where(
                    MemoirStar.id == star_id,
                    MemoirStar.agent_id == agent_id,
                )
            ).first()
            if star is None:
                return False
            star.description = description
            star.description_as_of = _as_utc(as_of_ts) or now_utc()
            session.add(star)
            session.commit()
            return True


__all__ = [
    "MEMOIR_STAR_CARD_LINE_LIMIT",
    "MEMOIR_STAR_CARD_NEIGHBOR_LIMIT",
    "MemoirConstellationRepository",
]
