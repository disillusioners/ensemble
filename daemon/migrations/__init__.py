"""Database migration system.

This module provides file-based database migration support with versioned,
reversible schema changes.

Example:
    from daemon.migrations import MigrationRunner
    
    runner = MigrationRunner(engine)
    applied = runner.run_pending_migrations()
    status = runner.get_migration_status()
"""

# Import runner components directly to avoid circular imports
from .runner import MigrationError, MigrationFile, MigrationRunner

# Lazy import for SchemaMigration to avoid circular import with daemon.repositories
def __getattr__(name: str):
    if name == "SchemaMigration":
        from .models import SchemaMigration
        return SchemaMigration
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "MigrationRunner",
    "MigrationFile",
    "MigrationError",
    "SchemaMigration",
]
