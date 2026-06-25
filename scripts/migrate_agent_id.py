#!/usr/bin/env python3
"""One-time migration script to fill agent_id from agent_dir.

This script populates the agent_id column by parsing it from agent_dir
for existing records where agent_id is NULL.

Tables processed:
    - sessions
    - session_mappings
    - job_queue_items
    - jobqueue (legacy table name)
    - task_queue_items

Examples:
    - ./agents/developer → developer
    - agents/leader → leader
    - /full/path/to/agents/tester → tester

Usage:
    python scripts/migrate_agent_id.py [--dry-run] [--db-path PATH]

Options:
    --dry-run     Show what would be migrated without making changes
    --db-path     Path to SQLite database (default: data/sessions.db)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_agent_id(agent_dir: str) -> str:
    """Parse agent_id from agent_dir path.
    
    Args:
        agent_dir: Path to agent directory (e.g., './agents/developer')
    
    Returns:
        The agent_id (last path component, e.g., 'developer')
    """
    if not agent_dir:
        return ""
    
    # Strip trailing slashes
    path = agent_dir.rstrip("/")
    
    # Handle forward slashes (Unix/Mac/Windows)
    if "/" in path:
        agent_id = path.rsplit("/", 1)[-1]
    else:
        agent_id = path
    
    # Handle backslashes (Windows paths)
    if "\\" in agent_id:
        agent_id = agent_id.rsplit("\\", 1)[-1]
    
    return agent_id


def get_tables_with_agent_columns(engine: Engine) -> list[str]:
    """Get list of tables that have both agent_dir and agent_id columns.
    
    Args:
        engine: SQLAlchemy engine instance
    
    Returns:
        List of table names that have both columns
    """
    tables_to_check = [
        "sessions",
        "session_mappings", 
        "job_queue_items",
        "jobqueue",  # Legacy table name
        "task_queue_items",
    ]
    
    valid_tables = []
    
    with engine.connect() as conn:
        for table_name in tables_to_check:
            try:
                # Check if table exists
                result = conn.execute(
                    text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
                )
                if not result.fetchone():
                    logger.debug(f"Table '{table_name}' does not exist, skipping")
                    continue
                
                # Check if table has both agent_dir and agent_id columns
                result = conn.execute(text(f"PRAGMA table_info({table_name})"))
                columns = [row[1] for row in result.fetchall()]
                
                if "agent_dir" in columns and "agent_id" in columns:
                    valid_tables.append(table_name)
                    logger.debug(f"Table '{table_name}' has both agent_dir and agent_id columns")
                else:
                    logger.debug(f"Table '{table_name}' missing agent_dir or agent_id column")
                    
            except Exception as e:
                logger.warning(f"Error checking table '{table_name}': {e}")
    
    return valid_tables


def migrate_table(engine: Engine, table_name: str, dry_run: bool = False) -> dict:
    """Migrate agent_id for a specific table.
    
    Args:
        engine: SQLAlchemy engine instance
        table_name: Name of the table to migrate
        dry_run: If True, don't make actual changes
    
    Returns:
        Dictionary with migration results
    """
    results = {
        "table": table_name,
        "total_rows": 0,
        "migrated": 0,
        "already_set": 0,
        "errors": 0,
        "examples": [],
    }
    
    # Determine primary key column
    pk_column = "session_id"
    if table_name == "session_mappings":
        pk_column = "mapping_id"
    elif table_name in ("job_queue_items", "jobqueue"):
        pk_column = "job_id"
    elif table_name == "task_queue_items":
        pk_column = "task_id"
    
    with engine.connect() as conn:
        # Get total rows
        result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        results["total_rows"] = result.scalar() or 0
        
        # Find rows where agent_id is NULL but agent_dir is not NULL/empty
        query = f"""
            SELECT {pk_column}, agent_dir 
            FROM {table_name} 
            WHERE agent_id IS NULL AND agent_dir IS NOT NULL AND agent_dir != ''
        """
        result = conn.execute(text(query))
        rows_to_migrate = result.fetchall()
        
        if not rows_to_migrate:
            logger.info(f"[{table_name}] No records need migration")
            results["already_set"] = results["total_rows"]
            return results
        
        results["migrated"] = len(rows_to_migrate)
        results["already_set"] = results["total_rows"] - len(rows_to_migrate)
        
        logger.info(f"[{table_name}] Found {len(rows_to_migrate)} records to migrate")
        
        for row in rows_to_migrate:
            pk_value, agent_dir = row
            new_agent_id = parse_agent_id(agent_dir)
            
            if dry_run:
                results["examples"].append({
                    "pk": pk_value,
                    "agent_dir": agent_dir,
                    "parsed_agent_id": new_agent_id,
                })
                if len(results["examples"]) < 5:
                    logger.info(f"  DRY-RUN: {pk_column}={pk_value}, "
                              f"agent_dir='{agent_dir}' → agent_id='{new_agent_id}'")
            else:
                try:
                    update_query = text(f"""
                        UPDATE {table_name} 
                        SET agent_id = :agent_id 
                        WHERE {pk_column} = :pk_value
                    """)
                    conn.execute(update_query, {"agent_id": new_agent_id, "pk_value": pk_value})
                    
                    results["examples"].append({
                        "pk": pk_value,
                        "agent_dir": agent_dir,
                        "parsed_agent_id": new_agent_id,
                    })
                    
                    if len(results["examples"]) < 5:
                        logger.info(f"  Migrated: {pk_column}={pk_value}, "
                                  f"agent_dir='{agent_dir}' → agent_id='{new_agent_id}'")
                        
                except Exception as e:
                    logger.error(f"  Error updating {pk_column}={pk_value}: {e}")
                    results["errors"] += 1
        
        if not dry_run:
            conn.commit()
            logger.info(f"[{table_name}] Migration completed: {results['migrated']} rows updated")
        else:
            logger.info(f"[{table_name}] DRY-RUN completed: {results['migrated']} rows would be migrated")
    
    return results


def show_before_state(engine: Engine) -> None:
    """Show current state before migration.
    
    Args:
        engine: SQLAlchemy engine instance
    """
    logger.info("=" * 60)
    logger.info("BEFORE MIGRATION - Current State")
    logger.info("=" * 60)
    
    tables = get_tables_with_agent_columns(engine)
    
    with engine.connect() as conn:
        for table_name in tables:
            try:
                # Count NULL agent_id
                result = conn.execute(text(f"""
                    SELECT COUNT(*) FROM {table_name} 
                    WHERE agent_id IS NULL AND agent_dir IS NOT NULL AND agent_dir != ''
                """))
                null_count = result.scalar() or 0
                
                # Total count
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                total = result.scalar() or 0
                
                # Sample records
                result = conn.execute(text(f"""
                    SELECT agent_dir, agent_id FROM {table_name} 
                    WHERE agent_id IS NULL AND agent_dir IS NOT NULL AND agent_dir != ''
                    LIMIT 3
                """))
                samples = result.fetchall()
                
                logger.info(f"Table: {table_name}")
                logger.info(f"  Total rows: {total}")
                logger.info(f"  Rows with NULL agent_id: {null_count}")
                
                if samples:
                    logger.info(f"  Sample records (NULL agent_id):")
                    for agent_dir, agent_id in samples:
                        logger.info(f"    agent_dir='{agent_dir}', agent_id={agent_id}")
                        
            except Exception as e:
                logger.warning(f"  Error checking {table_name}: {e}")
    
    logger.info("=" * 60)


def show_after_state(engine: Engine, results: list[dict]) -> None:
    """Show state after migration.
    
    Args:
        engine: SQLAlchemy engine instance
        results: List of migration results per table
    """
    logger.info("=" * 60)
    logger.info("AFTER MIGRATION - Summary")
    logger.info("=" * 60)
    
    total_migrated = sum(r["migrated"] for r in results)
    total_errors = sum(r["errors"] for r in results)
    
    for result in results:
        if result["migrated"] > 0:
            logger.info(f"Table: {result['table']}")
            logger.info(f"  Migrated: {result['migrated']}")
            logger.info(f"  Errors: {result['errors']}")
            
            if result["examples"]:
                logger.info(f"  Examples:")
                for ex in result["examples"][:3]:
                    logger.info(f"    {ex['agent_dir']} → {ex['parsed_agent_id']}")
    
    logger.info("-" * 60)
    logger.info(f"TOTAL: {total_migrated} rows migrated, {total_errors} errors")
    logger.info("=" * 60)


def run_migration(db_path: str, dry_run: bool = False) -> list[dict]:
    """Run the full migration.
    
    Args:
        db_path: Path to SQLite database
        dry_run: If True, don't make actual changes
    
    Returns:
        List of migration results per table
    """
    # Create engine
    db_file = Path(db_path)
    if not db_file.exists():
        # Try relative to project root
        project_root = Path(__file__).parent.parent
        db_file = project_root / db_path
        if not db_file.exists():
            logger.error(f"Database not found: {db_path}")
            sys.exit(1)
    
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    
    logger.info(f"Using database: {db_file}")
    
    # Show before state
    show_before_state(engine)
    
    # Get tables to migrate
    tables = get_tables_with_agent_columns(engine)
    
    if not tables:
        logger.warning("No tables found with agent_dir and agent_id columns")
        return []
    
    # Run migration for each table
    results = []
    for table_name in tables:
        logger.info("-" * 40)
        result = migrate_table(engine, table_name, dry_run=dry_run)
        results.append(result)
    
    # Show after state
    show_after_state(engine, results)
    
    return results


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate agent_id from agent_dir in SQLite database"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes",
    )
    parser.add_argument(
        "--db-path",
        default="data/sessions.db",
        help="Path to SQLite database (default: data/sessions.db)",
    )
    
    args = parser.parse_args()
    
    if args.dry_run:
        logger.info("DRY-RUN MODE - No changes will be made")
        logger.info("-" * 40)
    
    results = run_migration(args.db_path, dry_run=args.dry_run)
    
    # Summary
    total_migrated = sum(r["migrated"] for r in results)
    total_errors = sum(r["errors"] for r in results)
    
    if args.dry_run:
        if total_migrated > 0:
            logger.info(f"\nDRY-RUN: {total_migrated} records would be migrated")
            logger.info("Run without --dry-run to apply changes")
        else:
            logger.info("\nNo records need migration")
    else:
        if total_errors > 0:
            logger.warning(f"\nCompleted with {total_errors} errors")
            sys.exit(1)
        elif total_migrated > 0:
            logger.info(f"\nSuccessfully migrated {total_migrated} records")
        else:
            logger.info("\nNo records needed migration (already up to date)")


if __name__ == "__main__":
    main()
