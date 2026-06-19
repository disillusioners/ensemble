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
from sqlalchemy.exc import IntegrityError
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

    # Fields that callers may NOT change via update_connection().
    # ``id`` is the primary key, ``connection_name`` is the public identifier
    # (rename is intentionally not supported in Phase 1), and ``created_at``
    # is immutable for audit consistency.
    _PROTECTED_UPDATE_FIELDS = frozenset({"id", "connection_name", "created_at"})

    def update_connection(
        self,
        connection_name: str,
        **fields: Any,
    ) -> DbConnectionConfig | None:
        """Update fields on an existing connection and bump ``updated_at``.

        Accepts arbitrary keyword fields matching the model columns
        (e.g. ``host``, ``port``, ``database``, ``username``,
        ``credentials``, ``ssl_mode``, ``db_type``). Protected fields
        (``id``, ``connection_name``, ``created_at``) are silently
        filtered out and a warning is logged.

        L4 defense-in-depth: this method also wraps ``session.commit()``
        in a try/except :class:`IntegrityError`. ``connection_name`` is
        currently a protected field (rename is intentionally not
        supported in Phase 1 — see ``_PROTECTED_UPDATE_FIELDS``), so
        ``update_connection`` cannot directly trigger the UNIQUE
        constraint on ``connection_name``. The handler is here as
        forward protection: any future column added to this table with
        a UNIQUE constraint would otherwise leak an opaque SQLAlchemy
        :class:`IntegrityError` (with dialect-specific message text) to
        the caller. We translate it into a clean ``ValueError`` carrying
        the duplicate value and the field name, matching the pattern
        used by :class:`daemon.repositories.project.ProjectRepository.update`.

        Args:
            connection_name: The unique name of the connection to
                update. The connection is looked up by this value;
                renaming via update is not supported.
            **fields: Column values to overwrite on the model.

        Returns:
            The updated ``DbConnectionConfig`` instance, or ``None`` if
            no connection with the given name exists.

        Raises:
            AttributeError: If any of the supplied field names does not
                correspond to a column on ``DbConnectionConfig``.
            ValueError: If the underlying commit violates a UNIQUE
                constraint (e.g. a future ``host`` uniqueness check).
                The message names the offending field and value.
        """
        with Session(self.engine) as session:
            config = session.exec(
                select(DbConnectionConfig).where(
                    DbConnectionConfig.connection_name == connection_name
                )
            ).first()
            if config is None:
                logger.warning(
                    f"DB connection not found for update: name={connection_name}"
                )
                return None

            applied_fields: list[str] = []
            for key, value in fields.items():
                if key in self._PROTECTED_UPDATE_FIELDS:
                    logger.warning(
                        f"Ignoring protected field in update_connection: "
                        f"name={connection_name}, field={key}"
                    )
                    continue
                if not hasattr(config, key):
                    raise AttributeError(
                        f"DbConnectionConfig has no field {key!r}"
                    )
                setattr(config, key, value)
                applied_fields.append(key)

            config.update_timestamp()
            session.add(config)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                # Translate UNIQUE-constraint violations into a clean
                # ValueError with the duplicate value and field name.
                # The dialect-specific message text would otherwise
                # leak DB internals (constraint name, table name) to
                # the API caller. We match by constraint name first,
                # then fall back to a generic duplicate-value message
                # if we can identify the offending field from the
                # fields the caller actually wrote.
                offending_value = self._extract_unique_value(fields)
                offending_field = self._identify_unique_field(str(exc).lower())
                if offending_field is None:
                    # Fall back to the first field the caller wrote —
                    # best-effort when the dialect message is opaque.
                    offending_field = next(iter(fields.keys()), "value")
                raise ValueError(
                    f"A db_connection with {offending_field}={offending_value!r} already exists"
                ) from exc
            session.refresh(config)

            logger.info(
                f"Updated db connection: name={connection_name}, "
                f"fields={applied_fields}"
            )
            return config

    @staticmethod
    def _extract_unique_value(fields: dict[str, Any]) -> Any:
        """Pick the most-likely duplicate value from the fields dict.

        Used by the IntegrityError handler to populate the error
        message. We don't know which field triggered the violation
        from the fields alone — the database raises it after the
        commit — so we look at the SQLAlchemy error first via
        :meth:`_identify_unique_field`, and only fall back to the
        first supplied field when that fails.

        Args:
            fields: The kwargs the caller passed to
                ``update_connection``.

        Returns:
            The value the caller supplied for the offending field, or
            ``"<unknown>"`` if no fields were supplied.
        """
        if not fields:
            return "<unknown>"
        # First non-protected field is the best heuristic when the
        # dialect-specific error message doesn't name the field.
        for key, value in fields.items():
            if key not in DbConnectionRepository._PROTECTED_UPDATE_FIELDS:
                return value
        return next(iter(fields.values()))

    @staticmethod
    def _identify_unique_field(err_lower: str) -> str | None:
        """Identify the field name from a SQLAlchemy IntegrityError message.

        Tries to match the common SQLite/PostgreSQL message patterns:

        * SQLite: ``"UNIQUE constraint failed: db_connections.<field>"``
        * PostgreSQL: ``"duplicate key value violates unique constraint
          \"...\"`` — the constraint name encodes the field; we look for
          well-known field names as a fallback.

        Args:
            err_lower: Lowercased ``str(exc)`` from the IntegrityError.

        Returns:
            The suspected field name, or ``None`` if no match.
        """
        # SQLite pattern: "unique constraint failed: db_connections.<field>"
        marker = "unique constraint failed: db_connections."
        idx = err_lower.find(marker)
        if idx != -1:
            tail = err_lower[idx + len(marker):]
            # Take the first comma-separated token (SQLite lists all
            # columns of a composite UNIQUE index).
            return tail.split(",")[0].strip() or None
        # PostgreSQL pattern: try the constraint-name conventions used
        # in the migration files (e.g. "uq_db_connections_<field>").
        # If the error mentions a field by name directly, prefer that.
        for candidate in ("connection_name", "host", "port"):
            if candidate in err_lower:
                return candidate
        return None

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
