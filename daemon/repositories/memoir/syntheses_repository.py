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

THE PHASE-2 DEDUP KEY (V2-17 / §3.2) is stricter: the composite
``sha256(claim_norm + sorted(source_episode_ids))``. It is computed by
:func:`synthesis_dedup_key` and used by :meth:`find_duplicate` — the
"skip insert, return the existing row" idempotency path that turns an
at-least-once reflection retry into a safe no-op. The table still needs
no new column (DD-2): the key is computed in Python over the same
bounded in-window set.

THE WINDOW CUTOFF IS COMPUTED IN PYTHON, not in SQL: the plan's
``now() - interval '2 days'`` is PostgreSQL syntax and SQLite has no
``interval``, so the cutoff is bound as a parameter. That keeps the
predicate EXACTLY equivalent on both drivers.

THE FOREIGN-BELIEF IMPORT CONVENTION (SF10 / V2-Q9, advisory): an
imported belief — a belief that originated OUTSIDE Ari's own episodes —
lands as a ``CORROBORATE``-class local synthesis row that CITES the
original source (the local episode that carries the imported belief).
It is ADVISORY: there is NO v2 validation machinery that checks whether
the foreign belief is true, and none is planned for v2. The convention
is a WRITER convention, documented here so a future writer (human or
machine) lands an import the same way every time: the verb says what
the import IS (corroboration of an existing belief, not the creation of
a new one), and the citations say where it came from.
"""

from __future__ import annotations

import logging
import hashlib
import re
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from sqlalchemy import insert, update
from sqlmodel import Session, select

from daemon.repositories.memoir.models import SYNTHESIS_VERBS, MemoirSynthesis
from daemon.repositories.memoir.repository import as_aware_utc
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


def synthesis_dedup_key(claim: str, source_episode_ids: Sequence[str]) -> str:
    """The PHASE-2 composite dedup key (V2-17 / §3.2).

    ``sha256(claim_norm + sorted(sources))`` — the claim normalized by
    :func:`normalize_claim` and the source episode ids SORTED, so the
    key is independent of the order the writer listed the citations in.

    The key makes a reflection retry IDEMPOTENT: the same claim over
    the same sources is the same belief, no matter how many times the
    pass re-runs after a crash.
    """
    normalized = normalize_claim(claim)
    sources = sorted({str(item) for item in (source_episode_ids or [])})
    payload = normalized + "|" + ",".join(sources)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
            RuntimeError: when the old row was claimed by ANOTHER writer
                between the read and the write (the concurrent
                double-claim guard). The transaction rolls back — the
                new row is not left orphaned — and the failure is LOUD
                rather than a silent double-link.
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
            # THE CONCURRENT-DOUBLE-CLAIM GUARD (phase-2 hardening): the
            # old-row UPDATE carries ``WHERE superseded_by IS NULL``, so
            # the pointer is written ONLY if nobody else claimed the row
            # in the meantime. The read above is a hint, NOT the
            # arbitration — two writers can pass it concurrently, and
            # only the UPDATE's predicate decides the winner. A rowcount
            # of 0 means somebody else won the race: the whole
            # transaction rolls back (the new row is NOT left orphaned)
            # and the caller is told LOUDLY.
            pointer = conn.execute(
                update(table)
                .where(table.c.id == old_synthesis_id, table.c.superseded_by.is_(None))
                .values(superseded_by=new_id)
            )
            if int(pointer.rowcount or 0) != 1:
                raise RuntimeError(
                    f"synthesis {old_synthesis_id!r} was claimed by another "
                    f"writer before this transaction committed — the "
                    f"supersession was refused (no fork was left behind)"
                )

        with Session(self.engine) as session:
            row = session.get(MemoirSynthesis, new_id)
            if row is None:  # pragma: no cover - the insert just landed
                raise KeyError(f"synthesis {new_id!r} not found after insert")
            session.refresh(row)
            return row

    # ── the reads ────────────────────────────────────────────────────────

    def find_duplicate(
        self,
        agent_id: str,
        claim: str,
        source_episode_ids: Sequence[str] | None,
    ) -> MemoirSynthesis | None:
        """Return the in-window ACTIVE row with the SAME dedup key.

        The PHASE-2 idempotency path (V2-17 / §3.2): the composite key
        is ``sha256(claim_norm + sorted(source_episode_ids))``. A
        reflection pass that re-runs after a crash asks this question
        first — a hit means "this belief already exists", so the pass
        SKIPS the insert and returns the existing row instead of
        growing a second copy of the same claim.

        The window is the same 2-day scan window the writer uses, and
        only ACTIVE (un-superseded) rows count: a superseded belief is
        history, and re-writing it would resurrect it.
        """
        if not agent_id:
            return None
        if source_episode_ids is None or len(list(source_episode_ids)) == 0:
            return None
        target = synthesis_dedup_key(claim, source_episode_ids)
        cutoff = now_utc() - timedelta(days=MEMOIR_SYNTHESIS_DEDUP_WINDOW_DAYS)
        with Session(self.engine) as session:
            rows = session.exec(
                select(MemoirSynthesis)
                .where(
                    MemoirSynthesis.agent_id == agent_id,
                    MemoirSynthesis.created_at >= cutoff,
                    MemoirSynthesis.superseded_by.is_(None),
                )
                .order_by(MemoirSynthesis.created_at.desc())
                .limit(MEMOIR_SYNTHESIS_DEDUP_SCAN_CAP)
            ).all()
        for row in rows:
            existing = synthesis_dedup_key(
                row.claim, list(row.source_episode_ids or [])
            )
            if existing == target:
                return row
        return None

    def search_active(
        self,
        agent_id: str,
        query: str,
        limit: int = MEMOIR_SYNTHESIS_LIST_LIMIT,
    ) -> list[MemoirSynthesis]:
        """Return the ACTIVE syntheses whose claim MATCHES the query.

        The dual-recall arm (§3-A6 / V2-D 2.4): the recall surfaces the
        consolidated beliefs that answer the query, ahead of the raw
        episodes.

        The match is LEXICAL and driver-agnostic: the query is split
        into tokens and a row matches when ANY token of length >= 2
        appears in its claim (case-insensitive). A claim shorter than a
        token cannot answer a query about that token, and a query with
        no usable token matches nothing.

        The rows are ordered by CONFIDENCE DESC (the strongest belief
        first) with the creation time as the tie-break — the order the
        recall renders and the order the envelope's tail-drop consumes
        (the weakest synthesis is the one at the tail).
        """
        if not agent_id or not query:
            return []
        tokens = [
            token
            for token in re.split(r"[^\w]+", query.casefold(), flags=re.UNICODE)
            if len(token) >= 2
        ]
        if not tokens:
            return []
        limit = max(0, int(limit))
        if limit == 0:
            return []
        with Session(self.engine) as session:
            rows = session.exec(
                select(MemoirSynthesis)
                .where(
                    MemoirSynthesis.agent_id == agent_id,
                    MemoirSynthesis.superseded_by.is_(None),
                )
                .limit(MEMOIR_SYNTHESIS_DEDUP_SCAN_CAP)
            ).all()
        lowered_query = query.casefold()
        matched = [
            row
            for row in rows
            if any(token in (row.claim or "").casefold() for token in tokens)
        ]
        matched.sort(
            key=lambda row: (
                -(float(row.confidence) if row.confidence is not None else 0.0),
                -(as_aware_utc(row.created_at).timestamp()
                  if as_aware_utc(row.created_at) else 0.0),
                row.id,
            )
        )
        return matched[:limit]

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
    "synthesis_dedup_key",
]
