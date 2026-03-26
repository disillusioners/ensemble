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
        """Create the schema_migrations table if it doesn't exist.
        
        Uses CREATE TABLE IF NOT EXISTS to avoid race conditions when
        multiple processes try to create the table simultaneously.
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
    
    def get_applied_versions(self) -> set[str]:
        """Get set of applied migration versions.
        
        Returns:
            Set of version strings for all applied migrations.
        """
        with Session(self.engine) as session:
            migrations = session.exec(select(SchemaMigration)).all()
            return {m.version for m in migrations}
    
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
    
    def apply_migration(self, migration: MigrationFile) -> float:
        """Apply a single migration within a transaction.
        
        Args:
            migration: Migration to apply.
            
        Returns:
            Execution time in milliseconds.
            
        Raises:
            MigrationError: If the migration fails for non-duplicate reasons,
                or if any statement is skipped/fails.
        """
        logger.info(f"Starting migration: {migration.version} - {migration.name}")
        start_time = time.perf_counter()
        statements_failed = False
        
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
                            # Handle "duplicate column name" gracefully - column already exists
                            # This happens when SQLModel.metadata.create_all() already added the column
                            err_str = str(stmt_err).lower()
                            if "duplicate column name" in err_str:
                                # Column already exists - idempotent operation, continue
                                logger.info(
                                    f"Migration {migration.version}: column already exists, skipping (idempotent)"
                                )
                            # Handle "no such table" - table doesn't exist, this is a real failure
                            elif "no such table" in err_str:
                                logger.warning(
                                    f"Migration {migration.version}: table doesn't exist, skipping"
                                )
                                statements_failed = True
                            else:
                                raise
            except Exception as e:
                logger.error(f"Migration {migration.version} failed: {e}")
                raise MigrationError(
                    f"Migration {migration.version} failed: {e}"
                ) from e
            
            # If any statement was skipped, do NOT record the migration as complete
            # Migration must be atomic - all statements must succeed
            if statements_failed:
                raise MigrationError(
                    f"Migration {migration.version} had skipped statements - "
                    f"migration is not atomic and was not recorded as complete"
                )
            
            # Record the migration only if all statements succeeded
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
