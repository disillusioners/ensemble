"""Unit tests for the DB-resolution guard (component B, pin (iii)).

Fix-round 2 / N5: the ACTUAL decided polarity is pinned —
  * guard-internal error => FAIL-CLOSED (exit 78, loud);
  * both boot call sites are pinned (B7(iii));
  * the documented lifespan exit-3 mapping is pinned structurally.
"""

from pathlib import Path

import pytest

from daemon.ensemble_config import EnsembleConfig, assert_db_resolution_sanctioned

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_guard_fires_on_ambient_poison(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    assert not (data_dir / "ensemble.json").exists()
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_DB", "ens_e2e_scrub_probe")
    monkeypatch.delenv("ENSEMBLE_DB_AMBIENT_ALLOW", raising=False)
    with pytest.raises(SystemExit) as exc:
        assert_db_resolution_sanctioned(data_dir)
    assert exc.value.code == 78


def test_guard_does_not_fire_tier1(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "ensemble.json").write_text('{"database":"sqlite"}')
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_DB", "ens_e2e_scrub_probe")
    monkeypatch.delenv("ENSEMBLE_DB_AMBIENT_ALLOW", raising=False)
    assert_db_resolution_sanctioned(data_dir)  # must not raise


def test_guard_does_not_fire_sqlite_default(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.delenv("ENSEMBLE_DB_AMBIENT_ALLOW", raising=False)
    assert_db_resolution_sanctioned(data_dir)


def test_guard_fence_var_allows(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_DB", "ens_e2e_scrub_probe")
    monkeypatch.setenv("ENSEMBLE_DB_AMBIENT_ALLOW", "1")
    assert_db_resolution_sanctioned(data_dir)  # must not raise


def test_guard_does_not_call_create_engine(tmp_path, monkeypatch):
    import sqlalchemy

    sentinel = {"called": False}

    def fake_create_engine(*a, **kw):
        sentinel["called"] = True
        raise RuntimeError("engine must not be constructed on refuse path")

    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_DB", "ens_e2e_scrub_probe")
    monkeypatch.delenv("ENSEMBLE_DB_AMBIENT_ALLOW", raising=False)
    with pytest.raises(SystemExit):
        assert_db_resolution_sanctioned(data_dir)
    assert sentinel["called"] is False


def test_guard_internal_error_fails_CLOSED_exit_78(caplog):
    """N5 (leader fail-polarity): an unverifiable sanction must NOT proceed."""
    with caplog.at_level("ERROR", logger="daemon.ensemble_config"):
        with pytest.raises(SystemExit) as exc:
            assert_db_resolution_sanctioned(object())  # Path(...) raises inside
    assert exc.value.code == 78
    assert "UNVERIFIABLE" in caplog.text


def test_predicate_strings_present():
    import inspect

    src = inspect.getsource(assert_db_resolution_sanctioned)
    assert 'os.environ.get("POSTGRES_HOST")' in src
    assert 'os.environ.get("POSTGRES_DB")' in src
    assert 'os.environ.get("ENSEMBLE_DB_AMBIENT_ALLOW") == "1"' in src


# ---------------------------------------------------------------------------
# B7(iii) — BOTH boot call sites, pinned
# ---------------------------------------------------------------------------


def test_main_boot_preflight_call_site_exits_78_before_load(tmp_path, monkeypatch):
    from daemon import __main__ as dm

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_DB", "ens_e2e_scrub_probe")
    monkeypatch.delenv("ENSEMBLE_DB_AMBIENT_ALLOW", raising=False)

    called = {"load": False}

    def _sentinel_load(cls, d):
        called["load"] = True

    monkeypatch.setattr(EnsembleConfig, "load_or_create", classmethod(_sentinel_load))
    with pytest.raises(SystemExit) as exc:
        dm._boot_db_preflight()
    assert exc.value.code == 78
    assert called["load"] is False, "load_or_create must not run after a refusal"


def test_api_lifespan_call_site_is_ordered_before_load():
    """Structural pin of the second boot call site: the guard must run inside
    the lifespan BEFORE ``EnsembleConfig.load_or_create``."""
    src = (REPO_ROOT / "daemon" / "api.py").read_text(encoding="utf-8")
    lifespan_start = src.index("async def lifespan(app: FastAPI):")
    lifespan_yield = src.index("\n        yield", lifespan_start)
    guard = src.index("assert_db_resolution_sanctioned(data_dir)")
    load = src.index("EnsembleConfig.load_or_create(data_dir)", lifespan_start)
    assert lifespan_start < guard < lifespan_yield
    assert guard < load
    assert src.count("assert_db_resolution_sanctioned(data_dir)") == 1


def test_lifespan_startup_failure_maps_to_exit_3():
    """N5: the documented lifespan exit-3 mapping is pinned (uvicorn's
    STARTUP_FAILURE is what ``_serve_with_bind_retry`` exits when the
    server never started)."""
    from uvicorn.main import STARTUP_FAILURE

    assert STARTUP_FAILURE == 3
    src = (REPO_ROOT / "daemon" / "__main__.py").read_text(encoding="utf-8")
    assert "sys.exit(STARTUP_FAILURE)" in src
    assert "not server.started" in src
