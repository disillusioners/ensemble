"""Migration runner for applying and rolling back database migrations."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlmodel import Session, SQLModel

from .models import SchemaMigration

if TYPE_CHECKING:
    from sqlalchemy import Engine

logger = logging.getLogger(__name__)


class MigrationError(Exception):
    """Raised when a migration operation fails."""
    pass


class MigrationFile:
    """Represents a parsed migration file.
    
    Attributes:
        path: Path to the migration file.
        version: Migration version extracted from filename.
        name: Human-readable name extracted from filename.
        up_sql: SQL statements for applying the migration.
        down_sql: SQL statements for rolling back the migration.
    """
    
    def __init__(
        self,
        path: Path,
        version: str,
        name: str,
        up_sql: str,
        down_sql: str,
    ) -> None:
        self.path = path
        self.version = version
        self.name = name
        self.up_sql = up_sql.strip()
        self.down_sql = down_sql.strip()
    
    @property
    def checksum(self) -> str:
        """Calculate SHA-256 checksum of the file content."""
        content = self.path.read_text()
        return hashlib.sha256(content.encode()).hexdigest()
    
    @classmethod
    def parse(cls, path: Path) -> "MigrationFile":
        """Parse a migration file into its components.
        
        Args:
            path: Path to the migration file.
            
        Returns:
            Parsed MigrationFile instance.
            
        Raises:
            ValueError: If the file has an invalid format.
        """
        content = path.read_text()
        
        # Extract version from filename (YYYYMMDD_HHMMSS)
        version_match = re.match(r"^(\d{8}_\d{6})", path.stem)
        if not version_match:
            raise ValueError(f"Invalid migration filename: {path.name}")
        version = version_match.group(1)
        
        # Extract name from filename (after timestamp)
        name_match = re.match(r"^\d{8}_\d{6}_(.+)$", path.stem)
        name = name_match.group(1).replace("_", " ") if name_match else "unnamed"
        
        # Parse UP and DOWN sections
        up_match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
        down_match = re.search(r"--\s*DOWN\s*\n(.*?)$", content, re.DOTALL)
        
        if not up_match:
            raise ValueError(f"Missing -- UP section in migration: {path.name}")
        
        up_sql = up_match.group(1).strip()
        down_sql = down_match.group(1).strip() if down_match else ""
        
        return cls(
            path=path,
            version=version,
            name=name,
            up_sql=up_sql,
            down_sql=down_sql,
        )


class MigrationRunner:
    """Discovers and executes database migrations.
    
    The runner manages a collection of SQL migration files, tracks which have
    been applied, and executes pending migrations within transactions.
    
    Example:
        runner = MigrationRunner(engine)
        runner.run_pending_migrations()  # Apply all pending migrations
        runner.get_migration_status()   # Check current status
    """
    
    def __init__(
        self,
        engine: Engine,
        migrations_dir: Path | None = None,
    ) -> None:
        """Initialize the migration runner.
        
        Args:
            engine: SQLAlchemy engine for database operations.
            migrations_dir: Directory containing migration files.
                           Defaults to daemon/migrations/versions/.
        """
        self.engine = engine
        self.migrations_dir = migrations_dir or Path(__file__).parent / "versions"
    
    def ensure_migrations_table(self) -> None:
        """Create or update the schema_migrations table.
        
        Uses CREATE TABLE IF NOT EXISTS to avoid race conditions when
        multiple processes try to create the table simultaneously.
        
        Also adds missing columns to handle schema evolution - if the
        SchemaMigration model adds new columns, this ensures they exist.
        """
        with self.engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    execution_time_ms INTEGER,
                    checksum TEXT
                )
            """))
            conn.commit()
        
        # Auto-migrate: ensure all SchemaMigration columns exist
        # This handles cases where the model evolved but the table didn't
        self._sync_migrations_table_schema()
    
    def _sync_migrations_table_schema(self) -> None:
        """Sync schema_migrations table columns with SchemaMigration model.
        
        Compares the actual table schema with the expected columns from
        the SchemaMigration model and adds any missing columns.
        """
        model_columns = {
            "version": "TEXT",
            "name": "TEXT",
            "applied_at": "TEXT",
            "execution_time_ms": "INTEGER",
            "checksum": "TEXT",
        }
        
        with self.engine.connect() as conn:
            # Get existing columns
            result = conn.execute(text("PRAGMA table_info(schema_migrations)"))
            existing_columns = {row[1] for row in result.fetchall()}
            
            # Add missing columns
            for col_name, col_type in model_columns.items():
                if col_name not in existing_columns:
                    logger.info(f"Auto-migrating schema_migrations: adding column '{col_name}'")
                    conn.execute(text(f"ALTER TABLE schema_migrations ADD COLUMN {col_name} {col_type}"))
            
            conn.commit()
    
    def get_applied_versions(self) -> set[str]:
        """Get set of applied migration versions.
        
        Returns:
            Set of version strings for all applied migrations.
        """
        with Session(self.engine) as session:
            migrations = session.exec(select(SchemaMigration)).all()
            # Extract version string from each migration
            versions = []
            for m in migrations:
                # SQLAlchemy may return Row objects containing the model
                if hasattr(m, 'version'):
                    # It's a SchemaMigration model directly
                    versions.append(m.version)
                elif isinstance(m, dict):
                    versions.append(m['version'])
                elif hasattr(m, '__getitem__'):
                    # It's a Row - get the first element which should be the model
                    item = m[0]
                    if hasattr(item, 'version'):
                        versions.append(item.version)
                    elif isinstance(item, dict):
                        versions.append(item['version'])
            return set(versions)
    
    def discover_migrations(self) -> list[MigrationFile]:
        """Discover and parse all migration files, sorted by version.
        
        Returns:
            List of MigrationFile objects sorted by version.
        """
        if not self.migrations_dir.exists():
            logger.info(f"Creating migrations directory: {self.migrations_dir}")
            self.migrations_dir.mkdir(parents=True, exist_ok=True)
            return []
        
        migrations = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            try:
                migration = MigrationFile.parse(path)
                migrations.append(migration)
            except ValueError as e:
                logger.warning(f"Skipping invalid migration file {path.name}: {e}")
        
        return sorted(migrations, key=lambda m: m.version)
    
    def get_pending_migrations(self) -> list[MigrationFile]:
        """Get migrations that haven't been applied yet.
        
        Returns:
            List of MigrationFile objects for pending migrations.
        """
        applied = self.get_applied_versions()
        all_migrations = self.discover_migrations()
        return [m for m in all_migrations if m.version not in applied]
    
    def _table_exists(self, conn, table_name: str) -> bool:
        """Check if a table exists in the database."""
        result = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table_name},
        )
        return result.fetchone() is not None
    
    def _column_exists(self, conn, table_name: str, column_name: str) -> bool:
        """Check if a column exists in a table."""
        if not self._table_exists(conn, table_name):
            return False
        result = conn.execute(text(f"PRAGMA table_info({table_name})"))
        return any(row[1] == column_name for row in result.fetchall())
    
    def _is_rename_migration_needed(self, conn) -> bool:
        """Check if the session→instance rename migration needs to run.
        
        Returns True if any old 'session' named tables or columns exist.
        """
        # Check for old table names
        for old_table in ("sessions", "session_hierarchy", "session_mappings"):
            if self._table_exists(conn, old_table):
                return True
        
        # Check for old column names in renamed/existing tables
        for table, old_col in [
            ("schedule_executions", "session_id"),
            ("projects", "creator_session_id"),
            ("job_queue_items", "session_id"),
            ("message_queue", "session_id"),
        ]:
            if self._column_exists(conn, table, old_col):
                return True
        
        return False
    
    def apply_migration(self, migration: MigrationFile) -> float:
        """Apply a single migration within a transaction.
        
        Args:
            migration: Migration to apply.
            
        Returns:
            Execution time in milliseconds.
            
        Raises:
            MigrationError: If the migration fails for reasons other than
                idempotent scenarios (duplicate column, missing table, already exists).
        """
        logger.info(f"Starting migration: {migration.version} - {migration.name}")
        start_time = time.perf_counter()
        
        # Pre-check for rename migration: if old session tables/columns don't exist,
        # the rename migration is a no-op (tables already created with new names)
        if "rename session to instance" in migration.name.lower():
            with self.engine.connect() as check_conn:
                if not self._is_rename_migration_needed(check_conn):
                    logger.info(
                        f"Migration {migration.version}: no old 'session' schema detected, "
                        f"recording as applied (no-op)"
                    )
                    with Session(self.engine) as session:
                        record = SchemaMigration(
                            version=migration.version,
                            name=migration.name,
                            applied_at=datetime.now(timezone.utc).isoformat(),
                            execution_time_ms=0,
                            checksum=migration.checksum,
                        )
                        session.add(record)
                        session.commit()
                    return 0.0
        
        with self.engine.begin() as conn:
            # Execute the UP SQL
            try:
                # Split on semicolons and execute each statement
                statements = [
                    s.strip() for s in migration.up_sql.split(";") if s.strip()
                ]
                for stmt in statements:
                    if stmt:
                        try:
                            conn.execute(text(stmt))
                        except Exception as stmt_err:
                            # Handle idempotent scenarios gracefully:
                            # These occur when SQLModel.metadata.create_all() has already
                            # applied the schema change, or when tables have been renamed.
                            err_str = str(stmt_err).lower()
                            
                            if "duplicate column name" in err_str:
                                # Column already exists - idempotent operation, continue
                                logger.info(
                                    f"Migration {migration.version}: column already exists, skipping (idempotent)"
                                )
                            elif "no such table" in err_str:
                                # Only treat as idempotent for CREATE statements
                                # ALTER/UPDATE/INSERT/DELETE on nonexistent tables are real errors
                                stmt_upper = stmt.upper().strip()
                                if stmt_upper.startswith(("CREATE ", "CREATE\n")):
                                    # Table doesn't exist - this is safe to skip when:
                                    # 1. create_all() already created tables with new names (rename migration)
                                    # 2. The table was never created (baseline migration on fresh DB)
                                    # 3. The table was already renamed by an earlier step
                                    logger.info(
                                        f"Migration {migration.version}: table doesn't exist, skipping (idempotent)"
                                    )
                                else:
                                    raise
                            elif "already exists" in err_str:
                                # Table/index already exists - idempotent for CREATE statements
                                logger.info(
                                    f"Migration {migration.version}: object already exists, skipping (idempotent)"
                                )
                            elif "no such column" in err_str:
                                # Column doesn't exist - this happens when a rename migration
                                # tries to SELECT an old column name but the table was already
                                # created with new column names by create_all()
                                logger.info(
                                    f"Migration {migration.version}: column doesn't exist, skipping (idempotent)"
                                )
                            elif "has no column" in err_str:
                                # INSERT into column that doesn't exist - same scenario as above
                                logger.info(
                                    f"Migration {migration.version}: column mismatch, skipping (idempotent)"
                                )
                            else:
                                raise
            except Exception as e:
                logger.error(f"Migration {migration.version} failed: {e}")
                raise MigrationError(
                    f"Migration {migration.version} failed: {e}"
                ) from e
            
            # Record the migration as applied (even if some statements were idempotently skipped)
            # This prevents re-running migrations that are no-ops on the current schema
            execution_time_ms = int((time.perf_counter() - start_time) * 1000)
            with Session(bind=conn) as session:
                record = SchemaMigration(
                    version=migration.version,
                    name=migration.name,
                    applied_at=datetime.now(timezone.utc).isoformat(),
                    execution_time_ms=execution_time_ms,
                    checksum=migration.checksum,
                )
                session.add(record)
                session.commit()
        
        logger.info(
            f"Completed migration {migration.version} in {execution_time_ms}ms"
        )
        return execution_time_ms
    
    def rollback_migration(self, version: str) -> float:
        """Rollback a specific migration.
        
        Args:
            version: Version of the migration to rollback.
            
        Returns:
            Execution time in milliseconds.
            
        Raises:
            MigrationError: If the migration is not found or has no DOWN section.
        """
        # Find the migration file
        migrations = self.discover_migrations()
        migration = next((m for m in migrations if m.version == version), None)
        
        if not migration:
            raise MigrationError(f"Migration {version} not found")
        
        if not migration.down_sql:
            raise MigrationError(f"Migration {version} has no DOWN section")
        
        logger.info(f"Rolling back migration: {version} - {migration.name}")
        start_time = time.perf_counter()
        
        with self.engine.begin() as conn:
            # Execute the DOWN SQL
            statements = [
                s.strip() for s in migration.down_sql.split(";") if s.strip()
            ]
            for stmt in statements:
                if stmt:
                    conn.execute(text(stmt))
            
            # Remove the migration record
            conn.execute(
                text("DELETE FROM schema_migrations WHERE version = :version"),
                {"version": version},
            )
        
        execution_time_ms = int((time.perf_counter() - start_time) * 1000)
        logger.info(f"Rolled back migration {version} in {execution_time_ms}ms")
        return execution_time_ms
    
    def run_pending_migrations(self) -> list[str]:
        """Apply all pending migrations.
        
        Returns:
            List of applied migration versions.
        """
        self.ensure_migrations_table()
        pending = self.get_pending_migrations()
        
        if not pending:
            logger.info("No pending migrations")
            return []
        
        applied = []
        for migration in pending:
            execution_time = self.apply_migration(migration)
            logger.info(
                f"Applied migration {migration.version} in {execution_time}ms"
            )
            applied.append(migration.version)
        
        return applied
    
    def get_migration_status(self) -> dict[str, object]:
        """Get the current migration status.
        
        Returns:
            Dictionary with applied and pending migration lists.
        """
        self.ensure_migrations_table()
        applied = sorted(self.get_applied_versions())
        all_migrations = self.discover_migrations()
        pending = [m.version for m in self.get_pending_migrations()]
        
        return {
            "applied": applied,
            "pending": pending,
            "total_discovered": len(all_migrations),
            "last_applied": applied[-1] if applied else None,
        }
