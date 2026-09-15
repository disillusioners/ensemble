"""Database migration models."""

from sqlmodel import Field, SQLModel


class SchemaMigration(SQLModel, table=True):
    """Tracks applied database migrations.
    
    Attributes:
        version: Migration version in YYYYMMDD_HHMMSS format.
        name: Human-readable migration name.
        applied_at: ISO 8601 timestamp when the migration was applied.
        execution_time_ms: Duration of migration execution in milliseconds.
        checksum: SHA-256 hash of the migration file content.
        precondition: Declared precondition token parsed from the file
            header (e.g. ``sqlite>=3.35.0``), stored for discoverability.
            NULL = unconditional migration.
        skip_reason: When a declared precondition FAILED, the row is
            still recorded (so the migration never re-fires) with this
            column carrying the evaluation failure reason. NULL = the
            migration executed.
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
    execution_time_ms: int | None = Field(
        default=None,
        description="Execution duration in ms"
    )
    checksum: str | None = Field(
        default=None,
        description="SHA-256 hash of migration content"
    )
    precondition: str | None = Field(
        default=None,
        description="Declared precondition token (e.g. sqlite>=3.35.0); NULL = unconditional"
    )
    skip_reason: str | None = Field(
        default=None,
        description="Why execution was skipped (precondition failure); NULL = executed"
    )
