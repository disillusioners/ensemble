"""P5 / P5c / P6 / P6b + P2-G / P2-H / P2-I — the loud-skip observability
and the manager-site heal/deactivation contracts (lane ``plane-pm-issue-tools``
Phase 3).

The manager's ``_bootstrap_builtin_servers`` is driven against a fake
``mcp_servers`` repository and a fake builtin registry (only the plane
definition), with ``caplog`` capturing the log surface:

* **P5** — the skip is at WARNING level and names the server, the AGENTS
  whose ``mcp_full_access`` references it, and the missing env var NAMES;
* **P5c / P3-C** — the typed reason goes in VERBATIM; the three causes
  (deliberately disabled / unexpectedly unavailable / workspace refused)
  are distinguishable; a refusal is NEVER a missing-env-var case;
  *deliberately disabled* produces NO warning;
* **P6 / P6b / P3-E** — the registry-vs-row cross-check fires for an
  agent declaring ``mcp_full_access`` with no registered row, is
  POINTER-ONLY (no reason re-interpolation), and a P2-G-deactivated row is
  *policy-refused* (still WARNING);
* **P2-G** — a pre-existing ACTIVE plane row is deactivated on a refusal,
  and the deactivation path never surfaces ``config.headers``;
* **P2-H** — a refusal boot leaves the row's ``config_schema_version``
  unchanged;
* **P2-I** — a plane row whose persisted slug differs from the resolved
  slug heals even without a schema-version drift.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import daemon.manager as manager_module
from daemon.manager import InstanceManager
from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
from daemon.plane_workspace import current_plane_workspace, reset_resolution_cache
from daemon.registry import AgentMetadata

MCP_URL = "https://mcp.example.test/plane/http/api-key/mcp"


# ── Fakes ────────────────────────────────────────────────────────────────────


@dataclass
class _FakeRow:
    id: str = "row-plane"
    name: str = "plane"
    is_builtin: bool = True
    is_active: bool = True
    config: dict = field(default_factory=dict)
    config_schema_version: str = "2"


class _FakeRepo:
    def __init__(self, rows: list[_FakeRow] | None = None):
        self.rows: dict[str, _FakeRow] = {r.name: r for r in (rows or [])}
        self.updates: list[dict] = []

    def get_mcp_server_by_name(self, name: str):
        return self.rows.get(name)

    def list_mcp_servers(self, **kwargs):
        return [r for r in self.rows.values() if r.is_active]

    def create_mcp_server(self, **kwargs):
        name = kwargs["name"]
        row = _FakeRow(
            name=name,
            config=kwargs.get("config", {}),
            config_schema_version=kwargs.get("config_schema_version", "3"),
        )
        self.rows[name] = row
        return row

    def update_mcp_server(self, row_id, **kwargs):
        self.updates.append({"id": row_id, **kwargs})
        for row in self.rows.values():
            if row.id == row_id:
                if "is_active" in kwargs:
                    row.is_active = kwargs["is_active"]
                if "config" in kwargs:
                    row.config = kwargs["config"]
                if "config_schema_version" in kwargs:
                    row.config_schema_version = kwargs["config_schema_version"]
        return next(r for r in self.rows.values() if r.id == row_id)


class _FakeBuiltinRegistry:
    def __init__(self, definitions):
        self._definitions = {d.name: d for d in definitions}

    def get_all(self):
        return list(self._definitions.values())

    def get_by_name(self, name):
        return self._definitions.get(name)


def _agent_meta(agent_id: str, mcp_full_access: list[str] | None = None):
    return AgentMetadata(
        id=agent_id,
        name=agent_id,
        description=f"Synthetic {agent_id}",
        path=Path(f"/tmp/{agent_id}"),
        team_members=[],
        mcp_full_access=mcp_full_access or [],
    )


class _FakeAgentRegistry:
    def __init__(self, metas):
        self._metas = metas

    def list_all(self):
        return list(self._metas)

    def agents_referencing_builtin_mcp(self, server_name):
        return sorted(
            m.id
            for m in self._metas
            if server_name in (m.mcp_full_access or [])
        )


def _make_manager(repo: _FakeRepo) -> InstanceManager:
    """A manager shell with ONLY the attributes the bootstrap touches."""
    mgr = InstanceManager.__new__(InstanceManager)
    mgr._mcp_server_repository = repo
    return mgr


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_resolution_cache()
    yield
    reset_resolution_cache()


@pytest.fixture
def env(monkeypatch):
    def _set(
        *,
        self_env="dev",
        slug="nea",
        db="ensemble_dev",
        mcp_url=MCP_URL,
        mcp_key="dummy-plane-mcp-key",
        mcp_enabled: str | None = None,
        disable_builtin: str | None = None,
    ):
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", self_env)
        monkeypatch.setenv("POSTGRES_DB", db)
        if slug is None:
            monkeypatch.delenv("PLANE_MCP_WORKSPACE_SLUG", raising=False)
        else:
            monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", slug)
        if mcp_url is None:
            monkeypatch.delenv("PLANE_MCP_URL", raising=False)
        else:
            monkeypatch.setenv("PLANE_MCP_URL", mcp_url)
        if mcp_key is None:
            monkeypatch.delenv("PLANE_MCP_API_KEY", raising=False)
        else:
            monkeypatch.setenv("PLANE_MCP_API_KEY", mcp_key)
        for var, value in (
            ("PLANE_MCP_ENABLED", mcp_enabled),
            ("MCP_DISABLE_BUILT_IN_PLANE", disable_builtin),
        ):
            if value is None:
                monkeypatch.delenv(var, raising=False)
            else:
                monkeypatch.setenv(var, value)
        reset_resolution_cache()
        return current_plane_workspace()

    return _set


def _bootstrap(mgr, repo, agents, caplog):
    """Run the bootstrap with the plane-only builtin registry + fake agents.

    Captured at DEBUG so the INFO-level shapes (the deliberately-disabled
    notice) are visible too — the assertions filter by level themselves.
    """
    with (
        patch(
            "daemon.manager.get_mcp_registry",
            return_value=_FakeBuiltinRegistry([PlaneServerDefinition()]),
        ),
        patch(
            "daemon.registry.get_registry",
            return_value=_FakeAgentRegistry(agents),
        ),
        caplog.at_level(logging.DEBUG, logger="daemon.manager"),
    ):
        mgr._bootstrap_builtin_servers()
    return caplog


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def _messages(caplog):
    return "\n".join(r.getMessage() for r in caplog.records)


# ── P5: the loud skip ───────────────────────────────────────────────────────


class TestLoudSkipWarning:
    def test_missing_env_names_the_agents_and_vars(self, env, caplog):
        """P5 — WARNING level + the server + the agents + the env var
        NAMES. The original INFO line named none of them."""
        env(self_env="dev", slug="e2e", mcp_url=None, mcp_key=None)
        repo = _FakeRepo()
        mgr = _make_manager(repo)
        agents = [_agent_meta("project-manager", ["plane"])]

        _bootstrap(mgr, repo, agents, caplog)

        warnings = _warnings(caplog)
        assert warnings, "the skip must WARN, not INFO"
        # The SKIP record itself must be at WARNING level (the cross-check
        # below also warns — it is a different fact and must not mask a
        # demoted skip).
        skip_records = [
            r for r in caplog.records if "skipped" in r.getMessage()
        ]
        assert skip_records, "no skip record was emitted at all"
        assert all(
            r.levelno >= logging.WARNING for r in skip_records
        ), "the skip record is not at WARNING level"
        text = _messages(caplog)
        assert "plane" in text
        assert "project-manager" in text
        assert "PLANE_MCP_URL" in text
        assert "PLANE_MCP_API_KEY" in text
        assert "missing environment configuration" in text

    def test_refusal_names_the_agents_and_the_typed_reason(self, env, caplog):
        """P5c/P3-C — the resolver's typed reason VERBATIM + the agents."""
        env(self_env="dev", slug="nea")
        repo = _FakeRepo()
        mgr = _make_manager(repo)
        agents = [_agent_meta("project-manager", ["plane"])]

        _bootstrap(mgr, repo, agents, caplog)

        refusal_records = [
            r
            for r in caplog.records
            if "workspace refused" in r.getMessage()
        ]
        assert refusal_records, "no refusal record was emitted"
        assert all(
            r.levelno >= logging.WARNING for r in refusal_records
        ), "the workspace-refused skip is not at WARNING level"
        text = _messages(caplog)
        assert (
            "workspace 'nea' is production-only; this environment resolves to dev"
            in text
        )
        assert "project-manager" in text

    def test_a_refusal_is_never_rendered_as_missing_env(self, env, caplog):
        """P5c — the refusal message must not name env vars at all (the
        config is present; the POLICY refused)."""
        env(self_env="dev", slug="nea")
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        refusal_lines = [
            r.getMessage()
            for r in _warnings(caplog)
            if "workspace refused" in r.getMessage()
        ]
        assert refusal_lines
        for line in refusal_lines:
            assert "missing environment configuration" not in line

    def test_deliberately_disabled_produces_no_warning(self, env, caplog):
        """P5c — the kill-switch cause is a deliberate operator decision:
        INFO, not WARNING."""
        env(self_env="dev", slug="e2e", mcp_enabled="false")
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        with caplog.at_level(logging.INFO, logger="daemon.manager"):
            _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        # No WARNING records at all for the plane skip.
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(
            "deliberately disabled" in r.getMessage() for r in caplog.records
        )

    def test_three_causes_are_distinguishable(self, env, caplog):
        """P3-C — deliberately disabled / unexpectedly unavailable /
        workspace refused render as three DISTINCT shapes."""
        shapes = {}
        for label, kwargs in (
            ("disabled", dict(self_env="dev", slug="e2e", mcp_enabled="false")),
            ("unavailable", dict(self_env="dev", slug="e2e", mcp_url=None)),
            ("refused", dict(self_env="dev", slug="nea")),
        ):
            caplog.clear()
            env(**kwargs)
            repo = _FakeRepo()
            mgr = _make_manager(repo)
            _bootstrap(
                mgr,
                repo,
                [_agent_meta("project-manager", ["plane"])],
                caplog,
            )
            shapes[label] = _messages(caplog)
        assert "deliberately disabled" in shapes["disabled"]
        assert "missing environment configuration" in shapes["unavailable"]
        assert "workspace refused" in shapes["refused"]
        assert "workspace refused" not in shapes["unavailable"]
        assert "missing environment configuration" not in shapes["refused"]


# ── P2-G: the stale-row deactivation ────────────────────────────────────────


class TestStaleRowDeactivation:
    def test_active_row_is_deactivated_on_refusal(self, env, caplog):
        """P2-G — a pre-existing ACTIVE plane row is deactivated when the
        resolution is refused, so the cross-check/warmup/preload never
        treat it as registered."""
        env(self_env="dev", slug="nea")
        row = _FakeRow(
            config={
                "headers": {
                    "Authorization": "Bearer dummy",
                    "x-workspace-slug": "nea",
                }
            }
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert row.is_active is False
        assert {"id": row.id, "is_active": False} in repo.updates

    def test_no_row_no_create_on_refusal(self, env, caplog):
        env(self_env="dev", slug="nea")
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert repo.rows == {}

    def test_deactivation_never_surfaces_config_headers(self, env, caplog):
        """P2-G boundary (AD2): the deactivation path must not log/echo
        the row's config.headers — the Authorization bearer lives there."""
        env(self_env="dev", slug="nea")
        secret_marker = "plane_api_SECRET_DO_NOT_LOG"
        row = _FakeRow(
            config={
                "headers": {
                    "Authorization": f"Bearer {secret_marker}",
                    "x-workspace-slug": "nea",
                }
            }
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert row.is_active is False
        assert secret_marker not in _messages(caplog)
        assert "Authorization" not in _messages(caplog)

    def test_live_is_never_deactivated(self, env, caplog):
        env(self_env="live", slug="nea", db="ensemble_prod")
        row = _FakeRow(
            config={"headers": {"x-workspace-slug": "nea"}},
            config_schema_version="3",
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert row.is_active is True


# ── P2-H / P2-I: the heal gates ─────────────────────────────────────────────


class TestHealGates:
    def test_refusal_boot_leaves_schema_version_unchanged(self, env, caplog):
        """P2-H — a refusal boot defers the heal: the pre-existing row's
        ``config_schema_version`` stays what it was (no heal write at
        all)."""
        env(self_env="dev", slug="nea")
        row = _FakeRow(
            config={"headers": {"x-workspace-slug": "nea"}},
            config_schema_version="2",
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert row.config_schema_version == "2"
        assert not [u for u in repo.updates if "config_schema_version" in u]

    def test_allowed_boot_heals_schema_drift(self, env, caplog):
        """P8 at the manager site — an allowed boot with a schema drift
        re-points the persisted slug to the RESOLVED slug."""
        env(self_env="dev", slug="e2e")
        row = _FakeRow(
            config={
                "headers": {"x-workspace-slug": "nea"},
                "env": {"CONTEXT7_API_URL": "https://ctx.example.test"},
            },
            config_schema_version="2",
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert row.config_schema_version == "3"
        assert row.config["headers"]["x-workspace-slug"] == "e2e"
        assert row.config["env"]["CONTEXT7_API_URL"] == "https://ctx.example.test"

    def test_slug_equality_trigger_heals_without_schema_drift(
        self, env, caplog
    ):
        """P2-I — a plane row whose persisted slug differs from the
        RESOLVED slug heals EVEN when the schema version already matches
        (the ADJ-1×E4 divergence: a permitted-slug change on live)."""
        env(self_env="live", slug="nea", db="ensemble_prod")
        row = _FakeRow(
            config={"headers": {"x-workspace-slug": "e2e"}},
            config_schema_version="3",  # no schema drift
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert row.config["headers"]["x-workspace-slug"] == "nea"
        assert "slug-equality trigger" in _messages(caplog)

    def test_no_heal_when_slug_matches(self, env, caplog):
        env(self_env="live", slug="nea", db="ensemble_prod")
        row = _FakeRow(
            config={"headers": {"x-workspace-slug": "nea"}},
            config_schema_version="3",
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert not [
            u for u in repo.updates if "config" in u
        ], "no heal may fire when nothing drifted"


# ── P6 / P6b: the registry-vs-row cross-check ───────────────────────────────


class TestRegistryRowCrossCheck:
    def test_declared_but_unregistered_warns_naming_agent_and_server(
        self, env, caplog
    ):
        """P6 — an agent declaring ``mcp_full_access: ['plane']`` with NO
        registered row ⇒ WARNING naming the agent + the server."""
        env(self_env="dev", slug="e2e", mcp_url=None)
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        text = _messages(caplog)
        assert "capability gap" in text
        assert "project-manager" in text
        assert "'plane'" in text

    def test_policy_refused_row_stays_warning(self, env, caplog):
        """P6b/P3-E — a P2-G-deactivated row is POLICY-REFUSED, not
        never-registered: the cross-check still WARNs."""
        env(self_env="dev", slug="nea")
        row = _FakeRow(config={"headers": {"x-workspace-slug": "nea"}})
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert row.is_active is False  # P2-G deactivated it
        policy_lines = [
            r.getMessage()
            for r in _warnings(caplog)
            if "POLICY-REFUSED" in r.getMessage()
        ]
        assert policy_lines
        assert "project-manager" in policy_lines[0]

    def test_cross_check_is_pointer_only(self, env, caplog):
        """P6b — the cross-check emits agent + server + a POINTER; it does
        NOT re-interpolate the typed reason."""
        env(self_env="dev", slug="nea")
        # No row at all ⇒ the GENERIC gap branch fires (a re-interpolation
        # here would surface the typed refusal 'workspace 'nea' is
        # production-only...').
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        gap_lines = [
            r.getMessage()
            for r in _warnings(caplog)
            if "capability gap" in r.getMessage()
        ]
        assert gap_lines
        for line in gap_lines:
            assert "production-only" not in line, (
                "the cross-check re-interpolated the typed reason"
            )
            assert "see the" in line  # the pointer form

    def test_deliberately_off_is_not_a_warning(self, env, caplog):
        """P6 — an intentionally-disabled server does NOT produce a
        cross-check WARNING (else every env with the integration off
        warns on every boot)."""
        env(self_env="dev", slug="e2e", mcp_enabled="false")
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        with caplog.at_level(logging.INFO, logger="daemon.manager"):
            _bootstrap(
                mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog
            )

        assert not [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "capability gap" in r.getMessage()
        ]

    def test_registered_row_produces_no_gap_warning(self, env, caplog):
        env(self_env="live", slug="nea", db="ensemble_prod")
        row = _FakeRow(
            config={"headers": {"x-workspace-slug": "nea"}},
            config_schema_version="3",
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert not [
            r
            for r in caplog.records
            if "capability gap" in r.getMessage()
        ]

    def test_registry_helper_enumerates_referencing_agents(self):
        """Phase-3 §2.4 — the read-only registry helper."""
        from daemon.registry import AgentRegistry

        registry = AgentRegistry.__new__(AgentRegistry)
        metas = [
            _agent_meta("project-manager", ["plane"]),
            _agent_meta("worker"),
            _agent_meta("leader", ["plane"]),
        ]
        registry._agents = {m.id: m for m in metas}
        registry._custom_agents = {}
        assert registry.agents_referencing_builtin_mcp("plane") == [
            "leader",
            "project-manager",
        ]
        assert registry.agents_referencing_builtin_mcp("context7") == []
