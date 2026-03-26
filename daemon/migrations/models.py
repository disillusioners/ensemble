"""Database migration models."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class SchemaMigration(SQLModel, table=True):
    """Tracks applied database migrations.
    
    Attributes:
        version: Migration version in YYYYMMDD_HHMMSS format.
        name: Human-readable migration name.
        applied_at: ISO 8601 timestamp when the migration was applied.
        execution_time_ms: Duration of migration execution in milliseconds.
        checksum: SHA-256 hash of the migration file content.
    """
    
    __tablename__ = "schema_migrations"
    
    version: str = Field(
        primary_key=True,
        description="Migration version (YYYYMMDD_HHMMSS)"
    )
    name: str = Field(
        description="Human-readable migration name"
    )
    applied_at: str = Field(
        description="ISO 8601 timestamp when applied"
    )
    execution_time_ms: Optional[int] = Field(
        default=None,
        description="Execution duration in ms"
    )
    checksum: Optional[str] = Field(
        default=None,
        description="SHA-256 hash of migration content"
    )
