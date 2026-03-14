"""SQLModel-based Session Repository implementation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete as sql_delete, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session as SQLModelSession, select, col

from .models import Session, SessionHierarchy, SessionStatus


def get_agent_name(agent_dir: str) -> str:
    """Derive agent name from agent directory path.
    
    Args:
        agent_dir: Path to the agent directory.
        
    Returns:
        Agent name in Title Case (e.g., "Coder", "Designer").
    """
    return Path(agent_dir).name.title()


class SQLModelSessionRepository:
    """SQLModel-based Session repository with hierarchy support."""
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # --------------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------------

    def _load_children(self, db_session: Session, session_id: str) -> list[str]:
        """Load child session IDs from hierarchy table."""
        links = db_session.exec(
            select(SessionHierarchy).where(SessionHierarchy.parent_id == session_id)
        ).all()
        return [link.child_id for link in links]

    def _enrich_session(self, db_session: Session, session: Session | None) -> Session | None:
        """Load children onto session."""
        if session is None:
            return None
        session.children = self._load_children(db_session, session.session_id)
        return session

    def _enrich_sessions(self, db_session: Session, sessions: list[Session]) -> list[Session]:
        """Load children for multiple sessions."""
        for s in sessions:
            s.children = self._load_children(db_session, s.session_id)
        return sessions

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def create(
        self,
        session_id: str,
        agent_dir: str,
        parent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "idle",
    ) -> Session:
        """Create a new session.
        
        Args:
            session_id: Unique session identifier.
            agent_dir: Path to the agent directory.
            parent_id: Optional parent session ID for hierarchical sessions.
            metadata: Optional metadata dictionary.
            status: Session status (default: "idle").
            
        Returns:
            Created Session object.
        """
        with SQLModelSession(self.engine) as db_session:
            agent_name = get_agent_name(agent_dir)
            now = datetime.utcnow().isoformat()
            
            session = Session(
                session_id=session_id,
                agent_dir=agent_dir,
                agent_name=agent_name,
                parent_id=parent_id,
                status=status,
                session_metadata=metadata or {},
                created_at=now,
                updated_at=now,
            )

            db_session.add(session)
            
            # Add to hierarchy if parent_id is provided
            if parent_id is not None:
                hierarchy_link = SessionHierarchy(
                    parent_id=parent_id,
                    child_id=session_id,
                    created_at=now,
                )
                db_session.add(hierarchy_link)
            
            db_session.commit()
            db_session.refresh(session)

            return self._enrich_session(db_session, session)

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        with SQLModelSession(self.engine) as db_session:
            session = db_session.get(Session, session_id)
            return self._enrich_session(db_session, session)

    def get_by_agent_dir(self, agent_dir: str) -> list[Session]:
        """Get all sessions for a given agent directory."""
        with SQLModelSession(self.engine) as db_session:
            stmt = select(Session).where(Session.agent_dir == agent_dir)
            sessions = list(db_session.exec(stmt))
            return self._enrich_sessions(db_session, sessions)

    def get_children(self, session_id: str) -> list[Session]:
        """Get all child sessions of a session."""
        with SQLModelSession(self.engine) as db_session:
            stmt = select(Session).where(Session.parent_id == session_id)
            sessions = list(db_session.exec(stmt))
            return self._enrich_sessions(db_session, sessions)

    def get_parent(self, session_id: str) -> Session | None:
        """Get the parent session of a session."""
        with SQLModelSession(self.engine) as db_session:
            session = db_session.get(Session, session_id)
            if session is None or session.parent_id is None:
                return None
        return self.get(session.parent_id)

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    def list(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Session], int]:
        """List sessions with optional status filter and pagination.
        
        Args:
            status: Optional status filter.
            limit: Maximum number of sessions to return.
            offset: Number of sessions to skip.
            
        Returns:
            Tuple of (list of sessions, total count).
        """
        with SQLModelSession(self.engine) as db_session:
            # Get total count using database-level counting
            count_stmt = select(func.count()).select_from(Session)
            if status:
                count_stmt = count_stmt.where(Session.status == status)
            total = db_session.exec(count_stmt).one()

            # Get paginated sessions
            stmt = select(Session)
            if status:
                stmt = stmt.where(Session.status == status)
            
            stmt = stmt.order_by(col(Session.created_at).desc()).offset(offset).limit(limit)
            sessions = list(db_session.exec(stmt))
            
            return self._enrich_sessions(db_session, sessions), total

    def list_by_parent(self, parent_id: str) -> list[Session]:
        """List all child sessions of a parent."""
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(Session)
                .join(SessionHierarchy, SessionHierarchy.child_id == Session.session_id)
                .where(SessionHierarchy.parent_id == parent_id)
            )
            sessions = list(db_session.exec(stmt))
            return self._enrich_sessions(db_session, sessions)

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def update(self, session_id: str, **updates) -> Session | None:
        """Update a session's fields."""
        with SQLModelSession(self.engine) as db_session:
            session = db_session.get(Session, session_id)
            if session is None:
                return None

            if 'status' in updates and not SessionStatus.is_valid(updates['status']):
                raise ValueError(f"Invalid status: {updates['status']}")

            for key, value in updates.items():
                if hasattr(session, key):
                    setattr(session, key, value)

            session.updated_at = datetime.utcnow().isoformat()
            db_session.commit()
            db_session.refresh(session)

            return self._enrich_session(db_session, session)

    def update_status(self, session_id: str, status: str) -> Session | None:
        """Update session status."""
        return self.update(session_id, status=status)

    def update_title(self, session_id: str, title: str) -> Session | None:
        """Update session title in session_metadata."""
        with SQLModelSession(self.engine) as db_session:
            session = db_session.get(Session, session_id)
            if session is None:
                return None

            session.session_metadata["title"] = title
            flag_modified(session, "session_metadata")
            session.updated_at = datetime.utcnow().isoformat()
            db_session.commit()
            db_session.refresh(session)

            return self._enrich_session(db_session, session)

    def set_metadata(self, session_id: str, key: str, value: Any) -> Session | None:
        """Set a session_metadata key-value pair."""
        with SQLModelSession(self.engine) as db_session:
            session = db_session.get(Session, session_id)
            if session is None:
                return None

            session.session_metadata[key] = value
            flag_modified(session, "session_metadata")
            session.updated_at = datetime.utcnow().isoformat()
            db_session.commit()
            db_session.refresh(session)

            return self._enrich_session(db_session, session)

    def delete_metadata(self, session_id: str, key: str) -> Session | None:
        """Delete a session_metadata key."""
        with SQLModelSession(self.engine) as db_session:
            session = db_session.get(Session, session_id)
            if session is None:
                return None

            session.session_metadata.pop(key, None)
            flag_modified(session, "session_metadata")
            session.updated_at = datetime.utcnow().isoformat()
            db_session.commit()
            db_session.refresh(session)

            return self._enrich_session(db_session, session)

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, session_id: str) -> dict[str, Any]:
        """Delete a session and its hierarchy references."""
        with SQLModelSession(self.engine) as db_session:
            session = db_session.get(Session, session_id)
            if session is None:
                return {"deleted": False, "session_id": session_id, "error": "Not found"}

            # Delete from hierarchy where session is parent
            db_session.exec(
                sql_delete(SessionHierarchy).where(SessionHierarchy.parent_id == session_id)
            )

            # Delete from hierarchy where session is child
            db_session.exec(
                sql_delete(SessionHierarchy).where(SessionHierarchy.child_id == session_id)
            )

            # Delete session
            db_session.delete(session)
            db_session.commit()

            return {
                "deleted": True,
                "session_id": session_id,
                "agent_dir": session.agent_dir,
            }

    def delete_all(self) -> int:
        """Delete all sessions from the database.
        
        Returns:
            Number of sessions deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            # Count before deletion
            total = len(list(db_session.exec(select(Session))))

            # Delete all hierarchy links
            db_session.exec(sql_delete(SessionHierarchy))
            
            # Delete all sessions
            db_session.exec(sql_delete(Session))
            
            db_session.commit()

            return total
