"""The POK repository — engine-passed CRUD over the two POK tables.

Layer discipline (the memoir precedent): this module NEVER builds an
engine and NEVER reads an ambient ``POSTGRES_*`` variable — the
repository is a thin wrapper around the CALLER'S engine
(``manager._engine``). Tests use a throwaway in-memory SQLite engine;
no ``InstanceManager`` is ever constructed.

What lives here (mechanics only, no policy):
* row CRUD (insert / get / list) and the SUPERSIDE TRANSACTION
  (flip prior active → superseded + insert successor, one commit —
  ``uq_pok_active_slug`` makes dual-active structurally impossible,
  so the txn is the only safe ordering);
* the curator flip (pinned / disputed / archived / audience /
  audience_agents / source_tier — the tier flip is Phase 2, trust-tier
  addendum T-3) with the forward-only archived guard;
* access/verify counters (query-path bookkeeping);
* the embeddings-companion CRUD (upsert / map / delete).

Policy (fence, budgets, caps, audience admission, refusal taxonomy)
lives in the service layer — ``daemon/services/pok_service.py`` and
``daemon/services/pok_injection.py``. NO FOREIGN KEY anywhere (§4.1):
``project_id`` / ``superseded_by_id`` are plain TEXT; the supersede
txn maintains the lineage wire.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import text as sa_text  # noqa: F401  (kept for parity)
from sqlmodel import Session, select

from daemon.repositories.pok.models import (
    POK_STATUSES,
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Unset:
    """Sentinel for 'leave audience_agents unchanged' in curate()."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "UNSET"


UNSET = _Unset()


class POKRepository:
    """CRUD + the supersede transaction over the caller's engine."""

    def __init__(self, engine: "Engine") -> None:
        self._engine = engine

    # ── reads ────────────────────────────────────────────────────────────

    def get(self, entry_id: str) -> ProjectOperatingKnowledge | None:
        with Session(self._engine) as session:
            return session.get(ProjectOperatingKnowledge, entry_id)

    def get_by_slug(
        self, project_id: str, slug: str, *, status: str | None = "active"
    ) -> ProjectOperatingKnowledge | None:
        stmt = select(ProjectOperatingKnowledge).where(
            ProjectOperatingKnowledge.project_id == project_id,
            ProjectOperatingKnowledge.slug == slug,
        )
        if status is not None:
            stmt = stmt.where(ProjectOperatingKnowledge.status == status)
        with Session(self._engine) as session:
            return session.exec(stmt).first()

    def list_entries(
        self,
        project_id: str,
        *,
        statuses: Sequence[str] = ("active",),
        include_superseded_of: bool = False,
    ) -> list[ProjectOperatingKnowledge]:
        """All entries of the project with the given statuses, slug order.

        UNIVERSAL READ (ER-1.4): callers NEVER pass an audience filter
        here — retrieval is audience-blind by invariant; the audience
        dimension gates ACTIVE injection only.
        """
        stmt = select(ProjectOperatingKnowledge).where(
            ProjectOperatingKnowledge.project_id == project_id,
            ProjectOperatingKnowledge.status.in_(list(statuses)),
        )
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
        rows.sort(key=lambda r: r.slug)
        return rows

    def count_active(self, project_id: str) -> int:
        stmt = (
            select(ProjectOperatingKnowledge)
            .where(
                ProjectOperatingKnowledge.project_id == project_id,
                ProjectOperatingKnowledge.status == "active",
            )
        )
        with Session(self._engine) as session:
            return len(list(session.exec(stmt).all()))

    def count_active_in_family(self, project_id: str, family: str) -> int:
        """Active rows whose slug starts ``<family>-`` (§4.5 family key)."""
        stmt = select(ProjectOperatingKnowledge).where(
            ProjectOperatingKnowledge.project_id == project_id,
            ProjectOperatingKnowledge.status == "active",
            ProjectOperatingKnowledge.slug.startswith(f"{family}-"),
        )
        with Session(self._engine) as session:
            return len(list(session.exec(stmt).all()))

    def lineage(self, entry_id: str) -> list[ProjectOperatingKnowledge]:
        """The supersede chain for one slug lineage, oldest first.

        Walks ``superseded_by_id`` forward from the oldest row that
        reaches ``entry_id`` (a row whose superseded_by_id == entry_id
        is its successor). Plain-TEXT wire (no FK) walked by hand.
        """
        with Session(self._engine) as session:
            anchor = session.get(ProjectOperatingKnowledge, entry_id)
            if anchor is None:
                return []
            rows = list(
                session.exec(
                    select(ProjectOperatingKnowledge).where(
                        ProjectOperatingKnowledge.project_id == anchor.project_id,
                        ProjectOperatingKnowledge.slug == anchor.slug,
                    )
                ).all()
            )
        rows.sort(key=lambda r: (r.created_at, r.id))
        return rows

    # ── writes ───────────────────────────────────────────────────────────

    def insert(self, entry: ProjectOperatingKnowledge) -> ProjectOperatingKnowledge:
        with Session(self._engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def supersede_and_insert(
        self,
        prior: ProjectOperatingKnowledge,
        successor: ProjectOperatingKnowledge,
    ) -> "tuple[ProjectOperatingKnowledge, str]":
        """THE supersede transaction (§4.2): flip prior → superseded
        (with ``superseded_by_id`` = successor id) and insert the
        successor, ONE commit.

        Ordering is load-bearing: the prior row must leave the
        ``active`` set before the successor enters it, or
        ``uq_pok_active_slug`` fires. One commit = no window.
        ``pinned`` does NOT carry over (the successor is unpinned;
        re-pinning is an explicit curator act via pok_curate).
        """
        prior_id = prior.id
        prior.status = "superseded"
        prior.superseded_by_id = successor.id
        prior.pinned = False
        prior.updated_at = _utc_now_iso()
        with Session(self._engine) as session:
            session.add(prior)
            session.add(successor)
            session.commit()
            session.refresh(successor)
        return successor, prior_id

    def curate(
        self,
        entry_id: str,
        *,
        pinned: bool | None = None,
        disputed: bool | None = None,
        archived: bool | None = None,
        audience: str | None = None,
        audience_agents: Any = UNSET,
        source_tier: Any = UNSET,
    ) -> ProjectOperatingKnowledge | None:
        """Apply curator flips (pok_curate).

        ``archived=True`` is FORWARD-ONLY (status moves to archived
        and never back — the un-archive refusal lives at the tool
        layer, the flip here just never writes a non-archived status).
        ``audience_agents`` uses the module ``UNSET`` sentinel to
        distinguish "leave unchanged" (default) from "clear back to
        NULL" (pass an empty list). A non-empty list is JSON-encoded
        at this boundary.
        ``source_tier`` (Phase 2 — trust-tier addendum T-3) uses the
        same UNSET sentinel; unlike ``audience_agents`` there is no
        clear-to-NULL form — the only writable values are the
        vocabulary members ('agent' | 'curated'), validated upstream.
        """
        with Session(self._engine) as session:
            row = session.get(ProjectOperatingKnowledge, entry_id)
            if row is None:
                return None
            if pinned is not None:
                row.pinned = bool(pinned)
            if disputed is not None:
                row.disputed = bool(disputed)
            if archived is True:
                row.status = "archived"
                row.pinned = False
            if audience is not None:
                row.audience = audience
            if audience_agents is not UNSET:
                row.audience_agents = (
                    json.dumps(list(audience_agents)) if audience_agents else None
                )
            if source_tier is not UNSET:
                row.source_tier = source_tier
            row.updated_at = _utc_now_iso()
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def bump_access(self, entry_ids: Iterable[str], *, now: str | None = None) -> int:
        """Query-path bookkeeping: access_count += 1, last_accessed_at."""
        ids = [i for i in entry_ids if i]
        if not ids:
            return 0
        stamp = now or _utc_now_iso()
        with Session(self._engine) as session:
            rows = list(
                session.exec(
                    select(ProjectOperatingKnowledge).where(
                        ProjectOperatingKnowledge.id.in_(ids)
                    )
                ).all()
            )
            for row in rows:
                row.access_count = (row.access_count or 0) + 1
                row.last_accessed_at = stamp
                session.add(row)
            session.commit()
            return len(rows)

    def record_verification(self, entry_id: str, *, now: str | None = None) -> bool:
        """verify_count += 1 + last_verified stamp (pok_verify phase-2
        path; the 30-day decay reads ``last_verified``)."""
        stamp = now or _utc_now_iso()
        with Session(self._engine) as session:
            row = session.get(ProjectOperatingKnowledge, entry_id)
            if row is None:
                return False
            row.verify_count = (row.verify_count or 0) + 1
            row.last_verified = stamp
            row.updated_at = stamp
            session.add(row)
            session.commit()
            return True

    def oldest_least_verified_active(
        self, project_id: str, *, exclude_ids: Sequence[str] = ()
    ) -> ProjectOperatingKnowledge | None:
        """The refusal-naming candidate (§4.2 admission): oldest active
        row, least verify_count — the archive-cure target the
        64-cap refusal names. Never the pinned rows (curator-owned).
        """
        stmt = select(ProjectOperatingKnowledge).where(
            ProjectOperatingKnowledge.project_id == project_id,
            ProjectOperatingKnowledge.status == "active",
            ProjectOperatingKnowledge.pinned == False,  # noqa: E712
        )
        if exclude_ids:
            stmt = stmt.where(
                ProjectOperatingKnowledge.id.not_in(list(exclude_ids))
            )
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
        if not rows:
            return None
        rows.sort(key=lambda r: (r.verify_count or 0, r.created_at, r.id))
        return rows[0]

    # ── embeddings companion (ranking-only) ─────────────────────────────

    def upsert_embedding(self, entry_id: str, vector: list[float]) -> None:
        with Session(self._engine) as session:
            existing = session.exec(
                select(ProjectOperatingKnowledgeEmbedding).where(
                    ProjectOperatingKnowledgeEmbedding.entry_id == entry_id
                )
            ).first()
            if existing is not None:
                existing.embedding = list(vector)
                existing.created_at = _utc_now_iso()
                session.add(existing)
            else:
                session.add(
                    ProjectOperatingKnowledgeEmbedding(
                        entry_id=entry_id, embedding=list(vector)
                    )
                )
            session.commit()

    def embeddings_map(self, project_id: str) -> dict[str, list[float]]:
        """``{entry_id: vector}`` for the project's entries (the
        selector's ``embeddings_map`` shape). Missing rows = BM25-only
        degradation for that entry (AC14 lag flag)."""
        stmt = select(ProjectOperatingKnowledgeEmbedding).join(
            ProjectOperatingKnowledge,
            ProjectOperatingKnowledge.id
            == ProjectOperatingKnowledgeEmbedding.entry_id,
        ).where(ProjectOperatingKnowledge.project_id == project_id)
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
        return {r.entry_id: list(r.embedding) for r in rows}

    def delete_embeddings_for(self, entry_ids: Iterable[str]) -> int:
        ids = [i for i in entry_ids if i]
        if not ids:
            return 0
        with Session(self._engine) as session:
            rows = list(
                session.exec(
                    select(ProjectOperatingKnowledgeEmbedding).where(
                        ProjectOperatingKnowledgeEmbedding.entry_id.in_(ids)
                    )
                ).all()
            )
            for row in rows:
                session.delete(row)
            session.commit()
            return len(rows)
