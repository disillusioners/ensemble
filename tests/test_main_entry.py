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
* P2.3 B5.6 (F-DR1-1): the frozen entry ``run_app.py`` (the
  ``ensemble.spec`` PyInstaller entry) runs the SAME
  ``_boot_db_preflight`` explicitly BEFORE delegating to ``main()`` and
  calls ``main(run_preflight=False)`` — exit 75/78 reach the launcher
  on frozen-binary boots too (not uvicorn's exit-3 crash track), and
  the probe fires exactly once per entry. The pack cannot boot a built
  binary, so these tests exercise run_app.py's module body directly
  (runpy); frozen-binary e2e is proven by the DR-1 re-run drill itself.
"""

import os
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


# ── Frozen entry (run_app.py) pre-uvicorn preflight (F-DR1-1, P2.3 B5.6) ────


def _run_run_app():
    """Execute run_app.py (the ensemble.spec frozen entry) in a fresh
    namespace. ``daemon.__main__`` is already imported, so attribute
    patches on the module take effect; the frozen-only ``.env`` block is
    inert under pytest (``sys.frozen`` unset)."""
    import runpy

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return runpy.run_path(
        os.path.join(repo_root, "run_app.py"), run_name="run_app_under_test"
    )


def test_run_app_runs_preflight_before_main(uvicorn_run_patch, tmp_path):
    """Wiring + order + exactly-once: run_app calls _boot_db_preflight
    BEFORE main(), passes run_preflight=False, one probe per boot."""
    import daemon.__main__ as entry

    order = []
    with (
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
        patch.object(
            entry, "_boot_db_preflight", side_effect=lambda: order.append("preflight")
        ) as pf_mock,
        patch.object(
            entry, "main", side_effect=lambda *a, **k: order.append("main")
        ) as main_mock,
    ):
        _run_run_app()

    pf_mock.assert_called_once()
    main_mock.assert_called_once_with(run_preflight=False)
    assert order == ["preflight", "main"]
    uvicorn_run_patch.assert_not_called()  # main() was the mock


def test_run_app_preflight_pg_unreachable_exits_75_before_main(
    uvicorn_run_patch, tmp_path
):
    """F-DR1-1 core: PG unreachable on the frozen path → SystemExit 75
    from run_app's OWN preflight call — BEFORE main() runs, so uvicorn
    never owns the process (no exit-3 crash track)."""
    from sqlalchemy.exc import OperationalError

    import daemon.__main__ as entry

    engine = MagicMock()
    engine.connect.side_effect = OperationalError(
        "SELECT 1", None, Exception("connection refused")
    )

    with (
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=lambda d: _pg_config(postgres=True),
        ),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch.object(entry, "main") as main_mock,
    ):
        with pytest.raises(SystemExit) as excinfo:
            _run_run_app()

    assert excinfo.value.code == 75
    main_mock.assert_not_called()
    uvicorn_run_patch.assert_not_called()


def test_run_app_preflight_pg_auth_refused_exits_78_before_main(
    uvicorn_run_patch, tmp_path
):
    """Auth refusal on the frozen path → SystemExit 78 before main()."""
    import daemon.__main__ as entry

    engine = MagicMock()
    engine.connect.side_effect = _auth_refused_operational_error("28P01")

    with (
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=lambda d: _pg_config(postgres=True),
        ),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch.object(entry, "main") as main_mock,
    ):
        with pytest.raises(SystemExit) as excinfo:
            _run_run_app()

    assert excinfo.value.code == 78
    main_mock.assert_not_called()
    uvicorn_run_patch.assert_not_called()


def test_run_app_success_boots_uvicorn_with_single_probe(uvicorn_run_patch, tmp_path):
    """Happy path end-to-end through REAL run_app + REAL main(): the
    probe engine is built EXACTLY ONCE across both call sites and
    uvicorn.run is reached — no double probe, no new boot steps."""
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    with (
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
        patch(
            "daemon.ensemble_config.EnsembleConfig.load_or_create",
            side_effect=lambda d: _pg_config(postgres=True),
        ),
        patch("sqlalchemy.create_engine", return_value=engine) as engine_mock,
    ):
        _run_run_app()

    engine_mock.assert_called_once()  # single probe across run_app + main
    conn.execute.assert_called_once()
    engine.dispose.assert_called_once()
    uvicorn_run_patch.assert_called_once()


def test_main_default_runs_preflight_once(uvicorn_run_patch, tmp_path):
    """Dev entry (`python -m daemon`) unchanged: main() still probes
    exactly once, in its original position."""
    import daemon.__main__ as entry

    with (
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
        patch.object(entry, "_boot_db_preflight") as pf_mock,
    ):
        entry.main()

    pf_mock.assert_called_once()
    uvicorn_run_patch.assert_called_once()


def test_main_run_preflight_false_skips_probe(uvicorn_run_patch, tmp_path):
    """The skip flag (frozen-entry only) suppresses main()'s internal
    probe without touching any other boot step."""
    import daemon.__main__ as entry

    with (
        patch.dict(
            "os.environ",
            {"ENSEMBLE_DATA_DIR": str(tmp_path), "DATA_DIR": ""},
            clear=False,
        ),
        patch.object(entry, "_boot_db_preflight") as pf_mock,
    ):
        entry.main(run_preflight=False)

    pf_mock.assert_not_called()
    uvicorn_run_patch.assert_called_once()


def test_preflight_contract_constants_unchanged():
    """Equivalence pin: the probe keeps Phase-1's exact contract — exit
    75 tempfail / 78 auth-refused / BOOT_DB_TIMEOUT_S=10s budget."""
    import daemon.__main__ as entry
    from daemon.constants import BOOT_DB_TIMEOUT_S

    assert entry._EXIT_BOOT_DB_TEMPFAIL == 75
    assert entry._EXIT_BOOT_DB_AUTH_REFUSED == 78
    assert BOOT_DB_TIMEOUT_S == 10


def test_run_app_source_pins_preflight_call():
    """Source-shape pin (torn-write guard): the frozen entry keeps the
    explicit pre-uvicorn preflight call BEFORE the skip-flag handoff."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent / "run_app.py"
    ).read_text(encoding="utf-8")
    assert "daemon.__main__._boot_db_preflight()" in src
    assert "daemon.__main__.main(run_preflight=False)" in src
    # order pin: the probe call precedes the main() handoff in source
    assert src.index("daemon.__main__._boot_db_preflight()") < src.index(
        "daemon.__main__.main(run_preflight=False)"
    )


# ── P2.3 final batch (tidier M1/M3) — alert-sink boot seam ─────────────────
# Pack home: boot_probes_unit_test (this file + test_health_probes.py).
# M1: the upgrade-journal SSE emit done-callback must never raise on a
# CANCELLED task (CancelledError from task.exception() — the 2026-07-12
# wait_for_result family). M3: the alert-sink registration must NEVER
# gate daemon startup (import failure → one warning, boot continues).


def test_m1_log_emit_result_cancelled_task_warns_once_not_raises(caplog):
    """M1 (tidier): a cancelled broadcaster.emit task → ONE warning line,
    no CancelledError escaping the done-callback (mutation-lethal guard —
    the pre-fix body called task.exception() unconditionally)."""
    import asyncio
    import logging

    from daemon.tools.upgrade_journal import _log_emit_result

    async def _scenario():
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.sleep(3600))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled() is True
        with caplog.at_level(logging.WARNING, logger="daemon.tools.upgrade_journal"):
            _log_emit_result(task)  # MUST NOT raise
        return task

    asyncio.run(_scenario())
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "CANCELLED" in r.message
    ]
    assert len(warnings) == 1, f"expected exactly one CANCELLED warning, got {warnings!r}"


def test_m1_log_emit_result_failed_task_still_warns(caplog):
    """M1 companion (regression): the pre-existing failure path keeps its
    single warning — the cancelled-guard changed nothing else."""
    import asyncio
    import logging

    from daemon.tools.upgrade_journal import _log_emit_result

    async def _scenario():
        loop = asyncio.get_running_loop()

        async def _boom():
            raise RuntimeError("emit exploded")

        task = loop.create_task(_boom())
        try:
            await task
        except RuntimeError:
            pass
        with caplog.at_level(logging.WARNING, logger="daemon.tools.upgrade_journal"):
            _log_emit_result(task)
        return task

    asyncio.run(_scenario())
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "emit FAILED" in r.message
    ]
    assert len(warnings) == 1


def test_m3_alert_sink_registration_failure_never_gates_boot(monkeypatch, caplog):
    """M3 (tidier): simulated import failure at the registration seam →
    the helper logs ONE warning and RETURNS (never raises — the alert
    sink must never gate daemon startup)."""
    import logging
    import sys

    import daemon.api as api_mod

    # poison the module entry: `import daemon.tools.upgrade_journal`
    # inside the helper raises ImportError ("... halted; None in sys.modules")
    monkeypatch.setitem(sys.modules, "daemon.tools.upgrade_journal", None)
    with caplog.at_level(logging.WARNING, logger="daemon.api"):
        api_mod._register_upgrade_alert_sink(object())  # must NOT raise
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "alert-sink" in r.message
    ]
    assert len(warnings) == 1, f"expected one alert-sink warning, got {[r.message for r in caplog.records]}"
    assert "boot continues" in warnings[0].message


def test_m3_alert_sink_registration_success_path(monkeypatch):
    """M3 companion: happy path still registers exactly once through the
    real register_alert_sink API (last-wins), sinking the broadcaster.
    Runs ON a live loop — broadcaster_alert_sink captures the running
    loop at construction (same condition as the lifespan call site)."""
    import asyncio

    import daemon.tools.upgrade_journal as uj
    import daemon.api as api_mod

    calls = []
    real_register = uj.register_alert_sink

    def _recording_register(sink):
        calls.append(sink)
        return real_register(sink)

    monkeypatch.setattr(uj, "register_alert_sink", _recording_register)
    broadcaster = object()

    async def _scenario():
        api_mod._register_upgrade_alert_sink(broadcaster)

    asyncio.run(_scenario())
    assert len(calls) == 1
    # the registered sink IS the broadcaster bridge (wrapped, not raw)
    assert calls[0] is not broadcaster
    assert callable(calls[0])
    # restore no-op sink so later tests see a clean registry
    real_register(None)
