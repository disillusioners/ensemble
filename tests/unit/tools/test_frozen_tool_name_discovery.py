"""Tests for frozen-binary-safe tool-name discovery.

Covers the bug fix: ``discover_all_tool_names()`` in
``daemon/tools/_tool_registry.py`` AST-scans ``.py`` source files on disk to
find ``@tool``-decorated functions. In PyInstaller-frozen prod builds
(``ensemble-prod``) ``daemon/`` ships as bytecode only — every category
module's ``file_path.exists()`` is False, and the function silently returns an
empty set. The result: every factory-created tool name (project_*, todo_view,
terminate_instance, ...) vanishes from the validator universe and produces
false-positive "unknown tool" warnings on agent boot.

Pre-fix behavior: prod daemon boot 2026-08-20 20:00:35 logged 30 WARNING
lines for agent ``project-manager`` (27 allow entries + 3 deny entries —
all real tools).

The fix introduces a static fallback universe ``KNOWN_TOOL_NAMES`` adjacent
to ``CATEGORY_MODULES`` and rewires ``discover_all_tool_names()`` to:

* Return ``set(KNOWN_TOOL_NAMES)`` when ZERO source files were read
  (fully frozen — bytecode-only).
* Merge source-discovered names with ``KNOWN_TOOL_NAMES`` otherwise
  (source canonical where present, static list covers the rest).

These tests are pure unit tests. They use ``monkeypatch`` to simulate the
frozen-binary environment by pointing ``CATEGORY_MODULES`` at non-existent
file paths or at a temp-dir layout, and by patching ``_tool_registry.__file__``
to redirect the hardcoded ``Path(__file__).parent`` resolution.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import daemon.tools._tool_registry as reg
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    KNOWN_TOOL_NAMES,
    discover_all_tool_names,
)


# 30 unique tool names from the 2026-08-20 20:00:35 incident. The count 30
# in the incident = 27 allow-entry warning lines + 3 deny-entry warning
# lines; some names appear in both lists. These 30 distinct names cover
# every project-manager warning emitted at that boot.
FLAGGED_NAMES: list[str] = [
    "project_get",
    "project_list",
    "project_search",
    "project_get_by_instance",
    "project_get_by_directory",
    "project_history_list",
    "project_history_search",
    "project_cn_list",
    "project_create",
    "project_update",
    "project_set_status",
    "project_history_add",
    "project_cn_add",
    "project_cn_remove",
    "project_set_tags",
    "project_add_tag",
    "project_remove_tag",
    "project_set_shortnames",
    "project_add_shortname",
    "project_remove_shortname",
    "project_set_metadata",
    "project_delete_metadata",
    "project_link",
    "project_unlink",
    "project_add_directory",
    "project_remove_directory",
    "todo_view",
    "project_history_delete",
    "project_delete",
    "terminate_instance",
]


def test_known_tool_names_is_superset_of_flagged_entries() -> None:
    """All 30 incident names must be present in BOTH the static fallback
    universe AND the source-discovered set (so source mode and frozen mode
    agree, and the static list covers the prod incident)."""
    static_set = set(KNOWN_TOOL_NAMES)
    source_set = discover_all_tool_names()

    missing_static = [n for n in FLAGGED_NAMES if n not in static_set]
    missing_source = [n for n in FLAGGED_NAMES if n not in source_set]

    assert missing_static == [], (
        f"KNOWN_TOOL_NAMES is missing flagged names: {missing_static}"
    )
    assert missing_source == [], (
        f"discover_all_tool_names() is missing flagged names: {missing_source}"
    )
    assert len(KNOWN_TOOL_NAMES) >= len(FLAGGED_NAMES)
    assert len(source_set) >= len(FLAGGED_NAMES)


def test_discover_falls_back_when_no_source_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ZERO category-module source files exist on disk (frozen binary
    simulation), ``discover_all_tool_names()`` must return the static
    universe — non-empty and containing all 30 flagged names."""
    # Point CATEGORY_MODULES at module paths whose .py files do not exist
    # under daemon/tools/. The names below are deliberately chosen so no
    # real file matches daemon/tools/<name>.py.
    fake_modules = {
        "fake_alpha": "daemon.tools.does_not_exist_xyz_alpha_123",
        "fake_beta": "daemon.tools.does_not_exist_xyz_beta_456",
    }
    monkeypatch.setattr(reg, "CATEGORY_MODULES", fake_modules)

    result = discover_all_tool_names()

    # Must equal the static universe (no source, so no merge contribution).
    assert result == set(KNOWN_TOOL_NAMES), (
        f"Expected fallback to KNOWN_TOOL_NAMES, got diff "
        f"only_in_result={result - set(KNOWN_TOOL_NAMES)} "
        f"only_in_static={set(KNOWN_TOOL_NAMES) - result}"
    )
    assert len(result) > 0
    for name in FLAGGED_NAMES:
        assert name in result, f"Frozen fallback missing flagged name: {name}"


def test_discover_merges_source_and_static(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When SOME source files are readable (partial-source mode), the result
    must merge source-discovered names with KNOWN_TOOL_NAMES — source is
    canonical where present, the static list covers the rest."""
    # discover_all_tool_names() resolves category-module paths relative to
    # Path(__file__).parent (= daemon/tools/). Patch the module's __file__
    # so the resolution lands in tmp_path, then write a small fake tool
    # module there.
    # (`__file__` is resolved at call time from module globals, so setattr(reg, '__file__', ...) redirects Path(__file__).parent inside discover_all_tool_names.)
    fake_module_filename = "fake_partial_tools.py"
    fake_tool_name = "test_partial_frozen_unique_tool_xyz"
    fake_module_path = tmp_path / fake_module_filename
    fake_module_path.write_text(
        "from langchain_core.tools import tool\n"
        "\n"
        "@tool\n"
        f"def {fake_tool_name}():\n"
        '    """Docstring."""\n'
        "    return None\n"
    )

    fake_modules = {"partial": "daemon.tools.fake_partial_tools"}
    monkeypatch.setattr(reg, "__file__", str(tmp_path / "_tool_registry.py"))
    monkeypatch.setattr(reg, "CATEGORY_MODULES", fake_modules)

    result = discover_all_tool_names()

    # Source contribution must be present.
    assert fake_tool_name in result, (
        f"Expected source-discovered tool '{fake_tool_name}' in merged result"
    )
    # Static fallback must still cover the rest.
    assert set(KNOWN_TOOL_NAMES).issubset(result), (
        "Merged result missing some KNOWN_TOOL_NAMES entries — static fallback "
        "should be present in partial-source mode too"
    )
    # And of course all flagged incident names are covered.
    for name in FLAGGED_NAMES:
        assert name in result, f"Partial-source merge missing flagged name: {name}"


def test_registry_validation_zero_warnings_for_project_manager_frozen_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end acceptance check: with discover_all_tool_names() returning
    the static fallback path (the frozen mode), AgentRegistry.discover() +
    validate_tool_configs() must emit ZERO warnings that mention
    ``project-manager``.

    This is the production repro: before the fix, the same call produced
    32 WARNING lines for project-manager at prod daemon boot
    2026-08-20 20:00:35.

    We simulate frozen-binary mode by patching ``_tool_registry.__file__``
    so that ``Path(__file__).parent`` (the hardcoded base dir used by
    ``discover_all_tool_names()``) resolves to an empty tmp directory.
    Every category module's source file then appears "missing" on disk —
    exactly what happens when ``daemon/`` ships as bytecode in a PyInstaller
    bundle. ``CATEGORY_MODULES.keys()`` is left intact (categories don't
    depend on disk readability), so the only thing the fallback path needs
    to repair is factory-created tool names.
    """
    from daemon.registry import AgentRegistry

    # Point Path(__file__).parent at an empty tmp directory — this is the
    # exact effect of a PyInstaller frozen build where daemon/ ships as
    # bytecode only. CATEGORY_MODULES stays as-is so categories (filesystem,
    # chart, image, plane, instance, council, self, question, mcp, ...) remain
    # visible to the validator.
    monkeypatch.setattr(reg, "__file__", str(tmp_path / "_tool_registry.py"))

    # Repo root is three parents up from this test file:
    # tests/unit/tools/test_xxx.py -> parents[3] == repo_root.
    repo_root = Path(__file__).resolve().parents[3]
    agents_dir = repo_root / "agents"
    assert agents_dir.exists(), f"agents/ not found at {agents_dir}"
    assert (agents_dir / "project-manager").is_dir(), "project-manager agent must exist for this regression test"

    registry = AgentRegistry(agents_dir)
    registry.discover()

    warnings = registry.validate_tool_configs()
    project_manager_warnings = [w for w in warnings if "project-manager" in w]

    assert project_manager_warnings == [], (
        f"Expected ZERO project-manager warnings in frozen mode, got "
        f"{len(project_manager_warnings)}: {project_manager_warnings}"
    )
