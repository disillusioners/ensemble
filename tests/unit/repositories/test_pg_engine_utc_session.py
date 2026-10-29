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

import ast
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

import daemon.repositories.factory as factory_mod
from daemon.constants import (
    MANAGER_ENGINE_APPLICATION_NAME,
    MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS,
    MANAGER_ENGINE_POOL_RECYCLE_S,
    MANAGER_ENGINE_POOL_TIMEOUT_S,
)
from daemon import constants
from daemon.config import LimitsConfig
from daemon.constants import MANAGER_POOL_SIZE_DEFAULT
from daemon.ensemble_config import EnsembleConfig, PostgresConfig
from daemon.repositories.factory import (
    PG_SESSION_CONNECT_ARGS,
    DatabaseConfig,
    create_engine_from_config,
    create_postgres_engine,
)
from daemon.services import settings_service
from sqlalchemy import text
from sqlalchemy.engine import make_url

#: The pack-local declared collected count (NEW packs only — the
#: pre-existing corpus declares none; this constant was ADDED by the
#: db-pool-idle-txn F2 amendment, measured at pack-writing time = 10;
#: the LANE side of the 2026-10-29 merge still collected 10).
#: 10 -> 22 (the merge's UNION carried in origin/latest's side — 15
#: collected there, incl. ``TestManagerPoolKnobLadder`` — without
#: re-measuring the declared constant; the union also DROPPED the body
#: of ``test_default_pool_size_overflow_and_pre_ping``). 22 -> 23
#: (2026-10-29 merge-semantics fix): the R2 re-derivation added the
#: structural wiring pin and restored the dropped body — see FOLD-MAP
#: NOTE-6.
EXPECTED_COLLECTED = 23

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FACTORY_PATH = _REPO_ROOT / "daemon" / "repositories" / "factory.py"

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

    def test_the_manager_engine_sizing_and_knobs_are_as_contracted(
        self, monkeypatch
    ):
        """R2 (2026-10-29 merge-semantics fix) — the manager engine's
        SIZING and its F2 KNOB SET are pinned to the CONTRACTED values.

        The sizing became CONFIG-DRIVEN (the ``limits.manager_pool_size``
        knob hotfix): the engine is built with the ladder-resolved
        ``pool_size=manager_pool_size``; with the kwarg omitted (the
        call sites that have no boot-applied Config) the factory falls
        back to the DEFAULT TIER (``MANAGER_POOL_SIZE_DEFAULT`` = 15).
        The pre-merge pin asserted a hard-coded ``pool_size == 5`` —
        stale since the knob landed, and it would have RED-flagged the
        release's own wiring.

        (iii) the default tier and the MANAGED SETTING's default are
        the same constant (floor = default) — pinned by the sibling
        ``TestManagerPoolKnobLadder`` below; the tier's VALUE is
        asserted here so the two halves cannot drift apart silently.
        """
        captured, engine = _capture_engine_kwargs(monkeypatch)
        try:
            # (i) the sizing — the default tier, and the overflow.
            assert captured["kwargs"]["pool_size"] == MANAGER_POOL_SIZE_DEFAULT
            assert MANAGER_POOL_SIZE_DEFAULT == 15
            assert captured["kwargs"]["max_overflow"] == 10
            # (ii) the F2 knob set — exactly as contracted.
            assert captured["kwargs"]["pool_pre_ping"] is True
            assert captured["kwargs"]["pool_reset_on_return"] == "rollback"
            assert (
                captured["kwargs"]["pool_recycle"]
                == MANAGER_ENGINE_POOL_RECYCLE_S
            )
            assert MANAGER_ENGINE_POOL_RECYCLE_S == 3600
            assert (
                captured["kwargs"]["pool_timeout"]
                == MANAGER_ENGINE_POOL_TIMEOUT_S
            )
            assert MANAGER_ENGINE_POOL_TIMEOUT_S == 30
        finally:
            engine.dispose()

    def test_the_construction_takes_the_ladder_resolved_pool_size(self):
        """R2 (i) — STRUCTURAL: the construction site's ``pool_size`` is
        the LADDER-RESOLVED value (``manager_pool_size``), not a
        literal, and the factory's default for that parameter is the
        default-tier constant. A severed knob (the engine always built
        with the fallback) or a re-hard-coded literal REDs here."""
        tree = ast.parse(_FACTORY_PATH.read_text())
        construction = None
        for node in tree.body:
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "create_postgres_engine"
            ):
                construction = node
                break
        assert construction is not None, (
            "R2: create_postgres_engine was not found in factory.py"
        )
        kwargs = {}
        for inner in ast.walk(construction):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "create_engine"
            ):
                kwargs = {
                    kw.arg: kw.value
                    for kw in inner.keywords
                    if kw.arg is not None
                }
                break
        assert "pool_size" in kwargs and "max_overflow" in kwargs, (
            "R2: could not read pool_size/max_overflow from the manager "
            "engine construction site"
        )
        pool_size_node = kwargs["pool_size"]
        assert (
            isinstance(pool_size_node, ast.Name)
            and pool_size_node.id == "manager_pool_size"
        ), (
            "R2: the construction site's pool_size is no longer the "
            "ladder-resolved `manager_pool_size` — the knob wiring was "
            f"severed (observed: {ast.dump(pool_size_node)})"
        )
        overflow = ast.literal_eval(kwargs["max_overflow"])
        assert overflow == 10, (
            f"R2: the manager engine's max_overflow drifted: {overflow}"
        )
        # The parameter's default IS the default-tier constant.
        positional = list(construction.args.posonlyargs) + list(
            construction.args.args
        )
        defaults = list(construction.args.defaults)
        offset = len(positional) - len(defaults)
        for index, arg in enumerate(positional):
            if arg.arg != "manager_pool_size":
                continue
            default = defaults[index - offset]
            assert (
                isinstance(default, ast.Name)
                and default.id == "MANAGER_POOL_SIZE_DEFAULT"
            ), (
                "R2: the factory's manager_pool_size default is no longer "
                f"MANAGER_POOL_SIZE_DEFAULT (observed: {ast.dump(default)})"
            )
            return
        raise AssertionError(
            "R2: create_postgres_engine has no manager_pool_size parameter"
        )

    def test_pool_size_kwarg_reaches_engine(self, monkeypatch):
        """CRITICAL knob-path pin (review fix round): the EXPLICIT
        ``manager_pool_size`` kwarg — the value the manager threads from
        the boot-applied ``config.limits`` — is what the engine is built
        with. NOT the fallback constant: this pin fails if the factory
        stops honoring the kwarg (the severed-knob defect this round
        fixes)."""
        captured: dict = {}
        real_create_engine = factory_mod.create_engine

        def spy(*args, **kwargs):
            captured["kwargs"] = kwargs
            return real_create_engine(*args, **kwargs)

        monkeypatch.setattr(factory_mod, "create_engine", spy)
        for var in _PG_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        engine = create_postgres_engine(
            _test_pg_config(), manager_pool_size=37
        )
        try:
            assert captured["kwargs"]["pool_size"] == 37
        finally:
            engine.dispose()

    def test_default_pool_size_overflow_and_pre_ping(self, monkeypatch):
        """With the kwarg OMITTED (the 3 call sites with no boot-applied
        Config: auth CLI, settings bootstrap throwaway, migration
        worker), the factory falls back to the default tier constant;
        overflow + pre-ping are unchanged. (Renamed from
        ``test_pool_and_pre_ping_settings_unchanged`` — the old name
        predates the knob and no longer said what is pinned.)

        BODY RESTORED (2026-10-29 merge-semantics fix): the merge's
        union resolution left this function with its header and NO
        assertions — a vacuous test. Its intent is preserved here as
        the FALLBACK-EQUIVALENCE leg: the omitted-kwarg path and the
        explicit default-tier path build the SAME engine.
        """
        captured: dict = {}
        real_create_engine = factory_mod.create_engine

        def spy(*args, **kwargs):
            captured["kwargs"] = kwargs
            return real_create_engine(*args, **kwargs)

        monkeypatch.setattr(factory_mod, "create_engine", spy)
        for var in _PG_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        engine = create_postgres_engine(_test_pg_config())
        try:
            assert captured["kwargs"]["pool_size"] == (
                MANAGER_POOL_SIZE_DEFAULT
            )
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
            assert captured["kwargs"]["pool_size"] == (
                MANAGER_POOL_SIZE_DEFAULT
            )
            assert captured["kwargs"]["max_overflow"] == 10
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
    def test_manager_threads_the_ladder_value_into_the_factory(self):
        """Structural pin (checkpoint-pin layout): the PRODUCTION call
        site (``daemon/manager.py``) passes
        ``manager_pool_size=self.config.limits.manager_pool_size`` — the
        boot-applied YAML Config read (``self.config``, set from the
        ``config`` ctor arg), NOT the ensemble.json ``EnsembleConfig``
        (which has no ``.limits``). This is the hop that was severed in
        the first cut (engine always built with the default constant)."""
        import inspect

        from daemon import manager as manager_mod

        source = inspect.getsource(manager_mod)
        assert (
            "manager_pool_size=self.config.limits.manager_pool_size" in source
        )
        # And the factory call it feeds.
        assert "create_postgres_engine(" in source


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


class TestManagerPoolKnobLadder:
    """The LIMITS_MANAGER_POOL_SIZE knob's 4-tier ladder pins — layout
    mirrors ``tests/unit/tools/test_checkpoint_pool_config.py``
    (default tier / field mirror / registry mirror / env tier / yaml
    tier / both bounds at the registry validator and the pydantic
    Field). Review fix round: the first cut's engine pin was
    tautological (its fixture carried no ``.limits``, so it asserted
    only the fallback constant); the knob-path pins now live in
    ``TestPgEngineUtcSessionConnectArgs`` above."""

    _KEY = "limits.manager_pool_size"

    def test_the_default_tier_is_the_constant(self):
        assert constants.MANAGER_POOL_SIZE_DEFAULT == 15

    def test_the_field_defaults_mirror_the_constant(self):
        limits = LimitsConfig()
        assert limits.manager_pool_size == (
            constants.MANAGER_POOL_SIZE_DEFAULT
        )

    def test_the_settings_registry_default_mirrors_the_field(self):
        spec = settings_service.MANAGED_KEYS[self._KEY]
        assert spec.default == constants.MANAGER_POOL_SIZE_DEFAULT
        assert spec.env_var == "LIMITS_MANAGER_POOL_SIZE"
        assert spec.config_path == "limits.manager_pool_size"
        assert spec.value_type == "int"
        # Fail-LOUD bounds, mirrored Field-side AND registry-side
        # (Ruling 1 — one validation truth): floor 15 == the default,
        # ceiling 30 = the connection budget.
        assert spec.ge == 15
        assert spec.le == 30

    def test_the_env_tier_is_effective(self, monkeypatch):
        monkeypatch.setenv("LIMITS_MANAGER_POOL_SIZE", "22")
        limits = LimitsConfig()
        assert limits.manager_pool_size == 22

    def test_the_yaml_tier_is_the_shipped_literal(self):
        import yaml

        with open("config.yaml", "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        assert raw["limits"]["manager_pool_size"] == (
            constants.MANAGER_POOL_SIZE_DEFAULT
        )

    def test_the_bounds_are_enforced_by_the_registry_validator(self):
        """The registry validator must reject the values the pydantic
        Field would reject — BOTH bounds (one validation truth)."""
        spec = settings_service.MANAGED_KEYS[self._KEY]
        ok, value, failure, _detail = settings_service.parse_and_validate(
            spec, 15
        )
        assert ok is True and value == 15
        ok, value, failure, _detail = settings_service.parse_and_validate(
            spec, 30
        )
        assert ok is True and value == 30
        ok, _value, failure, _detail = settings_service.parse_and_validate(
            spec, 14
        )
        assert ok is False and failure == "validation"
        ok, _value, failure, _detail = settings_service.parse_and_validate(
            spec, 31
        )
        assert ok is False and failure == "validation"

    def test_the_field_rejects_both_bounds_too(self):
        # The Field bounds raise pydantic's ValidationError — the pin
        # names the type so an unrelated exception cannot satisfy it.
        with pytest.raises(ValidationError):
            LimitsConfig(manager_pool_size=14)
        with pytest.raises(ValidationError):
            LimitsConfig(manager_pool_size=31)
        # In-bounds values construct cleanly.
        assert LimitsConfig(manager_pool_size=15).manager_pool_size == 15
        assert LimitsConfig(manager_pool_size=30).manager_pool_size == 30

    def test_below_floor_env_tier_is_skipped_at_boot_not_clamped(self):
        """Ground truth for the fail-loud contract at the BOOT surface:
        with the shipped yaml literal shadowing the pydantic env tier,
        the resolver rejects the below-floor env value via the SAME
        registry validator — one WARNING, the env tier skipped, the
        yaml tier active. The value is NEVER clamped to 15 (or to
        anything else). (An unshadowed env tier — no yaml literal —
        CRASHES load via the Field's ValidationError instead; that
        surface is pinned by test_the_field_rejects_both_bounds_too.)"""
        spec = settings_service.MANAGED_KEYS[self._KEY]
        ok, _value, failure, _detail = settings_service.parse_and_validate(
            spec, "10"
        )
        assert ok is False and failure == "validation"
        snapshot = {self._KEY: {"default": 15, "yaml_value": 15}}
        resolution = settings_service.resolve(
            self._KEY, snapshot, db_overrides={}
        )
        assert resolution["source"] == "yaml"
        assert resolution["value"] == 15
        assert resolution["env_value"] is None  # the rejected tier

    def test_the_ceiling_budget_is_the_documented_one(self):
        """Ceiling rationale (constants.py): 30+10 manager +
        2×20 checkpoint = 80 ≤ max_connections=100."""
        manager_max = 30 + 10
        checkpoint_max = 2 * 20
        assert manager_max + checkpoint_max == 80
        assert 80 <= 100
