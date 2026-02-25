"""Persistence layer with SQLite for session management."""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

logger = logging.getLogger(__name__)


def init_database(db_path: Path) -> sqlite3.Connection:
    """Create database file and parent directories if needed.
    
    Creates the sessions and session_hierarchy tables.
    
    Args:
        db_path: Path to the SQLite database file.
        
    Returns:
        sqlite3.Connection: Database connection.
    """
    # Create parent directories if they don't exist
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create database connection
    # check_same_thread=False allows connection to be used across FastAPI's async handlers
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    # Create sessions table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            agent_dir TEXT NOT NULL,
            parent_id TEXT,
            status TEXT DEFAULT 'idle',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata JSON
        )
    """)
    
    # Create session_hierarchy table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_hierarchy (
            parent_id TEXT,
            child_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (parent_id, child_id)
        )
    """)
    
    # Create message_queue table for input message queue system
    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_queue (
            message_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT DEFAULT 'ready',
            priority INTEGER DEFAULT 1,
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 5,
            error_message TEXT,
            metadata JSON,
            enqueued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processing_started_at TIMESTAMP,
            completed_at TIMESTAMP,
            next_retry_at TIMESTAMP
    )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_message_queue_session_status 
        ON message_queue(session_id, status, priority, enqueued_at)
    """)
    
    conn.commit()
    logger.info(f"Database initialized at {db_path}")
    return conn


def get_checkpointer(conn: sqlite3.Connection) -> SqliteSaver:
    """Create and return a SqliteSaver checkpointer.
    
    Args:
        conn: SQLite database connection.
        
    Returns:
        SqliteSaver: LangGraph checkpointer instance.
    """
    return SqliteSaver(conn)


def save_session_metadata(
    conn: sqlite3.Connection,
    session_id: str,
    agent_dir: str,
    parent_id: str | None = None
) -> None:
    """Insert session metadata into the sessions table.
    
    If parent_id is provided, also inserts into session_hierarchy.
    
    Args:
        conn: Database connection.
        session_id: Unique session identifier.
        agent_dir: Path to the agent directory.
        parent_id: Optional parent session ID for hierarchical sessions.
    """
    conn.execute(
        """
        INSERT INTO sessions (session_id, agent_dir, parent_id, status)
        VALUES (?, ?, ?, 'idle')
        """,
        (session_id, agent_dir, parent_id)
    )
    
    if parent_id is not None:
        conn.execute(
            """
            INSERT INTO session_hierarchy (parent_id, child_id)
            VALUES (?, ?)
            """,
            (parent_id, session_id)
        )
    
    conn.commit()
    logger.info(f"Saved session metadata for session_id={session_id}, parent_id={parent_id}")


def update_session_status(
    conn: sqlite3.Connection,
    session_id: str,
    status: str
) -> None:
    """Update session status and updated_at timestamp.
    
    Args:
        conn: Database connection.
        session_id: Session identifier to update.
        status: New status value.
    """
    conn.execute(
        """
        UPDATE sessions
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE session_id = ?
        """,
        (status, session_id)
    )
    conn.commit()
    logger.info(f"Updated session status: session_id={session_id}, status={status}")


def get_session_metadata(
    conn: sqlite3.Connection,
    session_id: str
) -> dict[str, Any] | None:
    """Get session metadata including children list.
    
    Args:
        conn: Database connection.
        session_id: Session identifier to retrieve.
        
    Returns:
        Dictionary with session info and children list, or None if not found.
    """
    cursor = conn.execute(
        """
        SELECT session_id, agent_dir, parent_id, status, 
               created_at, updated_at, metadata
        FROM sessions
        WHERE session_id = ?
        """,
        (session_id,)
    )
    row = cursor.fetchone()
    
    if row is None:
        return None
    
    # Get children from session_hierarchy
    children_cursor = conn.execute(
        """
        SELECT child_id
        FROM session_hierarchy
        WHERE parent_id = ?
        """,
        (session_id,)
    )
    children = [child_row["child_id"] for child_row in children_cursor.fetchall()]
    
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    
    return {
        "session_id": row["session_id"],
        "agent_dir": row["agent_dir"],
        "parent_id": row["parent_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "metadata": metadata,
        "children": children
    }


def list_all_sessions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get all sessions with their children.
    
    Args:
        conn: Database connection.
        
    Returns:
        List of session dictionaries with children.
    """
    cursor = conn.execute(
        """
        SELECT session_id, agent_dir, parent_id, status,
               created_at, updated_at, metadata
        FROM sessions
        ORDER BY created_at DESC
        """
    )
    rows = cursor.fetchall()
    
    sessions = []
    for row in rows:
        # Get children for each session
        children_cursor = conn.execute(
            """
            SELECT child_id
            FROM session_hierarchy
            WHERE parent_id = ?
            """,
            (row["session_id"],)
        )
        children = [child_row["child_id"] for child_row in children_cursor.fetchall()]
        
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        
        sessions.append({
            "session_id": row["session_id"],
            "agent_dir": row["agent_dir"],
            "parent_id": row["parent_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": metadata,
            "children": children
        })
    
    return sessions


def delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Delete session from sessions and session_hierarchy tables.
    
    Deletes from both places where the session appears:
    - As a parent in session_hierarchy
    - As a child in session_hierarchy
    
    Args:
        conn: Database connection.
        session_id: Session identifier to delete.
    """
    # Delete from session_hierarchy where session is parent (delete children references)
    conn.execute(
        """
        DELETE FROM session_hierarchy
        WHERE parent_id = ?
        """,
        (session_id,)
    )
    
    # Delete from session_hierarchy where session is child
    conn.execute(
        """
        DELETE FROM session_hierarchy
        WHERE child_id = ?
        """,
        (session_id,)
    )
    
    # Delete from sessions table
    conn.execute(
        """
        DELETE FROM sessions
        WHERE session_id = ?
        """,
        (session_id,)
    )
    
    conn.commit()
    logger.info(f"Deleted session: session_id={session_id}")


def delete_all_sessions(conn: sqlite3.Connection) -> int:
    """Delete all sessions from the database.
    
    Args:
        conn: Database connection.
        
    Returns:
        Number of sessions deleted.
    """
    cursor = conn.execute("SELECT COUNT(*) FROM sessions")
    count = cursor.fetchone()[0]
    
    conn.execute("DELETE FROM session_hierarchy")
    conn.execute("DELETE FROM sessions")
    conn.commit()
    
    logger.info(f"Deleted all {count} sessions from database")
    return count


def cleanup_old_checkpoints(
    conn: sqlite3.Connection,
    checkpointer: SqliteSaver,
    ttl_hours: int,
    max_count: int
) -> dict[str, Any]:
    """Clean up old checkpoints based on TTL and max count.
    
    This is a placeholder implementation. LangGraph checkpoint cleanup
    requires access to internal tables which are managed by the checkpointer.
    
    Args:
        conn: Database connection.
        checkpointer: SqliteSaver checkpointer instance.
        ttl_hours: Time-to-live in hours for checkpoints.
        max_count: Maximum number of checkpoints to keep per thread.
        
    Returns:
        Dictionary with cleanup results (placeholder).
    """
    logger.info(
        f"Checkpoint cleanup called with ttl_hours={ttl_hours}, max_count={max_count}"
    )
    logger.info(
        "Checkpoint cleanup is a placeholder - would clean checkpoints older "
        "than {ttl_hours} hours, keeping at most {max_count} per thread"
    )
    
    # Placeholder return value
    # Full implementation would need to access LangGraph's internal checkpoint tables
    return {
        "cleaned_count": 0,
        "remaining_count": 0,
        "note": "Placeholder implementation - checkpoint cleanup not yet fully implemented"
    }


def cleanup_message_queue(
    conn: sqlite3.Connection,
    max_age_hours: int = 24
) -> int:
    """Remove old completed/failed messages from the queue.
    
    Args:
        conn: Database connection.
        max_age_hours: Maximum age of completed messages to keep.
        
    Returns:
        Number of messages deleted.
    """
    cursor = conn.execute("""
        DELETE FROM message_queue
        WHERE status IN ('completed', 'failed')
          AND completed_at < datetime('now', ? || ' hours')
    """, (f'-{max_age_hours}',))
    
    deleted = cursor.rowcount
    conn.commit()
    
    if deleted > 0:
        logger.info(f"Cleaned up {deleted} old queue messages")
    
    return deleted
