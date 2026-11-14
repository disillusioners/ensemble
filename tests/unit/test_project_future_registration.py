"""Registration tests for the project_future tool surface (AC21–AC24).

The registration is DISTRIBUTED and warn-only at boot (tool-lane §Enforcement):
missing CATEGORY_MODULES → allow WARNING only; missing KNOWN_TOOL_NAMES →
frozen-binary false "unknown tool"; a late extend is SILENTLY INVISIBLE and no
test catches it (that is why PO7 exists). These pins assert positively at the
registry/filter level — the boot-time validation is warn-only and therefore
not observable under this lane's zero-daemon-contact discipline.
"""

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    list_tools_by_category,
    scan_tools_for_full_docs,
)
from daemon.tools.instance import resolve_tool_filter
from daemon.tools.project_future import create_project_future_tools


FUTURE_TOOLS = (
    "project_future_add",
    "project_future_list",
    "project_future_remove",
)


def _scanned_future_tools() -> list:
    """Factory tools populated into the registry the way production does.

    ``_tool_metadata`` is filled by ``scan_tools_for_full_docs`` during
    instance assembly (instance.py order: extend → create_help_tool → scan →
    filter). Without a scan the registry's category map is empty and
    ``resolve_tool_filter`` would see no category membership at all.
    """
    tools = create_project_future_tools(store=None, current_instance_id="", agent_id="")
    scan_tools_for_full_docs(tools)
    return tools


def test_category_modules_entry():
    """AC21: CATEGORY_MODULES['project_future'] == 'daemon.tools.project_future'.

    The value MUST be a daemon.tools.* path (the daemon.tools.* path rule in
    the CATEGORY_MODULES preamble) or the AST scanner never finds the module.
    """
    assert CATEGORY_MODULES["project_future"] == "daemon.tools.project_future"


def test_known_tool_names_superset():
    """AC22: KNOWN_TOOL_NAMES ⊇ {add, list, remove} (frozen-binary pin)."""
    assert set(FUTURE_TOOLS) <= set(KNOWN_TOOL_NAMES)


def test_known_tool_names_sorted_position():
    """project_future_* sorts BEFORE project_history_* ('f' < 'h') — the hand
    placement must sit between project_delete_metadata and project_get."""
    names = sorted(KNOWN_TOOL_NAMES)
    i = names.index("project_future_add")
    assert names[i - 1] == "project_delete_metadata"
    assert names[i : i + 3] == list(FUTURE_TOOLS)
    assert names[i + 3] == "project_get"


def test_category_not_privileged():
    """W5 do-not-copy decision: project_future stays NON-privileged — empty-allow
    agents inherit it exactly as they inherit project_history today (A15)."""
    assert "project_future" not in PRIVILEGED_TOOL_CATEGORIES


def test_allow_filter_positive_direction():
    """AC24 (positive): allow=['project_future'] → all three tools survive."""
    _scanned_future_tools()  # populate the registry category map
    allowed = resolve_tool_filter(["project_future"], None)
    assert allowed is not None
    assert set(FUTURE_TOOLS) <= allowed


def test_allow_filter_negative_direction():
    """AC24 (negative): an allow list WITHOUT project_future drops all three."""
    _scanned_future_tools()
    allowed = resolve_tool_filter(["bash"], None)
    assert allowed is not None
    assert not (set(FUTURE_TOOLS) & allowed)


def test_allow_filter_deny_wins():
    """deny wins conflicts: allow project_future but deny remove → remove dropped."""
    _scanned_future_tools()
    allowed = resolve_tool_filter(
        ["project_future"], ["project_future_remove"]
    )
    assert allowed is not None
    assert {"project_future_add", "project_future_list"} <= allowed
    assert "project_future_remove" not in allowed


def test_tool_name_in_tool_categories_map():
    """list_tools_by_category exposes project_future owning exactly the 3 verbs."""
    _scanned_future_tools()
    cats = dict(list_tools_by_category())
    assert "project_future" in cats
    assert set(cats["project_future"]) == set(FUTURE_TOOLS)
