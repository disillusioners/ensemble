"""Registration proof for the scheduling tool category (D9 / ADR-009).

Static verification that the ``scheduling`` category is correctly
registered and granted — NO daemon restart involved (runtime exposure
lands at the next registry exposure). Five test groups, three of them
parametrized over the registered agents ``["ari", "leader", "jober"]``
(all three ARE registered — no xfail/skip per OD-6):

1. ``test_scheduling_in_meta_json_allowlist`` — each agent grants the category.
2. ``test_meta_json_allowlist_subset_of_registry`` — every allow entry is a
   known tool or category (drift pin between meta.json and the registry).
3. ``test_scheduling_category_resolves_with_four_tools`` — the category
   module resolves and the four tool names are in the frozen universe.
4. ``test_scheduling_section_no_system_internals`` — the ``## Scheduling``
   section of each tools_note carries ZERO system-internals tokens
   (docs/agent-prompt-writing-guide.md §1; update BOTH the guide and
   FORBIDDEN_TOKENS together if the closure evolves).
5. ``test_scheduling_section_cross_refs_resolve`` — every ``See `X``` ref
   in the section resolves to a real heading in the owning agent's files
   or another agent's files (guide §3 convention v2).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"

REGISTERED_AGENTS = ["ari", "leader", "jober"]

SCHEDULING_TOOLS = (
    "task_schedule",
    "task_schedule_list",
    "task_schedule_cancel",
    "task_schedule_update",
)

# Forbidden-token closure per docs/agent-prompt-writing-guide.md §1 (:15-28)
# and §10 pre-commit checklist. If the guide's forbidden layer evolves,
# update THIS tuple in the same commit (guide §1 is the spec).
FORBIDDEN_TOKENS = (
    "meta.json",
    "tools.allow",
    "tools.deny",
    "daemon/",
    "_tool_registry",
    "skill-set.yaml",
    "innate_skills",
    "auto_load",
    "agent_id=",
    "default_agent_versions",
    "seed_all",
    "tests/unit/",
    "tests/integration/",
    ".md",
)


def _load_meta(agent_id: str) -> dict:
    return json.loads((AGENTS_DIR / agent_id / "meta.json").read_text())


def _load_section(agent_id: str) -> str:
    """The `## Scheduling*` section of an agent's tools_note.md (raises if absent)."""
    md = (AGENTS_DIR / agent_id / "tools_note.md").read_text()
    match = re.search(r"^## Scheduling.*?(?=^## |\Z)", md, re.MULTILINE | re.DOTALL)
    assert match, f"agents/{agent_id}/tools_note.md missing '## Scheduling' section"
    return match.group(0)


# ---------------------------------------------------------------------------
# 1. Allow-list grant (D7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", REGISTERED_AGENTS)
def test_scheduling_in_meta_json_allowlist(agent_id: str) -> None:
    """D7 + D9: each target agent's tools allow-list contains 'scheduling'."""
    meta = _load_meta(agent_id)
    allow = meta.get("tools", {}).get("allow", [])
    assert "scheduling" in allow, (
        f"agents/{agent_id}/meta.json tools allow-list missing 'scheduling'"
    )


# ---------------------------------------------------------------------------
# 2. Allow-list ⊆ registry universe (drift pin)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", REGISTERED_AGENTS)
def test_meta_json_allowlist_subset_of_registry(agent_id: str) -> None:
    """Every allow entry is a known tool name or a known category key."""
    from daemon.tools._tool_registry import CATEGORY_MODULES, KNOWN_TOOL_NAMES

    meta = _load_meta(agent_id)
    allow = meta.get("tools", {}).get("allow", [])
    known = set(KNOWN_TOOL_NAMES) | set(CATEGORY_MODULES.keys())
    unknown = [entry for entry in allow if entry not in known]
    assert not unknown, (
        f"agents/{agent_id}/meta.json allow-list contains entries unknown to "
        f"the registry: {unknown!r}"
    )


# ---------------------------------------------------------------------------
# 3. Category resolves + frozen universe carries the four tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", SCHEDULING_TOOLS)
def test_scheduling_category_resolves_with_four_tools(tool_name: str) -> None:
    """The scheduling category is wired and the tool name is registered."""
    from daemon.tools._tool_registry import (
        CATEGORY_MODULES,
        DYNAMIC_TOOL_NAMES,
        KNOWN_TOOL_NAMES,
    )

    assert CATEGORY_MODULES.get("scheduling") == "daemon.tools.scheduling", (
        "CATEGORY_MODULES must map 'scheduling' -> 'daemon.tools.scheduling'"
    )
    assert tool_name in KNOWN_TOOL_NAMES, (
        f"{tool_name!r} missing from KNOWN_TOOL_NAMES — regenerate the "
        f"frozen universe (see the regen command in _tool_registry.py)"
    )
    assert tool_name in DYNAMIC_TOOL_NAMES, (
        f"{tool_name!r} missing from DYNAMIC_TOOL_NAMES — factory-created "
        f"tools must be listed there or allow-list validation rejects them"
    )


# ---------------------------------------------------------------------------
# 4. Prompt-closure: zero system internals in the new sections (guide §1/§10)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", REGISTERED_AGENTS)
def test_scheduling_section_no_system_internals(agent_id: str) -> None:
    """The ## Scheduling section is agent-POV only — zero forbidden tokens."""
    section = _load_section(agent_id)
    hits = [token for token in FORBIDDEN_TOKENS if token in section]
    assert not hits, (
        f"agents/{agent_id}/tools_note.md ## Scheduling mentions forbidden "
        f"token(s) {hits!r} — reword to agent POV per the prompt-writing "
        f"guide §1"
    )


# ---------------------------------------------------------------------------
# 5. Cross-references resolve (guide §3 convention v2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", REGISTERED_AGENTS)
def test_scheduling_section_cross_refs_resolve(agent_id: str) -> None:
    """Every 'See `X`' ref resolves to a real heading (own agent or peer).

    Form: same-agent `See `Section Name`` or cross-agent
    `See `agent's Section Name`` — the backtick-quoted form keeps the
    heading token unambiguous.
    """
    md = (AGENTS_DIR / agent_id / "tools_note.md").read_text()
    section = _load_section(agent_id)
    refs = re.findall(r"[Ss]ee\s+`([^`]+)`", section)
    for ref in refs:
        heading = ref.split("'s ", 1)[-1].strip()
        resolves_in_own = heading in md
        resolves_in_peer = any(
            heading in (AGENTS_DIR / other / "tools_note.md").read_text()
            for other in REGISTERED_AGENTS
            if other != agent_id
        )
        assert resolves_in_own or resolves_in_peer, (
            f"agents/{agent_id}/tools_note.md ## Scheduling has an unresolved "
            f"cross-reference: {ref!r} (heading {heading!r} not found in any "
            f"of the registered agents' tool notes)"
        )
