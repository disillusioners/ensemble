"""Event repository for SSE event persistence."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, delete as sql_delete
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from .models import Event, EventKind


class EventRepository:
    """Repository for Event CRUD operations with cursor-based delivery."""

    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def create_event(
        self,
        instance_id: str,
        kind: str,
        data: dict[str, Any] | None = None,
    ) -> Event:
        """Create a new event."""
        event = Event(
            instance_id=instance_id,
            kind=kind,
            data=json.dumps(data) if data is not None else None,
            created_at=datetime.now(timezone.utc),
        )

        with Session(self.engine) as session:
            session.add(event)
            session.commit()
            session.refresh(event)

        return event

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, event_id: int) -> Event | None:
        """Get an event by ID."""
        with Session(self.engine) as session:
            return session.get(Event, event_id)

    def get_by_instance(
        self,
        instance_id: str,
        limit: int = 100,
    ) -> list[Event]:
        """Get all events for an instance."""
        with Session(self.engine) as session:
            stmt = (
                select(Event)
                .where(Event.instance_id == instance_id)
                .order_by(Event.created_at.asc())
                .limit(limit)
            )
            return list(session.exec(stmt))

    def get_events_since(
        self,
        instance_id: str,
        after_id: int | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Get events after cursor position (cursor-based delivery).

        Args:
            instance_id: The instance to get events for.
            after_id: Return events with id > after_id (cursor position).
                     None means start from beginning.
            limit: Maximum number of events to return.

        Returns:
            List of events after the cursor position.
        """
        with Session(self.engine) as session:
            if after_id is not None:
                # Cursor-based: get events with id > after_id
                stmt = (
                    select(Event)
                    .where(
                        Event.instance_id == instance_id,
                        Event.id > after_id,
                    )
                    .order_by(Event.id.asc())
                    .limit(limit)
                )
            else:
                # No cursor: get latest events (for SSE initial connection)
                stmt = (
                    select(Event)
                    .where(Event.instance_id == instance_id)
                    .order_by(Event.created_at.desc())
                    .limit(limit)
                )

            events = list(session.exec(stmt))

            # For initial connection (no cursor), reverse to chronological order
            if after_id is None:
                events.reverse()

            return events

    # --------------------------------------------------------
    # QUERY
    # --------------------------------------------------------

    def get_latest_event_id(self, instance_id: str) -> int | None:
        """Get the ID of the latest event for an instance.

        Useful for determining cursor position for new connections.
        """
        with Session(self.engine) as session:
            stmt = select(func.max(Event.id)).where(
                Event.instance_id == instance_id
            )
            result = session.exec(stmt).one()
            return result

    def count_by_instance(self, instance_id: str) -> int:
        """Count events for an instance."""
        with Session(self.engine) as session:
            stmt = select(func.count()).select_from(Event).where(
                Event.instance_id == instance_id
            )
            return session.exec(stmt).one()

    # --------------------------------------------------------
    # CLEANUP
    # --------------------------------------------------------

    def cleanup_old(self, max_age_hours: int = 24) -> int:
        """Delete events older than N hours.

        Args:
            max_age_hours: Maximum age of events to keep.

        Returns:
            Number of events deleted.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

        with Session(self.engine) as session:
            stmt = sql_delete(Event).where(Event.created_at < cutoff)
            result = session.exec(stmt)
            session.commit()
            return result.rowcount

    def delete_by_instance(self, instance_id: str) -> int:
        """Delete all events for an instance."""
        with Session(self.engine) as session:
            stmt = sql_delete(Event).where(Event.instance_id == instance_id)
            result = session.exec(stmt)
            session.commit()
            return result.rowcount
