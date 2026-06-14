"""Database connection registry models.

This module contains the SQLModel table definition for the
``DbConnectionConfig`` entity used by the Database Tool Category
feature (Phase 1: Connection Registry Layer).

The repository stores credentials as opaque encrypted strings. The
encryption/decryption boundary lives at the tool layer (matching the
``source_configs`` pattern), not in the repository.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlmodel import SQLModel, Field


class DbConnectionConfig(SQLModel, table=True):
    """SQLModel table for named database connection configurations.

    Stores connection metadata (host, port, credentials, etc.) for
    named database connections that agents can reference by
    ``connection_name``. Credentials are stored as opaque encrypted
    strings — the repository never decrypts them.
    """

    __tablename__ = "db_connections"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
    )
    connection_name: str = Field(unique=True, index=True, max_length=128)
    db_type: str = Field(max_length=32)
    host: str = Field(max_length=256)
    port: int | None = Field(default=None)
    database: str | None = Field(default=None)
    username: str | None = Field(default=None)
    # Opaque encrypted string. Repository never decrypts.
    credentials: str | None = Field(default=None)
    ssl_mode: str = Field(default="prefer", max_length=32)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_public_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization, omitting credentials.

        The ``credentials`` field is replaced with a boolean indicator
        so callers can tell whether a password is set without seeing
        the secret itself.

        Returns:
            Dictionary representation of the connection config that
            never contains the credentials value.
        """
        return {
            "id": self.id,
            "connection_name": self.connection_name,
            "db_type": self.db_type,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "has_password": self.credentials is not None,
            "ssl_mode": self.ssl_mode,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def update_timestamp(self) -> None:
        """Bump ``updated_at`` to the current UTC time.

        Callers should invoke this before committing a mutation. The
        pattern follows ``SQLModelSourceRepository.update_source_config``
        where the repository assigns ``updated_at`` itself; this helper
        is provided for tests and external callers that mutate the
        object directly.
        """
        self.updated_at = datetime.now(timezone.utc).isoformat()
