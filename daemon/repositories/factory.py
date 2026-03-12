"""Simple factory for creating repository instances."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .project.repository import SQLModelProjectRepository
from .session.repository import SQLModelSessionRepository
from .message_queue.repository import SQLModelMessageQueueRepository
from .source.repository import SQLModelSourceRepository


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


def create_project_repository(
    config: DatabaseConfig,
    create_tables: bool = True,
) -> SQLModelProjectRepository:
    """Create a ProjectRepository from configuration.
    
    Args:
        config: Database configuration.
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelProjectRepository instance.
    """
    # Build engine kwargs based on database type
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
    
    if create_tables:
        SQLModel.metadata.create_all(engine)
    
    return SQLModelProjectRepository(engine)


def create_session_repository(
    config: DatabaseConfig,
    create_tables: bool = True,
) -> SQLModelSessionRepository:
    """Create a SessionRepository from configuration.
    
    Args:
        config: Database configuration.
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelSessionRepository instance.
    """
    engine = _create_engine(config)
    
    if create_tables:
        SQLModel.metadata.create_all(engine)
    
    return SQLModelSessionRepository(engine)


def create_message_queue_repository(
    config: DatabaseConfig,
    create_tables: bool = True,
) -> SQLModelMessageQueueRepository:
    """Create a MessageQueueRepository from configuration.
    
    Args:
        config: Database configuration.
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelMessageQueueRepository instance.
    """
    engine = _create_engine(config)
    
    if create_tables:
        SQLModel.metadata.create_all(engine)
    
    return SQLModelMessageQueueRepository(engine)


def create_source_repository(
    config: DatabaseConfig,
    create_tables: bool = True,
) -> SQLModelSourceRepository:
    """Create a SourceRepository from configuration.
    
    Args:
        config: Database configuration.
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelSourceRepository instance.
    """
    engine = _create_engine(config)
    
    if create_tables:
        SQLModel.metadata.create_all(engine)
    
    return SQLModelSourceRepository(engine)


def _create_engine(config: DatabaseConfig):
    """Create a database engine from configuration.
    
    Args:
        config: Database configuration.
    
    Returns:
        SQLAlchemy engine instance.
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
