"""Tests for daemon/__main__.py — boot preflight + bounded graceful shutdown.

Auto-Restart Phase 1:

* Exit-75 boot preflight (ADR-011): PostgreSQL selected + unreachable →
  ``SystemExit(75)``; reachable → proceeds; SQLite → check skipped.
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
    return cfg


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

    def fake_engine(cfg):
        engine = MagicMock()
        engine.connect.side_effect = OperationalError(
            "SELECT 1", None, Exception("connection refused")
        )
        return engine

    with (
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=fake_load_or_create,
        ) as load_mock,
        patch(
            "daemon.repositories.factory.create_postgres_engine",
            side_effect=fake_engine,
        ) as engine_mock,
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
        patch(
            "daemon.repositories.factory.create_postgres_engine",
            return_value=engine,
        ),
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
        patch(
            "daemon.repositories.factory.create_postgres_engine"
        ) as engine_mock,
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
