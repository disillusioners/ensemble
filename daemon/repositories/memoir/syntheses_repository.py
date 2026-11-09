"""Memoir Store v2 — the syntheses repository.

Phase 1 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase1-plan.md`` T1.5,
V2-D 1.8 / 1.9; flags V2-15, V2-30).

``memoir_syntheses`` shipped EMPTY in v1 (DD-2) so v2 would add a
BEHAVIOUR, not migration churn. This module is that behaviour — the
four-layer store's layer 4:

* :meth:`MemoirSynthesesRepository.create_synthesis` — writes ONE
  synthesized belief, with MANDATORY CITATIONS (a belief with no
  source episode is a rumour, not a synthesis), a CLOSED VERB
  vocabulary (the same one the ``.sql`` CHECK enforces), and the
  per-agent SCAN-WINDOW DEDUP (SF4 / V2-30) that keeps the reflection
  from writing the same claim twice inside the 2-day window.
* :meth:`MemoirSynthesesRepository.supersede_with` — the ATOMIC
  SUPERSESSION WRITER (V2-D 1.8): the insert, the old row's
  ``superseded_by`` pointer and the new row's ``supersedes``
  back-pointer land in ONE transaction. A two-call create-then-mark
  pattern would leave an unlinked fork if the process died between the
  calls — the exact state the v4 conflict-resolution invariant
  forbids.
* :meth:`MemoirSynthesesRepository.get_synthesis` — the read of ONE
  row.
* :meth:`MemoirSynthesesRepository.list_active_syntheses` — the
  UN-superseded rows of an agent.

THE DEDUP KEY (V2-D 1.9): NFC + whitespace-collapse + casefold. The
comparison is made in Python against the ACTIVE (un-superseded)
syntheses of the SAME agent inside the 2-day window — the scale
rationale is that a reflection pass operates over a bounded window
(~dozens of claims), so the in-window check is instantaneous and the
table needs no new indexed column.

THE WINDOW CUTOFF IS COMPUTED IN PYTHON, not in SQL: the plan's
``now() - interval '2 days'`` is PostgreSQL syntax and SQLite has no
``interval``, so the cutoff is bound as a parameter. That keeps the
predicate EXACTLY equivalent on both drivers.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from sqlalchemy import insert, update
from sqlmodel import Session, select

from daemon.repositories.memoir.models import SYNTHESIS_VERBS, MemoirSynthesis
from daemon.services.timestamps import now_utc

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

#: The dedup window (V2-D 1.9 / SF4): the reflection operates over a
#: bounded 2-day window, and a claim already present in that window
#: (active, un-superseded, same agent) is NOT written again.
MEMOIR_SYNTHESIS_DEDUP_WINDOW_DAYS = 2

#: How many in-window rows the dedup scan compares. The window is
#: inherently bounded; the cap is a defence against a runaway writer
#: and is far above the ~dozens the plan's scale rationale expects.
MEMOIR_SYNTHESIS_DEDUP_SCAN_CAP = 1000

#: The default page size of :meth:`list_active_syntheses`.
MEMOIR_SYNTHESIS_LIST_LIMIT = 50


def normalize_claim(claim: str) -> str:
    """The dedup key: NFC + whitespace-collapse + casefold.

    NFC first (so a decomposed and a composed spelling of the same
    grapheme cluster compare equal), then whitespace-collapse (so a
    claim written across a line break matches the same claim written
    on one line), then casefold (the FULL Unicode casefold — not
    ``lower()``, which is not the same thing for e.g. the German sharp
    s).
    """
    if not isinstance(claim, str):
        raise ValueError("claim must be a string")
    normalized = unicodedata.normalize("NFC", claim)
    return " ".join(normalized.split()).casefold()


class MemoirSynthesesRepository:
    """Sync repository over the syntheses layer.

    Sync class operating through ``Session`` blocks (the
    ``daemon/repositories/memoir/repository.py`` precedent), with the
    atomic supersession writer using ``engine.begin()`` directly.
    """

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine

    # ── the writer ───────────────────────────────────────────────────────

    def create_synthesis(
        self,
        agent_id: str,
        claim: str,
        source_episode_ids: Sequence[str] | None,
        verb: str,
        confidence: float | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        supersedes: str | None = None,
    ) -> MemoirSynthesis:
        """Create ONE synthesis — or return the in-window duplicate.

        Raises:
            ValueError: when the citations are missing or empty
                (MANDATORY CITATIONS), or when the verb is outside the
                closed vocabulary (``SYNTHESIS_VERBS``).
        """
        if not agent_id:
            raise ValueError("agent_id is required")
        if not isinstance(claim, str) or not claim.strip():
            raise ValueError("claim is required")
        self._require_citations(source_episode_ids)
        self._require_verb(verb)

        cutoff = now_utc() - timedelta(days=MEMOIR_SYNTHESIS_DEDUP_WINDOW_DAYS)
        target = normalize_claim(claim)

        with Session(self.engine) as session:
            existing = session.exec(
                select(MemoirSynthesis)
                .where(
                    MemoirSynthesis.agent_id == agent_id,
                    MemoirSynthesis.created_at >= cutoff,
                    MemoirSynthesis.superseded_by.is_(None),
                )
                .order_by(MemoirSynthesis.created_at.desc())
                .limit(MEMOIR_SYNTHESIS_DEDUP_SCAN_CAP)
            ).all()
            for row in existing:
                if normalize_claim(row.claim) == target:
                    # THE IN-WINDOW DUPLICATE — the existing row IS the
                    # answer; the store does not grow a second copy.
                    return row

            row = MemoirSynthesis(
                id=uuid.uuid4().hex,
                agent_id=agent_id,
                claim=claim,
                source_episode_ids=list(source_episode_ids),
                valid_from=valid_from,
                valid_to=valid_to,
                verb=verb,
                supersedes=supersedes,
                superseded_by=None,
                confidence=confidence,
                created_at=now_utc(),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    # ── the atomic supersession writer ───────────────────────────────────

    def supersede_with(
        self,
        agent_id: str,
        old_synthesis_id: str,
        new_synthesis_dict: Mapping[str, Any],
    ) -> MemoirSynthesis:
        """Supersede ONE synthesis with a new one — ATOMICALLY.

        The insert of the new row, the old row's ``superseded_by``
        pointer and the new row's ``supersedes`` back-pointer land in
        ONE ``engine.begin()`` transaction (V2-D 1.8). A process death
        between the two halves would leave an unlinked fork — the new
        belief existing with no pointer, or the old belief pointing at
        a row that does not exist.

        Raises:
            ValueError: when the new row has no citations, or its verb
                is outside the closed vocabulary.
            KeyError: when the old synthesis does not exist for this
                agent, or is already superseded (re-superseding would
                overwrite the earlier pointer and lose the chain).
        """
        if not agent_id:
            raise ValueError("agent_id is required")
        if not old_synthesis_id:
            raise ValueError("old_synthesis_id is required")
        if not new_synthesis_dict:
            raise ValueError("new_synthesis_dict is required")

        new_values = dict(new_synthesis_dict)
        new_values.pop("agent_id", None)
        new_values.pop("supersedes", None)
        new_values.pop("superseded_by", None)

        source_episode_ids = new_values.get("source_episode_ids")
        self._require_citations(source_episode_ids)
        self._require_verb(new_values.get("verb"))

        new_id = str(new_values.pop("id", None) or uuid.uuid4().hex)
        new_values.setdefault("created_at", now_utc())

        table = MemoirSynthesis.__table__
        with self.engine.begin() as conn:
            old = conn.execute(
                select(table.c.id, table.c.superseded_by, table.c.agent_id).where(
                    table.c.id == old_synthesis_id
                )
            ).first()
            if old is None:
                raise KeyError(f"synthesis {old_synthesis_id!r} not found")
            if old.agent_id != agent_id:
                raise KeyError(
                    f"synthesis {old_synthesis_id!r} does not belong to agent "
                    f"{agent_id!r}"
                )
            if old.superseded_by is not None:
                raise ValueError(
                    f"synthesis {old_synthesis_id!r} is already superseded by "
                    f"{old.superseded_by!r}"
                )

            new_values["id"] = new_id
            new_values["agent_id"] = agent_id
            # THE BACK-POINTER — always to the row this call supersedes.
            new_values["supersedes"] = old_synthesis_id
            new_values["superseded_by"] = None
            conn.execute(insert(table).values(**new_values))
            conn.execute(
                update(table)
                .where(table.c.id == old_synthesis_id)
                .values(superseded_by=new_id)
            )

        with Session(self.engine) as session:
            row = session.get(MemoirSynthesis, new_id)
            if row is None:  # pragma: no cover - the insert just landed
                raise KeyError(f"synthesis {new_id!r} not found after insert")
            session.refresh(row)
            return row

    # ── the reads ────────────────────────────────────────────────────────

    def get_synthesis(self, synthesis_id: str) -> MemoirSynthesis | None:
        """Return ONE synthesis by id, or ``None``."""
        if not synthesis_id:
            return None
        with Session(self.engine) as session:
            return session.get(MemoirSynthesis, synthesis_id)

    def list_active_syntheses(
        self, agent_id: str, limit: int = MEMOIR_SYNTHESIS_LIST_LIMIT
    ) -> list[MemoirSynthesis]:
        """Return the ACTIVE (un-superseded) syntheses of an agent.

        Newest first.
        """
        if not agent_id:
            return []
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(MemoirSynthesis)
                    .where(
                        MemoirSynthesis.agent_id == agent_id,
                        MemoirSynthesis.superseded_by.is_(None),
                    )
                    .order_by(MemoirSynthesis.created_at.desc())
                    .limit(limit)
                ).all()
            )

    # ── the guards ───────────────────────────────────────────────────────

    @staticmethod
    def _require_citations(source_episode_ids: Sequence[str] | None) -> None:
        """MANDATORY CITATIONS — a synthesis without sources is refused."""
        if source_episode_ids is None:
            raise ValueError(
                "source_episode_ids is required — a synthesis must cite the "
                "episodes it was derived from"
            )
        if len(list(source_episode_ids)) == 0:
            raise ValueError(
                "source_episode_ids must not be empty — a synthesis must cite "
                "at least one episode"
            )

    @staticmethod
    def _require_verb(verb: Any) -> None:
        """The CLOSED verb vocabulary — the same one the CHECK enforces."""
        if verb not in SYNTHESIS_VERBS:
            raise ValueError(
                f"verb {verb!r} is not one of {sorted(SYNTHESIS_VERBS)}"
            )


__all__ = [
    "MEMOIR_SYNTHESIS_DEDUP_SCAN_CAP",
    "MEMOIR_SYNTHESIS_DEDUP_WINDOW_DAYS",
    "MEMOIR_SYNTHESIS_LIST_LIMIT",
    "MemoirSynthesesRepository",
    "normalize_claim",
]
