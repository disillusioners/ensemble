"""db-backed-settings Phase 4 (t1+t2) — the resolution CENTERPIECE.

Unit tests for the ONE shared tier resolver
(``daemon.services.settings_service.resolve``) and the boot-apply hook
(``apply_db_settings_overrides``) against the Phase-1 implementation.

Ladder under test (BINDING — architecture-recommendation.md)::

    live DB row  >  os.environ[env_var]  >  yaml literal  >  registry default

The acceptance case (a) — the env SHADOWING fix — is authored to MUST-FAIL
against any implementation that derives the env tier from pydantic's env
tier (i.e. from the merged ``Config.limits.llm_concurrency`` field):
``load_config`` passes yaml sections as pydantic INIT KWARGS and init
kwargs beat env vars in pydantic-settings, so while a yaml literal
exists the merged field shows the yaml value (10), never the env value
(7). A resolver reading that field reports ``value=10, source="yaml"``
and this file's shadowing tests fail loud. (Proven on the real
pydantic behavior in the commission evidence; see the shadowing tests
below — the yaml tier here is ALWAYS a hand-built snapshot carrying the
RAW literal, never the merged field.)

Fence discipline (2026-10-12 critical note — zero tolerance): every
temp path is a ``tempfile``/``tmp_path`` artifact under the system temp
dir; ZERO HOME-derived or install-dir paths are resolved; nothing is
removed outside this file's own temp dirs.

Env discipline: ``LIMITS_LLM_CONCURRENCY`` is NOT in the conftest
``clean_env`` tracked set (tests/conftest.py ``_TRACKED_ENV_EXACT`` /
``_TRACKED_ENV_PREFIXES``), so every read of the env tier in this file
goes through an explicit ``monkeypatch.setenv``/``delenv`` — the tier
under test is never ambient.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon.config import Config
from daemon.models.app_settings import AppSetting
from daemon.services import settings_service
from daemon.services.settings_service import (
    MANAGED_KEYS,
    SettingsService,
    SettingsValidationError,
    UnknownManagedKeyError,
    apply_db_settings_overrides,
    parse_and_validate,
    resolve,
)

KEY = "limits.llm_concurrency"
SPEC = MANAGED_KEYS[KEY]


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _env(monkeypatch: pytest.MonkeyPatch, raw: str | None) -> None:
    """Pin the env tier explicitly (set, or delete when ``raw`` is None)."""
    if raw is None:
        monkeypatch.delenv(SPEC.env_var, raising=False)
    else:
        monkeypatch.setenv(SPEC.env_var, raw)


def _snapshot(
    *,
    default: Any = 10,
    env_value: Any = None,
    yaml_value: Any = None,
    applied_value: Any = None,
) -> dict[str, dict[str, Any]]:
    """Hand-built pristine snapshot — the resolver's ONLY tier input."""
    return {
        KEY: {
            "default": default,
            "env_value": env_value,
            "yaml_value": yaml_value,
            **({"applied_value": applied_value} if applied_value is not None else {}),
        }
    }


def _make_config(llm_concurrency: int = 10, db_path: str = "./data/instances.db") -> Config:
    """A Config with the yaml-literal-equivalent merged in (init kwargs
    beat env in pydantic-settings — exactly the ``load_config`` shape)."""
    return Config(
        limits={"llm_concurrency": llm_concurrency},
        persistence={"db_path": db_path},
    )


def _ensemble_sqlite() -> SimpleNamespace:
    """SQLite-route ensemble config stub (``is_postgres`` is the only read)."""
    return SimpleNamespace(is_postgres=False)


def _seed_rows(db_path: str, rows: list[dict[str, Any]]) -> None:
    """Create the app_settings table + insert rows on a fresh engine."""
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        SQLModel.metadata.create_all(engine, tables=[AppSetting.__table__])
        with Session(engine) as session:
            for row in rows:
                session.add(AppSetting(**row))
            session.commit()
    finally:
        engine.dispose()


def _row(
    value_json: str = "5",
    value_type: str = "int",
    key: str = KEY,
    updated_by: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "value_json": value_json,
        "value_type": value_type,
        "updated_by": updated_by,
        "updated_at": "2026-10-18T00:00:00+00:00",
    }


def _spy_bootstrap_engine_dispose(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Wrap ``_build_bootstrap_engine`` so each built engine's ``dispose``
    is recorded. Returns the list of engines disposed (the spy evidence)."""
    disposed: list[Any] = []
    real_build = settings_service._build_bootstrap_engine

    def wrapped(ensemble_config: Any, config: Any):
        engine = real_build(ensemble_config, config)
        real_dispose = engine.dispose

        def _dispose() -> None:
            disposed.append(engine)
            real_dispose()

        engine.dispose = _dispose
        return engine

    monkeypatch.setattr(settings_service, "_build_bootstrap_engine", wrapped)
    return disposed


@pytest.fixture
def svc_engine(tmp_path):
    """Temp-SQLite engine with the app_settings table created
    (CRUD-lane fixture — mirrors the ``settings_engine`` fixture of
    ``tests/test_daemon_settings_api.py:158-166``)."""
    engine = create_engine(f"sqlite:///{tmp_path}/svc.db")
    SQLModel.metadata.create_all(engine, tables=[AppSetting.__table__])
    try:
        yield engine
    finally:
        engine.dispose()


# ────────────────────────────────────────────────────────────────────────────
# (a)-(f) — the resolver ladder
# ────────────────────────────────────────────────────────────────────────────


class TestResolverLadder:
    def test_a_env_beats_yaml_literal_shadowing_fix(self, monkeypatch):
        """(a) THE acceptance case: yaml literal 10 present + env 7 → 7/env.

        MUST-FAIL-BY-CONSTRUCTION against a pydantic-env-tier implementation:
        with a yaml literal merged as an init kwarg, pydantic-settings'
        env tier is dead (init kwargs win) — the merged field reads 10.
        Only a resolver reading ``os.environ`` DIRECTLY can report 7/env.
        """
        _env(monkeypatch, "7")
        snap = _snapshot(yaml_value=10, env_value="7")
        result = resolve(KEY, snap)
        assert result["value"] == 7
        assert result["source"] == "env"
        # Tier detail carriage: parsed+validated per tier.
        assert result["env_value"] == 7
        assert result["yaml_value"] == 10
        assert result["db_override"] is None
        assert result["default"] == 10

    def test_a_resolver_reads_live_env_not_snapshot_bookkeeping(self, monkeypatch):
        """(a, unit pin) snapshot bookkeeping says env ABSENT
        (``env_value=None``) WHILE the live env var is SET → the
        resolver reads ``os.environ`` DIRECTLY, not the snapshot
        bookkeeping, and reports the env tier with the PARSED value.
        Must-FAIL against a snapshot-bookkeeping-trusting (or
        pydantic-env-tier) implementation. Boot-level twin:
        ``test_env_beats_yaml_at_boot_the_shadowing_fix``."""
        _env(monkeypatch, "7")
        snap = _snapshot(yaml_value=10, env_value=None)  # bookkeeping: no env
        result = resolve(KEY, snap)
        assert result["source"] == "env"
        assert result["value"] == 7
        assert result["env_value"] == 7
        assert result["yaml_value"] == 10

    def test_b_db_beats_env(self, monkeypatch):
        """(b) override row + env set → db wins (three-POSITIONAL shape)."""
        _env(monkeypatch, "7")
        snap = _snapshot(yaml_value=10, env_value="7")
        result = resolve(KEY, snap, {KEY: 3})
        assert result["value"] == 3
        assert result["source"] == "db"
        assert result["db_override"] == 3
        assert result["env_value"] == 7

    def test_b_db_overrides_kwarg_shape(self, monkeypatch):
        """(b, deviation cover) the SAME case via the ``db_overrides=``
        keyword form — both invocation shapes must behave identically."""
        _env(monkeypatch, "7")
        result = resolve(KEY, _snapshot(yaml_value=10), db_overrides={KEY: 4})
        assert result["value"] == 4
        assert result["source"] == "db"

    def test_c_yaml_tier_wins_when_no_env_no_row(self, monkeypatch):
        """(c) no env, no row → the RAW yaml literal wins."""
        _env(monkeypatch, None)
        result = resolve(KEY, _snapshot(yaml_value=10))
        assert result["value"] == 10
        assert result["source"] == "yaml"
        assert result["yaml_value"] == 10
        assert result["env_value"] is None
        assert result["db_override"] is None

    def test_c_two_positional_shape_still_works(self, monkeypatch):
        """(c, deviation cover) two-positional call (no third arg) — the
        pre-deviation call shape must keep working (db tier simply absent)."""
        _env(monkeypatch, None)
        result = resolve(KEY, _snapshot(yaml_value=8))
        assert result["value"] == 8
        assert result["source"] == "yaml"

    def test_d_default_tier_when_nothing_set(self, monkeypatch):
        """(d) nothing set → registry default, source="default".

        This is the case that bans deriving the yaml tier from the
        pydantic-merged Config field: a default-only install cannot be
        distinguished from a yaml literal by the merged field, and such
        an implementation would mis-report source="yaml" here.
        """
        _env(monkeypatch, None)
        snap = _snapshot(yaml_value=None)
        result = resolve(KEY, snap)
        assert result["value"] == SPEC.default == 10
        assert result["source"] == "default"
        assert result["yaml_value"] is None
        assert result["env_value"] is None

    def test_d_default_tier_with_empty_or_missing_snapshot(self, monkeypatch):
        """(d) empty snapshot dict / None snapshot — same default tier."""
        _env(monkeypatch, None)
        for snap in ({}, None):
            result = resolve(KEY, snap)
            assert result["source"] == "default"
            assert result["value"] == 10

    def test_e_invalid_db_values_skipped_lower_tier_wins(self, monkeypatch):
        """(e) invalid DB values (0 → range fail, "junk" → parse fail)
        never win; the env tier below them does."""
        _env(monkeypatch, "7")
        snap = _snapshot(yaml_value=10, env_value="7")
        for bad in (0, "junk"):
            result = resolve(KEY, snap, {KEY: bad})
            assert result["source"] == "env", f"bad db value {bad!r} must be skipped"
            assert result["value"] == 7
            # An invalid tier never renders its detail field either.
            assert result["db_override"] is None

    def test_e_invalid_env_value_skipped_yaml_wins(self, monkeypatch, caplog):
        _env(monkeypatch, "junk")
        with caplog.at_level(logging.WARNING, logger="daemon.services.settings_service"):
            result = resolve(KEY, _snapshot(yaml_value=10))
        assert result["source"] == "yaml"
        assert result["value"] == 10
        assert result["env_value"] is None
        # Tier-skip observability: one WARNING carrying key + env-var
        # name + failure class (parse).
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        assert KEY in msg and SPEC.env_var in msg and "failed parse" in msg

    def test_e_invalid_yaml_value_skipped_default_wins(self, monkeypatch, caplog):
        _env(monkeypatch, None)
        with caplog.at_level(logging.WARNING, logger="daemon.services.settings_service"):
            result = resolve(KEY, _snapshot(yaml_value=0))
        assert result["source"] == "default"
        assert result["value"] == 10
        assert result["yaml_value"] is None
        # Tier-skip observability: one WARNING carrying key + tier name
        # + failure class (validation — ge=1).
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        assert KEY in msg and "yaml tier" in msg and "failed validation" in msg

    def test_e_bool_never_smuggles_through_int_tiers(self, monkeypatch):
        """Strictness pin: JSON ``true`` must not parse as int 1."""
        _env(monkeypatch, None)
        result = resolve(KEY, _snapshot(yaml_value=None), {KEY: True})
        assert result["source"] == "default"
        assert result["db_override"] is None

    def test_f_unknown_key_refused_by_resolver(self):
        with pytest.raises(UnknownManagedKeyError):
            resolve("nope.not_managed", _snapshot())

    def test_f_unknown_key_refused_by_service(self, svc_engine):
        """(f) the registry gate spans the service CRUD too."""
        service = SettingsService(svc_engine)
        with pytest.raises(UnknownManagedKeyError):
            service.set_override("nope.not_managed", 5)
        with pytest.raises(UnknownManagedKeyError):
            service.clear_override("nope.not_managed")


class TestParseAndValidate:
    """The ONE validation truth — mirrors the pydantic Field exactly."""

    def test_int_accepts_int_and_clean_string(self):
        assert parse_and_validate(SPEC, 5)[0] is True
        assert parse_and_validate(SPEC, "5")[1] == 5

    def test_int_rejects_bool_float_and_junk(self):
        for raw in (True, 7.5, "junk", None):
            ok, _v, failure_class, _d = parse_and_validate(SPEC, raw)
            assert ok is False, raw
            assert failure_class in ("parse", "validation")

    def test_range_ge1(self):
        ok, _v, failure_class, _d = parse_and_validate(SPEC, 0)
        assert ok is False
        assert failure_class == "validation"
        ok, value, _c, _d = parse_and_validate(SPEC, 1)
        assert ok is True and value == 1


# ────────────────────────────────────────────────────────────────────────────
# SettingsService CRUD on temp SQLite (registry gate + row round-trip)
# ────────────────────────────────────────────────────────────────────────────


class TestSettingsServiceCrud:
    def test_round_trip_set_get_clear(self, svc_engine):
        service = SettingsService(svc_engine)
        assert service.get_overrides() == {}
        service.set_override(KEY, 6, updated_by="operator-cli")
        assert service.get_overrides() == {KEY: 6}
        assert service.clear_override(KEY) is True
        assert service.get_overrides() == {}
        assert service.clear_override(KEY) is False  # idempotent

    def test_set_override_rejects_invalid_values(self, svc_engine):
        service = SettingsService(svc_engine)
        for bad in (0, "junk", True):
            with pytest.raises(SettingsValidationError):
                service.set_override(KEY, bad)
        assert service.get_overrides() == {}  # nothing written

    def test_empty_actor_string_normalized_to_null(self, svc_engine):
        """``updated_by=""`` → NULL row (never the empty string)."""
        SettingsService(svc_engine).set_override(KEY, 6, updated_by="")
        with Session(svc_engine) as session:
            row = session.get(AppSetting, KEY)
            assert row is not None
            assert row.updated_by is None

    def test_unregistered_row_skipped_with_warning(self, svc_engine, tmp_path, caplog):
        """Direct-DB tampering guard: unregistered row never surfaces."""
        _seed_rows(str(tmp_path / "svc.db"), [_row(key="evil.freeform")])
        with caplog.at_level(logging.WARNING, logger="daemon.services.settings_service"):
            overrides = SettingsService(svc_engine).get_overrides()
        assert overrides == {}
        assert any(
            "unregistered app_settings row" in r.getMessage() for r in caplog.records
        )

    @pytest.mark.parametrize(
        ("value_json", "expected_marker"),
        [
            ("0", "failed validation"),  # range failure (ge=1)
            ('"junk"', "failed parse"),  # undecodable-as-int value
        ],
    )
    def test_invalid_row_skipped_with_class_specific_error_log(
        self, svc_engine, tmp_path, caplog, value_json, expected_marker
    ):
        """(e, row-level) an invalid row is skipped with its CLASS-specific
        ERROR log and lets lower tiers win (one row per key — PK)."""
        _seed_rows(str(tmp_path / "svc.db"), [_row(value_json=value_json)])
        with caplog.at_level(logging.ERROR, logger="daemon.services.settings_service"):
            overrides = SettingsService(svc_engine).get_overrides()
        assert overrides == {}
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert expected_marker in joined
        assert "lower tier wins" in joined


# ────────────────────────────────────────────────────────────────────────────
# (bootstrap-read) — apply_db_settings_overrides on temp SQLite
# ────────────────────────────────────────────────────────────────────────────


class TestBootstrapApply:
    def test_seeded_row_mutates_config_and_carries_applied_value(self, tmp_path, monkeypatch):
        """Temp SQLite seeded row → the returned Config carries the override
        at EXACTLY the attribute chain ``InstanceManager.__init__`` reads
        (manager.py:555 — ``asyncio.Semaphore(config.limits.llm_concurrency)``)."""
        _env(monkeypatch, None)
        db_path = str(tmp_path / "boot.db")
        _seed_rows(db_path, [_row(value_json="5")])
        config = _make_config(llm_concurrency=10, db_path=db_path)

        disposed = _spy_bootstrap_engine_dispose(monkeypatch)
        returned, snapshot = apply_db_settings_overrides(
            config, _ensemble_sqlite(), raw_yaml_limits={"llm_concurrency": 10}
        )

        assert returned is config  # same object, mutated in place
        # The exact value InstanceManager.__init__ would read:
        assert config.limits.llm_concurrency == 5
        # Snapshot carriage: {default, env_value, yaml_value, applied_value}
        assert snapshot[KEY] == {
            "default": 10,
            "env_value": None,
            "yaml_value": 10,
            "applied_value": 5,
        }
        # The throwaway engine serves the boot read ONLY — disposed on success.
        assert len(disposed) == 1

    def test_env_beats_yaml_at_boot_the_shadowing_fix(self, tmp_path, monkeypatch):
        """Boot-apply end of the (a) acceptance: with a yaml literal merged
        into the Config (init kwargs — env dead in pydantic) AND the env var
        set, boot-apply assigns the ENV winner, not the yaml literal."""
        _env(monkeypatch, "7")
        db_path = str(tmp_path / "boot.db")
        config = _make_config(llm_concurrency=10, db_path=db_path)  # "yaml literal" 10

        apply_db_settings_overrides(
            config, _ensemble_sqlite(), raw_yaml_limits={"llm_concurrency": 10}
        )

        assert config.limits.llm_concurrency == 7  # env applied over yaml
        # (accepted deviation: boot-apply assigns the resolved winner for
        # EVERY managed key, not only DB-present ones)

    def test_yaml_literal_applied_when_no_env_no_row(self, tmp_path, monkeypatch):
        _env(monkeypatch, None)
        db_path = str(tmp_path / "boot.db")
        config = _make_config(llm_concurrency=10, db_path=db_path)
        _returned, snapshot = apply_db_settings_overrides(
            config, _ensemble_sqlite(), raw_yaml_limits={"llm_concurrency": 10}
        )
        assert config.limits.llm_concurrency == 10
        assert snapshot[KEY]["applied_value"] == 10
        assert snapshot[KEY]["yaml_value"] == 10

    def test_default_only_install_reports_default_tier(self, tmp_path, monkeypatch):
        """No env, no row, no yaml literal → applied=default, snapshot
        yaml_value ABSENT (never derived from the merged field)."""
        _env(monkeypatch, None)
        db_path = str(tmp_path / "boot.db")
        config = _make_config(llm_concurrency=10, db_path=db_path)
        _returned, snapshot = apply_db_settings_overrides(
            config, _ensemble_sqlite(), raw_yaml_limits=None
        )
        assert snapshot[KEY]["yaml_value"] is None
        assert snapshot[KEY]["applied_value"] == 10
        assert config.limits.llm_concurrency == 10

    def test_missing_table_first_boot_config_unchanged_no_raise(
        self, tmp_path, monkeypatch, caplog
    ):
        """Load-bearing on EVERY first boot: the read runs BEFORE
        create_all / the SQLite runner — missing table = no overrides."""
        _env(monkeypatch, None)
        db_path = str(tmp_path / "fresh.db")  # exists only as a path — no tables
        create_engine(f"sqlite:///{db_path}").dispose()
        config = _make_config(llm_concurrency=10, db_path=db_path)

        with caplog.at_level(logging.INFO, logger="daemon.services.settings_service"):
            returned, snapshot = apply_db_settings_overrides(
                config, _ensemble_sqlite(), raw_yaml_limits=None
            )

        assert returned is config
        assert config.limits.llm_concurrency == 10  # unchanged
        assert snapshot[KEY]["applied_value"] == 10
        assert any("app_settings table absent" in r.getMessage() for r in caplog.records)

    def test_dead_engine_read_failure_config_unchanged_error_dispose(
        self, tmp_path, monkeypatch, caplog
    ):
        """Dead-engine path: the read fails AFTER the engine is built →
        Config unchanged (no env/yaml set → literally unchanged), ERROR
        log naming the fallback (lower tiers), and the finally-dispose
        STILL ran on the failure path."""
        _env(monkeypatch, None)
        db_path = str(tmp_path / "boot.db")
        config = _make_config(llm_concurrency=10, db_path=db_path)

        disposed = _spy_bootstrap_engine_dispose(monkeypatch)

        def _boom(session: Any) -> dict:
            raise RuntimeError("simulated dead database")

        monkeypatch.setattr(settings_service, "_read_managed_rows", _boom)
        with caplog.at_level(logging.ERROR, logger="daemon.services.settings_service"):
            returned, snapshot = apply_db_settings_overrides(
                config, _ensemble_sqlite(), raw_yaml_limits=None
            )

        assert returned is config
        assert config.limits.llm_concurrency == 10  # unchanged
        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(
            "boot read failed" in m and "fail-open" in m and "lower tiers" in m for m in errors
        )
        # finally-dispose fired on the FAILURE path too.
        assert len(disposed) == 1

    def test_dead_engine_still_applies_lower_tiers(self, tmp_path, monkeypatch):
        """Fail-open proof: with the DB read dead, the env tier below it
        still applies (the ladder keeps resolving down the tiers)."""
        _env(monkeypatch, "7")
        db_path = str(tmp_path / "boot.db")
        config = _make_config(llm_concurrency=10, db_path=db_path)

        def _boom(session: Any) -> dict:
            raise RuntimeError("simulated dead database")

        monkeypatch.setattr(settings_service, "_read_managed_rows", _boom)
        apply_db_settings_overrides(
            config, _ensemble_sqlite(), raw_yaml_limits={"llm_concurrency": 10}
        )
        assert config.limits.llm_concurrency == 7  # env tier applied despite dead DB

    def test_engine_build_failure_config_unchanged_no_raise(
        self, tmp_path, monkeypatch, caplog
    ):
        """Worst-case failure: the engine cannot even be built → no engine
        to dispose, Config unchanged (fail-open), ERROR logged."""
        _env(monkeypatch, None)
        db_path = str(tmp_path / "boot.db")
        config = _make_config(llm_concurrency=10, db_path=db_path)

        def _unbuildable(ensemble_config: Any, cfg: Any):
            raise OSError("simulated unwritable data dir")

        monkeypatch.setattr(settings_service, "_build_bootstrap_engine", _unbuildable)
        with caplog.at_level(logging.ERROR, logger="daemon.services.settings_service"):
            returned, snapshot = apply_db_settings_overrides(
                config, _ensemble_sqlite(), raw_yaml_limits=None
            )

        assert returned is config
        assert config.limits.llm_concurrency == 10
        assert any("boot read failed" in r.getMessage() for r in caplog.records)
        assert snapshot[KEY]["applied_value"] == 10

    @pytest.mark.parametrize(
        ("value_json", "expected_marker"),
        [
            ("0", "failed validation"),
            ('"junk"', "failed parse"),
        ],
    )
    def test_invalid_row_at_boot_skips_to_lower_tier_with_error(
        self, tmp_path, monkeypatch, caplog, value_json, expected_marker
    ):
        """(e, boot-level) a seeded invalid row is skipped with its
        class-specific ERROR log; the env tier below it is applied."""
        _env(monkeypatch, "7")
        db_path = str(tmp_path / "boot.db")
        _seed_rows(db_path, [_row(value_json=value_json)])
        config = _make_config(llm_concurrency=10, db_path=db_path)

        with caplog.at_level(logging.ERROR, logger="daemon.services.settings_service"):
            _returned, snapshot = apply_db_settings_overrides(
                config, _ensemble_sqlite(), raw_yaml_limits={"llm_concurrency": 10}
            )

        assert config.limits.llm_concurrency == 7  # env tier won over the bad row
        assert snapshot[KEY]["applied_value"] == 7
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert expected_marker in joined


# ────────────────────────────────────────────────────────────────────────────
# Snapshot module-global — ONE storage (accepted deviation pin)
# ────────────────────────────────────────────────────────────────────────────


class TestSnapshotSingleStorage:
    def test_router_setter_delegates_to_service_module_global(self, monkeypatch):
        """The router's setter/getter delegate to the service module's
        module-global — there is exactly ONE copy of the snapshot."""
        from daemon.routers import settings as routers_settings

        monkeypatch.setattr(settings_service, "_resolution_snapshot", None)
        snap = _snapshot(applied_value=7)
        routers_settings.set_settings_resolution_snapshot(snap)
        assert settings_service.get_settings_resolution_snapshot() == snap
        # And the router getter reads the same storage back.
        assert routers_settings.get_settings_resolution_snapshot() == snap

    def test_snapshot_getter_none_when_never_set(self, monkeypatch):
        monkeypatch.setattr(settings_service, "_resolution_snapshot", None)
        assert settings_service.get_settings_resolution_snapshot() is None
