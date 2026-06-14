"""SQLModel-based Database Connection Registry repository.

The repository is the persistence layer for the Database Tool
Category's Connection Registry (Phase 1). It is intentionally
narrow: CRUD on ``db_connections`` plus helpers for fetching
opaque credentials and rendering public (no-secrets) views.

The repository never encrypts or decrypts credentials — it stores
and returns opaque strings as given. The tool layer is responsible
for any cryptographic transformation. This mirrors the
``source_configs`` repository pattern.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select, col

from .models import DbConnectionConfig


logger = logging.getLogger(__name__)


class DbConnectionRepository:
    """SQLModel-based repository for named database connection configs.

    The repository takes a SQLAlchemy ``Engine`` only. It does NOT
    receive a credential manager — credentials are treated as opaque
    strings on the way in and on the way out.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy engine bound to a SQLite or PostgreSQL
                database. The same engine should be shared across all
                repositories to avoid lock contention.
        """
        self.engine = engine

    # ==================== CRUD ====================

    def create(
        self,
        connection_name: str,
        db_type: str,
        host: str,
        port: int | None = None,
        database: str | None = None,
        username: str | None = None,
        credentials: str | None = None,
        ssl_mode: str = "prefer",
    ) -> DbConnectionConfig:
        """Create a new database connection configuration.

        Args:
            connection_name: Unique human-readable name for the
                connection (e.g. ``"analytics_warehouse"``).
            db_type: Database driver type identifier
                (e.g. ``"postgres"``).
            host: Database host.
            port: Optional database port.
            database: Optional database/schema name.
            username: Optional database username.
            credentials: Opaque encrypted credentials string. Stored
                verbatim — the repository does not encrypt or decrypt.
            ssl_mode: SSL mode string. Defaults to ``"prefer"``.

        Returns:
            The newly created ``DbConnectionConfig`` instance.
        """
        with Session(self.engine) as session:
            now = datetime.now(timezone.utc).isoformat()
            config = DbConnectionConfig(
                connection_name=connection_name,
                db_type=db_type,
                host=host,
                port=port,
                database=database,
                username=username,
                credentials=credentials,
                ssl_mode=ssl_mode,
                created_at=now,
                updated_at=now,
            )

            session.add(config)
            session.commit()
            session.refresh(config)

            logger.info(
                f"Created db connection: name={connection_name}, "
                f"db_type={db_type}, host={host}"
            )
            return config

    def get_by_name(self, connection_name: str) -> DbConnectionConfig | None:
        """Get a connection configuration by its unique name.

        Args:
            connection_name: The connection's unique name.

        Returns:
            The ``DbConnectionConfig`` instance, or ``None`` if no
            connection with that name exists.
        """
        with Session(self.engine) as session:
            stmt = select(DbConnectionConfig).where(
                DbConnectionConfig.connection_name == connection_name
            )
            return session.exec(stmt).first()

    def list_all(self) -> list[DbConnectionConfig]:
        """List all connection configurations ordered by name.

        Returns:
            List of ``DbConnectionConfig`` instances. Empty list if
            none exist.
        """
        with Session(self.engine) as session:
            stmt = select(DbConnectionConfig).order_by(
                col(DbConnectionConfig.connection_name).asc()
            )
            return list(session.exec(stmt))

    def list_public(self) -> list[dict[str, Any]]:
        """List all connections as public (no-secrets) dictionaries.

        Each dict comes from ``DbConnectionConfig.to_public_dict()``
        and never contains the ``credentials`` field.

        Returns:
            List of public dict representations, ordered by
            ``connection_name``.
        """
        return [config.to_public_dict() for config in self.list_all()]

    def get_credentials(self, connection_name: str) -> str | None:
        """Return the opaque credentials string for a connection.

        The repository does not decrypt — the returned string is the
        exact value that was stored at creation/update time. The
        tool layer is responsible for any decryption.

        Args:
            connection_name: The connection's unique name.

        Returns:
            The opaque credentials string, or ``None`` if the
            connection has no credentials set or does not exist.
        """
        config = self.get_by_name(connection_name)
        if config is None:
            return None
        return config.credentials

    def delete(self, connection_name: str) -> bool:
        """Delete a connection configuration by name.

        Args:
            connection_name: The connection's unique name.

        Returns:
            ``True`` if a row was deleted, ``False`` if no connection
            with that name existed.
        """
        with Session(self.engine) as session:
            config = session.exec(
                select(DbConnectionConfig).where(
                    DbConnectionConfig.connection_name == connection_name
                )
            ).first()
            if config is None:
                logger.warning(
                    f"DB connection not found for deletion: name={connection_name}"
                )
                return False

            session.delete(config)
            session.commit()

            logger.info(f"Deleted db connection: name={connection_name}")
            return True
