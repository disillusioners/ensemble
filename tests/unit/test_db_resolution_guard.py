"""Unit tests for the DB-resolution guard (component B, pin (iii))."""

import os
from pathlib import Path

import pytest

from daemon.ensemble_config import assert_db_resolution_sanctioned, EnsembleConfig


def test_guard_fires_on_ambient_poison(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # no ensemble.json => tier 2
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
    # must not raise
    assert_db_resolution_sanctioned(data_dir)


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
    # must not raise
    assert_db_resolution_sanctioned(data_dir)


def test_guard_does_not_call_create_engine(tmp_path, monkeypatch):
    # Non-vacuity control: sentinel engine factory must not be invoked.
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


def test_counter_mutation_removing_predicate_would_fire_engine(tmp_path, monkeypatch):
    # Documents the counter-mutation: removing the ambient predicate makes
    # the guard never fire, so load_or_create proceeds to tier 2. Pin the
    # presence of the predicate by checking the source.
    src = Path("daemon/ensemble_config.py").read_text() if False else None
    import inspect
    src = inspect.getsource(assert_db_resolution_sanctioned)
    assert "POSTGRES_HOST" in src
    assert "POSTGRES_DB" in src
    assert "ENSEMBLE_DB_AMBIENT_ALLOW" in src
