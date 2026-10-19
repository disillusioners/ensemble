"""settings-worker-slots Phase 2 — the ``settings`` tool category.

Tool-lane tests for ``daemon/tools/settings_tools.py``
(``settings_get`` / ``settings_set`` / ``settings_clear``), the
agent-facing surface of the honest never-live contract (D5):

* **Honest contract matrix** — get-shape (per-key fields incl.
  ``divergent``), set-shape with ``live: false`` +
  ``applies_at: "next daemon restart"`` + ``running_value_still``
  divergence, clear idempotency.
* **Registry-gate rejection** — unknown keys refused on read AND
  write with the ``"ERROR: unknown managed setting key"`` string;
  invalid values refused with the authored validator detail.
* **Unwired-service envelope** — the pinned
  ``{"error": "RuntimeError"}`` via the council-B3 option-(b)
  seam (the router DI global ``_settings_service`` read directly;
  NOT the HTTPException(503) getter).
* **Audit line + actor attribution** — one
  ``[Settings][tool]`` INFO line per write/clear (O2 parity with
  the router-side lines), ``<redacted>`` for a hypothetical
  sensitive key, agent-id attribution (Cardinal #2).
* **Allow-list strip behavior** — a ``settings``-allowed agent
  resolves exactly the three tools through the REAL
  ``create_instance_tools()`` path; non-allow / empty-allow /
  no-tools-config agents resolve ZERO (R-SR16 privileged
  default-deny). NOTE: the ari/leader meta.json allow entries
  landed 2026-10-18 (P3) — this file intentionally does NOT
  assert them.

Service harness: a REAL ``SettingsService`` on an in-memory
SQLite engine (the ``app_settings`` table only) + a hand-built
pristine snapshot — the same shapes the boot path produces.

Fence discipline (2026-10-12 critical note — zero tolerance): no
HOME-derived or install-dir paths are resolved anywhere in this
file; the only filesystem touch is the tmp_path-staged synthetic
agent tree under the system temp dir.

Env discipline (W2): the three ``LIMITS_*`` managed-key env vars
are explicitly deleted per test — the env tier is never ambient.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

import daemon.registry as dr
from daemon.models.app_settings import AppSetting
from daemon.registry import AgentRegistry
from daemon.services import settings_service as ss
from daemon.services.settings_service import (
    MANAGED_KEYS,
    ManagedKeySpec,
    SettingsService,
)
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)

# Repo root: tests/unit/tools/test_settings_tools.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]

SETTINGS_CATEGORY = "settings"
SETTINGS_MODULE = "daemon.tools.settings_tools"
SETTINGS_TOOL_NAMES: frozenset[str] = frozenset({
    "settings_get",
    "settings_set",
    "settings_clear",
})

KEY = "limits.worker_pool_size"
OTHER_KEY = "limits.chat_worker_pool_size"


# ────────────────────────────────────────────────────────────────────────────
# Fixtures — router DI global (B3 option-(b) seam) + services SSOT snapshot
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _settings_env_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """W2 env discipline: no managed-key env var is ambient."""
    for spec in MANAGED_KEYS.values():
        monkeypatch.delenv(spec.env_var, raising=False)


@pytest.fixture(autouse=True)
def _restore_router_di(monkeypatch: pytest.MonkeyPatch):
    """Save/restore the router-owned DI global the tools read.

    The tool seam is ``daemon.routers.settings._settings_service``
    (council B3 option (b)) — every test pins it explicitly via
    monkeypatch so the real daemon state can never leak in, and
    monkeypatch restores the original value after each test.
    """
    from daemon.routers import settings as router_settings

    monkeypatch.setattr(router_settings, "_settings_service", None)
    return router_settings


@pytest.fixture(autouse=True)
def _restore_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the services-module SSOT snapshot (F6: tools read the
    services module-global, so tests pin THAT global)."""
    monkeypatch.setattr(
        ss,
        "_resolution_snapshot",
        _hand_snapshot(),
    )


def _hand_snapshot() -> dict[str, dict[str, Any]]:
    """Hand-built pristine snapshot — the shape
    ``apply_db_settings_overrides`` produces at boot, with the
    registry defaults as the boot-applied running truth."""
    return {
        "limits.llm_concurrency": {
            "default": 10,
            "env_value": None,
            "yaml_value": None,
            "applied_value": 10,
        },
        "limits.worker_pool_size": {
            "default": MANAGED_KEYS["limits.worker_pool_size"].default,
            "env_value": None,
            "yaml_value": None,
            "applied_value": MANAGED_KEYS["limits.worker_pool_size"].default,
        },
        "limits.chat_worker_pool_size": {
            "default": MANAGED_KEYS["limits.chat_worker_pool_size"].default,
            "env_value": None,
            "yaml_value": None,
            "applied_value": MANAGED_KEYS["limits.chat_worker_pool_size"].default,
        },
    }


@pytest.fixture
def wired_service(_restore_router_di) -> SettingsService:
    """A REAL SettingsService on an in-memory SQLite engine, wired
    into the router DI global (the exact symbol the tools read)."""
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine, tables=[AppSetting.__table__])
    service = SettingsService(engine=engine)
    _restore_router_di._settings_service = service
    return service


def _tool_by_name(name: str, manager: Any = None) -> Any:
    """Build one settings tool by name through the factory."""
    from daemon.tools.settings_tools import create_settings_tools

    tools = create_settings_tools(manager, "inst-test-0001")
    for tool_obj in tools:
        if tool_obj.name == name:
            return tool_obj
    raise AssertionError(f"tool {name!r} not found")


def _invoke(tool_obj: Any, **kwargs: Any) -> str:
    """Invoke a langchain StructuredTool with kwargs."""
    return tool_obj.invoke(kwargs)


# ────────────────────────────────────────────────────────────────────────────
# Static registration checklist (the compact settings-category pin)
# ────────────────────────────────────────────────────────────────────────────


class TestStaticRegistrationChecklist:
    """The four registry touch-points for the ``settings``
    category (phase2-plan.md task 3, F2-corrected labels)."""

    def test_ast_discovery_finds_all_three(self) -> None:
        discovered = discover_source_only_tool_names()
        assert SETTINGS_TOOL_NAMES - discovered == set()

    def test_category_modules_entry(self) -> None:
        assert CATEGORY_MODULES.get(SETTINGS_CATEGORY) == SETTINGS_MODULE

    def test_dynamic_tool_names_contains_all_three(self) -> None:
        assert SETTINGS_TOOL_NAMES - set(DYNAMIC_TOOL_NAMES) == set()

    def test_known_tool_names_contains_all_three(self) -> None:
        assert SETTINGS_TOOL_NAMES - set(KNOWN_TOOL_NAMES) == set()

    def test_privileged_category_contains_settings(self) -> None:
        assert SETTINGS_CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_factory_tools_carry_category_attrs(self) -> None:
        from daemon.tools.settings_tools import create_settings_tools

        tools = create_settings_tools(None, "inst-test-0001")
        assert [t.name for t in tools] == [
            "settings_get",
            "settings_set",
            "settings_clear",
        ]
        for tool_obj in tools:
            assert tool_obj._tool_category == SETTINGS_CATEGORY
            assert tool_obj._tool_category_first_party is True
            assert hasattr(tool_obj, "_full_doc_")

    def test_construction_seam_extends_instance_tools(self) -> None:
        """The decorator-only-is-invisible guard: the construction
        extend must exist in ``daemon/tools/instance.py`` (the
        secrets-block sibling)."""
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        assert "from .settings_tools import create_settings_tools" in source
        assert "create_settings_tools(" in source
        assert "tools.extend(settings_tool_list)" in source


# ────────────────────────────────────────────────────────────────────────────
# Honest contract matrix (D5)
# ────────────────────────────────────────────────────────────────────────────


ENTRY_FIELDS = {
    "key",
    "value",
    "source",
    "default",
    "env_value",
    "yaml_value",
    "db_override",
    "requires_restart",
    "sensitive",
    "divergent",
}


class TestHonestGet:
    def test_get_all_shape(self, wired_service: SettingsService) -> None:
        tool_obj = _tool_by_name("settings_get")
        payload = json.loads(_invoke(tool_obj))
        entries = payload["settings"]
        assert [e["key"] for e in entries] == list(MANAGED_KEYS)
        for entry in entries:
            assert ENTRY_FIELDS <= set(entry), (
                f"entry for {entry['key']!r} missing honest-contract "
                f"fields: {ENTRY_FIELDS - set(entry)}"
            )
            spec = MANAGED_KEYS[entry["key"]]
            assert entry["value"] == spec.default  # boot-applied truth
            assert entry["source"] == "default"
            assert entry["requires_restart"] is True
            assert entry["divergent"] is False
        assert payload["restart_required"] is False

    def test_get_single_key(self, wired_service: SettingsService) -> None:
        tool_obj = _tool_by_name("settings_get")
        entry = json.loads(_invoke(tool_obj, key=KEY))
        assert entry["key"] == KEY
        assert ENTRY_FIELDS <= set(entry)
        assert entry["source"] == "default"
        assert entry["divergent"] is False

    def test_get_reports_pending_override_as_divergent(
        self, wired_service: SettingsService
    ) -> None:
        """The honest-restart core: after a DB write the running
        value (boot-applied) diverges from the resolved db tier —
        the tool says so WITHOUT claiming liveness."""
        wired_service.set_override(KEY, 8, updated_by="tester")
        tool_obj = _tool_by_name("settings_get")
        entry = json.loads(_invoke(tool_obj, key=KEY))
        assert entry["source"] == "db"
        assert entry["db_override"] == 8
        assert entry["value"] != 8  # boot-applied truth unchanged
        assert entry["divergent"] is True
        listing = json.loads(_invoke(tool_obj))
        assert listing["restart_required"] is True


class TestHonestSet:
    def test_set_response_is_never_live(
        self, wired_service: SettingsService
    ) -> None:
        """The D5 binding shape: live:false + applies_at +
        running_value_still divergence, stated explicitly."""
        tool_obj = _tool_by_name("settings_set")
        old_default = MANAGED_KEYS[KEY].default
        response = json.loads(_invoke(tool_obj, key=KEY, value=8))
        assert response["key"] == KEY
        assert response["written"] == 8
        assert response["live"] is False
        assert response["applies_at"] == "next daemon restart"
        assert response["requires_restart"] is True
        assert response["running_value_still"] == old_default
        assert response["divergent"] is True
        assert response["source"] == "db"
        assert "restart" in response["note"].lower()

    def test_set_persists_through_the_service(
        self, wired_service: SettingsService
    ) -> None:
        tool_obj = _tool_by_name("settings_set")
        _invoke(tool_obj, key=KEY, value=8)
        overrides = wired_service.get_overrides()
        assert overrides[KEY] == 8

    def test_set_row_carries_updated_by(
        self, wired_service: SettingsService
    ) -> None:
        tool_obj = _tool_by_name("settings_set")
        _invoke(tool_obj, key=KEY, value=8)
        with Session(wired_service._engine) as session:
            row = session.get(AppSetting, KEY)
        assert row is not None
        # No manager → the instance id is the attribution fallback.
        assert row.updated_by == "inst-test-0001"

    def test_set_validation_error_is_error_text(
        self, wired_service: SettingsService
    ) -> None:
        tool_obj = _tool_by_name("settings_set")
        # ge=1 violated
        out = _invoke(tool_obj, key=KEY, value=0)
        assert out.startswith("ERROR: ")
        assert "validation" in out or "parse" in out
        # nothing was written
        assert KEY not in wired_service.get_overrides()
        # wrong type (str that is not int-parsable)
        out = _invoke(tool_obj, key=KEY, value="abc")
        assert out.startswith("ERROR: ")
        assert KEY not in wired_service.get_overrides()

    def test_set_unknown_key_registry_gate(
        self, wired_service: SettingsService
    ) -> None:
        tool_obj = _tool_by_name("settings_set")
        out = _invoke(tool_obj, key="not.a.key", value=1)
        assert out.startswith("ERROR: unknown managed setting key")

    def test_set_to_default_leaves_db_row_clear_restores_ladder(
        self, wired_service: SettingsService
    ) -> None:
        """The reason the third tool exists: set-to-default leaves
        a db-tier row; only clear restores env/yaml/default."""
        default = MANAGED_KEYS[KEY].default
        set_tool = _tool_by_name("settings_set")
        _invoke(set_tool, key=KEY, value=default)
        assert json.loads(_invoke(_tool_by_name("settings_get"), key=KEY))[
            "source"
        ] == "db"
        clear_tool = _tool_by_name("settings_clear")
        _invoke(clear_tool, key=KEY)
        assert json.loads(_invoke(_tool_by_name("settings_get"), key=KEY))[
            "source"
        ] == "default"


class TestHonestClear:
    def test_clear_idempotent(self, wired_service: SettingsService) -> None:
        clear_tool = _tool_by_name("settings_clear")
        # No row yet — cleared: false (idempotent, still honest).
        first = json.loads(_invoke(clear_tool, key=KEY))
        assert first["cleared"] is False
        assert first["live"] is False
        assert first["applies_at"] == "next daemon restart"
        # Write + clear — cleared: true, ladder falls back.
        _invoke(_tool_by_name("settings_set"), key=KEY, value=8)
        second = json.loads(_invoke(clear_tool, key=KEY))
        assert second["cleared"] is True
        assert second["source"] == "default"
        assert second["live"] is False
        # Clear again — still honest, still false.
        third = json.loads(_invoke(clear_tool, key=KEY))
        assert third["cleared"] is False

    def test_clear_unknown_key_registry_gate(
        self, wired_service: SettingsService
    ) -> None:
        out = _invoke(_tool_by_name("settings_clear"), key="not.a.key")
        assert out.startswith("ERROR: unknown managed setting key")

    def test_clear_honest_shape_fields(
        self, wired_service: SettingsService
    ) -> None:
        _invoke(_tool_by_name("settings_set"), key=KEY, value=8)
        response = json.loads(
            _invoke(_tool_by_name("settings_clear"), key=KEY)
        )
        for field in (
            "key",
            "written",
            "cleared",
            "updated_by",
            "requires_restart",
            "source",
            "applies_at",
            "running_value_still",
            "divergent",
            "live",
            "note",
        ):
            assert field in response, f"clear response missing {field!r}"
        assert response["live"] is False
        assert response["applies_at"] == "next daemon restart"


# ────────────────────────────────────────────────────────────────────────────
# Unwired-service envelope (council B3 option (b) — the pinned
# {"error": "RuntimeError"} shape, NOT HTTPException)
# ────────────────────────────────────────────────────────────────────────────


class TestUnwiredServiceEnvelope:
    def test_all_three_tools_yield_runtimeerror_envelope(
        self, _restore_router_di
    ) -> None:
        """The router DI global is None → the tool's OWN
        RuntimeError → class-name-only JSON envelope. The
        HTTPException(503) getter is NEVER called (option (c)
        rejected — its class name would render "HTTPException")."""
        assert _restore_router_di._settings_service is None
        cases: list[tuple[str, dict[str, Any]]] = [
            ("settings_get", {}),  # list-all path
            ("settings_get", {"key": KEY}),  # single-key path
            ("settings_set", {"key": KEY, "value": 8}),
            ("settings_clear", {"key": KEY}),
        ]
        for name, kwargs in cases:
            tool_obj = _tool_by_name(name)
            response = _invoke(tool_obj, **kwargs)
            payload = json.loads(response)
            assert payload == {"error": "RuntimeError"}, (
                f"{name}({kwargs}): expected the pinned RuntimeError "
                f"envelope, got {response!r}"
            )

    def test_envelope_is_not_httpexception(self, _restore_router_di) -> None:
        """Negative guard for the option-(c) rejection: the envelope
        class name must be RuntimeError, never HTTPException."""
        tool_obj = _tool_by_name("settings_set")
        payload = json.loads(_invoke(tool_obj, key=KEY, value=8))
        assert payload["error"] == "RuntimeError"
        assert "HTTPException" not in json.dumps(payload)

    def test_unexpected_exception_is_class_name_only(
        self, wired_service: SettingsService
    ) -> None:
        """PB-F1 playbook: a service that explodes with a
        canary-carrying exception must NOT leak the canary —
        class-name-only envelope."""

        class FakeDBError(Exception):
            pass

        canary = "postgresql://user:CANARY@host/db"

        def _boom(*args: Any, **kwargs: Any) -> None:
            raise FakeDBError(f"connection refused: {canary}")

        wired_service.get_overrides = _boom  # type: ignore[method-assign]
        tool_obj = _tool_by_name("settings_get")
        response = _invoke(tool_obj)
        assert canary not in response
        assert json.loads(response) == {"error": "FakeDBError"}


# ────────────────────────────────────────────────────────────────────────────
# Audit line + actor attribution (task 2 — O2 parity, Cardinal #2)
# ────────────────────────────────────────────────────────────────────────────


class _InstanceRow:
    """Minimal instance-row stand-in for actor resolution."""

    agent_id = "ari"


class TestAuditAndAttribution:
    def test_set_audit_line_shape_and_actor(
        self,
        wired_service: SettingsService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        manager = MagicMock(name="InstanceManager")
        # MagicMock auto-creates EVERY attribute — disable the
        # _resolve_actor shortcut so attribution exercises the
        # canonical instance-repository path.
        manager._resolve_agent_id_for_actor = None
        manager._instance_repository.get.return_value = _InstanceRow()
        tool_obj = _tool_by_name("settings_set", manager)
        with caplog.at_level("INFO", logger="daemon.tools.settings_tools"):
            response = json.loads(_invoke(tool_obj, key=KEY, value=8))
        # Actor attribution: the agent id (Cardinal #2), never the
        # HTTP subject, never the instance id.
        assert response["updated_by"] == "ari"
        audit_lines = [
            r.getMessage()
            for r in caplog.records
            if "[Settings][tool] db override set" in r.getMessage()
        ]
        assert len(audit_lines) == 1
        assert (
            f"key={KEY} value=8 agent=ari instance=inst-test-0001"
            in audit_lines[0]
        )

    def test_clear_audit_line(
        self,
        wired_service: SettingsService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        manager = MagicMock(name="InstanceManager")
        # MagicMock auto-creates EVERY attribute — disable the
        # _resolve_actor shortcut so attribution exercises the
        # canonical instance-repository path.
        manager._resolve_agent_id_for_actor = None
        manager._instance_repository.get.return_value = _InstanceRow()
        tool_obj = _tool_by_name("settings_clear", manager)
        with caplog.at_level("INFO", logger="daemon.tools.settings_tools"):
            _invoke(tool_obj, key=KEY)
        audit_lines = [
            r.getMessage()
            for r in caplog.records
            if "[Settings][tool] db override cleared" in r.getMessage()
        ]
        assert len(audit_lines) == 1
        assert f"key={KEY} agent=ari instance=inst-test-0001" in audit_lines[0]

    def test_sensitive_key_audit_redaction(
        self,
        wired_service: SettingsService,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Redaction verified for a HYPOTHETICAL sensitive key: the
        audit line renders ``value=<redacted>`` (the sensitive
        tripwire); the response still reports honestly to the
        caller who supplied the value."""
        secret_key = "test.sensitive_knob"
        monkeypatch.setitem(
            MANAGED_KEYS,
            secret_key,
            ManagedKeySpec(
                key=secret_key,
                config_path="test.sensitive_knob",
                env_var="TEST_SENSITIVE_KNOB",
                value_type="int",
                default=1,
                ge=1,
                requires_restart=True,
                sensitive=True,
                description="hypothetical sensitive key (test-only)",
            ),
        )
        monkeypatch.setitem(
            ss._resolution_snapshot,  # type: ignore[arg-type]
            secret_key,
            {"default": 1, "env_value": None, "yaml_value": None,
             "applied_value": 1},
        )
        manager = MagicMock(name="InstanceManager")
        # MagicMock auto-creates EVERY attribute — disable the
        # _resolve_actor shortcut so attribution exercises the
        # canonical instance-repository path.
        manager._resolve_agent_id_for_actor = None
        manager._instance_repository.get.return_value = _InstanceRow()
        tool_obj = _tool_by_name("settings_set", manager)
        with caplog.at_level("INFO", logger="daemon.tools.settings_tools"):
            response = json.loads(
                _invoke(tool_obj, key=secret_key, value=9)
            )
        audit_lines = [
            r.getMessage()
            for r in caplog.records
            if "[Settings][tool] db override set" in r.getMessage()
        ]
        assert len(audit_lines) == 1
        assert "value='<redacted>'" in audit_lines[0]
        assert "value=9" not in audit_lines[0]
        # The response entry nulls the sensitive-key tier details…
        assert response["key"] == secret_key
        assert response["written"] == 9  # caller supplied it — echo is honest
        # …and the follow-up GET nulls them too.
        get_entry = json.loads(_invoke(_tool_by_name("settings_get"), key=secret_key))
        assert get_entry["sensitive"] is True
        assert get_entry["db_override"] is None
        assert get_entry["env_value"] is None
        assert get_entry["yaml_value"] is None

    def test_actor_falls_back_to_instance_id(
        self, wired_service: SettingsService
    ) -> None:
        """No manager → the instance id is the attribution fallback
        (still Cardinal-#2-compliant provenance)."""
        tool_obj = _tool_by_name("settings_set", None)
        response = json.loads(_invoke(tool_obj, key=KEY, value=8))
        assert response["updated_by"] == "inst-test-0001"


# ────────────────────────────────────────────────────────────────────────────
# Allow-list / privileged-category gates (R-SR16) — synthetic agents
# through the REAL create_instance_tools() path. The ari/leader
# meta.json entries landed 2026-10-18 (P3) and are deliberately
# NOT asserted here.
# ────────────────────────────────────────────────────────────────────────────


def _stage_synthetic_agent(
    tmp_path: Path, agent_id: str, tools_cfg: dict | None
) -> Path:
    """Stage a synthetic agent dir under tmp_path by cloning
    watcher's real meta.json shape with a replaced tools config."""
    real_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    real_meta.pop("watchover", None)
    real_meta["id"] = agent_id
    real_meta["name"] = agent_id.title()
    real_meta["team_members"] = []
    real_meta["innate_skills"] = []
    real_meta["tools"] = tools_cfg
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "meta.json").write_text(json.dumps(real_meta), encoding="utf-8")
    return agents_dir


@pytest.fixture
def registry_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _install(agents_dir: Path) -> AgentRegistry:
        registry = AgentRegistry(agents_dir)
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)
        return registry

    return _install


def _build_instance_tools(agent_id: str) -> dict[str, object]:
    from daemon.tools.instance import create_instance_tools

    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    manager._secret_service = None
    manager._instance_repository = None
    tools = create_instance_tools(manager, f"inst-{agent_id}", agent_id)
    return {getattr(t, "name", "?"): t for t in tools}


def _settings_names(by_name: dict) -> set:
    return set(by_name) & SETTINGS_TOOL_NAMES


class TestAllowListGates:
    def test_allow_entry_resolves_all_three(
        self, tmp_path: Path, registry_for
    ) -> None:
        agents_dir = _stage_synthetic_agent(
            tmp_path, "syn-settings-allow", {"allow": ["bash", SETTINGS_CATEGORY]}
        )
        registry_for(agents_dir)
        by_name = _build_instance_tools("syn-settings-allow")
        assert _settings_names(by_name) == SETTINGS_TOOL_NAMES

    def test_allow_single_tool_name_grants_only_that_tool(
        self, tmp_path: Path, registry_for
    ) -> None:
        _stage_synthetic_agent(
            tmp_path,
            "syn-settings-one",
            {"allow": ["bash", "settings_get"]},
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-settings-one")
        assert _settings_names(by_name) == {"settings_get"}

    def test_without_allow_entry_resolves_none(
        self, tmp_path: Path, registry_for
    ) -> None:
        _stage_synthetic_agent(
            tmp_path,
            "syn-settings-none",
            {"allow": ["bash", "filesystem", "time", "help"]},
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-settings-none")
        assert _settings_names(by_name) == set()

    def test_empty_allow_default_deny_rsr16(
        self, tmp_path: Path, registry_for
    ) -> None:
        """R-SR16: empty-allow agents NEVER inherit a privileged
        category — settings mutates daemon-global runtime state."""
        _stage_synthetic_agent(tmp_path, "syn-settings-empty", {"allow": []})
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-settings-empty")
        assert _settings_names(by_name) == set()
        # …but ordinary categories still resolve.
        assert "bash" in by_name or "time" in by_name

    def test_no_tools_config_at_all_default_deny_rsr16(
        self, tmp_path: Path, registry_for
    ) -> None:
        """The other default-allow path: tools config entirely
        ABSENT — the strip must apply there too."""
        _stage_synthetic_agent(tmp_path, "syn-settings-null", None)
        meta_path = tmp_path / "agents" / "syn-settings-null" / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["tools"] = None
        meta_path.write_text(json.dumps(meta))
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-settings-null")
        assert _settings_names(by_name) == set()

    def test_deny_wins_over_allow(
        self, tmp_path: Path, registry_for
    ) -> None:
        _stage_synthetic_agent(
            tmp_path,
            "syn-settings-deny",
            {"allow": [SETTINGS_CATEGORY], "deny": [SETTINGS_CATEGORY]},
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-settings-deny")
        assert _settings_names(by_name) == set()
