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

AMENDED (db-pool-idle-txn, F2.8 / D-37 / C-9, 2026-10-28): the exact
equality on the manager engine's ``connect_args`` at the former
``:86-88`` now asserts the **D-47 TEMPLATE form** (structure, never a
literal), and the pack additionally carries the F2 knob-matrix pins
(F2-a…F2-e): the explicit pool-hygiene knobs, the template's
disabled/enabled structure, and the conninfo-channel attribution
(``application_name`` in the URL query dict — D-49 — NEVER in the
``-c options`` string). ``:65`` (the D-17 guard on the SHARED
constant) is untouched by the amendment.
"""

from __future__ import annotations

import daemon.repositories.factory as factory_mod
from daemon.constants import (
    MANAGER_ENGINE_APPLICATION_NAME,
    MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS,
    MANAGER_ENGINE_POOL_RECYCLE_S,
    MANAGER_ENGINE_POOL_TIMEOUT_S,
)
from daemon.ensemble_config import EnsembleConfig, PostgresConfig
from daemon.repositories.factory import (
    PG_SESSION_CONNECT_ARGS,
    DatabaseConfig,
    create_engine_from_config,
    create_postgres_engine,
)
from sqlalchemy import text
from sqlalchemy.engine import make_url

#: The pack-local declared collected count (NEW packs only — the
#: pre-existing corpus declares none; this constant was ADDED by the
#: db-pool-idle-txn F2 amendment, measured at pack-writing time).
EXPECTED_COLLECTED = 10

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


def _capture_engine_kwargs(monkeypatch) -> tuple[dict, object]:
    """Spy-capture ``create_engine``'s args/kwargs (manager factory)."""
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
    return captured, engine


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
        captured, engine = _capture_engine_kwargs(monkeypatch)
        try:
            # Amended per D-37/C-9 (db-pool-idle-txn F2.8): the exact
            # equality now asserts the manager TEMPLATE's current
            # rendering, value-by-value — while the D-47 backstop is
            # DISABLED that is exactly the UTC clause (the
            # idle-in-txn clause is OMITTED until enablement, and NO
            # statement_timeout clause exists — D-31).
            assert captured["kwargs"]["connect_args"] == {
                "options": factory_mod._manager_pg_session_options()
            }
            assert (
                captured["kwargs"]["connect_args"]["options"]
                == "-c timezone=UTC"
            )
            # ...on the psycopg3 PG driver for THIS stack.
            url = captured["args"][0]
            assert url.startswith("postgresql+psycopg://")
            assert engine.dialect.name == "postgresql"
        finally:
            engine.dispose()

    def test_pool_and_pre_ping_settings_unchanged(self, monkeypatch):
        """The fix must not disturb the pool sizing / pre-ping that the
        shared engine pool sizing depends on (WORKER_POOL_SIZE note)."""
        captured, engine = _capture_engine_kwargs(monkeypatch)
        try:
            assert captured["kwargs"]["pool_size"] == 5
            assert captured["kwargs"]["max_overflow"] == 10
            assert captured["kwargs"]["pool_pre_ping"] is True
        finally:
            engine.dispose()


class TestF2ManagerEngineKnobMatrix:
    """F2-a / F2-b / F2-d — the explicit pool-hygiene knob set.

    db-pool-idle-txn (job 634c38f2), contract
    ``final-implementation-spec.md`` §1 F2.4 + phase2-plan §3.1/AC14.
    The knobs assert against the IMPORTED ``daemon/constants.py``
    constants (DR-20: structure — a constant reference — never a bare
    literal).
    """

    def test_f2a_reset_on_return_is_explicit_rollback(self, monkeypatch):
        """F2-a — pool_reset_on_return='rollback' is explicit (the
        'an aborted connection is never handed out' invariant is
        pinned, not a library default)."""
        captured, engine = _capture_engine_kwargs(monkeypatch)
        try:
            assert (
                captured["kwargs"]["pool_reset_on_return"] == "rollback"
            )
        finally:
            engine.dispose()

    def test_f2b_pool_recycle_is_set_from_the_constant(self, monkeypatch):
        """F2-b — pool_recycle is set (precedent REPAIR_POOL_RECYCLE_S)."""
        captured, engine = _capture_engine_kwargs(monkeypatch)
        try:
            assert (
                captured["kwargs"]["pool_recycle"]
                == MANAGER_ENGINE_POOL_RECYCLE_S
            )
            assert MANAGER_ENGINE_POOL_RECYCLE_S == 3600
        finally:
            engine.dispose()

    def test_f2c_idle_in_txn_clause_absent_while_disabled(self):
        """F2-c — D-47: while the backstop is DISABLED the rendered
        manager options string OMITS the idle-in-txn clause entirely
        (and carries NO statement_timeout — D-31). The assertion is
        STRUCTURAL (clause presence/absence), never the literal
        ``60000``."""
        assert factory_mod.MANAGER_ENGINE_IDLE_IN_TXN_BACKSTOP_ENABLED is False
        rendered = factory_mod._manager_pg_session_options()
        assert "idle_in_transaction_session_timeout" not in rendered
        assert "statement_timeout" not in rendered
        assert rendered == "-c timezone=UTC"

    def test_f2d_pre_ping_stays(self, monkeypatch):
        """F2-d — pool_pre_ping=True stays (the existing pin, kept
        green by the F2 change)."""
        captured, engine = _capture_engine_kwargs(monkeypatch)
        try:
            assert captured["kwargs"]["pool_pre_ping"] is True
        finally:
            engine.dispose()

    def test_f2e_template_structure_order_pinned_value_from_constant(
        self, monkeypatch
    ):
        """F2-e — the D-47 TEMPLATE's structure: IF the clause is
        present its value EQUALS the imported constant (never a
        literal), the option ORDER is pinned (UTC FIRST, idle-in-txn
        SECOND), and pool_timeout is pinned at the D-15 constant."""
        captured, engine = _capture_engine_kwargs(monkeypatch)
        try:
            assert (
                captured["kwargs"]["pool_timeout"]
                == MANAGER_ENGINE_POOL_TIMEOUT_S
            )
            assert MANAGER_ENGINE_POOL_TIMEOUT_S == 30
        finally:
            engine.dispose()
        # The ENABLED form is exercised on the template function with
        # the module flag flipped IN-TEST (enablement itself is a
        # one-edit change — D-47; the pin asserts the structure so
        # enablement does not rewrite it).
        monkeypatch.setattr(
            factory_mod, "MANAGER_ENGINE_IDLE_IN_TXN_BACKSTOP_ENABLED", True
        )
        enabled = factory_mod._manager_pg_session_options()
        expected = (
            "-c timezone=UTC"
            f" -c idle_in_transaction_session_timeout="
            f"{MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS}"
        )
        assert enabled == expected
        # Order pinned: UTC first, idle-in-txn second.
        assert enabled.startswith("-c timezone=UTC -c ")
        assert enabled.index("timezone=UTC") < enabled.index(
            "idle_in_transaction_session_timeout"
        )
        assert "statement_timeout" not in enabled

    def test_f2_g_conninfo_channel_application_name(self, monkeypatch):
        """F2-g half (this pack's leg) — D-49: the manager engine's
        ``application_name`` rides the URL QUERY DICT (the conninfo
        channel), NEVER the ``-c options`` string, and equals the
        imported constant."""
        captured, engine = _capture_engine_kwargs(monkeypatch)
        try:
            query = make_url(captured["args"][0]).query
            assert (
                query.get("application_name")
                == MANAGER_ENGINE_APPLICATION_NAME
            )
            assert (
                MANAGER_ENGINE_APPLICATION_NAME == "ensemble-manager"
            )
            options = captured["kwargs"]["connect_args"].get("options", "")
            assert "application_name" not in options
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
