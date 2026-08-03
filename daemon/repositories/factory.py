"""Simple factory for creating repository instances."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, event
from sqlmodel import Session, SQLModel

from .project.repository import SQLModelProjectRepository
from .instance.repository import SQLModelInstanceRepository
from .message_queue.repository import SQLModelMessageQueueRepository
from .source.repository import SQLModelSourceRepository
from .job_queue.repository import JobRepository
from .job_queue.queue_repository import JobQueueRepository
from .mcp_server.repository import SQLModelMcpServerRepository
from .db_connection.repository import DbConnectionRepository
from .infra.repository import SQLModelInfraRepository
from .shared_context.repository import SharedContextMetadataRepository
from .shared_context.models import SharedContextMetadata
from .skill.repository import (
    SkillABTestRepository,
    SkillEmbeddingRepository,
    SkillLineageRepository,
    SkillRepository,
    SkillTriggerRepository,
    SkillUsageRepository,
)
from .skill.skill_bank_repository import SkillBankRepository
from .blueprint.repository import BlueprintRepository
from .blueprint.embedding_repository import (
    BlueprintEmbeddingRepository,
    create_blueprint_embedding_repository as _create_blueprint_embedding_repository,
)

if TYPE_CHECKING:
    from daemon.ensemble_config import EnsembleConfig

logger = logging.getLogger(__name__)


def create_mcp_server_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SQLModelMcpServerRepository:
    """Create an McpServerRepository from configuration or shared engine.
    
    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.
    
    Returns:
        Configured SQLModelMcpServerRepository instance.
    
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
    
    return SQLModelMcpServerRepository(engine)


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


def create_postgres_engine(config: "EnsembleConfig") -> Engine:
    """Create a SQLAlchemy **sync** engine for PostgreSQL.

    Uses the psycopg (sync) driver so the returned engine works with the
    existing sync ``Session(engine)`` consumer pattern used everywhere in
    the repository layer. Phase 2 of the migration plan introduces async
    sessions; for Phase 1 the sync engine is intentional.

    Connection URL is constructed inline (rather than calling
    ``config.get_postgres_url()``) because the latter returns the
    ``postgresql+asyncpg://`` URL used by the future async path.

    Args:
        config: EnsembleConfig containing PostgreSQL connection details.
                ``POSTGRES_*`` environment variables override file values
                for credential rotation without rewriting ``ensemble.json``.

    Returns:
        SQLAlchemy Engine instance ready for ``with engine.connect()`` /
        ``Session(engine)``.
    """
    import os

    pg = config.postgres
    host = os.environ.get("POSTGRES_HOST", pg.host)
    port = os.environ.get("POSTGRES_PORT", str(pg.port))
    db = os.environ.get("POSTGRES_DB", pg.db)
    user = os.environ.get("POSTGRES_USER", pg.user)
    password = os.environ.get("POSTGRES_PASSWORD", pg.password)

    url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"

    logger.info(
        f"Creating PostgreSQL engine: {host}:{port}/{db}"
    )

    engine = create_engine(
        url,
        echo=False,
        pool_size=5,
        max_overflow=10,
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

    # Skip for non-SQLite databases (PostgreSQL uses SQLModel metadata)
    if "sqlite" not in str(conn.engine.url):
        return

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
        # Extract last path component (e.g., 'developer' from './agents/developer')
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

    # Skip for non-SQLite databases (PostgreSQL uses SQLModel metadata)
    if "sqlite" not in str(engine.url):
        logger.info("Skipping SQLite migrations for non-SQLite database")
        return

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

        # NOTE: The agent_id rename 'coder' → 'developer' was previously handled
        # here as a Python UPDATE block. This function is no longer called in
        # production (factory creation paths rely on the SQLModel metadata +
        # MigrationRunner pipeline). Production SQLite migrations are now
        # applied via:
        #   daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql
        # and the corresponding PostgreSQL updates live in
        # daemon/manager.py:_ensure_postgres_columns().


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


def create_db_connection_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> DbConnectionRepository:
    """Create a DbConnectionRepository from configuration or shared engine.

    The repository is the persistence layer for the Database Tool
    Category's Connection Registry (Phase 1). It stores credentials
    as opaque encrypted strings — encryption/decryption is the
    responsibility of the tool layer, not the repository.

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured DbConnectionRepository instance.

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

    return DbConnectionRepository(engine)


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


def create_job_queue_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> JobQueueRepository:
    """Create a JobQueueRepository from configuration or shared engine.

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured JobQueueRepository instance.

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

    return JobQueueRepository(engine)


def create_infra_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SQLModelInfraRepository:
    """Create an InfraRepository from configuration or shared engine.

    Persistence layer for the infrastructure asset storage
    (Phase 1 of the infra info storage design). Three tables
    are created on first use:

    * ``infra_asset_types`` — global type-registry (no
      ``project_id``).
    * ``infra_assets`` — per-project asset storage with
      ``UNIQUE(project_id, type, name)`` and JSONB columns
      for ``attributes`` and ``relationships``.
    * ``infra_asset_history`` — built-in audit trail.

    The JSONB columns are typed via the
    :class:`~daemon.repositories.infra.types.JSONBType`
    TypeDecorator, which maps to ``JSONB`` on PostgreSQL and
    ``JSON`` on SQLite so the same schema works on both
    drivers. GIN indexes for the JSONB columns are declared
    in SQLAlchemy ``__table_args__`` with
    ``postgresql_using="gin"`` — SQLAlchemy emits them on
    PostgreSQL and silently skips them on SQLite (which has
    no GIN equivalent).

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured :class:`SQLModelInfraRepository` instance.
    """
    if engine is None:
        if config is None:
            raise ValueError("Either config or engine must be provided")
        engine = create_engine_from_config(config)

    if create_tables:
        SQLModel.metadata.create_all(engine)

    return SQLModelInfraRepository(engine)


def create_shared_context_metadata_repository(
    engine: Engine,
    create_tables: bool = False,
) -> SharedContextMetadataRepository:
    """Create a SharedContextMetadataRepository bound to ``engine``.

    Persistence layer for the ``shared_context_metadata`` table
    (Phase 1 of the Shared Context Metadata KV system). One table:

    * ``shared_context_metadata`` — generic ``(context_key,
      meta_key) → meta_value`` KV store with a composite
      ``UniqueConstraint`` enforcing no duplicate keys within a
      context. The ``meta_value`` column is typed via
      :class:`~daemon.repositories.infra.types.JSONBType` so the
      same schema works on both SQLite and PostgreSQL.

    Args:
        engine: Shared engine instance (recommended for avoiding
            lock contention). The repository is a thin wrapper
            around this engine; no per-call engine creation.
        create_tables: If True, create the table on the bound
            engine if it does not already exist. Defaults to
            ``False`` because the table is created by
            ``SQLModel.metadata.create_all()`` at startup —
            no migration file required.

    Returns:
        Configured :class:`SharedContextMetadataRepository` instance.
    """
    if create_tables:
        SharedContextMetadata.__table__.create(engine)

    return SharedContextMetadataRepository(engine)


def create_skill_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SkillRepository:
    """Create a SkillRepository from configuration or shared engine.

    Persistence layer for the Skill Evolution System (Phase 1).
    Six tables are created on first use (via
    ``SQLModel.metadata.create_all``):

    * ``skills`` — skill document + counter columns.
    * ``skill_lineage`` — parent/child evolution DAG.
    * ``skill_usage_records`` — per-task usage events.
    * ``skill_triggers`` — declarative condition → action rules.
    * ``skill_embeddings`` — cached per-skill vector embeddings
      (JSON array of floats, cross-driver via ``JSONBType``).
    * ``skill_ab_tests`` — A/B test buckets grouping old + new
      variants.

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured :class:`SkillRepository` instance.

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

    return SkillRepository(engine)


def create_blueprint_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> BlueprintRepository:
    """Create a BlueprintRepository from configuration or shared engine.

    Persistence layer for the Project Blueprint subsystem (Phase 1).
    Three tables are created on first use (via
    ``SQLModel.metadata.create_all``):

    * ``project_blueprints`` — project-scoped blueprint documents.
    * ``project_blueprint_triggers`` — trigger queries and embeddings.
    * ``project_blueprint_revisions`` — append-only content snapshots.

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured :class:`BlueprintRepository` instance.

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

    return BlueprintRepository(engine)


def create_blueprint_embedding_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> BlueprintEmbeddingRepository:
    """Create a BlueprintEmbeddingRepository from configuration or shared engine.

    Thin, well-typed handle over the ``project_blueprint_triggers`` table,
    used by the blueprint embedding service (independent of skill_evolution).
    Reuses the existing trigger table (no new migration).

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured :class:`BlueprintEmbeddingRepository` instance.
    """
    if engine is None:
        if config is None:
            raise ValueError("Either config or engine must be provided")
        engine = create_engine_from_config(config)

    if create_tables:
        SQLModel.metadata.create_all(engine)

    return _create_blueprint_embedding_repository(engine)


def create_skill_lineage_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SkillLineageRepository:
    """Create a SkillLineageRepository from configuration or shared engine.

    Persistence layer for the ``skill_lineage`` table (Phase 1
    of the Skill Evolution System). The table is created on
    first use via ``SQLModel.metadata.create_all``.

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured :class:`SkillLineageRepository` instance.

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

    return SkillLineageRepository(engine)


def create_skill_usage_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SkillUsageRepository:
    """Create a SkillUsageRepository from configuration or shared engine.

    Persistence layer for the ``skill_usage_records`` table
    (Phase 1 of the Skill Evolution System). The table is
    created on first use via ``SQLModel.metadata.create_all``.

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured :class:`SkillUsageRepository` instance.

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

    return SkillUsageRepository(engine)


def create_skill_trigger_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SkillTriggerRepository:
    """Create a SkillTriggerRepository from configuration or shared engine.

    Persistence layer for the ``skill_triggers`` table (Phase 1
    of the Skill Evolution System). The table is created on
    first use via ``SQLModel.metadata.create_all``. The
    ``condition_json`` column is typed via
    :class:`~daemon.repositories.infra.types.JSONBType` so the
    same schema works on both SQLite and PostgreSQL.

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured :class:`SkillTriggerRepository` instance.

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

    return SkillTriggerRepository(engine)


def create_skill_embedding_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SkillEmbeddingRepository:
    """Create a SkillEmbeddingRepository from configuration or shared engine.

    Persistence layer for the ``skill_embeddings`` table
    (Phase 1 of the Skill Evolution System). The table is
    created on first use via ``SQLModel.metadata.create_all``.
    The ``embedding`` column stores a JSON array of floats via
    :class:`~daemon.repositories.infra.types.JSONBType` — NOT
    BYTEA, NOT pickle — so the same schema works on both SQLite
    and PostgreSQL.

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured :class:`SkillEmbeddingRepository` instance.

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

    return SkillEmbeddingRepository(engine)


def create_skill_ab_test_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SkillABTestRepository:
    """Create a SkillABTestRepository from configuration or shared engine.

    Persistence layer for the ``skill_ab_tests`` table (Phase 1
    of the Skill Evolution System). The table is created on
    first use via ``SQLModel.metadata.create_all``. Counter
    columns (``comparisons``, ``extension_count``) are bumped
    atomically via raw-SQL UPDATE in the repository methods to
    avoid the read-modify-write race under concurrent feedback
    ingestion.

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured :class:`SkillABTestRepository` instance.

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

    return SkillABTestRepository(engine)


def create_skill_bank_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SkillBankRepository:
    """Create a SkillBankRepository from configuration or shared engine.

    Persistence layer for the Skill Bank — an isolated,
    user-facing CRUD store (NOT part of the Skill Evolution
    System). One table is created on first use via
    ``SQLModel.metadata.create_all``:

    * ``skill_bank`` — user-managed skill templates, scoped per
      project (or ``project_id IS NULL`` for global). No FK to
      ``skills``. No counters, no lineage, no embeddings.

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended for avoiding lock contention).
        create_tables: If True, create tables if they don't exist.

    Returns:
        Configured :class:`SkillBankRepository` instance.

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

    return SkillBankRepository(engine)


# Backward compatibility alias
create_task_repository = create_job_repository


__all__ = [
    "DatabaseConfig",
    "create_engine_from_config",
    "create_project_repository",
    "create_instance_repository",
    "create_message_queue_repository",
    "create_source_repository",
    "create_db_connection_repository",
    "create_job_repository",
    "create_job_queue_repository",
    "create_mcp_server_repository",
    "create_infra_repository",
    "create_shared_context_metadata_repository",
    "create_skill_repository",
    "create_skill_lineage_repository",
    "create_skill_usage_repository",
    "create_skill_trigger_repository",
    "create_skill_embedding_repository",
    "create_skill_ab_test_repository",
    "create_skill_bank_repository",
    "run_migrations",
]
