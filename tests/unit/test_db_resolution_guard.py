"""Unit tests for the DB-resolution guard (component B, pin (iii)).

Fix-round 2 / N5: the ACTUAL decided polarity is pinned —
  * guard-internal error => FAIL-CLOSED (exit 78, loud);
  * both boot call sites are pinned (B7(iii));
  * the documented lifespan exit-3 mapping is pinned structurally.
"""

from pathlib import Path

import pytest

from daemon.ensemble_config import (
    EnsembleConfig,
    assert_db_resolution_sanctioned,
    assert_prod_dsn_sanctioned,
    resolved_postgres_db_name,
)

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
    """Tier-1 SQLITE json (no DSN) — the ambient tier-2 guard does not
    fire. Re-scoped (rogue-containment p2, B2 §12-C3 / P0): the
    no-fire claim holds for NON-PROD resolutions only — this leg's
    ambient name is the scratch probe and the config is sqlite, so the
    post-load prod-DSN fence (assert_prod_dsn_sanctioned) does not fire
    either. A tier-1 POSTGRES config whose resolved name is
    ensemble_prod now REFUSES for a non-live identity — pinned by
    test_tier1_flip_side_by_side (below) and the BF family in
    tests/unit/test_prod_dsn_fence.py."""
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


# ---------------------------------------------------------------------------
# rogue-containment p2 — component B2 (tier-1 prod-DSN fence) extensions.
# BF family per component-E2-pins-verification-measurement.md §E2; pins
# P0..P10 per component-B-tier1-prod-dsn-fence.md §7. Offline only:
# identity monkeypatched at the resolver, scratch names, no real PG.
# ---------------------------------------------------------------------------


def _patch_b2_identity(monkeypatch, marker, source="auto"):
    """Patch boot identity at the RESOLVER module attrs. The B2 guard
    imports ``_self_env_marker`` / ``_self_env_source`` function-locally
    ⇒ call-time lookup ⇒ these patches are authoritative."""
    monkeypatch.setattr(
        "daemon.tools.upgrade_tools._self_env_marker", lambda: marker
    )
    monkeypatch.setattr(
        "daemon.tools.upgrade_tools._self_env_source", lambda: source
    )


def test_prod_dsn_predicate_strings_present():
    """BF-7 | mutant BF-M7: the B2 predicate + marker literals are pinned
    CHARACTER-FOR-CHARACTER (POK plan-body-ledger-agreement — these
    mirror component-B-tier1-prod-dsn-fence.md §4.2/§4.8 verbatim; any
    drift REDs). Also pins the env-wins expression at BOTH DSN
    construction sites (EnsembleConfig.get_postgres_url AND
    repositories.factory.create_postgres_engine — B2 §4.2 second-site
    cross-reference)."""
    import inspect

    from daemon.ensemble_config import (
        assert_prod_dsn_sanctioned as _guard,
    )
    from daemon.ensemble_config import (
        resolved_postgres_db_name as _helper,
    )
    from daemon.repositories import factory as _factory

    helper_src = inspect.getsource(_helper)
    assert 'os.environ.get("POSTGRES_DB", config.postgres.db)' in helper_src
    assert ".strip().lower()" in helper_src

    guard_src = inspect.getsource(_guard)
    assert (
        'resolved_postgres_db_name(ensemble_config) == "ensemble_prod"'
        in guard_src
    )
    assert "tier=resolved-dsn-targets-prod" in guard_src
    assert "DB RESOLUTION REFUSED (rogue-containment)" in guard_src
    assert "self_env_source=" in guard_src
    # Independent-gating (B2 §4.6): the prod-name decision path NEVER
    # READS the fence var (the docstring naming it is the contract
    # statement; a code read-shape here would be the bypass). Behavioral
    # face: P7 in test_prod_dsn_fence.py.
    assert 'os.environ.get("ENSEMBLE_DB_AMBIENT_ALLOW"' not in guard_src

    # env-wins expression present at BOTH DSN sites (a drift in either
    # is caught — counter-mutant: factory rewrites the line to pg.db only)
    assert 'db = os.environ.get("POSTGRES_DB", pg.db)' in inspect.getsource(
        EnsembleConfig.get_postgres_url
    )
    assert 'db = os.environ.get("POSTGRES_DB", pg.db)' in inspect.getsource(
        _factory.create_postgres_engine
    )


def test_tier1_flip_side_by_side(tmp_path, monkeypatch):
    """BF-8 | mutant BF-M8: the tier-1 semantic flip asserted BOTH
    directions SIDE-BY-SIDE — the same postgres-shaped tier-1 json
    REFUSES when its resolved db name is ensemble_prod (identity
    sandbox), and still passes when the name is a scratch name, so
    neither direction can silently regress. Mutant BF-M8 (reintroduce
    the old unconditional 'tier-1 => sanctioned' short-circuit in the
    prod-DSN guard) turns the prod leg green-where-should-red. The
    sqlite leg (test_guard_does_not_fire_tier1) stays green throughout."""
    prod_dir = tmp_path / "prod"
    prod_dir.mkdir()
    (prod_dir / "ensemble.json").write_text(
        '{"database":"postgres","postgres":{"db":"ensemble_prod",'
        '"host":"127.0.0.1","port":5432,"user":"u","password":"p"}}'
    )
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    (scratch_dir / "ensemble.json").write_text(
        '{"database":"postgres","postgres":{"db":"ens_e2e_scrub_probe",'
        '"host":"127.0.0.1","port":5432,"user":"u","password":"p"}}'
    )
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    _patch_b2_identity(monkeypatch, "sandbox", "auto")
    with pytest.raises(SystemExit) as exc:
        assert_prod_dsn_sanctioned(EnsembleConfig.load_or_create(prod_dir))
    assert exc.value.code == 78
    assert_prod_dsn_sanctioned(
        EnsembleConfig.load_or_create(scratch_dir)
    )  # must not raise


def test_prod_dsn_guard_ordering_in_boot_preflight():
    """P8 (main face): in ``_boot_db_preflight`` the prod-DSN guard runs
    AFTER ``load_or_create`` and BEFORE the connectivity probe's engine
    construction — pre-TCP (the probe is the first socket activity).
    Reordering mutant REDs."""
    src = (REPO_ROOT / "daemon" / "__main__.py").read_text(encoding="utf-8")
    fn_start = src.index("def _boot_db_preflight(")
    fn_end = src.index("def _boot_auth_config_preflight(", fn_start)
    body = src[fn_start:fn_end]
    load = body.index("EnsembleConfig.load_or_create(data_dir)")
    guard = body.index("assert_prod_dsn_sanctioned(ensemble_config)")
    probe = body.index("engine = create_engine(")
    assert load < guard < probe


def test_prod_dsn_guard_ordering_in_api_lifespan():
    """P8 (api face): in the lifespan backstop the prod-DSN guard runs
    after ``EnsembleConfig.load_or_create(data_dir)`` and BEFORE
    ``app.state.ensemble_config`` is set (B2 T3), inside the lifespan's
    pre-yield section. The tier-2 pins (guard < load, call-count 1) stay
    green — distinct names."""
    src = (REPO_ROOT / "daemon" / "api.py").read_text(encoding="utf-8")
    lifespan_start = src.index("async def lifespan(app: FastAPI):")
    lifespan_yield = src.index("\n        yield", lifespan_start)
    load = src.index("EnsembleConfig.load_or_create(data_dir)", lifespan_start)
    guard = src.index(
        "assert_prod_dsn_sanctioned(ensemble_config)", lifespan_start
    )
    state = src.index(
        "app.state.ensemble_config = ensemble_config", lifespan_start
    )
    assert lifespan_start < guard < lifespan_yield
    assert load < guard < state
    assert src.count("assert_prod_dsn_sanctioned(ensemble_config)") == 1


def test_cli_resolve_engine_call_site_ordering():
    """P9 (structural face) | mutant BF-M5: the auth CLI's guard-free
    exemption (B2 §4.7) is CLOSED — ``resolve_engine`` calls the tier-2
    guard BEFORE ``load_or_create`` (a refused CLI writes no poison
    artifact) and the prod-DSN guard after load, BEFORE
    ``create_postgres_engine``. Removing either guard call REDs."""
    src = (REPO_ROOT / "daemon" / "auth" / "cli.py").read_text(
        encoding="utf-8"
    )
    fn_start = src.index("def resolve_engine(")
    fn_end = src.index("def _tables_present(", fn_start)
    body = src[fn_start:fn_end]
    tier2 = body.index("assert_db_resolution_sanctioned(data_dir)")
    load = body.index("EnsembleConfig.load_or_create(data_dir)")
    prod = body.index("assert_prod_dsn_sanctioned(ensemble_config)")
    engine = body.index("create_postgres_engine(ensemble_config)")
    assert tier2 < load < prod < engine


def test_cli_resolve_engine_refuses_and_writes_no_artifact(tmp_path, monkeypatch):
    """P9 (behavioral face) — CLI closure (B2 §4.7). Leg 1: EMPTY data
    dir + ambient prod POSTGRES_* + sandbox identity ⇒ the tier-2 guard
    refuses BEFORE load_or_create, so NO ensemble.json is persisted
    (anti-poison — the old exemption auto-persisted the sanction
    artifact). Leg 2: a pre-existing prod-name tier-1 json (no ambient)
    refuses via the prod-DSN gate. P10 (CLI face): the postgres engine
    factory is NEVER constructed on the refusal path."""
    import sqlalchemy

    from daemon.auth import cli as auth_cli

    sentinel = {"called": False}

    def _no_engine(*a, **kw):
        sentinel["called"] = True
        raise RuntimeError("engine must not be constructed on refuse path")

    monkeypatch.setattr(
        "daemon.repositories.factory.create_postgres_engine",
        _no_engine,
        raising=False,
    )
    monkeypatch.setattr(sqlalchemy, "create_engine", _no_engine, raising=False)
    _patch_b2_identity(monkeypatch, "sandbox", "auto")
    monkeypatch.delenv("ENSEMBLE_DB_AMBIENT_ALLOW", raising=False)

    # Leg 1 — tier-2 refusal pre-persist.
    data_dir = tmp_path / "empty"
    data_dir.mkdir()
    monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_DB", "ensemble_prod")
    with pytest.raises(SystemExit) as exc:
        auth_cli.resolve_engine(config=object())
    assert exc.value.code == 78
    assert not (data_dir / "ensemble.json").exists(), (
        "a refused CLI must persist NO ensemble.json (anti-poison, B2 §4.7)"
    )

    # Leg 2 — tier-1 prod-name refusal (ambient scrubbed; json db=ensemble_prod).
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    prod_dir = tmp_path / "prodjson"
    prod_dir.mkdir()
    (prod_dir / "ensemble.json").write_text(
        '{"database":"postgres","postgres":{"db":"ensemble_prod",'
        '"host":"127.0.0.1","port":5432,"user":"u","password":"p"}}'
    )
    monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(prod_dir))
    with pytest.raises(SystemExit) as exc:
        auth_cli.resolve_engine(config=object())
    assert exc.value.code == 78
    assert sentinel["called"] is False


def test_boot_preflight_refuses_prod_dsn_pre_tcp(tmp_path, monkeypatch, caplog):
    """P10 (preflight face) + B2 T2 acceptance: a tier-1 prod-json boot
    (sandbox identity) through ``_boot_db_preflight`` exits 78 BEFORE the
    connectivity probe constructs any engine (pre-TCP — the sqlalchemy
    sentinel must never fire) with the marker substring
    ``tier=resolved-dsn-targets-prod`` plus the self_env/resolution
    marker fields. Marker-channel note (B2 §4.10): this refusal fires
    BEFORE ``install_root_logging()`` ⇒ root-logger lastResort → stderr
    → journald; the in-process caplog assert stands in for the S5
    stderr/journal assertion channel (per-family channels are pinned,
    not interchangeable)."""
    import sqlalchemy

    from daemon import __main__ as dm

    sentinel = {"called": False}

    def _no_engine(*a, **kw):
        sentinel["called"] = True
        raise RuntimeError("engine must not be constructed on refuse path")

    monkeypatch.setattr(sqlalchemy, "create_engine", _no_engine, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "ensemble.json").write_text(
        '{"database":"postgres","postgres":{"db":"ensemble_prod",'
        '"host":"127.0.0.1","port":5432,"user":"u","password":"p"}}'
    )
    monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(data_dir))
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("ENSEMBLE_DB_AMBIENT_ALLOW", raising=False)
    _patch_b2_identity(monkeypatch, "sandbox", "auto")
    with caplog.at_level("ERROR", logger="daemon.ensemble_config"):
        with pytest.raises(SystemExit) as exc:
            dm._boot_db_preflight()
    assert exc.value.code == 78
    assert sentinel["called"] is False
    assert "tier=resolved-dsn-targets-prod" in caplog.text
    assert "self_env=sandbox" in caplog.text
    assert "resolution=derived" in caplog.text  # source="auto" ⇒ derived label


def test_env_wins_positive_control_scratch_name(tmp_path, monkeypatch):
    """E2 BF-3 positive control: the env-override MECHANISM still works
    for sanctioned names — a tier-1 scratch json + ambient
    ``POSTGRES_DB=ens_e2e_scrub_probe`` resolves THROUGH the real
    ``get_postgres_url`` to the scratch name (credential rotation flows
    preserved, B2 §4.5) and the prod-DSN guard does not fire."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "ensemble.json").write_text(
        '{"database":"postgres","postgres":{"db":"ensemble",'
        '"host":"127.0.0.1","port":5432,"user":"u","password":"p"}}'
    )
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_DB", "ens_e2e_scrub_probe")
    _patch_b2_identity(monkeypatch, "sandbox", "auto")
    config = EnsembleConfig.load_or_create(data_dir)
    assert_prod_dsn_sanctioned(config)  # must not raise
    assert resolved_postgres_db_name(config) == "ens_e2e_scrub_probe"
    assert "ens_e2e_scrub_probe" in config.get_postgres_url()
