"""Tests for daemon.ensemble_config.EnsembleConfig (Phase 1 feature).

Covers:
- Auto-creation of ensemble.json when missing
- Loading existing ensemble.json
- Saving values that round-trip
- Postgres env-var auto-detection
- SQLite default fallback
- Atomic write semantics (temp + os.replace)
- Graceful handling of invalid JSON
"""

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from daemon.ensemble_config import EnsembleConfig, PostgresConfig, SqliteConfig


class TestLoadOrCreate:
    """Tests for EnsembleConfig.load_or_create()."""

    def test_load_non_existent_config_creates_with_sqlite_default(self, tmp_path: Path):
        """No config file + no Postgres env → auto-create with database='sqlite'."""
        # Ensure no POSTGRES_* env vars
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            os.environ.pop(key, None)

        config = EnsembleConfig.load_or_create(tmp_path)

        assert config.database == "sqlite"
        # Default postgres URL accessible
        assert config.postgres.host == "localhost"
        assert config.postgres.port == 5432
        # ensemble.json should have been written
        config_file = tmp_path / "ensemble.json"
        assert config_file.exists(), "ensemble.json should be created on first load"

    def test_load_existing_config_reads_values(self, tmp_path: Path):
        """Pre-written ensemble.json is read verbatim."""
        config_data = {
            "database": "postgres",
            "postgres": {
                "host": "db.internal",
                "port": 5433,
                "db": "prod_db",
                "user": "prod_user",
                "password": "secret",
            },
            "sqlite": {
                "instances_db": "/tmp/i.db",
                "checkpoints_db": "/tmp/c.db",
            },
        }
        (tmp_path / "ensemble.json").write_text(json.dumps(config_data))

        config = EnsembleConfig.load_or_create(tmp_path)

        assert config.database == "postgres"
        assert config.postgres.host == "db.internal"
        assert config.postgres.port == 5433
        assert config.postgres.db == "prod_db"
        assert config.postgres.user == "prod_user"
        assert config.postgres.password == "secret"
        assert config.sqlite.instances_db == "/tmp/i.db"
        assert config.sqlite.checkpoints_db == "/tmp/c.db"

    def test_postgres_env_auto_detected_when_no_config(self, tmp_path: Path, monkeypatch):
        """POSTGRES_HOST + POSTGRES_DB set, no config file → defaults to 'postgres'."""
        # Drop all POSTGRES_* env vars to start clean
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            monkeypatch.delenv(key, raising=False)

        monkeypatch.setenv("POSTGRES_HOST", "env-host")
        monkeypatch.setenv("POSTGRES_DB", "env_db")

        config = EnsembleConfig.load_or_create(tmp_path)

        assert config.database == "postgres"
        # The decision should be persisted to disk
        saved = json.loads((tmp_path / "ensemble.json").read_text())
        assert saved["database"] == "postgres"

    def test_no_postgres_env_defaults_to_sqlite(self, tmp_path: Path, monkeypatch):
        """No env vars, no config file → defaults to 'sqlite'."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            monkeypatch.delenv(key, raising=False)

        config = EnsembleConfig.load_or_create(tmp_path)

        assert config.database == "sqlite"

    def test_partial_postgres_env_does_not_trigger_postgres_default(self, tmp_path: Path, monkeypatch):
        """Only POSTGRES_HOST set (no POSTGRES_DB) → still defaults to sqlite."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            monkeypatch.delenv(key, raising=False)

        monkeypatch.setenv("POSTGRES_HOST", "only-host")
        # No POSTGRES_DB

        config = EnsembleConfig.load_or_create(tmp_path)

        assert config.database == "sqlite"

    def test_invalid_json_falls_back_to_defaults(self, tmp_path: Path, monkeypatch):
        """Malformed JSON in ensemble.json → returns default config (graceful)."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            monkeypatch.delenv(key, raising=False)

        (tmp_path / "ensemble.json").write_text("{this is: not valid json,,")

        config = EnsembleConfig.load_or_create(tmp_path)

        # Graceful fallback to default
        assert config.database == "sqlite"


class TestSave:
    """Tests for EnsembleConfig.save()."""

    def test_save_persists_values(self, tmp_path: Path):
        """Create config, save, reload → values match."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            os.environ.pop(key, None)

        config = EnsembleConfig(
            database="postgres",
            postgres=PostgresConfig(
                host="save-host", port=5434, db="save_db",
                user="save_user", password="save_pass",
            ),
            sqlite=SqliteConfig(
                instances_db="/save/i.db",
                checkpoints_db="/save/c.db",
            ),
        )
        config.save(tmp_path)

        # Reload from disk
        data = json.loads((tmp_path / "ensemble.json").read_text())
        assert data["database"] == "postgres"
        assert data["postgres"]["host"] == "save-host"
        assert data["postgres"]["port"] == 5434
        assert data["sqlite"]["instances_db"] == "/save/i.db"

    def test_save_creates_parent_directory(self, tmp_path: Path):
        """save() must create data_dir if it doesn't exist."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            os.environ.pop(key, None)

        nested_dir = tmp_path / "nested" / "deeper" / "data"
        config = EnsembleConfig(database="sqlite")
        config.save(nested_dir)

        assert (nested_dir / "ensemble.json").exists()

    def test_save_uses_atomic_write_pattern(self, tmp_path: Path):
        """Verify save() writes to a .tmp file then os.replace's it."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            os.environ.pop(key, None)

        config = EnsembleConfig(database="sqlite")
        config.save(tmp_path)

        config_path = tmp_path / "ensemble.json"
        # The .tmp sibling should NOT exist after a successful save
        assert config_path.exists()
        assert not config_path.with_suffix(".json.tmp").exists()

    def test_save_includes_trailing_newline(self, tmp_path: Path):
        """Saved file has trailing newline for POSIX friendliness."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            os.environ.pop(key, None)

        config = EnsembleConfig(database="sqlite")
        config.save(tmp_path)

        content = (tmp_path / "ensemble.json").read_text()
        assert content.endswith("\n")


class TestGetPostgresUrl:
    """Tests for EnsembleConfig.get_postgres_url()."""

    def test_default_postgres_url(self):
        """Default EnsembleConfig produces a postgresql+asyncpg URL."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            os.environ.pop(key, None)

        config = EnsembleConfig()
        url = config.get_postgres_url()

        assert url.startswith("postgresql+asyncpg://")
        assert "localhost:5432" in url
        assert "/ensemble" in url

    def test_env_overrides_take_precedence(self, monkeypatch):
        """POSTGRES_* env vars override file values for credential rotation."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            monkeypatch.delenv(key, raising=False)

        monkeypatch.setenv("POSTGRES_HOST", "env-host")
        monkeypatch.setenv("POSTGRES_DB", "env_db")
        monkeypatch.setenv("POSTGRES_USER", "env_user")
        monkeypatch.setenv("POSTGRES_PASSWORD", "env_pw")
        monkeypatch.setenv("POSTGRES_PORT", "5555")

        config = EnsembleConfig()
        url = config.get_postgres_url()

        assert "env-host:5555" in url
        assert "/env_db" in url
        assert "env_user:env_pw@" in url


class TestPropertyAccessors:
    """Tests for is_postgres / is_sqlite / postgres_env_available."""

    def test_is_postgres_true_when_database_postgres(self):
        config = EnsembleConfig(database="postgres")
        assert config.is_postgres is True
        assert config.is_sqlite is False

    def test_is_sqlite_true_when_database_sqlite(self):
        config = EnsembleConfig(database="sqlite")
        assert config.is_sqlite is True
        assert config.is_postgres is False

    def test_postgres_env_available_both_set(self, monkeypatch):
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "h")
        monkeypatch.setenv("POSTGRES_DB", "d")

        config = EnsembleConfig()
        assert config.postgres_env_available is True

    def test_postgres_env_available_partial(self, monkeypatch):
        """Only one of HOST/DB set → False."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "h")
        # POSTGRES_DB intentionally missing

        config = EnsembleConfig()
        assert config.postgres_env_available is False
