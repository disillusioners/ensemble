"""Factory for creating repository instances.

Provides a unified interface for creating repositories configured for
different database backends (SQLite, PostgreSQL).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from sqlmodel import Session, SQLModel

from .protocol import ProjectRepositoryProtocol
from .project_repository import SQLModelProjectRepository
from .unit_of_work import SQLiteUnitOfWork, PostgreSQLUnitOfWork


class DatabaseType(str, Enum):
    """Supported database types."""
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


@dataclass
class DatabaseConfig:
    """Configuration for database connection.
    
    Supports both SQLite (for development/testing) and PostgreSQL (for production).
    """
    db_type: Literal["sqlite", "postgresql"] = "sqlite"
    
    # SQLite settings
    db_path: str = "data.db"
    
    # PostgreSQL settings
    postgres_url: str | None = None  # postgresql://user:pass@host:port/db
    pool_size: int = 5
    max_overflow: int = 10
    
    # Common settings
    echo: bool = False  # SQL logging
    pool_pre_ping: bool = True  # Connection health checks
    
    @classmethod
    def sqlite(cls, db_path: str = "data.db", echo: bool = False) -> "DatabaseConfig":
        """Create SQLite configuration."""
        return cls(db_type="sqlite", db_path=db_path, echo=echo)
    
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
            db_type="postgresql",
            postgres_url=url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            echo=echo,
        )


def create_project_repository(
    config: DatabaseConfig,
    create_tables: bool = True,
) -> SQLModelProjectRepository:
    """Create a ProjectRepository based on configuration.
    
    This is the main factory function for creating repository instances.
    It handles:
    - Database engine creation
    - Table creation (if needed)
    - Session management
    
    Args:
        config: Database configuration specifying type and connection details.
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelProjectRepository instance.
    
    Example:
        # SQLite (development)
        config = DatabaseConfig.sqlite("projects.db")
        repo = create_project_repository(config)
        
        # PostgreSQL (production)
        config = DatabaseConfig.postgresql(
            url="postgresql://user:pass@localhost/mydb"
        )
        repo = create_project_repository(config)
    """
    if config.db_type == "sqlite":
        uow = SQLiteUnitOfWork(
            db_path=config.db_path,
            echo=config.echo,
            pool_pre_ping=config.pool_pre_ping,
        )
    elif config.db_type == "postgresql":
        if not config.postgres_url:
            raise ValueError("postgres_url is required for PostgreSQL")
        uow = PostgreSQLUnitOfWork(
            database_url=config.postgres_url,
            echo=config.echo,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_pre_ping=config.pool_pre_ping,
        )
    else:
        raise ValueError(f"Unsupported database type: {config.db_type}")
    
    if create_tables:
        uow.create_tables()
    
    # Create a session for the repository
    # Note: The repository manages its own session lifecycle
    session = Session(uow.engine)
    
    return SQLModelProjectRepository(session)


def create_project_repository_from_uow(
    uow: SQLiteUnitOfWork | PostgreSQLUnitOfWork,
    create_tables: bool = True,
) -> SQLModelProjectRepository:
    """Create a ProjectRepository from an existing Unit of Work.
    
    Use this when you want to share the same Unit of Work across
    multiple repositories or when you need more control over
    transaction boundaries.
    
    Args:
        uow: An existing Unit of Work instance.
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelProjectRepository instance.
    """
    if create_tables:
        uow.create_tables()
    
    session = Session(uow.engine)
    return SQLModelProjectRepository(session)


# Convenience function for backward compatibility
def create_sqlite_repository(
    db_path: str,
    echo: bool = False,
) -> SQLModelProjectRepository:
    """Create a SQLite-based ProjectRepository (convenience function).
    
    This provides a simple interface for the common case of SQLite development.
    
    Args:
        db_path: Path to SQLite database file.
        echo: If True, SQL statements will be logged.
    
    Returns:
        Configured SQLModelProjectRepository instance.
    """
    config = DatabaseConfig.sqlite(db_path=db_path, echo=echo)
    return create_project_repository(config)


def create_postgresql_repository(
    url: str,
    pool_size: int = 5,
    max_overflow: int = 10,
    echo: bool = False,
) -> SQLModelProjectRepository:
    """Create a PostgreSQL-based ProjectRepository (convenience function).
    
    Args:
        url: PostgreSQL connection URL.
        pool_size: Number of connections in the pool.
        max_overflow: Maximum overflow connections.
        echo: If True, SQL statements will be logged.
    
    Returns:
        Configured SQLModelProjectRepository instance.
    """
    config = DatabaseConfig.postgresql(
        url=url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        echo=echo,
    )
    return create_project_repository(config)
