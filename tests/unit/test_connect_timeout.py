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
  (h) the BOOT-PREFLIGHT probe URL is built with ``URL.create`` too:
      byte-identical to the pre-change string form in the normal case,
      and the intended host / port / database survive a
      URL-special-character password (the f-string form mangled the
      HOST into an unresolvable one -> exit 75 boot-block).

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
from daemon.ensemble_config import EnsembleConfig
from tests.helpers.hard_reboot_fixtures import (  # noqa: F401 — fixture
    no_pg_env,
    pg_config,
)

_UNIT_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "scripts/systemd/ensemble-daemon.service"
)


def test_connect_timeout_bound_is_set_on_the_engine(no_pg_env, monkeypatch):
    """(a) The shared PG engine binds the boot connect budget."""
    captured: dict = {}
    real_create_engine = factory_mod.create_engine

    def spy(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return real_create_engine(*args, **kwargs)

    monkeypatch.setattr(factory_mod, "create_engine", spy)
    engine = factory_mod.create_postgres_engine(pg_config())
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
    engine = factory_mod.create_postgres_engine(pg_config())
    try:
        with pytest.raises(RuntimeError):
            engine.connect()
    finally:
        engine.dispose()

    bound = int(captured["connect_timeout"])
    assert bound == BOOT_DB_TIMEOUT_S
    assert bound <= 10  # the pre-flight budget, NOT the ~75-130s TCP timeout


def test_other_engine_sites_carry_no_connect_timeout(no_pg_env, monkeypatch):
    """(f) ONLY the canonical PG engine carries the libpq bound.

    The other two ``create_engine`` call sites in the factory —
    ``create_engine_from_config``'s SQLite branch and its
    dialect-agnostic branch — must stay free of ``connect_timeout``:
    the bound is a property of the canonical PostgreSQL engine only.
    """
    captured: list[dict] = []
    real_create_engine = factory_mod.create_engine

    def spy(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})
        return real_create_engine(*args, **kwargs)

    monkeypatch.setattr(factory_mod, "create_engine", spy)

    # The SQLite branch.
    sqlite_engine = factory_mod.create_engine_from_config(
        factory_mod.DatabaseConfig.sqlite(":memory:")
    )
    try:
        assert captured[-1]["kwargs"]["connect_args"] == {"check_same_thread": False}
        assert "connect_timeout" not in str(captured[-1]["args"][0])
    finally:
        sqlite_engine.dispose()

    # The dialect-agnostic branch (a PostgreSQL URL, but not the
    # canonical engine — it must NOT inherit the bound).
    pg_engine = factory_mod.create_engine_from_config(
        factory_mod.DatabaseConfig.postgresql(
            "postgresql+psycopg://ensemble:ensemble@127.0.0.1:15432/ensemble_test"
        )
    )
    try:
        assert captured[-1]["kwargs"]["connect_args"] == {"options": "-c timezone=UTC"}
        assert "connect_timeout" not in str(captured[-1]["args"][0])
    finally:
        pg_engine.dispose()


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

    cfg = pg_config()
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


def test_special_char_password_keeps_the_bound_and_the_target(
    no_pg_env, monkeypatch
):
    """(g) A password with URL-special characters keeps the bound + target.

    The URL used to be built by f-string interpolation, so the RAW
    password landed in the URL unescaped. Measured against the old
    construction: a password containing ``?`` re-parsed into a
    corrupted DATABASE name (``ensemble_prod&connect_timeout=10``) and
    an EMPTY query — the ``connect_timeout`` bound SILENTLY vanished,
    which is exactly the failure the bound exists to prevent. A
    password containing ``@`` re-parsed into a corrupted HOST.

    The URL is now built with ``URL.create(...)``: the password is
    escaped, the host / database / port are the intended ones, and the
    bound still rides the query.
    """
    captured: dict = {}
    real_create_engine = factory_mod.create_engine

    def spy(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return real_create_engine(*args, **kwargs)

    monkeypatch.setattr(factory_mod, "create_engine", spy)
    monkeypatch.setenv("POSTGRES_PASSWORD", "pa?ss#word@host")

    engine = factory_mod.create_postgres_engine(pg_config())
    try:
        url = engine.url
        # The bound survived the special characters.
        assert url.query["connect_timeout"] == str(BOOT_DB_TIMEOUT_S)
        assert str(captured["args"][0]).endswith(
            f"?connect_timeout={BOOT_DB_TIMEOUT_S}"
        )
        # The target is the INTENDED one — host, port, database.
        assert url.host == "127.0.0.1"
        assert url.port == 15432
        assert url.database == "ensemble_test"
        # The password round-trips exactly (escaped in the URL, decoded
        # back to the original by the parser).
        assert url.password == "pa?ss#word@host"
        # ... and what the driver receives re-parses to the same values.
        reparsed = sqlalchemy.engine.make_url(
            url.render_as_string(hide_password=False)
        )
        assert reparsed.password == "pa?ss#word@host"
        assert reparsed.host == "127.0.0.1"
        assert reparsed.database == "ensemble_test"
        assert reparsed.query["connect_timeout"] == str(BOOT_DB_TIMEOUT_S)
    finally:
        engine.dispose()


def test_preflight_url_is_byte_identical_to_the_string_form(no_pg_env):
    """(h1) The preflight URL is unchanged in the normal case.

    The preflight probe URL is now built with ``URL.create`` (the
    same construction as the shared factory), but in the normal case
    — no URL-special characters in the password — it renders
    byte-for-byte identical to the f-string form it replaced. Nothing
    about the probe's target changes with the fix; only the escaping
    of a pathological password does.
    """
    cfg = pg_config()
    pg = cfg.postgres
    string_form = (
        f"postgresql+psycopg://{pg.user}:{pg.password}@{pg.host}:{pg.port}/{pg.db}"
    )

    assert daemon_entry._preflight_pg_url(cfg) == string_form


def test_preflight_url_keeps_the_intended_target_with_a_special_char_password(
    no_pg_env, monkeypatch
):
    """(h2) A URL-special-character password no longer corrupts the HOST.

    Measured against the f-string form: a password containing ``@``
    re-parsed into a DIFFERENT (unresolvable) host — the probe then
    failed to connect and the daemon exited 75 (boot-blocked, though
    fail-closed: it never probed the WRONG database). ``URL.create``
    escapes the password, so the host, port, database and password
    are the intended ones.
    """
    monkeypatch.setenv("POSTGRES_PASSWORD", "pa?ss#word@host")
    cfg = pg_config()

    url = daemon_entry._preflight_pg_url(cfg)
    parsed = sqlalchemy.engine.make_url(url)

    assert parsed.host == "127.0.0.1"
    assert parsed.port == 15432
    assert parsed.database == "ensemble_test"
    assert parsed.password == "pa?ss#word@host"


def test_preflight_url_carries_no_connect_timeout_query(no_pg_env):
    """(h3) The preflight's bound rides ``connect_args``, not the URL query.

    The shared factory rides ``connect_timeout`` on the URL query; the
    preflight rides it on the probe engine's ``connect_args`` (the
    factory's ``connect_args`` slot is reserved for the B2/B3
    UTC-session contract). That mechanism difference is deliberate —
    pinned here so the fix does not silently move the bound.
    """
    cfg = pg_config()

    parsed = sqlalchemy.engine.make_url(daemon_entry._preflight_pg_url(cfg))

    assert "connect_timeout" not in parsed.query
