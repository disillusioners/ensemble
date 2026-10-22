"""THE GATING TEST — the ``mcp_rpc`` tools are reachable ONLY through the
``mcp_rpc`` allow entry (or a literal tool name).

The hazard this suite closes (2026-10-22, remote-hands center gateway):

* the ``mcp`` category is a DYNAMIC category — its expansion is a prefix
  sweep (``daemon/tools/instance.py``) that claims every
  ``mcp_<server>_<tool>`` name;
* the ``mcp_rpc_*`` tool names of the remote-hands gateway MATCH that
  pattern exactly;
* ~20 agents carry ``"mcp"`` in their ``tools.allow``;
* the ``mcp_rpc`` category is PRIVILEGED (opt-in only) — the sweep must
  not be a back door around that gate.

THE METHOD (the CONSTANT-PIN TAUTOLOGY RULE)
---------------------------------------------

Every assertion here runs the REAL lane:

* ``resolve_tool_filter(allow, deny, tool_categories=..., all_tool_names=...)``
  — the real resolver (``daemon/tools/instance.py``), against a SYNTHETIC
  universe;
* ``_apply_tool_filter(tools, agent_id, ...)`` — the real drop site, with
  the ``_MockTool`` / ``_make_meta`` fixture kit of
  ``tests/unit/tools/test_dynamic_toolset_expansion.py``.

Nothing here re-implements the filter and nothing asserts that a constant
equals itself. The sweep is exercised in the shape where it ACTUALLY
fires (an EMPTY ``mcp`` category) — otherwise the test would pass for the
wrong reason.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


from daemon.registry import AgentMetadata, ToolFilter
from daemon.tools._tool_registry import (
    list_tools_by_category,
    scan_tools_for_full_docs,
)
from daemon.tools.instance import _apply_tool_filter, resolve_tool_filter

#: The four tool names of the ``mcp_rpc`` category.
MCP_RPC_TOOLS = {
    "mcp_rpc_list_machine",
    "mcp_rpc_connect",
    "mcp_rpc_command",
    "mcp_rpc_disconnect",
}

#: A dynamic MCP server's tools — the ones the ``mcp`` sweep is FOR.
MCP_SERVER_TOOLS = {"mcp_ctx7_get_docs", "mcp_webfetch_fetch"}

#: An unrelated tool.
OTHER_TOOLS = {"read_file", "bash"}


class _MockTool:
    """Minimal tool stub — ``_apply_tool_filter`` only reads ``.name``.

    The ``category`` is the ``@register_tool_category`` stamp the real
    tools carry; the real lane reads it (``_strip_privileged_category_tools``
    and the category map), so the stub carries it too.
    """

    def __init__(self, name: str, category: str | None = None):
        self.name = name
        if category is not None:
            self._tool_category = category


def _make_meta(
    agent_id: str,
    *,
    tools_allow: list[str] | None = None,
    tools_deny: list[str] | None = None,
) -> AgentMetadata:
    """Build a real ``AgentMetadata`` with a real ``ToolFilter``."""
    tools = None
    if tools_allow is not None or tools_deny is not None:
        tools = ToolFilter(
            allow=list(tools_allow) if tools_allow else None,
            deny=list(tools_deny) if tools_deny else None,
        )
    return AgentMetadata(
        id=agent_id,
        name=agent_id,
        description=f"Synthetic {agent_id}",
        path=Path(f"/tmp/{agent_id}"),
        team_members=[],
        tools=tools,
        innate_skills=[],
    )


def _fake_registry(meta: AgentMetadata) -> MagicMock:
    registry = MagicMock()
    registry.get_version.return_value = meta
    registry.get_resolved.return_value = meta
    return registry


def _synthetic_categories() -> dict[str, list[str]]:
    """The category map of the SYNTHETIC universe.

    The ``mcp`` category is present and EMPTY — that is the shape in
    which the prefix sweep actually fires. The ``mcp_rpc`` category
    carries its four tools, exactly as the production registry does after
    ``scan_tools_for_full_docs`` runs (the production order in
    ``create_instance_tools`` is: build the tools → scan → filter).
    """
    return {
        "mcp": [],
        "mcp_rpc": sorted(MCP_RPC_TOOLS),
        "filesystem": ["read_file"],
    }


UNIVERSE = set(MCP_RPC_TOOLS) | MCP_SERVER_TOOLS | OTHER_TOOLS


# ─────────────────────────────────────────────────────────────────────────────
# The resolver — the real lane, against the synthetic universe
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveToolFilterGating:
    def test_allow_mcp_does_not_grant_the_mcp_rpc_tools(self):
        """``allow=['mcp']`` must NOT reach the remote-hands tools.

        The sweep fires here (the ``mcp`` category is EMPTY) — so this is
        the arm that actually exercises the fix.
        """
        result = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories=_synthetic_categories(),
            all_tool_names=UNIVERSE,
        )
        assert result is not None
        assert not (MCP_RPC_TOOLS & result), (
            "allow=['mcp'] granted the privileged mcp_rpc tools: "
            f"{sorted(MCP_RPC_TOOLS & result)}"
        )
        # The sweep still does its job for the tools it is FOR.
        assert MCP_SERVER_TOOLS <= result

    def test_allow_mcp_rpc_grants_the_mcp_rpc_tools(self):
        """``allow=['mcp_rpc']`` MUST reach the remote-hands tools."""
        result = resolve_tool_filter(
            allow=["mcp_rpc"],
            deny=None,
            tool_categories=_synthetic_categories(),
            all_tool_names=UNIVERSE,
        )
        assert result is not None
        assert MCP_RPC_TOOLS <= result
        # And only those — the category does not leak the mcp namespace.
        assert not (MCP_SERVER_TOOLS & result)

    def test_a_literal_tool_name_grants_exactly_that_tool(self):
        """The second (and only other) documented access path."""
        result = resolve_tool_filter(
            allow=["mcp_rpc_command"],
            deny=None,
            tool_categories=_synthetic_categories(),
            all_tool_names=UNIVERSE,
        )
        assert result is not None
        assert "mcp_rpc_command" in result
        assert not (MCP_RPC_TOOLS - {"mcp_rpc_command"} & result)

    def test_the_empty_allow_universe_does_not_grant_the_privileged_tools(self):
        """The default universe never joins a PRIVILEGED category.

        ``resolve_tool_filter`` answers ``None`` for the empty-allow
        universe ("everything is potentially allowed") — the PRIVILEGED
        strip is the caller's job (``_strip_privileged_category_tools``),
        and it is exercised end-to-end below.
        """
        result = resolve_tool_filter(
            allow=None,
            deny=None,
            tool_categories=_synthetic_categories(),
            all_tool_names=UNIVERSE,
        )
        assert result is None

    def test_the_deny_list_wins_over_the_allow_entry(self):
        result = resolve_tool_filter(
            allow=["mcp_rpc"],
            deny=["mcp_rpc_command"],
            tool_categories=_synthetic_categories(),
            all_tool_names=UNIVERSE,
        )
        assert result is not None
        assert "mcp_rpc_command" not in result
        assert "mcp_rpc_connect" in result


# ─────────────────────────────────────────────────────────────────────────────
# The drop site — the real lane, end to end
# ─────────────────────────────────────────────────────────────────────────────


class TestApplyToolFilterGating:
    def test_an_agent_with_allow_mcp_keeps_the_mcp_rpc_tools_out(self):
        """END-TO-END through the real drop site.

        The tools carry their ``_tool_category`` stamp, and the registry
        is populated the way production populates it (the scan runs
        BEFORE the filter).
        """
        tools = [
            _MockTool("mcp_rpc_list_machine", "mcp_rpc"),
            _MockTool("mcp_rpc_connect", "mcp_rpc"),
            _MockTool("mcp_rpc_command", "mcp_rpc"),
            _MockTool("mcp_rpc_disconnect", "mcp_rpc"),
            _MockTool("read_file", "filesystem"),
        ]
        scan_tools_for_full_docs(tools)

        meta = _make_meta("leader-ish", tools_allow=["mcp", "filesystem"])
        with patch("daemon.registry.get_registry", return_value=_fake_registry(meta)):
            filtered = _apply_tool_filter(
                tools,
                "leader-ish",
                mcp_tool_names=sorted(MCP_SERVER_TOOLS),
            )

        names = {t.name for t in filtered}
        assert not (MCP_RPC_TOOLS & names), (
            f"the drop site granted the privileged mcp_rpc tools: "
            f"{sorted(MCP_RPC_TOOLS & names)}"
        )
        assert "read_file" in names

    def test_an_agent_with_allow_mcp_rpc_gets_the_mcp_rpc_tools(self):
        tools = [
            _MockTool("mcp_rpc_list_machine", "mcp_rpc"),
            _MockTool("mcp_rpc_connect", "mcp_rpc"),
            _MockTool("mcp_rpc_command", "mcp_rpc"),
            _MockTool("mcp_rpc_disconnect", "mcp_rpc"),
            _MockTool("read_file", "filesystem"),
        ]
        scan_tools_for_full_docs(tools)

        meta = _make_meta("ari-ish", tools_allow=["mcp_rpc"])
        with patch("daemon.registry.get_registry", return_value=_fake_registry(meta)):
            filtered = _apply_tool_filter(tools, "ari-ish")

        names = {t.name for t in filtered}
        assert MCP_RPC_TOOLS <= names
        assert "read_file" not in names

    def test_an_agent_with_an_empty_allow_gets_no_mcp_rpc_tools(self):
        """The empty-allow inherit universe never reaches the privileged lane."""
        tools = [
            _MockTool("mcp_rpc_list_machine", "mcp_rpc"),
            _MockTool("mcp_rpc_connect", "mcp_rpc"),
            _MockTool("mcp_rpc_command", "mcp_rpc"),
            _MockTool("mcp_rpc_disconnect", "mcp_rpc"),
            _MockTool("read_file", "filesystem"),
        ]
        scan_tools_for_full_docs(tools)

        meta = _make_meta("watcher-ish", tools_allow=[])
        with patch("daemon.registry.get_registry", return_value=_fake_registry(meta)):
            filtered = _apply_tool_filter(tools, "watcher-ish")

        names = {t.name for t in filtered}
        assert not (MCP_RPC_TOOLS & names)

    def test_the_sweep_cannot_claim_the_tools_of_another_registered_category(self):
        """THE SWEEP, directly.

        The sweep is the only lane in which an ``mcp`` allow entry could
        reach an ``mcp_rpc_*`` name. This arms it with the shape that
        makes it fire (an EMPTY ``mcp`` category) and asserts that the
        tools of ANOTHER registered category are NOT claimed.
        """
        categories = {
            "mcp": [],
            "mcp_rpc": sorted(MCP_RPC_TOOLS),
        }
        result = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories=categories,
            all_tool_names=UNIVERSE,
        )
        assert result is not None
        assert not (MCP_RPC_TOOLS & result)
        # The sweep did not go to sleep — it still found its own tools.
        assert MCP_SERVER_TOOLS <= result


# ─────────────────────────────────────────────────────────────────────────────
# The registry shape
# ─────────────────────────────────────────────────────────────────────────────


class TestRegistryShape:
    def test_the_mcp_rpc_category_is_registered(self):
        scan_tools_for_full_docs(
            [
                _MockTool("mcp_rpc_list_machine", "mcp_rpc"),
                _MockTool("mcp_rpc_connect", "mcp_rpc"),
                _MockTool("mcp_rpc_command", "mcp_rpc"),
                _MockTool("mcp_rpc_disconnect", "mcp_rpc"),
            ]
        )
        categories = dict(list_tools_by_category())
        assert categories.get("mcp_rpc"), "the mcp_rpc category is not registered"
        assert set(categories["mcp_rpc"]) == MCP_RPC_TOOLS

    def test_the_mcp_rpc_category_is_privileged(self):
        from daemon.tools._tool_registry import PRIVILEGED_TOOL_CATEGORIES

        assert "mcp_rpc" in PRIVILEGED_TOOL_CATEGORIES
