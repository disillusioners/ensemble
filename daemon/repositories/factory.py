"""Simple factory for creating repository instances."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

from .project.repository import SQLModelProjectRepository
from .session.repository import SQLModelSessionRepository
from .message_queue.repository import SQLModelMessageQueueRepository
from .source.repository import SQLModelSourceRepository
from .job_queue.repository import JobRepository


@dataclass
class DatabaseConfig:
    """Simple database configuration.
    
    Works with both SQLite and PostgreSQL via connection string.
    """
    connection_string: str
    echo: bool = False
    
    # PostgreSQL-specific (ignored for SQLite)
    pool_size: int = 5
    max_overflow: int = 10
    
    @classmethod
    def sqlite(cls, db_path: str = "data.db", echo: bool = False) -> "DatabaseConfig":
        """Create SQLite configuration."""
        return cls(connection_string=f"sqlite:///{db_path}", echo=echo)
    
    @classmethod
    def postgresql(
        cls,
        url: str,
        pool_size: int = 5,
        max_overflow: int = 10,
        echo: bool = False,
    ) -> "DatabaseConfig":
        """Create PostgreSQL configuration."""
        return cls(
            connection_string=url,
            echo=echo,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )


def create_engine_from_config(config: DatabaseConfig) -> Engine:
    """Create a database engine from configuration.
    
    This is the recommended way to create an engine. Create ONE engine
    and share it across all repositories to avoid database lock contention.
    
    Args:
        config: Database configuration.
    
    Returns:
        SQLAlchemy Engine instance configured for the database.
    
    Example:
        config = DatabaseConfig.sqlite("data.db")
        engine = create_engine_from_config(config)
        
        # Share engine across all repositories
        queue_repo = create_message_queue_repository(engine=engine)
        session_repo = create_session_repository(engine=engine)
    """
    is_sqlite = "sqlite" in config.connection_string.lower()
    
    if is_sqlite:
        engine = create_engine(
            config.connection_string,
            echo=config.echo,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )
        
        # Configure SQLite for better concurrency
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    else:
        engine = create_engine(
            config.connection_string,
            echo=config.echo,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_pre_ping=True,
        )
    
    return engine


def run_migrations(engine: Engine) -> None:
    """Run database migrations to add missing columns to existing tables.
    
    This function checks for and adds any columns that exist in the model
    but are missing from the actual database schema. This handles cases where
    new columns are added to models after the database was initially created.
    
    Args:
        engine: SQLAlchemy Engine instance.
    """
    from sqlalchemy import text
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Get the connection to check existing columns
    with engine.connect() as conn:
        # Check if projects table exists and has job_queue_paused column
        try:
            result = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'"))
            row = result.fetchone()
            if row:
                table_sql = row[0] if row[0] else ""
                if 'job_queue_paused' not in table_sql:
                    conn.execute(text("ALTER TABLE projects ADD COLUMN job_queue_paused BOOLEAN DEFAULT 0"))
                    conn.commit()
                    logger.info("Migration: Added job_queue_paused column to projects table")
        except Exception as e:
            logger.warning(f"Migration check failed (table may not exist yet): {e}")


def create_project_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SQLModelProjectRepository:
    """Create a ProjectRepository from configuration or shared engine.
    
    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelProjectRepository instance.
    
    Note:
        Either config or engine must be provided. If both are provided,
        engine takes precedence.
    """
    if engine is None:
        if config is None:
            raise ValueError("Either config or engine must be provided")
        engine = create_engine_from_config(config)
    
    if create_tables:
        SQLModel.metadata.create_all(engine)
        run_migrations(engine)
    
    return SQLModelProjectRepository(engine)


def create_session_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SQLModelSessionRepository:
    """Create a SessionRepository from configuration or shared engine.
    
    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelSessionRepository instance.
    
    Note:
        Either config or engine must be provided. If both are provided,
        engine takes precedence.
    """
    if engine is None:
        if config is None:
            raise ValueError("Either config or engine must be provided")
        engine = create_engine_from_config(config)
    
    if create_tables:
        SQLModel.metadata.create_all(engine)
        run_migrations(engine)
    
    return SQLModelSessionRepository(engine)


def create_message_queue_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SQLModelMessageQueueRepository:
    """Create a MessageQueueRepository from configuration or shared engine.
    
    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelMessageQueueRepository instance.
    
    Note:
        Either config or engine must be provided. If both are provided,
        engine takes precedence.
    """
    if engine is None:
        if config is None:
            raise ValueError("Either config or engine must be provided")
        engine = create_engine_from_config(config)
    
    if create_tables:
        SQLModel.metadata.create_all(engine)
        run_migrations(engine)
    
    return SQLModelMessageQueueRepository(engine)


def create_source_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SQLModelSourceRepository:
    """Create a SourceRepository from configuration or shared engine.
    
    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelSourceRepository instance.
    
    Note:
        Either config or engine must be provided. If both are provided,
        engine takes precedence.
    """
    if engine is None:
        if config is None:
            raise ValueError("Either config or engine must be provided")
        engine = create_engine_from_config(config)
    
    if create_tables:
        SQLModel.metadata.create_all(engine)
        run_migrations(engine)
    
    return SQLModelSourceRepository(engine)


def create_job_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> JobRepository:
    """Create a JobRepository from configuration or shared engine.
    
    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured JobRepository instance.
    
    Note:
        Either config or engine must be provided. If both are provided,
        engine takes precedence.
    """
    if engine is None:
        if config is None:
            raise ValueError("Either config or engine must be provided")
        engine = create_engine_from_config(config)
    
    if create_tables:
        SQLModel.metadata.create_all(engine)
        run_migrations(engine)
    
    return JobRepository(engine)


# Backward compatibility alias
create_task_repository = create_job_repository
