"""Integration tests for Phase 3 migration — backfill NULL project_ids.

Tests verify the migration SQL (20260424_000001_backfill_null_project_ids.sql):
1. Creates system default project if it doesn't exist
2. Creates system FIFO queue for the system default project
3. Backfills job_queue_items with NULL project_id → system default
4. Backfills job_queue_items with empty string project_id → system default
5. Backfills dead_letter_items with NULL/empty project_id → system default
6. Assigns queue_id to orphaned jobs (project_id set but queue_id NULL)
7. Assigns queue_id to orphaned dead_letter_items

System default project UUID: 71931ae0-0f25-5fbf-853b-2a78cc978d7e
System FIFO queue ID:       sys-fifo-71931ae0-0f25-5fbf-853b-2a78cc978d7e

Run with:
    pytest tests/integration/test_migration.py -v
"""

import os
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

# Path to the migration file
MIGRATION_FILE = os.path.join(
    os.path.dirname(__file__),
    "../../daemon/migrations/versions/20260424_000001_backfill_null_project_ids.sql",
)

# Known system default project UUID
SYSTEM_DEFAULT_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
SYSTEM_FIFO_QUEUE_ID = "sys-fifo-71931ae0-0f25-5fbf-853b-2a78cc978d7e"


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


def run_migration(engine) -> None:
    """Execute the migration SQL on the given engine."""
    migration_sql = read_migration_sql()
    statements = [s.strip() for s in migration_sql.split(";") if s.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            if stmt:
                conn.execute(text(stmt))


def setup_full_schema(engine) -> None:
    """Set up a realistic schema with all required tables."""
    projects_sql = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project_type TEXT NOT NULL,
    status TEXT NOT NULL,
    description TEXT,
    metadata TEXT,
    relationships TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
    job_queues_sql = """
CREATE TABLE IF NOT EXISTS job_queues (
    queue_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    queue_name TEXT NOT NULL,
    queue_name_lower TEXT NOT NULL,
    queue_type TEXT NOT NULL,
    concurrency_limit INTEGER NOT NULL DEFAULT 1,
    is_paused INTEGER NOT NULL DEFAULT 0,
    is_system INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
    job_items_sql = """
CREATE TABLE IF NOT EXISTS job_queue_items (
    job_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    agent_dir TEXT NOT NULL,
    message TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'api',
    project_id TEXT,
    queue_id TEXT,
    priority INTEGER NOT NULL DEFAULT 5,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    instance_id TEXT,
    error_message TEXT,
    result_summary TEXT,
    job_metadata TEXT,
    cancelled_at TEXT
)
"""
    dlq_sql = """
CREATE TABLE IF NOT EXISTS dead_letter_items (
    dlq_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    agent_dir TEXT NOT NULL,
    message TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'api',
    project_id TEXT,
    queue_id TEXT,
    priority INTEGER NOT NULL DEFAULT 5,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    failed_at TEXT,
    moved_to_dlq_at TEXT,
    reason TEXT,
    metadata_json TEXT
)
"""
    with engine.begin() as conn:
        conn.execute(text(projects_sql))
        conn.execute(text(job_queues_sql))
        conn.execute(text(job_items_sql))
        conn.execute(text(dlq_sql))


@pytest.fixture
def db_engine():
    """Create in-memory SQLite engine for migration testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    setup_full_schema(engine)
    yield engine
    engine.dispose()


def execute_scalar(engine, query: str):
    """Execute a SELECT and return the first column of the first row."""
    with engine.connect() as conn:
        result = conn.execute(text(query))
        return result.scalar()


def execute_one(engine, query: str):
    """Execute a SELECT and return the first row."""
    with engine.connect() as conn:
        result = conn.execute(text(query))
        return result.fetchone()


# =============================================================================
# Tests: Migration creates system default project and queue
# =============================================================================

class TestMigrationCreatesSystemProject:
    """Tests for Step 1: Ensure system default project exists."""

    def test_migration_creates_system_default_project(self, db_engine):
        """Migration creates the system default project record."""
        run_migration(db_engine)

        row = execute_one(
            db_engine,
            f"SELECT project_id, name, project_type, status FROM projects WHERE project_id = '{SYSTEM_DEFAULT_PROJECT_ID}'",
        )

        assert row is not None, "System default project was not created"
        assert row[0] == SYSTEM_DEFAULT_PROJECT_ID
        assert row[1] == "__system_default__"
        assert row[2] == "system"
        assert row[3] == "active"

    def test_migration_is_idempotent_system_project(self, db_engine):
        """Running migration twice does not duplicate the system project."""
        run_migration(db_engine)
        run_migration(db_engine)

        count = execute_scalar(
            db_engine,
            f"SELECT COUNT(*) FROM projects WHERE project_id = '{SYSTEM_DEFAULT_PROJECT_ID}'",
        )
        assert count == 1, f"System default project appears {count} times (expected 1)"


class TestMigrationCreatesSystemQueue:
    """Tests for Step 2: Ensure system FIFO queue exists."""

    def test_migration_creates_system_fifo_queue(self, db_engine):
        """Migration creates the system FIFO queue for the system default project."""
        run_migration(db_engine)

        row = execute_one(
            db_engine,
            f"SELECT queue_id, project_id, queue_name, queue_type, is_system "
            f"FROM job_queues WHERE queue_id = '{SYSTEM_FIFO_QUEUE_ID}'",
        )

        assert row is not None, "System FIFO queue was not created"
        assert row[0] == SYSTEM_FIFO_QUEUE_ID
        assert row[1] == SYSTEM_DEFAULT_PROJECT_ID
        assert row[2] == "system_fifo_queue"
        assert row[3] == "fifo"
        assert row[4] == 1  # is_system

    def test_migration_is_idempotent_system_queue(self, db_engine):
        """Running migration twice does not duplicate the system FIFO queue."""
        run_migration(db_engine)
        run_migration(db_engine)

        count = execute_scalar(
            db_engine,
            f"SELECT COUNT(*) FROM job_queues WHERE queue_id = '{SYSTEM_FIFO_QUEUE_ID}'",
        )
        assert count == 1, f"System FIFO queue appears {count} times (expected 1)"


# =============================================================================
# Tests: Backfill job_queue_items (Steps 3 & 4)
# =============================================================================

class TestMigrationBackfillsJobQueueItems:
    """Tests for Steps 3 & 4: Backfill NULL/empty project_id in job_queue_items."""

    def test_migration_backfills_null_project_id(self, db_engine):
        """Migration updates job_queue_items with NULL project_id to system default."""
        now = "2026-04-24T00:00:00"

        with db_engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source, project_id, priority, status, created_at)
                VALUES
                    ('job-null-1', 'coder', '/agents/coder', 'Test message', 'api', NULL, 5, 'pending', :now)
            """), {"now": now})

        # Verify NULL before migration
        count_null_before = execute_scalar(
            db_engine,
            "SELECT COUNT(*) FROM job_queue_items WHERE project_id IS NULL",
        )
        assert count_null_before == 1, "Job with NULL project_id was not inserted"

        run_migration(db_engine)

        # Verify NULL is gone
        count_null_after = execute_scalar(
            db_engine,
            "SELECT COUNT(*) FROM job_queue_items WHERE project_id IS NULL",
        )
        assert count_null_after == 0, "NULL project_ids still remain after migration"

        # Verify job's project_id is now system default
        project_id = execute_scalar(
            db_engine,
            "SELECT project_id FROM job_queue_items WHERE job_id = 'job-null-1'",
        )
        assert project_id == SYSTEM_DEFAULT_PROJECT_ID, (
            f"Expected project_id={SYSTEM_DEFAULT_PROJECT_ID}, got {project_id}"
        )

    def test_migration_backfills_empty_string_project_id(self, db_engine):
        """Migration updates job_queue_items with empty string project_id to system default."""
        now = "2026-04-24T00:00:00"

        with db_engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source, project_id, priority, status, created_at)
                VALUES
                    ('job-empty-1', 'coder', '/agents/coder', 'Test message', 'api', '', 5, 'pending', :now)
            """), {"now": now})

        # Verify empty string before migration
        count_empty_before = execute_scalar(
            db_engine,
            "SELECT COUNT(*) FROM job_queue_items WHERE project_id = ''",
        )
        assert count_empty_before == 1, "Job with empty project_id was not inserted"

        run_migration(db_engine)

        # Verify empty string is gone
        count_empty_after = execute_scalar(
            db_engine,
            "SELECT COUNT(*) FROM job_queue_items WHERE project_id = ''",
        )
        assert count_empty_after == 0, "Empty-string project_ids still remain after migration"

        # Verify job's project_id is now system default
        project_id = execute_scalar(
            db_engine,
            "SELECT project_id FROM job_queue_items WHERE job_id = 'job-empty-1'",
        )
        assert project_id == SYSTEM_DEFAULT_PROJECT_ID, (
            f"Expected project_id={SYSTEM_DEFAULT_PROJECT_ID}, got {project_id}"
        )

    def test_migration_does_not_affect_valid_project_ids(self, db_engine):
        """Migration does not change jobs that already have a valid project_id."""
        now = "2026-04-24T00:00:00"
        custom_project = "my-custom-project-xyz"

        with db_engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source, project_id, priority, status, created_at)
                VALUES
                    ('job-valid-1', 'coder', '/agents/coder', 'Test message', 'api', :proj, 5, 'pending', :now)
            """), {"proj": custom_project, "now": now})

        run_migration(db_engine)

        project_id = execute_scalar(
            db_engine,
            "SELECT project_id FROM job_queue_items WHERE job_id = 'job-valid-1'",
        )
        assert project_id == custom_project, (
            f"Valid project_id was changed from {custom_project} to {project_id}"
        )

    def test_migration_multiple_null_jobs(self, db_engine):
        """Migration correctly backfills multiple jobs with NULL project_id."""
        now = "2026-04-24T00:00:00"

        with db_engine.begin() as conn:
            for i in range(5):
                conn.execute(text("""
                    INSERT INTO job_queue_items
                        (job_id, agent_id, agent_dir, message, source, project_id, priority, status, created_at)
                    VALUES
                        (:jid, 'coder', '/agents/coder', 'Test message', 'api', NULL, 5, 'pending', :now)
                """), {"jid": f"job-null-{i}", "now": now})

        run_migration(db_engine)

        # All should be system default
        for i in range(5):
            project_id = execute_scalar(
                db_engine,
                f"SELECT project_id FROM job_queue_items WHERE job_id = 'job-null-{i}'",
            )
            assert project_id == SYSTEM_DEFAULT_PROJECT_ID, (
                f"job-null-{i} has project_id={project_id}, expected {SYSTEM_DEFAULT_PROJECT_ID}"
            )

        # No NULLs remain
        null_count = execute_scalar(
            db_engine,
            "SELECT COUNT(*) FROM job_queue_items WHERE project_id IS NULL",
        )
        assert null_count == 0


# =============================================================================
# Tests: Backfill dead_letter_items (Step 5)
# =============================================================================

class TestMigrationBackfillsDeadLetterItems:
    """Tests for Step 5: Backfill NULL/empty project_id in dead_letter_items."""

    def test_migration_backfills_null_project_id_in_dlq(self, db_engine):
        """Migration updates dead_letter_items with NULL project_id to system default."""
        now = "2026-04-24T00:00:00"

        with db_engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO dead_letter_items
                    (dlq_id, job_id, agent_id, agent_dir, message, source, project_id,
                     priority, error_message, retry_count, failed_at, reason)
                VALUES
                    ('dlq-null-1', 'job-dlq-1', 'coder', '/agents/coder',
                     'Failed message', 'api', NULL, 5, 'Timeout', 3, :now, 'MAX_RETRIES')
            """), {"now": now})

        run_migration(db_engine)

        # Verify NULL is gone
        null_count = execute_scalar(
            db_engine,
            "SELECT COUNT(*) FROM dead_letter_items WHERE project_id IS NULL",
        )
        assert null_count == 0, "NULL project_ids still remain in dead_letter_items"

        # Verify dlq item's project_id is now system default
        project_id = execute_scalar(
            db_engine,
            "SELECT project_id FROM dead_letter_items WHERE dlq_id = 'dlq-null-1'",
        )
        assert project_id == SYSTEM_DEFAULT_PROJECT_ID, (
            f"DLQ item project_id={project_id}, expected {SYSTEM_DEFAULT_PROJECT_ID}"
        )

    def test_migration_backfills_empty_string_project_id_in_dlq(self, db_engine):
        """Migration updates dead_letter_items with empty string project_id to system default."""
        now = "2026-04-24T00:00:00"

        with db_engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO dead_letter_items
                    (dlq_id, job_id, agent_id, agent_dir, message, source, project_id,
                     priority, error_message, retry_count, failed_at, reason)
                VALUES
                    ('dlq-empty-1', 'job-dlq-2', 'coder', '/agents/coder',
                     'Failed message', 'api', '', 5, 'Timeout', 3, :now, 'MAX_RETRIES')
            """), {"now": now})

        run_migration(db_engine)

        # Verify empty string is gone
        empty_count = execute_scalar(
            db_engine,
            "SELECT COUNT(*) FROM dead_letter_items WHERE project_id = ''",
        )
        assert empty_count == 0, "Empty-string project_ids still remain in dead_letter_items"

        project_id = execute_scalar(
            db_engine,
            "SELECT project_id FROM dead_letter_items WHERE dlq_id = 'dlq-empty-1'",
        )
        assert project_id == SYSTEM_DEFAULT_PROJECT_ID

    def test_migration_does_not_affect_valid_project_ids_in_dlq(self, db_engine):
        """Migration does not change DLQ items that already have a valid project_id."""
        now = "2026-04-24T00:00:00"
        custom_project = "my-custom-project-xyz"

        with db_engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO dead_letter_items
                    (dlq_id, job_id, agent_id, agent_dir, message, source, project_id,
                     priority, error_message, retry_count, failed_at, reason)
                VALUES
                    ('dlq-valid-1', 'job-dlq-3', 'coder', '/agents/coder',
                     'Failed message', 'api', :proj, 5, 'Timeout', 3, :now, 'MAX_RETRIES')
            """), {"proj": custom_project, "now": now})

        run_migration(db_engine)

        project_id = execute_scalar(
            db_engine,
            "SELECT project_id FROM dead_letter_items WHERE dlq_id = 'dlq-valid-1'",
        )
        assert project_id == custom_project


# =============================================================================
# Tests: Assign queue_id to orphaned jobs (Steps 6 & 7)
# =============================================================================

class TestMigrationAssignsQueueId:
    """Tests for Steps 6 & 7: Assign queue_id to orphaned jobs and DLQ items."""

    def test_migration_assigns_queue_id_to_orphaned_jobs(self, db_engine):
        """Migration assigns system FIFO queue_id to jobs that have system project_id but NULL queue_id."""
        now = "2026-04-24T00:00:00"

        # Insert job with system project_id but NULL queue_id (simulating Phase 2 orphaned job)
        with db_engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source, project_id, priority, status, created_at)
                VALUES
                    ('job-orphan-1', 'coder', '/agents/coder', 'Orphaned job', 'api',
                     :proj, 5, 'pending', :now)
            """), {"proj": SYSTEM_DEFAULT_PROJECT_ID, "now": now})

        # Verify queue_id is NULL before migration
        queue_id_before = execute_scalar(
            db_engine,
            "SELECT queue_id FROM job_queue_items WHERE job_id = 'job-orphan-1'",
        )
        assert queue_id_before is None, "Job should have NULL queue_id before migration"

        run_migration(db_engine)

        # Verify queue_id is now assigned
        queue_id_after = execute_scalar(
            db_engine,
            "SELECT queue_id FROM job_queue_items WHERE job_id = 'job-orphan-1'",
        )
        assert queue_id_after == SYSTEM_FIFO_QUEUE_ID, (
            f"Expected queue_id={SYSTEM_FIFO_QUEUE_ID}, got {queue_id_after}"
        )

    def test_migration_assigns_queue_id_to_orphaned_dlq_items(self, db_engine):
        """Migration assigns system FIFO queue_id to DLQ items with system project_id but NULL queue_id."""
        now = "2026-04-24T00:00:00"

        # Insert DLQ item with system project_id but NULL queue_id
        with db_engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO dead_letter_items
                    (dlq_id, job_id, agent_id, agent_dir, message, source, project_id,
                     priority, error_message, retry_count, failed_at, reason)
                VALUES
                    ('dlq-orphan-1', 'job-dlq-orphan', 'coder', '/agents/coder',
                     'Orphaned DLQ', 'api', :proj, 5, 'Timeout', 3, :now, 'MAX_RETRIES')
            """), {"proj": SYSTEM_DEFAULT_PROJECT_ID, "now": now})

        run_migration(db_engine)

        queue_id = execute_scalar(
            db_engine,
            "SELECT queue_id FROM dead_letter_items WHERE dlq_id = 'dlq-orphan-1'",
        )
        assert queue_id == SYSTEM_FIFO_QUEUE_ID, (
            f"Expected queue_id={SYSTEM_FIFO_QUEUE_ID}, got {queue_id}"
        )

    def test_migration_does_not_change_queue_id_if_already_set(self, db_engine):
        """Migration does not override queue_id if already assigned."""
        now = "2026-04-24T00:00:00"
        existing_queue = "existing-queue-123"

        with db_engine.begin() as conn:
            # Insert job with system project_id AND existing queue_id
            conn.execute(text("""
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source, project_id, queue_id, priority, status, created_at)
                VALUES
                    ('job-with-queue-1', 'coder', '/agents/coder', 'Job with queue',
                     'api', :proj, :queue, 5, 'pending', :now)
            """), {"proj": SYSTEM_DEFAULT_PROJECT_ID, "queue": existing_queue, "now": now})

        run_migration(db_engine)

        queue_id = execute_scalar(
            db_engine,
            "SELECT queue_id FROM job_queue_items WHERE job_id = 'job-with-queue-1'",
        )
        assert queue_id == existing_queue, (
            f"queue_id was changed from {existing_queue} to {queue_id}"
        )


# =============================================================================
# Tests: End-to-end migration scenario
# =============================================================================

class TestMigrationEndToEnd:
    """End-to-end tests simulating a realistic pre-migration state."""

    def test_migration_handles_mixed_null_and_valid_jobs(self, db_engine):
        """Migration correctly handles a mix of NULL, empty, and valid project_ids."""
        now = "2026-04-24T00:00:00"
        custom_project = "existing-project-abc"

        with db_engine.begin() as conn:
            # Job with NULL project_id
            conn.execute(text("""
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source, project_id, priority, status, created_at)
                VALUES
                    ('job-mixed-1', 'coder', '/agents/coder', 'NULL project', 'api', NULL, 5, 'pending', :now)
            """), {"now": now})

            # Job with empty string project_id
            conn.execute(text("""
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source, project_id, priority, status, created_at)
                VALUES
                    ('job-mixed-2', 'coder', '/agents/coder', 'Empty project', 'api', '', 5, 'pending', :now)
            """), {"now": now})

            # Job with valid project_id
            conn.execute(text("""
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source, project_id, priority, status, created_at)
                VALUES
                    ('job-mixed-3', 'coder', '/agents/coder', 'Valid project', 'api', :proj, 5, 'pending', :now)
            """), {"proj": custom_project, "now": now})

        run_migration(db_engine)

        # Verify no NULL or empty project_ids remain in job_queue_items
        null_or_empty = execute_scalar(
            db_engine,
            "SELECT COUNT(*) FROM job_queue_items WHERE project_id IS NULL OR project_id = ''",
        )
        assert null_or_empty == 0, (
            f"{null_or_empty} jobs still have NULL or empty project_id after migration"
        )

        # Verify NULL job is system default
        p1 = execute_scalar(
            db_engine,
            "SELECT project_id FROM job_queue_items WHERE job_id = 'job-mixed-1'",
        )
        assert p1 == SYSTEM_DEFAULT_PROJECT_ID

        # Verify empty string job is system default
        p2 = execute_scalar(
            db_engine,
            "SELECT project_id FROM job_queue_items WHERE job_id = 'job-mixed-2'",
        )
        assert p2 == SYSTEM_DEFAULT_PROJECT_ID

        # Verify valid job is unchanged
        p3 = execute_scalar(
            db_engine,
            "SELECT project_id FROM job_queue_items WHERE job_id = 'job-mixed-3'",
        )
        assert p3 == custom_project

    def test_migration_all_null_jobs_get_queue_id(self, db_engine):
        """After backfill, all formerly NULL jobs have queue_id assigned."""
        now = "2026-04-24T00:00:00"

        with db_engine.begin() as conn:
            for i in range(3):
                conn.execute(text("""
                    INSERT INTO job_queue_items
                        (job_id, agent_id, agent_dir, message, source, project_id, priority, status, created_at)
                    VALUES
                        (:jid, 'coder', '/agents/coder', 'Orphan job', 'api', NULL, 5, 'pending', :now)
                """), {"jid": f"job-orphan-{i}", "now": now})

        run_migration(db_engine)

        for i in range(3):
            queue_id = execute_scalar(
                db_engine,
                f"SELECT queue_id FROM job_queue_items WHERE job_id = 'job-orphan-{i}'",
            )
            assert queue_id == SYSTEM_FIFO_QUEUE_ID, (
                f"job-orphan-{i} has queue_id={queue_id}, expected {SYSTEM_FIFO_QUEUE_ID}"
            )
