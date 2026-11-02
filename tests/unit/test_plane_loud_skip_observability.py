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
from typing import Any
from unittest.mock import patch

from daemon.manager import InstanceManager
from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
from daemon.registry import AgentMetadata, AgentRegistry
from tests.helpers.plane_workspace_fixtures import (
    make_plane_env_fixture,
    reset_plane_workspace_cache,  # noqa: F401 — the autouse fixture
)

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

    def get_mcp_server_by_name(self, name: str) -> _FakeRow | None:
        return self.rows.get(name)

    def list_mcp_servers(self, **kwargs) -> list[_FakeRow]:
        return [r for r in self.rows.values() if r.is_active]

    def create_mcp_server(self, **kwargs) -> _FakeRow:
        name = kwargs["name"]
        row = _FakeRow(
            name=name,
            config=kwargs.get("config", {}),
            config_schema_version=kwargs.get("config_schema_version", "3"),
        )
        self.rows[name] = row
        return row

    def update_mcp_server(self, row_id, **kwargs) -> _FakeRow:
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
    def __init__(self, definitions: list[Any]) -> None:
        self._definitions = {d.name: d for d in definitions}

    def get_all(self) -> list[Any]:
        return list(self._definitions.values())

    def get_by_name(self, name: str) -> Any | None:
        return self._definitions.get(name)


def _agent_meta(
    agent_id: str, mcp_full_access: list[str] | None = None
) -> AgentMetadata:
    return AgentMetadata(
        id=agent_id,
        name=agent_id,
        description=f"Synthetic {agent_id}",
        path=Path(f"/tmp/{agent_id}"),
        team_members=[],
        mcp_full_access=mcp_full_access or [],
    )


class _FakeAgentRegistry(AgentRegistry):
    """A registry double that inherits the REAL bulk enumeration.

    ``AgentRegistry`` is a plain class (no ABC, no I/O in
    ``__init__``), so subclassing it gives the fake the production
    ``agents_by_mcp_full_access`` — one implementation, no drift
    between the double and the code under test.
    """

    def __init__(self, metas: list[AgentMetadata]) -> None:
        self._metas = metas

    def list_all(self) -> list[AgentMetadata]:
        return list(self._metas)

    def agents_referencing_builtin_mcp(self, server_name: str) -> list[str]:
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


env = make_plane_env_fixture(
    (
        ("self_env", "ENSEMBLE_SELF_ENV", "dev"),
        ("db", "POSTGRES_DB", "ensemble_dev"),
        ("slug", "PLANE_MCP_WORKSPACE_SLUG", "nea"),
        ("mcp_url", "PLANE_MCP_URL", MCP_URL),
        ("mcp_key", "PLANE_MCP_API_KEY", "dummy-plane-mcp-key"),
        ("mcp_enabled", "PLANE_MCP_ENABLED", None),
        ("disable_builtin", "MCP_DISABLE_BUILT_IN_PLANE", None),
    ),
    return_resolution=True,
)


def _bootstrap(mgr, repo, agents, caplog) -> None:
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


def _bootstrap_registry_broken(mgr, repo, caplog) -> None:
    """Run the bootstrap with the AGENT REGISTRY unreachable (C1/C2 —
    the function-local ``daemon.registry.get_registry`` seam raises)."""
    with (
        patch(
            "daemon.manager.get_mcp_registry",
            return_value=_FakeBuiltinRegistry([PlaneServerDefinition()]),
        ),
        patch(
            "daemon.registry.get_registry",
            side_effect=RuntimeError("planted registry outage"),
        ),
        caplog.at_level(logging.DEBUG, logger="daemon.manager"),
    ):
        mgr._bootstrap_builtin_servers()
    return caplog


def _bootstrap_quarantined(mgr, repo, agents, caplog) -> None:
    """Run the bootstrap with the plane definition's BASE availability
    gate forced False (C3 — the quarantine/package shape: env vars
    PRESENT, ``super().is_available()`` False ⇒ CAUSE_UNAVAILABLE with
    NO missing env vars)."""
    from daemon.mcp.builtin_servers.base import BuiltinServerDefinition

    with (
        patch(
            "daemon.manager.get_mcp_registry",
            return_value=_FakeBuiltinRegistry([PlaneServerDefinition()]),
        ),
        patch(
            "daemon.registry.get_registry",
            return_value=_FakeAgentRegistry(agents),
        ),
        patch.object(
            BuiltinServerDefinition,
            "is_available",
            new=classmethod(lambda cls: False),
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


# ── A1(iv): the heal reads the ACTUALLY-persisted slug ──────────────────────


class TestHealReadsTheActuallyPersistedSlug:
    """A1(iv) (reviewer-adjudicated tidier must-fix, 2026-11-02) — the
    drift computation reads the value under the header name
    CASE-INSENSITIVELY, so a case-variant header NAME (``X-Workspace-Slug``)
    reads the same as the canonical key.

    Pre-fix, the exact-case ``dict.get`` returned ``None`` for a
    case-variant name and ``None != resolved`` is True — the heal fired
    on rows whose slug actually MATCHED. Value case-sensitivity is
    UNCHANGED (``E2E`` still drifts from ``e2e``)."""

    def test_case_variant_name_with_matching_value_does_not_heal(
        self, env, caplog
    ):
        """The case-variant ``'e2e'`` row no longer heals — the
        actually-read value matches the resolved slug, so there is NO
        drift."""
        env(self_env="dev", slug="e2e")
        row = _FakeRow(
            config={"headers": {"X-Workspace-Slug": "e2e"}},
            config_schema_version="3",
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert row.config["headers"]["X-Workspace-Slug"] == "e2e"
        assert not [
            u for u in repo.updates if "config" in u
        ], "a case-variant header NAME must not fabricate slug drift"

    def test_case_variant_name_with_nea_value_heals_to_lowercase_e2e(
        self, env, caplog
    ):
        """A case-variant ``'nea'`` row DOES drift on a dev/e2e boot —
        and the healed value is the resolver's lowercase ``'e2e'``
        (headers re-materialised from the template)."""
        env(self_env="dev", slug="e2e")
        row = _FakeRow(
            config={"headers": {"X-Workspace-Slug": "nea"}},
            config_schema_version="3",
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert row.config["headers"]["x-workspace-slug"] == "e2e"
        assert "slug-equality trigger" in _messages(caplog)

    def test_case_variant_VALUE_still_drifts_and_heals(self, env, caplog):
        """Value case-sensitivity UNCHANGED — ``E2E`` is a real drift
        from ``e2e`` (the comparison stays exact on values)."""
        env(self_env="dev", slug="e2e")
        row = _FakeRow(
            config={"headers": {"x-workspace-slug": "E2E"}},
            config_schema_version="3",
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert row.config["headers"]["x-workspace-slug"] == "e2e"


# ── F3: the unavailable-cause stale-row NOTICE (shape (b)) ──────────────────


class TestUnavailableStaleRowNotice:
    """F3 (reviewer fold 2026-11-02) — with ``PLANE_MCP_URL``/``KEY`` absent
    (the *unavailable* cause, NOT workspace-refused), a pre-existing ACTIVE
    row survives with its persisted slug and previously emitted NO operator
    signal (the cross-check reads an active row as delivered ⇒ silent).
    Disposition: a NOTICE (WARNING naming the row, its persisted slug, the
    missing env vars, remediation) — deliberately NOT deactivation: P2-G
    scoped deactivation to the workspace-REFUSED cause (a policy verdict);
    P2-H's posture defers row mutations on non-available boots."""

    def test_unavailable_boot_with_stale_active_row_emits_notice(
        self, env, caplog
    ):
        env(self_env="dev", slug="e2e", mcp_url=None, mcp_key=None)
        row = _FakeRow(
            config={
                "headers": {
                    "Authorization": "Bearer AD2-SENTINEL",
                    "x-workspace-slug": "nea",
                }
            }
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        notice_lines = [
            r.getMessage()
            for r in _warnings(caplog)
            if "STALE ACTIVE row" in r.getMessage()
        ]
        assert notice_lines, "no stale-row notice was emitted"
        notice = notice_lines[0]
        assert "'nea'" in notice  # the persisted slug (repr form)
        assert "PLANE_MCP_URL" in notice  # the missing env var
        assert "Remediation" in notice
        assert "not delivered to agents" in notice.lower() or (
            "NOT delivered to agents" in notice
        )
        assert "project-manager" in notice  # the referencing agents
        # AD2 boundary (W1): the notice echoes the x-workspace-slug SCALAR
        # only — never the config.headers dict (it carries the bearer).
        assert "AD2-SENTINEL" not in notice
        assert "Authorization" not in notice

    def test_notice_does_not_mutate_the_row(self, env, caplog):
        """The (b)-shape contract: the notice is SIGNAL ONLY — the row
        stays ACTIVE and untouched on a transient config absence (an
        (a)-shape deactivation here is a regression)."""
        env(self_env="dev", slug="e2e", mcp_url=None, mcp_key=None)
        row = _FakeRow(config={"headers": {"x-workspace-slug": "nea"}})
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert row.is_active is True  # NOT deactivated on the unavailable cause
        assert not [
            u for u in repo.updates if "is_active" in u
        ], "the unavailable cause mutated the row"

    def test_notice_absent_when_no_row_exists(self, env, caplog):
        """No stale row ⇒ no notice (the plain missing-env skip WARNING
        stands alone)."""
        env(self_env="dev", slug="e2e", mcp_url=None, mcp_key=None)
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        assert not [
            r
            for r in _warnings(caplog)
            if "STALE ACTIVE row" in r.getMessage()
        ]
        assert any(
            "missing environment configuration" in r.getMessage()
            for r in _warnings(caplog)
        )


# ── C1-C3: observability truthfulness (tidier must-fix 2026-11-02) ──────────


class TestRegistryFailureTruthfulness:
    """C1 — a FAILED agent enumeration (registry unavailable) is a
    DIFFERENT fact from an EMPTY enumeration. The skip WARNING must say
    "could not be determined", never assert the false fact "no agent
    references it"."""

    def test_registry_failure_says_undetermined_not_none_reference(
        self, env, caplog
    ):
        env(self_env="dev", slug="e2e", mcp_url=None)
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        _bootstrap_registry_broken(mgr, repo, caplog)

        skip_lines = [
            r.getMessage()
            for r in caplog.records
            if "skipped" in r.getMessage()
        ]
        assert skip_lines, "no skip record was emitted"
        joined = "\n".join(skip_lines)
        assert "could not be determined (registry unavailable)" in joined
        assert "no agent references it" not in joined, (
            "a FAILED enumeration was rendered as the false fact "
            "'no agent references it'"
        )

    def test_registry_failure_does_not_name_any_agent(self, env, caplog):
        """The undetermined rendering names NO agent — an empty
        enumeration is not being fabricated from a failure."""
        env(self_env="dev", slug="e2e", mcp_url=None)
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        _bootstrap_registry_broken(mgr, repo, caplog)

        skip_lines = [
            r.getMessage()
            for r in caplog.records
            if "skipped" in r.getMessage()
        ]
        assert skip_lines
        assert "project-manager" not in "\n".join(skip_lines)

    def test_cross_check_registry_failure_is_a_warning(self, env, caplog):
        """C2 — a silenced cross-check is a LOST capability-gap signal:
        the registry-fail line is WARNING, not DEBUG."""
        env(self_env="dev", slug="e2e", mcp_url=None)
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        _bootstrap_registry_broken(mgr, repo, caplog)

        skipped_lines = [
            r
            for r in caplog.records
            if "cross-check skipped" in r.getMessage()
        ]
        assert skipped_lines, "no cross-check-skip record was emitted"
        assert all(
            r.levelno >= logging.WARNING for r in skipped_lines
        ), "the registry-fail cross-check line is not at WARNING level"


class TestQuarantineCauseRendering:
    """C3 — an UNAVAILABLE cause with NO missing env vars (the
    quarantine / missing-package shape) renders the typed
    ``unavailable_reason()`` (which names the quarantine), never the
    false "missing environment configuration: unknown"."""

    def test_quarantine_cause_names_the_quarantine_not_env_vars(
        self, env, caplog
    ):
        env(self_env="dev", slug="e2e")  # URL + KEY present
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        _bootstrap_quarantined(
            mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog
        )

        skip_records = [
            r for r in caplog.records if "skipped" in r.getMessage()
        ]
        assert skip_records
        text = "\n".join(r.getMessage() for r in skip_records)
        assert "quarantined for this boot" in text, (
            "the quarantine cause did not name the quarantine"
        )
        assert "missing environment configuration" not in text, (
            "the quarantine cause was rendered as a missing-env case"
        )
        assert "GET /api/mcp-servers" in text  # the remediation pointer

    def test_quarantine_stale_row_notice_names_the_quarantine(
        self, env, caplog
    ):
        """The F3 stale-row notice's cause line is cause-aware for the
        quarantine shape too."""
        env(self_env="dev", slug="e2e")
        row = _FakeRow(config={"headers": {"x-workspace-slug": "e2e"}})
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap_quarantined(
            mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog
        )

        notice_lines = [
            r.getMessage()
            for r in _warnings(caplog)
            if "STALE ACTIVE row" in r.getMessage()
        ]
        assert notice_lines
        notice = notice_lines[0]
        assert "quarantined for this boot" in notice
        assert "PLANE_MCP_URL" not in notice  # the env vars are PRESENT

    def test_genuine_missing_env_rendering_is_unchanged(self, env, caplog):
        """The REAL missing-env shape keeps its exact rendering (the
        C3 branch must not swallow it)."""
        env(self_env="dev", slug="e2e", mcp_url=None, mcp_key=None)
        repo = _FakeRepo()
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        skip_records = [
            r for r in caplog.records if "skipped" in r.getMessage()
        ]
        assert skip_records
        text = "\n".join(r.getMessage() for r in skip_records)
        assert "missing environment configuration: PLANE_MCP_URL, PLANE_MCP_API_KEY" in text
        assert "quarantined" not in text


class TestStaleRowNoticeRewording:
    """C3 — the notice's future wording states the ROW's true
    lifecycle ("stays ACTIVE and is re-delivered"), not the internal
    mechanism name ("reactivate/heal")."""

    def test_notice_uses_the_row_lifecycle_wording(self, env, caplog):
        env(self_env="dev", slug="e2e", mcp_url=None, mcp_key=None)
        row = _FakeRow(config={"headers": {"x-workspace-slug": "nea"}})
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        notice_lines = [
            r.getMessage()
            for r in _warnings(caplog)
            if "STALE ACTIVE row" in r.getMessage()
        ]
        assert notice_lines
        notice = notice_lines[0]
        assert "the row stays ACTIVE and is re-delivered" in notice
        assert "reactivate/heal" not in notice


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

    def test_inactive_row_renders_its_true_state_not_no_row(self, env, caplog):
        """F8 (reviewer fold) — an INACTIVE pre-existing row is NOT
        "no registered MCP server row exists": the message must state the
        row's TRUE state. The unavailable cause (missing env) with a stale
        inactive row hits exactly this branch (no deactivation ran, so it
        is never in ``policy_refused``)."""
        env(self_env="dev", slug="e2e", mcp_url=None)
        row = _FakeRow(
            config={"headers": {"x-workspace-slug": "e2e"}},
            is_active=False,
        )
        repo = _FakeRepo([row])
        mgr = _make_manager(repo)

        _bootstrap(mgr, repo, [_agent_meta("project-manager", ["plane"])], caplog)

        gap_lines = [
            r.getMessage()
            for r in _warnings(caplog)
            if "capability gap" in r.getMessage()
        ]
        assert gap_lines, "the cross-check must still WARN (tools are lost)"
        assert any("exists yet is INACTIVE" in line for line in gap_lines)
        for line in gap_lines:
            assert "no registered MCP server row exists" not in line, (
                "an INACTIVE row was rendered as 'no row exists'"
            )
            assert "see the" in line  # the pointer form survives

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
