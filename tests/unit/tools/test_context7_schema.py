"""C3 — the context7 built-in template (soak-gate-mcp-warmup).

C3 makes the context7 API base settable through the PROTECTED,
schema-backed path (decisions §3g G1-G6, requirements §3-C3):

* the schema carries exactly TWO env-section fields — the effective
  lever (``CONTEXT7_API_URL``, no default) and the daemon-side mirror
  (``CONTEXT7_BASE_URL``, default ``https://context7.com``, INERT to
  the package);
* ``build_config`` validates the URL-shaped values (a non-http(s)
  scheme and embedded userinfo are rejected);
* the schema version is bumped to ``"3"``;
* the schema-version heal is ENV-PRESERVING (BLOCKING 3) — the
  operator's pinned ``CONTEXT7_API_URL`` and the KMS-marker keys
  SURVIVE the 2 ⇒ 3 heal;
* the configured value reaches the CHILD through
  ``build_pooled_stdio_config`` → ``StdioServerParameters(env=...)``;
* the built-in's generic ``config`` PUT still 403s
  (``BUILTIN_SERVER_PROTECTED``) — C3 adds a schema, not an exemption.
"""

from __future__ import annotations

import pytest

from daemon.manager import InstanceManager, _heal_config_preserving_env
from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition
from daemon.mcp.config import McpStdioConfig
from daemon.mcp.warmup_pool import McpWarmupPool, build_pooled_stdio_config
from daemon.models.mcp_server import McpServerUpdate
from daemon.routers import mcp_servers as mcp_servers_router


@pytest.fixture
def definition() -> Context7ServerDefinition:
    return Context7ServerDefinition()


# ── the schema ─────────────────────────────────────────────────────────


def test_the_schema_is_not_empty(definition):
    schema = definition.get_config_schema()
    assert schema, "the context7 schema must NOT be empty any more"


def test_the_schema_carries_exactly_the_two_env_entries(definition):
    schema = definition.get_config_schema()
    keys = [field["key"] for field in schema]
    assert keys == ["CONTEXT7_BASE_URL", "CONTEXT7_API_URL"]
    for field in schema:
        assert field["section"] == "env", (
            f"{field['key']} must be an ENV-section field — the env "
            "section is the only channel the package reads"
        )


def test_the_withdrawn_name_is_absent(definition):
    keys = [field["key"] for field in definition.get_config_schema()]
    assert "CONTEXT7_API_BASE_URL" not in keys, (
        "CONTEXT7_API_BASE_URL is the WITHDRAWN name — neither the "
        "package nor the daemon reads it"
    )


def test_the_descriptions_state_the_g1_distinction(definition):
    """A description regression is a FAIL (the inert-lever hazard)."""
    fields = {f["key"]: f for f in definition.get_config_schema()}
    mirror = (fields["CONTEXT7_BASE_URL"]["description"] or "").upper()
    lever = (fields["CONTEXT7_API_URL"]["description"] or "").upper()

    assert "INERT" in mirror, (
        "the CONTEXT7_BASE_URL description MUST state that the field is "
        "inert to the package — an operator who believes it moves the "
        "package would be silently wrong"
    )
    assert "MIRROR" in mirror
    assert "EFFECTIVE" in lever, (
        "the CONTEXT7_API_URL description MUST state that it is the "
        "effective lever"
    )


def test_build_config_produces_the_default_env(definition):
    config = definition.build_config({})
    env = config["env"]
    assert env["CONTEXT7_BASE_URL"] == "https://context7.com"
    assert "CONTEXT7_API_URL" not in env, (
        "CONTEXT7_API_URL has NO default — a pinned default would freeze "
        "the exact thing that broke"
    )
    # The transport surface is unchanged.
    assert config["command"] == "npx"
    assert config["args"] == ["-y", "@upstash/context7-mcp"]


def test_the_schema_version_is_three(definition):
    assert definition.schema_version == "3"


# ── the validation ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "ftp://context7.com",
        "file:///etc/passwd",
        "context7.com",
        "javascript:alert(1)",
    ],
)
def test_a_bad_scheme_is_rejected(definition, value):
    with pytest.raises(ValueError):
        definition.build_config({"CONTEXT7_API_URL": value})


@pytest.mark.parametrize(
    "value",
    [
        "https://user:pass@context7.com",
        "https://user@context7.com",
    ],
)
def test_embedded_userinfo_is_rejected(definition, value):
    with pytest.raises(ValueError):
        definition.build_config({"CONTEXT7_BASE_URL": value})


def test_a_valid_url_is_accepted(definition):
    config = definition.build_config(
        {"CONTEXT7_API_URL": "https://api.example.com/v1"}
    )
    assert config["env"]["CONTEXT7_API_URL"] == "https://api.example.com/v1"


# ── the child env ──────────────────────────────────────────────────────


def test_the_configured_value_reaches_the_child_env(definition):
    """The value lands on the child's env — NOT on the daemon's env."""
    row_config = {"env": {"CONTEXT7_API_URL": "https://api.example.com/v1"}}
    merged = build_pooled_stdio_config(definition, row_config)
    assert isinstance(merged, McpStdioConfig)
    assert merged.env["CONTEXT7_API_URL"] == "https://api.example.com/v1"
    # The daemon's own environment is NOT the channel.
    import os

    assert "CONTEXT7_API_URL" not in os.environ or (
        os.environ.get("CONTEXT7_API_URL") != "https://api.example.com/v1"
    )


async def test_the_constructed_stdio_parameters_carry_the_value(monkeypatch):
    """The assertion is on the CONSTRUCTED params, not the daemon env.

    The pytest root conftest mocks the ``mcp`` SDK, so the REAL
    ``StdioServerParameters`` is not reachable from a unit test. The pin
    therefore captures the CONSTRUCTOR CALL at the real spawn site
    (``warmup_pool._create_pooled_connection``) — which is a stronger
    assertion than re-constructing the object in the test.
    """
    from daemon.mcp import warmup_pool as warmup_pool_module

    definition = Context7ServerDefinition()
    row_config = {"env": {"CONTEXT7_API_URL": "https://api.example.com/v1"}}
    merged = build_pooled_stdio_config(definition, row_config)

    captured: dict = {}

    def _capture_params(**kwargs):
        captured.update(kwargs)
        return object()

    class _SentinelClient:
        def __init__(self, params):
            captured["params_object"] = params

    monkeypatch.setattr(warmup_pool_module, "StdioServerParameters", _capture_params)
    monkeypatch.setattr(warmup_pool_module, "TaskScopedStdioClient", _SentinelClient)

    pool = McpWarmupPool()
    pool.register_server("context7", merged)

    with pytest.raises(Exception):
        await pool._create_pooled_connection("context7")

    assert captured["command"] == "npx"
    assert captured["env"]["CONTEXT7_API_URL"] == "https://api.example.com/v1"
    assert "CONTEXT7_API_URL" not in captured["args"]


# ── BLOCKING 3 — the heal is env-preserving ───────────────────────────


def test_the_heal_helper_preserves_the_operator_env():
    default_config = {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp"],
        "env": {"CONTEXT7_BASE_URL": "https://context7.com"},
    }
    existing_config = {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp"],
        "env": {
            "CONTEXT7_API_URL": "https://pinned.example/api",
            "CONTEXT7_KMS_REF": "__KMS_REF__abc123__",
        },
    }
    healed = _heal_config_preserving_env(default_config, existing_config)

    assert healed["env"]["CONTEXT7_API_URL"] == "https://pinned.example/api", (
        "the operator's pinned value MUST survive the schema heal"
    )
    assert healed["env"]["CONTEXT7_KMS_REF"] == "__KMS_REF__abc123__", (
        "the KMS-marker key MUST survive the schema heal"
    )
    assert healed["env"]["CONTEXT7_BASE_URL"] == "https://context7.com", (
        "the template-derived defaults are rebuilt ALONGSIDE the "
        "operator's keys, not instead of them"
    )


def test_the_heal_keeps_undeclared_row_keys():
    """A schema narrowing must not discard an undeclared row key."""
    default_config = {"env": {"CONTEXT7_BASE_URL": "https://context7.com"}}
    existing_config = {"env": {"SOME_UNDECLARED_KEY": "value"}}
    healed = _heal_config_preserving_env(default_config, existing_config)
    assert healed["env"]["SOME_UNDECLARED_KEY"] == "value"


class _FakeRow:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "row-1")
        self.name = kwargs.get("name", "context7")
        self.description = kwargs.get("description", "ctx")
        self.is_builtin = kwargs.get("is_builtin", True)
        self.is_active = kwargs.get("is_active", True)
        self.config = kwargs.get("config", {})
        self.config_schema = kwargs.get("config_schema", [])
        self.config_schema_version = kwargs.get("config_schema_version", "2")


class _FakeRepo:
    def __init__(self, row):
        self.row = row
        self.updates = []

    def get_mcp_server_by_name(self, name):
        return self.row if name == self.row.name else None

    def get_mcp_server(self, server_id):
        return self.row if server_id == self.row.id else None

    def update_mcp_server(self, server_id, **kwargs):
        self.updates.append((server_id, kwargs))
        return self.row

    def create_mcp_server(self, **kwargs):  # pragma: no cover — not used
        raise AssertionError("the row must not be re-created")


def test_the_boot_heal_preserves_the_operator_env_end_to_end():
    """The real heal path — the operator env survives the 2 ⇒ 3 heal."""
    row = _FakeRow(
        config={
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp"],
            "env": {
                "CONTEXT7_API_URL": "https://pinned.example/api",
                "CONTEXT7_KMS_REF": "__KMS_REF__abc123__",
            },
        },
        config_schema_version="2",
    )
    repo = _FakeRepo(row)
    manager = object.__new__(InstanceManager)
    manager._mcp_server_repository = repo

    manager._bootstrap_builtin_servers()

    assert repo.updates, "the schema drift must trigger the heal"
    _, kwargs = repo.updates[0]
    assert kwargs["config_schema_version"] == "3"
    healed_env = kwargs["config"]["env"]
    assert healed_env["CONTEXT7_API_URL"] == "https://pinned.example/api"
    assert healed_env["CONTEXT7_KMS_REF"] == "__KMS_REF__abc123__"
    assert healed_env["CONTEXT7_BASE_URL"] == "https://context7.com"

    # The EFFECTIVE post-heal env is what C2's pre-check reads.
    merged = build_pooled_stdio_config(
        Context7ServerDefinition(), {"env": healed_env}
    )
    assert (
        Context7ServerDefinition().get_precheck_url(merged.env)
        == "https://pinned.example/api"
    )


# ── the protected-built-in regression ──────────────────────────────────


class _FakeManager:
    is_write_paused = False

    def __init__(self, row):
        self._mcp_server_repository = _FakeRepo(row)
        self._mcp_service = None


class _FakeRequest:
    def __init__(self, manager):
        self.app = type("App", (), {"state": type("State", (), {"manager": manager})()})()


async def test_the_builtin_config_put_is_still_protected():
    """C3 adds a schema — it does NOT create an exemption."""
    from fastapi import HTTPException

    row = _FakeRow(config={"transport": "stdio", "command": "npx", "args": []})
    manager = _FakeManager(row)
    request = _FakeRequest(manager)

    with pytest.raises(HTTPException) as excinfo:
        await mcp_servers_router.update_mcp_server(
            row.id,
            McpServerUpdate(config={"transport": "stdio", "command": "evil"}),
            request,
        )
    assert excinfo.value.status_code == 403
    detail = excinfo.value.detail
    assert detail["code"] == "BUILTIN_SERVER_PROTECTED"
