"""Event repository for SSE event persistence."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

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
        message_id: str | None = None,
    ) -> Event:
        """Create a new event."""
        event = Event(
            instance_id=instance_id,
            message_id=message_id,
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
                .order_by(Event.id.asc())
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
                # No cursor: get events in chronological order (id ascending)
                stmt = (
                    select(Event)
                    .where(Event.instance_id == instance_id)
                    .order_by(Event.id.asc())
                    .limit(limit)
                )

            events = list(session.exec(stmt))

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

    def count_kind_for_instance_matching(
        self,
        instance_id: str,
        kind: str,
        data_like: str,
    ) -> int:
        """Count events of ``kind`` for ``instance_id`` whose serialized
        ``data`` contains ``data_like``.

        Mid-flight QA channel (design §4.3): derives the wedge-guard
        ``emission_index`` from PERSISTED event history — count prior
        ``stuck_awaiting_answer`` rows whose payload references the
        question_pack_id. This survives ``StaleTaskRecovery``'s
        retry-child minting (which drops unknown Task columns) and
        daemon restarts, unlike any in-RAM counter.

        The LIKE probe on the JSON-serialized ``data`` TEXT column is
        intentionally simple: the ``question_pack_id`` is a UUID4 —
        a substring match on the serialized payload cannot collide
        with unrelated fields. Works identically on SQLite and
        PostgreSQL (``data`` is TEXT on both).
        """
        with Session(self.engine) as session:
            stmt = (
                select(func.count())
                .select_from(Event)
                .where(Event.instance_id == instance_id)
                .where(Event.kind == kind)
                .where(Event.data.like(f"%{data_like}%"))
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
