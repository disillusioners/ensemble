#!/usr/bin/env python3
"""One-time migration: rename agent_id 'coder' → 'developer' in all tables.

Updates agent_id and agent_dir columns across all 6 tables. Supports both
PostgreSQL and SQLite (auto-detects from connection URL).

Tables:
    - instances (agent_id, agent_dir)
    - instance_mappings (agent_id, agent_dir)
    - job_queue_items (agent_id, agent_dir)
    - dead_letter_items (agent_id, agent_dir)
    - projects (creator_agent_id)
    - jobqueue (legacy, if exists)

Usage:
    python scripts/migrate_coder_to_developer.py [--dry-run] [--db-url URL]
    python scripts/migrate_coder_to_developer.py --dry-run
    python scripts/migrate_coder_to_developer.py --db-url postgresql://user:pass@localhost/db
"""
import argparse
import os
import sys

from sqlalchemy import create_engine, text

# NOTE: coder→developer migration is also handled in:
#   daemon/manager.py:_ensure_postgres_columns() (PostgreSQL runtime)
#   daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql (SQLite production)

# Tables and their UPDATE SQL for the coder→developer rename.
# Each entry: (table_name, column_with_coder, full_update_sql)
MIGRATION_STATEMENTS = [
    ("instances", "agent_id",
     "UPDATE instances SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'"),
    ("instance_mappings", "agent_id",
     "UPDATE instance_mappings SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'"),
    ("job_queue_items", "agent_id",
     "UPDATE job_queue_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'"),
    ("dead_letter_items", "agent_id",
     "UPDATE dead_letter_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'"),
    ("projects", "creator_agent_id",
     "UPDATE projects SET creator_agent_id = 'developer' WHERE creator_agent_id = 'coder'"),
    # Legacy table (may not exist)
    ("jobqueue", "agent_id",
     "UPDATE jobqueue SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'"),
]


def get_db_url(args):
    """Resolve the database URL from args or environment."""
    if args.db_url:
        return args.db_url
    return os.environ.get("DATABASE_URL", "postgresql://localhost:5432/ensemble")


def is_postgres(db_url: str) -> bool:
    """Detect if the DB URL points to PostgreSQL."""
    return "postgresql" in db_url or "postgres" in db_url


def dry_run(engine):
    """Show affected row counts without modifying data."""
    print("=== DRY RUN — no changes will be made ===\n")
    total = 0
    for table, column, _ in MIGRATION_STATEMENTS:
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE {column} = 'coder'")
                )
                count = result.scalar()
                print(f"  {table:25s} ({column}): {count} row(s) to update")
                total += count
        except Exception as e:
            print(f"  {table:25s} ({column}): SKIPPED ({e})")
    print(f"\n  Total rows to update: {total}")
    print("\n=== DRY RUN complete — run without --dry-run to apply ===")


def run_migration(engine, pg: bool):
    """Execute the migration."""
    print(f"=== Migrating coder → developer ({'PostgreSQL' if pg else 'SQLite'}) ===\n")
    for table, column, sql in MIGRATION_STATEMENTS:
        if table == "jobqueue" and pg:
            # PostgreSQL: wrap in exception handler for legacy table
            pg_sql = (
                f"DO $$ BEGIN {sql}; "
                "EXCEPTION WHEN undefined_table THEN NULL; END $$"
            )
            try:
                with engine.begin() as conn:
                    conn.execute(text(pg_sql))
                print(f"  ✓ {table}")
            except Exception as e:
                print(f"  ⚠ {table}: {e}")
        else:
            try:
                with engine.begin() as conn:
                    conn.execute(text(sql))
                print(f"  ✓ {table}")
            except Exception as e:
                print(f"  ⚠ {table}: {e} (skipped)")
    print("\n=== Migration complete ===")


def main():
    parser = argparse.ArgumentParser(
        description="Rename agent_id 'coder' → 'developer' in all tables"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show affected row counts without modifying data"
    )
    parser.add_argument(
        "--db-url", default=None,
        help="Database URL (default: env DATABASE_URL or postgresql://localhost:5432/ensemble)"
    )
    args = parser.parse_args()

    db_url = get_db_url(args)
    print(f"Database: {db_url}\n")
    engine = create_engine(db_url)
    pg = is_postgres(db_url)

    if args.dry_run:
        dry_run(engine)
    else:
        run_migration(engine, pg)


if __name__ == "__main__":
    main()
