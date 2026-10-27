"""C1 — the per-boot MCP quarantine registry (soak-gate-mcp-warmup).

The registry lives in ``daemon/mcp/init_guard.py`` (the ONE leaf home,
decisions §3e E5 / W-4). This file pins:

* a quarantined server is UNAVAILABLE at **every** consult site — the
  ``is_available()`` classmethod is the seam, and it is consulted at
  four sites (bootstrap, warmup-pool registration, warm path, cold
  path) plus the two explicit cold-path seams;
* the consult uses the **INSTANCE** form ``cls().name`` — the class
  attribute ``cls.name`` is a ``property`` object, so the class-level
  form would silently never match a registry key;
* the plane override's ``super().is_available()`` hole (W-5) is closed;
* the entries carry the operator-visible fields;
* ``clear_quarantine`` / ``reset_for_tests`` re-open the gate.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from daemon.mcp import init_guard
from daemon.mcp.builtin_servers import get_registry
from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition
from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
from daemon.services import mcp_service

REPO_ROOT = Path(__file__).resolve().parents[3]



# ── the seam itself ────────────────────────────────────────────────────


def test_quarantine_makes_the_builtin_unavailable():
    """A quarantined server is skipped by ``is_available()``."""
    definition = Context7ServerDefinition()
    assert definition.is_available() is True

    entry = init_guard.server_state("context7")
    assert entry is None  # nothing recorded yet

    # Quarantine through the public failure path (async).
    import asyncio

    asyncio.run(init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED))
    asyncio.run(init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED))
    asyncio.run(init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED))

    assert init_guard.is_quarantined("context7") is True
    assert definition.is_available() is False


def test_the_consult_uses_the_instance_form():
    """``cls().name`` — NOT ``cls.name`` (the property-object trap).

    ``name`` is a ``@property @abstractmethod`` on the base class, so
    the class-level attribute is a property OBJECT. A registry lookup
    with it would never match and would silently disable the whole
    quarantine.

    The assertion parses the AST (not the text) so a mention inside a
    docstring cannot satisfy — or break — the pin.
    """
    import ast

    base_src = (REPO_ROOT / "daemon" / "mcp" / "builtin_servers" / "base.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(base_src)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "is_quarantined"
    ]
    assert calls, "the base definition MUST consult the quarantine registry"
    for call in calls:
        assert call.args, "is_quarantined() must be called with the server name"
        arg = call.args[0]
        assert isinstance(arg, ast.Attribute) and arg.attr == "name", (
            "the consult argument must be the server NAME"
        )
        assert isinstance(arg.value, ast.Call), (
            "the consult MUST use the instance form cls().name — cls.name "
            "is a property object and would silently never match"
        )
        assert (
            isinstance(arg.value.func, ast.Name)
            and arg.value.func.id == "cls"
        ), (
            "the instance form must be cls() — the definitions are "
            "stateless and already instantiated at builtin_servers/__init__.py"
        )


def test_plane_override_consults_super_first():
    """W-5 — the plane override bypassed ``super()`` entirely.

    Without the ``super()`` call a quarantined plane would NOT be
    skipped (the whole registry consult lives in the base class).
    """
    src = inspect.getsource(PlaneServerDefinition.is_available)
    body = src.split('"""', 2)[-1]
    super_idx = body.find("super().is_available()")
    kill_idx = body.find("_mcp_kill_switch_enabled()")
    assert super_idx != -1, "the plane override MUST call super().is_available()"
    assert kill_idx != -1
    assert super_idx < kill_idx, (
        "the super() consult MUST come FIRST — otherwise the plane's own "
        "gates can return True for a quarantined server"
    )


def test_quarantine_skips_plane_too(monkeypatch):
    """A quarantined plane is unavailable even with its own gates met."""
    monkeypatch.setenv("PLANE_MCP_URL", "https://plane.example.com")
    monkeypatch.setenv("PLANE_MCP_API_KEY", "test-key")
    monkeypatch.delenv("PLANE_MCP_ENABLED", raising=False)

    definition = PlaneServerDefinition()
    assert definition.is_available() is True

    import asyncio

    asyncio.run(init_guard.record_failure("plane", init_guard.FAILURE_CLASS_DNS_NXDOMAIN))
    assert init_guard.is_quarantined("plane") is True
    assert definition.is_available() is False


def test_other_servers_are_unaffected():
    """Degrade ALONE — one server's failure never touches the others."""
    import asyncio

    asyncio.run(init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED))
    assert init_guard.is_quarantined("context7") is False  # degraded, not quarantined
    assert get_registry().get_by_name("webfetch").is_available() is True
    assert init_guard.is_quarantined("webfetch") is False


# ── the consult-site census (ALL sites) ───────────────────────────────


def test_all_four_is_available_consult_sites_exist():
    """The four ``is_available()`` consult sites are all present.

    A pin that covers only SOME of the consult sites is a false
    comfort: a quarantined server must disappear from EVERY path (no
    DB row, no pool registration, no warm, no cold discovery).
    """
    manager_src = (REPO_ROOT / "daemon" / "manager.py").read_text(encoding="utf-8")
    service_src = (REPO_ROOT / "daemon" / "services" / "mcp_service.py").read_text(
        encoding="utf-8"
    )

    # S1 bootstrap + S2 warmup-pool registration.
    assert manager_src.count("definition.is_available()") >= 2, (
        "the bootstrap site and the warmup-pool registration site must "
        "both consult is_available()"
    )
    # S4 warm path + the COLD path consult.
    assert service_src.count("definition.is_available()") >= 2, (
        "the warm path and the cold path must both consult is_available()"
    )


def test_the_cold_path_seams_consult_the_registry_directly():
    """S4/S5 — the cold discovery and the cold spawn consult directly.

    The cold path spawns a subprocess per instance (the incident's ×4
    multiplier), so it carries an EXPLICIT quarantine consult in
    addition to the ``is_available()`` gate.
    """
    src = (REPO_ROOT / "daemon" / "services" / "mcp_service.py").read_text(
        encoding="utf-8"
    )
    assert src.count("_is_server_quarantined(") >= 3, (
        "the cold discovery, the cold-start fall-through and the "
        "session-provider entry point must each consult the registry"
    )


def test_cold_discovery_returns_empty_for_a_quarantined_server():
    """S4 — the cold discovery does NOT spawn for a quarantined server."""
    import asyncio

    asyncio.run(init_guard.record_failure("context7", init_guard.FAILURE_CLASS_DNS_NXDOMAIN))
    assert init_guard.is_quarantined("context7") is True

    service = object.__new__(mcp_service.McpService)
    result = asyncio.run(service._discover_schemas_cold(_FakeServer("context7")))
    assert result == [], "a quarantined server must not be cold-discovered"


def test_cold_spawn_refuses_a_quarantined_server():
    """S5 — the cold-start fall-through refuses a quarantined server."""
    import asyncio

    from langchain_core.tools import ToolException

    asyncio.run(init_guard.record_failure("context7", init_guard.FAILURE_CLASS_DNS_NXDOMAIN))

    provider = object.__new__(mcp_service._McpSessionProviderImpl)
    provider._service = object.__new__(mcp_service.McpService)
    provider._instance_id = "inst-1"

    with pytest.raises(ToolException) as excinfo:
        asyncio.run(provider.get_session("context7"))
    assert "quarantined" in str(excinfo.value)


# ── the entries + the recovery paths ──────────────────────────────────


def test_entries_carry_reason_since_attempts_and_error_class():
    """The operator-visible fields are all present."""
    import asyncio

    asyncio.run(init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_TIMEOUT))
    entry = init_guard.server_state("context7")
    assert entry is not None
    assert entry.reason == init_guard.FAILURE_CLASS_INIT_TIMEOUT
    assert entry.last_error_class == init_guard.FAILURE_CLASS_INIT_TIMEOUT
    assert entry.attempts == 1
    assert entry.since is not None and entry.since.endswith("+00:00")

    snapshot = init_guard.state_snapshot()
    assert "context7" in snapshot
    published = snapshot["context7"]
    for field in (
        "state",
        "reason",
        "since",
        "init_attempts",
        "last_error_class",
        "breaker_state",
        "recoverable",
    ):
        assert field in published, f"the published entry must carry '{field}'"
    assert published["state"] == init_guard.STATE_DEGRADED
    assert published["recoverable"] is True


def test_quarantine_entries_only_list_quarantined_servers():
    import asyncio

    asyncio.run(init_guard.record_failure("ctx-a", init_guard.FAILURE_CLASS_INIT_FAILED))
    assert init_guard.quarantine_entries() == []

    asyncio.run(init_guard.record_failure("ctx-a", init_guard.FAILURE_CLASS_INIT_FAILED))
    asyncio.run(init_guard.record_failure("ctx-a", init_guard.FAILURE_CLASS_INIT_FAILED))
    entries = init_guard.quarantine_entries()
    assert len(entries) == 1
    assert entries[0]["server"] == "ctx-a"
    assert entries[0]["attempts"] == 3


def test_clear_quarantine_reopens_the_gate():
    """The operator reset path — the gate re-opens without a restart."""
    import asyncio

    asyncio.run(init_guard.record_failure("context7", init_guard.FAILURE_CLASS_DNS_NXDOMAIN))
    assert init_guard.is_quarantined("context7") is True

    assert init_guard.clear_quarantine("context7") is True
    assert init_guard.is_quarantined("context7") is False
    assert Context7ServerDefinition().is_available() is True
    # A second clear is a no-op (there is nothing to clear).
    assert init_guard.clear_quarantine("context7") is False


def test_reset_for_tests_clears_the_state():
    """W-4a — the test-only reset clears the registry AND the breakers."""
    import asyncio

    asyncio.run(init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED))
    assert init_guard.state_snapshot() != {}

    init_guard.reset_for_tests()
    assert init_guard.state_snapshot() == {}
    assert init_guard.is_quarantined("context7") is False
    assert init_guard.quarantine_entries() == []
    assert Context7ServerDefinition().is_available() is True


class _FakeServer:
    """The minimal shape ``_discover_schemas_cold`` needs before it
    reaches the (never-taken) connection path."""

    def __init__(self, name: str) -> None:
        self.name = name
