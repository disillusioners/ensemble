"""DECISIVE GATE — Rule 1 of the 2026-10-20 user ruling (ARI-ONLY allow-lists).

The ruling (Discord, direct, 2026-10-20) is BINDING and RETRACTS the
earlier "ari + leader" audience for the four new tool categories:

* ``board_*``      (``board_create`` / ``board_update`` / ``board_list`` /
  ``board_get``)
* ``self_alarm``
* ``view_write``
* ``board_link``

Every one of them belongs to agent ``ari`` ALONE. Leader keeps its
pre-existing tools (``view_link`` et al.) untouched.

This module is the PROOF, and it runs through the REAL resolution
path — no synthetic stand-in for the policy layer:

1. the REAL ``agents/<id>/meta.json`` files (the allow-lists under
   test are read from disk, not hand-fed);
2. the REAL ``create_instance_tools`` build (the factory seam that
   materialises the four categories);
3. the REAL ``_apply_tool_filter`` — which is the production filter
   that calls ``resolve_tool_filter`` and the privileged-category
   default-deny strip.

A synthetic-agent harness would prove the FILTER works; this proves
the SHIPPED allow-lists produce the ruled audience.

The three-layer posture the commission established (category gate +
allow-list entry + in-body gate) means each tool is defended twice
over: this module pins layer 2 (the allow-lists) against the REAL
files, and the per-tool suites pin layer 3 (the body gate).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The four categories the ruling narrowed to ari-alone.
RULED_CATEGORIES: tuple[str, ...] = ("board", "self-alarm", "view-write", "board-link")

#: The concrete tool names those categories materialise.
RULED_TOOL_NAMES: frozenset[str] = frozenset({
    "board_create",
    "board_update",
    "board_list",
    "board_get",
    "self_alarm",
    "view_write",
    "board_link",
})

#: Leader's pre-existing tool that must SURVIVE the narrowing.
LEADER_KEEPS: str = "view_link"


def _manager() -> MagicMock:
    """A manager double rich enough for the real tool factory.

    The factory seam reads a handful of manager attributes; a
    ``MagicMock`` satisfies them all and keeps every category's
    service-wiring check happy (none of them raise on a mock).
    """
    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    manager._secret_service = None
    manager._instance_repository = None
    return manager


def _resolved_tool_names(agent_id: str) -> set[str]:
    """Resolve an agent's REAL tool surface through the REAL filter."""
    from daemon.tools.instance import _apply_tool_filter, create_instance_tools

    tools = create_instance_tools(_manager(), f"inst-{agent_id}", agent_id)
    resolved = _apply_tool_filter(tools, agent_id)
    return {getattr(t, "name", "?") for t in resolved}


def _real_allow(agent_id: str) -> list[str]:
    """The REAL ``tools.allow`` array read from disk."""
    meta = json.loads(
        (REPO_ROOT / "agents" / agent_id / "meta.json").read_text(encoding="utf-8")
    )
    return meta["tools"]["allow"]


class TestRule1RealAllowLists:
    """Layer 2 — the shipped allow-lists, read from the real files."""

    @pytest.mark.parametrize("category", RULED_CATEGORIES)
    def test_ari_holds_every_ruled_category(self, category: str) -> None:
        assert category in _real_allow("ari")

    @pytest.mark.parametrize("category", RULED_CATEGORIES)
    def test_leader_holds_no_ruled_category(self, category: str) -> None:
        """ARI-ONLY — leader's allow list carries NONE of the four."""
        assert category not in _real_allow("leader")

    def test_leader_keeps_its_pre_existing_view_link(self) -> None:
        """The ruling narrows the NEW categories only — leader's
        pre-existing ``view-views`` entry (which carries ``view_link``)
        is untouched."""
        assert "view-views" in _real_allow("leader")


class TestRule1RealResolution:
    """The REAL resolution path — the decisive behavioural gate."""

    def test_leader_is_denied_every_ruled_tool(self) -> None:
        names = _resolved_tool_names("leader")
        leaked = sorted(names & RULED_TOOL_NAMES)
        assert leaked == [], (
            f"leader must NOT resolve any ruled tool (ARI-ONLY ruling); "
            f"leaked {leaked}"
        )

    def test_ari_is_allowed_every_ruled_tool(self) -> None:
        names = _resolved_tool_names("ari")
        missing = sorted(RULED_TOOL_NAMES - names)
        assert missing == [], (
            f"ari must resolve every ruled tool; missing {missing}"
        )

    def test_leader_still_resolves_view_link(self) -> None:
        """The narrowing must not collateral-damage leader's
        pre-existing surface."""
        names = _resolved_tool_names("leader")
        assert LEADER_KEEPS in names, (
            f"leader must keep {LEADER_KEEPS!r}; resolved {sorted(names)}"
        )
