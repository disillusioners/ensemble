"""Simple factory for creating repository instances."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

from .project.repository import SQLModelProjectRepository
from .instance.repository import SQLModelInstanceRepository
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
        instance_repo = create_instance_repository(engine=engine)
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


def _add_agent_id_column(conn, table_name: str, logger) -> None:
    """Add agent_id column to a table if it doesn't exist and populate from agent_dir.
    
    Args:
        conn: Database connection.
        table_name: Name of the table to migrate.
        logger: Logger instance for recording migration progress.
    """
    from sqlalchemy import text
    
    # Check if column exists
    result = conn.execute(text(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'"))
    row = result.fetchone()
    if not row or not row[0]:
        return
    
    table_sql = row[0]
    if 'agent_id' in table_sql:
        return  # Column already exists
    
    # Determine PK column per table
    pk_column = "instance_id"
    if table_name in ("instance_mappings", "session_mappings"):
        pk_column = "mapping_id"
    elif table_name in ("job_queue_items", "jobqueue"):
        pk_column = "job_id"
    
    # Add column as nullable first (for backward compat with old rows)
    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN agent_id TEXT"))
    conn.commit()
    logger.info(f"Migration: Added agent_id column to {table_name} table")
    
    # Populate agent_id from agent_dir using Python (more reliable than complex SQL)
    # Fetch rows that need updating
    result = conn.execute(text(f"SELECT {pk_column}, agent_dir FROM {table_name} WHERE agent_id IS NULL AND agent_dir IS NOT NULL AND agent_dir != ''"))
    rows = result.fetchall()
    
    for row in rows:
        pk_value, agent_dir = row
        # Extract last path component (e.g., 'coder' from './agents/coder')
        agent_id = agent_dir.rstrip('/').rsplit('/', 1)[-1] if '/' in agent_dir else agent_dir
        agent_id = agent_id.rsplit('\\', 1)[-1] if '\\' in agent_id else agent_id
        conn.execute(text(f"UPDATE {table_name} SET agent_id = :agent_id WHERE {pk_column} = :pk_value"), 
                    {"agent_id": agent_id, "pk_value": pk_value})
    
    conn.commit()
    logger.info(f"Migration: Populated agent_id from agent_dir in {table_name} table ({len(rows)} rows)")


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
        # Migration: Add job_queue_paused to projects
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
        
        # Migration: Add creator_agent_id to projects
        try:
            result = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'"))
            row = result.fetchone()
            if row:
                table_sql = row[0] if row[0] else ""
                if 'creator_agent_id' not in table_sql:
                    conn.execute(text("ALTER TABLE projects ADD COLUMN creator_agent_id TEXT"))
                    conn.commit()
                    logger.info("Migration: Added creator_agent_id column to projects table")
        except Exception as e:
            logger.warning(f"Migration check failed for creator_agent_id: {e}")
        
        # Migration: Add agent_id to instances
        try:
            _add_agent_id_column(conn, "instances", logger)
        except Exception as e:
            logger.warning(f"Migration failed for instances table: {e}")
        
        # Migration: Add agent_id to instance_mappings
        try:
            _add_agent_id_column(conn, "instance_mappings", logger)
        except Exception as e:
            logger.warning(f"Migration failed for instance_mappings table: {e}")
        
        # Migration: Add agent_id to jobqueue (legacy table name)
        try:
            _add_agent_id_column(conn, "jobqueue", logger)
        except Exception as e:
            logger.warning(f"Migration failed for jobqueue table: {e}")
        
        # Migration: Add agent_id to job_queue_items (new table name)
        try:
            _add_agent_id_column(conn, "job_queue_items", logger)
        except Exception as e:
            logger.warning(f"Migration failed for job_queue_items table: {e}")


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
    
    # NOTE: File-based migrations are now handled by MigrationRunner
    # via run_pending_migrations() in the API startup.
    # Legacy Python migrations (run_migrations) are disabled.
    
    return SQLModelProjectRepository(engine)


def create_instance_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SQLModelInstanceRepository:
    """Create an InstanceRepository from configuration or shared engine.
    
    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelInstanceRepository instance.
    
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
    
    # NOTE: File-based migrations are now handled by MigrationRunner
    # via run_pending_migrations() in the API startup.
    # Legacy Python migrations (run_migrations) are disabled.
    
    return SQLModelInstanceRepository(engine)


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
    
    # NOTE: File-based migrations are now handled by MigrationRunner
    # via run_pending_migrations() in the API startup.
    # Legacy Python migrations (run_migrations) are disabled.
    
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
    
    # NOTE: File-based migrations are now handled by MigrationRunner
    # via run_pending_migrations() in the API startup.
    # Legacy Python migrations (run_migrations) are disabled.
    
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
    
    # NOTE: File-based migrations are now handled by MigrationRunner
    # via run_pending_migrations() in the API startup.
    # Legacy Python migrations (run_migrations) are disabled.
    
    return JobRepository(engine)


# Backward compatibility alias
create_task_repository = create_job_repository
