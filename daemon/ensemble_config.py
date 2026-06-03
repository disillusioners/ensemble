"""Ensemble configuration — database selection and connection settings.

The `EnsembleConfig` model is loaded from (or auto-created at) `<data_dir>/ensemble.json`.
On first startup, the configuration is auto-detected from environment variables:
    - If POSTGRES_HOST AND POSTGRES_DB are both set → default to "postgres"
    - Otherwise → default to "sqlite"

Environment variables take precedence over file values at runtime, which supports
operations like credential rotation without rewriting the config file.
"""

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class PostgresConfig(BaseModel):
    """PostgreSQL connection settings."""

    host: str = "localhost"
    port: int = 5432
    db: str = "ensemble"
    user: str = "ensemble"
    password: str = ""


class SqliteConfig(BaseModel):
    """SQLite database file paths."""

    instances_db: str = "./data/instances.db"
    checkpoints_db: str = "./data/checkpoints.db"


class EnsembleConfig(BaseModel):
    """Ensemble configuration loaded from ensemble.json."""

    database: str = "sqlite"  # "sqlite" or "postgres"
    postgres: PostgresConfig = PostgresConfig()
    sqlite: SqliteConfig = SqliteConfig()

    @property
    def is_postgres(self) -> bool:
        return self.database == "postgres"

    @property
    def is_sqlite(self) -> bool:
        return self.database == "sqlite"

    @classmethod
    def load_or_create(cls, data_dir: Path) -> "EnsembleConfig":
        """Load ensemble.json from data_dir, or create with auto-detected defaults.

        Auto-detection rule:
            - If ensemble.json exists → load it
            - If POSTGRES_HOST AND POSTGRES_DB are both set AND ensemble.json doesn't
              exist → default to "postgres"
            - Otherwise → default to "sqlite"

        The resulting configuration is always persisted to disk via `save()` so the
        first-startup decision is recorded and can be inspected later.
        """
        config_path = data_dir / "ensemble.json"

        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                config = cls(**data)
                logger.info(f"Loaded ensemble config: database={config.database}")
                return config
            except Exception as e:
                logger.warning(f"Failed to load ensemble.json: {e}. Using defaults.")
                return cls()

        # Auto-detect from environment variables
        if os.environ.get("POSTGRES_HOST") and os.environ.get("POSTGRES_DB"):
            logger.info("PostgreSQL ENV vars detected, defaulting to postgres")
            config = cls(database="postgres")
        else:
            logger.info("No PostgreSQL ENV vars detected, defaulting to sqlite")
            config = cls()

        # Save the auto-created config (atomic write)
        config.save(data_dir)
        return config

    def save(self, data_dir: Path) -> None:
        """Save config to ensemble.json with atomic write (write to temp, then os.replace)."""
        config_path = data_dir / "ensemble.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = config_path.with_suffix(".json.tmp")
        try:
            temp_path.write_text(self.model_dump_json(indent=2) + "\n")
            os.replace(str(temp_path), str(config_path))
            logger.info(f"Saved ensemble config to {config_path}")
        except Exception as e:
            logger.error(f"Failed to save ensemble.json: {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise

    def get_postgres_url(self) -> str:
        """Get PostgreSQL connection URL for async engine creation.

        Format: postgresql+asyncpg://user:password@host:port/db

        Environment variables override file values, supporting credential rotation
        without rewriting ensemble.json.
        """
        pg = self.postgres
        # Override with ENV vars if set (ENV takes precedence over file)
        host = os.environ.get("POSTGRES_HOST", pg.host)
        port = os.environ.get("POSTGRES_PORT", str(pg.port))
        db = os.environ.get("POSTGRES_DB", pg.db)
        user = os.environ.get("POSTGRES_USER", pg.user)
        password = os.environ.get("POSTGRES_PASSWORD", pg.password)

        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"

    @property
    def postgres_env_available(self) -> bool:
        """Check if PostgreSQL ENV vars are available."""
        return bool(os.environ.get("POSTGRES_HOST") and os.environ.get("POSTGRES_DB"))
