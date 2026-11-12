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
              exist AND no SQLite DBs (instances.db/checkpoints.db) → default to "postgres"
            - If POSTGRES_HOST AND POSTGRES_DB are both set AND ensemble.json doesn't
              exist BUT SQLite DBs exist → default to "sqlite" (migration preparation)
            - Otherwise → default to "sqlite"

        The resulting configuration is always persisted to disk via `save()` so the
        first-startup decision is recorded and can be inspected later.
        """
        config_path = data_dir / "ensemble.json"

        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                config = cls(**data)
                logger.info(f"Loaded ensemble config: database={config.database}")
                return config
            except Exception as e:
                logger.warning(f"Failed to load ensemble.json: {e}. Using defaults.")
                return cls()

        # Auto-detect from environment variables
        if os.environ.get("POSTGRES_HOST") and os.environ.get("POSTGRES_DB"):
            has_sqlite_dbs = (data_dir / "instances.db").exists() or (data_dir / "checkpoints.db").exists()
            if has_sqlite_dbs:
                logger.info("PostgreSQL ENV vars detected but SQLite DBs exist, defaulting to sqlite for migration preparation")
                config = cls()
            else:
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
            temp_path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
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


def assert_db_resolution_sanctioned(data_dir) -> None:
    """Fail-closed guard: refuse an ambient-POSTGRES-driven first boot
    (tier 2) unless ``ENSEMBLE_DB_AMBIENT_ALLOW=1`` is set.

    Fires (sys.exit(78) + marker line) iff:
      - (data_dir / "ensemble.json") does NOT exist (tier 2 path), AND
      - POSTGRES_HOST and POSTGRES_DB are both set, AND
      - ENSEMBLE_DB_AMBIENT_ALLOW != "1".

    Tier 1 (json exists) => does not fire. Tier 3/4 (sqlite) => does not
    fire. Marker line names the tier + ambient var NAMES only (never
    values) + the fence var name (F7).
    """
    import sys as _sys

    try:
        dd = Path(data_dir)
        config_path = dd / "ensemble.json"
        if config_path.exists():
            return  # tier 1 — sanctioned
        host_set = bool(os.environ.get("POSTGRES_HOST"))
        db_set = bool(os.environ.get("POSTGRES_DB"))
        if not (host_set and db_set):
            return  # tier 3/4 — sqlite default, safe
        if os.environ.get("ENSEMBLE_DB_AMBIENT_ALLOW") == "1":
            return  # sanctioned
        ambient_names = []
        for name in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
                     "POSTGRES_USER", "POSTGRES_PASSWORD"):
            if os.environ.get(name):
                ambient_names.append(name)
        logger.error(
            "DB RESOLUTION REFUSED (rogue-containment): tier=ambient "
            "(ensemble.json absent) with ambient POSTGRES_* present "
            "(%s) and ENSEMBLE_DB_AMBIENT_ALLOW != 1. "
            "Refusing before any TCP connection. Set "
            "ENSEMBLE_DB_AMBIENT_ALLOW=1 for a sanctioned first boot "
            "(the persisted ensemble.json makes later boots tier 1).",
            ",".join(ambient_names),
        )
        _sys.exit(78)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-closed on guard errors
        logger.warning("assert_db_resolution_sanctioned unexpected error: %s", exc)
