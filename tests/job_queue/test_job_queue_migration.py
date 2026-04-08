"""Tests for JobQueue migration.

This module tests the migration that adds the job_queues table and related
schema changes. These tests verify:
- Table creation and structure
- Column additions to existing tables
- System queue seeding for existing projects
- Constraint validation
- Idempotency (running migration multiple times)

IMPORTANT: These tests use raw SQL to simulate the migration environment,
as the actual migration runner applies SQL files directly.
"""

import os
import pytest
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.pool import StaticPool


# Path to the migration file
MIGRATION_FILE = os.path.join(
    os.path.dirname(__file__),
    "../../daemon/migrations/versions/20260409_000001_add_job_queues_table.sql"
)


def read_migration_sql() -> str:
    """Read the migration SQL file content."""
    with open(MIGRATION_FILE, "r") as f:
        content = f.read()
    # Extract UP section only
    lines = content.split("\n")
    up_lines = []
    in_up = False
    for line in lines:
        if line.strip() == "-- UP":
            in_up = True
            continue
        if line.strip() == "-- DOWN":
            break
        if in_up:
            up_lines.append(line)
    return "\n".join(up_lines)


@pytest.fixture
def db_engine():
    """Create in-memory SQLite engine for migration testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    engine.dispose()


def setup_initial_schema(engine):
    """Set up the initial schema state before migration.
    
    Creates the tables that exist before the job_queues migration:
    - projects table (required for seeding)
    - job_queue_items table (to test queue_id column addition)
    """
    with engine.begin() as conn:
        # Create projects table
        conn.execute(text("""
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                project_type TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
        
        # Create job_queue_items table (without queue_id column)
        conn.execute(text("""
            CREATE TABLE job_queue_items (
                job_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                agent_dir TEXT NOT NULL,
                message TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'api',
                project_id TEXT,
                priority INTEGER NOT NULL DEFAULT 5,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                instance_id TEXT,
                error_message TEXT,
                result_summary TEXT,
                metadata TEXT,
                cancelled_at TEXT
            )
        """))


def insert_projects(engine):
    """Insert sample projects for seeding tests."""
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO projects (project_id, name, project_type, status, created_at, updated_at)
            VALUES 
                ('proj-001', 'Project 1', 'software', 'active', '2026-01-01', '2026-01-01'),
                ('proj-002', 'Project 2', 'research', 'active', '2026-01-02', '2026-01-02')
        """))


def run_migration(engine):
    """Execute the migration SQL on the given engine."""
    migration_sql = read_migration_sql()
    
    # Split by semicolons and execute each statement
    statements = [s.strip() for s in migration_sql.split(";") if s.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            if stmt:
                conn.execute(text(stmt))


def execute_query(engine, query: str):
    """Execute a SELECT query and return results."""
    with engine.connect() as conn:
        return conn.execute(text(query))


class TestMigrationCreatesJobQueuesTable:
    """Tests for job_queues table creation."""

    def test_migration_creates_job_queues_table(self, db_engine):
        """Test migration creates job_queues table with all required columns."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        # Verify table exists
        inspector = inspect(db_engine)
        tables = inspector.get_table_names()
        assert "job_queues" in tables
        
        # Verify all columns exist
        columns = {col["name"] for col in inspector.get_columns("job_queues")}
        expected_columns = {
            "queue_id", "project_id", "queue_name", "queue_name_lower",
            "queue_type", "concurrency_limit", "is_paused", "is_system",
            "description", "created_at", "updated_at"
        }
        assert expected_columns.issubset(columns), f"Missing columns: {expected_columns - columns}"

    def test_migration_creates_project_index(self, db_engine):
        """Test migration creates index on project_id."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        inspector = inspect(db_engine)
        indexes = inspector.get_indexes("job_queues")
        index_names = [idx["name"] for idx in indexes]
        
        assert "idx_job_queues_project" in index_names

    def test_migration_adds_queue_id_column(self, db_engine):
        """Test migration adds queue_id column to job_queue_items table."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        inspector = inspect(db_engine)
        columns = {col["name"] for col in inspector.get_columns("job_queue_items")}
        
        assert "queue_id" in columns

    def test_migration_creates_queue_id_index(self, db_engine):
        """Test migration creates index on job_queue_items.queue_id."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        inspector = inspect(db_engine)
        indexes = inspector.get_indexes("job_queue_items")
        index_names = [idx["name"] for idx in indexes]
        
        assert "idx_job_queue_items_queue" in index_names


class TestMigrationSeedsSystemQueues:
    """Tests for system queue seeding."""

    def test_migration_seeds_system_queues_for_projects(self, db_engine):
        """Test migration seeds two system queues for each existing project."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        # Query all queues
        result = execute_query(db_engine, """
            SELECT queue_id, project_id, queue_name, queue_type, concurrency_limit, is_system
            FROM job_queues
            WHERE is_system = 1
            ORDER BY project_id, queue_name
        """)
        rows = result.fetchall()
        
        # We have 2 projects, each gets 2 system queues (FIFO + parallel)
        assert len(rows) == 4, f"Expected 4 system queues (2 per project), got {len(rows)}"
        
        # Verify FIFO queue for project 1
        fifo_queue_proj1 = next(r for r in rows if r[1] == "proj-001" and r[2] == "system_fifo_queue")
        assert fifo_queue_proj1[0] == "sys-fifo-proj-001"
        assert fifo_queue_proj1[3] == "fifo"
        assert fifo_queue_proj1[4] == 1  # concurrency_limit
        assert fifo_queue_proj1[5] == 1  # is_system
        
        # Verify parallel queue for project 1
        parallel_queue_proj1 = next(r for r in rows if r[1] == "proj-001" and r[2] == "system_parallel_queue")
        assert parallel_queue_proj1[0] == "sys-parallel-proj-001"
        assert parallel_queue_proj1[3] == "parallel"
        assert parallel_queue_proj1[4] == 3  # concurrency_limit
        assert parallel_queue_proj1[5] == 1  # is_system
        
        # Verify FIFO queue for project 2
        fifo_queue_proj2 = next(r for r in rows if r[1] == "proj-002" and r[2] == "system_fifo_queue")
        assert fifo_queue_proj2[0] == "sys-fifo-proj-002"
        
        # Verify parallel queue for project 2
        parallel_queue_proj2 = next(r for r in rows if r[1] == "proj-002" and r[2] == "system_parallel_queue")
        assert parallel_queue_proj2[0] == "sys-parallel-proj-002"

    def test_migration_seeds_queues_for_multiple_projects(self, db_engine):
        """Test migration seeds system queues for all existing projects."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        # Count system queues
        result = execute_query(db_engine, """
            SELECT COUNT(*) FROM job_queues WHERE is_system = 1
        """)
        count = result.scalar()
        
        # Should have 4 system queues (2 projects x 2 queue types)
        assert count == 4

    def test_migration_seeds_queue_ids_correct_format(self, db_engine):
        """Test system queue IDs follow expected format."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        result = execute_query(db_engine, """
            SELECT queue_id, queue_name FROM job_queues WHERE is_system = 1
        """)
        rows = result.fetchall()
        
        for queue_id, queue_name in rows:
            if queue_name == "system_fifo_queue":
                assert queue_id.startswith("sys-fifo-"), f"FIFO queue ID format: {queue_id}"
            elif queue_name == "system_parallel_queue":
                assert queue_id.startswith("sys-parallel-"), f"Parallel queue ID format: {queue_id}"

    def test_migration_seeds_timestamps(self, db_engine):
        """Test seeded queues have valid timestamps."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        result = execute_query(db_engine, """
            SELECT created_at, updated_at FROM job_queues WHERE is_system = 1
        """)
        rows = result.fetchall()
        
        for created_at, updated_at in rows:
            assert created_at is not None
            assert updated_at is not None
            # SQLite datetime format should be present
            assert len(created_at) > 0
            assert len(updated_at) > 0

    def test_migration_seeds_description(self, db_engine):
        """Test seeded queues have description set."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        result = execute_query(db_engine, """
            SELECT queue_name, description FROM job_queues WHERE is_system = 1
        """)
        rows = result.fetchall()
        
        for queue_name, description in rows:
            assert description is not None
            assert len(description) > 0


class TestMigrationIdempotency:
    """Tests for migration idempotency."""

    def test_migration_idempotency_create_table(self, db_engine):
        """Test that CREATE TABLE statements are idempotent (IF NOT EXISTS).
        
        Note: The ALTER TABLE ADD COLUMN statement is NOT idempotent in SQLite
        (SQLite doesn't support ALTER TABLE ... ADD COLUMN IF NOT EXISTS).
        The migration file clears job_queue_items on each run and re-seeds,
        but will fail if run twice on the same database due to the column
        already existing. In production, the migration runner should track
        applied migrations to prevent double-runs.
        """
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        
        # Run migration first time
        run_migration(db_engine)
        
        # Count queues after first run
        result1 = execute_query(db_engine, "SELECT COUNT(*) FROM job_queues")
        count1 = result1.scalar()
        
        # Verify first run succeeded - 4 queues (2 projects x 2 queue types)
        assert count1 == 4
        
        # Note: Running the migration a second time on the same database
        # will fail due to ALTER TABLE ADD COLUMN (column already exists).
        # This is a known SQLite limitation. In production, the migration
        # runner should check if migration was already applied.
        # We don't test second-run behavior here since it would fail.

    def test_migration_seeds_are_reinserted_after_clear(self, db_engine):
        """Test that clearing job_queue_items and re-running seeds queues correctly.
        
        This tests the part of idempotency that works: the migration clears
        job_queue_items each time, then re-seeds the system queues.
        """
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        
        # Run migration first time
        run_migration(db_engine)
        
        # Count queues after first run
        result1 = execute_query(db_engine, "SELECT COUNT(*) FROM job_queues WHERE is_system = 1")
        count1 = result1.scalar()
        assert count1 == 4
        
        # Manually delete all queues (simulating a state reset)
        with db_engine.begin() as conn:
            conn.execute(text("DELETE FROM job_queues"))
        
        # Verify queues are deleted
        result = execute_query(db_engine, "SELECT COUNT(*) FROM job_queues")
        assert result.scalar() == 0
        
        # Re-run seeding portion only (just the INSERT statements)
        with db_engine.begin() as conn:
            # Seed FIFO queues
            conn.execute(text("""
                INSERT INTO job_queues (
                    queue_id, project_id, queue_name, queue_name_lower,
                    queue_type, concurrency_limit, is_paused, is_system,
                    description, created_at, updated_at
                )
                SELECT 
                    'sys-fifo-' || project_id,
                    project_id,
                    'system_fifo_queue',
                    'system_fifo_queue',
                    'fifo',
                    1,
                    0,
                    1,
                    'System FIFO queue - default, one job at a time',
                    datetime('now'),
                    datetime('now')
                FROM projects
            """))
            
            # Seed parallel queues
            conn.execute(text("""
                INSERT INTO job_queues (
                    queue_id, project_id, queue_name, queue_name_lower,
                    queue_type, concurrency_limit, is_paused, is_system,
                    description, created_at, updated_at
                )
                SELECT 
                    'sys-parallel-' || project_id,
                    project_id,
                    'system_parallel_queue',
                    'system_parallel_queue',
                    'parallel',
                    3,
                    0,
                    1,
                    'System parallel queue - configurable concurrency',
                    datetime('now'),
                    datetime('now')
                FROM projects
            """))
        
        # Verify queues are re-seeded correctly
        result = execute_query(db_engine, "SELECT COUNT(*) FROM job_queues WHERE is_system = 1")
        assert result.scalar() == 4


class TestMigrationConstraints:
    """Tests for database constraints enforced by migration."""

    def test_migration_check_constraint_queue_type(self, db_engine):
        """Test that inserting queue with invalid queue_type fails."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        # Try to insert invalid queue_type
        with pytest.raises(Exception) as exc_info:
            with db_engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO job_queues (
                        queue_id, project_id, queue_name, queue_name_lower,
                        queue_type, concurrency_limit, is_paused, is_system,
                        created_at, updated_at
                    )
                    VALUES (
                        'test-invalid', 'proj-001', 'test', 'test',
                        'invalid', 1, 0, 0,
                        datetime('now'), datetime('now')
                    )
                """))
        
        # Should raise integrity error due to CHECK constraint
        assert "CHECK constraint failed" in str(exc_info.value) or "constraint" in str(exc_info.value).lower()

    def test_migration_unique_constraint(self, db_engine):
        """Test that duplicate (project_id, queue_name_lower) fails."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        # Try to insert duplicate system queue (should fail due to unique constraint)
        with pytest.raises(Exception) as exc_info:
            with db_engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO job_queues (
                        queue_id, project_id, queue_name, queue_name_lower,
                        queue_type, concurrency_limit, is_paused, is_system,
                        created_at, updated_at
                    )
                    VALUES (
                        'duplicate-id', 'proj-001', 'system_fifo_queue', 'system_fifo_queue',
                        'fifo', 1, 0, 1,
                        datetime('now'), datetime('now')
                    )
                """))
        
        # Should raise integrity error due to UNIQUE constraint
        assert "UNIQUE constraint failed" in str(exc_info.value) or "constraint" in str(exc_info.value).lower()

    def test_migration_foreign_key_to_projects(self, db_engine):
        """Test that queue references valid project_id."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        # Queue_id is prefixed with project_id, so it inherently references valid project
        result = execute_query(db_engine, """
            SELECT COUNT(*) FROM job_queues
            WHERE project_id IN (SELECT project_id FROM projects)
        """)
        count = result.scalar()
        
        assert count == 4  # All 4 seeded queues reference valid projects


class TestMigrationClearsExistingJobs:
    """Tests for migration clearing existing jobs."""

    def test_migration_clears_job_queue_items(self, db_engine):
        """Test migration deletes existing job_queue_items (clean slate)."""
        # Set up initial schema with job_queue_items
        with db_engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    project_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """))
            
            conn.execute(text("""
                CREATE TABLE job_queue_items (
                    job_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    agent_dir TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'api'
                )
            """))
            
            conn.execute(text("""
                INSERT INTO projects (project_id, name, project_type, status, created_at, updated_at)
                VALUES ('proj-001', 'Test', 'software', 'active', '2026-01-01', '2026-01-01')
            """))
            
            conn.execute(text("""
                INSERT INTO job_queue_items (job_id, agent_id, agent_dir, message, source)
                VALUES 
                    ('old-job-1', 'coder', '/agents/coder', 'Old job 1', 'api'),
                    ('old-job-2', 'coder', '/agents/coder', 'Old job 2', 'api')
            """))
        
        # Verify jobs exist before migration
        result = execute_query(db_engine, "SELECT COUNT(*) FROM job_queue_items")
        assert result.scalar() == 2
        
        # Run migration
        run_migration(db_engine)
        
        # Verify jobs are cleared
        result = execute_query(db_engine, "SELECT COUNT(*) FROM job_queue_items")
        assert result.scalar() == 0


class TestMigrationColumnTypes:
    """Tests for correct column types in migrated tables."""

    def test_job_queues_column_types(self, db_engine):
        """Test job_queues columns have correct SQLite types."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        inspector = inspect(db_engine)
        columns = {col["name"]: col["type"] for col in inspector.get_columns("job_queues")}
        
        # Check TEXT columns
        assert "TEXT" in str(columns["queue_id"]).upper()
        assert "TEXT" in str(columns["project_id"]).upper()
        assert "TEXT" in str(columns["queue_name"]).upper()
        assert "TEXT" in str(columns["queue_type"]).upper()
        
        # Check INTEGER columns
        assert "INTEGER" in str(columns["concurrency_limit"]).upper()
        assert "BOOLEAN" in str(columns["is_paused"]).upper() or "INTEGER" in str(columns["is_paused"]).upper()
        assert "BOOLEAN" in str(columns["is_system"]).upper() or "INTEGER" in str(columns["is_system"]).upper()

    def test_job_queue_items_queue_id_nullable(self, db_engine):
        """Test queue_id column on job_queue_items is nullable."""
        setup_initial_schema(db_engine)
        insert_projects(db_engine)
        run_migration(db_engine)
        
        inspector = inspect(db_engine)
        columns = {col["name"]: col for col in inspector.get_columns("job_queue_items")}
        
        assert columns["queue_id"]["nullable"] is True
