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
from .task_queue.repository import TaskRepository


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
    
    return SQLModelSourceRepository(engine)


def create_task_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> TaskRepository:
    """Create a TaskRepository from configuration or shared engine.
    
    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured TaskRepository instance.
    
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
    
    return TaskRepository(engine)


# Backward compatibility alias
_create_engine = create_engine_from_config
