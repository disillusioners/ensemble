"""SQLModel-based repository for OpenCode session persistence.

Direct port of the SQLite ``sessions`` table in
``.inspiration-projects/opencode_skill_src/internal/daemon/registry.go``
(plus the operations described in
``opencode-skill-go-source-full-technical-analysis.md`` section 7).

Differences from the Go implementation:

- The Go binary uses a single ``sessions`` table for *all* project-sessions.
  We give ours a dedicated ``opencode_sessions`` table to avoid clashing
  with ensemble's other tables.
- We add an explicit ``ix_opencode_sessions_id`` index — the Go binary
  relies on a linear scan for ``FindByID``, but a unique-by-id index is
  cheap and lets ``FindByID`` use the index path in PostgreSQL too.
- We model ``LatestResponse`` and ``Questions`` as ``JSON`` columns
  (Postgres ``jsonb``) so the Python side stores/loads dicts and lists
  without manual serialization.

The repository owns the table creation flow via
``create_opencode_session_repository()`` in this module, which uses
``OpenCodeSessionRecord.__table__.create()`` (NOT ``SQLModel.metadata.create_all``)
to avoid polluting the dedicated engine with ensemble's other 22+ tables.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Index
from sqlalchemy.engine import Engine
from sqlalchemy.types import JSON
from sqlmodel import Field, Session, SQLModel, select

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SQLModel table
# ─────────────────────────────────────────────────────────────────────────────


class OpenCodeSessionRecord(SQLModel, table=True):
    """Persistent record for one OpenCode session.

    Mirrors the columns in ``registry.go`` lines 444-460:

        project          TEXT NOT NULL,
        session_name     TEXT NOT NULL,
        id               TEXT,
        working_dir       TEXT,
        last_agent       TEXT DEFAULT '',
        is_agent_locked  INTEGER DEFAULT 0,
        state            TEXT DEFAULT 'IDLE',
        latest_response  TEXT DEFAULT '',
        questions        TEXT DEFAULT '[]',
        last_activity    TEXT DEFAULT '',
        PRIMARY KEY (project, session_name)

    We add an index on ``id`` to make ``FindByID`` O(log N) — see
    migration ``20260606_000002_create_opencode_sessions_table.sql``.
    """

    __tablename__ = "opencode_sessions"
    # SQLModel doesn't render ``__table_args__``-defined indexes unless
    # we register them here. We declare the FK + index on ``id``.
    __table_args__ = (
        Index("ix_opencode_sessions_id", "id"),
    )

    project: str = Field(primary_key=True, max_length=255)
    session_name: str = Field(primary_key=True, max_length=255)
    id: str | None = Field(default=None, max_length=255)
    working_dir: str | None = Field(default=None, max_length=1024)
    last_agent: str = Field(default="", max_length=128)
    is_agent_locked: bool = Field(default=False)
    state: str = Field(default="IDLE", max_length=32)
    # JSON columns (Postgres jsonb / SQLite text-with-JSON). SQLAlchemy
    # serializes on write and deserializes on read, so the Python
    # attributes are typed as the deserialized values.
    latest_response: Any | None = Field(
        default=None,
        sa_column=Column("latest_response", JSON),
    )
    questions: list[Any] = Field(
        default_factory=list,
        sa_column=Column("questions", JSON),
    )
    last_activity: str = Field(default="", max_length=64)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for IPC. JSON columns are already deserialized."""
        return {
            "project": self.project,
            "session_name": self.session_name,
            "id": self.id,
            "working_dir": self.working_dir,
            "last_agent": self.last_agent,
            "is_agent_locked": self.is_agent_locked,
            "state": self.state,
            "latest_response": self.latest_response,
            "questions": self.questions or [],
            "last_activity": self.last_activity,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _now_rfc3339() -> str:
    """Return current UTC time in RFC3339 — matches ``time.RFC3339`` in Go.

    Used for ``last_activity`` columns. The Go binary writes
    ``sm.lastActivity.Format(time.RFC3339)`` (manager.go:125).
    """
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Repository
# ─────────────────────────────────────────────────────────────────────────────


class OpenCodeSessionRepository:
    """CRUD for the ``opencode_sessions`` table.

    Method names match the Go ``Registry`` type for line-by-line parity.
    Each method takes/returns plain Python objects (not SQLModel rows)
    so callers don't have to worry about session lifetimes.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ── CREATE ─────────────────────────────────────────────────────────────

    def create(
        self,
        project: str,
        session_name: str,
        session_id: str,
        working_dir: str,
    ) -> None:
        """Insert a new row. Mirrors ``Registry.Create`` (registry.go).

        The Go version returns an error on a duplicate primary key. We
        rely on the SQLAlchemy ``IntegrityError`` for the same effect.
        """
        with Session(self.engine) as session:
            record = OpenCodeSessionRecord(
                project=project,
                session_name=session_name,
                id=session_id,
                working_dir=working_dir,
                last_activity=_now_rfc3339(),
            )
            session.add(record)
            session.commit()

    # ── READ ───────────────────────────────────────────────────────────────

    def get(self, project: str, session_name: str) -> dict[str, Any] | None:
        """Fetch a single row by composite PK. Mirrors ``Registry.Get``."""
        with Session(self.engine) as session:
            record = session.get(OpenCodeSessionRecord, (project, session_name))
            return record.to_dict() if record else None

    def list(self) -> list[dict[str, Any]]:
        """Return all rows, ordered by project then session_name.

        Mirrors ``Registry.List`` (registry.go). Used for crash recovery
        on daemon startup.
        """
        with Session(self.engine) as session:
            statement = select(OpenCodeSessionRecord).order_by(
                OpenCodeSessionRecord.project,
                OpenCodeSessionRecord.session_name,
            )
            return [r.to_dict() for r in session.exec(statement).all()]

    def find_by_id(self, session_id: str) -> dict[str, Any] | None:
        """Look up the ``(project, session_name)`` owning a given session id.

        Mirrors ``Registry.FindByID`` (registry.go). Uses
        ``ix_opencode_sessions_id`` for the lookup. Returns ``None`` when
        no match — callers in the Go version distinguish "not found"
        from "found but in a wrong state".
        """
        with Session(self.engine) as session:
            statement = select(OpenCodeSessionRecord).where(
                OpenCodeSessionRecord.id == session_id,
            )
            record = session.exec(statement).first()
            return record.to_dict() if record else None

    # ── UPDATE ─────────────────────────────────────────────────────────────

    def update_agent_state(
        self,
        project: str,
        session_name: str,
        last_agent: str,
        is_locked: bool,
    ) -> None:
        """Update ``last_agent`` + ``is_agent_locked`` atomically.

        Mirrors ``Registry.UpdateAgentState`` (registry.go). This is
        called by ``/start-work`` in the server handler to lock the
        agent to ``"atlas"`` (constants.START_WORK_AGENT).
        """
        with Session(self.engine) as session:
            record = session.get(OpenCodeSessionRecord, (project, session_name))
            if record is None:
                raise KeyError(f"opencode session {project}/{session_name} not found")
            record.last_agent = last_agent
            record.is_agent_locked = is_locked
            session.add(record)
            session.commit()

    def update_state(self, project: str, session_name: str, state: str) -> None:
        """Update only the ``state`` column. Mirrors ``Registry.UpdateState``."""
        with Session(self.engine) as session:
            record = session.get(OpenCodeSessionRecord, (project, session_name))
            if record is None:
                raise KeyError(f"opencode session {project}/{session_name} not found")
            record.state = state
            session.add(record)
            session.commit()

    def update_last_activity(
        self,
        project: str,
        session_name: str,
        timestamp: str,
    ) -> None:
        """Stamp ``last_activity`` with the supplied RFC3339 timestamp.

        The Go binary documents this in the analysis (section 7 of
        ``opencode-skill-go-source-full-technical-analysis.md``) but the
        reference implementation did not actually call it from a manager
        path. We expose it as a first-class repository method so
        heartbeat/recovery code in ensemble can stamp activity without
        a full state write.
        """
        with Session(self.engine) as session:
            record = session.get(OpenCodeSessionRecord, (project, session_name))
            if record is None:
                raise KeyError(f"opencode session {project}/{session_name} not found")
            record.last_activity = timestamp
            session.add(record)
            session.commit()

    def update_session_data(
        self,
        project: str,
        session_name: str,
        last_agent: str,
        is_agent_locked: bool,
        state: str,
        latest_response: Any,
        questions: list[Any],
        last_activity: str,
    ) -> None:
        """Bulk update all dynamic columns. Mirrors ``Registry.UpdateSessionData``.

        Called by the persistence callback registered in
        ``setupStatePersistence`` (server.go:417-428) whenever a
        manager emits an ``OnStateChange`` notification.

        The JSON columns are passed through directly — the SQLAlchemy
        ``JSON`` type handles serialization.
        """
        with Session(self.engine) as session:
            record = session.get(OpenCodeSessionRecord, (project, session_name))
            if record is None:
                raise KeyError(f"opencode session {project}/{session_name} not found")
            record.last_agent = last_agent
            record.is_agent_locked = is_agent_locked
            record.state = state
            record.latest_response = latest_response
            record.questions = questions or []
            record.last_activity = last_activity
            session.add(record)
            session.commit()

    # ── DELETE ─────────────────────────────────────────────────────────────

    def delete(self, project: str, session_name: str) -> None:
        """Delete a row. Mirrors ``Registry.Delete`` (registry.go).

        Raises ``KeyError`` if the row was already gone — matches the
        Go ``ErrNotFound`` semantics used by ``INIT_SESSION`` to skip
        the cleanup step when no previous session exists.
        """
        with Session(self.engine) as session:
            record = session.get(OpenCodeSessionRecord, (project, session_name))
            if record is None:
                raise KeyError(f"opencode session {project}/{session_name} not found")
            session.delete(record)
            session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Factory glue
# ─────────────────────────────────────────────────────────────────────────────


def create_opencode_session_repository(engine: Engine) -> OpenCodeSessionRepository:
    """Create a repository and ensure the table + index exist.

    Uses ``OpenCodeSessionRecord.__table__.create()`` instead of
    ``SQLModel.metadata.create_all()`` because the opencode engine is
    **dedicated** — it must NOT contain ensemble's other 22+ tables
    (instances, projects, job_queues, message_queue, tasks, events, etc.).
    The global ``SQLModel.metadata`` registry includes all of them, so
    ``create_all()`` would pollute the dedicated DB.

    This creates only the ``opencode_sessions`` table and its
    ``ix_opencode_sessions_id`` index — nothing else.
    """
    OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)
    return OpenCodeSessionRepository(engine)
