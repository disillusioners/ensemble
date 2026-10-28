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

import os

import pytest
from pydantic import ValidationError

import daemon.repositories.factory as factory_mod
from daemon import constants
from daemon.config import LimitsConfig
from daemon.constants import MANAGER_POOL_SIZE_DEFAULT
from daemon.ensemble_config import EnsembleConfig, PostgresConfig
from daemon.repositories.factory import (
    PG_SESSION_CONNECT_ARGS,
    DatabaseConfig,
    create_engine_from_config,
    create_postgres_engine,
)
from daemon.services import settings_service
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

    def test_pool_size_kwarg_reaches_engine(self, monkeypatch):
        """CRITICAL knob-path pin (review fix round): the EXPLICIT
        ``manager_pool_size`` kwarg — the value the manager threads from
        the boot-applied ``config.limits`` — is what the engine is built
        with. NOT the fallback constant: this pin fails if the factory
        stops honoring the kwarg (the severed-knob defect this round
        fixes)."""
        captured: dict = {}
        real_create_engine = factory_mod.create_engine

        def spy(*args, **kwargs):
            captured["kwargs"] = kwargs
            return real_create_engine(*args, **kwargs)

        monkeypatch.setattr(factory_mod, "create_engine", spy)
        for var in _PG_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        engine = create_postgres_engine(
            _test_pg_config(), manager_pool_size=37
        )
        try:
            assert captured["kwargs"]["pool_size"] == 37
        finally:
            engine.dispose()

    def test_default_pool_size_overflow_and_pre_ping(self, monkeypatch):
        """With the kwarg OMITTED (the 3 call sites with no boot-applied
        Config: auth CLI, settings bootstrap throwaway, migration
        worker), the factory falls back to the default tier constant;
        overflow + pre-ping are unchanged. (Renamed from
        ``test_pool_and_pre_ping_settings_unchanged`` — the old name
        predates the knob and no longer said what is pinned.)"""
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
            assert captured["kwargs"]["pool_size"] == (
                MANAGER_POOL_SIZE_DEFAULT
            )
            assert captured["kwargs"]["max_overflow"] == 10
            assert captured["kwargs"]["pool_pre_ping"] is True
        finally:
            engine.dispose()

    def test_manager_threads_the_ladder_value_into_the_factory(self):
        """Structural pin (checkpoint-pin layout): the PRODUCTION call
        site (``daemon/manager.py``) passes
        ``manager_pool_size=self.config.limits.manager_pool_size`` — the
        boot-applied YAML Config read (``self.config``, set from the
        ``config`` ctor arg), NOT the ensemble.json ``EnsembleConfig``
        (which has no ``.limits``). This is the hop that was severed in
        the first cut (engine always built with the default constant)."""
        import inspect

        from daemon import manager as manager_mod

        source = inspect.getsource(manager_mod)
        assert (
            "manager_pool_size=self.config.limits.manager_pool_size" in source
        )
        # And the factory call it feeds.
        assert "create_postgres_engine(" in source


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


class TestManagerPoolKnobLadder:
    """The LIMITS_MANAGER_POOL_SIZE knob's 4-tier ladder pins — layout
    mirrors ``tests/unit/tools/test_checkpoint_pool_config.py``
    (default tier / field mirror / registry mirror / env tier / yaml
    tier / both bounds at the registry validator and the pydantic
    Field). Review fix round: the first cut's engine pin was
    tautological (its fixture carried no ``.limits``, so it asserted
    only the fallback constant); the knob-path pins now live in
    ``TestPgEngineUtcSessionConnectArgs`` above."""

    _KEY = "limits.manager_pool_size"

    def test_the_default_tier_is_the_constant(self):
        assert constants.MANAGER_POOL_SIZE_DEFAULT == 15

    def test_the_field_defaults_mirror_the_constant(self):
        limits = LimitsConfig()
        assert limits.manager_pool_size == (
            constants.MANAGER_POOL_SIZE_DEFAULT
        )

    def test_the_settings_registry_default_mirrors_the_field(self):
        spec = settings_service.MANAGED_KEYS[self._KEY]
        assert spec.default == constants.MANAGER_POOL_SIZE_DEFAULT
        assert spec.env_var == "LIMITS_MANAGER_POOL_SIZE"
        assert spec.config_path == "limits.manager_pool_size"
        assert spec.value_type == "int"
        # Fail-LOUD bounds, mirrored Field-side AND registry-side
        # (Ruling 1 — one validation truth): floor 15 == the default,
        # ceiling 30 = the connection budget.
        assert spec.ge == 15
        assert spec.le == 30

    def test_the_env_tier_is_effective(self, monkeypatch):
        monkeypatch.setenv("LIMITS_MANAGER_POOL_SIZE", "22")
        limits = LimitsConfig()
        assert limits.manager_pool_size == 22

    def test_the_yaml_tier_is_the_shipped_literal(self):
        import yaml

        with open("config.yaml", "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        assert raw["limits"]["manager_pool_size"] == (
            constants.MANAGER_POOL_SIZE_DEFAULT
        )

    def test_the_bounds_are_enforced_by_the_registry_validator(self):
        """The registry validator must reject the values the pydantic
        Field would reject — BOTH bounds (one validation truth)."""
        spec = settings_service.MANAGED_KEYS[self._KEY]
        ok, value, failure, _detail = settings_service.parse_and_validate(
            spec, 15
        )
        assert ok is True and value == 15
        ok, value, failure, _detail = settings_service.parse_and_validate(
            spec, 30
        )
        assert ok is True and value == 30
        ok, _value, failure, _detail = settings_service.parse_and_validate(
            spec, 14
        )
        assert ok is False and failure == "validation"
        ok, _value, failure, _detail = settings_service.parse_and_validate(
            spec, 31
        )
        assert ok is False and failure == "validation"

    def test_the_field_rejects_both_bounds_too(self):
        # The Field bounds raise pydantic's ValidationError — the pin
        # names the type so an unrelated exception cannot satisfy it.
        with pytest.raises(ValidationError):
            LimitsConfig(manager_pool_size=14)
        with pytest.raises(ValidationError):
            LimitsConfig(manager_pool_size=31)
        # In-bounds values construct cleanly.
        assert LimitsConfig(manager_pool_size=15).manager_pool_size == 15
        assert LimitsConfig(manager_pool_size=30).manager_pool_size == 30

    def test_below_floor_env_tier_is_skipped_at_boot_not_clamped(self):
        """Ground truth for the fail-loud contract at the BOOT surface:
        with the shipped yaml literal shadowing the pydantic env tier,
        the resolver rejects the below-floor env value via the SAME
        registry validator — one WARNING, the env tier skipped, the
        yaml tier active. The value is NEVER clamped to 15 (or to
        anything else). (An unshadowed env tier — no yaml literal —
        CRASHES load via the Field's ValidationError instead; that
        surface is pinned by test_the_field_rejects_both_bounds_too.)"""
        spec = settings_service.MANAGED_KEYS[self._KEY]
        ok, _value, failure, _detail = settings_service.parse_and_validate(
            spec, "10"
        )
        assert ok is False and failure == "validation"
        snapshot = {self._KEY: {"default": 15, "yaml_value": 15}}
        resolution = settings_service.resolve(
            self._KEY, snapshot, db_overrides={}
        )
        assert resolution["source"] == "yaml"
        assert resolution["value"] == 15
        assert resolution["env_value"] is None  # the rejected tier

    def test_the_ceiling_budget_is_the_documented_one(self):
        """Ceiling rationale (constants.py): 30+10 manager +
        2×20 checkpoint = 80 ≤ max_connections=100."""
        manager_max = 30 + 10
        checkpoint_max = 2 * 20
        assert manager_max + checkpoint_max == 80
        assert 80 <= 100
