"""Factory-level unit pins for the B2/B3 PG engine UTC-session fix.

Feature ``feature/fix-job-queue-timestamps-tz`` (review follow-up).
``daemon/repositories/factory.py::create_postgres_engine`` now passes
``connect_args={"options": "-c timezone=UTC"}`` so every pooled
PostgreSQL session renders ``now()`` in UTC. The SQL-side age readers
(readiness heartbeat max-age, hung-children watchdog) then share the
frame with the naive-UTC digit writers — see
``PG_SESSION_CONNECT_ARGS`` in the factory module for the full
rationale.

These are FACTORY pins: they capture the ``create_engine`` kwargs
without opening a connection, so they hold in SQLite-only
environments. The end-to-end session behavior (``SHOW timezone`` /
age math on a non-UTC-default PG server) is pinned separately in
``tests/postgres/test_pg_session_utc_frame_pg.py``.
"""

from __future__ import annotations

import daemon.repositories.factory as factory_mod
from daemon.ensemble_config import EnsembleConfig, PostgresConfig
from daemon.repositories.factory import (
    PG_SESSION_CONNECT_ARGS,
    DatabaseConfig,
    create_engine_from_config,
    create_postgres_engine,
)
from sqlalchemy import text

# POSTGRES_* environment must NEVER leak into these pins: the factory
# resolves engine coordinates with ``os.environ.get("POSTGRES_*", cfg)``
# precedence, and a prod-pointing shell (POSTGRES_DB=ensemble_prod)
# would silently redirect the constructed engine URL.
_PG_ENV_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)


def _test_pg_config() -> EnsembleConfig:
    """Config pointing at a clearly-fake test coordinate (no connect)."""
    return EnsembleConfig(
        database="postgres",
        postgres=PostgresConfig(
            host="127.0.0.1",
            port=15432,
            db="ensemble_test",
            user="ensemble",
            password="ensemble_dev",
        ),
    )


class TestPgEngineUtcSessionConnectArgs:
    """The PG engine factory pins the libpq session clock to UTC."""

    def test_constant_shape(self):
        # The option form is the libpq connection parameter carrying
        # server command-line options; psycopg3 merges connect_args
        # kwargs into the conninfo verbatim.
        assert PG_SESSION_CONNECT_ARGS == {"options": "-c timezone=UTC"}

    def test_create_postgres_engine_binds_utc_session_options(
        self, monkeypatch
    ):
        captured: dict = {}

        real_create_engine = factory_mod.create_engine

        def spy(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return real_create_engine(*args, **kwargs)

        monkeypatch.setattr(factory_mod, "create_engine", spy)
        for var in _PG_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        engine = create_postgres_engine(_test_pg_config())
        try:
            # The UTC session option reaches create_engine verbatim...
            assert captured["kwargs"]["connect_args"] == {
                "options": "-c timezone=UTC"
            }
            # ...on the psycopg3 PG driver for THIS stack.
            url = captured["args"][0]
            assert url.startswith("postgresql+psycopg://")
            assert engine.dialect.name == "postgresql"
        finally:
            engine.dispose()

    def test_pool_and_pre_ping_settings_unchanged(self, monkeypatch):
        """The fix must not disturb the pool sizing / pre-ping that the
        shared engine pool sizing depends on (WORKER_POOL_SIZE note)."""
        captured: dict = {}
        real_create_engine = factory_mod.create_engine

        def spy(*args, **kwargs):
            captured["kwargs"] = kwargs
            return real_create_engine(*args, **kwargs)

        monkeypatch.setattr(factory_mod, "create_engine", spy)
        for var in _PG_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        engine = create_postgres_engine(_test_pg_config())
        try:
            assert captured["kwargs"]["pool_size"] == 5
            assert captured["kwargs"]["max_overflow"] == 10
            assert captured["kwargs"]["pool_pre_ping"] is True
        finally:
            engine.dispose()


class TestSqliteEngineUntouched:
    """The SQLite engine path keeps its own connect_args — the UTC
    session option is PG-only (SQLite has no session TimeZone and its
    ``julianday('now')`` clock is UTC by definition)."""

    def test_sqlite_path_keeps_check_same_thread_only(
        self, monkeypatch, tmp_path
    ):
        captured: list[dict] = []
        real_create_engine = factory_mod.create_engine

        def spy(*args, **kwargs):
            captured.append(kwargs)
            return real_create_engine(*args, **kwargs)

        monkeypatch.setattr(factory_mod, "create_engine", spy)

        engine = create_engine_from_config(
            DatabaseConfig.sqlite(db_path=str(tmp_path / "utc_pin.db"))
        )
        try:
            assert captured[0]["connect_args"] == {"check_same_thread": False}
            assert "options" not in captured[0]["connect_args"]
            assert engine.dialect.name == "sqlite"
            # The pragma-listening SQLite engine still actually works.
            with engine.connect() as conn:
                assert conn.execute(text("SELECT 1")).scalar() == 1
        finally:
            engine.dispose()
