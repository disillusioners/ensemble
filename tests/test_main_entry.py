"""Tests for daemon/__main__.py — boot preflight + bounded graceful shutdown.

Auto-Restart Phase 1:

* Exit-75 boot preflight (ADR-011): PostgreSQL selected + unreachable →
  ``SystemExit(75)``; reachable → proceeds; SQLite → check skipped.
* Exit-78 auth-refusal half (review m2): SQLSTATE 28P01/28000/28P02
  raised by the probe → ``SystemExit(78)`` — retrying cannot fix
  credentials. Unknown/absent SQLSTATE on a connectivity-shaped error
  stays on the 75 track (transient-first).
* Local preflight engine (review M1): the probe builds its own engine
  via ``sqlalchemy.create_engine`` with
  ``connect_args={"connect_timeout": BOOT_DB_TIMEOUT_S}`` — it does NOT
  use ``create_postgres_engine`` (the shared factory sets no driver
  timeout and would hang ~75-130s on a firewall-DROP host). These
  tests patch ``sqlalchemy.create_engine`` accordingly (the implementation
  imports it inside the preflight function — the mock must own the
  ``sqlalchemy`` module symbol, not ``daemon.__main__``'s local).
* ``timeout_graceful_shutdown`` is forwarded to ``uvicorn.run`` (fixes
  C1 — bounded SIGTERM shutdown).
"""

from unittest.mock import MagicMock, patch

import pytest


def _run_main() -> None:
    from daemon.__main__ import main

    main()


def _pg_config(postgres: bool):
    """Build an EnsembleConfig-like object selecting the backend."""
    cfg = MagicMock()
    cfg.is_postgres = postgres
    cfg.is_sqlite = not postgres
    # _preflight_pg_url reads these attributes (env wins when set —
    # tests keep POSTGRES_* env vars unset so the config values are used).
    cfg.postgres.host = "127.0.0.1"
    cfg.postgres.port = 5432
    cfg.postgres.db = "ensemble_test"
    cfg.postgres.user = "ens"
    cfg.postgres.password = "hunter2"
    return cfg


def _auth_refused_operational_error(sqlstate: str | None):
    """Build an OperationalError shaped like psycopg's auth failure.

    SQLAlchemy wraps the DBAPI error as ``.orig``; psycopg exposes the
    SQLSTATE as ``orig.sqlstate`` (or ``orig.diag.sqlstate``).
    """
    from sqlalchemy.exc import OperationalError

    orig = Exception("FATAL: password authentication failed for user \"ens\"")
    if sqlstate is not None:
        orig.sqlstate = sqlstate
    return OperationalError("SELECT 1", None, orig)


@pytest.fixture
def uvicorn_run_patch():
    """Patch uvicorn.run to a no-op recording kwargs; also skips real boot."""
    with patch("daemon.__main__.uvicorn.run") as run_mock:
        yield run_mock


# ── Exit-75 boot preflight ─────────────────────────────────────────────────


def test_boot_preflight_pg_unreachable_exits_75(uvicorn_run_patch, tmp_path):
    """PG selected + engine connect fails → SystemExit 75, uvicorn never runs."""
    from sqlalchemy.exc import OperationalError

    def fake_load_or_create(data_dir):
        return _pg_config(postgres=True)

    engine = MagicMock()
    engine.connect.side_effect = OperationalError(
        "SELECT 1", None, Exception("connection refused")
    )

    with (
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=fake_load_or_create,
        ) as load_mock,
        patch("sqlalchemy.create_engine", return_value=engine) as engine_mock,
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
    ):
        with pytest.raises(SystemExit) as excinfo:
            _run_main()

    assert excinfo.value.code == 75
    uvicorn_run_patch.assert_not_called()
    load_mock.assert_called_once()
    engine_mock.assert_called_once()
    # M1 pin: the probe engine is built LOCALLY (not via the shared factory)
    # and carries the driver-side connect_timeout so a firewall-DROP host
    # fails inside the BOOT_DB_TIMEOUT_S budget.
    kwargs = engine_mock.call_args.kwargs
    assert kwargs.get("connect_args", {}).get("connect_timeout") is not None
    from daemon.constants import BOOT_DB_TIMEOUT_S

    assert kwargs["connect_args"]["connect_timeout"] == BOOT_DB_TIMEOUT_S


def test_boot_preflight_uses_local_engine_not_shared_factory(
    uvicorn_run_patch, tmp_path, monkeypatch
):
    """M1: the preflight must NOT call create_postgres_engine (no timeout)."""
    import daemon.__main__ as entry

    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    with (
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=lambda data_dir: _pg_config(postgres=True),
        ),
        patch(
            "daemon.repositories.factory.create_postgres_engine"
        ) as factory_mock,
        patch("sqlalchemy.create_engine", return_value=engine),
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
    ):
        _run_main()

    factory_mock.assert_not_called()
    engine.dispose.assert_called_once()
    conn.execute.assert_called_once()


def test_boot_preflight_pg_reachable_proceeds(uvicorn_run_patch, tmp_path):
    """PG selected + SELECT 1 succeeds → no exit; uvicorn.run is reached."""
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    with (
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=lambda data_dir: _pg_config(postgres=True),
        ),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
    ):
        _run_main()  # must NOT raise SystemExit

    # The SELECT 1 was executed
    conn.execute.assert_called_once()
    # The probe engine was disposed (no leaked pool)
    engine.dispose.assert_called_once()
    uvicorn_run_patch.assert_called_once()


def test_boot_preflight_sqlite_skips_check(uvicorn_run_patch, tmp_path):
    """SQLite backend → no engine attempt at all; boot proceeds."""
    with (
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=lambda data_dir: _pg_config(postgres=False),
        ),
        patch("sqlalchemy.create_engine") as engine_mock,
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
    ):
        _run_main()

    engine_mock.assert_not_called()
    uvicorn_run_patch.assert_called_once()


def test_boot_preflight_unexpected_error_fails_open(uvicorn_run_patch, tmp_path):
    """An unexpected check error (not connectivity) must NOT block boot."""
    with (
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=ValueError("corrupted ensemble.json? no — just a bug"),
        ),
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
    ):
        _run_main()  # fail-open: proceeds to uvicorn

    uvicorn_run_patch.assert_called_once()


# ── Exit-78 auth refusal (review m2) ───────────────────────────────────────


@pytest.mark.parametrize("sqlstate", ["28P01", "28000", "28P02"])
def test_boot_preflight_pg_auth_refused_exits_78(
    uvicorn_run_patch, tmp_path, sqlstate
):
    """SQLSTATE 28P01/28000/28P02 → SystemExit 78 — credentials cannot be
    fixed by retrying, so the supervisor must stop, not loop on 75."""
    engine = MagicMock()
    engine.connect.side_effect = _auth_refused_operational_error(sqlstate)

    with (
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=lambda data_dir: _pg_config(postgres=True),
        ),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
    ):
        with pytest.raises(SystemExit) as excinfo:
            _run_main()

    assert excinfo.value.code == 78
    uvicorn_run_patch.assert_not_called()


def test_boot_preflight_auth_sqlstate_via_diag_layout(uvicorn_run_patch, tmp_path):
    """The diag.sqlstate alternate layout is also honored (defensive getattr)."""
    from sqlalchemy.exc import OperationalError

    orig = Exception("FATAL: role does not exist")
    orig.diag = type("D", (), {"sqlstate": "28000"})()
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("SELECT 1", None, orig)

    with (
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=lambda data_dir: _pg_config(postgres=True),
        ),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
    ):
        with pytest.raises(SystemExit) as excinfo:
            _run_main()

    assert excinfo.value.code == 78


def test_boot_preflight_unknown_sqlstate_stays_75(uvicorn_run_patch, tmp_path):
    """Unknown SQLSTATE (e.g. 57P03 cannot_connect_now) → transient track.

    A PG restart reports 57P03 — not an auth failure — so the daemon must
    keep the budget-exempt 75 retry behavior.
    """
    engine = MagicMock()
    engine.connect.side_effect = _auth_refused_operational_error("57P03")

    with (
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=lambda data_dir: _pg_config(postgres=True),
        ),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
    ):
        with pytest.raises(SystemExit) as excinfo:
            _run_main()

    assert excinfo.value.code == 75


def test_boot_preflight_missing_sqlstate_stays_75(uvicorn_run_patch, tmp_path):
    """No SQLSTATE attribute at all (non-psycopg wrapper) → 75, never 78.

    A driver that does not expose sqlstate must not be misread as an auth
    refusal — transient-first is the safe default.
    """
    engine = MagicMock()
    engine.connect.side_effect = _auth_refused_operational_error(None)

    with (
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=lambda data_dir: _pg_config(postgres=True),
        ),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
    ):
        with pytest.raises(SystemExit) as excinfo:
            _run_main()

    assert excinfo.value.code == 75


def test_boot_preflight_diag_sqlstate_non_string_ignored(uvicorn_run_patch, tmp_path):
    """A non-string diag.sqlstate (broken wrapper) is ignored → 75."""
    from sqlalchemy.exc import OperationalError

    orig = Exception("connection refused")
    orig.diag = type("D", (), {"sqlstate": 28345})()  # int, not str
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("SELECT 1", None, orig)

    with (
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=lambda data_dir: _pg_config(postgres=True),
        ),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
    ):
        with pytest.raises(SystemExit) as excinfo:
            _run_main()

    assert excinfo.value.code == 75


# ── Bounded graceful shutdown (fixes C1) ──────────────────────────────────


def test_uvicorn_run_receives_timeout_graceful_shutdown(uvicorn_run_patch, tmp_path):
    """uvicorn.run must be passed timeout_graceful_shutdown from DaemonConfig."""
    with patch.dict(
        "os.environ",
        {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
        clear=False,
    ):
        _run_main()

    uvicorn_run_patch.assert_called_once()
    kwargs = uvicorn_run_patch.call_args.kwargs
    assert "timeout_graceful_shutdown" in kwargs
    assert isinstance(kwargs["timeout_graceful_shutdown"], int)
    assert kwargs["timeout_graceful_shutdown"] > 0


def test_uvicorn_timeout_graceful_shutdown_env_override(uvicorn_run_patch, tmp_path):
    """DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS overrides the default."""
    with patch.dict(
        "os.environ",
        {
            "ENSEMBLE_DATA_DIR": str(tmp_path),
            "DATA_DIR": "",
            "DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS": "123",
        },
        clear=False,
    ):
        _run_main()

    kwargs = uvicorn_run_patch.call_args.kwargs
    assert kwargs["timeout_graceful_shutdown"] == 123


def test_uvicorn_wiring_unchanged(uvicorn_run_patch, tmp_path):
    """Sanity: host/port/access_log wiring follows config, unchanged by Phase 1."""
    from daemon.config import load_config

    with patch.dict(
        "os.environ",
        {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
        clear=False,
    ):
        _run_main()
        config = load_config()

    kwargs = uvicorn_run_patch.call_args.kwargs
    assert kwargs["host"] == config.daemon.host
    assert kwargs["port"] == config.daemon.port
    assert kwargs["access_log"] is False
    assert kwargs["reload"] is False
