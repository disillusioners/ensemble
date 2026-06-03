# Phase 1: Config System

> **Effort**: 4-6 hours
> **Priority**: High
> **Risk**: Medium (config changes can break deployments)

## Goal

Add `ensemble.json` support with database type selection. Auto-create on first start if `DATABASE_URL` env var is present. Ensure backward compatibility with existing `config.yaml` deployments.

## Decisions

- **Auto-create `ensemble.json` on first start** if `DATABASE_URL` env var is detected
- `config.yaml` remains the primary config source
- `ensemble.json` overrides `config.yaml` when present
- Fallback to SQLite via config edit (set `"database": "sqlite"`)

## Changes

### 1. Update `PersistenceConfig`

**File**: `daemon/config.py`

**Before**:
```python
class PersistenceConfig(BaseModel):
    database: str = "data/ensemble.db"
    checkpoint_database: str = "data/checkpoints.db"
```

**After**:
```python
class PostgresConfig(BaseModel):
    """PostgreSQL connection configuration."""
    url: str = Field(..., description="PostgreSQL connection URL (postgresql://...)")
    pool_size: int = Field(default=5, description="SQLAlchemy pool size")
    max_overflow: int = Field(default=10, description="SQLAlchemy max overflow")
    pool_timeout: int = Field(default=30, description="Pool checkout timeout (seconds)")

class SqliteConfig(BaseModel):
    """SQLite database paths."""
    instances_db: str = Field(default="data/ensemble.db", description="SQLModel database path")
    checkpoints_db: str = Field(default="data/checkpoints.db", description="LangGraph checkpoint DB path")

class PersistenceConfig(BaseModel):
    """Persistence configuration.
    
    Supports both SQLite (default) and PostgreSQL backends. The database
    type is determined by the 'database' field. When 'database' is 'postgres',
    postgres config is required. When 'sqlite', sqlite config is used.
    """
    database: Literal["sqlite", "postgres"] = Field(
        default="sqlite",
        description="Database backend type"
    )
    sqlite: SqliteConfig = Field(default_factory=SqliteConfig)
    postgres: PostgresConfig | None = Field(
        default=None,
        description="PostgreSQL config (required when database='postgres')"
    )
    
    def model_post_init(self, __context):
        """Validate that required config is present for selected backend."""
        if self.database == "postgres" and self.postgres is None:
            raise ValueError("postgres config required when database='postgres'")
    
    @property
    def is_postgres(self) -> bool:
        return self.database == "postgres"
```

### 2. Add `EnsembleConfig` Class

**File**: `daemon/config.py` (new class, same file)

```python
class EnsembleConfig(BaseModel):
    """Root configuration for ensemble.json.
    
    This file is auto-generated on first start if DATABASE_URL env var
    is present. It takes priority over config.yaml when both exist.
    """
    database: Literal["sqlite", "postgres"] = "sqlite"
    sqlite: SqliteConfig = Field(default_factory=SqliteConfig)
    postgres: PostgresConfig | None = None
    
    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "EnsembleConfig | None":
        """Auto-detect DATABASE_URL env var and generate config.
        
        Returns None if DATABASE_URL is not set.
        """
        env = env or os.environ
        db_url = env.get("DATABASE_URL")
        if not db_url:
            return None
        
        return cls(
            database="postgres",
            postgres=PostgresConfig(
                url=db_url,
                pool_size=int(env.get("POSTGRES_POOL_SIZE", "5")),
                max_overflow=int(env.get("POSTGRES_MAX_OVERFLOW", "10")),
            ),
        )
    
    def to_file(self, path: Path) -> None:
        """Write config to ensemble.json."""
        path.write_text(self.model_dump_json(indent=2))
    
    @classmethod
    def from_file(cls, path: Path) -> "EnsembleConfig":
        """Load config from ensemble.json."""
        if not path.exists():
            raise FileNotFoundError(f"ensemble.json not found at {path}")
        return cls.model_validate_json(path.read_text())
```

### 3. Add Config Loader Logic

**File**: `daemon/config.py` (new function)

```python
def load_persistence_config(
    config_yaml_path: Path = Path("config.yaml"),
    ensemble_json_path: Path = Path("ensemble.json"),
) -> PersistenceConfig:
    """Load persistence config with priority: ensemble.json > config.yaml > auto-detect.
    
    Priority order:
    1. ensemble.json (if exists) - explicit user config
    2. config.yaml persistence section - existing config
    3. Auto-detect DATABASE_URL env var - generate ensemble.json
    4. Default to SQLite
    """
    # 1. ensemble.json takes priority
    if ensemble_json_path.exists():
        logger.info(f"Loading persistence config from {ensemble_json_path}")
        ensemble = EnsembleConfig.from_file(ensemble_json_path)
        return PersistenceConfig(
            database=ensemble.database,
            sqlite=ensemble.sqlite,
            postgres=ensemble.postgres,
        )
    
    # 2. config.yaml persistence section
    if config_yaml_path.exists():
        logger.info(f"Loading persistence config from {config_yaml_path}")
        with config_yaml_path.open() as f:
            yaml_data = yaml.safe_load(f)
        persistence = yaml_data.get("persistence", {})
        return PersistenceConfig(**persistence)
    
    # 3. Auto-detect DATABASE_URL and generate ensemble.json
    ensemble = EnsembleConfig.from_env()
    if ensemble:
        logger.info(
            f"DATABASE_URL detected. Auto-creating {ensemble_json_path} "
            f"with database={ensemble.database}"
        )
        ensemble.to_file(ensemble_json_path)
        return PersistenceConfig(
            database=ensemble.database,
            sqlite=ensemble.sqlite,
            postgres=ensemble.postgres,
        )
    
    # 4. Default to SQLite
    logger.info("No config found. Defaulting to SQLite.")
    return PersistenceConfig(database="sqlite")
```

### 4. Update `EnsembleConfig` App

**File**: `daemon/api.py` (lifespan startup)

**Before**:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load config
    config = load_config()
    # ... initialize manager ...
```

**After**:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load config with ensemble.json support
    config = load_config()
    persistence_config = load_persistence_config(
        config_yaml_path=Path("config.yaml"),
        ensemble_json_path=Path("ensemble.json"),
    )
    # ... initialize manager with persistence_config ...
```

## File Structure

### New Files

```
daemon/
├── ensemble_config.py          # EnsembleConfig class (optional, can keep in config.py)
```

### Modified Files

```
daemon/
├── config.py                   # Add PostgresConfig, SqliteConfig, EnsembleConfig, loader
├── api.py                      # Update lifespan to use new loader
```

## Testing

### Unit Test: Config Loading Priority

```python
# tests/unit/test_config_loading.py
import pytest
from pathlib import Path
from daemon.config import load_persistence_config, EnsembleConfig

def test_ensemble_json_takes_priority(tmp_path):
    """ensemble.json overrides config.yaml."""
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("""
persistence:
  database: sqlite
  sqlite:
    instances_db: data/ensemble.db
""")
    
    json_path = tmp_path / "ensemble.json"
    json_path.write_text("""
{
  "database": "postgres",
  "postgres": {
    "url": "postgresql://localhost/test"
  }
}
""")
    
    config = load_persistence_config(yaml_path, json_path)
    assert config.database == "postgres"
    assert config.postgres.url == "postgresql://localhost/test"


def test_config_yaml_fallback(tmp_path):
    """config.yaml used when ensemble.json doesn't exist."""
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("""
persistence:
  database: sqlite
""")
    
    json_path = tmp_path / "ensemble.json"
    # json_path doesn't exist
    
    config = load_persistence_config(yaml_path, json_path)
    assert config.database == "sqlite"


def test_auto_detect_database_url(tmp_path, monkeypatch):
    """DATABASE_URL env var triggers ensemble.json auto-creation."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    
    yaml_path = tmp_path / "config.yaml"
    json_path = tmp_path / "ensemble.json"
    
    config = load_persistence_config(yaml_path, json_path)
    assert config.database == "postgres"
    assert json_path.exists(), "ensemble.json should be auto-created"


def test_default_sqlite_when_no_config(tmp_path, monkeypatch):
    """Default to SQLite when no config and no env var."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    
    yaml_path = tmp_path / "config.yaml"  # doesn't exist
    json_path = tmp_path / "ensemble.json"  # doesn't exist
    
    config = load_persistence_config(yaml_path, json_path)
    assert config.database == "sqlite"
```

### Validation Test: Postgres Config Required

```python
def test_postgres_config_required():
    """PostgresConfig required when database='postgres'."""
    from daemon.config import PersistenceConfig
    from pydantic import ValidationError
    
    with pytest.raises(ValidationError, match="postgres config required"):
        PersistenceConfig(database="postgres", postgres=None)
```

## Acceptance Criteria

- [ ] `PersistenceConfig` supports `"sqlite"` and `"postgres"` types
- [ ] `EnsembleConfig` class with `from_env()` and `from_file()` methods
- [ ] `load_persistence_config()` implements priority: ensemble.json > config.yaml > auto-detect > default
- [ ] `DATABASE_URL` env var triggers `ensemble.json` auto-creation
- [ ] Backward compatible with existing `config.yaml` deployments
- [ ] Unit tests cover all priority cases
- [ ] Validation tests cover required field enforcement
- [ ] No breaking changes to existing config loading

## Rollback Plan

If issues arise:
1. Revert `config.py` changes
2. Revert `api.py` lifespan changes
3. Existing config loading works as before

No data migration needed—config-only changes.

## Estimated Diff Size

- 1 file modified: `daemon/config.py` (+150 lines)
- 1 file modified: `daemon/api.py` (+10 lines)

**Total**: 2 files, ~160 lines added

## Next Phase

[Phase 2: Driver + Checkpoint Abstraction](./03-phase-2-driver-checkpoint.md)
