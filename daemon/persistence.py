"""Persistence layer with SQLite for session management."""

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)


def init_database(db_path: Path) -> sqlite3.Connection:
    """Create database file and parent directories if needed.
    
    Creates the sessions and session_hierarchy tables.
    Enables WAL mode for better concurrent write performance.
    
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
    
    # Enable WAL mode for better concurrent write performance
    # Must be done BEFORE any tables are created
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")   # 30 second timeout
    conn.execute("PRAGMA synchronous=NORMAL")   # Faster writes (safe with WAL)
    conn.execute("PRAGMA cache_size=-64000")    # 64MB cache
    conn.execute("PRAGMA foreign_keys=ON")
    
    # Create sessions table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            agent_dir TEXT NOT NULL,
            agent_name TEXT,
            parent_id TEXT,
            status TEXT DEFAULT 'idle',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata JSON
        )
    """)
    
    # Migration: Add agent_name column if it doesn't exist
    cursor = conn.execute("PRAGMA table_info(sessions)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'agent_name' not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN agent_name TEXT")
        logger.info("Added agent_name column to sessions table")
    
    # Create index for efficient ordering by created_at
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_created_at 
        ON sessions(created_at DESC)
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
    
    # Create source_configs table for pluggable message sources
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_configs (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            name TEXT NOT NULL,
            config JSON NOT NULL,
            credentials TEXT,
            enabled BOOLEAN DEFAULT TRUE,
            status TEXT DEFAULT 'stopped',
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create session_mappings table for external_user -> agent_session mapping
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_mappings (
            mapping_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            agent_session_id TEXT NOT NULL,
            agent_dir TEXT NOT NULL,
            metadata JSON,
            last_message_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, external_user_id),
            FOREIGN KEY (source_id) REFERENCES source_configs(source_id)
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_mappings_source 
        ON session_mappings(source_id)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_mappings_session 
        ON session_mappings(agent_session_id)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_mappings_cleanup 
        ON session_mappings(last_message_at)
    """)
    
    # Create processed_external_messages table for deduplication
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_external_messages (
            source_id TEXT,
            external_message_id TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_id, external_message_id)
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_processed_msg_cleanup 
        ON processed_external_messages(processed_at)
    """)
    
    # Create projects table for project management
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            project_type TEXT NOT NULL DEFAULT 'general',
            status TEXT NOT NULL DEFAULT 'active',
            main_directory TEXT,
            related_directories TEXT DEFAULT '[]',
            description TEXT,
            shortnames TEXT DEFAULT '[]',
            metadata TEXT DEFAULT '{}',
            relationships TEXT DEFAULT '{}',
            creator_session_id TEXT,
            creator_agent_dir TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    
    # Indexes for projects table
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_projects_type ON projects(project_type)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_projects_creator_session ON projects(creator_session_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_projects_main_directory ON projects(main_directory)
    """)
    
    # Create project_tags junction table for efficient tag filtering
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_tags (
            project_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (project_id, tag),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
        )
    """)
    
    # Index for tag-based lookups
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_project_tags_tag ON project_tags(tag)
    """)
    
    # Create project_shortnames junction table for efficient shortname filtering
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_shortnames (
            project_id TEXT NOT NULL,
            shortname TEXT NOT NULL,
            PRIMARY KEY (project_id, shortname),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
        )
    """)
    
    # Index for shortname-based lookups
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_project_shortnames_shortname ON project_shortnames(shortname)
    """)
    
    # Create schedule_executions table for tracking scheduler execution history
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule_executions (
            execution_id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT,
            status TEXT NOT NULL,
            error_message TEXT,
            completed_at TIMESTAMP,
            FOREIGN KEY (schedule_id) REFERENCES source_configs(source_id) ON DELETE CASCADE
        )
    """)
    
    # Create indexes for schedule_executions
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_schedule_executions_schedule ON schedule_executions(schedule_id)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_schedule_executions_triggered ON schedule_executions(triggered_at)
    """)
    
    # Migration: Add creator columns if they don't exist
    cursor = conn.execute("PRAGMA table_info(projects)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'creator_session_id' not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN creator_session_id TEXT")
        logger.info("Added creator_session_id column to projects table")
    if 'creator_agent_dir' not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN creator_agent_dir TEXT")
        logger.info("Added creator_agent_dir column to projects table")
    if 'shortnames' not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN shortnames TEXT DEFAULT '[]'")
        logger.info("Added shortnames column to projects table")
    
    conn.commit()
    logger.info(f"Database initialized at {db_path}")
    return conn


async def get_checkpointer(db_path: Path) -> AsyncSqliteSaver:
    """Create and return an AsyncSqliteSaver checkpointer.
    
    Note: This creates the aiosqlite connection directly and which will be
    kept alive for the entire application lifetime. The checkpointer will
    be cleaned up when the application shuts down via the cleanup_checkpointer()
    method on the SessionManager class.
    
    Args:
        db_path: Path to the SQLite database file.
        
    Returns:
        AsyncSqliteSaver: LangGraph async checkpointer instance.
    """
    # Create connection directly - don't use async context manager
    conn = await aiosqlite.connect(str(db_path))
    return AsyncSqliteSaver(conn)


def get_agent_name(agent_dir: str) -> str:
    """Derive agent name from agent directory path.
    
    Args:
        agent_dir: Path to the agent directory.
        
    Returns:
        Agent name in Title Case (e.g., "Coder", "Designer").
    """
    from pathlib import Path
    return Path(agent_dir).name.title()


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
    # Derive agent_name from agent_dir
    agent_name = get_agent_name(agent_dir)
    
    conn.execute(
        """
        INSERT INTO sessions (session_id, agent_dir, agent_name, parent_id, status)
        VALUES (?, ?, ?, ?, 'idle')
        """,
        (session_id, agent_dir, agent_name, parent_id)
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
    logger.info(f"Saved session metadata for session_id={session_id}, parent_id={parent_id}, agent_name={agent_name}")


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


def update_session_title(
    conn: sqlite3.Connection,
    session_id: str,
    title: str
) -> None:
    """Update session title in the metadata JSON column.
    
    Args:
        conn: Database connection.
        session_id: Session identifier to update.
        title: New title value.
    """
    # Get current metadata
    cursor = conn.execute(
        "SELECT metadata FROM sessions WHERE session_id = ?",
        (session_id,)
    )
    row = cursor.fetchone()
    
    if row is None:
        logger.warning(f"Session {session_id} not found for title update")
        return
    
    # Parse existing metadata or create new dict
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata) if metadata else {}
    elif metadata is None:
        metadata = {}
    
    # Update title in metadata
    metadata["title"] = title
    
    # Save back to database
    conn.execute(
        """
        UPDATE sessions
        SET metadata = ?, updated_at = CURRENT_TIMESTAMP
        WHERE session_id = ?
        """,
        (json.dumps(metadata), session_id)
    )
    conn.commit()
    logger.info(f"Updated session title: session_id={session_id}, title={title[:50]}...")


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
        SELECT session_id, agent_dir, agent_name, parent_id, status, 
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
        metadata = json.loads(metadata) if metadata else {}
    elif metadata is None:
        metadata = {}
    
    # Extract title from metadata
    title = metadata.get("title") if isinstance(metadata, dict) else None
    
    return {
        "session_id": row["session_id"],
        "agent_dir": row["agent_dir"],
        "agent_name": row["agent_name"],
        "parent_id": row["parent_id"],
        "status": row["status"],
        "title": title,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "metadata": metadata,
        "children": children
    }


def list_all_sessions(
    conn: sqlite3.Connection, 
    limit: int = 100, 
    offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """Get sessions with their children, with pagination support.
    
    Args:
        conn: Database connection.
        limit: Maximum number of sessions to return (default: 100).
        offset: Number of sessions to skip (default: 0).
        
    Returns:
        Tuple of (list of session dictionaries with children, total count).
    """
    # Get total count first
    count_cursor = conn.execute("SELECT COUNT(*) as total FROM sessions")
    total = count_cursor.fetchone()["total"]
    
    # Get paginated sessions
    cursor = conn.execute(
        """
        SELECT session_id, agent_dir, agent_name, parent_id, status,
               created_at, updated_at, metadata
        FROM sessions
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset)
    )
    rows = cursor.fetchall()
    
    # Batch fetch children for all sessions (fixes N+1 query)
    session_ids = [row["session_id"] for row in rows]
    children_map: dict[str, list[str]] = {sid: [] for sid in session_ids}
    
    if session_ids:
        placeholders = ",".join("?" * len(session_ids))
        children_cursor = conn.execute(
            f"""
            SELECT parent_id, child_id
            FROM session_hierarchy
            WHERE parent_id IN ({placeholders})
            """,
            session_ids
        )
        for child_row in children_cursor.fetchall():
            parent_id = child_row["parent_id"]
            if parent_id in children_map:
                children_map[parent_id].append(child_row["child_id"])
    
    sessions = []
    for row in rows:
        session_id = row["session_id"]
        children = children_map.get(session_id, [])
        
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}
        elif metadata is None:
            metadata = {}
        
        # Extract title from metadata
        title = metadata.get("title") if isinstance(metadata, dict) else None
        
        sessions.append({
            "session_id": session_id,
            "agent_dir": row["agent_dir"],
            "agent_name": row["agent_name"],
            "parent_id": row["parent_id"],
            "status": row["status"],
            "title": title,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": metadata,
            "children": children
        })
    
    return sessions, total


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
    checkpointer: AsyncSqliteSaver,
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


async def get_session_messages(
    checkpointer: AsyncSqliteSaver,
    session_id: str
) -> list[dict[str, Any]]:
    """Get message history from LangGraph checkpoints.
    
    Args:
        checkpointer: Shared AsyncSqliteSaver instance.
        session_id: Session identifier to retrieve messages for.
        
    Returns:
        List of message dictionaries with role, content, thinking, tool_calls.
    """
    from datetime import datetime, timezone
    import uuid
    
    # Use the PASSED checkpointer instead of creating a new one
    config = {"configurable": {"thread_id": session_id}}
    
    # Get the current state from async checkpointer
    state = await checkpointer.aget(config)
    if state is None:
        return []
    
    # LangGraph stores messages in channel_values
    channel_values = state.get("channel_values", {})
    messages = channel_values.get("messages", [])
    if not messages:
        return []
    
    result = []
    
    # Build a map of tool_call_id -> output from ToolMessages
    tool_outputs = {}
    for msg in messages:
        if hasattr(msg, 'tool_call_id'):  # It's a ToolMessage
            tool_outputs[msg.tool_call_id] = msg.content
    
    for msg in messages:
        msg_type = getattr(msg, 'type', 'unknown')
        
        # Map message types to roles
        role_map = {
            'human': 'user',
            'ai': 'assistant',
            'system': 'system',
            'tool': 'tool',
        }
        role = role_map.get(msg_type, msg_type)
        
        # Skip tool messages in the main list (they're included in tool_calls)
        if msg_type == 'tool':
            continue
        
        content = getattr(msg, 'content', '') or ''
        
        # Extract thinking from additional_kwargs
        thinking = None
        if hasattr(msg, 'additional_kwargs'):
            kwargs = msg.additional_kwargs or {}
            if kwargs.get("thinking"):
                thinking = kwargs["thinking"]
            elif kwargs.get("reasoning_content"):
                thinking = kwargs["reasoning_content"]
        
        # Parse <think/> tags from content using shared utility
        from daemon.manager import parse_think_tags
        content, thinking_extracted = parse_think_tags(content)
        
        # Extract tool_calls for AI messages
        tool_calls = None
        if msg_type == 'ai' and hasattr(msg, 'tool_calls') and msg.tool_calls:
            tool_calls = []
            for tc in msg.tool_calls:
                # Handle both dict and object formats
                if isinstance(tc, dict):
                    tc_id = tc.get("id", "")
                    tool_calls.append({
                        "id": tc_id,
                        "name": tc.get("name", ""),
                        "arguments": tc.get("args", {}),
                        "output": tool_outputs.get(tc_id),
                    })
                else:
                    tc_id = getattr(tc, "id", "")
                    tool_calls.append({
                        "id": tc_id,
                        "name": getattr(tc, "name", ""),
                        "arguments": getattr(tc, "args", {}),
                        "output": tool_outputs.get(tc_id),
                    })
        
        # Generate a message ID based on content hash (for consistency)
        msg_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{session_id}:{role}:{content[:100]}"))
        
        result.append({
            "message_id": msg_id,
            "type": msg_type,
            "role": role,
            "content": content,
            "thinking": thinking,
            "thinking_extracted": thinking_extracted,
            "tool_calls": tool_calls,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    
    return result
