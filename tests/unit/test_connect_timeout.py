"""Unit pins for the hard-reboot PG-connect budget (G6).

Feature: hard-reboot recovery. Phase 1 / AC1-AC4.

The daemon's shared PostgreSQL engine (``daemon/repositories/factory.py``)
used to carry no libpq ``connect_timeout``: on a firewall-DROP host the
connect phase hung on the OS TCP timeout (~75-130s) instead of failing
inside ``BOOT_DB_TIMEOUT_S`` (10s). This file pins:

  (a) ``create_postgres_engine`` binds ``connect_timeout=BOOT_DB_TIMEOUT_S``
      in the connect args it hands to the driver.
  (b) the firewall-DROP semantics: the bound reaches libpq (so a DROP
      aborts at <=10s, not 75-130s) — mocked, no real hang, no real
      network.
  (c) the preflight lane is REUSED, not duplicated: the boot preflight
      issues exactly ONE ``SELECT 1`` probe per attempt.
  (d)+(e) the systemd unit template orders AFTER postgresql.service and
      declares NO ``Wants=`` / ``Requires=`` on it (a remote-PG host must
      not be forced to start a local PG; a local-PG host must not have
      one force-started behind the daemon's back).

No real PostgreSQL is contacted: every DB touch is mocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

import daemon.__main__ as daemon_entry
import daemon.repositories.factory as factory_mod
from daemon.constants import BOOT_DB_TIMEOUT_S
from daemon.ensemble_config import EnsembleConfig, PostgresConfig

# The ambient shell may export the LIVE PG coordinates; the factory
# resolves POSTGRES_* with precedence over the config, so a prod-pointing
# env would silently redirect the constructed engine. Drop them (mirrors
# tests/unit/repositories/test_pg_engine_utc_session.py).
_PG_ENV_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)

_UNIT_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "scripts/systemd/ensemble-daemon.service"
)


def _pg_config() -> EnsembleConfig:
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


@pytest.fixture
def no_pg_env(monkeypatch):
    """Neutralize ambient POSTGRES_* env so the factory uses the config."""
    for var in _PG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_connect_timeout_bound_is_set_on_the_engine(no_pg_env, monkeypatch):
    """(a) The shared PG engine binds the boot connect budget."""
    captured: dict = {}
    real_create_engine = factory_mod.create_engine

    def spy(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return real_create_engine(*args, **kwargs)

    monkeypatch.setattr(factory_mod, "create_engine", spy)
    engine = factory_mod.create_postgres_engine(_pg_config())
    try:
        # libpq takes connect_timeout as a conninfo parameter, so it rides
        # the URL query — a top-level create_engine(connect_timeout=...)
        # kwarg is rejected by the psycopg dialect.
        url = str(captured["args"][0])
        assert url.endswith(f"?connect_timeout={BOOT_DB_TIMEOUT_S}")
        assert engine.url.query["connect_timeout"] == str(BOOT_DB_TIMEOUT_S)
        # The UTC session option rides alongside it, untouched.
        assert captured["kwargs"]["connect_args"] == {"options": "-c timezone=UTC"}
    finally:
        engine.dispose()


def test_firewall_drop_is_bounded_at_the_boot_budget(no_pg_env, monkeypatch):
    """(b) A DROP aborts inside the budget — the bound reaches libpq.

    The libpq connect timeout is the real enforcement (an
    ``asyncio.wait_for`` cannot bound a sync connect). We mock the
    driver's connect so no real DROP (and no 75-130s wait) happens, and
    assert the bound libpq would have used is <= BOOT_DB_TIMEOUT_S.
    """
    captured: dict = {}

    def fake_connect(self, *cargs, **cparams):
        captured.update(cparams)
        raise RuntimeError("simulated firewall DROP")

    monkeypatch.setattr(PGDialect_psycopg, "connect", fake_connect)
    engine = factory_mod.create_postgres_engine(_pg_config())
    try:
        with pytest.raises(RuntimeError):
            engine.connect()
    finally:
        engine.dispose()

    bound = int(captured["connect_timeout"])
    assert bound == BOOT_DB_TIMEOUT_S
    assert bound <= 10  # the pre-flight budget, NOT the ~75-130s TCP timeout


def test_boot_preflight_probes_exactly_once(monkeypatch):
    """(c) The preflight lane is reused: one SELECT 1 per attempt."""
    probes: list[str] = []

    class _Conn:
        def execute(self, stmt):
            probes.append(str(stmt))
            return None

    class _Engine:
        def connect(self):
            class _CM:
                def __enter__(self):
                    return _Conn()

                def __exit__(self, *exc):
                    return False

            return _CM()

        def dispose(self):
            return None

    engines: list[dict] = []

    def fake_create_engine(url, **kwargs):
        engines.append(kwargs)
        return _Engine()

    cfg = _pg_config()
    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)
    monkeypatch.setattr(
        EnsembleConfig, "load_or_create", staticmethod(lambda data_dir: cfg)
    )

    daemon_entry._boot_db_preflight()

    assert probes == ["SELECT 1"]
    # The probe engine carries the same budget as the shared engine.
    assert engines[0]["connect_args"]["connect_timeout"] == BOOT_DB_TIMEOUT_S


def test_unit_template_orders_after_postgresql_only():
    """(d)+(e) After= only — no Wants= / Requires= on postgresql.service."""
    text = _UNIT_TEMPLATE.read_text()

    assert "After=postgresql.service" in text
    assert "Wants=postgresql.service" not in text
    assert "Requires=postgresql.service" not in text
