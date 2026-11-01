"""Repositories for the project skill source tables (Phase 1 task 3).

Sync repositories over a SQLAlchemy ``Engine`` (house style — the
service wraps every call in ``asyncio.to_thread``; nothing here runs on
the event loop). Failure semantics: expected failures RAISE — the
service owns the fail-loud vs fail-soft policy per call site (Q7:
index mutation never fails a search; D-Q7: load errors are explicit
envelopes).

A4 lifecycle rules implemented here (acceptance criteria, phase1 task 7):

* **(iv) delete-with-row** — :meth:`ProjectSkillFileRepository.delete_skill`
  deletes the skill's embedding rows in the SAME session/transaction.
* **(vi) delete-AFTER-success** —
  :meth:`ProjectSkillEmbeddingRepository.replace_for_skill` inserts the
  freshly generated rows and drops the old ones in ONE transaction;
  generation happens entirely BEFORE the call, so a generation failure
  never touches existing rows (stale beats permanently-absent).
* **(v) gc** — :meth:`ProjectSkillEmbeddingRepository.gc_orphans`
  removes embedding rows whose ``skill_id`` is not present in
  ``project_skill_files`` (covers project deletion / root changes /
  project_id changes — the opaque hash makes per-project LIKE cleanup
  impossible).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import Engine, delete, desc, text
from sqlmodel import Session, select

from .models import (
    ProjectSkillEmbedding,
    ProjectSkillFile,
    ProjectSkillUsageRecord,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectSkillFileRepository:
    """CRUD for ``project_skill_files`` (derived metadata only)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def upsert(self, row: ProjectSkillFile) -> ProjectSkillFile:
        """Insert-or-update one index row (natural key = PK pseudo-id)."""
        with Session(self.engine) as session:
            existing = session.get(ProjectSkillFile, row.id)
            if existing is None:
                session.add(row)
            else:
                for key, value in row.to_dict().items():
                    if key == "id":
                        continue
                    setattr(existing, key, value)
                existing.updated_at = _now_iso()
                session.add(existing)
            session.commit()
            session.refresh(existing if existing is not None else row)
            return existing if existing is not None else row

    def sync_scan(
        self,
        root_path: str,
        project_id: str,
        rows: Sequence[ProjectSkillFile],
    ) -> tuple[int, int]:
        """Wholesale per-root snapshot sync (A4-i: wholesale swap).

        Upserts every scanned row and DELETES rows for this
        ``(root_path, project_id)`` pair absent from the scan — vanished
        skills drop their index rows, and via the same transaction their
        embedding rows (A4-iv delete-with-row).

        Returns ``(upserted, deleted)`` counts.
        """
        scanned_ids = [row.id for row in rows]
        with Session(self.engine) as session:
            for row in rows:
                existing = session.get(ProjectSkillFile, row.id)
                if existing is None:
                    session.add(row)
                else:
                    for key, value in row.to_dict().items():
                        if key == "id":
                            continue
                        setattr(existing, key, value)
                    existing.updated_at = _now_iso()
                    session.add(existing)
            stale = list(
                session.exec(
                    select(ProjectSkillFile).where(
                        ProjectSkillFile.root_path == root_path,
                        ProjectSkillFile.project_id == project_id,
                    )
                )
            )
            deleted = 0
            for stale_row in stale:
                if stale_row.id in scanned_ids:
                    continue
                # A4(iv) delete-with-row: embeddings die with the index row,
                # same transaction.
                session.exec(
                    delete(ProjectSkillEmbedding).where(
                        ProjectSkillEmbedding.skill_id == stale_row.id
                    )
                )
                session.delete(stale_row)
                deleted += 1
            session.commit()
            return len(rows), deleted

    def get(self, pseudo_id: str) -> Optional[ProjectSkillFile]:
        with Session(self.engine) as session:
            return session.get(ProjectSkillFile, pseudo_id)

    def list_by_project(self, project_id: str) -> list[ProjectSkillFile]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(ProjectSkillFile).where(
                        ProjectSkillFile.project_id == project_id
                    )
                )
            )

    def list_by_root(self, root_path: str) -> list[ProjectSkillFile]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(ProjectSkillFile).where(
                        ProjectSkillFile.root_path == root_path
                    )
                )
            )

    def set_embedding_state(
        self,
        pseudo_id: str,
        embedded_at: Optional[str],
        last_error: Optional[str] = None,
    ) -> bool:
        """Update ``embedded_at`` / ``last_error`` after a refresh attempt."""
        with Session(self.engine) as session:
            row = session.get(ProjectSkillFile, pseudo_id)
            if row is None:
                return False
            row.embedded_at = embedded_at
            row.last_error = last_error
            row.updated_at = _now_iso()
            session.add(row)
            session.commit()
            return True

    def delete_skill(self, pseudo_id: str) -> bool:
        """A4(iv) delete-with-row (standalone form)."""
        with Session(self.engine) as session:
            row = session.get(ProjectSkillFile, pseudo_id)
            if row is None:
                return False
            session.exec(
                delete(ProjectSkillEmbedding).where(
                    ProjectSkillEmbedding.skill_id == pseudo_id
                )
            )
            session.delete(row)
            session.commit()
            return True

    def purge_orphan_projects(self) -> int:
        """A17 one-statement orphan purge (Phase 4).

        Deletes every ``project_skill_files`` row whose ``project_id``
        no longer exists in ``projects`` — a deleted project must not
        leave durable index rows behind — and (A4-iv, same statement's
        transaction) the embedding rows riding those ids. ``projects``
        is the daemon's canonical project table on the same engine, so
        the NOT EXISTS subquery is the single source of truth for
        "project still alive".

        Returns the number of index rows removed (embedding removals
        ride along and are not counted separately).
        """
        with Session(self.engine) as session:
            result = session.exec(
                delete(ProjectSkillFile).where(
                    text(
                        "NOT EXISTS ("
                        "SELECT 1 FROM projects p "
                        "WHERE p.project_id = project_skill_files.project_id"
                        ")"
                    )
                )
            )
            removed = int(getattr(result, "rowcount", 0) or 0)
            if removed:
                session.exec(
                    delete(ProjectSkillEmbedding).where(
                        text(
                            "NOT EXISTS ("
                            "SELECT 1 FROM project_skill_files f "
                            "WHERE f.id = project_skill_embeddings.skill_id"
                            ")"
                        )
                    )
                )
            session.commit()
            return removed


class ProjectSkillEmbeddingRepository:
    """CRUD for ``project_skill_embeddings`` (A1 dedicated table, no FK)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def insert_many(
        self, skill_id: str, vectors: Sequence[tuple[str, list[float]]]
    ) -> int:
        """Insert ``(trigger_query, embedding)`` rows for one pseudo-id."""
        if not vectors:
            return 0
        with Session(self.engine) as session:
            for query, vector in vectors:
                session.add(
                    ProjectSkillEmbedding(
                        skill_id=skill_id,
                        trigger_query=query,
                        embedding=list(vector),
                    )
                )
            session.commit()
            return len(vectors)

    def replace_for_skill(
        self, skill_id: str, vectors: Sequence[tuple[str, list[float]]]
    ) -> tuple[int, int]:
        """A4(vi) delete-AFTER-success: new rows in, old rows out, ONE txn.

        Callers generate ALL replacement vectors BEFORE calling this — a
        generation failure never reaches here, so existing (stale) rows
        survive. Stale beats permanently-absent (the digest-stable access
        path never retries a vanished vector).
        """
        with Session(self.engine) as session:
            old = list(
                session.exec(
                    select(ProjectSkillEmbedding).where(
                        ProjectSkillEmbedding.skill_id == skill_id
                    )
                )
            )
            removed = len(old)
            for row in old:
                session.delete(row)
            for query, vector in vectors:
                session.add(
                    ProjectSkillEmbedding(
                        skill_id=skill_id,
                        trigger_query=query,
                        embedding=list(vector),
                    )
                )
            session.commit()
            return len(vectors), removed

    def list_by_skill(self, skill_id: str) -> list[ProjectSkillEmbedding]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(ProjectSkillEmbedding).where(
                        ProjectSkillEmbedding.skill_id == skill_id
                    )
                )
            )

    def count_by_skill(self, skill_id: str) -> int:
        with Session(self.engine) as session:
            return len(
                list(
                    session.exec(
                        select(ProjectSkillEmbedding).where(
                            ProjectSkillEmbedding.skill_id == skill_id
                        )
                    )
                )
            )

    def delete_by_skill(self, skill_id: str) -> int:
        with Session(self.engine) as session:
            result = session.exec(
                delete(ProjectSkillEmbedding).where(
                    ProjectSkillEmbedding.skill_id == skill_id
                )
            )
            session.commit()
            return int(result.rowcount or 0)

    def gc_orphans(self) -> int:
        """A4(v): drop embedding rows whose ``skill_id`` ∉ project_skill_files."""
        with Session(self.engine) as session:
            result = session.exec(
                text(
                    "DELETE FROM project_skill_embeddings "
                    "WHERE skill_id NOT IN (SELECT id FROM project_skill_files)"
                )
            )
            session.commit()
            return int(result.rowcount or 0)


class ProjectSkillUsageRecordRepository:
    """CRUD for ``project_skill_usage_records`` (D2 companion, no FK).

    Phase-1 surface = the primitives the Phase-3 ``skill_feedback``
    acceptance needs (insert / latest-for-(skill,instance) /
    update_completion / update_feedback) — mirroring the
    ``SkillUsageRepository`` method names where sensible so Phase 3
    reads naturally against both repos.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def insert(self, row: ProjectSkillUsageRecord) -> ProjectSkillUsageRecord:
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get_latest_for_skill_instance(
        self, skill_id: str, instance_id: str
    ) -> Optional[ProjectSkillUsageRecord]:
        with Session(self.engine) as session:
            rows = list(
                session.exec(
                    select(ProjectSkillUsageRecord)
                    .where(
                        ProjectSkillUsageRecord.skill_id == skill_id,
                        ProjectSkillUsageRecord.instance_id == instance_id,
                    )
                    .order_by(desc(ProjectSkillUsageRecord.created_at))
                )
            )
            return rows[0] if rows else None

    def list_by_skill(self, skill_id: str) -> list[ProjectSkillUsageRecord]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(ProjectSkillUsageRecord).where(
                        ProjectSkillUsageRecord.skill_id == skill_id
                    )
                )
            )

    def update_completion(
        self,
        record_id: str,
        task_succeeded: bool,
        iterations: int,
        duration_seconds: int,
        task_message: Optional[str] = None,
    ) -> Optional[ProjectSkillUsageRecord]:
        with Session(self.engine) as session:
            row = session.get(ProjectSkillUsageRecord, record_id)
            if row is None:
                return None
            row.task_succeeded = task_succeeded
            row.iterations = iterations
            row.duration_seconds = duration_seconds
            if task_message is not None:
                row.task_message = task_message
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def update_feedback(
        self,
        record_id: str,
        feedback_applied: Optional[bool],
        feedback_note: Optional[str] = None,
    ) -> Optional[ProjectSkillUsageRecord]:
        with Session(self.engine) as session:
            row = session.get(ProjectSkillUsageRecord, record_id)
            if row is None:
                return None
            row.feedback_applied = feedback_applied
            if feedback_note is not None:
                row.feedback_note = feedback_note
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def delete_by_skill(self, skill_id: str) -> int:
        """Compensating sweep companion (used alongside the A4 gc when the
        owning lane retires a pseudo-id)."""
        with Session(self.engine) as session:
            result = session.exec(
                delete(ProjectSkillUsageRecord).where(
                    ProjectSkillUsageRecord.skill_id == skill_id
                )
            )
            session.commit()
            return int(result.rowcount or 0)
