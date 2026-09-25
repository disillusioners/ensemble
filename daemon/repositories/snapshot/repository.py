"""Repository for the Agent Snapshot system (PR3).

* :class:`SnapshotRepository` — CRUD on :class:`Snapshot` plus the
  R12 ``create-mints-successor`` atomic flip and the D3 boot sweep.

Design highlights
-----------------
* **Engine sharing.** Constructor takes a SQLAlchemy ``Engine`` only —
  the shared engine is created once at the ``InstanceManager`` level
  and passed to every repository (same shape as
  :mod:`daemon.repositories.skill.repository`).

* **R12 atomic flip.** :meth:`SnapshotRepository.create_successor`
  performs the successor INSERT (``status='active'`` +
  ``supersedes_snapshot_id=<prev>``) and the previous row's
  ``status → 'superseded'`` UPDATE inside ONE session/transaction —
  no torn state under concurrent search (verification rider (g)).
  Cross-root supersession is permitted (no same-root enforcement —
  design R12; validity is creator judgment).

* **Tag filter (R8).** :meth:`SnapshotRepository.filter_by_tags`
  implements ``tag_mode: all|any`` over ``domain_tags``. On
  PostgreSQL the JSONB ``@>`` containment operator does the work;
  on SQLite (JSON = TEXT) candidates are scanned in Python — the
  JSON-string scan is acceptable at v1 volumes (rider (f)).

* **Sync calls.** All methods are synchronous; callers bridge to
  async via ``asyncio.to_thread`` (the project's standard pattern).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Sequence

from sqlalchemy import func, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .models import (
    SNAPSHOT_STATUSES,
    SNAPSHOT_STATUS_ACTIVE,
    SNAPSHOT_STATUS_INTERRUPTED,
    SNAPSHOT_STATUS_RUNNING,
    SNAPSHOT_STATUS_SUPERSEDED,
    Snapshot,
    SnapshotEmbedding,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class SnapshotRepository:
    """CRUD + supersession for the ``snapshots`` table."""

    def __init__(self, engine: Engine) -> None:
        """Initialize the repository.

        Args:
            engine: Shared SQLAlchemy engine (created once at the
                manager level and passed to every repository).
        """
        self.engine = engine

    # ── Writes ────────────────────────────────────────────────────────

    def create_with_embeddings(
        self,
        snapshot: Snapshot,
        embeddings: Sequence[SnapshotEmbedding] | None = None,
    ) -> Snapshot:
        """Insert a snapshot row (and optional embedding rows) in one
        transaction.

        Args:
            snapshot: The row to insert. ``status`` must be a known
                ledger/lifecycle state (guard raises ``ValueError``
                otherwise — fail loud).
            embeddings: Optional :class:`SnapshotEmbedding` rows;
                their ``snapshot_id`` is stamped to the snapshot's id.

        Returns:
            The persisted snapshot.

        Raises:
            ValueError: Unknown ``status`` value (fail loud — the
                status column is the contract every search filter
                leans on).
        """
        if snapshot.status not in SNAPSHOT_STATUSES:
            raise ValueError(
                f"Unknown snapshot status {snapshot.status!r}; "
                f"expected one of {sorted(SNAPSHOT_STATUSES)}"
            )
        rows = list(embeddings or [])
        with Session(self.engine) as session:
            session.add(snapshot)
            for emb in rows:
                emb.snapshot_id = snapshot.id
                session.add(emb)
            session.commit()
            session.refresh(snapshot)
        return snapshot

    def create_successor(
        self,
        snapshot: Snapshot,
        supersedes_snapshot_id: str,
        embeddings: Sequence[SnapshotEmbedding] | None = None,
    ) -> Snapshot:
        """R12 create-mints-successor — INSERT successor + flip previous
        to ``superseded`` in ONE transaction.

        The new row is inserted with ``status='active'`` and
        ``supersedes_snapshot_id`` set; the previous row's status is
        updated to ``'superseded'`` in the SAME transaction — the
        atomic active→superseded flip (verification rider (g): no
        torn state under concurrent search — a reader sees either
        the old active row or the new active row, never neither and
        never both).

        Cross-root supersession is permitted: NO same-root/target
        enforcement exists here (design R12 — validity is creator
        judgment + overlapping tags, soft-warn never refuse).

        Args:
            snapshot: The successor row. ``status`` and
                ``supersedes_snapshot_id`` are stamped by this method.
            supersedes_snapshot_id: Id of the previous snapshot this
                row supersedes.
            embeddings: Optional :class:`SnapshotEmbedding` rows for
                the successor (STAY-ALONGSIDE semantics — the
                previous row's embeddings are NOT deleted; Q8-A).

        Returns:
            The persisted successor snapshot.

        Raises:
            ValueError: Unknown status (via
                :meth:`create_with_embeddings` guard — unreachable
                here since this method stamps ``active``).
        """
        snapshot.status = SNAPSHOT_STATUS_ACTIVE
        snapshot.supersedes_snapshot_id = supersedes_snapshot_id
        rows = list(embeddings or [])
        with Session(self.engine) as session:
            session.add(snapshot)
            for emb in rows:
                emb.snapshot_id = snapshot.id
                session.add(emb)
            # Same-transaction flip: previous row → superseded. A
            # missing previous row raises (caller surface: the
            # snapshot_create tool turns it into an error string —
            # superseding a nonexistent id is a caller bug, not a
            # soft-miss).
            prev = session.get(Snapshot, supersedes_snapshot_id)
            if prev is None:
                session.rollback()
                raise ValueError(
                    f"supersedes_snapshot_id {supersedes_snapshot_id!r} "
                    "does not exist — supersession refused"
                )
            prev.status = SNAPSHOT_STATUS_SUPERSEDED
            session.add(prev)
            session.commit()
            session.refresh(snapshot)
        return snapshot

    def set_status(self, snapshot_id: str, status: str) -> bool:
        """Set a snapshot's lifecycle status.

        Args:
            snapshot_id: Row id.
            status: One of the five writable states (guard raises
                ``ValueError`` otherwise).

        Returns:
            ``True`` if a row was updated, ``False`` if the id is
            unknown.
        """
        if status not in SNAPSHOT_STATUSES:
            raise ValueError(
                f"Unknown snapshot status {status!r}; "
                f"expected one of {sorted(SNAPSHOT_STATUSES)}"
            )
        with Session(self.engine) as session:
            row = session.get(Snapshot, snapshot_id)
            if row is None:
                return False
            row.status = status
            session.add(row)
            session.commit()
            return True

    # ── D3 boot sweep ─────────────────────────────────────────────────

    def mark_orphaned_running_interrupted(self) -> int:
        """Mark every ``running`` row ``interrupted`` (boot sweep).

        One idempotent startup query — same shape as
        ``JobRecoveryService.recover_on_startup`` scoped to ONE table
        (design §2.4). A second call matches zero rows. Lost:
        automatic retry. Kept: the agent sees the failure and can
        re-invoke.

        Returns:
            Number of rows flipped (0 on the happy no-op path).
        """
        with Session(self.engine) as session:
            result = session.exec(
                update(Snapshot)  # type: ignore[arg-type]
                .where(col(Snapshot.status) == SNAPSHOT_STATUS_RUNNING)
                .values(status=SNAPSHOT_STATUS_INTERRUPTED)
            )
            session.commit()
            flipped = result.rowcount or 0
        if flipped:
            logger.info(
                f"[Snapshot] boot sweep: marked {flipped} orphaned "
                "'running' snapshot row(s) 'interrupted'"
            )
        return flipped

    # ── Reads ─────────────────────────────────────────────────────────

    def get(self, snapshot_id: str) -> Optional[Snapshot]:
        """Fetch one snapshot by id, or ``None``."""
        with Session(self.engine) as session:
            return session.get(Snapshot, snapshot_id)

    def get_embeddings(self, snapshot_id: str) -> list[SnapshotEmbedding]:
        """Fetch the embedding rows for one snapshot (creation order)."""
        with Session(self.engine) as session:
            stmt = (
                select(SnapshotEmbedding)  # type: ignore[arg-type]
                .where(col(SnapshotEmbedding.snapshot_id) == snapshot_id)
                .order_by(col(SnapshotEmbedding.created_at))
            )
            return list(session.exec(stmt).all())

    def find_by_status(self, status: str) -> list[Snapshot]:
        """Fetch all rows in a given status (boot-sweep/ops reads)."""
        with Session(self.engine) as session:
            stmt = (
                select(Snapshot)  # type: ignore[arg-type]
                .where(col(Snapshot.status) == status)
                .order_by(col(Snapshot.created_at))
            )
            return list(session.exec(stmt).all())

    def list_active_by_project(
        self,
        project_id: str,
        limit: int = 50,
    ) -> list[Snapshot]:
        """Active-status candidates for project-scoped search (PR5).

        Args:
            project_id: Owning project (design D8 — search is
                project-scoped, PERMANENT stance).
            limit: Row cap (search LLM-selection is bounded to
                top-20 later; the SQL cap is the outer bound).

        Returns:
            Active snapshots, newest first.
        """
        with Session(self.engine) as session:
            stmt = (
                select(Snapshot)  # type: ignore[arg-type]
                .where(
                    col(Snapshot.project_id) == project_id,
                    col(Snapshot.status) == SNAPSHOT_STATUS_ACTIVE,
                )
                .order_by(col(Snapshot.created_at).desc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    def latest_for_target(
        self,
        target_instance_id: str,
        statuses: Sequence[str] = (SNAPSHOT_STATUS_ACTIVE,),
    ) -> Optional[Snapshot]:
        """Newest snapshot of one target in any of ``statuses``.

        R9 verdict support ("previous snapshot of this target
        exists") + the R6c near-duplicate default-skip read.

        Args:
            target_instance_id: The captured instance id.
            statuses: Status filter — active-only by default
                (superseded rows are history, not candidates).

        Returns:
            The newest matching row or ``None``.
        """
        with Session(self.engine) as session:
            stmt = (
                select(Snapshot)  # type: ignore[arg-type]
                .where(
                    col(Snapshot.target_instance_id) == target_instance_id,
                    col(Snapshot.status).in_(list(statuses)),
                )
                .order_by(col(Snapshot.created_at).desc())
                .limit(1)
            )
            return session.exec(stmt).first()

    def count_by_project(self, project_id: str) -> int:
        """Row count for a project (R16 monitoring groundwork)."""
        with Session(self.engine) as session:
            stmt = (
                select(func.count())  # type: ignore[call-overload]
                .select_from(Snapshot)
                .where(col(Snapshot.project_id) == project_id)
            )
            return int(session.exec(stmt).one())

    # ── R8 tag filter ─────────────────────────────────────────────────

    def filter_by_tags(
        self,
        candidates: Sequence[Snapshot],
        tags: Sequence[str],
        tag_mode: Literal["all", "any"] = "all",
    ) -> list[Snapshot]:
        """Filter candidate rows by R8 typed tags.

        * ``tag_mode='all'`` — every supplied tag must match
          (AND-semantics).
        * ``tag_mode='any'`` — at least one supplied tag must match
          (OR-semantics).

        PostgreSQL: the JSONB ``@>`` containment operator via the
        dialect-aware column type. SQLite: the JSON column is TEXT,
        so candidates are scanned in Python — the JSON-string scan
        is acceptable at v1 volumes (rider (f); GIN is PG-only).

        Empty ``tags`` matches everything (no filter).

        Args:
            candidates: Rows to filter (already project+status
                filtered by the caller).
            tags: Typed ``dim:value`` strings.
            tag_mode: ``'all'`` (default) or ``'any'``.

        Returns:
            The matching subset, original order preserved.

        Raises:
            ValueError: Unknown ``tag_mode`` (fail loud).
        """
        if tag_mode not in ("all", "any"):
            raise ValueError(
                f"Unknown tag_mode {tag_mode!r}; expected 'all' or 'any'"
            )
        wanted = [t for t in tags if t]
        if not wanted:
            return list(candidates)

        if self.engine.dialect.name == "postgresql" and candidates:
            # PG path: one containment query over the candidate ids.
            ids = [c.id for c in candidates]
            with Session(self.engine) as session:
                stmt = (
                    select(Snapshot)  # type: ignore[arg-type]
                    .where(col(Snapshot.id).in_(ids))
                )
                if tag_mode == "all":
                    # @> with the full wanted list = every tag present.
                    stmt = stmt.where(col(Snapshot.domain_tags).contains(wanted))
                else:
                    # OR-semantics: contain ANY single tag.
                    from sqlalchemy import or_

                    stmt = stmt.where(
                        or_(
                            *(
                                col(Snapshot.domain_tags).contains([tag])
                                for tag in wanted
                            )
                        )
                    )
                matched_ids = {
                    row.id for row in session.exec(stmt).all()
                }
            return [c for c in candidates if c.id in matched_ids]

        # SQLite / fallback: Python-side scan over the JSON arrays.
        def _matches(row: Snapshot) -> bool:
            have = set(row.domain_tags or [])
            if tag_mode == "all":
                return all(t in have for t in wanted)
            return any(t in have for t in wanted)

        return [c for c in candidates if _matches(c)]
