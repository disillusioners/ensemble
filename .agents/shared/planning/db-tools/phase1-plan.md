# Phase 1: Connection Registry Layer

## Objective
Create the `db_connections` SQLModel table and its repository (`DbConnectionRepository`) with encrypted credential storage. This is the persistence layer that all DB tools depend on. Must work on both SQLite and PostgreSQL.

## Coupling
- **Depends on:** None (foundation layer)
- **Coupling type:** — (root phase)
- **Shared files with other phases:** `daemon/repositories/db_connection/models.py` (read by Phase 2 pool service, Phase 3 tools)
- **Shared APIs/interfaces:** `DbConnectionRepository` class (consumed by Phase 3 tools), `DbConnectionConfig` model (read by Phase 2)
- **Why this coupling:** Phase 2+ need the connection model to read params; Phase 3 needs the repository to CRUD connections. Both only depend on the interface, not implementation details.

## Context
- This is the first phase — no prior deliverables
- Key decision D2: Single table, encrypted `credentials` column (password stored encrypted, not as a column)
- Key decision D1: Reuse `daemon.sources.credentials.CredentialManager` for encryption
- **Reference pattern:** `daemon/repositories/source/` (models.py + repository.py) — follow this exact structure

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `DbConnectionConfig` SQLModel | Table `db_connections`. Columns: `id` (UUID PK), `connection_name` (str, unique index), `db_type` (str, e.g. "postgres"), `host` (str), `port` (int, nullable), `database` (str, nullable), `username` (str, nullable), `credentials` (encrypted str \| None), `ssl_mode` (str, default "prefer"), `created_at` (ISO str), `updated_at` (ISO str). | `daemon/repositories/db_connection/models.py` (NEW) |
| 2 | Create `DbConnectionRepository` | CRUD operations: `create()`, `get_by_name()`, `list_all()`, `delete()`. Takes `engine: Engine` ONLY — **NO `credential_manager` parameter** (N1). The repository stores/retrieves opaque encrypted strings. Encryption/decryption is the TOOL layer's responsibility (matching the `source_configs` pattern). Methods accept/return an opaque `credentials: str | None` field. | `daemon/repositories/db_connection/repository.py` (NEW) |
| 3 | Create `__init__.py` | Export `DbConnectionConfig`, `DbConnectionRepository`. | `daemon/repositories/db_connection/__init__.py` (NEW) |
| 4 | Add factory function | Add `create_db_connection_repository(config=None, engine=None, create_tables=True)` to `daemon/repositories/factory.py`. Follow exact pattern of `create_source_repository()` — NO `credential_manager` param (N1). Add to `__all__`. | `daemon/repositories/factory.py` (MODIFY) |
| 5 | Wire model import in `manager.py` (W5, N10) | **CRITICAL:** Import `DbConnectionConfig` at the **MODULE LEVEL** of `daemon/manager.py` (top of file, alongside existing model imports like `Instance`, `SourceConfig`, etc.) — NOT inline in `__init__`. This ensures the model is registered in `SQLModel.metadata` BEFORE `SQLModel.metadata.create_all(engine)` runs at startup. | `daemon/manager.py` (MODIFY) |
| 6 | Write unit tests | Test CRUD operations using in-memory SQLite engine. Repository stores opaque strings — test that `create()` stores what it's given and `get_by_name()` returns it unchanged (no encryption logic in repo). Test `to_public_dict()` never includes credentials. Test unique name constraint. | `tests/test_db_connection_repository.py` (NEW) |

## Key Files

### NEW Files
- `daemon/repositories/db_connection/models.py` — SQLModel table definition
- `daemon/repositories/db_connection/repository.py` — Repository class with encrypted CRUD
- `daemon/repositories/db_connection/__init__.py` — Package exports
- `tests/test_db_connection_repository.py` — Unit tests

### MODIFIED Files
- `daemon/repositories/factory.py` — Add `create_db_connection_repository()` factory function
- `daemon/manager.py` — Import `DbConnectionConfig` model so it's registered in `SQLModel.metadata` before `create_all()` runs at startup (W5)

## Detailed Design

### `DbConnectionConfig` Model

```python
class DbConnectionConfig(SQLModel, table=True):
    __tablename__ = "db_connections"
    __table_args__ = (
        Index("idx_db_connections_name", "connection_name"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    connection_name: str = Field(unique=True, index=True, max_length=128)
    db_type: str = Field(max_length=32)  # "postgres", "mysql" (future), etc.
    host: str = Field(max_length=256)
    port: int | None = Field(default=None)
    database: str | None = Field(default=None)
    username: str | None = Field(default=None)
    credentials: str | None = None  # Encrypted JSON: {"password": "..."}
    ssl_mode: str = Field(default="prefer", max_length=32)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_public_dict(self) -> dict[str, Any]:
        """Return connection info WITHOUT secrets. Used by db_conn_list."""
        return {
            "connection_name": self.connection_name,
            "db_type": self.db_type,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "ssl_mode": self.ssl_mode,
            "has_password": self.credentials is not None,
        }
```

### `DbConnectionRepository` Key Methods

```python
class DbConnectionRepository:
    def __init__(self, engine: Engine):
        """Initialize with engine ONLY. No credential_manager (N1).
        
        The repository stores/retrieves opaque encrypted strings. 
        Encryption/decryption is the TOOL layer's responsibility,
        matching the source_configs pattern.
        """
        self.engine = engine

    def create(self, connection_name, db_type, host, port=None, database=None,
               username=None, credentials=None, ssl_mode="prefer") -> DbConnectionConfig:
        """Create a connection. `credentials` is an opaque encrypted string
        (encrypted by the caller/tool, NOT by the repository)."""

    def get_by_name(self, connection_name: str) -> DbConnectionConfig | None:
        """Get connection by name. Returns model with opaque `credentials` field.
        Raises ValueError if not found."""

    def get_credentials(self, connection_name: str) -> str | None:
        """Return the opaque encrypted credentials string. 
        Decryption is the caller's responsibility (N1)."""

    def list_all(self) -> list[DbConnectionConfig]:
        """List all connections."""

    def list_public(self) -> list[dict[str, Any]]:
        """List all connections as public-safe dicts (no secrets)."""

    def delete(self, connection_name: str) -> bool:
        """Delete by name. Returns True if deleted, False if not found."""
```

> **Note (N1):** The repository has NO `get_decrypted_password()` method and NO `credential_manager`. It only exposes `get_credentials()` which returns the opaque encrypted string. The tool layer decrypts it when needed (e.g., `db_postgres_dml_select` decrypts before building DSN). This matches the `source_configs` pattern where `routers/sources.py` handles encryption, not the repository.

### Factory Function

```python
# In daemon/repositories/factory.py

def create_db_connection_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> DbConnectionRepository:
    """Create a DbConnectionRepository.
    
    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance.
        create_tables: If True, create tables if they don't exist.
    
    Note (N1): NO credential_manager parameter. The repository stores opaque
    encrypted strings. Encryption/decryption is the tool layer's responsibility,
    matching the source_configs pattern where routers handle encryption.
    """
    if engine is None:
        if config is None:
            raise ValueError("Either config or engine must be provided")
        engine = create_engine_from_config(config)
    
    if create_tables:
        SQLModel.metadata.create_all(engine)
    
    return DbConnectionRepository(engine)
```

## Constraints
- Must work on both SQLite and PostgreSQL (no SQLite-specific SQL)
- **N1 — Repository has NO credential_manager:** `DbConnectionRepository.__init__(self, engine: Engine)` — no encryption parameter. The repository stores/retrieves opaque encrypted strings. This matches the `source_configs` pattern where `routers/sources.py` handles encryption, not the repository.
- Passwords must never be stored as plaintext — encryption happens in the TOOL layer before calling the repository
- `list_public()` and `to_public_dict()` must NEVER include the decrypted password or the encrypted credentials string
- Follow the existing `source/repository.py` pattern exactly for consistency
- Column names must be lowercase snake_case
- UUIDs as string PKs (consistent with `source_configs` pattern)
- ISO-format datetime strings (not native datetime — consistent with existing models)
- **W5/N10 — Model import at MODULE LEVEL:** `DbConnectionConfig` must be imported at the **top of `manager.py`** (module level), NOT inline in `__init__`. SQLModel only registers tables that have been imported into the Python process at module load time. Inline imports in `__init__` are too late if `create_all()` runs before the import line.

## Deliverables
- [ ] `daemon/repositories/db_connection/models.py` with `DbConnectionConfig` table
- [ ] `daemon/repositories/db_connection/repository.py` with `DbConnectionRepository` (NO credential_manager — N1)
- [ ] `daemon/repositories/db_connection/__init__.py` with exports
- [ ] `create_db_connection_repository()` added to `factory.py` (no credential_manager param — N1)
- [ ] `DbConnectionConfig` imported at MODULE LEVEL in `manager.py` (W5, N10)
- [ ] `tests/test_db_connection_repository.py` passing
- [ ] Table auto-creates on both SQLite and PostgreSQL
