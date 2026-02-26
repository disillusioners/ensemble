"""
Database operations for source configs, session mappings, and deduplication.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# ==================== Source Config Operations ====================


def save_source_config(
    conn: sqlite3.Connection,
    source_id: str,
    source_type: str,
    name: str,
    config: dict,
    credentials: str | None = None,
    enabled: bool = True,
) -> None:
    """Save or update a source configuration."""
    config_json = json.dumps(config)
    cursor = conn.execute(
        """
        INSERT INTO source_configs (source_id, source_type, name, config, credentials, enabled, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source_id) DO UPDATE SET
            source_type = excluded.source_type,
            name = excluded.name,
            config = excluded.config,
            credentials = excluded.credentials,
            enabled = excluded.enabled,
            updated_at = CURRENT_TIMESTAMP
        """,
        (source_id, source_type, name, config_json, credentials, enabled),
    )
    conn.commit()
    logger.info(f"Saved source config: source_id={source_id}, name={name}")


def get_source_config(conn: sqlite3.Connection, source_id: str) -> dict | None:
    """Get a source configuration by source_id."""
    cursor = conn.execute(
        """
        SELECT source_id, source_type, name, config, credentials, enabled, 
               status, error_message, created_at, updated_at
        FROM source_configs 
        WHERE source_id = ?
        """,
        (source_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    
    return _row_to_dict(cursor, row)


def list_source_configs(conn: sqlite3.Connection) -> list[dict]:
    """List all source configurations."""
    cursor = conn.execute(
        """
        SELECT source_id, source_type, name, config, credentials, enabled,
               status, error_message, created_at, updated_at
        FROM source_configs 
        ORDER BY created_at DESC
        """
    )
    return [_row_to_dict(cursor, row) for row in cursor.fetchall()]


def update_source_status(
    conn: sqlite3.Connection,
    source_id: str,
    status: str,
    error_message: str | None = None,
) -> None:
    """Update the status of a source configuration."""
    cursor = conn.execute(
        """
        UPDATE source_configs 
        SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
        WHERE source_id = ?
        """,
        (status, error_message, source_id),
    )
    conn.commit()
    if cursor.rowcount > 0:
        logger.info(f"Updated source status: source_id={source_id}, status={status}")
    else:
        logger.warning(f"Source not found for status update: source_id={source_id}")


def delete_source_config(conn: sqlite3.Connection, source_id: str) -> bool:
    """Delete a source configuration and all associated mappings.
    
    Returns True if deleted, False if not found.
    """
    # First delete all mappings for this source (cascade)
    conn.execute(
        "DELETE FROM session_mappings WHERE source_id = ?",
        (source_id,),
    )
    
    # Then delete the source config
    cursor = conn.execute(
        "DELETE FROM source_configs WHERE source_id = ?",
        (source_id,),
    )
    conn.commit()
    if cursor.rowcount > 0:
        logger.info(f"Deleted source config and associated mappings: source_id={source_id}")
        return True
    logger.warning(f"Source config not found for deletion: source_id={source_id}")
    return False


# ==================== Session Mapping Operations ====================


def save_session_mapping(
    conn: sqlite3.Connection,
    mapping_id: str,
    source_id: str,
    external_user_id: str,
    agent_session_id: str,
    agent_dir: str,
    metadata: dict | None = None,
) -> None:
    """Save or update a session mapping."""
    metadata_json = json.dumps(metadata) if metadata else None
    cursor = conn.execute(
        """
        INSERT INTO session_mappings 
        (mapping_id, source_id, external_user_id, agent_session_id, agent_dir, metadata, last_message_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(mapping_id) DO UPDATE SET
            source_id = excluded.source_id,
            external_user_id = excluded.external_user_id,
            agent_session_id = excluded.agent_session_id,
            agent_dir = excluded.agent_dir,
            metadata = excluded.metadata,
            last_message_at = CURRENT_TIMESTAMP
        """,
        (mapping_id, source_id, external_user_id, agent_session_id, agent_dir, metadata_json),
    )
    conn.commit()
    logger.info(
        f"Saved session mapping: mapping_id={mapping_id}, "
        f"source_id={source_id}, external_user_id={external_user_id}"
    )


def get_session_mapping(
    conn: sqlite3.Connection,
    source_id: str,
    external_user_id: str,
) -> dict | None:
    """Get a session mapping by source_id and external_user_id."""
    cursor = conn.execute(
        """
        SELECT mapping_id, source_id, external_user_id, agent_session_id, agent_dir,
               metadata, last_message_at, created_at
        FROM session_mappings 
        WHERE source_id = ? AND external_user_id = ?
        """,
        (source_id, external_user_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    
    return _row_to_dict(cursor, row)


def get_session_mapping_by_session(
    conn: sqlite3.Connection,
    agent_session_id: str,
) -> dict | None:
    """Get a session mapping by agent_session_id."""
    cursor = conn.execute(
        """
        SELECT mapping_id, source_id, external_user_id, agent_session_id, agent_dir,
               metadata, last_message_at, created_at
        FROM session_mappings 
        WHERE agent_session_id = ?
        """,
        (agent_session_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    
    return _row_to_dict(cursor, row)


def update_mapping_last_message(
    conn: sqlite3.Connection,
    source_id: str,
    external_user_id: str,
) -> None:
    """Update the last_message_at timestamp for a session mapping."""
    cursor = conn.execute(
        """
        UPDATE session_mappings 
        SET last_message_at = CURRENT_TIMESTAMP
        WHERE source_id = ? AND external_user_id = ?
        """,
        (source_id, external_user_id),
    )
    conn.commit()
    if cursor.rowcount > 0:
        logger.debug(
            f"Updated last_message_at: source_id={source_id}, external_user_id={external_user_id}"
        )


def delete_session_mapping(conn: sqlite3.Connection, mapping_id: str) -> bool:
    """Delete a session mapping. Returns True if deleted, False if not found."""
    cursor = conn.execute(
        "DELETE FROM session_mappings WHERE mapping_id = ?",
        (mapping_id,),
    )
    conn.commit()
    if cursor.rowcount > 0:
        logger.info(f"Deleted session mapping: mapping_id={mapping_id}")
        return True
    logger.warning(f"Session mapping not found for deletion: mapping_id={mapping_id}")
    return False


def list_session_mappings(
    conn: sqlite3.Connection,
    source_id: str,
) -> list[dict]:
    """List all session mappings for a source."""
    cursor = conn.execute(
        """
        SELECT mapping_id, source_id, external_user_id, agent_session_id, agent_dir,
               metadata, last_message_at, created_at
        FROM session_mappings 
        WHERE source_id = ?
        ORDER BY last_message_at DESC
        """,
        (source_id,),
    )
    return [_row_to_dict(cursor, row) for row in cursor.fetchall()]


# ==================== Deduplication Operations ====================


def is_duplicate_message(
    conn: sqlite3.Connection,
    source_id: str,
    external_message_id: str,
) -> bool:
    """Check if a message has already been processed and mark it as processed.
    
    Uses atomic check-and-insert with UNIQUE constraint.
    
    Note: This is an atomic operation - if INSERT succeeds, the message
    is marked as processed. If the caller fails to handle the message,
    it will be permanently dropped. Callers must handle their own errors
    before calling this if different behavior is needed.
    
    Returns:
        True if message was already processed (duplicate).
        False if this is a new message (and now marked as processed).
    """
    try:
        cursor = conn.execute(
            """
            INSERT INTO processed_external_messages (source_id, external_message_id)
            VALUES (?, ?)
            """,
            (source_id, external_message_id),
        )
        conn.commit()
        return False  # New message, not a duplicate
    except sqlite3.IntegrityError:
        # Unique constraint violation - message already exists
        conn.rollback()
        logger.debug(
            f"Duplicate message detected: source_id={source_id}, "
            f"external_message_id={external_message_id}"
        )
        return True  # Duplicate


def cleanup_processed_messages(
    conn: sqlite3.Connection,
    max_age_hours: int = 24,
) -> int:
    """Clean up processed messages older than max_age_hours. Returns count of deleted rows."""
    cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
    cursor = conn.execute(
        """
        DELETE FROM processed_external_messages 
        WHERE processed_at < ?
        """,
        (cutoff_time,),
    )
    conn.commit()
    deleted_count = cursor.rowcount
    if deleted_count > 0:
        logger.info(f"Cleaned up {deleted_count} processed messages older than {max_age_hours}h")
    return deleted_count


def cleanup_inactive_mappings(
    conn: sqlite3.Connection,
    max_age_days: int = 30,
) -> int:
    """Clean up inactive session mappings older than max_age_days. Returns count of deleted rows."""
    cutoff_time = datetime.now() - timedelta(days=max_age_days)
    cursor = conn.execute(
        """
        DELETE FROM session_mappings 
        WHERE (last_message_at IS NULL AND created_at < ?)
           OR (last_message_at IS NOT NULL AND last_message_at < ?)
        """,
        (cutoff_time, cutoff_time),
    )
    conn.commit()
    deleted_count = cursor.rowcount
    if deleted_count > 0:
        logger.info(f"Cleaned up {deleted_count} inactive session mappings older than {max_age_days}d")
    return deleted_count


# ==================== Helper Functions ====================


def _row_to_dict(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a dict with proper JSON parsing."""
    columns = [desc[0] for desc in cursor.description]
    result = dict(zip(columns, row))
    
    # Parse JSON fields
    if result.get("config") and isinstance(result["config"], str):
        result["config"] = json.loads(result["config"])
    if result.get("metadata") and isinstance(result["metadata"], str):
        result["metadata"] = json.loads(result["metadata"])
    if result.get("credentials") and isinstance(result["credentials"], str):
        result["credentials"] = json.loads(result["credentials"])
    
    return result
