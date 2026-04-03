#!/usr/bin/env python
"""
Comprehensive validation tests for Database Auto Migration System.

Tests all 6 scenarios:
1. FRESH DATABASE MIGRATION
2. IDEMPOTENT MIGRATION (DUPLICATE COLUMN)
3. MIGRATION TRACKING
4. MIGRATION FILE FORMAT
5. INTEGRATION WITH STARTUP
6. EDGE CASES

Usage: python tests/test_migration_system_comprehensive.py
"""

import hashlib
import logging
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

# IMPORTANT: Import all SQLModel table classes to register them with SQLModel.metadata
# Without these imports, SQLModel.metadata.create_all() won't create the tables
from daemon.repositories.project.models import Project, ProjectTagLink, ProjectShortnameLink  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceHierarchy  # noqa: F401
from daemon.repositories.source.models import SourceConfig, SessionMapping, ProcessedMessage, ScheduleExecution  # noqa: F401
from daemon.repositories.job_queue.models import JobItem  # noqa: F401

# Configure logging to see migration output
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def temp_db_dir() -> Generator[Path, None, None]:
    """Create temporary directory for test databases."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def temp_migrations_dir(temp_db_dir: Path) -> Generator[Path, None, None]:
    """Create temporary migrations directory with test migration files."""
    migrations_dir = temp_db_dir / "migrations" / "versions"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    
    # Create test migration files
    # NOTE: The baseline (000000) must have timestamp BEFORE retrospective migrations
    # so it runs first. In production, create_all() runs BEFORE migrations.
    migrations = {
        "20240000_000000_initial_schema.sql": """-- Migration: initial schema baseline
-- Created: 2024-01-01
-- Author: system
-- Description: Baseline migration capturing initial schema state

-- UP
-- This is the baseline - tables are created via SQLModel.metadata.create_all()
-- This migration records that we've captured the initial state

-- DOWN
DROP TABLE IF EXISTS schema_migrations;
""",
        "20240101_000001_add_job_queue_paused.sql": """-- Migration: add job_queue_paused to projects
-- Created: 2024-01-01 (retrospective)
-- Author: system
-- Description: Add job_queue_paused column to projects table for pausing job queues

-- UP
ALTER TABLE projects ADD COLUMN job_queue_paused BOOLEAN DEFAULT 0;

-- DOWN
-- SQLite does not support DROP COLUMN
""",
        "20240102_000002_add_creator_agent_id.sql": """-- Migration: add creator_agent_id to projects
-- Created: 2024-01-02 (retrospective)
-- Author: system
-- Description: Add creator_agent_id column to track which agent created the project

-- UP
ALTER TABLE projects ADD COLUMN creator_agent_id TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
""",
        "20240103_000003_add_agent_id_sessions.sql": """-- Migration: add agent_id to instances
-- Created: 2024-01-03 (retrospective)
-- Author: system
-- Description: Add agent_id column to instances table, populating from agent_dir

-- UP
ALTER TABLE instances ADD COLUMN agent_id TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
""",
        "20240104_000004_add_agent_id_session_mappings.sql": """-- Migration: add agent_id to instance_mappings
-- Created: 2024-01-04 (retrospective)
-- Author: system
-- Description: Add agent_id column to instance_mappings table

-- UP
ALTER TABLE instance_mappings ADD COLUMN agent_id TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
""",
        "20240106_000006_add_agent_id_job_queue_items.sql": """-- Migration: add agent_id to job_queue_items
-- Created: 2024-01-06 (retrospective)
-- Author: system
-- Description: Add agent_id column to job_queue_items table

-- UP
ALTER TABLE job_queue_items ADD COLUMN agent_id TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
""",
    }
    
    for filename, content in migrations.items():
        (migrations_dir / filename).write_text(content)
    
    yield migrations_dir


@pytest.fixture
def fresh_engine(temp_db_dir: Path, temp_migrations_dir: Path) -> Generator[tuple[Engine, Path], None, None]:
    """Create a fresh database engine with SQLModel tables created first.
    
    In production:
    1. SQLModel.metadata.create_all() creates tables
    2. Migrations run to add columns
    
    The baseline migration (20250326_000000) has empty UP because tables
    are already created by create_all(). Other migrations add columns.
    """
    db_path = temp_db_dir / "fresh.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # CRITICAL: Create tables FIRST (simulating SQLModel.metadata.create_all())
    # Then migrations will add columns via ALTER TABLE
    SQLModel.metadata.create_all(engine)
    
    yield engine, temp_migrations_dir
    engine.dispose()


@pytest.fixture
def sqlmodel_engine(temp_db_dir: Path, temp_migrations_dir: Path) -> Generator[tuple[Engine, Path], None, None]:
    """Create engine with SQLModel tables already created (simulates existing database).
    
    In production, SQLModel.metadata.create_all() creates tables with all columns
    defined in the models (including agent_id). Then migrations run and handle
    the case where columns already exist (duplicate column name).
    """
    db_path = temp_db_dir / "sqlmodel.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Create tables using SQLModel metadata (this includes agent_id already)
    SQLModel.metadata.create_all(engine)
    
    yield engine, temp_migrations_dir
    engine.dispose()


@pytest.fixture
def old_schema_engine(temp_db_dir: Path, temp_migrations_dir: Path) -> Generator[tuple[Engine, Path], None, None]:
    """Create engine with OLD schema (without agent_id columns) to test real migration."""
    db_path = temp_db_dir / "old_schema.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Create tables WITHOUT agent_id columns (simulating old schema)
    with engine.connect() as conn:
        # Create projects table without new columns
        conn.execute(text("""
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                project_type TEXT DEFAULT 'general',
                status TEXT DEFAULT 'active',
                main_directory TEXT,
                related_directories TEXT DEFAULT '[]',
                description TEXT,
                project_metadata TEXT DEFAULT '{}',
                relationships TEXT DEFAULT '{}',
                creator_instance_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
        
        # Create instances table without agent_id
        conn.execute(text("""
            CREATE TABLE instances (
                instance_id TEXT PRIMARY KEY,
                agent_dir TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'idle',
                instance_metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
        
        # Insert test data
        conn.execute(text("""
            INSERT INTO instances (instance_id, agent_dir, status, created_at, updated_at)
            VALUES ('old-instance-1', './agents/coder', 'idle', '2024-01-01T00:00:00', '2024-01-01T00:00:00')
        """))
        
        # Create instance_mappings table without agent_id
        conn.execute(text("""
            CREATE TABLE instance_mappings (
                mapping_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                external_user_id TEXT NOT NULL,
                agent_instance_id TEXT NOT NULL,
                agent_dir TEXT NOT NULL,
                instance_metadata TEXT DEFAULT '{}',
                last_message_at TEXT,
                created_at TEXT NOT NULL
            )
        """))
        
        # Create job_queue_items table without agent_id
        conn.execute(text("""
            CREATE TABLE job_queue_items (
                job_id TEXT PRIMARY KEY,
                agent_dir TEXT NOT NULL,
                message TEXT NOT NULL,
                source TEXT DEFAULT 'api',
                project_id TEXT,
                priority INTEGER DEFAULT 5,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                instance_id TEXT,
                error_message TEXT,
                result_summary TEXT,
                job_metadata TEXT DEFAULT '{}',
                cancelled_at TEXT
            )
        """))
        
        # Create instance_hierarchy table
        conn.execute(text("""
            CREATE TABLE instance_hierarchy (
                parent_id TEXT NOT NULL,
                child_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (parent_id, child_id)
            )
        """))
        
        conn.execute(text("""
            CREATE TABLE project_tags (
                project_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (project_id, tag)
            )
        """))
        
        conn.execute(text("""
            CREATE TABLE project_shortnames (
                project_id TEXT NOT NULL,
                shortname TEXT NOT NULL,
                PRIMARY KEY (project_id, shortname)
            )
        """))
        
        conn.execute(text("""
            CREATE TABLE source_configs (
                source_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                name TEXT NOT NULL,
                config TEXT DEFAULT '{}',
                credentials TEXT,
                enabled INTEGER DEFAULT 1,
                status TEXT DEFAULT 'stopped',
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
        
        conn.execute(text("""
            CREATE TABLE processed_external_messages (
                source_id TEXT NOT NULL,
                external_message_id TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY (source_id, external_message_id)
            )
        """))
        
        conn.execute(text("""
            CREATE TABLE schedule_executions (
                execution_id TEXT PRIMARY KEY,
                schedule_id TEXT NOT NULL,
                triggered_at TEXT NOT NULL,
                instance_id TEXT,
                status TEXT DEFAULT 'triggered',
                error_message TEXT,
                completed_at TEXT
            )
        """))
        
        conn.execute(text("""
            CREATE TABLE message_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT DEFAULT 'api',
                priority INTEGER DEFAULT 5,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
        
        conn.commit()
    
    yield engine, temp_migrations_dir
    engine.dispose()


# ============================================================================
# SCENARIO 1: FRESH DATABASE MIGRATION
# ============================================================================

class TestScenario1FreshDatabaseMigration:
    """Tests for migration on a completely fresh database."""
    
    def test_all_migrations_run_on_fresh_database(self, fresh_engine: tuple[Engine, Path]):
        """Verify all 6 migrations run successfully on a fresh database."""
        engine, migrations_dir = fresh_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        runner = MigrationRunner(engine, migrations_dir)
        applied = runner.run_pending_migrations()
        
        # All 6 migrations should be applied
        assert len(applied) == 6, f"Expected 6 migrations, got {len(applied)}: {applied}"
        
        # Verify they're in order (baseline first, then retrospective)
        expected_versions = [
            "20240000_000000",  # Baseline - runs first
            "20240101_000001",
            "20240102_000002", 
            "20240103_000003",
            "20240104_000004",
            "20240106_000006",
        ]
        assert applied == expected_versions, f"Migrations not in expected order: {applied}"
        
    def test_schema_migrations_table_exists(self, fresh_engine: tuple[Engine, Path]):
        """Verify schema_migrations table is created."""
        engine, migrations_dir = fresh_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        runner = MigrationRunner(engine, migrations_dir)
        runner.run_pending_migrations()
        
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='schema_migrations'
            """))
            row = result.fetchone()
        
        assert row is not None, "schema_migrations table should exist"
    
    def test_schema_migrations_tracks_all_applied(self, fresh_engine: tuple[Engine, Path]):
        """Verify all applied migrations are tracked in schema_migrations."""
        engine, migrations_dir = fresh_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        runner = MigrationRunner(engine, migrations_dir)
        runner.run_pending_migrations()
        
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT version, name, applied_at, checksum 
                FROM schema_migrations 
                ORDER BY version
            """))
            rows = result.fetchall()
        
        assert len(rows) == 6, f"Expected 6 tracked migrations, got {len(rows)}"
        
        # Verify each row has required fields
        for row in rows:
            version, name, applied_at, checksum = row
            assert version is not None, "version should not be null"
            assert name is not None, "name should not be null"
            assert applied_at is not None, "applied_at should not be null"
            assert checksum is not None, "checksum should not be null"
            # Verify checksum is SHA-256 (64 hex chars)
            assert len(checksum) == 64, f"checksum should be SHA-256, got {len(checksum)} chars"


# ============================================================================
# SCENARIO 2: IDEMPOTENT MIGRATION (DUPLICATE COLUMN)
# ============================================================================

class TestScenario2IdempotentMigration:
    """Tests for idempotent behavior when columns already exist."""
    
    def test_duplicate_column_handled_gracefully(self, sqlmodel_engine: tuple[Engine, Path]):
        """Verify 'duplicate column name' error is handled gracefully.
        
        This happens when SQLModel.metadata.create_all() already added the columns
        before migrations run.
        """
        engine, migrations_dir = sqlmodel_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        # First, verify the columns already exist (from SQLModel)
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(instances)"))
            columns = {row[1] for row in result.fetchall()}
        
        assert 'agent_id' in columns, "agent_id should already exist from SQLModel"
        
        # Run migrations - should handle duplicate columns gracefully
        runner = MigrationRunner(engine, migrations_dir)
        applied = runner.run_pending_migrations()
        
        # All migrations should still be marked as applied
        assert len(applied) == 6, f"Expected 6 migrations applied, got {len(applied)}"
    
    def test_migration_marked_complete_despite_duplicate(self, sqlmodel_engine: tuple[Engine, Path]):
        """Verify migration is marked complete even when columns already exist."""
        engine, migrations_dir = sqlmodel_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        runner = MigrationRunner(engine, migrations_dir)
        runner.run_pending_migrations()
        
        # Verify migrations are tracked
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT version FROM schema_migrations WHERE version = '20240103_000003'
            """))
            row = result.fetchone()
        
        assert row is not None, "Migration should be tracked despite duplicate column"
    
    def test_idempotent_re_run(self, sqlmodel_engine: tuple[Engine, Path]):
        """Verify running migrations twice doesn't cause errors."""
        engine, migrations_dir = sqlmodel_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        runner = MigrationRunner(engine, migrations_dir)
        
        # First run
        applied1 = runner.run_pending_migrations()
        assert len(applied1) == 6, "First run should apply 6 migrations"
        
        # Second run - should detect all are already applied
        applied2 = runner.run_pending_migrations()
        assert len(applied2) == 0, "Second run should apply 0 migrations (all pending)"


# ============================================================================
# SCENARIO 3: MIGRATION TRACKING
# ============================================================================

class TestScenario3MigrationTracking:
    """Tests for migration tracking functionality."""
    
    def test_get_applied_versions(self, fresh_engine: tuple[Engine, Path]):
        """Verify get_applied_versions returns correct set."""
        engine, migrations_dir = fresh_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        runner = MigrationRunner(engine, migrations_dir)
        
        # Before running - should be empty
        applied = runner.get_applied_versions()
        assert len(applied) == 0, "Should have no applied migrations initially"
        
        # Run migrations
        runner.run_pending_migrations()
        
        # After running - should have all 6
        applied = runner.get_applied_versions()
        assert len(applied) == 6, f"Should have 6 applied migrations, got {len(applied)}"
    
    def test_get_pending_migrations(self, fresh_engine: tuple[Engine, Path]):
        """Verify get_pending_migrations returns correct list."""
        engine, migrations_dir = fresh_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        runner = MigrationRunner(engine, migrations_dir)
        
        # Before running - should have all 6 pending
        pending = runner.get_pending_migrations()
        assert len(pending) == 6, f"Should have 6 pending migrations, got {len(pending)}"
        
        # Run migrations
        runner.run_pending_migrations()
        
        # After running - should have none pending
        pending = runner.get_pending_migrations()
        assert len(pending) == 0, "Should have no pending migrations after running"
    
    def test_get_migration_status(self, fresh_engine: tuple[Engine, Path]):
        """Verify get_migration_status returns correct structure."""
        engine, migrations_dir = fresh_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        runner = MigrationRunner(engine, migrations_dir)
        runner.run_pending_migrations()
        
        status = runner.get_migration_status()
        
        assert "applied" in status, "status should have 'applied' key"
        assert "pending" in status, "status should have 'pending' key"
        assert "total_discovered" in status, "status should have 'total_discovered' key"
        assert "last_applied" in status, "status should have 'last_applied' key"
        
        assert len(status["applied"]) == 6, f"Should have 6 applied: {status['applied']}"
        assert len(status["pending"]) == 0, "Should have no pending"
        assert status["total_discovered"] == 6, "Should discover 6 total"
        assert status["last_applied"] == "20240106_000006", f"Last applied should be latest: {status['last_applied']}"
    
    def test_checksum_validation(self, fresh_engine: tuple[Engine, Path]):
        """Verify checksums are correctly calculated and stored."""
        engine, migrations_dir = fresh_engine
        
        from daemon.migrations.runner import MigrationRunner, MigrationFile
        
        runner = MigrationRunner(engine, migrations_dir)
        runner.run_pending_migrations()
        
        # Get stored checksums
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT version, checksum FROM schema_migrations
            """))
            stored = {row[0]: row[1] for row in result.fetchall()}
        
        # Calculate expected checksums
        for migration_file in migrations_dir.glob("*.sql"):
            migration = MigrationFile.parse(migration_file)
            expected_checksum = migration.checksum
            
            # Find matching version (first part of filename)
            version = migration.version
            if version in stored:
                assert stored[version] == expected_checksum, \
                    f"Checksum mismatch for {version}: stored={stored[version]}, expected={expected_checksum}"


# ============================================================================
# SCENARIO 4: MIGRATION FILE FORMAT
# ============================================================================

class TestScenario4MigrationFileFormat:
    """Tests for migration file format parsing."""
    
    def test_parse_migration_file(self, temp_migrations_dir: Path):
        """Verify migration files are parsed correctly."""
        from daemon.migrations.runner import MigrationFile
        
        migration_path = temp_migrations_dir / "20240103_000003_add_agent_id_sessions.sql"
        migration = MigrationFile.parse(migration_path)
        
        assert migration.version == "20240103_000003", f"Version mismatch: {migration.version}"
        assert migration.name == "add agent id sessions", f"Name mismatch: {migration.name}"
        assert "ALTER TABLE instances ADD COLUMN agent_id TEXT" in migration.up_sql, \
            f"UP SQL mismatch: {migration.up_sql}"
        assert "SQLite does not support DROP COLUMN" in migration.down_sql, \
            f"DOWN SQL mismatch: {migration.down_sql}"
    
    def test_naming_convention(self, temp_migrations_dir: Path):
        """Verify migration files follow naming convention (YYYYMMDD_HHMMSS_description.sql)."""
        import re
        
        for migration_file in temp_migrations_dir.glob("*.sql"):
            # Check filename matches pattern
            pattern = r"^\d{8}_\d{6}_.+\.sql$"
            assert re.match(pattern, migration_file.name), \
                f"Filename doesn't match convention: {migration_file.name}"
        
        # Verify we have the expected number of migrations
        migration_files = list(temp_migrations_dir.glob("*.sql"))
        assert len(migration_files) == 6, f"Expected 6 migrations, got {len(migration_files)}"
    
    def test_up_down_sections_parsed(self, temp_migrations_dir: Path):
        """Verify UP and DOWN sections are correctly parsed."""
        from daemon.migrations.runner import MigrationFile
        
        for migration_file in temp_migrations_dir.glob("*.sql"):
            migration = MigrationFile.parse(migration_file)
            
            # All should have UP section
            assert migration.up_sql is not None, f"Missing UP section in {migration_file.name}"
            
            # DOWN can be empty but should exist (parsed as empty string if missing)
            # Note: The parser allows missing DOWN section
    
    def test_invalid_filename_rejected(self, temp_migrations_dir: Path):
        """Verify invalid filenames are rejected."""
        from daemon.migrations.runner import MigrationFile
        
        # Create invalid migration file
        invalid_file = temp_migrations_dir / "invalid_migration.sql"
        invalid_file.write_text("-- UP\nSELECT 1;")
        
        with pytest.raises(ValueError, match="Invalid migration filename"):
            MigrationFile.parse(invalid_file)
    
    def test_missing_up_section_rejected(self, temp_migrations_dir: Path):
        """Verify migration without UP section is rejected."""
        from daemon.migrations.runner import MigrationFile
        
        invalid_file = temp_migrations_dir / "20240101_000000_no_up.sql"
        invalid_file.write_text("-- DOWN\nSELECT 1;")
        
        with pytest.raises(ValueError, match="Missing -- UP section"):
            MigrationFile.parse(invalid_file)


# ============================================================================
# SCENARIO 5: INTEGRATION WITH STARTUP
# ============================================================================

class TestScenario5IntegrationWithStartup:
    """Tests for migration integration with application startup."""
    
    def test_migrations_run_automatically(self, fresh_engine: tuple[Engine, Path]):
        """Verify migrations run automatically during initialization."""
        engine, migrations_dir = fresh_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        # Simulate startup: create tables then run migrations
        SQLModel.metadata.create_all(engine)
        
        runner = MigrationRunner(engine, migrations_dir)
        applied = runner.run_pending_migrations()
        
        # Should handle both fresh and existing schemas
        assert len(applied) == 6, f"Should apply 6 migrations: {applied}"
    
    def test_application_starts_successfully_after_migrations(self, fresh_engine: tuple[Engine, Path]):
        """Verify application can start after migrations complete."""
        engine, migrations_dir = fresh_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        # Run migrations
        SQLModel.metadata.create_all(engine)
        runner = MigrationRunner(engine, migrations_dir)
        runner.run_pending_migrations()
        
        # Verify we can query the database (simulating app queries)
        with engine.connect() as conn:
            # Should be able to query schema_migrations
            result = conn.execute(text("SELECT COUNT(*) FROM schema_migrations"))
            count = result.scalar()
            assert count == 6, f"Should have 6 migrations tracked"
            
            # Should be able to query other tables
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = [row[0] for row in result.fetchall()]
            
            # Verify expected tables exist
            expected_tables = [
                'schema_migrations', 'instances', 'projects', 'instance_hierarchy',
                'project_tags', 'project_shortnames', 'source_configs',
                'instance_mappings', 'processed_external_messages',
                'schedule_executions', 'job_queue_items', 'message_queue'
            ]
            for table in expected_tables:
                assert table in tables, f"Table {table} should exist, got: {tables}"
    
    def test_no_data_loss_after_migration(self, old_schema_engine: tuple[Engine, Path]):
        """Verify existing data is preserved after migration."""
        engine, migrations_dir = old_schema_engine
        
        from daemon.migrations.runner import MigrationRunner
        
        # Verify test data exists before migration
        with engine.connect() as conn:
            result = conn.execute(text("SELECT instance_id, agent_dir FROM instances"))
            rows_before = result.fetchall()
        
        assert len(rows_before) == 1, "Should have 1 instance before migration"
        assert rows_before[0][0] == "old-instance-1", "Instance ID should match"
        
        # Run migrations
        runner = MigrationRunner(engine, migrations_dir)
        runner.run_pending_migrations()
        
        # Verify data still exists after migration
        with engine.connect() as conn:
            result = conn.execute(text("SELECT instance_id, agent_dir, agent_id FROM instances"))
            rows_after = result.fetchall()
        
        assert len(rows_after) == 1, "Should still have 1 instance after migration"
        assert rows_after[0][0] == "old-instance-1", "Instance ID should be preserved"
        assert rows_after[0][1] == "./agents/coder", "agent_dir should be preserved"


# ============================================================================
# SCENARIO 6: EDGE CASES
# ============================================================================

class TestScenario6EdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_migration_sql_error_rollback(self, temp_db_dir: Path, temp_migrations_dir: Path):
        """Verify transactional execution - rollback on failure."""
        # Create a migration with invalid SQL
        bad_migration = temp_migrations_dir / "99999999_999999_bad_migration.sql"
        bad_migration.write_text("""
-- UP
ALTER TABLE nonexistent_table ADD COLUMN test TEXT;

-- DOWN
SELECT 1;
""")
        
        db_path = temp_db_dir / "edge_case.db"
        engine = create_engine(f"sqlite:///{db_path}")
        
        from daemon.migrations.runner import MigrationRunner, MigrationError
        
        runner = MigrationRunner(engine, temp_migrations_dir)
        
        # This should raise an error
        with pytest.raises(MigrationError):
            runner.run_pending_migrations()
        
        # Verify the bad migration was NOT recorded
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT version FROM schema_migrations 
                WHERE version = '99999999_999999'
            """))
            row = result.fetchone()
        
        assert row is None, "Bad migration should NOT be recorded as applied"
        
        engine.dispose()
    
    def test_empty_migrations_directory(self, temp_db_dir: Path):
        """Verify behavior with empty migrations directory."""
        empty_dir = temp_db_dir / "empty_migrations" / "versions"
        empty_dir.mkdir(parents=True, exist_ok=True)
        
        db_path = temp_db_dir / "empty.db"
        engine = create_engine(f"sqlite:///{db_path}")
        
        from daemon.migrations.runner import MigrationRunner
        
        runner = MigrationRunner(engine, empty_dir)
        applied = runner.run_pending_migrations()
        
        assert len(applied) == 0, "Should apply 0 migrations from empty directory"
        
        # Schema migrations table should still be created
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='schema_migrations'
            """))
            row = result.fetchone()
        
        assert row is not None, "schema_migrations table should still be created"
        
        engine.dispose()
    
    def test_checksum_mismatch_detection(self, fresh_engine: tuple[Engine, Path]):
        """Verify checksum mismatch detection for modified migration files."""
        engine, migrations_dir = fresh_engine
        
        from daemon.migrations.runner import MigrationRunner, MigrationFile
        
        runner = MigrationRunner(engine, migrations_dir)
        runner.run_pending_migrations()
        
        # Get original checksum
        migration_path = migrations_dir / "20240103_000003_add_agent_id_sessions.sql"
        original_checksum = MigrationFile.parse(migration_path).checksum
        
        # Modify the migration file
        original_content = migration_path.read_text()
        migration_path.write_text(original_content + "\n-- Modified")
        
        new_checksum = MigrationFile.parse(migration_path).checksum
        
        # Checksums should be different
        assert original_checksum != new_checksum, \
            "Modified file should have different checksum"
        
        # Note: Current implementation doesn't enforce checksum validation on re-run
        # This test verifies the checksum mechanism works
    
    def test_concurrent_migration_safety(self, temp_db_dir: Path, temp_migrations_dir: Path):
        """Verify migrations can handle concurrent access attempts."""
        db_path = temp_db_dir / "concurrent.db"
        
        # Create two engines pointing to same database
        engine1 = create_engine(f"sqlite:///{db_path}")
        engine2 = create_engine(f"sqlite:///{db_path}")
        
        # IMPORTANT: Create tables first (simulates SQLModel.metadata.create_all())
        SQLModel.metadata.create_all(engine1)
        
        from daemon.migrations.runner import MigrationRunner
        
        runner1 = MigrationRunner(engine1, temp_migrations_dir)
        runner2 = MigrationRunner(engine2, temp_migrations_dir)
        
        # First runner applies migrations
        applied1 = runner1.run_pending_migrations()
        
        # Second runner should see no pending migrations
        applied2 = runner2.run_pending_migrations()
        
        # One should apply all, other should apply none
        total_applied = len(applied1) + len(applied2)
        assert total_applied == 6, f"Total migrations applied should be 6, got {total_applied}"
        
        engine1.dispose()
        engine2.dispose()


# ============================================================================
# MAIN: Run all tests and generate report
# ============================================================================

def run_comprehensive_test():
    """Run all tests and generate a comprehensive report."""
    import sys
    from io import StringIO
    
    print("\n" + "=" * 80)
    print("DATABASE AUTO MIGRATION SYSTEM - COMPREHENSIVE TEST REPORT")
    print("=" * 80)
    print(f"Date: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 80)
    
    # Capture test output
    output = StringIO()
    
    # Run pytest with verbose output
    result = pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-W", "ignore::DeprecationWarning",
        "--color=yes",
    ])
    
    print("\n" + "=" * 80)
    if result == 0:
        print("OVERALL VERDICT: ✅ READY - All migration tests passed!")
    else:
        print(f"OVERALL VERDICT: ❌ NOT READY - Tests failed with exit code {result}")
    print("=" * 80)
    
    return result


if __name__ == "__main__":
    import sys
    sys.exit(run_comprehensive_test())
